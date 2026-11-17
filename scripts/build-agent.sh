#!/usr/bin/env bash
# Build the agent image straight into minikube's container runtime and roll the Deployment.
set -euo pipefail

PROFILE="${MINIKUBE_PROFILE:-minikube}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

minikube -p "$PROFILE" image build -t ft2-agent:dev "$ROOT/agent"
minikube -p "$PROFILE" kubectl -- apply -f "$ROOT/deploy/agent/agent.yaml"
minikube -p "$PROFILE" kubectl -- -n ft2 rollout restart deploy/agent
minikube -p "$PROFILE" kubectl -- -n ft2 rollout status deploy/agent --timeout=120s
