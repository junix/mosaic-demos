# mosaic-demos

Database-backed plots built with Mosaic, vgplot, and a fully local DuckDB-WASM bundle. The primary output is twelve background-transparent PNGs; the browser views are an optional interactive companion.

Browse every demo with its source in **[gallery.html](gallery.html)** — searchable, follows your light/dark theme.

```bash
npm install
npm run build
npm run render   # out/*-transparent.png
npm test         # plus offline and pointer-interaction checks
```

Scenes cover large point density, dense signals, distributions, regression, cohorts, matrices, rankings, anomalies, vectors, small multiples, and SQL-linked brushing. All data is deterministic and synthetic. Runtime external requests are blocked by the validator.

## Transparent PNG reference gallery

`catalog.json` records the intended analytical question and visual family for every scene.

| Density | Signals | Linked views | Distributions |
|---|---|---|---|
| ![density](out/density-transparent.png) | ![signals](out/signals-transparent.png) | ![linked](out/linked-transparent.png) | ![distribution](out/distribution-transparent.png) |
| Regression | Retention | Quality matrix | Rankings |
| ![regression](out/regression-transparent.png) | ![retention](out/retention-transparent.png) | ![quality matrix](out/quality-matrix-transparent.png) | ![rankings](out/rankings-transparent.png) |
| Anomalies | Vector field | Small multiples | Operations |
| ![anomalies](out/anomalies-transparent.png) | ![vector field](out/vector-field-transparent.png) | ![small multiples](out/small-multiples-transparent.png) | ![operations](out/operations-transparent.png) |
