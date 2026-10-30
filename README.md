# Arenas

**Engineering decisions from a real AI product, each settled by measuring quality, latency and
cost on the same data instead of guessing.**

Each arena takes one real decision from [enable-ai](https://github.com/SeijiMatsumoto/enable-ai),
my AI-engineering news product ("which model should make this call?", "is retrieval missing
duplicates?"). It puts several options behind one interface, runs them on the same workload, and
picks a winner with fixed rules and paired significance tests. Same report, same answer.

**[→ Browse the hub](https://seijimatsumoto.github.io/arenas/)**: one card per arena with the
question, the answer, the numbers, where the labels came from, and what changed in production.

<!-- arenas:start -->
| Arena | Question | Finding | Labels | Outcome |
|---|---|---|---|---|
| [Voice Arena: Study 1: checking before speaking](https://github.com/SeijiMatsumoto/voice-arena) | Can a voice agent's sentences be checked against its sources before the caller hears them, without slowing the call? | On 42 unsupported sentences (4 natural, 38 planted) across 1 held-out scenario, cascade-jev caught 90% while blocking 2.6% of correct sentences, adding 161 ms p95 before the first word at $0.22 per 1,000 calls. Ignoring the limits, cascade-jev-calibrated caught the most (93%), not a significant difference. [→](https://github.com/SeijiMatsumoto/voice-arena/tree/main/snapshots/2026-10-29T18-11-07-063Z) | Mixed labels | No product decision |
| [Dedup Arena](https://github.com/SeijiMatsumoto/dedup-arena) | Which retrieval setup finds the existing story a new item belongs to, so enable-ai's feed doesn't show the same news twice? | Use **hybrid-voyage** (recall@5 96%, right story first 82%, 245ms median, $0.0118 per 1k). Production today (its logged decisions): 90.9% correct end to end; 31% of items that belong to an existing story became a duplicate story, and 2% of all items were merged into the wrong story. Production's logged candidates contained the right story for 84.5% of matching items, so 15% never reached the judge. openai-3-large reaches 97.3% (paired McNemar p=0.0005). [→](https://github.com/SeijiMatsumoto/dedup-arena/tree/main/snapshots/2026-10-27_2053) | LLM-judge labels | Decision pending |
| [Grounding Arena](https://github.com/SeijiMatsumoto/grounding-arena) | What is the cheapest way to catch unsupported LLM-extracted fields without rejecting correct ones? | On 80 held-out public sources, passage-window LLM checking retained 117/122 supported natural claims and caught the sole labeled natural error, at 45% less verification cost than full-source checking. Fixed-rule pick over all 217 scored fields: **value-in-evidence**. [→](https://github.com/SeijiMatsumoto/grounding-arena/tree/main/snapshots/2026-10-27_205248223384) | LLM-judge labels | Decision pending |
| [Decision Arena](https://github.com/SeijiMatsumoto/decision-arena) | For bounded classification (pick a label, yes/no, a score), should a decision model like Jev make the call, or a cheap LLM? | Use **jev-1.14** (99% accurate, 185ms median, $0.0183 per 1k calls). Best decision model (jev-1.14, 99%) vs best LLM (haiku-5.5, 98%): no significant accuracy difference; the decision model is 7.7× faster and 3.3× cheaper. [→](https://github.com/SeijiMatsumoto/decision-arena/tree/main/snapshots/2026-10-27_1839) | Hand-written labels | Decision pending |
<!-- arenas:end -->

*This table is rebuilt daily from each arena's `snapshots/latest.json`.*

## What every arena does

1. **Starts from a real decision in enable-ai**, using its real (public) data where possible.
2. **Compares many options behind one interface.** One config file lists contenders; adding one
   is one entry. Everything goes through OpenRouter.
3. **Measures quality, latency and cost together.** Quality alone is not an answer.
4. **Labels honestly.** Every result says where its ground truth came from: hand-written,
   production, reviewed, LLM judge, or simulated. Model labels are never called human ones.
5. **Makes deterministic picks.** Fixed rules plus exact paired tests (McNemar, sign test). No
   LLM decides the winner.
6. **Commits dated snapshots**, so any finding can be traced to the exact run behind it.
7. **Closes the loop.** Each card says whether production changed, and links the evidence.

## What's in this repo

| Path | What it is |
|---|---|
| [`kit/`](kit/) | **arena-kit**, the shared Python library: cached OpenRouter client, mock providers, runner, paired tests, fixed-rule picks, snapshots, and the CLI every arena gets for free. |
| [`kit/src/arena_kit/summary.schema.json`](kit/src/arena_kit/summary.schema.json) | **arena-summary/1**, the contract. Any arena in any language writes one `summary.json` per snapshot in this shape. |
| [`arenas.yaml`](arenas.yaml) | The registry of arenas the hub indexes. |
| [`src/arena_hub/`](src/arena_hub/) | The hub builder: fetches each `latest.json`, validates it, renders the page and the table above. |

## Start a new arena

```bash
uvx --from "git+https://github.com/SeijiMatsumoto/arenas.git#subdirectory=kit" \
  arena-kit new dedup-arena --question "Which dedup strategy keeps the feed clean at the lowest cost?"
cd dedup-arena && uv sync
uv run arena run --mock      # full pipeline offline: run, table, pick
```

The scaffold is a working classification arena (rules baseline vs. two LLMs, a starter
dataset, tests). Replace the items, `score` and contenders with the real problem. Then run it
for real, `uv run arena snapshot`, and add one line to `arenas.yaml`.

Arenas don't have to use the kit. [voice-arena](https://github.com/SeijiMatsumoto/voice-arena)
is TypeScript and writes the same `summary.json` itself. The hub only reads the contract.

## The contract in short

`snapshots/latest.json` (a copy of the newest `snapshots/<id>/summary.json`) holds:

- **arena:** name, question, repo. **snapshot:** id, date, folder.
- **headline:** the finding in one sentence. **pick:** name and the rule that chose it.
- **contenders:** quality, median latency, cost per 1k (`null` when unknown, never 0), verdict
  (`pick`, `tied`, `worse`, `other`, `excluded`, `baseline`).
- **data:** item count and label source. **method:** the fixed rule and the test.
- **outcome:** `changed` / `kept` / `pending` / `not-applicable`, with an evidence link.
- **caveats**, and **mock** (the hub refuses mock runs).

Hand-written fields (question, label source, outcome, caveats) live in each arena's
`arena.yaml`. After editing it, `arena-kit refresh` re-applies them to the latest summary
without a new run.

## Build the hub locally

```bash
uv sync
uv run hub build --local --readme   # reads sibling checkouts (see arenas.yaml)
open site/index.html
```

## Caveats

- Every result is about one product's workload. That's the point, but it means "X won here" is
  not "X wins everywhere".
- Not all labels are human. Each card shows the label source; read results based on LLM-judge or
  simulated labels as provisional.
- Latency is measured from one laptop through OpenRouter and includes network time.
