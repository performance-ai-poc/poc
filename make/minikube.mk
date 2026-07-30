.PHONY: minikube-start
minikube-start:
	@minikube status -p $(MINIKUBE_PROFILE) >/dev/null 2>&1 || \
		( \
			minikube start -p $(MINIKUBE_PROFILE) --wait=apiserver,system_pods --wait-timeout=5m || \
			{ echo "Minikube start failed; retrying after API-server warmup..."; sleep 10; minikube start -p $(MINIKUBE_PROFILE) --wait=apiserver,system_pods --wait-timeout=5m; } \
		)
	@kubectl config use-context $(MINIKUBE_PROFILE) >/dev/null
	@kubectl wait --for=condition=Ready nodes --all --timeout=120s >/dev/null

.PHONY: minikube-stop
minikube-stop:
	minikube stop -p $(MINIKUBE_PROFILE)

.PHONY: minikube-delete
minikube-delete:
	minikube delete -p $(MINIKUBE_PROFILE)
