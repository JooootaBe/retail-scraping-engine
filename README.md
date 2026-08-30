# retail-engine

Price intelligence data collection for Peruvian retail. It queries a retailer's public
storefront APIs and writes one row per (SKU, branch, run), so a pricing analyst can build a
time series and compare branches downstream.

The point is **branch attribution you can trust**: every row records which branch actually
answered, and how sure the engine is about it. Correctness before coverage.

## Status

| Collector | Retailer | State |
|---|---|---|
| `makro_plazavea` | Makro Perú (VTEX storefront) | working — 2 branches: 359 Santa Anita, 360 Surco |
| `makro_pe` | Makro Perú, other source | not started, and deliberately so |

Package version **1.2.0**; engine version `2026.08.28-24`, `SCHEMA_VERSION 7`, 79 columns per row.
1.1.0 shipped the wholesale (bi-price) columns and the one-folder-per-run layout; 1.2.0 added
`--categoria`. Seven engine versions since — v18 through v24 — are **released in git but not tagged
as a package release**; three of them move `SCHEMA_VERSION` (4 → 5 in v18, 5 → 6 in v20, 6 → 7 in
v24), so read
`CHANGELOG.md`'s `[Sin publicar]` section before consolidating runs from different dates.

The two branches are a decision, not a limitation: branch attribution is proven correct on a small
set of nodes before more are added. `CLAUDE.md` explains why, and what it costs to add the 21st.

## Install

```bash
python -m pip install -e .     # Python >=3.12, pulls playwright>=1.62.0
playwright install chromium
```

## Run

```bash
python3 src/retail_engine/collectors/makro_plazavea.py --catalogo 300 --por-categoria 3 --muestra 100
```

That discovers up to 300 SKUs, caps each subcategory at 3, and measures 100 of them against
every configured branch. `--version` prints the engine version and its changelog without
touching the network. The full flag list is in `CLAUDE.md`.

Output lands in `data/makro_plazavea/run_<YYYYMMDD_HHMMSS>/` — one immutable folder per run,
holding `filas.csv`, `run.json` and `raw.jsonl.gz`. Reading the whole history is a glob over
`run_*/filas.csv`. The process exit code is meaningful: 0 complete, 1 the run delivered less than
its selection promised, 2 bad arguments or missing Playwright, 130 Ctrl-C.

## Tests

64 `test_` functions across six files under `tests/makro_plazavea/`, none of which touch the
network. Pytest is not a declared dependency; each file also runs standalone and exits 0/1:

```bash
python3 tests/makro_plazavea/test_precio_mayorista.py
python -m pytest tests/ -q                              # if pytest is installed
```

Each file resolves the repo root as `parents[2]` from its own path, so the depth
`tests/<colector>/<archivo>.py` is load-bearing: moved one level deeper the suite dies with
`ModuleNotFoundError`. The five exploratory probes that found the bi-price are archived under
`tests/historia/sondas_makro_plazavea/` and do not run — a probe is not regression, and the two are
not interchangeable.

## Layout

```
src/retail_engine/collectors/makro_plazavea.py   the engine
ops/                                             operational tools; they never measure prices
docs/columnas.md                                 live: the dictionary of the 79 columns
docs/historia/                                   archived: the closed 1.1.0-era record
docs/bodegueros/                                 the new direction (Los Bodegueros)
tests/makro_plazavea/                            the regression suite — six files, all live
tests/fixtures/makro_plazavea/                   golden baseline + 23 hand-captured storefront cards
tests/historia/sondas_makro_plazavea/            archived: five exploratory probes, they do not run
tests/historia/regresion_makro_plazavea/         archived: one regression test whose run was deleted
data/                                            generated output (gitignored)
```

## Where things are documented

- `CLAUDE.md` — guiding principles, architecture, and how to run and verify a change. Read it
  before touching extraction logic. It is the only document kept current by design.
- `CHANGELOG.md` — release history, plus `[Sin publicar]` for v18–v24.
- `docs/columnas.md` — the column dictionary: what each of the 79 `filas.csv` columns says, why it
  exists, what its empty cell means, and which ones changed meaning between schema versions.
- `docs/historia/` — archived record of the 1.1.0 era. Nothing there is a roadmap; it is kept
  because it holds the evidence that forced each change. See `docs/historia/LEEME.md`.
  - `decisiones_1.1.0.md` — closed inventory of what 1.1.0 shipped. Parts of it were superseded by
    later versions and are annotated in place. Still cited from `CLAUDE.md` and `docs/columnas.md`.
  - `brief_*.md` — three closed work orders.
  - `contradicciones.md` — the documentation audit run before 1.1.0, with its resolutions.
- `docs/bodegueros/` — the new direction. See `docs/bodegueros/README.md`.

## License

Private. Not licensed for redistribution.
