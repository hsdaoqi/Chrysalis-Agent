from pathlib import Path

from chrysalis.memory import MemoryReviewStore, PersistDecision
from chrysalis.working import WorkingMemory


def _decision(target: str = "fact") -> PersistDecision:
    return PersistDecision(
        should_persist=True,
        target=target,
        value_score=0.82,
        confidence=0.9,
        reason="stable project memory",
        evidence=["user asked to remember it"],
        stability="high",
        reuse_likelihood="medium",
        safety_risk="low",
    )


def test_memory_review_store_creates_updates_and_approves(tmp_path: Path) -> None:
    store = MemoryReviewStore(tmp_path / "data" / "memory_reviews.json", tmp_path / "memory")
    working = WorkingMemory()
    working.request_long_term_update("The release workspace is D:/Project/Chrysalis.")

    item = store.create_from_decision(
        task="Remember the release workspace.",
        result={"ok": True, "final": "done"},
        decision=_decision(),
        working=working,
        session_id="session-1",
    )

    assert item is not None
    assert item["status"] == "pending"
    assert item["target"] == "fact"
    assert "release workspace" in item["content"]

    duplicate = store.create_from_decision(
        task="Remember the release workspace.",
        result={"ok": True, "final": "done"},
        decision=_decision(),
        working=working,
        session_id="session-1",
    )
    assert duplicate["id"] == item["id"]
    assert len(store.list_items()) == 1

    updated = store.update_item(
        item["id"],
        title="Release workspace",
        content="Use D:/Project/Chrysalis for release checks.",
        target="fact",
    )
    assert updated is not None
    assert updated["title"] == "Release workspace"
    assert updated["target"] == "fact"

    approved = store.approve(item["id"])
    assert approved["ok"] is True
    approved_item = approved["item"]
    assert approved_item["status"] == "approved"

    global_memory = (tmp_path / "memory" / "global_mem.txt").read_text(encoding="utf-8")
    assert "Release workspace" in global_memory
    assert "target: fact" in global_memory
    assert "Use D:/Project/Chrysalis" in global_memory


def test_memory_review_store_sop_approval_keeps_global_memory_clean(tmp_path: Path) -> None:
    store = MemoryReviewStore(tmp_path / "data" / "memory_reviews.json", tmp_path / "memory")

    item = store.create_from_decision(
        task="Remember the release checklist.",
        result={"ok": True, "final": "Run tests, build, then package."},
        decision=_decision("sop"),
        session_id="session-sop",
    )

    assert item is not None
    approved = store.approve(
        item["id"],
        title="Release checklist",
        content="Run tests, build, then package.",
        target="sop",
        artifact={"name": "release-checklist", "path": str(tmp_path / "skills" / "release-checklist")},
    )

    assert approved["ok"] is True
    assert approved["item"]["status"] == "approved"
    assert approved["item"]["artifact"]["name"] == "release-checklist"
    assert not (tmp_path / "memory" / "global_mem.txt").exists()


def test_memory_review_store_discards_pending_item(tmp_path: Path) -> None:
    store = MemoryReviewStore(tmp_path / "data" / "memory_reviews.json", tmp_path / "memory")

    item = store.create_from_decision(
        task="Remember the user prefers compact summaries.",
        result={"ok": True, "final": "User prefers compact summaries."},
        decision=_decision("user_profile"),
        session_id="session-2",
    )

    assert item is not None
    discarded = store.discard(item["id"])
    assert discarded["ok"] is True
    assert discarded["item"]["status"] == "discarded"
    assert store.list_items(status="pending") == []
    assert store.list_items(status="discarded")[0]["id"] == item["id"]
