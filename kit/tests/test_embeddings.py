import json

import pytest
from arena_kit.embeddings import (
    EmbeddingStore,
    default_cache_dir,
    representation_settings,
    vector_key,
)


def test_content_addressed_reuse_across_instances_and_order(tmp_path):
    a = EmbeddingStore(tmp_path)
    b = EmbeddingStore(tmp_path)
    a.put("model", "document one", [1., 2.], provenance={"request_key": "batch-one"})
    a.put("model", "document two", [2., 1.])
    assert b.get("model", "document two") == [2., 1.]
    assert b.get("model", "document one") == [1., 2.]
    assert b.get("other-model", "document one") is None
    assert b.get("model", "document one ") is None
    assert b.get("model", "document one", {"dimensions": 2}) is None
    assert b.get("model", "document one", endpoint="https://other/embeddings") is None


def test_profile_ignores_only_price_and_batch_identity():
    first = {"model": "m", "input": ["text"], "dimensions": 256,
             "provider": {"max_price": {"prompt": .02}}}
    second = {**first, "input": ["another", "text"],
              "provider": {"max_price": {"prompt": .2}}}
    assert representation_settings(first) == representation_settings(second) == {"dimensions": 256}
    changed = {**first, "input_type": "search_query"}
    assert vector_key("m", "text", representation_settings(first)) != vector_key(
        "m", "text", representation_settings(changed))


def test_tampered_vector_is_detected_and_existing_entry_is_preserved(tmp_path):
    store = EmbeddingStore(tmp_path)
    store.put("m", "text", [1., 2.])
    store.put("m", "text", [3., 4.])
    assert store.get("m", "text") == [1., 2.]
    path = store.path("m", "text")
    record = json.loads(path.read_text())
    record["vector"][0] = 9.
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="integrity mismatch"):
        store.get("m", "text")


@pytest.mark.parametrize("bad", [[], [0., 0.], [float("nan")], [float("inf")], [True]])
def test_invalid_vectors_are_rejected(tmp_path, bad):
    with pytest.raises(ValueError):
        EmbeddingStore(tmp_path).put("m", "text", bad)


def test_cache_location_can_be_shared_or_overridden(monkeypatch, tmp_path):
    monkeypatch.setenv("ARENA_EMBEDDING_CACHE", str(tmp_path))
    assert default_cache_dir() == tmp_path
