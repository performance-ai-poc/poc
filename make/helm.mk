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
	--set-string orchestrator.env.corsAllowedOrigins=$(CORS_ALLOWED_ORIGINS) \
	--set-string customerUi.ingress.enabled=$(CUSTOMER_UI_INGRESS_ENABLED) \
	--set-string customerUi.ingress.className=$(CUSTOMER_UI_INGRESS_CLASS_NAME) \
	--set-string customerUi.ingress.host=$(CUSTOMER_UI_INGRESS_HOST) \
	--set-string customerUi.ingress.path=$(CUSTOMER_UI_INGRESS_PATH) \
	--set-string customerUi.ingress.pathType=$(CUSTOMER_UI_INGRESS_PATH_TYPE) \
	--set-string customerUi.ingress.tls.enabled=$(CUSTOMER_UI_INGRESS_TLS_ENABLED) \
	--set-string customerUi.ingress.tls.secretName=$(CUSTOMER_UI_INGRESS_TLS_SECRET_NAME)

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
