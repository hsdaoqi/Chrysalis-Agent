from chrysalis.working import WorkingMemory


def test_plan_snapshot_exposes_nested_and_alias_fields() -> None:
    working = WorkingMemory()
    working.update_plan(
        goal="Ship the Electron plan view",
        steps=[
            {"id": "step-1", "title": "Wire plan snapshot", "status": "completed", "evidence": "state_snapshot"},
            {"id": "step-2", "title": "Render acceptance criteria", "status": "completed", "evidence": "UI rows"},
        ],
        acceptance_criteria=[
            {"id": "ac-1", "title": "Plan is visible in desktop-electron", "status": "satisfied", "evidence": "screenshot"},
            {"id": "ac-2", "title": "Evidence is visible", "status": "satisfied", "evidence": "plan evidence list"},
        ],
        evidence=["plan updated", "snapshot verified"],
        status="completed",
        summary="Plan UI is complete",
        action="set",
    )

    snapshot = working.state_snapshot()

    assert snapshot["plan"]["goal"] == "Ship the Electron plan view"
    assert snapshot["plan_goal"] == "Ship the Electron plan view"
    assert snapshot["plan_status"] == "completed"
    assert snapshot["plan_pending_steps"] == 0
    assert snapshot["plan_pending_acceptance_criteria"] == 0
    assert snapshot["plan"]["active_step_id"] == ""
    assert snapshot["plan_evidence"] == ["plan updated", "snapshot verified"]
    assert snapshot["plan"]["steps"][0]["title"] == "Wire plan snapshot"


def test_plan_reminder_triggers_after_interval() -> None:
    working = WorkingMemory()
    working.update_plan(
        goal="Finish the plan",
        steps=[{"id": "step-1", "title": "Write the UI", "status": "pending"}],
        acceptance_criteria=[{"id": "ac-1", "title": "Show plan rows", "status": "pending"}],
        action="set",
    )

    for _ in range(working.plan_reminder_interval):
        working.tick_round()

    reminder = working.plan_reminder_prompt()

    assert "Plan Reminder" in reminder
    assert "Write the UI" in reminder
    assert "Show plan rows" in reminder
    assert working.rounds_since_plan == 0
