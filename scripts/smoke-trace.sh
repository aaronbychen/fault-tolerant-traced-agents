#!/usr/bin/env bash
# Verify the trace pipeline (app -> OTel Collector -> Tempo) without the agent:
# send a few synthetic traces with telemetrygen, then search for them in Tempo.
set -euo pipefail

K="minikube kubectl --"
NS=observability
SERVICE="ft2-smoke-$(date +%s)"

$K -n "$NS" delete job telemetrygen --ignore-not-found >/dev/null
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
sed "s/SERVICE_NAME/$SERVICE/" "$ROOT/deploy/smoke/telemetrygen-job.yaml" | $K apply -f -
$K -n "$NS" wait --for=condition=complete job/telemetrygen --timeout=120s

echo "Waiting for Tempo to ingest..."
for _ in $(seq 1 12); do
  RESULT=$($K -n "$NS" exec deploy/grafana -- \
    wget -qO- "http://tempo.observability:3200/api/search?tags=service.name%3D$SERVICE" || true)
  if echo "$RESULT" | grep -q traceID; then
    echo "OK: found traces for service $SERVICE in Tempo"
    echo "$RESULT" | head -c 400; echo
    exit 0
  fi
  sleep 5
done
echo "FAIL: no traces for $SERVICE in Tempo" >&2
exit 1
