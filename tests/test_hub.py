import json

from arena_kit import contract

from arena_hub import build as hub


def summary(name, created_at, mock=False, status="pending"):
    return {
        "schema": "arena-summary/1",
        "arena": {"name": name, "title": name.title(), "question": "Which option wins <here>?",
                  "repo": f"https://github.com/example/{name}"},
        "snapshot": {"id": "s1", "created_at": created_at, "path": "snapshots/s1"},
        "headline": "Use **a** because it is cheap.",
        "pick": {"name": "a", "reason": "cheap"},
        "metric": {"name": "accuracy", "label": "Accuracy", "higher_is_better": True,
                   "format": "percent"},
        "contenders": [
            {"name": "b", "quality": 0.5, "latency_ms": 10, "cost_per_1k_usd": None,
             "verdict": "worse"},
            {"name": "a", "quality": 0.9, "latency_ms": 1200, "cost_per_1k_usd": 0.01,
             "verdict": "pick"},
        ],
        "data": {"items": 1200, "labels": {"source": "reviewed"}},
        "method": {"rules": "fixed"},
        "outcome": {"status": status},
        "caveats": [],
        "mock": mock,
    }


def write_arena(root, name, s):
    (root / name / "snapshots").mkdir(parents=True)
    (root / name / "snapshots" / "latest.json").write_text(json.dumps(s))


def test_build_renders_valid_arenas_and_skips_mock_and_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(hub, "ROOT", tmp_path)
    write_arena(tmp_path, "one", summary("one", "2026-10-01T00:00:00+00:00"))
    write_arena(tmp_path, "two", summary("two", "2026-10-05T00:00:00+00:00", status="changed"))
    write_arena(tmp_path, "fake", summary("fake", "2026-10-06T00:00:00+00:00", mock=True))
    registry = tmp_path / "arenas.yaml"
    registry.write_text("arenas:\n" + "".join(
        f"  - {{repo: example/{n}, local: {n}}}\n" for n in ("one", "two", "fake", "gone")))
    readme = tmp_path / "README.md"
    readme.write_text(f"# Hub\n\n{hub.START}\nold\n{hub.END}\n")

    entries = hub.build(local=True, out=tmp_path / "site", readme=readme, registry=registry)

    assert [bool(e.summary) for e in entries] == [True, True, False, False]
    page = (tmp_path / "site" / "index.html").read_text()
    assert page.index("Two") < page.index("One")  # newest first
    assert "&lt;here&gt;" in page and "<strong>a</strong>" in page  # escaped, then markdown
    assert "2 not shown" in page
    table = readme.read_text()
    assert "old" not in table and "Changed in production" in table
    shown = json.loads((tmp_path / "site" / "summaries.json").read_text())
    for s in shown:
        contract.validate(s)


def test_pick_is_listed_first():
    rows = hub.ranked(summary("x", "2026-10-01T00:00:00+00:00"))
    assert [r["name"] for r in rows] == ["a", "b"]


def test_disclosure_preserves_all_evidence_and_approaches():
    s = summary("study", "2026-10-01T00:00:00+00:00")
    s["headline"] = "First finding. Another finding with **evidence**."
    s["caveats"] = ["Model labels can be wrong."]
    s["data"]["labels"]["note"] = "A <reviewed> source."
    s["contenders"] += [dict(s["contenders"][0], name=f"extra-{i}") for i in range(6)]
    page = hub.card(s)
    assert "<details" in page and "<details open" not in page
    assert "Another finding with <strong>evidence</strong>." in page
    assert "extra-5" in page
    assert "Model labels can be wrong." in page
    assert "A &lt;reviewed&gt; source." in page


def test_study_with_no_pick_does_not_invent_metrics():
    s = summary("study", "2026-10-01T00:00:00+00:00")
    s["pick"] = None
    page = hub.card(s)
    assert "No approach selected" in page
    assert '<dl class="metrics">' not in page
