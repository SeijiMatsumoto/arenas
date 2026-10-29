from __future__ import annotations

from arena_kit import ArenaSpec, arena_main

from . import contenders, dataset

SPEC = ArenaSpec(
    root=dataset.ROOT,
    load_items=dataset.load,
    load_contenders=contenders.load,
    score=dataset.score,
)


def main() -> None:
    arena_main(SPEC)
