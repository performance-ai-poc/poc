POSTGRES_RESOURCE := $(RELEASE)-ai-chat-postgres
POSTGRES_SEED_JOB := $(RELEASE)-ai-chat-mcp-seed

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
