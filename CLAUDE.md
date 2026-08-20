# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Mission — read this before touching extraction logic

This is not a generic scraper. It is the data-collection engine for a **price intelligence practice**:
every design decision exists so that a pricing analyst can trust a row's branch attribution enough to
build a strategy on top of it. The engine currently tracks two Makro Perú branches (359 Santa Anita,
360 Surco), and that is deliberate — **Nivel 1: Correctness** comes before **Nivel 2: Coverage**
(`README.md` §2 and §6; `historia.md` tells the long version of how that principle was learned, through
two failed rewrites). Prove branch resolution is airtight on a small, well-understood set of nodes
before adding more.

Every change should be judged against this question: **does it make branch attribution more correct, or
does it just make the dataset bigger?** A bigger dataset with fuzzy attribution is worse than a small one
you can trust. Never add a branch faster than you can verify its signature is real (see "Scaling to 20+
branches").

## What this is

A single-purpose extractor: it pulls per-branch (sucursal) retail prices from Makro Perú's VTEX-powered
storefront (`www.makro.plazavea.com.pe`) using Playwright's async `APIRequestContext` to call VTEX's
public `simulation` / `orderForm` APIs directly (no page scraping, no purchases). There is no `src/`, no
test suite, no packaging — the whole engine is one file.

```
engines/mk_scraping_engine_0.1.0.py   the engine (~3,780 lines, VERSION 2026.08.13-13, SCHEMA_VERSION 3)
main.py                               ASCII launcher banner only — see caveat below
requirements.txt                      playwright==1.62.0 (Python 3.12)
README.md                             architectural spec of the engine
historia.md                           project narrative: v01 → v13 → 0.1.0, and why each version exists
salida/makro/                         all output (gitignored)
```

The project exists to build a time series of prices per SKU per branch so a pricing analyst can compare
branches later in SQL/pandas. **The extractor itself never compares branches and never drops rows** —
see "Guiding principles" before changing extraction logic.

**`main.py` caveat:** it defines `draw_banner()` but never calls it, and it imports `wcwidth`, which is
*not* in `requirements.txt`. It is not an entry point to the engine today. Don't assume `python3 main.py`
runs anything.

## Running it

```bash
python -m pip install -r requirements.txt
playwright install chromium          # the default --canal is "chrome"; falls back to managed Chromium

python3 engines/mk_scraping_engine_0.1.0.py --version                        # version + changelog, no network
python3 engines/mk_scraping_engine_0.1.0.py --catalogo 300 --por-categoria 3 --muestra 100   # normal test run
python3 engines/mk_scraping_engine_0.1.0.py --tope 60000                     # full catalog (slow, deliberate)
python3 engines/mk_scraping_engine_0.1.0.py --modo orderform                 # 3-request flow instead of simulation
python3 engines/mk_scraping_engine_0.1.0.py --reiniciar                      # delete CSVs, restart the series
python3 engines/mk_scraping_engine_0.1.0.py --salida /otra/ruta              # write elsewhere
```

Flags that matter: `--catalogo N` (stop discovery at N SKUs), `--muestra N` (how many discovered SKUs get
measured; deterministic hash selection), `--por-categoria N` (max SKUs per subcategory — makes the sample
*wide* instead of the first two categories of the tree), `--semilla` (salt of the selection hash),
`--presupuesto-descubrimiento N` (request budget reserved for discovery only), `--tope N` (hard request
cap per run; 0 = auto), `--intervalo` (seconds between requests, default 1.5), `--reintentos` (429/5xx
retries, default 3), `--auditoria PCT` (% of measurements cross-checked simulation vs orderForm, default
5, 0 disables), `--sin-evidencia` (skip archiving raw JSON), `--headed` / `--canal`.

**There is no lint/test/build tooling in this repo.** Verify a change by running the engine small
(`--catalogo 60 --por-categoria 2 --muestra 10 --auditoria 0`), then reading the console output, the
CSVs, and `salida/makro/ultima_corrida.json`. Check the process exit code — it is meaningful (see
"Budget exhaustion is a global failure").

## Versioning convention — read before editing

**Never edit the engine in place under the same identity, and never let two versions produce output under
the same name.** This isn't style — it's a documented incident: an old copy of the script (`... (1).py`
from a browser download) was run for hours and the dataset came out silently tagged with the wrong
taxonomy.

Three defenses exist and must stay intact when bumping versions:
1. The version lives in the **filename** so two versions can't collide on disk.
2. `VERSION` and the file's own name are printed in the run header (`archivo_script`).
3. Both are written into `salida/makro/ultima_corrida.json`, so every dataset traces back to the exact script.

When bumping: copy the file to a new name, update `VERSION` (`YYYY.MM.DD-NN`), and prepend a changelog
entry to `CAMBIOS` describing what changed. Bump `SCHEMA_VERSION` only if you add/remove/rename a `Fila`
field — it's written into every row so a CSV from six months ago can say what rules it was born under
(v13 deliberately kept it at `"3"` because no field changed, so v12 and v13 CSVs append together).
Do not delete older engine files — they document provenance for past CSV rows.

## Guiding principle: identify the branch, don't assume it

`Producto + precio + disponibilidad + firma logística + nodo` — never `Producto + precio -> guess the branch`.

The branch is never inferred from the postal code that was *sent*. It is *identified* by reading VTEX's
returned logistics signature (`warehouseId` + `dockId` + `courierId` + `courierName`) and matching it
against the known signatures in `NODOS` (`identificar_nodo`). If VTEX resolves a different node, the row
says so via `node_resolved` / `logistics_status` (e.g. `MISMATCH_RESOLVED_360`) instead of silently
mislabeling data. `polygonName` is excluded from the match (`Nodo.firma_core()`) because VTEX versions it
(`_V2`, `-V2`) without meaning the branch changed — drift is recorded separately as `polygon_drift`.

`identificar_nodo` loops over every entry in `NODOS` and returns whichever signature matches — a *search*,
not a lookup keyed by what you sent. That's what makes 2 → 20 branches free: the loop, the CSV-per-branch
output, and the manifest's per-branch tallies all iterate `NODOS`. To add a branch: add one entry (address,
coordinates, the four logistics identifiers, `seller_chain`, `archivo`). Nothing else changes — but read
"Scaling to 20+ branches" before trusting it.

