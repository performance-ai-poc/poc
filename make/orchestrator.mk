ORCHESTRATOR_DIR ?= ./orchestrator-svc
ORCHESTRATOR_IMAGE := orchestrator-svc:$(TAG)
ORCHESTRATOR_RESOURCE := $(RELEASE)-ai-chat-orchestrator

.PHONY: build-orchestrator
build-orchestrator:
	docker build -t $(ORCHESTRATOR_IMAGE) $(ORCHESTRATOR_DIR)

.PHONY: load-orchestrator
load-orchestrator:
	minikube image load -p $(MINIKUBE_PROFILE) $(ORCHESTRATOR_IMAGE)

.PHONY: prepare-orchestrator
prepare-orchestrator: build-orchestrator load-orchestrator

.PHONY: restart-orchestrator
restart-orchestrator:
	kubectl rollout restart \
		-n $(NAMESPACE) \
		deployment/$(ORCHESTRATOR_RESOURCE)

.PHONY: rebuild-orchestrator
rebuild-orchestrator: prepare-orchestrator deploy restart-orchestrator

.PHONY: logs-orchestrator
logs-orchestrator:
	kubectl logs \
		-n $(NAMESPACE) \
		-f deployment/$(ORCHESTRATOR_RESOURCE)