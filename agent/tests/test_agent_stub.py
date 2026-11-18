import json
from pathlib import Path

import pytest

from ft2_agent.agent import run_task
from ft2_agent.llm import StubModel

TEST_TASKS = json.loads((Path(__file__).parent.parent / "data" / "test_tasks.json").read_text())


@pytest.mark.parametrize("task", TEST_TASKS, ids=[t["task_id"] for t in TEST_TASKS])
def test_stub_agent_answers_test_tasks(task, spans):
    result = run_task(task["task_id"], task["question"], model=StubModel(0, 0))
    assert result.tools_used == task["expected"]["tools"]
    for phrase in task["expected"]["answer_contains"]:
        assert phrase in result.answer


def test_refund_answer_for_1042(spans):
    result = run_task("t-1", "Can I return order 1042 after 20 days?", model=StubModel(0, 0))
    assert result.answer.startswith("No.")
    assert "within 14 days" in result.answer
    assert "2026.1" in result.answer


def test_unknown_order_is_reported_not_raised(spans):
    result = run_task("t-2", "Can I return order 9999 after 3 days?", model=StubModel(0, 0))
    assert "couldn't find that order" in result.answer
    assert {"type": "tool", "name": "lookup_order", "arguments": {"order_id": "9999"}, "ok": False} in result.steps
