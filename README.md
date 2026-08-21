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
| `makro_pe` | Makro Perú, other source | planned, explicitly out of scope for 1.1.0 |

Version 1.0.0. Work on 1.1.0 (wholesale / bi-price) is in progress and changes the output
layout — see the pointers below before building anything on top of the current CSVs.

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

## Where things are documented

- `CLAUDE.md` — guiding principles, architecture, and how to run and verify a change. Read it
  before touching extraction logic.
- `docs/decisiones_1.1.0.md` — closed inventory of what ships in 1.1.0: new columns, output
  layout, known bugs, acceptance criterion.
- `CHANGELOG.md` — release history.

## License

Private. Not licensed for redistribution.
