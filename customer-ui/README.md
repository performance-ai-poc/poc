# Customer UI

This folder contains the customer-facing React/Vite app shell for the AI chat
POC. It is currently still the default Vite starter screen, so no chat workflow
or orchestrator API integration exists yet.

## What Lives Here

- `src/` - React application source.
- `src/assets/` - starter image and SVG assets.
- `public/` - static assets served directly by Vite/Nginx.
- `Dockerfile` - multi-stage Node build served by Nginx.
- `nginx.conf` - SPA fallback config for the production container.
- `.env.example` - intended location for future Vite runtime configuration.

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
docker build -t customer-ui:demo ./customer-ui
```

The root Makefile wraps this with:

```bash
make build-customer-ui
make rebuild-customer-ui
```

## Integration Notes

The eventual customer workflow should call `orchestrator-svc`'s `POST /chat`
endpoint and preserve the returned `session_id` across turns. At the moment,
`src/App.tsx` does not read any environment variables or make network calls.
