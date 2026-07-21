# Infra

Deployment infrastructure for the AI chat POC.

Current contents:

- `helm/` - Helm chart hierarchy for the local Kubernetes deployment.

The repo's root `Makefile` uses these files for `make helm-lint`,
`make helm-template`, and `make deploy`.
