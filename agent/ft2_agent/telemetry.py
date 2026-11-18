# OpenTelemetry setup and the GenAI-convention span helpers the agent loop uses.
#
# Span names and gen_ai.* attributes follow the OpenTelemetry GenAI semantic conventions, which
# moved to github.com/open-telemetry/semantic-conventions-genai in semconv v1.42.0 and are still
# in Development status. Message and tool content is recorded as structured values in the schema
# those conventions define. Anything the conventions don't cover uses an ft2.* name.
import os
from contextlib import contextmanager

from opentelemetry import propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode

TRACER_NAME = "ft2_agent"


def setup_tracing() -> TracerProvider | None:
    """Install an OTLP-exporting provider, unless one is already set (tests) or no endpoint is configured."""
    if isinstance(trace.get_tracer_provider(), TracerProvider):
        return None
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return None
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    # Resource.create() reads OTEL_SERVICE_NAME and OTEL_RESOURCE_ATTRIBUTES;
    # the exporter reads OTEL_EXPORTER_OTLP_ENDPOINT.
    provider = TracerProvider(resource=Resource.create())
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    return provider


def capture_content() -> bool:
    return os.environ.get("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "false").lower() == "true"


def tracer():
    return trace.get_tracer(TRACER_NAME)


# The output message schema spells this finish reason in the singular; OpenAI-style models say "tool_calls".
FINISH_REASONS = {"tool_calls": "tool_call"}


def _parts(message: dict) -> list[dict]:
    if message["role"] == "tool":
        return [{"type": "tool_call_response", "id": message["tool_call_id"], "response": message["content"]}]
    parts = [{"type": "text", "content": message["content"]}] if message.get("content") else []
    for call in message.get("tool_calls") or []:
        parts.append({"type": "tool_call", "id": call.id, "name": call.name, "arguments": call.arguments})
    return parts


def _messages(messages: list[dict]) -> list[dict]:
    return [{"role": message["role"], "parts": _parts(message)} for message in messages]


@contextmanager
def _span(name: str, kind: SpanKind, attributes: dict, context=None):
    with tracer().start_as_current_span(name, context=context, kind=kind, attributes=attributes) as span:
        try:
            yield span
        except Exception as error:
            # The SDK already sets ERROR status and records the exception; the conventions also require error.type.
            span.set_attribute("error.type", type(error).__name__)
            raise


@contextmanager
def agent_span(agent_name: str, description: str, task_id: str, carrier: dict | None = None):
    # carrier holds W3C trace headers (e.g. from a Kafka message); None means "use the current context".
    context = propagate.extract(carrier) if carrier else None
    attributes = {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": agent_name,
        "gen_ai.agent.description": description,
        "gen_ai.conversation.id": task_id,
        "ft2.task.id": task_id,
    }
    with _span(f"invoke_agent {agent_name}", SpanKind.INTERNAL, attributes, context) as span:
        yield span


@contextmanager
def chat_span(provider: str, model: str, step: int, messages: list[dict]):
    attributes = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": provider,
        "gen_ai.request.model": model,
        "ft2.step": step,
    }
    with _span(f"chat {model}", SpanKind.CLIENT, attributes) as span:
        if capture_content():
            span.set_attribute("gen_ai.input.messages", _messages(messages))
        yield span


def record_turn(span, turn):
    span.set_attribute("gen_ai.response.id", turn.response_id)
    span.set_attribute("gen_ai.response.model", turn.response_model)
    span.set_attribute("gen_ai.response.finish_reasons", [turn.finish_reason])
    span.set_attribute("gen_ai.usage.input_tokens", turn.input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", turn.output_tokens)
    if capture_content():
        reply = {"role": "assistant", "content": turn.text, "tool_calls": turn.tool_calls}
        finish_reason = FINISH_REASONS.get(turn.finish_reason, turn.finish_reason)
        span.set_attribute(
            "gen_ai.output.messages",
            [{"role": "assistant", "parts": _parts(reply), "finish_reason": finish_reason}],
        )


@contextmanager
def tool_span(name: str, call_id: str, arguments: dict):
    attributes = {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": name,
        "gen_ai.tool.call.id": call_id,
        "gen_ai.tool.type": "function",
    }
    with _span(f"execute_tool {name}", SpanKind.INTERNAL, attributes) as span:
        if capture_content():
            span.set_attribute("gen_ai.tool.call.arguments", arguments)
        yield span


def record_tool_result(span, name: str, result):
    if capture_content():
        span.set_attribute("gen_ai.tool.call.result", result)
    # Facts the broken-run check needs at checkpoint 3: which documents and versions search returned.
    if name == "search_policies" and isinstance(result, list):
        span.set_attribute("ft2.policy.doc_ids", [doc["doc_id"] for doc in result])
        span.set_attribute("ft2.policy.versions", [doc["version"] for doc in result])
        span.set_attribute("ft2.policy.effective_dates", [doc["effective_date"] for doc in result])
    if name == "lookup_order" and isinstance(result, dict) and "order_id" in result:
        span.set_attribute("ft2.order.id", result["order_id"])


def record_error(span, error: Exception):
    span.set_status(Status(StatusCode.ERROR, str(error)))
    span.set_attribute("error.type", type(error).__name__)


def trace_id_hex(span) -> str:
    return format(span.get_span_context().trace_id, "032x")
