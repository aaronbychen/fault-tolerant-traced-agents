# Checkpoint 1 design: traced support agent on minikube

Target date: **Nov 18**. Owner of the agent code: Richard. Tracing conventions reviewed by Tim; deployment contract set by Aaron.

## 1. What checkpoint 1 must show

From the proposal:

1. The agent answers a customer question using **both tools** (`search_policies`, `lookup_order`).
2. **One task is traced end to end in Tempo**: every model call and tool call is a span in a single trace.
3. **Stub-model mode** so test runs are free and deterministic.

Implicit in "checkpoint 1 is a complete demo on its own" (risk slide): it runs on minikube from the repo with a few commands, and we report measured cluster memory.

## 2. What already exists (merged in PR #1)

| Piece | File | Status |
|---|---|---|
| Cluster start (4 CPU / 6 GB, metrics-server) | `scripts/cluster-up.sh` | Done |
| OTel Collector, OTLP gRPC 4317 / HTTP 4318 → Tempo | `deploy/observability/otel-collector.yaml` | Done |
| Tempo single binary, local `emptyDir` storage | `deploy/observability/tempo.yaml` | Done; traces are lost when the pod restarts |
| Grafana with Tempo datasource, anonymous admin | `deploy/observability/grafana.yaml` | Done; no Prometheus yet (checkpoint 3) |
| Pipeline smoke test with telemetrygen | `scripts/smoke-trace.sh` | Done |
| Agent Deployment + Service in namespace `ft2` | `deploy/agent/agent.yaml` | Manifest done, **no image yet** |
| Build image inside minikube and roll out | `scripts/build-agent.sh` | Done; expects `agent/Dockerfile` |
| Memory report | `scripts/measure-memory.sh` | Done; needs to be run and recorded |

### Contract the agent must meet (from `deploy/agent/agent.yaml`)

- Image `ft2-agent:dev`, built from `agent/` straight into minikube (`imagePullPolicy: Never`). No registry is involved, and the image builds for whatever CPU the cluster runs (arm64 on Apple Silicon, amd64 elsewhere). Dependencies must therefore be pure Python or ship wheels for both.
- HTTP on port **8000**, `GET /healthz` for readiness.
- Reads standard `OTEL_*` env vars: `OTEL_SERVICE_NAME`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_PROTOCOL=grpc`, `OTEL_RESOURCE_ATTRIBUTES`, `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`.
- `STUB_MODEL=1` selects the fake model. `LLM_API_KEY` comes from the optional secret `llm-api` and is only needed when `STUB_MODEL=0`.

### Gaps found while reviewing

- **No `.gitignore`.** Add one before anyone creates a key file (`.env`, `*.secret.yaml`, `secret.yaml`, `__pycache__/`, `.venv/`). Git only ignores files it is not already tracking, so this must land first.
- `smoke-trace.sh` uses `minikube kubectl` without the `MINIKUBE_PROFILE` variable the other scripts honor. This is minor and only matters for a non-default profile.
- Tempo uses `emptyDir`, so restarting it before the demo erases the traces we want to show. That's fine for checkpoint 1; a PVC is worth considering for checkpoint 3.

## 3. Agent design

### 3.1 Layout

```
agent/
  Dockerfile              # python:3.12-slim, non-root, uvicorn on :8000
  .dockerignore           # .venv/, __pycache__/, tests/, .env
  requirements.txt        # pinned
  ft2_agent/
    app.py                # FastAPI: /healthz, POST /tasks
    agent.py              # run_task(): the agent loop
    llm.py                # LLM interface + StubModel + RealModel
    tools.py              # search_policies, lookup_order, tool schemas
    telemetry.py          # OTel SDK setup + GenAI span helpers
    check.py              # answer check for test tasks (skeleton now, used at checkpoint 3)
  data/
    policies/             # refund-policy-2026.md (14 days), shipping.md, ...
    policies_archive/     # refund-policy-2025.md (30 days): seeded failure later, unused now
    orders.json           # order 1042 etc.
    test_tasks.json       # question, expected facts (for check.py and tests)
  tests/
    test_tools.py
    test_agent_stub.py
    test_spans.py         # asserts trace shape with InMemorySpanExporter
```

### 3.2 Request flow

```
POST /tasks {"task_id": "t-1", "question": "Can I return order 1042 after 20 days?"}
  -> run_task(task, parent_context=None)
       loop (max 6 steps):
         model.step(messages, tools)  -> tool calls | final answer
         execute each tool call, append results
  <- {"task_id", "answer", "trace_id", "steps": [...]}
