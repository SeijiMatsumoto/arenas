"""Offline stand-in for `Client`, so every pipeline runs end to end without a key or a bill."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from typing import Any

from .client import Call, Response


def unit(*parts: Any) -> float:
    """Deterministic value in [0, 1) from any JSON-able parts."""
    digest = hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def noisy_answer(expected: str, choices: Sequence[str], accuracy: float, *seed: Any) -> str:
    """`expected` with probability `accuracy`, otherwise another choice; same seed, same answer."""
    if unit("hit", *seed) < accuracy or len(choices) < 2:
        return expected
    others = [c for c in choices if c != expected]
    return others[int(unit("miss", *seed) * len(others))]


class MockClient:
    """Same `post`/`chat` surface as `Client`. `responder(body)` returns the message text."""

    def __init__(self, responder: Callable[[dict[str, Any]], str] | None = None,
                 latency_ms: float = 50.0, cost_usd: float = 0.0):
        self.responder = responder or (lambda body: "")
        self.latency_ms = latency_ms
        self.cost_usd = cost_usd

    async def post(self, path: str, body: dict[str, Any], **_: Any) -> Response:
        text = self.responder(body)
        payload = {"choices": [{"message": {"content": text}}], "usage": {"cost": self.cost_usd}}
        jitter = 0.5 + unit(path, body)
        return Response(payload, self.latency_ms * jitter, False)

    async def chat(self, body: dict[str, Any], **_: Any) -> Call:
        r = await self.post("/v1/chat/completions", body)
        return Call(r.payload["choices"][0]["message"]["content"], r.latency_ms, self.cost_usd)
