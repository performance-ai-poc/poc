DASHBOARD_UI_DIR ?= ./dashboard-ui
DASHBOARD_UI_IMAGE := dashboard-ui:$(TAG)
DASHBOARD_UI_RESOURCE := $(RELEASE)-ai-chat-dashboard-ui

.PHONY: build-dashboard-ui
build-dashboard-ui:
	docker build --no-cache -t $(DASHBOARD_UI_IMAGE) $(DASHBOARD_UI_DIR)

.PHONY: load-dashboard-ui
load-dashboard-ui:
	minikube image load -p $(MINIKUBE_PROFILE) $(DASHBOARD_UI_IMAGE)

.PHONY: prepare-dashboard-ui
prepare-dashboard-ui: build-dashboard-ui load-dashboard-ui

.PHONY: restart-dashboard-ui
restart-dashboard-ui:
	kubectl rollout restart \
		-n $(NAMESPACE) \
		deployment/$(DASHBOARD_UI_RESOURCE)

.PHONY: rebuild-dashboard-ui
rebuild-dashboard-ui: prepare-dashboard-ui deploy restart-dashboard-ui

.PHONY: logs-dashboard-ui
logs-dashboard-ui:
	kubectl logs \
		-n $(NAMESPACE) \
		-f deployment/$(DASHBOARD_UI_RESOURCE)
