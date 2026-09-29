# Nexis Finance — frontend

React 19 + TypeScript + Vite, with TanStack Query for server state and Plotly for charts.

```bash
npm install
npm run dev        # http://localhost:5173 — proxies /api to http://127.0.0.1:8000
npm run typecheck
npm run build      # static bundle in dist/ (route-level code splitting; Plotly in its own chunk)
```

* `src/pages/`: one module per page (17 routes, lazy-loaded)
* `src/components/`: `Plot` (theme-aware Plotly wrapper), `DataTable` (sort, filter, paginate, CSV), metric strips with glossary tooltips, pickers, UI primitives
* `src/hooks/`: workspace context (dataset, portfolio, settings), query hooks, a job runner that polls background jobs
* `src/services/api.ts`: typed fetch wrapper that turns the API's structured errors into readable messages

Set `VITE_API_BASE_URL` to call an API on another origin instead of using the dev proxy.
