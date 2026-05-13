from chrysalis.memory import Memory
from chrysalis.skills import SkillLibrary


def test_skill_library_search_and_execute(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    memory = Memory(tmp_path / "memory", tmp_path / "data" / "memory.json")
    (skills_dir / "write_note.py").write_text(
        """
def execute(task: str) -> dict:
    return {"ok": True, "message": "技能已执行", "task": task}
""",
        encoding="utf-8",
    )
    memory.add_skill("write_note", "来源任务：写入 note 文件")

    library = SkillLibrary(skills_dir, memory)

    result = library.execute("write_note", "请写入 note 文件")
    assert result["ok"] is True
    assert result["message"] == "技能已执行"
