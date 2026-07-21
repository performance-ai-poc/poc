CUSTOMER_UI_LOCAL_PORT ?= 8080
DASHBOARD_UI_LOCAL_PORT ?= 8082
ORCHESTRATOR_LOCAL_PORT ?= 8001
MCP_SERVER_LOCAL_PORT ?= 8000

.PHONY: port-forward-customer-ui
port-forward-customer-ui:
	kubectl port-forward \
		-n $(NAMESPACE) \
		service/$(CUSTOMER_UI_RESOURCE) \
		$(CUSTOMER_UI_LOCAL_PORT):80

.PHONY: port-forward-dashboard-ui
port-forward-dashboard-ui:
	kubectl port-forward \
		-n $(NAMESPACE) \
		service/$(DASHBOARD_UI_RESOURCE) \
		$(DASHBOARD_UI_LOCAL_PORT):80

.PHONY: port-forward-orchestrator
port-forward-orchestrator:
	kubectl port-forward \
		-n $(NAMESPACE) \
		service/$(ORCHESTRATOR_RESOURCE) \
		$(ORCHESTRATOR_LOCAL_PORT):8001

.PHONY: port-forward-mcp
port-forward-mcp:
	kubectl port-forward \
		-n $(NAMESPACE) \
		service/$(MCP_SERVER_RESOURCE) \
		$(MCP_SERVER_LOCAL_PORT):8000