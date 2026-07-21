.PHONY: minikube-start
minikube-start:
	@minikube status -p $(MINIKUBE_PROFILE) >/dev/null 2>&1 || \
		minikube start -p $(MINIKUBE_PROFILE)
	@kubectl config use-context $(MINIKUBE_PROFILE) >/dev/null

.PHONY: minikube-stop
minikube-stop:
	minikube stop -p $(MINIKUBE_PROFILE)

.PHONY: minikube-delete
minikube-delete:
	minikube delete -p $(MINIKUBE_PROFILE)