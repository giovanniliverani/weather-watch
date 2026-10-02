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
uv run eww serve     # repository root: the API on http://127.0.0.1:8710
npm run dev          # web/: the map on http://localhost:5710
```

The API answers browser calls only from http://localhost:5710 and http://127.0.0.1:5710 (`SERVE_CORS_ORIGINS` in `eww/config.py`; `API_PAGE_ORIGINS` in `src/config.ts` repeats it for the error message, and a test keeps the two equal), so keep the dev server there. The same goes for the production build: try it with `npm run preview`, which serves `dist/` on port 5710; opened from any other address, the map cannot reach the API.

| Command | What it does |
|---|---|
| `npm run dev` | Development server with live reload |
| `npm run typecheck` | Type-check only |
| `npm run build` | Type-check, then write the production files to `dist/` |
| `npm run preview` | Serve `dist/` on port 5710 to try the production build |

The API address is `API_BASE_URL` in `src/config.ts` (override with `VITE_API_BASE_URL` in `web/.env.local`). To offer another map style, add one entry to `BASEMAPS` there: a vector style URL, or raster tiles (for example satellite) with their credit line.

Two satellite styles are listed. "Today from space" is NASA GIBS daily imagery of the last complete UTC day; it needs no key. "Satellite" is Esri World Imagery and appears only when `web/.env.local` contains `VITE_ESRI_API_KEY=<your key>` (an ArcGIS Location Platform key with the basemaps privilege). `.env.local` is gitignored, so the key is never committed; it does end up in the built JavaScript, so restrict the key to this site's address in the Esri dashboard. Restart `npm run dev` after adding it. Keep pay-as-you-go off in Location Platform so the free tier stops instead of billing. A black satellite map with the pins still on it means Esri refused the key: check it has not expired and that its address restriction includes the address you open the map on.

What leaves the laptop: map tiles and fonts from OpenFreeMap, satellite tiles from NASA GIBS (gibs.earthdata.nasa.gov) or Esri (ibasemaps-api.arcgis.com, with your key in every tile address, so it shows in the browser's developer tools and in the corporate proxy's logs), photos or videos from their own sites when you open them, and (through `eww serve`, not the browser) the event's position to Open-Meteo when you point at, focus or open the Weather tab. The "find my location" button asks the browser for your position, and only after you click it and allow it in the browser's prompt; Chrome and Edge then send nearby Wi-Fi networks and your IP address to Google's or Microsoft's location service to work it out.

`audit.json` holds the last design and accessibility audit; `uv run eww report frontend` reads it into `docs/m7.md`.

## Where things live

| Path | What it is |
|---|---|
| `src/main.tsx` | Entry point: mounts the app into `index.html` |
| `src/App.tsx` | The page layout and the view state (filters, selected event, map position), mirrored into the URL |
| `src/url.ts` | Reading and writing the URL query |
| `src/api.ts` | The only code that calls the API, one function per endpoint |
| `src/types.ts` | The shapes the API returns (docs/architecture.md section 2) |
| `src/config.ts` | Settings in one place: API address and the page addresses it answers, the basemap list (one entry per map style), window presets. The severity steps come from the API (`/severity-steps`) |
| `src/theme.ts` | The dark/light theme and the chosen basemap, remembered per browser (not in the URL) |
| `src/symbols.ts`, `src/HazardSymbol.tsx` | The hazard symbols: glyph, colour per ground, rings for the Orange and Red score bands (`severity_band`), hollow when ended; drawn once for the map icons and the legend |
| `src/icons.tsx` | The interface icons (chevron, close, sun, moon) |
| `src/MapView.tsx` | The MapLibre map: symbols, footprints, geolocation, and the cluster markers in view |
| `src/clusters.ts` | A cluster as a button: a donut of its hazard mix around the count, named for screen readers |
| `src/FilterColumn.tsx`, `src/EventList.tsx` | The left column (data age, filters, legend with counts) and the list of shown events |
| `src/index.css` | The look: dark surfaces, the accent colour, the phone layout |
| `src/Panel.tsx` | The selected event: Details, News, Posts and Weather tabs |
| `src/About.tsx` | The About page with every credit, from `/attributions` |
| `src/useResource.ts` | Loads one API answer, cancels stale requests, caches revisits where asked (not forecasts) |
