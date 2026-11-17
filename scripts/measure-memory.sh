#!/usr/bin/env bash
# Report how much memory the minikube node and our pods actually use (checkpoint 1 risk item).
set -euo pipefail

PROFILE="${MINIKUBE_PROFILE:-minikube}"
K="minikube -p $PROFILE kubectl --"

echo "== minikube container (docker stats)"
docker stats --no-stream --format "{{.Name}}: {{.MemUsage}} ({{.MemPerc}})" "$PROFILE"

echo
echo "== node"
$K top node

echo
echo "== pods, sorted by memory"
$K top pods -A --sort-by=memory
