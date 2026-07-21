RELEASE ?= demo
NAMESPACE ?= default
TAG ?= demo
CHART_DIR ?= ./infra/helm/ai-chat
MINIKUBE_PROFILE ?= minikube

.PHONY: help
help:
	@echo "Available commands:"
	@echo "  make doctor"
	@echo "  make dev"
	@echo "  make build-all"
	@echo "  make load-all"
	@echo "  make prepare-all"
	@echo "  make deploy"
	@echo "  make status"
	@echo "  make port-forward-start"
	@echo "  make port-forward-stop"
	@echo ""
	@echo "Per service:"
	@echo "  make rebuild-customer-ui"
	@echo "  make rebuild-dashboard-ui"
	@echo "  make rebuild-orchestrator"
	@echo "  make rebuild-mcp"

.PHONY: doctor
doctor:
	@command -v docker >/dev/null || { echo "docker missing"; exit 1; }
	@command -v kubectl >/dev/null || { echo "kubectl missing"; exit 1; }
	@command -v minikube >/dev/null || { echo "minikube missing"; exit 1; }
	@command -v helm >/dev/null || { echo "helm missing"; exit 1; }
	@helm lint $(CHART_DIR)
	@echo "Environment looks ready."

.PHONY: status
status:
	@kubectl get deployments -n $(NAMESPACE)
	@kubectl get pods -n $(NAMESPACE)
	@kubectl get services -n $(NAMESPACE)

.PHONY: build-all
build-all: build-customer-ui build-dashboard-ui build-orchestrator build-mcp

.PHONY: load-all
load-all: load-customer-ui load-dashboard-ui load-orchestrator load-mcp

.PHONY: prepare-all
prepare-all: build-all load-all

.PHONY: dev
dev: minikube-start prepare-all deploy status