## Guiding principle: finding the warehouse is not confirming the dispatch

VTEX can offer several SLAs for the same item. That one comes from the expected warehouse does **not** mean
it's the one the customer would receive — `selectedSla` says that. Through v08 the script took the first
SLA whose warehouse matched and called it `MATCH`, which over-interpreted the evidence.

`fulfillment_confirmed` (`SI`/`NO`) is therefore separate from finding the right warehouse:
- explicit VTEX selection, or the single unambiguous SLA when only one exists → confirmed
- multiple SLAs offered and VTEX didn't pick → **not** confirmed, even if the warehouse matches

When the warehouse matches but dispatch isn't confirmed: `logistics_status = MATCH_SIN_CONFIRMAR` and
`price_status = QUALIFIED` — neither `VERIFIED` nor `UNVERIFIED`. Don't collapse this into `MATCH` for
convenience; an ambiguous case must stay ambiguous in the data.

## Guiding principle: price is never discarded

If VTEX returned a price, it is written to the CSV — even if logistics validation failed. Three columns,
deliberately separate:

- `price` — the **fact** (what VTEX answered)
- `price_status` — the **judgment** (`VERIFIED` / `VERIFIED_SELLER_RAIZ` / `QUALIFIED` / `UNVERIFIED` / `NO_PRICE`)
- `logistics_status` — the **why** (`MATCH`, `MATCH_SELLER_RAIZ`, `MATCH_SIN_CONFIRMAR`, `SIN_STOCK`,
  `NO_COVERAGE`, `OPERADOR_EXTERNO`, `MISMATCH_RESOLVED_*`, `HTTP_ERROR`, `EXCEPTION`, ...)

