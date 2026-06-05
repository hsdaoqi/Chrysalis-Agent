from datetime import datetime, timedelta
from pathlib import Path

from chrysalis.memory import PersistDecision
from chrysalis.skills.curator import SkillCurator
from chrysalis.skills.store import SkillStore
from chrysalis.working import WorkingMemory


def test_skill_store_create_search_and_view(tmp_path: Path) -> None:
    store = SkillStore(skills_dir=tmp_path / "skills", project_root=tmp_path)
    record = store.create(
        name="web article extract",
        description="Extract and summarize a web article.",
        body="# Web Article Extract\n\n## Steps\n1. Scan the page.\n2. Summarize the article.\n",
        category="browser",
        tags=["web", "summary"],
        status="active",
    )

    assert record.path == tmp_path / "skills" / "web-article-extract"
    assert not (tmp_path / "skills" / "browser" / "web-article-extract").exists()
    assert "## About" in record.body
    l1_text = (tmp_path / "memory" / "global_mem_insight.txt").read_text(encoding="utf-8")
    assert l1_text == "skills/web-article-extract/SKILL.md\n"

    (record.path / "helper.py").write_text("print('ok')\n", encoding="utf-8")
    results = store.search("summarize web article", top_k=3)
    assert results
    assert results[0].name == record.name

    viewed = store.view(record.name)
    assert viewed["ok"] is True
    assert "Summarize the article." in viewed["content"]
    assert viewed["metadata"]["description"] == "Extract and summarize a web article."
    assert "helper.py" in viewed["linked_files"]["scripts"]


def test_skill_validation_flags_missing_sections(tmp_path: Path) -> None:
    store = SkillStore(skills_dir=tmp_path / "skills", project_root=tmp_path)
    record = store.create(
        name="broken skill",
        description="",
        body="# Broken Skill\n\n## Steps\n1. Do something.\n",
        category="general",
        status="draft",
    )
    curator = SkillCurator(store=store, min_turns=1, min_tool_calls=1)

    validation = curator.validate_skill(record)
    assert validation["ok"] is False
    assert "missing description" in validation["issues"]
    assert any("When To Use" in issue for issue in validation["issues"])


def test_skill_curator_promotes_valid_draft(tmp_path: Path) -> None:
    store = SkillStore(skills_dir=tmp_path / "skills", project_root=tmp_path)
    curator = SkillCurator(store=store, min_turns=1, min_tool_calls=1)
    working = WorkingMemory()
    working.request_long_term_update("This workflow is reusable.")

    result = curator.maybe_create_draft(
        task="Summarize a web article",
        result={"ok": True, "final": "done", "usage": {"turns": 2}},
        working=working,
        history_lines=["[USER]: Summarize a web article", "[Agent] done"],
        tool_trace=[{"tool": "web_scan", "args": {"url": "https://example.com"}, "ok": True}],
        session_id="session-1",
    )

    assert result["ok"] is True
    assert result["promoted"] is True
    assert result["skill"]["status"] == "active"
    record = store.find(result["skill"]["name"])
    assert record is not None
    assert record.path.parent == tmp_path / "skills"
    assert "## About" in record.body
    pointer = f"{record.path.relative_to(tmp_path).as_posix()}/SKILL.md"
    l1_text = (tmp_path / "memory" / "global_mem_insight.txt").read_text(encoding="utf-8")
    assert pointer in l1_text.splitlines()


def test_skill_store_install_from_path_uses_flat_layout(tmp_path: Path) -> None:
    source = tmp_path / "source-skill"
    source.mkdir()
    (source / "SKILL.md").write_text(
        "# Source Skill\n\n## When To Use\n- External workflow.\n\n## Steps\n1. Run helper.\n",
        encoding="utf-8",
    )
    (source / "helper.py").write_text("print('helper')\n", encoding="utf-8")
    store = SkillStore(skills_dir=tmp_path / "skills", project_root=tmp_path)

    result = store.install_from_path(source, category="browser")

    assert result["ok"] is True
    record = store.find("source-skill")
    assert record is not None
    assert record.path == tmp_path / "skills" / "source-skill"
    l1_text = (tmp_path / "memory" / "global_mem_insight.txt").read_text(encoding="utf-8")
    assert "skills/source-skill/SKILL.md" in l1_text.splitlines()
    viewed = store.view(record.name)
    assert viewed["ok"] is True
    assert "helper.py" in viewed["linked_files"]["scripts"]


def test_skill_curator_merges_similar_draft_into_existing_skill(tmp_path: Path) -> None:
    store = SkillStore(skills_dir=tmp_path / "skills", project_root=tmp_path)
    store.create(
        name="browser-web-article-extract",
        description="Extract and summarize web articles.",
        body=(
            "# Browser Web Article Extract\n\n"
            "## When To Use\n"
            "- Summarize a webpage.\n\n"
            "## Steps\n"
            "1. Scan the page.\n"
            "2. Summarize the article.\n\n"
            "## Provenance\n"
            "- source_task: Summarize a webpage\n"
        ),
        category="browser",
        tags=["web", "summary"],
        status="active",
    )
    curator = SkillCurator(store=store, min_turns=1, min_tool_calls=1)
    working = WorkingMemory()
    working.request_long_term_update("This workflow is reusable.")

    result = curator.maybe_create_draft(
        task="Summarize a web article",
        result={"ok": True, "final": "done", "usage": {"turns": 2}},
        working=working,
        history_lines=["[USER]: Summarize a web article", "[Agent] done"],
        tool_trace=[{"tool": "web_scan", "args": {"url": "https://example.com"}, "ok": True}],
        session_id="session-2",
    )

    assert result["ok"] is True
    assert result["merged"] is True
    merged = store.find("browser-web-article-extract")
    assert merged is not None
    assert "Related Drafts" in merged.body


