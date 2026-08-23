"""Deterministic tests for V5.1 P3: optional embedding + RRF, BM25 fallback."""

from minicc.memory.l1 import L1Memory, MemoryStore, rrf_fuse


def _memory(content: str, record_id: int | None = None, priority: int = 50) -> L1Memory:
    return L1Memory(
        type="fact",
        content=content,
        priority=priority,
        scope="project",
        record_id=record_id,
    )


# A tiny deterministic bag-of-words embedder over a fixed vocabulary — no random
# vector-library dependency, so RRF / hybrid search are fully reproducible.
_VOCAB = ["auth", "deploy", "billing", "postgres", "login", "bug", "test"]


def bag_embedder(text: str) -> list[float]:
    lowered = text.lower()
    return [1.0 if word in lowered else 0.0 for word in _VOCAB]


# --- rrf_fuse ---------------------------------------------------------------


def test_rrf_fuse_merges_rankings() -> None:
    a = _memory("A", record_id=1)
    b = _memory("B", record_id=2)
    c = _memory("C", record_id=3)

    # A is top of list1, B is top of list2; C appears mid in both.
    fused = rrf_fuse([[a, c], [b, c]], limit=3)
    assert {m.record_id for m in fused} == {1, 2, 3}
    # C ranks high in both lists, so its summed RRF score tops the pair A vs B.
    assert fused[0].record_id == 3


def test_rrf_fuse_respects_limit_and_empty() -> None:
    entries = [_memory("A", record_id=1), _memory("B", record_id=2)]
    assert len(rrf_fuse([entries, entries], limit=1)) == 1
    assert rrf_fuse([], limit=5) == []


# --- search strategy + embedding storage ------------------------------------


def test_search_without_embedder_is_bm25(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db")
    store.initialize()
    store.add_memories(
        [_memory("deploy the auth service with make deploy-auth"), _memory("billing uses postgres")]
    )
    results = store.search("auth deploy", scope="project", limit=5)
    assert [m.content for m in results] == ["deploy the auth service with make deploy-auth"]


def test_search_with_embedder_is_hybrid(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db", embedder=bag_embedder)
    store.initialize()
    store.add_memories(
        [_memory("deploy the auth service"), _memory("billing uses postgres")]
    )
    results = store.search("auth deploy", scope="project", limit=5)
    assert results  # embedding + BM25 both surface the auth memory
    assert results[0].content == "deploy the auth service"


def test_add_memories_stores_embedding_and_ranks(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory" / "project.db", embedder=bag_embedder)
    store.initialize()
    store.add_memories(
        [_memory("auth bug in login"), _memory("billing is on postgres")]
    )
    # Pure embedding recall (bypass BM25) ranks the auth memory first for an
    # auth query even though BM25 would too — here we assert the vector is stored
    # and cosine-similarity ranking returns something meaningful.
    ranked = store._embedding_search(bag_embedder("auth login"), scope="project", limit=5)
    assert ranked
    assert ranked[0].content == "auth bug in login"


def test_embedder_failure_degrades_to_bm25(tmp_path) -> None:
    def broken_embedder(text: str) -> list[float]:
        del text
        raise RuntimeError("no model")

    store = MemoryStore(tmp_path / "memory" / "project.db", embedder=broken_embedder)
    store.initialize()
    # add_memories must not fail when the embedder throws.
    store.add_memories([_memory("deploy the auth service")])
    assert store.count_memories() == 1
    # search must degrade to BM25 rather than propagate the embedder error.
    results = store.search("auth deploy", scope="project", limit=5)
    assert [m.content for m in results] == ["deploy the auth service"]