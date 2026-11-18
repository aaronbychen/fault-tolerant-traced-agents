# Fault Tolerant Traced Agents

## Local cluster quickstart

Requires Docker, minikube, and a bash shell (Git Bash on Windows).

```bash
scripts/cluster-up.sh                                  # minikube: 4 CPUs, 6 GB (override MINIKUBE_CPUS / MINIKUBE_MEMORY)
minikube kubectl -- apply -f deploy/observability/     # OTel Collector, Tempo, Grafana
scripts/smoke-trace.sh                                 # send test traces and confirm they land in Tempo
```

Open Grafana and go to Explore → Tempo:

```bash
minikube kubectl -- -n observability port-forward svc/grafana 3000:3000   # http://localhost:3000
```

Deploy the agent once `agent/` contains a Dockerfile (see the contract at the top of `deploy/agent/agent.yaml`):

```bash
scripts/build-agent.sh
```

Send one task to the agent and print its trace from Tempo (checkpoint 1 end-to-end check):

```bash
scripts/smoke-agent.sh                                    # default: "Can I return order 1042 after 20 days?"
scripts/smoke-agent.sh "How long does express shipping take for order 1044?"
```

The agent runs a stub model by default (`STUB_MODEL=1`), so no API key is needed. Run its tests locally with Python 3.12:

```bash
cd agent && python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/pytest
```

Using a non-default minikube profile? Prefix the scripts with `MINIKUBE_PROFILE=<name>`.

Apps inside the cluster send OTLP to `otel-collector.observability:4317` (gRPC) or `:4318` (HTTP).

`scripts/measure-memory.sh` reports node and per-pod memory usage.
