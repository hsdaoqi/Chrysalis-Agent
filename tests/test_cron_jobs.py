import json
from pathlib import Path

import pytest

from chrysalis.cron.jobs import CronError, create_job, list_jobs, load_job, save_job, update_job
from configs.config import AgentConfig


def _config(tmp_path: Path) -> AgentConfig:
    return AgentConfig(
        root=tmp_path,
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        skills_dir=tmp_path / "skills",
        workspace_dir=tmp_path / "workspace",
    )


def test_jobs_expose_config_path_without_persisting_it(tmp_path: Path) -> None:
    config = _config(tmp_path)
    created = create_job(
        config,
        job_id="path-test",
        schedule={"type": "once", "run_at": "2026-06-06T10:00"},
        prompt="Run once.",
    )

    path = config.data_dir / "cron" / "jobs" / "path-test.json"
    assert created["path"] == str(path)
    assert path.exists()
    assert "path" not in json.loads(path.read_text(encoding="utf-8"))

    loaded = load_job(config, "path-test")
    assert loaded is not None
    assert loaded["path"] == str(path)
    loaded["path"] = "should-not-persist"
    save_job(config, loaded)
    assert "path" not in json.loads(path.read_text(encoding="utf-8"))

    listed = list_jobs(config, include_disabled=True)
    listed_job = next(job for job in listed if job["id"] == "path-test")
    assert listed_job["path"] == str(path)


def test_update_job_preserves_runtime_history(tmp_path: Path) -> None:
    config = _config(tmp_path)
    created = create_job(
        config,
        job_id="daily-summary",
        name="Daily summary",
        schedule={
            "type": "periodic",
            "period": "daily",
            "time": "08:00",
            "start_at": "2026-01-01T00:00",
        },
        prompt="Write a summary.",
        repeat_times=5,
    )
    created["state"]["last_run_at"] = "2026-06-01T08:00"
    created["state"]["last_status"] = "ok"
    created["state"]["last_output"] = "data/cron/output/daily-summary/out.md"
    created["repeat"]["completed"] = 2
    save_job(config, created)

    updated = update_job(
        config,
        "daily-summary",
        name="Morning summary",
        schedule={
            "type": "periodic",
            "period": "weekly",
            "weekday": 1,
            "time": "09:30",
            "start_at": "2026-01-01T00:00",
        },
        prompt="Write a tighter summary.",
        repeat_times=7,
        max_delay_minutes=30,
    )

    assert updated["id"] == "daily-summary"
    assert updated["name"] == "Morning summary"
    assert updated["schedule"]["period"] == "weekly"
    assert updated["schedule_display"] == "weekly weekday=1 at 09:30"
    assert updated["prompt"] == "Write a tighter summary."
    assert updated["repeat"] == {"times": 7, "completed": 2}
    assert updated["max_delay_minutes"] == 30
    assert updated["state"]["last_run_at"] == "2026-06-01T08:00"
    assert updated["state"]["last_status"] == "ok"
    assert updated["state"]["last_output"] == "data/cron/output/daily-summary/out.md"
    assert updated["state"]["next_run_at"] is not None
    assert load_job(config, "daily-summary")["updated_at"]


def test_update_job_rejects_running_job(tmp_path: Path) -> None:
    config = _config(tmp_path)
    job = create_job(
        config,
        job_id="running-job",
        schedule={"type": "once", "run_at": "2026-06-06T10:00"},
        prompt="Run once.",
    )
    job["state"]["running"] = True
    save_job(config, job)

    with pytest.raises(CronError, match="running job cannot be edited"):
        update_job(
            config,
            "running-job",
            schedule={"type": "once", "run_at": "2026-06-07T10:00"},
            prompt="Run later.",
        )
