# Observability Helm Chart

Independent Kubernetes observability plane for the Performance AI POC.

This chart owns:

- The OpenTelemetry Collector DaemonSet and node-local OTLP ports.
- Read-only Collector RBAC and Kubernetes metadata discovery.
- Redaction, allowlisting, and capture-policy processing.
- OpenObserve, its credentials, Service, and persistent storage.
- `analytics-svc`, the read-only telemetry analytics API.
- `dashboard-ui`, including an optional Nginx Ingress.

Application charts do not depend on these Kubernetes object names. Instrumented
pods export OTLP to the Collector on their node through `status.hostIP:4317`.

## Install

```bash
helm upgrade --install observability ./infra/helm/observability \
  --namespace ai-chat \
  --create-namespace \
  --wait \
  --timeout 5m
```

Install or upgrade the application independently:

```bash
helm upgrade --install demo ./infra/helm/ai-chat \
  --namespace ai-chat \
  --create-namespace
```

## Capture policy

Metadata-only is the safe default:

```bash
helm upgrade observability ./infra/helm/observability \
  --namespace ai-chat \
  --reuse-values \
  --set-string otelCollector.captureMode=metadata-only
```

Use `content-approved` only when content capture has been explicitly approved.

## Existing release migration

The old `demo` release owned OpenObserve and the Collector. Preserve its
OpenObserve claim before upgrading the application chart:

```bash
kubectl annotate pvc demo-ai-chat-openobserve \
  --namespace ai-chat \
  helm.sh/resource-policy=keep \
  --overwrite

helm upgrade demo ./infra/helm/ai-chat --namespace ai-chat --reuse-values

helm upgrade --install observability ./infra/helm/observability \
  --namespace ai-chat \
  --set-string openObserve.persistence.existingClaim=demo-ai-chat-openobserve \
  --wait \
  --timeout 5m
```

This retains existing traces while moving the running observability resources
to the new Helm release. Back up shared-environment data before any Helm
ownership migration.

## Verify

```bash
helm lint ./infra/helm/observability
helm template observability ./infra/helm/observability --namespace ai-chat
kubectl get daemonset,deployment,service,pvc -n ai-chat
```

OpenObserve can be reached locally with:

```bash
kubectl port-forward -n ai-chat service/observability-openobserve 5080:5080
```

The dashboard can be reached through its NodePort `30082`, by port-forwarding
`service/observability-dashboard-ui`, or through the optional `dashboard.local`
Ingress when an ingress controller is installed.
