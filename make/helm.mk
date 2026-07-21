HELM_IMAGE_VALUES := \
	--set customerUi.image.tag=$(TAG) \
	--set dashboardUi.image.tag=$(TAG) \
	--set orchestrator.image.tag=$(TAG) \
	--set mcpServer.image.tag=$(TAG)

.PHONY: helm-lint
helm-lint:
	helm lint $(CHART_DIR)

.PHONY: helm-template
helm-template:
	helm template $(RELEASE) $(CHART_DIR) \
		--namespace $(NAMESPACE) \
		$(HELM_IMAGE_VALUES)

.PHONY: deploy
deploy: helm-lint
	helm upgrade --install $(RELEASE) $(CHART_DIR) \
		--namespace $(NAMESPACE) \
		--create-namespace \
		--wait \
		--timeout 3m \
		$(HELM_IMAGE_VALUES)

.PHONY: uninstall
uninstall:
	-helm uninstall $(RELEASE) -n $(NAMESPACE)