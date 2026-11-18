# Regression guard for the trace shape in design doc §3.5.
from opentelemetry import propagate, trace
from opentelemetry.trace import StatusCode

from ft2_agent.agent import run_task
from ft2_agent.llm import StubModel


def _run(spans, question="Can I return order 1042 after 20 days?", carrier=None):
    result = run_task("t-span", question, carrier=carrier, model=StubModel(0, 0))
    finished = spans.get_finished_spans()
    return result, finished, {span.name: span for span in finished}


def test_one_trace_with_expected_span_tree(spans):
    result, finished, _ = _run(spans)
    names = [span.name for span in sorted(finished, key=lambda s: s.start_time)]
    assert names == [
        "invoke_agent support-agent",
        "chat stub-model",
        "execute_tool search_policies",
        "execute_tool lookup_order",
        "chat stub-model",
    ]
    assert {format(span.context.trace_id, "032x") for span in finished} == {result.trace_id}

    root = next(span for span in finished if span.parent is None)
    assert all(span.parent.span_id == root.context.span_id for span in finished if span is not root)


def test_genai_attributes(spans):
    _, finished, by_name = _run(spans)
    root = by_name["invoke_agent support-agent"]
    assert root.attributes["gen_ai.operation.name"] == "invoke_agent"
    assert root.attributes["ft2.task.id"] == "t-span"

    chats = [span for span in finished if span.name.startswith("chat ")]
    assert [span.attributes["gen_ai.response.finish_reasons"] for span in chats] == [("tool_calls",), ("stop",)]
    for span in chats:
        assert span.kind == trace.SpanKind.CLIENT
        assert span.attributes["gen_ai.provider.name"] == "ft2.stub"
        assert span.attributes["gen_ai.request.model"] == "stub-model"
        assert span.attributes["gen_ai.usage.input_tokens"] > 0
        assert span.attributes["gen_ai.usage.output_tokens"] > 0

    search = by_name["execute_tool search_policies"]
    assert search.attributes["gen_ai.tool.name"] == "search_policies"
    assert search.attributes["gen_ai.tool.call.id"].startswith("call_")
    assert search.attributes["ft2.policy.versions"][0] == "2026.1"
    assert by_name["execute_tool lookup_order"].attributes["ft2.order.id"] == "1042"


def test_message_content_is_opt_in(spans, monkeypatch):
    monkeypatch.delenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", raising=False)
    _, _, by_name = _run(spans)
    assert "gen_ai.input.messages" not in by_name["chat stub-model"].attributes

    spans.clear()
    monkeypatch.setenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "true")
    _, _, by_name = _run(spans)
    assert "1042" in by_name["chat stub-model"].attributes["gen_ai.input.messages"]
    assert "gen_ai.tool.call.result" in by_name["execute_tool lookup_order"].attributes


def test_failed_tool_span_is_red(spans):
    _, _, by_name = _run(spans, question="Can I return order 9999 after 3 days?")
    lookup = by_name["execute_tool lookup_order"]
    assert lookup.status.status_code == StatusCode.ERROR
    assert lookup.attributes["error.type"] == "ToolError"


def test_carrier_continues_an_upstream_trace(spans):
    # What the Kafka consumer will do at checkpoint 2: pass message headers as the carrier.
    tracer = trace.get_tracer("test-producer")
    with tracer.start_as_current_span("produce task") as producer:
        carrier = {}
        propagate.inject(carrier)
    upstream_trace_id = format(producer.get_span_context().trace_id, "032x")

    spans.clear()
    result, finished, by_name = _run(spans, carrier=carrier)
    assert result.trace_id == upstream_trace_id
    assert by_name["invoke_agent support-agent"].parent.span_id == producer.get_span_context().span_id
