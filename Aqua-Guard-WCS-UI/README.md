# Aqua Guard — React dashboard

React 18 + TypeScript + Vite + Tailwind. It talks to the Flask API in `backend/` (see the project root README).

```bash
npm install
npm run dev          # http://localhost:5173
npm run build        # production bundle in dist/
```

* **API address**: `VITE_API_BASE` (see `.env.example`); if unset the app uses `http(s)://<page host>:5000/api`, so opening the dev server from a phone at `http://<PC-LAN-IP>:5173` automatically calls `http://<PC-LAN-IP>:5000/api`.
  The backend must allow that origin (`AQUAGUARD_CORS_ORIGINS` or `AQUAGUARD_CORS_ALLOW_PRIVATE_LAN=true`) and listen on the LAN (`AQUAGUARD_HOST=0.0.0.0`).
* **Auth**: server-side session in an HttpOnly cookie + per-session CSRF header (kept in memory). Reloading the page restores the session through `/api/me`. Roles are enforced by the server; the UI only hides what a role may not use. First login after an account is created/reset forces a password change.
* **Pages**: Overview, Water Monitoring, Leak Detection, Rainwater System, Devices (online/last seen, link quality, gateway channel/RSSI, sessions), Analytics (real-time, daily/weekly/monthly, flow comparison, leak and rain history), Alerts, Reports (user) / Admin (admin), Profile.
* **Live data**: polling with automatic back-off and recovery; the last data stays on screen with a "connection lost" banner.
* `src/app/components/Dashboard.tsx` is a legacy, unused component kept for reference.
