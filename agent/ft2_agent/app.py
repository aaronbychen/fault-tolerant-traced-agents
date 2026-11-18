# Support agent HTTP service. Contract: deploy/agent/agent.yaml (port 8000, GET /healthz).
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel, ConfigDict, Field

from .agent import StepLimitError, run_task
from .llm import ModelError
from .telemetry import setup_tracing

provider = setup_tracing()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Flush buffered spans so the last tasks before a pod stop still reach Tempo.
    if provider is not None:
        provider.shutdown()


app = FastAPI(title="FT2 Support Agent", lifespan=lifespan)
# Skip probe traffic and the per-message ASGI receive/send spans, which only add noise to agent traces.
FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz", exclude_spans=["receive", "send"])


# Web request payload
class TaskRequest(BaseModel):
    # Reject misspelled or unexpected input instead of silently ignoring it.
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    task_id: str = Field(default_factory=lambda: f"task-{uuid4().hex[:12]}", max_length=128)


@app.post("/tasks")
def submit_task(request: TaskRequest):
    try:
        result = run_task(request.task_id, request.question)
    except ModelError as exc:
        raise HTTPException(status_code=502, detail=f"Model call failed: {exc}") from exc
    except StepLimitError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc

    return {
        "task_id": result.task_id,
        "answer": result.answer,
        "trace_id": result.trace_id,
        "tools_used": result.tools_used,
        "steps": result.steps,
    }


# Readiness probe target
@app.get("/healthz")
def healthz():
    return {"status": "ok"}
