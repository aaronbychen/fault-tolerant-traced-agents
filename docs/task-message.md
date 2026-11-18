# Task message format

Owner: Tim. Every agent task uses this format, whether it arrives over HTTP (checkpoint 1) or Kafka (checkpoint 2). The burst Job, the Kafka producer and worker, the test tasks and the check all read or write it.

## Body

One JSON object, UTF-8:

```json
{
  "task_id": "refund-1042",
  "question": "Can I return order 1042 after 20 days?",
  "order_id": "1042",
  "expected": {
    "tools": ["search_policies", "lookup_order"],
    "answer_contains": ["14 days", "not eligible"],
    "facts": {"refund_window_days": 14, "delivered_date": "2026-10-24"}
  }
}
```

| Field | Type | Required | Meaning |
|---|---|---|---|
| `task_id` | string, up to 128 chars | yes | Unique per task. Recorded as `ft2.task.id` and `gen_ai.conversation.id` on the agent span, and used as the Kafka message key. A retried or redelivered task keeps its ID. |
| `question` | string, 1 to 2000 chars | yes | The customer's question, sent to the agent as the user message. |
| `order_id` | string or null | no | The order the question is about, when the sender knows it. The agent still reads the order ID from the question text, so this field is informational until the Kafka worker passes it through. |
| `expected` | object | test tasks only | The known answer. Real traffic leaves it out. |

`expected` holds:

| Field | Type | Meaning |
|---|---|---|
| `tools` | string[] | Tools the agent should call, in order. Unit tests compare it with `tools_used`. |
| `answer_contains` | string[] | Phrases the final answer must contain. |
| `facts` | object | Fact name to correct value. The check looks for each fact in every step's output and marks the first step whose value differs. |
| `faulty_step` | string | Seeded failures only. The step the check should mark: a tool name (`search_policies`, `lookup_order`) or `model` for a model call. This is the ground truth for the diagnosis experiment. |

Facts in use:

| Fact | Correct value comes from | Example |
|---|---|---|
| `refund_window_days` | the current refund policy | `14` |
| `delivered_date` | the order | `"2026-10-24"` |
| `express_shipping` | the shipping policy | `"1 to 2 business days"` |

`agent/data/test_tasks.json` holds the test tasks in this format.

## On Kafka

- Topic `agent-tasks`, with at least 4 partitions, since KEDA won't scale workers past the partition count.
- Key: `task_id`. Value: the JSON body.
- Headers: the W3C trace context the producer writes with the OpenTelemetry propagator, `traceparent` and `tracestate` when set, as UTF-8 strings. The worker passes them to `run_task(..., carrier=...)`, so the task's spans join the producer's trace.
- The worker commits the offset only after the task finishes, so delivery is at least once. If a worker dies mid-task, another pod runs it again with the same `task_id` and `traceparent`, and the retry appears in the same trace. Tools are read-only, so running a task twice is safe.
- Replaying a task (for example after fixing a document) publishes a new message with the same body and a new trace context, plus a span link to the original trace. The fixed run and the broken run then show up as two separate traces.

## Over HTTP

`POST /tasks` takes `question` and an optional `task_id`, and generates the ID when it's missing. It rejects other fields for now.

## Open question (Tim and Richard, by Dec 2)

How a seeded failure is triggered: per worker (for example `POLICY_DIR` pointing at `policies_archive/`) or per task (a field in this message). Per task lets one burst mix seeded failures with normal tasks for the diagnosis experiment.
