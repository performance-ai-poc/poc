ANALYTICS_DIR ?= ./analytics-svc
ANALYTICS_IMAGE := analytics-svc:$(TAG)
ANALYTICS_RESOURCE := $(OBSERVABILITY_RELEASE)-analytics

.PHONY: build-analytics
build-analytics:
	docker build --no-cache -t $(ANALYTICS_IMAGE) $(ANALYTICS_DIR)

.PHONY: load-analytics
load-analytics:
	minikube image load -p $(MINIKUBE_PROFILE) $(ANALYTICS_IMAGE)

.PHONY: prepare-analytics
prepare-analytics: build-analytics load-analytics

.PHONY: restart-analytics
restart-analytics:
	kubectl rollout restart \
		-n $(NAMESPACE) \
		deployment/$(ANALYTICS_RESOURCE)

.PHONY: rebuild-analytics
rebuild-analytics: prepare-analytics deploy restart-analytics

.PHONY: logs-analytics
logs-analytics:
	kubectl logs \
		-n $(NAMESPACE) \
		-f deployment/$(ANALYTICS_RESOURCE)
