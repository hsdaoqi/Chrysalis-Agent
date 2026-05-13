from chrysalis.observation import compact_observation


def test_compact_observation_keeps_status_and_compresses_text():
    text = "a" * 800 + "MIDDLE" + "z" * 800

    result = compact_observation({"ok": True, "path": "x.txt", "content": text})

    assert result["ok"] is True
    assert result["path"] == "x.txt"
    assert len(result["content"]) < len(text)
    assert result["content"].startswith("a" * 600)
    assert result["content"].endswith("z" * 600)
    assert "MIDDLE" not in result["content"]


def test_compact_observation_keeps_head_and_tail_for_lists():
    entries = [{"name": f"file_{index}.txt"} for index in range(20)]

    result = compact_observation({"entries": entries})

    names = [item.get("name") for item in result["entries"] if "name" in item]
    assert "file_0.txt" in names
    assert "file_19.txt" in names
    assert {"omitted_items": 8} in result["entries"]
