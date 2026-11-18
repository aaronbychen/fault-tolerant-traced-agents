from fastapi.testclient import TestClient

from ft2_agent.app import app

client = TestClient(app)


def test_healthz():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_post_task(spans):
    response = client.post("/tasks", json={"task_id": "t-http", "question": "Can I return order 1042 after 20 days?"})
    assert response.status_code == 200
    body = response.json()
    assert body["tools_used"] == ["search_policies", "lookup_order"]
    assert "14 days" in body["answer"]

    # The HTTP server span is the root, and the agent span hangs under it in the same trace.
    finished = spans.get_finished_spans()
    assert {format(span.context.trace_id, "032x") for span in finished} == {body["trace_id"]}
    root = next(span for span in finished if span.parent is None)
    assert root.name == "POST /tasks"


def test_task_id_is_generated_when_missing(spans):
    response = client.post("/tasks", json={"question": "What is the warranty on electronics?"})
    assert response.status_code == 200
    assert response.json()["task_id"].startswith("task-")


def test_rejects_unexpected_fields():
    response = client.post("/tasks", json={"question": "hi", "cmd": ["ls"]})
    assert response.status_code == 422


def test_real_model_not_configured_is_502(monkeypatch):
    monkeypatch.setenv("STUB_MODEL", "0")
    response = client.post("/tasks", json={"question": "Can I return order 1042?"})
    assert response.status_code == 502
