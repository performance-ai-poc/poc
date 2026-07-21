MCP_SERVER_DIR ?= ./mcp-server
MCP_SERVER_IMAGE := mcp-server:$(TAG)
MCP_SERVER_RESOURCE := $(RELEASE)-ai-chat-mcp-server

.PHONY: build-mcp
build-mcp:
	docker build -t $(MCP_SERVER_IMAGE) $(MCP_SERVER_DIR)

.PHONY: load-mcp
load-mcp:
	minikube image load -p $(MINIKUBE_PROFILE) $(MCP_SERVER_IMAGE)

.PHONY: prepare-mcp
prepare-mcp: build-mcp load-mcp

.PHONY: restart-mcp
restart-mcp:
	kubectl rollout restart \
		-n $(NAMESPACE) \
		deployment/$(MCP_SERVER_RESOURCE)

.PHONY: rebuild-mcp
rebuild-mcp: prepare-mcp deploy restart-mcp

.PHONY: logs-mcp
logs-mcp:
	kubectl logs \
		-n $(NAMESPACE) \
		-f deployment/$(MCP_SERVER_RESOURCE)