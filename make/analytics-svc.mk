ANALYTICS_SVC_DIR ?= ./analytics-svc
ANALYTICS_SVC_IMAGE := analytics-svc:$(TAG)
ANALYTICS_SVC_RESOURCE := $(RELEASE)-ai-chat-analytics

.PHONY: build-analytics
build-analytics:
	docker build --no-cache -t $(ANALYTICS_SVC_IMAGE) $(ANALYTICS_SVC_DIR)

.PHONY: load-analytics
load-analytics:
	minikube image load -p $(MINIKUBE_PROFILE) $(ANALYTICS_SVC_IMAGE)

.PHONY: prepare-analytics
prepare-analytics: build-analytics load-analytics

.PHONY: restart-analytics
restart-analytics:
	kubectl rollout restart \
		-n $(NAMESPACE) \
		deployment/$(ANALYTICS_SVC_RESOURCE)

.PHONY: rebuild-analytics
rebuild-analytics: prepare-analytics deploy restart-analytics

.PHONY: logs-analytics
logs-analytics:
	kubectl logs \
		-n $(NAMESPACE) \
		-f deployment/$(ANALYTICS_SVC_RESOURCE)