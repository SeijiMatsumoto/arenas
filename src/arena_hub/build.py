"""Build the hub: fetch every arena's latest summary, validate it, render one page and a README table.

    uv run hub build            # fetch summaries from GitHub (what CI does)
    uv run hub build --local    # read sibling checkouts listed in arenas.yaml
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from arena_kit import contract
from arena_kit.snapshot import fmt_quality, ms, replace_block, usd

ROOT = Path(__file__).resolve().parents[2]
START, END = "<!-- arenas:start -->", "<!-- arenas:end -->"

OUTCOME = {"changed": "Changed in production", "kept": "Kept current setup",
           "pending": "Decision pending", "not-applicable": "No product decision"}
LABELS = {"hand-written": "Hand-written labels", "production": "Production labels",
          "reviewed": "Reviewed labels", "llm-judge": "LLM-judge labels",
          "simulated": "Simulated data", "mixed": "Mixed labels"}
VERDICT_ORDER = {"pick": 0, "tied": 1, "baseline": 2, "other": 3, "worse": 4, "excluded": 5}


@dataclass
class Entry:
    repo: str
    path: str
    summary: dict[str, Any] | None
    problem: str | None = None


def load_registry(path: Path) -> list[dict[str, Any]]:
    return yaml.safe_load(path.read_text())["arenas"]


def fetch(arena: dict[str, Any], *, local: bool, http: httpx.Client) -> list[Entry]:
    entries = []
    for rel in arena.get("summaries", ["snapshots/latest.json"]):
        try:
            if local:
                text = (ROOT / arena["local"] / rel).read_text()
            else:
                branch = arena.get("branch", "main")
                url = f"https://raw.githubusercontent.com/{arena['repo']}/{branch}/{rel}"
                response = http.get(url)
                response.raise_for_status()
                text = response.text
            summary = json.loads(text)
            contract.validate(summary)
            problem = "mock run" if summary["mock"] else None
            entries.append(Entry(arena["repo"], rel, None if problem else summary, problem))
        except (OSError, httpx.HTTPError, json.JSONDecodeError, contract.ContractError) as error:
            entries.append(Entry(arena["repo"], rel, None, f"{type(error).__name__}: {error}"))
    return entries


def inline(text: str) -> str:
    """The tiny bit of markdown headlines use: **bold** and `code`."""
    out = html.escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", out)


def snapshot_url(s: dict[str, Any]) -> str:
    return f"{s['arena']['repo'].rstrip('/')}/tree/main/{s['snapshot']['path']}"


def title(s: dict[str, Any]) -> str:
    t = s["arena"].get("title") or s["arena"]["name"]
    return f"{t}: {s['arena']['study']}" if s["arena"].get("study") else t


def ranked(s: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    higher = s["metric"]["higher_is_better"]
    rows = sorted(s["contenders"], key=lambda c: (
        VERDICT_ORDER.get(c["verdict"], 9),
        -(c["quality"] or 0) if higher else (c["quality"] or 0)))
    return rows[:limit]


def latency_label(s: dict[str, Any]) -> str:
    latency = s.get("latency") or {}
    return latency.get("label") or latency.get("stat", "p50")


def card(s: dict[str, Any]) -> str:
    m = s["metric"]
    repo = html.escape(s["arena"]["repo"])
    name = html.escape(s["arena"].get("title") or s["arena"]["name"])
    picked = next((c for c in s["contenders"]
                   if s.get("pick") and c["name"] == s["pick"]["name"]), None)
    # A concise preview; the complete finding remains available in the disclosure.
    finding = re.split(r"(?<=[.!?])\s+(?=[A-Z])", s["headline"], maxsplit=1)[0]
    rows = "".join(
        f"<tr class='v-{c['verdict']}'><th scope='row'>{html.escape(c['name'])}"
        f"{' <span class=tag>Pick</span>' if c['verdict'] == 'pick' else ''}</th>"
        f"<td>{fmt_quality(c['quality'], m)}</td><td>{ms(c['latency_ms'])}</td>"
        f"<td>{usd(c['cost_per_1k_usd'])}</td></tr>"
        for c in ranked(s, limit=len(s["contenders"])))
    metrics = ""
    if picked:
        metrics = f"""<div class="selection"><p class="selection-label">Fixed-rule pick</p>
        <p class="pick-name">{html.escape(picked['name'])}</p>
        <dl class="metrics">
          <div><dt>{html.escape(m['label'])}</dt><dd>{fmt_quality(picked['quality'], m)}</dd></div>
          <div><dt>{html.escape(latency_label(s))}</dt><dd>{ms(picked['latency_ms'])}</dd></div>
          <div><dt>Cost / 1k</dt><dd>{usd(picked['cost_per_1k_usd'])}</dd></div>
        </dl><p class="metric-note">Different studies measure different outcomes. Compare within each study.</p></div>"""
    else:
        metrics = '<div class="selection"><p class="pick-name">No approach selected</p></div>'
    labels = s["data"]["labels"]
    caveats = "".join(f"<li>{inline(c)}</li>" for c in s.get("caveats", []))
    outcome = s["outcome"]
    outcome_text = html.escape(OUTCOME[outcome["status"]])
    if outcome.get("evidence_url"):
        outcome_text = f"<a href='{html.escape(outcome['evidence_url'])}'>{outcome_text}</a>"
    caution = (f"<p class=study-caveat><strong>Keep in mind:</strong> {inline(s['caveats'][0])}</p>"
               if s.get("caveats") else "")
    study = (f"<p class=study-name>{html.escape(s['arena']['study'])}</p>"
             if s["arena"].get("study") else "")
    return f"""