```

`run_task` takes an optional **parent context carrier** (a dict of W3C `traceparent` headers). At checkpoint 1 FastAPI supplies the parent. At checkpoint 2 the Kafka consumer passes the message headers into the same function, so the loop needs no changes for Kafka. This is the main seam between Richard's and Tim's work.

### 3.3 Tools

| Tool | Input | Behavior | Returns |
|---|---|---|---|
| `search_policies` | `query: str` | Keyword-overlap scoring over `data/policies/*.md`; top 2 docs | `[{doc_id, version, effective_date, text}]` |
| `lookup_order` | `order_id: str` | Lookup in `orders.json` | `{order_id, status, delivered_date, items}` or a not-found error |

Keep search deliberately simple (no embeddings or vector DB). The demo is about tracing, and a keyword search keeps the image small and the results deterministic. Every result carries `doc_id`, `version` and `effective_date`, and those land on the span. This is what lets the checkpoint 3 check point at the search step when it returns the 2025 document.

### 3.4 Model interface

```python
class Model(Protocol):
    name: str                      # used as gen_ai.request.model
    def step(self, messages, tools) -> ModelTurn: ...   # tool_calls | text, usage
```

- **StubModel** (default, `STUB_MODEL=1`): deterministic script. Turn 1 calls `search_policies(question)` and `lookup_order(<id parsed by regex>)`. Turn 2 builds the answer from the returned policy text (for example "Yes/No, the return window is N days from delivery"). It reports fake but plausible token counts so the usage attributes are filled in. Two knobs help later checkpoints:
  - `STUB_LATENCY_MS`: sleep per model call. Without it the stub finishes in about 1 ms, and Kafka lag never builds up enough to show KEDA scaling.
  - `STUB_CPU_MS`: busy-loop per call, so the HPA-on-CPU comparison has CPU to react to.
- **RealModel** (`STUB_MODEL=0`): one provider SDK with native tool calling and a small, cheap model, reading `LLM_API_KEY`. It's only used for the trace walkthrough, per the cost risk on the proposal. **Team decision needed: which provider.** Nothing else in the design depends on it.

Both models emit exactly the same span shape, so traces from stub runs and real runs line up and can be compared.

### 3.5 Tracing (OpenTelemetry GenAI conventions)

We write spans by hand with the OTel SDK instead of using a vendor auto-instrumentation package. The stub model needs the same spans as the real one, and finding where the conventions fall short is a stated deliverable, which needs us to apply them ourselves. The GenAI conventions are still "Development" status, so set `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental` and record the semconv version we followed in the README.

Expected trace for one task:

```
POST /tasks                                   (FastAPI instrumentation, SERVER)
└─ invoke_agent support-agent                 gen_ai.operation.name=invoke_agent
   │                                          gen_ai.agent.name=support-agent, ft2.task.id
   ├─ chat stub-model                         gen_ai.operation.name=chat, gen_ai.provider.name,
   │                                          gen_ai.request.model, gen_ai.usage.input_tokens/output_tokens,
   │                                          gen_ai.response.finish_reasons=["tool_calls"]
   ├─ execute_tool search_policies            gen_ai.operation.name=execute_tool, gen_ai.tool.name,
   │                                          gen_ai.tool.call.id, ft2.policy.doc_ids, ft2.policy.versions
   ├─ execute_tool lookup_order               gen_ai.tool.name, gen_ai.tool.call.id, ft2.order.id
   └─ chat stub-model                         finish_reasons=["stop"]
```

- Prompts and completions are recorded only when `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true`, as span events or attributes depending on what the current semconv says. Tim confirms which form.
- Attributes we add that the conventions don't cover (`ft2.task.id`, `ft2.policy.*`, later `ft2.check.*`, `ft2.attempt`) use the `ft2.` prefix. Each one is evidence for the "where the conventions fell short" section of the report.
- Tool exceptions set span status `ERROR` and `error.type`. A wrong-but-successful result stays `OK`. That is the "green logs" problem, and the checkpoint 3 check deals with it.
- Use `BatchSpanProcessor`, and call `force_flush()` on shutdown so the last task's spans survive a pod stop.

### 3.6 Container

- `python:3.12-slim`, non-root user, `uvicorn ft2_agent.app:app --host 0.0.0.0 --port 8000`.
- Copy `requirements.txt` and install before copying the code, so code edits don't reinstall dependencies.
- Expected dependencies: `fastapi`, `uvicorn`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc`, `opentelemetry-instrumentation-fastapi`, plus one LLM SDK. All have arm64 and amd64 wheels.
- Memory: stays well under the 512 Mi limit already in `agent.yaml`.

### 3.7 Patterns carried over from the course labs

The agent service follows the shape of the Lab 5 job-submitter app, so the code will look familiar to anyone grading or reviewing it:

| Lab 5 job submitter | FT² agent |
|---|---|
| FastAPI app, `POST /tasks`, `GET /healthz` | Same endpoints |
| Pydantic request model with `extra = "forbid"` | `TaskRequest(task_id, question)`, also `forbid` |
| `_parse_command` allowlist: the request may never pick an arbitrary script | Tool allowlist: the model may only call registered tools, and arguments are validated against each tool's schema before running |
| Errors mapped to HTTP codes (400 bad input, 503/504 upstream) | Same: 400 bad task, 502 LLM API error, 504 step limit or timeout |
| Config from env vars, secrets from a K8s Secret, `secret.example.yaml` committed | Same (`llm-api` secret, optional) |
| `python:3.12-slim`, requirements layer before code | Same |

What we deliberately do **not** carry over:

- **No local model (distilgpt2 / PyTorch).** That image was over 1 GB and needed 1–3 GiB per pod. That would make scale-from-zero slow and fill the 6 GB cluster after a few workers, and distilgpt2 cannot do tool calling. The stub model replaces it for every load test.
- **No Jobs or RBAC in the agent.** The agent never calls the Kubernetes API. Workers are a Deployment that KEDA scales, which is the "stays up and serves" case from Lab 5, not the "runs once" case.
- **No Docker Hub push.** Images build straight into minikube.

## 4. Tests

- `test_tools.py`: search returns the 2026 refund policy for a refund question, and `lookup_order("1042")` returns the fixture while an unknown id returns an error.
- `test_agent_stub.py`: on the 1042 question, the stub agent calls both tools and the answer mentions 14 days.
- `test_spans.py`: with an `InMemorySpanExporter`, the finished spans form one trace with the tree in §3.5 and carry the required `gen_ai.*` attributes. This is the regression guard for the tracing work.

Run with `pytest agent/tests`. A GitHub Actions workflow (`ruff` + `pytest` + `docker build`) is on the checkpoint 2 list. It takes about 20 lines, so add it now if time allows.

## 5. Checkpoint 1 acceptance checklist

```bash
scripts/cluster-up.sh
minikube kubectl -- apply -f deploy/observability/
scripts/smoke-trace.sh                 # pipeline OK
scripts/build-agent.sh                 # agent pod Ready
scripts/smoke-agent.sh                 # NEW: POST /tasks, print answer, find trace in Tempo
scripts/measure-memory.sh              # record numbers in README
```

- [ ] `POST /tasks` with the order 1042 question returns the 14-day answer, and the response lists both tools.
- [ ] Grafana Explore → Tempo shows that task as one trace with the span tree in §3.5.
- [ ] Stub mode is the default and needs no secrets.
- [ ] `pytest` passes.
- [ ] Memory usage recorded (proposal risk item).
- [ ] Real-model mode works with the `llm-api` secret. **Stretch for Nov 18**: the stub covers every checkpoint 1 requirement.

`scripts/smoke-agent.sh` copies the approach of `smoke-trace.sh`: port-forward or `kubectl exec` a request, take `trace_id` from the response, then poll `tempo:3200/api/traces/<id>` until it returns.

## 6. Work order

1. `.gitignore` and the `agent/` skeleton with `/healthz`, then build and deploy to prove the contract (pod Ready).
2. Data fixtures and the two tools, with tests.
3. StubModel and the agent loop, with tests.
4. `telemetry.py` and the span helpers, with the span-shape test. **Pair with Tim on attribute names.**
5. `smoke-agent.sh`, then an end-to-end run on minikube and a screenshot of the trace for the checkpoint.
6. Stretch: RealModel, then the CI workflow.

## 7. Built now so checkpoints 2 and 3 are cheap

- `run_task(task, parent_context)` is already the Kafka consumer's entry point, so checkpoint 2 adds a consumer loop and header propagation and leaves the agent alone.
- `task_id` is carried end to end. Because tools are read-only, running a task twice is safe, which the "killed worker's task finishes on another pod" demo needs.
- The archived 2025 policy and `test_tasks.json` expected facts are already in place for the seeded silent failure and `check.py`.
- The stub latency and CPU knobs exist for the KEDA-vs-HPA and throughput experiments.

## 8. Open decisions for the team

1. LLM provider and model for `RealModel`.
2. Who writes `telemetry.py`: Richard writes it and Tim reviews (proposed), or Tim owns it.
3. Package layout: one `agent/` image that serves both HTTP (checkpoint 1) and the Kafka worker (checkpoint 2) through an entrypoint flag (proposed), or two images.
