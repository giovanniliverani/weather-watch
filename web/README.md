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

The API address is `API_BASE_URL` in `src/config.ts` (override with `VITE_API_BASE_URL` in `web/.env.local`). To offer another map style, add one entry to `BASEMAPS` there: a vector style URL, or raster tiles (for example satellite) with their credit line.

What leaves the laptop: map tiles and fonts from OpenFreeMap, photos or videos from their own sites when you open them, and (through `eww serve`, not the browser) the event's position to Open-Meteo when you open the Weather tab. The "find my location" button asks the browser for your position, and only after you click it and allow it in the browser's prompt; Chrome and Edge then send nearby Wi-Fi networks and your IP address to Google's or Microsoft's location service to work it out.

`audit.json` holds the last design and accessibility audit; `uv run eww report frontend` reads it into `docs/m7.md`.

## Where things live

| Path | What it is |
|---|---|
| `src/main.tsx` | Entry point: mounts the app into `index.html` |
| `src/App.tsx` | The page layout and the view state (filters, selected event, map position), mirrored into the URL |
| `src/url.ts` | Reading and writing the URL query |
| `src/api.ts` | The only code that calls the API, one function per endpoint |
| `src/types.ts` | The shapes the API returns (docs/architecture.md section 2) |
| `src/config.ts` | Settings in one place: API address, the basemap list (one entry per map style), window presets, severity steps |
| `src/theme.ts` | The dark/light theme and the chosen basemap, remembered per browser (not in the URL) |
| `src/symbols.ts`, `src/HazardSymbol.tsx` | The hazard symbols: glyph, colour per ground, rings for Orange and Red, hollow when ended; drawn once for the map icons and the legend |
| `src/icons.tsx` | The interface icons (chevron, close, sun, moon) |
| `src/MapView.tsx` | The MapLibre map: symbols, clusters, footprints, geolocation |
| `src/FilterColumn.tsx`, `src/EventList.tsx` | The left column (data age, filters, legend with counts) and the list of shown events |
| `src/index.css` | The look: dark surfaces, the accent colour, the phone layout |
| `src/Panel.tsx` | The selected event: Details, News, Posts and Weather tabs |
| `src/About.tsx` | The About page with every credit, from `/attributions` |
| `src/useResource.ts` | Loads one API answer, cancels stale requests, caches revisits |