A row with `price_status=UNVERIFIED` is real signal (that SKU has no coverage from that branch), not noise
to filter at extraction time. `medir()` and `construir_fila()` never raise on a per-SKU problem — a failed
measurement becomes a `Fila` with an `error` field, so one bad SKU can't kill a run. Do not "clean this up"
with early returns that skip writing a row.

## Guiding principle: budget exhaustion is a global failure, not a bad SKU (v12/v13)

This is the newest and most easily broken rule. `TopeAgotadoError` (with `TopeAgotadoAlEntrarError` /
`TopeAgotadoEnReintentoError`) is a **run-level** condition, not a SKU-level one. It must never be caught
by the per-SKU `except` in `medir()` and turned into an `EXCEPTION`/`NO_PRICE` row — that disguised a dead
run as a run full of unavailable products. When it fires, measurement stops, what was already measured is
kept, and the *pending* measurements are listed in `manifiesto.medicion` (no invented rows).

The consequences are encoded, not narrated:
- `evaluar_corrida()` decides `corrida_completa`, `motivos_fallo_global`, and the **process exit code**.
- A *deliberate* discovery limit (`--catalogo`, `--por-categoria`, `--presupuesto-descubrimiento`) is not a
  failure — exit **0**, with `descubrimiento.clasificacion` saying the catalog was partial on purpose
  (`LIMITE_POR_CATEGORIA`, `LIMITE_PRESUPUESTO_DESCUBRIMIENTO`).
- Measurement or chain-stock cut short by budget → `medicion.completa=false` / `stock_cadena.completo=false`
  → exit **1**. The run delivered less than the selection promised, and nobody asked for that.
- Missing Playwright → exit **2**; Ctrl-C → exit **130**.

Also from v13: a per-node early alarm fires if a node accumulates 5+ measurements with no
`MATCH`/`MATCH_SELLER_RAIZ`/`MATCH_SIN_CONFIRMAR`, so a badly loaded `NODOS` signature can't burn a whole
run producing only `OPERADOR_EXTERNO` in silence.

## Guiding principle: no input files — the catalog is discovered every run (v11)

The engine reads **nothing** to decide what to measure. `panel.json` (a frozen basket on disk, v01–v10) is
gone. It solved a real problem — random sampling means two runs share no SKUs and there's no time series —
but it was a crutch of the *sampling*, and it brought its own bug: chain stock came from that old photo and
looked live (0 of 100 SKUs changed between runs). Any field read from the panel is the past disguised as
the present.

Reproducibility now comes from a deterministic hash instead of a file (`seleccionar`):

```
orden = md5(f"{semilla}:{sku_id}")   ->  take the first N
```

Same seed + same catalog = same sample, nothing persisted; and new Makro products enter the draw on their
own. **Every file the engine writes is output. None is read back to decide what to measure.** Don't
reintroduce an input file to "stabilize" the sample.

## Guiding principle: two kinds of stock answer two different questions

`availability` comes from checkout **with the branch's address** ("can this store ship it today?").
`chain_stock` comes from the catalog **without branch context** ("how much is left anywhere in the chain?").
They are refreshed independently (`refrescar_stock_cadena`, a handful of batched `fq=productId:` requests,
not one per SKU) and are not supposed to agree.

`calcular_stock_signal` crosses them into one actionable label:
- `DISPONIBLE` — the branch has it
- `QUIEBRE_LOCAL` — branch is out, chain has it — **the row that's worth money**: real demand, visible
  stockout, competitor-comparable
- `QUIEBRE_CADENA` — out everywhere
- `QUIEBRE_LOCAL_CADENA_DESCONOCIDA` — branch is out, chain status unknown

## Guiding principle: evidence is cheap insurance, verify instead of trusting

Three separate parser bugs (`all_headers` vs `headers`, a destructive fallback, a `sellerChain` that wrongly
discarded valid nodes) each cost re-scraped history. Two defenses now exist:

- **Raw evidence** (`guardar_evidencia`) — every response is archived, compressed, to
  `salida/makro/raw/<date>/<run_id>.jsonl.gz` (headers/cookies excluded on purpose — session tokens never
  touch disk). A parser fix can be replayed against the archive with zero new requests.
