"""Turn each entry of contenders.yaml into an async function item -> Call."""

from __future__ import annotations

from time import perf_counter
from typing import Any

import yaml
from arena_kit import Call, Client, Contender, MockClient, noisy_answer

from . import dataset

PROMPT = """Classify the headline into exactly one label: {labels}.
Answer with the label only.

Headline: {text}"""

KEYWORDS = {
    "funding": ("raises", "round", "series", "seed", "acquired"),
    "research": ("paper", "study", "researchers", "propose"),
    "opinion": ("why ", "case against", "not dead", "stopped"),
}


def keyword_rules(text: str) -> str:
    lowered = text.lower()
    for label, words in KEYWORDS.items():
        if any(w in lowered for w in words):
            return label
    return "release"


def _rules(entry: dict[str, Any]) -> Contender:
    async def fn(item: dict[str, Any]) -> Call:
        started = perf_counter()
        output = keyword_rules(item["input"])
        return Call(output, (perf_counter() - started) * 1000, cost_usd=0.0)

    return Contender(entry["name"], fn, kind="rules")


def _llm(entry: dict[str, Any], labels: list[str], client: Client | None) -> Contender:
    async def fn(item: dict[str, Any]) -> Call:
        body = {
            "model": entry["model"],
            "messages": [{"role": "user", "content": PROMPT.format(
                labels=", ".join(labels), text=item["input"])}],
            "temperature": 0,
            "max_tokens": 10,
        }
        if client is None:  # mock: right answer with probability mock_accuracy
            mock = MockClient(
                lambda _: noisy_answer(item["expected"], labels,
                                       entry.get("mock_accuracy", 0.85), entry["name"], item["id"]),
                latency_ms=entry.get("mock_latency_ms", 300),
            )
            return await mock.chat(body)
        return await client.chat(body, price_in=entry.get("price_in"),
                                 price_out=entry.get("price_out"), rpm=entry.get("rpm"))

    return Contender(entry["name"], fn, kind="llm", concurrency=entry.get("concurrency", 8))


def load(mock: bool) -> list[Contender]:
    config = yaml.safe_load((dataset.ROOT / "contenders.yaml").read_text())
    labels = dataset.labels(dataset.load())
    client = None if mock else Client(dataset.ROOT / ".cache")
    built = []
    for entry in config["contenders"]:
        if entry.get("enabled", True) is False:
            continue
        built.append(_rules(entry) if entry["type"] == "rules" else _llm(entry, labels, client))
    return built
