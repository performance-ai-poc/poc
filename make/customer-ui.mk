CUSTOMER_UI_DIR ?= ./customer-ui
CUSTOMER_UI_IMAGE := customer-ui:$(TAG)
CUSTOMER_UI_RESOURCE := $(RELEASE)-ai-chat-customer-ui

.PHONY: build-customer-ui
build-customer-ui:
	docker build --no-cache -t $(CUSTOMER_UI_IMAGE) $(CUSTOMER_UI_DIR)

.PHONY: load-customer-ui
load-customer-ui:
	minikube image load -p $(MINIKUBE_PROFILE) $(CUSTOMER_UI_IMAGE)

.PHONY: prepare-customer-ui
prepare-customer-ui: build-customer-ui load-customer-ui

.PHONY: restart-customer-ui
restart-customer-ui:
	kubectl rollout restart \
		-n $(NAMESPACE) \
		deployment/$(CUSTOMER_UI_RESOURCE)

.PHONY: rebuild-customer-ui
rebuild-customer-ui: prepare-customer-ui deploy restart-customer-ui

.PHONY: logs-customer-ui
logs-customer-ui:
	kubectl logs \
		-n $(NAMESPACE) \
		-f deployment/$(CUSTOMER_UI_RESOURCE)
