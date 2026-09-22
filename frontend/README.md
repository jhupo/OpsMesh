# OpsMesh Web

OpsMesh Web vendors the complete [`shadcn-admin`](https://github.com/satnaing/shadcn-admin)
application source at upstream commit `e16c87f`, with the internationalization implementation from
pull request [#311](https://github.com/satnaing/shadcn-admin/pull/311) at commit `95b8f5a` applied on
top. The upstream navigation, pages, components, layouts, and example data remain available as the
frontend baseline. OpsMesh integrations are added at product boundaries instead of rewriting the
foundation components.

The first OpsMesh integration layer provides real password login and protected sessions, accessible
workspace/project loading, an `All Projects` selector, and the shared header actions. The application
remains a Vite SPA built with React, TypeScript, TanStack Router, TanStack Query, Tailwind CSS, and
source-owned shadcn/ui components.

## Source layout

- `src/api`: OpsMesh backend transport and typed API errors; no domain policy.
- `src/components/ui`: shadcn/ui source components managed through `components.json`.
- `src/components/layout`: application shell, navigation, and responsive layout.
- `src/context`: global visual, layout, workspace, and project selection providers.
- `src/features`: complete upstream examples plus OpsMesh-owned product slices.
- `src/i18n`: PR #311 locale registry, browser detection, persistence, and translations.
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

Password login replaces the upstream mock sign-in action and uses the public `/auth/login`,
`/auth/me`, and token revocation contracts. The raw
bearer token is kept in tab-scoped `sessionStorage`, cleared when the bearer credential is rejected
or the user logs out, and never written to persistent browser storage. A rejected current password
does not discard a valid session. A successful password change follows the backend's token
revocation contract and returns the browser to login. Protected routes validate the current user
before rendering. The application also owns explicit 401, 403, 404, 500, and 503 routes.

On 2026-09-21, browser verification against the installed server passed password login,
`/auth/me`, protected-route refresh, logout with server-side token revocation, and login again.
On 2026-09-22, the frontend source was reset to the complete upstream application and PR #311 i18n
baseline. The first retained OpsMesh integrations are authentication, workspace/project context,
the `All Projects` selector, and the shared header actions. The login form uses the Shadcn Space
`label-06` floating-label component; its Email/Password labels stay English by request. Only Chinese
and English locales are enabled. The theme button toggles light/dark directly. Settings and user
actions live in the header avatar menu, not the sidebar; the duplicate dashboard top navigation
and sidebar user footer are removed. The authenticated layout owns one fixed header with a bottom
divider; routes replace only the independently scrolling content. Settings use registry `tabs-06`
vertical animated navigation (horizontal at mobile widths). Further product replacement should be
performed feature by feature without deleting the upstream examples first.

Profile combines name/email and password settings, using `PATCH /auth/me` and `PUT /auth/password`.
Biography and notification preferences are disabled placeholders; they do not persist browser-only
data or report simulated saves. Birthday, profile links, appearance and the separate account page
have been removed. Only a development session with authoritative `/auth/me.platform_admin === true`
can enable the Router/Query debug panels in Settings → Display. That display preference is local
and per-user; login and non-admin sessions never mount the panels. An installed backend without
this response field keeps the panels hidden until its release is updated.

Verification on 2026-09-22: installed-server login and profile save passed through the SSH tunnel.
An isolated in-memory instance of the real auth API verified the admin toggle and ordinary-user
rejection without changing server roles. Desktop/mobile light/dark review covered login, settings,
the persistent header, content scrolling and route changes. Five affected backend auth flows,
Ruff, frontend lint, TypeScript and the production build passed. Notification preference storage
and biography storage remain explicitly reserved, not implemented.

## Attribution

The retained `shadcn-admin` source is used under its MIT license. See
`licenses/shadcn-admin-MIT.txt`. The `label-06` and `tabs-06` source retains the Shadcn Space MIT notice in
`licenses/shadcn-space-MIT.txt`. OpsMesh changes remain under the repository's LGPL-3.0 license.