- **Sampled reconciliation** (`--auditoria`, default 5%) — that share of measurements is fetched *both* ways
  and diffed (`reconciliar`) on price, availability, warehouse and seller chain. `simulation` is trusted by
  default because it's 1 request instead of 3; this is what keeps verifying that trust without paying 3x.

Disable them (`--sin-evidencia`, `--auditoria 0`) only for throwaway test runs.

## Guiding principle: a schema change must never silently corrupt history

`COLUMNAS = list(Fila().__dict__.keys())`, so adding a field auto-adds a CSV column. In append mode that's
dangerous: an old header would silently receive wider rows, misaligning every downstream read.
`archivar_si_cambio_el_esquema` compares the on-disk header against `COLUMNAS` before every run and, if they
differ, renames the old file with a cutoff timestamp instead of appending under a mismatched header — nothing
is lost, both eras stay independently readable. Don't hand-edit a CSV header to make the mismatch go away.

## Architecture (`engines/mk_scraping_engine_0.1.0.py`, single file, top to bottom)

1. **Version block + `CAMBIOS`** — `VERSION`, `SCHEMA_VERSION`, and the per-version changelog described above.
2. **Constants** — `MOTOR = "makro"` names the output subfolder (`salida/makro/`) so future engines
   (Tottus, Sodimac) can't collide; `RETAILER` travels *inside* every row.
3. **`Nodo` / `NODOS`** — branch signature catalog; `firma_core()` and `direccion()`.
4. **`Producto` / `Fila`** — `Producto` is a catalog entry; `Fila` is one measurement (SKU × branch ×
   moment) and its fields are the CSV schema. Every row carries `run_id`, `schema_version`, `retailer`, and
   `dq_flags`.
5. **`Cliente`** — polite wrapper over Playwright's async `APIRequestContext` (*not* `page`): minimum
   interval between requests, exponential backoff on 429/5xx, honors `Retry-After`, hard request cap
   enforced **per attempt** (v12), request counters broken down by phase. Note the comment about
   `APIResponse.headers` being a *property*, not a coroutine. Bugs in `ERRORES_DE_CODIGO`
   (`AttributeError`, `TypeError`, ...) are deliberately **not** retried.
6. **Discovery** (`total_desde_resources`, `aplanar_categorias`, `parsear_producto`, `descubrir_catalogo`,
   `es_basura`, `es_landing_seo`) — walks the VTEX category tree building each category's full path,
   paginates by page length when the `resources` header is unparseable (v12), and caps SEO landing pages
   (lowercase names like "absolut vodka") at 1 SKU so they don't flood the sample with one product's variants.
7. **`seleccionar`** — deterministic hash sampling described above; persists nothing.
8. **Evidence & audit** (`guardar_evidencia`, `reconciliar`) and **chain stock** (`refrescar_stock_cadena`,
   `calcular_stock_signal`).
9. **Response parsing** (`extraer_logistica`, `evaluar_calidad`, `extraer_item`, `extraer_direccion`,
   `clasificar_fulfillment`, `identificar_nodo`) — `clasificar_fulfillment` distinguishes *who actually
   ships* (`tienda`, `tienda_raiz`, `proveedor`/dropship, `generico_pv`, `desconocido`), since the storefront
   also sells non-Makro inventory. `evaluar_calidad` runs three targeted rules (`DQ_SKU_DISTINTO`,
   `DQ_PRECIO_MAYOR`, `DQ_PRECIO_CERO`) that have each caught a real bad row — kept small on purpose.
10. **Measurement** (`consultar_simulation`, `consultar_orderform`, `_orderform_con_cliente`,
    `construir_fila`, `medir`) — `simulation` is 1 stateless request; `orderform` is the 3-request flow and
    the only one returning a backend-resolved address, so since v10 it gets **its own browser context** (the
    cart is state and was contaminating measurements). Default mode falls back to orderform per-SKU only when
    simulation returned no logistics *and* availability wasn't already a definitive
    `cannotBeDelivered`/`withoutStock`, and never overwrites a simulation price with an empty orderform result.
