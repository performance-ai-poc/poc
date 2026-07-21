# Dashboard UI

This folder contains the future operator/observability dashboard React app for
the AI chat POC. It is currently still the default Vite starter screen, so no
telemetry views or API integration exist yet.

## What Lives Here

- `src/` - React application source.
- `src/assets/` - starter image and SVG assets.
- `public/` - static assets served directly by Vite/Nginx.
- `Dockerfile` - multi-stage Node build served by Nginx.
- `nginx.conf` - SPA fallback plus `/api/` proxy to the in-cluster orchestrator service.

## Running Locally

```bash
npm install
npm run dev
```

Other useful commands:

```bash
npm run lint
npm run build
npm run preview
```

## Container

From the repo root:

```bash
docker build -t dashboard-ui:demo ./dashboard-ui
```

The root Makefile wraps this with:

```bash
make build-dashboard-ui
make rebuild-dashboard-ui
```

## Integration Notes

The dashboard is expected to become the place where request IDs, run IDs,
session IDs, tenant IDs, and future agent/OTel telemetry can be inspected.
Today, the orchestrator logs those identifiers to stdout, but this UI does not
read or display them yet.
