import json

from chrysalis.memory import Memory


def test_memory_episode_uses_head_tail_summary(tmp_path):
    memory = Memory(tmp_path / "memory", tmp_path / "data" / "memory.json")
    result = "a" * 250 + "MIDDLE" + "z" * 250

    memory.remember_episode("长结果任务", result)

    data = json.loads((tmp_path / "data" / "memory.json").read_text(encoding="utf-8"))
    saved = data["episodes"][0]["result"]
    assert saved.startswith("a" * 200)
    assert saved.endswith("z" * 200)
    assert "MIDDLE" not in saved


def test_memory_recovers_empty_data_file(tmp_path):
    data_path = tmp_path / "data" / "memory.json"
    data_path.parent.mkdir()
    data_path.write_text("", encoding="utf-8")

    memory = Memory(tmp_path / "memory", data_path)
    memory.remember_episode("任务", "结果")

    data = json.loads(data_path.read_text(encoding="utf-8"))
    assert data["episodes"][0]["task"] == "任务"
