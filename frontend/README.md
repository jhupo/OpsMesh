# OpsMesh Web

OpsMesh Web uses the application skeleton and layout patterns from
[`shadcn-admin`](https://github.com/satnaing/shadcn-admin), adapted to the OpsMesh domain model.
The application is a Vite SPA built with React, TypeScript, TanStack Router, TanStack Query,
Tailwind CSS, and source-owned shadcn/ui components.

## Source layout

- `src/api`: backend transport and typed API errors; no domain policy.
- `src/components/ui`: shadcn/ui source components managed through `components.json`.
- `src/components/layout`: application shell, navigation, and responsive layout.
- `src/context`: global visual and layout providers.
- `src/features`: product slices; a feature owns its page composition and local contracts.
- `src/i18n`: locale registry, detection, persistence, and translations.
- `src/routes`: TanStack Router transport boundaries that compose features.
- `src/styles`: semantic tokens and global styles.

Routes must stay thin. Server state belongs in TanStack Query-backed feature clients; local visual
state stays near its feature. User-visible strings must be translation keys rather than literals.

## Commands

```bash
pnpm install --frozen-lockfile
pnpm dev
pnpm lint
pnpm typecheck
pnpm build
```

The development server proxies `/api` to `VITE_API_PROXY_TARGET` (default
`http://127.0.0.1:8000`). The browser uses `/api/v1` by default; set `VITE_API_BASE_URL` only when
the public API is mounted elsewhere.

For a remote installation whose API binds to loopback, keep an SSH tunnel open:

```bash
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 127.0.0.1:18000:127.0.0.1:8000 root@192.168.232.129
```

Set `VITE_API_PROXY_TARGET=http://127.0.0.1:18000` in the ignored `.env.local`.
The frontend remains local and the API, database and worker remain on the server.
Reconnect the tunnel if its SSH process exits; do not expose the API port just for development.

Password login uses the public `/auth/login`, `/auth/me`, and token revocation contracts. The raw
bearer token is kept in tab-scoped `sessionStorage`, cleared on authentication failure and logout,
and never written to persistent browser storage. Protected routes validate the current user before
rendering. The application also owns explicit 401, 403, 404, 500, and 503 routes.

On 2026-09-21, browser verification against the installed server passed password login,
`/auth/me`, protected-route refresh, logout with server-side token revocation, and login again.
Overview counters remain placeholders and are not evidence of connected workspace/product flows.

## Attribution

The retained `shadcn-admin` source and the Shadcn Space `toggle-01` component are used under their
MIT licenses. See `licenses/shadcn-admin-MIT.txt` and `licenses/shadcn-space-MIT.txt`. OpsMesh
changes remain under the repository's LGPL-3.0 license.
