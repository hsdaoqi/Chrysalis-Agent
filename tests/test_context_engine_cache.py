from pathlib import Path

from chrysalis.context_engine import (
    RUNTIME_CONTEXT_HEADER,
    STABLE_CONTEXT_HEADER,
    ContextBudget,
    ContextEngine,
)
from chrysalis.skills.store import SkillStore
from chrysalis.working import WorkingMemory


def test_context_engine_places_stable_memory_before_runtime_state(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    skills_dir = tmp_path / "skills"
    memory_dir.mkdir()
    skills_dir.mkdir()
    (memory_dir / "global_mem_insight.txt").write_text("stable insight index", encoding="utf-8")
    (memory_dir / "global_mem.txt").write_text("stable project memory", encoding="utf-8")

    working = WorkingMemory()
    working.update_checkpoint(key_info="volatile current task state")
    engine = ContextEngine(
        project_root=tmp_path,
        memory_dir=memory_dir,
        skills_dir=skills_dir,
        budget=ContextBudget(total_chars=10_000),
    )

    assembled = engine.assemble(
        base_system="base system",
        task="general task",
        working=working,
        session_context="volatile continuation",
    )

    system = assembled.system
    assert system.index(STABLE_CONTEXT_HEADER) < system.index(RUNTIME_CONTEXT_HEADER)
    assert system.index("[Memory L1 Insight]") < system.index("[Global L2 Memory]")
    assert system.index("[Global L2 Memory]") < system.index(RUNTIME_CONTEXT_HEADER)
    assert system.index("volatile current task state") < system.index("[Runtime Continuation]")
    assert assembled.included == ["system", "l1", "global_memory", "working_memory", "session_context"]


def test_context_engine_keeps_stable_prefix_when_runtime_context_changes(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    skills_dir = tmp_path / "skills"
    memory_dir.mkdir()
    skills_dir.mkdir()
    (memory_dir / "global_mem_insight.txt").write_text("stable insight index", encoding="utf-8")
    (memory_dir / "global_mem.txt").write_text("stable project memory", encoding="utf-8")
    (memory_dir / "git_sop.md").write_text("git-specific instructions", encoding="utf-8")
    (memory_dir / "web_setup_sop.md").write_text("web-specific instructions", encoding="utf-8")

    engine = ContextEngine(
        project_root=tmp_path,
        memory_dir=memory_dir,
        skills_dir=skills_dir,
        budget=ContextBudget(total_chars=10_000),
    )

    git_context = engine.assemble(base_system="base system", task="git commit workflow")
    web_context = engine.assemble(base_system="base system", task="browser web workflow")
    marker = "\n\n" + RUNTIME_CONTEXT_HEADER + "\n"

    assert git_context.system.split(marker, 1)[0] == web_context.system.split(marker, 1)[0]
    assert "git-specific instructions" in git_context.system
    assert "web-specific instructions" in web_context.system


def test_context_engine_reports_budget_sections_and_hit_reasons(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    skills_dir = tmp_path / "skills"
    memory_dir.mkdir()
    (memory_dir / "global_mem_insight.txt").write_text("stable insight index", encoding="utf-8")
    (memory_dir / "global_mem.txt").write_text("stable project memory", encoding="utf-8")
    (memory_dir / "git_sop.md").write_text("git workflow instructions", encoding="utf-8")
    SkillStore(skills_dir=skills_dir, project_root=tmp_path).create(
        name="git commit helper",
        description="Reusable git commit workflow.",
        body="# Git Commit Helper\n\n## Steps\n1. Inspect status.\n2. Commit changes.\n",
        category="git",
        tags=["git", "commit"],
        status="active",
    )
    l1_text = (memory_dir / "global_mem_insight.txt").read_text(encoding="utf-8")
    assert "skills/git-commit-helper/SKILL.md" in l1_text

    working = WorkingMemory()
    working.update_checkpoint(key_info="prepare commit", related_sop="git_sop.md")
    engine = ContextEngine(
        project_root=tmp_path,
        memory_dir=memory_dir,
        skills_dir=skills_dir,
        budget=ContextBudget(total_chars=10_000),
    )

    assembled = engine.assemble(
        base_system="base system",
        task="git commit workflow",
        working=working,
    )

    budget = assembled.budget
    sections = {section["name"]: section for section in budget["sections"]}
    assert budget["section_count"] == len(budget["sections"])
    assert sections["l1"]["used_chars"] > 0
    assert sections["l1"]["stable"] is True
    assert "skills/git-commit-helper/SKILL.md" in assembled.system
    assert sections["related_memory"]["items"][0]["source"] == "memory/git_sop.md"
    assert "git" in sections["related_memory"]["items"][0]["matched"]
    assert "related_skills" not in sections
    assert "[Relevant Skills]" not in assembled.system
    assert "key_info" in sections["working_memory"]["reason"]


def test_context_engine_reports_global_memory_recall_items(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    skills_dir = tmp_path / "skills"
    memory_dir.mkdir()
    skills_dir.mkdir()
    (memory_dir / "global_mem.txt").write_text(
        "\n".join([
            "## Release workspace",
            "- target: fact",
            "- approved_at: 2026-06-05T10:00:00",
            "",
            "Use D:/Project/Chrysalis for release checks.",
            "",
            "## Unrelated note",
            "- target: fact",
            "",
            "This block should not match the current task.",
        ]),
        encoding="utf-8",
    )

    engine = ContextEngine(
        project_root=tmp_path,
        memory_dir=memory_dir,
        skills_dir=skills_dir,
        budget=ContextBudget(total_chars=10_000),
    )

    assembled = engine.assemble(base_system="base system", task="check the release workspace")
    sections = {section["name"]: section for section in assembled.budget["sections"]}

    global_items = sections["global_memory"]["items"]
    assert global_items
    assert global_items[0]["name"] == "Release workspace"
    assert global_items[0]["source"] == "memory/global_mem.txt"
    assert "release" in global_items[0]["matched"]