11. **Output** (`archivar_si_cambio_el_esquema`, `apendear_csv`, `evaluar_corrida`, `escribir_manifiesto`) —
    schema guard first; CSVs are **appended**, never overwritten (that's the time-series property);
    `--reiniciar` is the explicit opt-in to wipe. The manifest records script/schema version, run ID,
    requests per phase, per-branch tallies, `descubrimiento` / `medicion` / `stock_cadena` completeness
    blocks, and `resumen.corrida_completa` + `motivos_fallo_global`.
12. **`main()`** — CLI parsing, browser lifecycle, per-run global state reset (`MEDICION`,
    `STOCK_CADENA_ESTADO`, `ALARMA_FIRMA_DISPARADA` — they're module globals so `escribir_manifiesto` can
    read them; keep them cleared per run so a second call in one process starts clean), a warning if pre-v11
    CSVs are still sitting in the old flat `salida/` (it never moves them), auto request-cap sizing, and the
    final `evaluar_corrida` verdict + exit code.

## Scaling to 20+ branches

More branches, same architecture — most machinery already scales because it iterates `NODOS`:

- **Free**: `identificar_nodo`'s search loop, the measurement loop, one CSV per branch (`nodo.archivo`), the
  manifest's per-branch breakdown, and request-cap auto-sizing.
- **Grows linearly, needs planning**: requests per run (SKUs × branches, ~×4 worst case) and wall-clock time
  (requests are strictly sequential at `--intervalo`, by design — "respeto al servidor" in the module
  docstring). 2 → 20 branches is roughly a 10x run. Size `--muestra` and `--intervalo` accordingly instead of
  raising `--tope` blindly.
- **The real risk when onboarding a branch**: a wrong or incomplete `NODOS` entry doesn't error — VTEX simply
  never returns that signature, every row becomes `node_resolved="OTHER"` / `OPERADOR_EXTERNO`, and the branch
  looks "covered" in row counts while contributing zero verified prices. The v13 per-node alarm now warns
  during the run, but still: **before trusting a new branch, run a small `--muestra` against just that node and
  confirm rows come back `MATCH` / `MATCH_SELLER_RAIZ`.**
- **Out of scope here**: multi-retailer adapters and a retailer-agnostic domain model (README §6, Nivel 3+).
  Don't start building those abstractions speculatively — the `MOTOR`/`salida/<motor>/` split is the only
  concession the engine makes to that future.

## Output files (`salida/makro/`)

- `makro_359_santa_anita.csv`, `makro_360_surco.csv`, ... one per `Nodo.archivo` — one row per (SKU, branch,
  run); same schema; append-only. A new `NODOS` entry gets its own file automatically.
- `ultima_corrida.json` — health/audit record of the most recent run (per-branch tallies, sampling stats,
  completeness contracts, contract-drift alarm, exit verdict).
- `raw/<date>/<run_id>.jsonl.gz` — compressed raw VTEX responses, for replaying parser fixes without
  re-hitting the server.

The analyst does the cross-branch comparison downstream (SQL/pandas `UNION` across every branch's CSV) — do
not add cross-branch comparison logic to this engine, at 2 branches or at 20.

## Notes for future sessions

- `README.md` (spec) and `historia.md` (narrative) are the authoritative context documents in-repo. Older
  guidance referenced an `arquitectura_motor_price_intelligence_v8.md` roadmap; that file is **not** in this
  repo — don't cite section numbers from it.
- `CLAUDE.md`, `salida/`, `.env`, and generated CSV/JSONL artifacts are gitignored, so edits to this file are
  not protected by git history.
- `graphify-out/` holds a generated knowledge graph of this repo (`graph.html`, `GRAPH_REPORT.md`) — useful
  for orientation, but the engine file itself is always the source of truth.

- `docs/decisiones_1.1.0.md` — closed inventory of what ships in 1.1.0:
  verified bi-price mechanism, 25 new columns, known bugs, acceptance
  criterion and scope. Consult before proposing changes.