def test_skill_curator_rejects_agent_turns_when_memory_judge_says_no(tmp_path: Path) -> None:
    store = SkillStore(skills_dir=tmp_path / "skills", project_root=tmp_path)
    curator = SkillCurator(store=store)
    working = WorkingMemory()

    result = curator.maybe_create_draft(
        task="Summarize a web article",
        result={"ok": True, "final": "done", "agent_turns": 20},
        working=working,
        history_lines=["[USER]: Summarize a web article", "[Agent] done"],
        tool_trace=[],
        session_id="session-turns",
        memory_decision=PersistDecision(
            should_persist=False,
            target="session_only",
            value_score=0.2,
            confidence=0.8,
            reason="turn count alone is not enough",
            evidence=["successful but low value"],
            stability="low",
            reuse_likelihood="low",
            safety_risk="low",
        ),
    )

    assert result["ok"] is False
    assert result["skipped"] is True
    assert "memory judge rejected" in result["reason"]


def test_skill_usage_pin_and_search_boost(tmp_path: Path) -> None:
    store = SkillStore(skills_dir=tmp_path / "skills", project_root=tmp_path)
    store.create(
        name="generic article summary",
        description="Summarize articles.",
        body="# Generic Article Summary\n\n## When To Use\n- Articles.\n\n## Steps\n1. Summarize.\n",
        category="browser",
        tags=["summary"],
        status="active",
    )
    pinned = store.create(
        name="web article specialist",
        description="Summarize web articles.",
        body="# Web Article Specialist\n\n## When To Use\n- Web articles.\n\n## Steps\n1. Scan.\n2. Summarize.\n",
        category="browser",
        tags=["summary", "web"],
        status="active",
    )

    store.set_pinned(pinned.name, True)
    results = store.search("summarize web article", top_k=2)

    assert results[0].name == pinned.name
    assert "pinned" in results[0].reasons


def test_skill_search_is_manual_and_view_records_usage(tmp_path: Path) -> None:
    store = SkillStore(skills_dir=tmp_path / "skills", project_root=tmp_path)
    record = store.create(
        name="web article extract",
        description="Extract and summarize a web article.",
        body="# Web Article Extract\n\n## When To Use\n- Web article summary.\n\n## Steps\n1. Scan.\n",
        category="browser",
        tags=["web", "summary"],
        status="active",
    )

    results = store.search("summarize web article")
    assert results
    assert results[0].name == record.name
    matched = store.find(record.name)
    assert matched is not None
    assert matched.metadata["stats"]["matches"] == 0

    viewed = store.view(record.name)
    assert viewed["ok"] is True
    viewed_record = store.find(record.name)
    assert viewed_record is not None
    assert viewed_record.metadata["stats"]["views"] == 1


def test_skill_delete_removes_directory_instead_of_archiving(tmp_path: Path) -> None:
    store = SkillStore(skills_dir=tmp_path / "skills", project_root=tmp_path)
    record = store.create(
        name="temporary review skill",
        description="A disposable skill.",
        body="# Temporary Review Skill\n\n## Steps\n1. Delete this.\n",
        category="general",
        status="draft",
    )
    skill_path = record.path

    result = store.delete(record.name)

    assert result["ok"] is True
    assert result["path"] == str(skill_path.resolve())
    assert not skill_path.exists()
    assert store.find(record.name, include_drafts=True, include_archived=True) is None
    assert not (tmp_path / "skills" / ".archive" / skill_path.name).exists()


def test_skill_lifecycle_stale_archive_restore_and_pin(tmp_path: Path) -> None:
    store = SkillStore(skills_dir=tmp_path / "skills", project_root=tmp_path)
    old = (datetime.now() - timedelta(days=120)).isoformat(timespec="seconds")
    pinned_old = (datetime.now() - timedelta(days=120)).isoformat(timespec="seconds")
    store.create(
        name="old active skill",
        description="Old reusable workflow.",
        body="# Old Active Skill\n\n## When To Use\n- Old tasks.\n\n## Steps\n1. Work.\n",
        category="general",
        status="active",
        metadata={"created_at": old, "updated_at": old},
    )
    pinned = store.create(
        name="pinned old skill",
        description="Pinned reusable workflow.",
        body="# Pinned Old Skill\n\n## When To Use\n- Pinned tasks.\n\n## Steps\n1. Work.\n",
        category="general",
        status="active",
        metadata={"created_at": pinned_old, "updated_at": pinned_old, "pinned": True},
    )

    result = store.curate_lifecycle(stale_after_days=30, archive_after_days=90)
    assert result["ok"] is True
    assert any(change["action"] == "archive" for change in result["changes"])
    assert store.find("old active skill") is None
    archived = store.find("old active skill", include_archived=True)
    assert archived is not None
    assert archived.status == "archived"

    pinned_record = store.find(pinned.name)
    assert pinned_record is not None
    assert pinned_record.status == "active"

    restored = store.restore("old-active-skill")
    assert restored["ok"] is True
    assert store.find("old active skill") is not None
    l1_text = (tmp_path / "memory" / "global_mem_insight.txt").read_text(encoding="utf-8")
    assert "skills/old-active-skill/SKILL.md" in l1_text.splitlines()
