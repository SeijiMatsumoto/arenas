import asyncio
import importlib
import json
import sys

import httpx
import pytest
from arena_kit import contract, scaffold
from arena_kit.client import Client
from arena_kit.recommend import recommend
from arena_kit.stats import bootstrap_diff_ci, mcnemar_p, sign_test_p


def stat(quality, correct, p50=100.0, cost=1.0, errors=0, kind="llm"):
    return {"kind": kind, "quality": quality, "correct": correct, "p50_ms": p50,
            "cost_per_1k_usd": cost, "errors": errors}


def test_mcnemar_symmetric_and_bounded():
    assert mcnemar_p(0, 0) == 1.0
    assert mcnemar_p(3, 10) == mcnemar_p(10, 3)
    assert mcnemar_p(0, 12) < 0.001


def test_sign_test_and_bootstrap_are_deterministic():
    a = {str(i): 1.0 for i in range(20)}
    b = {str(i): 0.0 for i in range(20)}
    assert sign_test_p(a, b) < 0.001
    assert bootstrap_diff_ci(a, b) == bootstrap_diff_ci(a, b) == (1.0, 1.0, 1.0)


def test_recommend_picks_cheaper_tie_and_flags_worse():
    keys = [str(i) for i in range(100)]
    perfect = {k: True for k in keys}
    near = {k: k != "0" for k in keys}
    bad = {k: int(k) % 2 == 0 for k in keys}
    rec = recommend({
        "big": stat(1.0, perfect, p50=900, cost=5),
        "small": stat(0.99, near, p50=100, cost=0.5),
        "weak": stat(0.5, bad, p50=10, cost=0.01),
        "broken": stat(1.0, perfect, errors=2),
    })
    assert rec["top"] == "big"
    assert rec["pick"] == "small"
    assert rec["verdicts"] == {"big": "tied", "small": "pick", "weak": "worse",
                               "broken": "excluded"}


def test_recommend_unknown_cost_ranks_last():
    keys = {str(i): True for i in range(10)}
    rec = recommend({"a": stat(1.0, keys, p50=100, cost=None),
                     "b": stat(1.0, keys, p50=100, cost=2.0)})
    assert rec["pick"] == "b"


MANIFEST = """
name: demo-arena
question: Which thing should do the job?
repo: https://github.com/example/demo-arena
labels: {source: hand-written}
outcome: {status: pending}
"""


def make_summary(tmp_path, created_at, mock):
    (tmp_path / "arena.yaml").write_text(MANIFEST)
    folder = tmp_path / "snapshots" / created_at[:10]
    folder.mkdir(parents=True, exist_ok=True)
    s = contract.build(
        contract.load_manifest(tmp_path), snapshot_id=folder.name, created_at=created_at,
        snapshot_path=f"snapshots/{folder.name}", headline="Use **a** for everything.",
        pick={"name": "a", "reason": "best"}, metric={"name": "accuracy", "label": "Accuracy",
                                                       "higher_is_better": True,
                                                       "format": "percent"},
        contenders=[{"name": "a", "quality": 1.0, "latency_ms": 1.0, "cost_per_1k_usd": None,
                     "verdict": "pick"}], items=3, rules="r", mock=mock)
    contract.write(tmp_path, folder, s)
    return s


def test_contract_rejects_bad_summaries(tmp_path):
    s = make_summary(tmp_path, "2026-10-01T00:00:00+00:00", mock=False)
    with pytest.raises(contract.ContractError, match="labels/source"):
        contract.validate({**s, "data": {"items": 3, "labels": {"source": "vibes"}}})
    with pytest.raises(contract.ContractError, match="schema"):
        contract.validate({**s, "schema": "arena-summary/0"})


def test_mock_never_replaces_real_latest(tmp_path):
    make_summary(tmp_path, "2026-10-01T00:00:00+00:00", mock=False)
    make_summary(tmp_path, "2026-10-02T00:00:00+00:00", mock=True)
    latest = json.loads((tmp_path / "snapshots" / "latest.json").read_text())
    assert latest["mock"] is False
    make_summary(tmp_path, "2026-10-03T00:00:00+00:00", mock=False)
    latest = json.loads((tmp_path / "snapshots" / "latest.json").read_text())
    assert latest["snapshot"]["id"] == "2026-10-03"


def test_refresh_applies_new_outcome(tmp_path):
    make_summary(tmp_path, "2026-10-01T00:00:00+00:00", mock=False)
    (tmp_path / "arena.yaml").write_text(MANIFEST.replace(
        "{status: pending}", "{status: changed, evidence_url: 'https://example.com/pr/1'}"))
    contract.refresh(tmp_path)
    for path in ("snapshots/latest.json", "snapshots/2026-10-01/summary.json"):
        assert json.loads((tmp_path / path).read_text())["outcome"]["status"] == "changed"


def test_client_caches_exact_request(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}],
                                         "usage": {"cost": 0.001}})

    async def go():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = Client(tmp_path, http)
        first = await client.chat({"model": "m", "messages": []})
        second = await client.chat({"model": "m", "messages": []})
        return first, second

    first, second = asyncio.run(go())
    assert (first.output, first.cost_usd, first.cached) == ("ok", 0.001, False)
    assert second.cached and len(calls) == 1


def test_unbilled_policy_does_not_retry_unknown_outcomes(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(502, text="bad gateway")

    async def go():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        await Client(tmp_path, http, retry="unbilled").post("/v1/x", {"model": "m"})

    with pytest.raises(RuntimeError, match="502"):
        asyncio.run(go())
    assert len(calls) == 1


def test_scaffolded_arena_runs_offline(tmp_path, monkeypatch):
    root = scaffold.create("demo-arena", "Which demo contender should we use?", tmp_path)
    assert (root / ".gitignore").exists() and (root / "snapshots").is_dir()
    assert "__PACKAGE__" not in (root / "pyproject.toml").read_text()
    monkeypatch.syspath_prepend(str(root / "src"))
    monkeypatch.syspath_prepend(str(root / "tests"))
    test_module = importlib.import_module("test_pipeline")
    test_module.test_mock_run_snapshots_a_valid_summary(tmp_path / "out")
    test_module.test_keyword_rules_cover_every_starter_label()
    for name in [m for m in sys.modules if m.startswith(("demo_arena", "test_pipeline"))]:
        del sys.modules[name]


def test_scaffold_refuses_existing_and_bad_names(tmp_path):
    scaffold.create("x-arena", "Which demo contender should we use?", tmp_path)
    with pytest.raises(SystemExit):
        scaffold.create("x-arena", "Which demo contender should we use?", tmp_path)
    with pytest.raises(SystemExit):
        scaffold.create("Bad Name", "Which demo contender should we use?", tmp_path)