<article class="study" id="{html.escape(s['arena']['name'])}">
  <div class="study-main">
    <div class="study-copy">
      <div class="study-meta"><h3><a href="{repo}">{name}</a></h3>
        <time datetime="{html.escape(s['snapshot']['created_at'])}">{s['snapshot']['created_at'][:10]}</time></div>
      {study}
      <h4>{inline(s['arena']['question'])}</h4>
      <p class="finding">{inline(finding)}</p>
      {caution}
      <p class="provenance">{s['data']['items']:,} evaluated items <span aria-hidden="true">/</span> {html.escape(LABELS[labels['source']])}</p>
      <div class="study-links"><a class="report-link" href="{html.escape(snapshot_url(s))}">Read the study <span aria-hidden="true">↗</span></a>
        <a href="{repo}">Source code <span aria-hidden="true">↗</span></a></div>
    </div>
    {metrics}
  </div>
  <details class="evidence">
    <summary>Compare approaches &amp; inspect the evidence</summary>
    <div class="evidence-body">
      <p class="full-finding">{inline(s['headline'])}</p>
      <div class="table-scroll" role="region" aria-label="{name} comparison" tabindex="0">
      <table><caption>All {len(s['contenders'])} approaches from this snapshot</caption>
        <thead><tr><th scope="col">Approach</th><th scope="col">{html.escape(m['label'])}</th>
        <th scope="col">{html.escape(latency_label(s))}</th><th scope="col">Cost / 1k</th></tr></thead>
        <tbody>{rows}</tbody></table></div>
      <div class="evidence-notes"><div><h5>Method &amp; labels</h5>
        <p>{inline(s['method']['rules'])}</p><p>{inline(labels.get('note', LABELS[labels['source']]))}</p></div>
        <div><h5>Limits &amp; follow-through</h5><ul>{caveats}</ul>
        <p>{outcome_text}. {inline(outcome.get('note', ''))}</p></div></div>
    </div>
  </details>
</article>"""


def render_page(summaries: list[dict[str, Any]], problems: list[Entry], built: str) -> str:
    missing = ""
    if problems:
        items = "".join(f"<li>{html.escape(p.repo)}: <code>{html.escape(p.problem or '')}</code></li>"
                        for p in problems)
        missing = f"<details class=missing><summary>{len(problems)} not shown</summary><ul>{items}</ul></details>"
    template = Path(__file__).with_name("page.html").read_text()
    return template.format(cards="".join(card(s) for s in summaries), missing=missing,
                           built=built, count=len(summaries))


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_table(summaries: list[dict[str, Any]]) -> str:
    lines = ["| Arena | Question | Finding | Labels | Outcome |", "|---|---|---|---|---|"]
    for s in summaries:
        outcome = OUTCOME[s["outcome"]["status"]]
        if s["outcome"].get("evidence_url"):
            outcome = f"[{outcome}]({s['outcome']['evidence_url']})"
        lines.append(
            f"| [{cell(title(s))}]({s['arena']['repo']}) | {cell(s['arena']['question'])} | "
            f"{cell(s['headline'])} [→]({snapshot_url(s)}) | "
            f"{LABELS[s['data']['labels']['source']]} | {outcome} |")
    return "\n".join(lines)


def build(*, local: bool, out: Path, readme: Path | None, registry: Path) -> list[Entry]:
    with httpx.Client(timeout=30, follow_redirects=True) as http:
        entries = [e for a in load_registry(registry) for e in fetch(a, local=local, http=http)]
    shown = sorted((e.summary for e in entries if e.summary),
                   key=lambda s: s["snapshot"]["created_at"], reverse=True)
    problems = [e for e in entries if e.problem]
    out.mkdir(parents=True, exist_ok=True)
    built = datetime.now(UTC).strftime("%Y-%m-%d")
    (out / "index.html").write_text(render_page(shown, problems, built))
    for asset in ("style.css", "theme.js"):
        (out / asset).write_text(Path(__file__).with_name(asset).read_text())
    (out / "summaries.json").write_text(json.dumps(shown, indent=1))
    if readme:
        readme.write_text(replace_block(readme.read_text(), render_table(shown), START, END))
    return entries


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="hub")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build", help="render site/index.html from every arena's latest summary")
    p.add_argument("--local", action="store_true", help="read sibling checkouts, not GitHub")
    p.add_argument("--out", type=Path, default=ROOT / "site")
    p.add_argument("--readme", action="store_true", help="also update the table in README.md")
    p.add_argument("--strict", action="store_true", help="exit 1 if any arena is missing")
    args = parser.parse_args(argv)
    entries = build(local=args.local, out=args.out,
                    readme=ROOT / "README.md" if args.readme else None,
                    registry=ROOT / "arenas.yaml")
    for e in entries:
        status = "ok" if e.summary else f"skipped ({e.problem})"
        print(f"{e.repo}/{e.path}: {status}")
    if args.strict and any(e.problem for e in entries):
        sys.exit(1)
