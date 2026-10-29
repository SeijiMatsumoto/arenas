"""Items and scoring. Each item needs an "id"; "tags" are optional."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "datasets" / "starter.jsonl"


def load(path: Path = DATASET) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def labels(items: list[dict[str, Any]]) -> list[str]:
    return sorted({i["expected"] for i in items})


def score(item: dict[str, Any], output: Any) -> bool:
    return str(output).strip().strip(".").lower() == item["expected"]
