#!/usr/bin/env bash
# Start the FT^2 minikube cluster. Override sizing with MINIKUBE_CPUS / MINIKUBE_MEMORY.
set -euo pipefail

PROFILE="${MINIKUBE_PROFILE:-minikube}"
CPUS="${MINIKUBE_CPUS:-4}"
MEMORY="${MINIKUBE_MEMORY:-6144}"

minikube start -p "$PROFILE" \
  --driver=docker \
  --cpus="$CPUS" \
  --memory="$MEMORY" \
  --kubernetes-version=stable

# metrics-server: needed for `kubectl top` and, later, the HPA comparison.
minikube -p "$PROFILE" addons enable metrics-server

minikube -p "$PROFILE" kubectl -- get nodes
