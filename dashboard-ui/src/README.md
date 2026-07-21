# Dashboard UI Source

React source for the future observability dashboard.

Current entrypoints:

- `main.tsx` mounts the React app into `index.html`.
- `App.tsx` renders the current Vite starter screen.
- `index.css` and `App.css` contain global and component-level styles.
- `assets/` contains images and SVGs imported by React components.

When dashboard features are added, this folder should grow around telemetry
views for request, session, tenant, agent, MCP, and model activity.
