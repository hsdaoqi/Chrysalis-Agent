from pathlib import Path

from chrysalis.config import PROJECT_ROOT, AgentConfig, project_path


def test_project_path_is_root_relative():
    assert project_path("data") == (PROJECT_ROOT / "data").resolve()


def test_default_dirs_are_project_root_relative():
    cfg = AgentConfig()
    assert cfg.data_dir == PROJECT_ROOT / "data"
    assert cfg.memory_dir == PROJECT_ROOT / "memory"
    assert cfg.skills_dir == PROJECT_ROOT / "skills"
    assert cfg.workspace_dir == PROJECT_ROOT / "workspace"


def test_absolute_path_is_preserved(tmp_path):
    cfg = AgentConfig(data_dir=tmp_path / "data")
    assert cfg.data_dir == (tmp_path / "data").resolve()
