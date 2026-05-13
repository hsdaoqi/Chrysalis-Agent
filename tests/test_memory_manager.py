import json

import pytest

from chrysalis.memory_manager import MemoryManager


def test_memory_manager_creates_l0_to_l4_structure(tmp_path):
    manager = MemoryManager(tmp_path / "memory", tmp_path / "data" / "memory.json")

    assert manager.paths.l0_meta_sop.exists()
    assert manager.paths.l1_insight.exists()
    assert manager.paths.l2_global.exists()
    assert manager.paths.l3_dir.exists()
    assert manager.paths.l4_archive.exists()
    assert manager.paths.machine_memory.exists()


def test_memory_manager_skill_index_dedupes_by_name(tmp_path):
    manager = MemoryManager(tmp_path / "memory", tmp_path / "data" / "memory.json")

    manager.add_skill_index("write_note", "旧描述")
    manager.add_skill_index("write_note", "新描述")

    skills = manager.list_skills()
    assert skills == [{"name": "write_note", "description": "新描述"}]


def test_memory_manager_l2_fact_requires_evidence(tmp_path):
    manager = MemoryManager(tmp_path / "memory", tmp_path / "data" / "memory.json")

    with pytest.raises(ValueError, match="工具验证依据"):
        manager.add_global_fact("PATH", "桌面路径是 D:\\桌面", "")


def test_memory_manager_adds_verified_l2_fact(tmp_path):
    manager = MemoryManager(tmp_path / "memory", tmp_path / "data" / "memory.json")

    manager.add_global_fact("PATH", "桌面路径是 D:\\桌面", "shell_run 返回 ok=True")

    text = manager.paths.l2_global.read_text(encoding="utf-8")
    assert "## PATH" in text
    assert "桌面路径是 D:\\桌面" in text


def test_memory_manager_rejects_secrets(tmp_path):
    manager = MemoryManager(tmp_path / "memory", tmp_path / "data" / "memory.json")

    with pytest.raises(ValueError, match="疑似密钥"):
        manager.add_skill_index("secret_skill", "api_key=abc")


def test_memory_manager_archives_l4_on_episode(tmp_path):
    manager = MemoryManager(tmp_path / "memory", tmp_path / "data" / "memory.json")

    manager.remember_episode("写讲稿", "已完成")

    data = json.loads(manager.paths.machine_memory.read_text(encoding="utf-8"))
    archive_lines = manager.paths.l4_archive.read_text(encoding="utf-8").splitlines()
    assert data["episodes"][0]["task"] == "写讲稿"
    assert len(archive_lines) == 1
    assert json.loads(archive_lines[0])["task"] == "写讲稿"
