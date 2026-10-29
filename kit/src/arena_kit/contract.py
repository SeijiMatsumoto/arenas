"""The summary contract: one JSON file per study that the hub reads, whatever language wrote it.

Two inputs make a summary:
- `arena.yaml` at the arena root, hand-written: the question, where labels came from, what changed
  in the product, caveats. Edit it any time and run `arena-kit refresh`.
- The generated part from a snapshot: headline, pick, per-contender numbers.

Each snapshot folder gets `summary.json`; `snapshots/latest.json` is a copy of the newest one and
is the path the hub fetches.
"""

from __future__ import annotations

import json
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

SCHEMA_ID = "arena-summary/1"
MANIFEST = "arena.yaml"
LATEST = "latest.json"
# Keys of a summary that come from arena.yaml rather than from a run.
MANIFEST_KEYS = ("outcome", "caveats")


class ContractError(ValueError):
    pass


@cache
def schema() -> dict[str, Any]:
    return json.loads(files("arena_kit").joinpath("summary.schema.json").read_text())


def validate(summary: dict[str, Any]) -> None:
    errors = sorted(Draft202012Validator(schema()).iter_errors(summary), key=lambda e: e.path)
    if errors:
        lines = [f"  {'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in errors]
        raise ContractError("summary does not match arena-summary/1:\n" + "\n".join(lines))


def load_manifest(root: Path) -> dict[str, Any]:
    path = root / MANIFEST
    if not path.exists():
        raise ContractError(f"{path} is missing; it holds the question, label source and outcome")
    manifest = yaml.safe_load(path.read_text()) or {}
    for key in ("name", "question", "repo", "labels", "outcome"):
        if key not in manifest:
            raise ContractError(f"{path}: missing '{key}'")
    return manifest


def build(manifest: dict[str, Any], *, snapshot_id: str, created_at: str, snapshot_path: str,
          headline: str, pick: dict[str, str] | None, metric: dict[str, Any],
          contenders: list[dict[str, Any]], items: int, rules: str, mock: bool,
          test: str | None = "exact McNemar, paired", alpha: float | None = 0.05,
          study: str | None = None) -> dict[str, Any]:
    arena = {k: manifest[k] for k in ("name", "title", "question", "repo") if k in manifest}
    if study:
        arena["study"] = study
    data = {"items": items, "labels": manifest["labels"]}
    if manifest.get("data"):
        data["description"] = manifest["data"]
    method = {"rules": rules, **({"test": test} if test else {}),
              **({"alpha": alpha} if alpha is not None else {})}
    summary = {
        "schema": SCHEMA_ID,
        "arena": arena,
        "snapshot": {"id": snapshot_id, "created_at": created_at, "path": snapshot_path},
        "headline": headline,
        "pick": pick,
        "metric": metric,
        "contenders": contenders,
        "data": data,
        "method": method,
        "outcome": manifest["outcome"],
        "caveats": manifest.get("caveats", []),
        "mock": mock,
    }
    validate(summary)
    return summary


def write(root: Path, snapshot_dir: Path, summary: dict[str, Any]) -> Path:
    """Write <snapshot>/summary.json, and snapshots/latest.json when this snapshot is the newest."""
    validate(summary)
    text = json.dumps(summary, indent=2) + "\n"
    path = snapshot_dir / "summary.json"
    path.write_text(text)
    latest = snapshot_dir.parent / LATEST
    current = json.loads(latest.read_text()) if latest.exists() else None
    # A mock run never replaces a real one: the hub would drop the arena's card.
    if current is None or (
        current["snapshot"]["created_at"] <= summary["snapshot"]["created_at"]
        and not (summary["mock"] and not current["mock"])
    ):
        latest.write_text(text)
    return path


def refresh(root: Path, snapshots_dir: Path | None = None) -> Path:
    """Re-copy arena.yaml metadata into the latest summary (after editing outcome or caveats)."""
    manifest = load_manifest(root)
    latest = (snapshots_dir or root / "snapshots") / LATEST
    if not latest.exists():
        raise ContractError(f"{latest} does not exist yet; take a snapshot first")
    summary = json.loads(latest.read_text())
    for key in ("name", "title", "question", "repo"):
        if key in manifest:
            summary["arena"][key] = manifest[key]
    summary["data"]["labels"] = manifest["labels"]
    if manifest.get("data"):
        summary["data"]["description"] = manifest["data"]
    for key in MANIFEST_KEYS:
        summary[key] = manifest.get(key, [] if key == "caveats" else summary.get(key))
    validate(summary)
    text = json.dumps(summary, indent=2) + "\n"
    latest.write_text(text)
    snapshot_summary = root / summary["snapshot"]["path"] / "summary.json"
    if snapshot_summary.exists():
        snapshot_summary.write_text(text)
    return latest
