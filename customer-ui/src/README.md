# Customer UI Source

React source for the customer-facing app shell.

Current entrypoints:

- `main.tsx` mounts the React app into `index.html`.
- `App.tsx` renders the chat thread and composer, and calls the orchestrator via `api.ts`.
- `index.css` and `App.css` contain global and component-level styles.
- `assets/` contains images and SVGs imported by React components.

When the chat workflow is added, keep API calling code isolated from rendering
components and preserve the orchestrator's returned `session_id` across turns.
