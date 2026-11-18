# The agent loop. run_task() is the single entry point: the HTTP API calls it now,
# and the Kafka worker calls it at checkpoint 2, passing the message headers as carrier.
from dataclasses import dataclass, field

from . import telemetry
from .llm import Model, get_model
from .tools import ToolError, run_tool, tool_schemas

AGENT_NAME = "support-agent"
MAX_STEPS = 6

SYSTEM_PROMPT = (
    "You are a customer support agent for an online store. Use search_policies to find the "
    "current policy and lookup_order for order details before answering. Answer in two or "
    "three sentences and name the policy version you relied on."
)


class StepLimitError(Exception):
    pass


@dataclass
class TaskResult:
    task_id: str
    answer: str
    trace_id: str
    steps: list[dict] = field(default_factory=list)

    @property
    def tools_used(self) -> list[str]:
        return [step["name"] for step in self.steps if step["type"] == "tool"]


def run_task(task_id: str, question: str, carrier: dict | None = None, model: Model | None = None) -> TaskResult:
    model = model or get_model()
    tools = tool_schemas()
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": question}]
    steps = []

    with telemetry.agent_span(AGENT_NAME, task_id, carrier) as root:
        for step in range(1, MAX_STEPS + 1):
            with telemetry.chat_span(model.provider, model.name, step, messages) as span:
                turn = model.step(messages, tools)
                telemetry.record_turn(span, turn)
            steps.append({"type": "model", "name": model.name, "finish_reason": turn.finish_reason})

            if not turn.tool_calls:
                return TaskResult(task_id, turn.text or "", telemetry.trace_id_hex(root), steps)

            messages.append({"role": "assistant", "content": turn.text, "tool_calls": turn.tool_calls})
            for call in turn.tool_calls:
                messages.append(_execute(call, steps))

        error = StepLimitError(f"no answer after {MAX_STEPS} model calls")
        telemetry.record_error(root, error)
        raise error


def _execute(call, steps: list[dict]) -> dict:
    with telemetry.tool_span(call.name, call.id, call.arguments) as span:
        try:
            result = run_tool(call.name, call.arguments)
            telemetry.record_tool_result(span, call.name, result)
            ok = True
        except ToolError as error:
            # Report the failure to the model instead of failing the task, and mark the span red.
            telemetry.record_error(span, error)
            result = {"error": str(error)}
            ok = False
    steps.append({"type": "tool", "name": call.name, "arguments": call.arguments, "ok": ok})
    return {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": result}
