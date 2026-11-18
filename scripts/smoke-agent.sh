#!/usr/bin/env bash
# Checkpoint 1 end-to-end check: send one task to the agent, then find its trace in Tempo
# and print the span tree. Override the question with: scripts/smoke-agent.sh "your question"
set -euo pipefail

PROFILE="${MINIKUBE_PROFILE:-minikube}"
K="minikube -p $PROFILE kubectl --"
QUESTION="${1:-Can I return order 1042 after 20 days?}"
TASK_ID="smoke-$(date +%s)"

# POST from inside the agent pod, so no port-forward is needed.
RESPONSE=$($K -n ft2 exec deploy/agent -- python -c '
import json, sys, urllib.request
body = json.dumps({"task_id": sys.argv[1], "question": sys.argv[2]}).encode()
req = urllib.request.Request("http://localhost:8000/tasks", data=body, headers={"Content-Type": "application/json"})
print(urllib.request.urlopen(req).read().decode())
' "$TASK_ID" "$QUESTION")

echo "$RESPONSE" | python3 -m json.tool
TRACE_ID=$(echo "$RESPONSE" | python3 -c 'import json, sys; print(json.load(sys.stdin)["trace_id"])')

echo "Waiting for trace $TRACE_ID in Tempo..."
for _ in $(seq 1 12); do
  TRACE=$($K -n observability exec deploy/grafana -- \
    wget -qO- "http://tempo.observability:3200/api/traces/$TRACE_ID" 2>/dev/null || true)
  if [ -n "$TRACE" ]; then
    echo "OK: trace $TRACE_ID is in Tempo"
    echo "$TRACE" | python3 -c '
import json, sys
data = json.load(sys.stdin)
spans = []
for batch in data.get("batches") or data.get("resourceSpans") or []:
    for scope in batch.get("scopeSpans") or batch.get("instrumentationLibrarySpans") or []:
        spans.extend(scope["spans"])
by_id = {s["spanId"]: s for s in spans}
children = {}
for s in spans:
    children.setdefault(s.get("parentSpanId") if s.get("parentSpanId") in by_id else None, []).append(s)
def show(parent, depth):
    for s in sorted(children.get(parent, []), key=lambda s: int(s["startTimeUnixNano"])):
        ms = (int(s["endTimeUnixNano"]) - int(s["startTimeUnixNano"])) / 1e6
        print("  %s%s  (%.1f ms)" % ("  " * depth, s["name"], ms))
        show(s["spanId"], depth + 1)
show(None, 0)
'
    exit 0
  fi
  sleep 5
done
echo "FAIL: trace $TRACE_ID not found in Tempo" >&2
exit 1
