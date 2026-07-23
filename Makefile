SHELL := /bin/bash
.DEFAULT_GOAL := help

-include orchestrator-svc/.env

include make/common.mk
include make/minikube.mk
include make/helm.mk
include make/customer-ui.mk
include make/dashboard-ui.mk
include make/orchestrator.mk
include make/mcp-server.mk
include make/postgres.mk
include make/port-forward.mk
