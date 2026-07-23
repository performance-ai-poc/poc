HELM_IMAGE_VALUES := \
	--set customerUi.image.tag=$(TAG) \
	--set dashboardUi.image.tag=$(TAG) \
	--set orchestrator.image.tag=$(TAG) \
	--set mcpServer.image.tag=$(TAG)

HELM_RUNTIME_VALUES := \
	--set-string orchestrator.env.appEnv=$(APP_ENV) \
	--set-string orchestrator.env.host=$(HOST) \
	--set-string orchestrator.env.port=$(PORT) \
	--set-string orchestrator.env.logLevel=$(LOG_LEVEL) \
	--set-string orchestrator.env.agentLiveCalls=$(AGENT_LIVE_CALLS) \
	--set-string orchestrator.env.llmBaseUrl=$(LLM_BASE_URL) \
	--set-string orchestrator.env.llmApiKey=$(LLM_API_KEY) \
	--set-string orchestrator.env.llmModel=$(LLM_MODEL) \
	--set-string orchestrator.env.defaultTenantId=$(DEFAULT_TENANT_ID) \
	--set-string orchestrator.env.corsAllowedOrigins=$(CORS_ALLOWED_ORIGINS)

.PHONY: helm-lint
helm-lint:
	helm lint $(CHART_DIR)

.PHONY: helm-template
helm-template:
	helm template $(RELEASE) $(CHART_DIR) \
		--namespace $(NAMESPACE) \
		$(HELM_IMAGE_VALUES) \
		$(HELM_RUNTIME_VALUES)

.PHONY: deploy
deploy: helm-lint
	helm upgrade --install $(RELEASE) $(CHART_DIR) \
		--namespace $(NAMESPACE) \
		--create-namespace \
		--wait \
		--timeout 3m \
		$(HELM_IMAGE_VALUES) \
		$(HELM_RUNTIME_VALUES)

.PHONY: uninstall
uninstall:
	-helm uninstall $(RELEASE) -n $(NAMESPACE)
