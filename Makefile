SHELL := /bin/bash
.DEFAULT_GOAL := help

include make/common.mk
include make/minikube.mk
include make/helm.mk
include make/customer-ui.mk
include make/dashboard-ui.mk
include make/orchestrator.mk
include make/mcp-server.mk
include make/port-forward.mk