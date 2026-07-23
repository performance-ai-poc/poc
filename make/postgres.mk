POSTGRES_RESOURCE := $(RELEASE)-ai-chat-postgres
POSTGRES_SEED_JOB := $(RELEASE)-ai-chat-mcp-seed
POSTGRES_SEED_MANIFEST := /tmp/$(POSTGRES_SEED_JOB).yaml

.PHONY: restart-postgres
restart-postgres:
	kubectl rollout restart \
		-n $(NAMESPACE) \
		statefulset/$(POSTGRES_RESOURCE)

.PHONY: rollout-postgres
rollout-postgres:
	kubectl rollout status \
		-n $(NAMESPACE) \
		statefulset/$(POSTGRES_RESOURCE)

.PHONY: logs-postgres
logs-postgres:
	kubectl logs \
		-n $(NAMESPACE) \
		-f statefulset/$(POSTGRES_RESOURCE)

.PHONY: delete-postgres-pod
delete-postgres-pod:
	kubectl delete pod \
		-n $(NAMESPACE) \
		$(POSTGRES_RESOURCE)-0

.PHONY: logs-postgres-seed
logs-postgres-seed:
	kubectl logs \
		-n $(NAMESPACE) \
		job/$(POSTGRES_SEED_JOB)

.PHONY: seed-postgres
seed-postgres:
	helm template $(RELEASE) $(CHART_DIR) \
		--namespace $(NAMESPACE) \
		$(HELM_IMAGE_VALUES) \
		$(HELM_RUNTIME_VALUES) \
		--show-only templates/mcp-seed-job.yaml \
		> $(POSTGRES_SEED_MANIFEST)
	kubectl apply -n $(NAMESPACE) -f $(POSTGRES_SEED_MANIFEST)
