# Model interface for the agent loop, plus the deterministic stub used for tests and load runs.
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Protocol
from uuid import uuid4

from .tools import tokenize

# Messages use one neutral format so the agent loop doesn't depend on a provider:
#   {"role": "user", "content": str}
#   {"role": "assistant", "content": str | None, "tool_calls": [ToolCall, ...]}
#   {"role": "tool", "tool_call_id": str, "name": str, "content": <tool result>}


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ModelTurn:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    input_tokens: int = 0
    output_tokens: int = 0
    response_id: str = ""
    response_model: str = ""


class ModelError(Exception):
    pass


class Model(Protocol):
    provider: str
    name: str

    def step(self, messages: list[dict], tools: list[dict]) -> ModelTurn: ...


def _approx_tokens(value) -> int:
    return max(1, len(json.dumps(value, default=str)) // 4)


class StubModel:
    """Scripted stand-in for an LLM: turn 1 calls the tools, turn 2 answers from their results.

    STUB_LATENCY_MS sleeps per call so Kafka lag can build up under load.
    STUB_CPU_MS busy-loops per call so the HPA-on-CPU comparison has CPU to react to.
    """

    provider = "ft2.stub"
    name = "stub-model"

    def __init__(self, latency_ms: int | None = None, cpu_ms: int | None = None):
        self.latency_ms = int(os.environ.get("STUB_LATENCY_MS", "0")) if latency_ms is None else latency_ms
        self.cpu_ms = int(os.environ.get("STUB_CPU_MS", "0")) if cpu_ms is None else cpu_ms

    def step(self, messages: list[dict], tools: list[dict]) -> ModelTurn:
        self._simulate_work()
        question = next(m["content"] for m in messages if m["role"] == "user")
        tool_results = {m["name"]: m["content"] for m in messages if m["role"] == "tool"}
        available = {tool["name"] for tool in tools}

        if not tool_results:
            calls = []
            if "search_policies" in available:
                calls.append(ToolCall(f"call_{uuid4().hex[:8]}", "search_policies", {"query": question}))
            order_id = _order_id(question)
            if order_id and "lookup_order" in available:
                calls.append(ToolCall(f"call_{uuid4().hex[:8]}", "lookup_order", {"order_id": order_id}))
            if calls:
                return self._turn(messages, None, calls, "tool_calls")

        return self._turn(messages, _compose_answer(question, tool_results), [], "stop")

    def _simulate_work(self):
        if self.cpu_ms:
            end = time.perf_counter() + self.cpu_ms / 1000
            while time.perf_counter() < end:
                pass
        if self.latency_ms:
            time.sleep(self.latency_ms / 1000)

    def _turn(self, messages, text, calls, finish_reason) -> ModelTurn:
        output = text if text is not None else [vars(call) for call in calls]
        return ModelTurn(
            text=text,
            tool_calls=calls,
            finish_reason=finish_reason,
            input_tokens=_approx_tokens(messages),
            output_tokens=_approx_tokens(output),
            response_id=f"stub-{uuid4().hex[:12]}",
            response_model=self.name,
        )


def _order_id(question: str) -> str | None:
    match = re.search(r"\border\s*#?\s*(\d{3,})", question, re.IGNORECASE)
    return match.group(1) if match else None


def _compose_answer(question: str, tool_results: dict) -> str:
    docs = tool_results.get("search_policies")
    order = tool_results.get("lookup_order")
    parts = []

    if isinstance(order, dict) and "error" in order:
        parts.append(f"I couldn't find that order ({order['error']}).")
    elif isinstance(order, dict):
        if order.get("delivered_date"):
            parts.append(f"Order {order['order_id']} was delivered on {order['delivered_date']}.")
        else:
            parts.append(f"Order {order['order_id']} has status {order['status']} and has not been delivered yet.")

    if not docs or not isinstance(docs, list):
        parts.append("I couldn't find a policy that covers this question.")
        return " ".join(parts)

    doc = docs[0]
    window = re.search(r"within (\d+) days", doc["text"])
    asked = re.search(r"(\d+)\s+days", question)
    if "refund" in doc["title"].lower() and window:
        allowed = int(window.group(1))
        source = f"our {doc['title'].lower()} (version {doc['version']}, effective {doc['effective_date']})"
        if asked:
            days = int(asked.group(1))
            verdict = "is eligible" if days <= allowed else "is not eligible"
            prefix = "Yes." if days <= allowed else "No."
            parts.insert(0, prefix)
            parts.append(
                f"Under {source}, items can be returned within {allowed} days of delivery, "
                f"so a return after {days} days {verdict} for a refund."
            )
        else:
            parts.append(f"Under {source}, items can be returned within {allowed} days of delivery.")
    else:
        parts.append(_best_sentence(question, doc["text"]))
    return " ".join(parts)


def _best_sentence(question: str, text: str) -> str:
    terms = set(tokenize(question))
    sentences = [s.strip() for s in re.split(r"(?<=\.)\s+", text) if s.strip()]
    return max(sentences, key=lambda s: len(terms & set(tokenize(s))))


def get_model() -> Model:
    if os.environ.get("STUB_MODEL", "1") != "0":
        return StubModel()
    # The real provider is a team decision (design doc §8); until then only the stub runs.
    raise ModelError("STUB_MODEL=0 but no real model is configured yet")
