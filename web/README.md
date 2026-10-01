# web/: the React map

The map of Extreme Weather Watch as a web page. It reads only the local API (`uv run eww serve`) and holds no data logic: filters, severity and merges are decided in Python, and the page only draws what the API returns.

## The tools, in plain words

- **Node** runs JavaScript outside the browser. Here it only runs the build tools; nothing in production needs it.
- **npm** is Node's package manager, like uv for Python. `package.json` is the `pyproject.toml`; `package-lock.json` is the `uv.lock`; packages land in `node_modules/` (like `.venv`).
- **TypeScript** is JavaScript with type hints that are checked before the code runs, like mypy but strict: `npm run build` refuses to finish while any type error remains.
- **Vite** is the dev server and the bundler. In development it serves the source with instant reloads; `npm run build` turns it into a few small files in `dist/`.
- **React** builds the page from components: functions that take data and return what to show. When the data changes, React redraws only what changed.
- **MapLibre GL JS** draws the map in the browser with the graphics card. It is the biggest piece (about 280 KB compressed), so it loads after the page shell.

## Run it

Once, from this folder: `npm install` (on the corporate network, if it fails with a certificate error, set `NODE_EXTRA_CA_CERTS` to the corporate root file first).

Then two terminals:

```bash
uv run eww serve     # repository root: the API on http://127.0.0.1:8000
npm run dev          # web/: the map on http://localhost:5173
```

The API only answers browser calls from port 5173, so keep the dev server there.

| Command | What it does |
|---|---|
| `npm run dev` | Development server with live reload |
| `npm run typecheck` | Type-check only |
| `npm run build` | Type-check, then write the production files to `dist/` |
| `npm run preview` | Serve `dist/` on port 5173 to try the production build |

The API address is `API_BASE_URL` in `src/config.ts` (override with `VITE_API_BASE_URL` in `web/.env.local`).

## Where things live

| Path | What it is |
|---|---|
| `src/main.tsx` | Entry point: mounts the app into `index.html` |
| `src/App.tsx` | The page layout and the view state (filters, selected event, map position), mirrored into the URL |
| `src/url.ts` | Reading and writing the URL query |
| `src/api.ts` | The only code that calls the API, one function per endpoint |
| `src/types.ts` | The shapes the API returns (docs/architecture.md section 2) |
| `src/config.ts` | Settings in one place: API address, basemap, window presets, severity steps, hazard colours |
| `src/MapView.tsx` | The MapLibre map: pins, clusters, footprints, geolocation |
| `src/FiltersForm.tsx`, `src/EventList.tsx`, `src/StatusStrip.tsx` | Filters, the list of shown events, the data freshness line |
| `src/Panel.tsx` | The selected event: Details, News, Posts and Weather tabs |
| `src/About.tsx` | The About page with every credit, from `/attributions` |
| `src/useResource.ts` | Loads one API answer, cancels stale requests, caches revisits |
