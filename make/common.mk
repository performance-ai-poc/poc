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
	@echo "  make restart-postgres"
	@echo "  make rollout-postgres"
	@echo "  make logs-postgres"
	@echo "  make reset-stack"
	@echo ""
	@echo "Telemetry (otel/, standalone — not part of make dev):"
	@echo "  make otel-up"
	@echo "  make otel-down"
	@echo "  make otel-logs"
	@echo "  make otel-status"
	@echo "  make otel-test"

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

.PHONY: reset-stack
reset-stack:
	-helm uninstall $(RELEASE) -n $(NAMESPACE) >/dev/null 2>&1 || true
	-kubectl delete job,deploy,sts,svc,secret,pvc -n $(NAMESPACE) --all --ignore-not-found >/dev/null 2>&1 || true
	-kubectl delete pod -n $(NAMESPACE) --all --force --grace-period=0 --ignore-not-found >/dev/null 2>&1 || true
	-ids=$$(docker ps -aq --filter ancestor=customer-ui:$(TAG) --filter ancestor=dashboard-ui:$(TAG) --filter ancestor=orchestrator-svc:$(TAG) --filter ancestor=mcp-server:$(TAG)); \
		if [ -n "$$ids" ]; then docker rm -f $$ids >/dev/null 2>&1 || true; fi
	-docker rmi -f customer-ui:$(TAG) dashboard-ui:$(TAG) orchestrator-svc:$(TAG) mcp-server:$(TAG) >/dev/null 2>&1 || true
	-docker builder prune -af >/dev/null 2>&1 || true
	-minikube image rm customer-ui:$(TAG) >/dev/null 2>&1 || true
	-minikube image rm dashboard-ui:$(TAG) >/dev/null 2>&1 || true
	-minikube image rm orchestrator-svc:$(TAG) >/dev/null 2>&1 || true
	-minikube image rm mcp-server:$(TAG) >/dev/null 2>&1 || true
	-minikube image rm docker.io/library/customer-ui:$(TAG) >/dev/null 2>&1 || true
	-minikube image rm docker.io/library/dashboard-ui:$(TAG) >/dev/null 2>&1 || true
	-minikube image rm docker.io/library/orchestrator-svc:$(TAG) >/dev/null 2>&1 || true
	-minikube image rm docker.io/library/mcp-server:$(TAG) >/dev/null 2>&1 || true
