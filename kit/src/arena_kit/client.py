"""OpenRouter HTTP plumbing: disk cache keyed by the exact request, rate limits, retries.

Two retry policies, because "retry on error" is only safe when the failed call cannot have been
billed:

- "transient" retries timeouts, 408/429/5xx and upstream errors reported inside a 200. Fine for
  cheap calls where a duplicate charge doesn't matter.
- "unbilled" retries only 429 and connection failures that happened before the request was sent.
  A timeout or 5xx is an unknown outcome (the provider may have run and billed it), so it is
  raised instead of silently paid for twice.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

import httpx

OPENROUTER = "https://openrouter.ai/api"
TRANSIENT_STATUS = {408, 429, 500, 502, 503, 524, 529}
MAX_ATTEMPTS = 8

RetryPolicy = Literal["transient", "unbilled"]


@dataclass
class Response:
    payload: dict[str, Any]
    latency_ms: float
    cached: bool


@dataclass
class Call:
    """What a contender returns for one item. `cost_usd` is None when unknown, never 0."""

    output: Any
    latency_ms: float
    cost_usd: float | None = None
    cached: bool = False


class CacheMiss(RuntimeError):
    """Raised in cache-only mode instead of making a paid call."""


class RateLimiter:
    """Spaces request starts so a model never exceeds `rpm` requests per minute."""

    def __init__(self, rpm: float):
        self.interval = 60 / rpm
        self.next_slot = 0.0
        self.lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self.lock:
            now = asyncio.get_running_loop().time()
            delay = max(0.0, self.next_slot - now)
            self.next_slot = max(now, self.next_slot) + self.interval
        await asyncio.sleep(delay)


def retry_delay(response: httpx.Response | None, attempt: int) -> float:
    """Honor Retry-After when the server sends it; otherwise exponential backoff, capped."""
    header = response.headers.get("retry-after") if response is not None else None
    if header:
        try:
            return float(header) + random.random()
        except ValueError:
            pass
    return min(2**attempt, 30) + random.random()


def request_key(url: str, body: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps([url, body], sort_keys=True).encode()).hexdigest()


def usage_cost(payload: dict[str, Any], price_in: float | None = None,
               price_out: float | None = None) -> float | None:
    """USD cost OpenRouter reported, or an estimate from per-1M-token prices."""
    usage = payload.get("usage") or {}
    if usage.get("cost") is not None:
        return float(usage["cost"])
    if price_in is None:
        return None
    tokens_in = usage.get("prompt_tokens") or usage.get("total_tokens") or 0
    tokens_out = usage.get("completion_tokens") or 0
    return (tokens_in * price_in + tokens_out * (price_out or 0)) / 1e6


def message_text(payload: dict[str, Any]) -> str:
    return (payload.get("choices") or [{}])[0].get("message", {}).get("content") or ""


class Client:
    """POST with a disk cache keyed by the exact request, so reruns only pay for what's new.

    Cached hits report the latency measured when the call was first made, flagged `cached`,
    so latency stats can exclude them.
    """

    def __init__(self, cache_dir: Path, http: httpx.AsyncClient | None = None, *,
                 api_key_env: str = "OPENROUTER_API_KEY", cache_only: bool = False,
                 retry: RetryPolicy = "transient"):
        self.cache_dir = cache_dir
        self.http = http or httpx.AsyncClient(timeout=120)
        self.api_key_env = api_key_env
        self.cache_only = cache_only
        self.retry = retry
        self.limiters: dict[str, RateLimiter] = {}
        self.gates: dict[str, asyncio.Semaphore] = {}

    def gate(self, name: str, concurrency: int) -> asyncio.Semaphore:
        return self.gates.setdefault(name, asyncio.Semaphore(concurrency))

    def _retryable(self, status: int | None, error: Exception | None) -> bool:
        if self.retry == "transient":
            return error is not None or status in TRANSIENT_STATUS
        # "unbilled": only failures where the provider never ran the request.
        return status == 429 or isinstance(error, httpx.ConnectError)

    async def post(self, path: str, body: dict[str, Any], *, rpm: float | None = None,
                   limit_key: str | None = None, base_url: str = OPENROUTER) -> Response:
        url = f"{base_url}{path}"
        key = request_key(url, body)
        cache_path = self.cache_dir / key[:2] / f"{key}.json"
        if cache_path.exists():
            hit = json.loads(cache_path.read_text())
            return Response(hit["response"], hit["latency_ms"], True)
        if self.cache_only:
            raise CacheMiss(f"not cached: {body.get('model')} {path}")

        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError(f"{self.api_key_env} is not set")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        limiter = self.limiters.setdefault(limit_key or url, RateLimiter(rpm)) if rpm else None

        for attempt in range(MAX_ATTEMPTS):
            last = attempt == MAX_ATTEMPTS - 1
            if limiter:
                await limiter.wait()
            started = perf_counter()
            try:
                response = await self.http.post(url, json=body, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as error:
                if last or not self._retryable(None, error):
                    raise
                await asyncio.sleep(retry_delay(None, attempt))
                continue
            latency_ms = (perf_counter() - started) * 1000
            if response.status_code >= 400:
                if not last and self._retryable(response.status_code, None):
                    await asyncio.sleep(retry_delay(response, attempt))
                    continue
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
            payload = response.json()
            if "error" in payload and not any(k in payload for k in ("data", "choices", "results")):
                # OpenRouter sometimes reports upstream failures inside a 200.
                if not last and self.retry == "transient":
                    await asyncio.sleep(retry_delay(None, attempt))
                    continue
                raise RuntimeError(f"Upstream error: {str(payload['error'])[:300]}")
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({"response": payload, "latency_ms": latency_ms}))
            return Response(payload, latency_ms, False)
        raise AssertionError("unreachable")

    async def chat(self, body: dict[str, Any], *, price_in: float | None = None,
                   price_out: float | None = None, rpm: float | None = None) -> Call:
        """Chat completion returning the message text as a `Call`."""
        r = await self.post("/v1/chat/completions", body, rpm=rpm, limit_key=body.get("model"))
        return Call(message_text(r.payload), r.latency_ms,
                    usage_cost(r.payload, price_in, price_out), r.cached)
