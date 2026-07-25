# AI Chat Helm Chart

Application chart for the local AI chat POC deployment.

The OpenTelemetry Collector and OpenObserve are intentionally owned by the
separate [`observability`](../observability/) chart so the observability team
can install, configure, and upgrade its plane independently.

It currently deploys:

- `customer-ui` as a NodePort service on port `30080`.
- Optional Ingress for `customer-ui` on a configurable host.
- `orchestrator-svc` as a NodePort service on port `30081`.
- `dashboard-ui` as a NodePort service on port `30082`.
- `mcp-server` as a ClusterIP service on port `8000`.
- PostgreSQL 16 as a one-replica StatefulSet with persistent storage.
- A post-install seed Job that creates the demo schema, inserts deterministic
  data, and creates the `mcp_readonly` SELECT-only role.

Default image tags, service types, ports, and orchestrator environment values
are defined in `values.yaml`.

Useful commands from the repo root:

```bash
make helm-lint
make helm-template
make deploy
make uninstall
```

For the full stack, install the observability release first:

```bash
make deploy-observability
make deploy-app
```

## Customer UI Ingress

The customer UI already serves the built React app through Nginx inside the
pod. If you want to expose it without port-forwarding, enable the optional
Ingress and point a local host name at Minikube:

```yaml
customerUi:
  ingress:
    enabled: true
    host: customer.local
```

Then:

```bash
minikube addons enable ingress
minikube ip
```

Add the Minikube IP to `/etc/hosts` for `customer.local`, then redeploy:

```bash
make deploy
```

Open `http://customer.local` in your browser.

For Postgres operations in Minikube:

```bash
make restart-postgres
make rollout-postgres
make logs-postgres
make logs-postgres-seed
```

## PostgreSQL and MCP

The Kubernetes deployment mirrors the database contract in the repository's
`docker-compose.yml`:

- `app` owns `appdb` and is used only by the first-install seed Job.
- `mcp_readonly` receives `CONNECT`, schema `USAGE`, and table `SELECT`.
- The long-running MCP pod receives only `READONLY_DATABASE_URL`.
- PostgreSQL is reachable only inside the cluster through the
  `<release>-ai-chat-postgres` ClusterIP Service.
- A StatefulSet-created PersistentVolumeClaim stores `/var/lib/postgresql/data`
  and is retained when the StatefulSet is deleted or scaled down.

The passwords in `values.yaml` intentionally match the local Compose POC. Pass
different values for a shared environment:

```bash
helm upgrade --install demo ./infra/helm/ai-chat \
  --namespace ai-chat \
  --create-namespace \
  --set postgresql.auth.appPassword='<app-password>' \
  --set postgresql.auth.readonlyPassword='<readonly-password>'
```

For production, supply credentials through an external secret-management
workflow instead of committing them to a values file. Passwords used by this
POC chart must also be safe inside PostgreSQL URI user-info fields.

### Verify the installation

```bash
kubectl get pods,services,pvc,jobs -n ai-chat
kubectl logs job/demo-ai-chat-mcp-seed -n ai-chat
kubectl logs deployment/demo-ai-chat-mcp-server -n ai-chat
```

Confirm that the owner can write:

```bash
kubectl exec -n ai-chat demo-ai-chat-postgres-0 -- \
  psql -U app -d appdb -c "CREATE TABLE permission_check (id integer); DROP TABLE permission_check;"
```

Confirm that the read-only user can read but cannot write:

```bash
kubectl exec -n ai-chat demo-ai-chat-postgres-0 -- env PGPASSWORD=mcp_readonly \
  psql -h 127.0.0.1 -U mcp_readonly -d appdb -c "SELECT count(*) FROM customers;"

kubectl exec -n ai-chat demo-ai-chat-postgres-0 -- env PGPASSWORD=mcp_readonly \
  psql -h 127.0.0.1 -U mcp_readonly -d appdb -c "DELETE FROM customers;"
```

The final command must fail with a PostgreSQL permission error.

### Persistence check

Record a table count, delete only the PostgreSQL pod, then wait for the
StatefulSet to recreate it:

```bash
kubectl delete pod demo-ai-chat-postgres-0 -n ai-chat
kubectl rollout status statefulset/demo-ai-chat-postgres -n ai-chat
```

The replacement pod mounts the same claim, so the seeded tables and data remain.
The chart intentionally does not re-run the destructive demo seed on upgrades.

If you change Postgres auth values or need to force the live pod to pick up a
new Secret, run:

```bash
make restart-postgres
make rollout-postgres
```
