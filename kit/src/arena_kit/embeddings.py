"""Shared, content-addressed storage for genuine embedding vectors.

Independent of arena IDs and request batch order. This store is local data, not a vector DB.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_ENDPOINT = "https://openrouter.ai/api/v1/embeddings"


def default_cache_dir() -> Path:
    return Path(os.environ.get("ARENA_EMBEDDING_CACHE", Path.home() / ".cache/arena-kit/embeddings"))


def representation_settings(body: dict) -> dict:
    """Keep representation/routing options, excluding only the price filter and input batch."""
    settings = {k: v for k, v in body.items() if k not in {"model", "input"}}
    if "provider" in settings:
        provider = {k: v for k, v in settings["provider"].items() if k != "max_price"}
        if provider:
            settings["provider"] = provider
        else:
            del settings["provider"]
    return settings


def identity(model: str, text: str, settings: dict | None = None,
             endpoint: str = DEFAULT_ENDPOINT) -> dict:
    return {"model": model, "endpoint": endpoint, "settings": settings or {},
            "text_sha256": hashlib.sha256(text.encode()).hexdigest()}


def vector_key(model: str, text: str, settings: dict | None = None,
               endpoint: str = DEFAULT_ENDPOINT) -> str:
    profile = identity(model, text, settings, endpoint)
    return hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()


def validate_vector(vector: list[float]) -> None:
    if not isinstance(vector, list) or not vector:
        raise ValueError("embedding vector must be a nonempty list")
    if any(type(x) not in {int, float} or not math.isfinite(x) for x in vector):
        raise ValueError("embedding vector must contain finite numbers")
    norm = math.hypot(*vector)
    if not norm or not math.isfinite(norm):
        raise ValueError("embedding vector must have a finite nonzero norm")


def checksum(vector: list[float]) -> str:
    return hashlib.sha256(json.dumps(vector, separators=(",", ":")).encode()).hexdigest()


class EmbeddingStore:
    def __init__(self, root: Path | None = None):
        self.root = root if root is not None else default_cache_dir()

    def path(self, model: str, text: str, settings: dict | None = None,
             endpoint: str = DEFAULT_ENDPOINT) -> Path:
        key = vector_key(model, text, settings, endpoint)
        return self.root / key[:2] / f"{key}.json"

    def get(self, model: str, text: str, settings: dict | None = None,
            endpoint: str = DEFAULT_ENDPOINT) -> list[float] | None:
        path = self.path(model, text, settings, endpoint)
        if not path.exists():
            return None
        record = json.loads(path.read_text())
        expected = identity(model, text, settings, endpoint)
        if record.get("schema") != "arena-embedding/1" or record.get("identity") != expected:
            raise ValueError(f"embedding identity mismatch: {path}")
        vector = record["vector"]
        validate_vector(vector)
        if len(vector) != record["dimensions"] or checksum(vector) != record["vector_sha256"]:
            raise ValueError(f"embedding integrity mismatch: {path}")
        return vector

    def put(self, model: str, text: str, vector: list[float], settings: dict | None = None,
            endpoint: str = DEFAULT_ENDPOINT, *, provenance: dict[str, Any] | None = None) -> str:
        validate_vector(vector)
        target = self.path(model, text, settings, endpoint)
        # Preserve the first valid response for a profile; aliases are not immutable model
        # versions. Use version-specific IDs/settings or a separate store when refreshing.
        if self.get(model, text, settings, endpoint) is not None:
            return target.stem
        record = {"schema": "arena-embedding/1", "identity": identity(model, text, settings, endpoint),
                  "dimensions": len(vector), "vector": vector, "vector_sha256": checksum(vector),
                  "created_at": datetime.now(UTC).isoformat(), "provenance": provenance or {}}
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(json.dumps(record) + "\n")
            # Link is an atomic, no-overwrite install on the same filesystem.
            try:
                os.link(temporary, target)
            except FileExistsError:
                self.get(model, text, settings, endpoint)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return target.stem
