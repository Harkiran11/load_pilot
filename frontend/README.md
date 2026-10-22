# Frontend dashboard

React, TypeScript, and Vite UI for viewing live loads, fleet schedules,
terminals, pricing advice, bids, shipments, and autopilot activity.
It calls the dispatcher API and receives live updates over WebSockets.

## Structure

```text
frontend/
├── src/
│   ├── pages/       Dashboard, load board, fleet, bids, shipments, and settings
│   ├── components/  Reusable screens, dialogs, maps, and UI elements
│   ├── api/         HTTP clients, endpoint definitions, and response types
│   ├── hooks/       Reusable React behavior and subscriptions
│   ├── context/     Shared application state
│   ├── config/      API configuration and navigation
│   ├── layouts/     Shared page shell and navigation layout
│   ├── lib/         Shared helpers
│   ├── utils/       Formatting and domain utilities
│   ├── App.tsx      Application routes
│   └── main.tsx     React entry point
├── vite.config.ts   Build plugins, path aliases, and development API proxies
├── .env.example     Optional local API settings
└── package.json     Dependencies and npm commands
```

## Run locally

Requires **Node.js 22.12+** and npm. Start the [backend](../backend/README.md)
on **8000** and simulator on **8087**. Run from `frontend/`:

```sh
npm ci
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Open **http://127.0.0.1:5173/dashboard**. Stop with `Ctrl+C`.
The development proxy uses the local service addresses by default. To change
them, copy `.env.example` to `.env` and edit:

```dotenv
VITE_API_BASE_URL=http://127.0.0.1:8000
SIMULATION_API_URL=http://127.0.0.1:8087
```

Restart Vite after changing settings. Keep model credentials in Python services.

## Useful commands

| Command | Purpose |
| --- | --- |
| `npm run typecheck` | Check TypeScript types |
| `npm run lint` | Run ESLint |
| `npm run build` | Type-check and generate `dist/` |
| `npm run preview -- --host 127.0.0.1 --port 4173 --strictPort` | Preview an existing build locally |
| `npm ci` | Reinstall dependencies from the lockfile |

Static hosting needs an `index.html` route fallback and API/WebSocket routing.
`VITE_API_BASE_URL` is baked into the build; simulator controls are development-only.
