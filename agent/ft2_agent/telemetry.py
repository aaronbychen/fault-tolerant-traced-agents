# OpenTelemetry setup and the GenAI-convention span helpers the agent loop uses.
#
# Span names and gen_ai.* attributes follow the OpenTelemetry GenAI semantic conventions
# (Development status; written against semconv v1.37+, which uses gen_ai.provider.name
# and gen_ai.input/output.messages). Anything the conventions don't cover uses an ft2.* name.
import json
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


def _json(value) -> str:
    return json.dumps(value, default=lambda o: vars(o) if hasattr(o, "__dict__") else str(o))


@contextmanager
def agent_span(agent_name: str, task_id: str, carrier: dict | None = None):
    # carrier holds W3C trace headers (e.g. from a Kafka message); None means "use the current context".
    context = propagate.extract(carrier) if carrier else None
    with tracer().start_as_current_span(
        f"invoke_agent {agent_name}",
        context=context,
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": agent_name,
            "gen_ai.conversation.id": task_id,
            "ft2.task.id": task_id,
        },
    ) as span:
        yield span


@contextmanager
def chat_span(provider: str, model: str, step: int, messages: list[dict]):
    with tracer().start_as_current_span(
        f"chat {model}",
        kind=SpanKind.CLIENT,
        attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": provider,
            "gen_ai.request.model": model,
            "ft2.step": step,
        },
    ) as span:
        if capture_content():
            span.set_attribute("gen_ai.input.messages", _json(messages))
        yield span


def record_turn(span, turn):
    span.set_attribute("gen_ai.response.id", turn.response_id)
    span.set_attribute("gen_ai.response.model", turn.response_model)
    span.set_attribute("gen_ai.response.finish_reasons", [turn.finish_reason])
    span.set_attribute("gen_ai.usage.input_tokens", turn.input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", turn.output_tokens)
    if capture_content():
        output = {"role": "assistant", "content": turn.text, "tool_calls": turn.tool_calls}
        span.set_attribute("gen_ai.output.messages", _json([output]))


@contextmanager
def tool_span(name: str, call_id: str, arguments: dict):
    with tracer().start_as_current_span(
        f"execute_tool {name}",
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": name,
            "gen_ai.tool.call.id": call_id,
            "gen_ai.tool.type": "function",
        },
    ) as span:
        if capture_content():
            span.set_attribute("gen_ai.tool.call.arguments", _json(arguments))
        yield span


def record_tool_result(span, name: str, result):
    if capture_content():
        span.set_attribute("gen_ai.tool.call.result", _json(result))
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
