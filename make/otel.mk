OTEL_COMPOSE_FILE ?= otel/docker-compose.otel.yml

.PHONY: otel-up
otel-up:
	docker compose -f $(OTEL_COMPOSE_FILE) up -d
	@echo "Collector: http://localhost:4318 (OTLP/HTTP), :4317 (OTLP/gRPC)"
	@echo "OpenObserve: http://localhost:5080"

.PHONY: otel-down
otel-down:
	docker compose -f $(OTEL_COMPOSE_FILE) down

.PHONY: otel-logs
otel-logs:
	docker compose -f $(OTEL_COMPOSE_FILE) logs -f otel-collector

.PHONY: otel-status
otel-status:
	docker compose -f $(OTEL_COMPOSE_FILE) ps

.PHONY: otel-test
otel-test: ## Runs the acceptance scripts that only need otel-up (not the app or a cluster).
	./otel/tests/test_collector_up.sh
	./otel/tests/test_redaction.sh
