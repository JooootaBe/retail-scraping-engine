# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Mission — read this before touching extraction logic

This is not a generic scraper. It is the data-collection engine for a **price intelligence practice**:
every design decision exists so that a pricing analyst can trust a row's branch attribution enough to
build a strategy on top of it. The engine currently tracks two Makro Perú branches (359 Santa Anita,
360 Surco), and that is deliberate — **Nivel 1: Correctness** comes before **Nivel 2: Coverage**.
That principle was learned through two failed rewrites of this engine; this file is now the only place
that record survives. Prove branch resolution is airtight on a small, well-understood set of nodes
before adding more.

Every change should be judged against this question: **does it make branch attribution more correct, or
does it just make the dataset bigger?** A bigger dataset with fuzzy attribution is worse than a small one
you can trust. Never add a branch faster than you can verify its signature is real (see "Scaling to 20+
branches").

## What this is

A single-purpose extractor: it pulls per-branch (sucursal) retail prices from Makro Perú's VTEX-powered
storefront (`www.makro.plazavea.com.pe`) using Playwright's async `APIRequestContext` to call VTEX's
public `simulation` / `orderForm` APIs directly (no page scraping, no purchases). The extraction logic is
still **one file**: a single module inside an installable package, with no automated test suite around it.

```
src/retail_engine/collectors/makro_plazavea.py   the engine (6,069 lines, VERSION 2026.08.22-17, SCHEMA_VERSION 4)
pyproject.toml                                   package `retail-engine` 1.2.0, Python >=3.12, playwright>=1.62.0
docs/decisiones_1.1.0.md                         closed inventory of what 1.1.0 shipped
CHANGELOG.md                                     released and unreleased changes
tests/probes/makro_plazavea/                     five exploratory probes (v1…v5) — scripts, not a test suite
tests/fixtures/makro_plazavea/golden_v5.csv      baseline produced by probe v5, with its own README
ops/                                             operational tools — NOT the engine; they never measure prices
ops/arbol_categorias.py                          snapshots the category tree and diffs it against the last one
data/                                            generated output (gitignored, `.gitignore:32`)
```

`ops/` is a deliberate boundary, not a folder for leftovers. Anything there runs by hand or by cron, imports
from the engine and never modifies it, and answers a question *about* the catalog rather than extracting from
it. `arbol_categorias.py` is the first: it exists because a new category is a commercial signal — someone on
the other side decided to start selling something — and until now it entered the traversal in silence.

The project exists to build a time series of prices per SKU per branch so a pricing analyst can compare
branches later in SQL/pandas. **The extractor itself never compares branches and never drops rows** —
see "Guiding principles" before changing extraction logic.

## Running it

```bash
python -m pip install -e .
playwright install chromium          # the default --canal is "chrome"; falls back to managed Chromium

MK=src/retail_engine/collectors/makro_plazavea.py

python3 $MK --version                                        # version + changelog, no network
python3 $MK --catalogo 300 --por-categoria 3 --muestra 100   # normal test run
python3 $MK --tope 60000                                     # full catalog (slow, deliberate)
python3 $MK --modo orderform                                 # 3-request flow instead of simulation
python3 $MK --categoria "/399/"                              # only that branch of the tree
python3 $MK --skus 10012716,11401644                         # measure an explicit list, no discovery
python3 $MK --dry-run --skus 10012716                        # measure, print, write nothing
python3 $MK --salida /otra/ruta                              # write elsewhere
```

Flags that matter: `--catalogo N` (stop discovery at N SKUs), `--muestra N` (how many discovered SKUs get
measured; deterministic hash selection), `--por-categoria N` (max SKUs per subcategory — makes the sample
*wide* instead of the first two categories of the tree), `--semilla` (salt of the selection hash),
`--presupuesto-descubrimiento N` (request budget reserved for discovery only), `--tope N` (hard request
cap per run; 0 = auto), `--intervalo` (seconds between requests, default 1.5), `--reintentos` (429/5xx
retries, default 3), `--auditoria PCT` (% of measurements cross-checked simulation vs orderForm, default
5, 0 disables), `--sin-evidencia` (skip archiving raw JSON), `--headed` / `--canal`.

1.2.0 added `--categoria "/399/,/77/"` — see "Guiding principle: scope is chosen, not inherited from
the tree" below; it is the flag with the most reasoning behind it.

Three flags came with 1.1.0. `--skus <lista>` (max 20) measures an explicit list and skips discovery —
it is incompatible with `--catalogo` / `--muestra` on purpose, because a run must have exactly one
answer to "how was this selected"; the manifest records `modo_seleccion` so these runs can be filtered
out of the series later. `--dry-run` measures and prints without writing anything, not even the run
folder. `--auditoria-mayorista N` (default 3, 0 disables) is the only place the engine asks for
`qty > 1` — see "Guiding principle: the wholesale price is reconstructed, not observed".

**`--reiniciar` no longer exists.** It deleted the per-branch CSVs to "restart the series", which made
sense when the series was one mutable file being appended to. With one immutable folder per run there is
no accumulated file to reset, and the only thing the flag could still delete is closed history. A flag
whose only possible effect is destroying the past does not get redefined — it gets removed.

**There is no lint/test/build tooling wired up in this repo** — `tests/` holds probes and a fixture, not a
runner. Verify a change by running the engine small (`--catalogo 60 --por-categoria 2 --muestra 10
--auditoria 0`), then reading the console output, `filas.csv` and `run.json` inside the run's own folder
under `data/makro_plazavea/`. Check the process exit code — it is meaningful (see "Budget exhaustion is a
global failure").

For a change that touches extraction rules, the sharper test is the acceptance criterion of
`docs/decisiones_1.1.0.md` §10: re-measure the 20 SKUs of `golden_v5.csv` with `--skus` and diff against
the fixture, applying that section's three exclusion groups. It closed at 40/40 on 2026-08-21. A pure
function can be tested without the network at all — that is why the rules live in pure functions.

## Versioning convention — read before editing

**Never edit the engine in place under the same identity, and never let two versions produce output under
the same name.** This isn't style — it's a documented incident: an old copy of the script (`... (1).py`
from a browser download) was run for hours and the dataset came out silently tagged with the wrong
taxonomy.

Three defenses exist and must stay intact when bumping versions:
1. The version lives in the **filename** so two versions can't collide on disk. *(No longer literally true
   since the move to `src/`: the collector has one stable path, `makro_plazavea.py`, so defenses 2 and 3
   carry the whole load and must not be weakened.)*
2. `VERSION` and the file's own name are printed in the run header (`archivo_script`).
3. Both are written into the run manifest (`run.json`, inside the run's own folder), so every dataset
   traces back to the exact script.

When bumping: update `VERSION` (`YYYY.MM.DD-NN`) and prepend a changelog
entry to `CAMBIOS` describing what changed. Bump `SCHEMA_VERSION` only if you add/remove/rename a `Fila`
field — it's written into every row so a CSV from six months ago can say what rules it was born under.
v13 and v14 deliberately kept it at `"3"` because no field changed; v15 raised it to `"4"` when the 22
wholesale columns landed (56 → 78). Since each run now writes its own file, a schema change no longer
risks corrupting an existing one — but `schema_version` is still what tells a consolidation layer which
runs it may safely `UNION`.
If an older engine file is ever kept next to the current collector, do not delete it — it documents
provenance for past CSV rows. (The tree holds only `makro_plazavea.py` today.)

## Guiding principle: identify the branch, don't assume it

`Producto + precio + disponibilidad + firma logística + nodo` — never `Producto + precio -> guess the branch`.

The branch is never inferred from the postal code that was *sent*. It is *identified* by reading VTEX's
returned logistics signature (`warehouseId` + `dockId` + `courierId` + `courierName`) and matching it
against the known signatures in `NODOS` (`identificar_nodo`). If VTEX resolves a different node, the row
says so via `node_resolved` / `logistics_status` (e.g. `MISMATCH_RESOLVED_360`) instead of silently
mislabeling data. `polygonName` is excluded from the match (`Nodo.firma_core()`) because VTEX versions it
(`_V2`, `-V2`) without meaning the branch changed — drift is recorded separately as `polygon_drift`.

`identificar_nodo` loops over every entry in `NODOS` and returns whichever signature matches — a *search*,
not a lookup keyed by what you sent. That's what makes 2 → 20 branches free: the loop, the measurement
loop, and the manifest's per-branch tallies all iterate `NODOS`. Since 1.1.0 the output is one long CSV
keyed `(run_id, node_id, sku_id)`, so a new branch adds *values to a column* rather than a file
(`docs/decisiones_1.1.0.md` §8) — cheaper still. To add a branch: add one entry (address, coordinates,
the four logistics identifiers, `seller_chain`, `archivo`). Nothing else changes — but read "Scaling to
20+ branches" before trusting it.

`Nodo.archivo` is the one leftover: it named that branch's CSV in 1.0.0 and the engine no longer reads it.
It stays because it is how a `makro_359_santa_anita.csv` from the old history can still be traced to its
node.

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

## Guiding principle: scope is chosen, not inherited from the tree (v17)

The category tree has **3,401 nodes**, and `descubrir_catalogo` walks them in a fixed order —
by route — stopping when `--catalogo N` is reached. The fixed order is deliberate and must stay:
it is what makes discovery reproducible without persisting anything.

But it had a consequence nobody chose. A bounded run always measured the *first* categories of the
tree — Packs Limpieza, Packs Desayunos, Packs Vinos — and abarrotes could never come up at all.
So `--catalogo 300` never meant "300 SKUs of the catalog"; it meant "the first 300 that happen to
appear", a sample biased by an accidental property of how VTEX sorts its own tree. The scope of
the series was being decided by the tree, not by the analyst. That is what `--categoria` fixes.

`--categoria "/399/,/77/"` restricts the universe before anything is walked. Three things about it
are load-bearing:

- **The filter runs before the traversal, not after.** This is not an optimisation, it is whether
  the flag works at all: discarding a category *after* paginating it costs one request per category
  thrown away — 3,300 categories at `--intervalo 1.5` is ~83 minutes spent to produce nothing.
  Measured on `/399/` (Limpieza): 47 categories walked, **3,353 skipped without a single request**.
- **Prefix match by segment, never `startswith` on the string.** Asking for `/39/` must not drag in
  `399`, which is a different branch entirely. Matching whole segments is also what makes asking for
  a parent include its children, which is the useful behaviour.
- **A route that isn't in the tree is an argument error (exit 2), not a warning.** A run that
  measures zero categories because someone typed `/3999/` would otherwise finish with exit 0 and an
  empty CSV — indistinguishable from a branch that genuinely ran out of stock. All bad routes are
  reported together, so someone who passed four and mistyped two doesn't discover it one at a time.
  The check needs the live tree, so it happens inside discovery rather than in `parsear_argumentos`.

`--categoria` is incompatible with `--skus`: that flag already names exactly what to measure, so
accepting a filter that changes nothing would imply one was applied. It composes freely with
`--catalogo` and `--por-categoria`, which keep operating *inside* the restricted universe.

### What `completo` means once scope is restricted

This is the part that can quietly corrupt a conclusion. A filtered run that walks everything it was
asked to walk is complete — but complete *with respect to the request*, not with respect to Makro's
catalog. Reading one as the other would let a series built on a single branch be reported later as
category coverage.

So the manifest separates the two:

- `descubrimiento.alcance` — `CATALOGO_COMPLETO` or `CATEGORIAS_SELECCIONADAS`.
- `descubrimiento.clasificacion` — `COMPLETO` only for an unfiltered full walk;
  **`COMPLETO_EN_CATEGORIAS`** when the requested branches were exhausted. Same nothing-is-missing
  guarantee, different universe, and the name says which.
- `categoria_filtro`, `categorias_seleccionadas`, `categorias_a_recorrer` — without these the scope
  of an old run can't be reconstructed: two runs with the same SKU count may have looked at
  different branches.

`--por-categoria` keeps its older, stronger contract on top of all this: with it set, discovery can
**never** be marked complete, filtered or not. That flag's promise is "wide sample", and no amount
of scope restriction turns a sample into a census.

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
  `raw.jsonl.gz` inside the run's own folder, next to the rows it explains (headers/cookies excluded on
  purpose — session tokens never touch disk). A parser fix can be replayed against the archive with zero new requests.
- **Sampled reconciliation** (`--auditoria`, default 5%) — that share of measurements is fetched *both* ways
  and diffed (`reconciliar`) on price, availability, warehouse and seller chain. `simulation` is trusted by
  default because it's 1 request instead of 3; this is what keeps verifying that trust without paying 3x.

Disable them (`--sin-evidencia`, `--auditoria 0`) only for throwaway test runs.

## Guiding principle: a schema change must never silently corrupt history

`COLUMNAS = list(Fila().__dict__.keys())`, so adding a field auto-adds a CSV column. Through 1.0.0 that was
dangerous, because the CSV was *appended* to: an old header silently received wider rows and misaligned every
downstream read. A guard (`archivar_si_cambio_el_esquema`) renamed the old file before that could happen.

1.1.0 removed the guard, and it is worth knowing why so nobody misses it. With one immutable folder per run,
every run writes its own `filas.csv` with its own header and never reopens it — two schemas can no longer
share a file, so there is nothing left to misalign. What the guard protected is now carried by
`schema_version`, written into every row.

The risk did not disappear, it moved: a glob across `run_*/filas.csv` can still pull together runs with
different headers. That is the consolidation layer's job, and `schema_version` is what it decides with.
Don't hand-edit a header to make a mismatch go away.

## Guiding principle: the wholesale price is reconstructed, not observed

1.1.0's reason for existing is the bi-precio: Makro discounts a SKU past a declared threshold
(`CantidadBiPrecioMK`), and that is the number a pricing analyst actually negotiates against. The mechanism,
the formula and the 22 columns are specified in `docs/decisiones_1.1.0.md` §1 and §5 — read there, don't
restate them here.

What belongs here is the epistemics, because it is easy to get wrong twice:

- The **threshold** exists only in the catalog response; the **discount** appears in both catalog and
  `simulation`. Neither source is sufficient alone, so `Producto` carries the catalog-only fields from
  discovery into measurement. That is *not* a return of the `panel.json` input file — nothing is read from
  disk; it is one run's catalog answer travelling in memory to that same run's measurement.
- The engine measures at `qty=1`, and at `qty=1` the discount is not applied. So `precio_mayorista` is
  **reconstructed by subtraction**, not observed. `--auditoria-mayorista` is the only thing that observes it:
  it re-measures a few rows at `qty = bi_umbral` and compares. Rows it touched say
  `precio_mayorista_verificado = SI`; every other row says `NO`, and `NO` is the honest default, not a gap.
- When the measured price disagrees with the reconstructed one, the **measured** value wins (it is what the
  customer pays), the row is flagged `DQ_MAYORISTA_DISCREPA`, and `descuento_monto` is left untouched —
  the disagreement between what VTEX *declared* and what it *charges* is the finding, so erasing either side
  would erase it. This has not happened yet: the formula is exact at thresholds 2, 3, 4, 12, 15 and 24
  (§11), the full range observed in this catalog. That is not the same as every possible threshold.
- The audit samples for **threshold coverage**, not representativeness — one low, the highest available,
  then unseen thresholds. Three audits of the same threshold prove the formula for that threshold only.

## Architecture (`src/retail_engine/collectors/makro_plazavea.py`, single file, top to bottom)

1. **Version block + `CAMBIOS`** — `VERSION`, `SCHEMA_VERSION`, and the per-version changelog described above.
2. **Constants + output paths** — `MOTOR = "makro_plazavea"` names the output namespace so future
   collectors can't collide. The collector is named by **source**, not by retailer: `makro_pe` would be
   the same retailer through a different storefront and must not share a folder. `RETAILER` travels
   *inside* every row. `raiz_repo()` finds the repo by marker (`pyproject.toml`) instead of counting
   parents — before 1.1.0 the output root resolved *inside* the installable package. `carpeta_corrida()`
   is the single function that builds output paths; if you find a `SALIDA / "algo"` anywhere else, it's
   a bug.
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
   The full path `aplanar_categorias` already builds is what makes `--categoria` a prefix match rather than
   a tree re-walk: `filtrar_categorias`, `ruta_bajo` and `normalizar_ruta_categoria` are pure and testable
   without the network, and the filter is applied to that flat list *before* the loop (see "scope is chosen,
   not inherited from the tree").
7. **Selection** — `seleccionar` is the deterministic hash sampling described above; persists nothing.
   `descubrir_por_skus` is the `--skus` path: it resolves an explicit list with batched `fq=skuId:`
   (20 SKUs in 2 requests) and returns three lists, because "asked and the catalog said no"
   (`SKU_NO_ENCONTRADO`, gets a row) is not "never asked, the budget ran out" (gets no row).
8. **Evidence & audit** (`guardar_evidencia`, `reconciliar`) and **chain stock** (`refrescar_stock_cadena`,
   `calcular_stock_signal`).
9. **Response parsing** (`extraer_logistica`, `evaluar_calidad`, `extraer_item`, `extraer_direccion`,
   `clasificar_fulfillment`, `identificar_nodo`) — `clasificar_fulfillment` distinguishes *who actually
   ships* (`tienda`, `tienda_raiz`, `proveedor`/dropship, `generico_pv`, `operador_externo`,
   `desconocido`), since the storefront also sells non-Makro inventory. `operador_externo` arrived in
   1.1.0 for a real case (`STK917NF` / `DCK-NF-MK-917` / `DD-NF-CD-917-URBANO`): the origin was fully
   identified and simply wasn't Makro. Calling that `desconocido` invited treating a firm fact about
   assortment as missing data, so `desconocido` is now reserved for *actually missing* logistics.
   `evaluar_calidad` runs four targeted rules (`DQ_SKU_DISTINTO`, `DQ_PRECIO_MAYOR`, `DQ_PRECIO_CERO`,
   `DQ_UNIDAD_INCONSISTENTE`) that have each caught a real bad row — kept small on purpose. A fifth,
   `DQ_MAYORISTA_DISCREPA`, is set by the wholesale audit rather than by `evaluar_calidad`.

   Also here, all pure: `calcular_mayorista`, `clasificar_ean`, `resolver_presentacion` and the teaser
   readers (`leer_regimen`, `leer_descuento`). They take JSON or strings and return values — no network,
   no `Fila` — so they can be tested directly, and `enriquecer_fila` is deliberately thin glue over them.
10. **Measurement** (`consultar_simulation`, `consultar_orderform`, `_orderform_con_cliente`,
    `construir_fila`, `medir`) — `simulation` is 1 stateless request; `orderform` is the 3-request flow and
    the only one returning a backend-resolved address, so since v10 it gets **its own browser context** (the
    cart is state and was contaminating measurements). Default mode falls back to orderform per-SKU only when
    simulation returned no logistics *and* availability wasn't already a definitive
    `cannotBeDelivered`/`withoutStock`, and never overwrites a simulation price with an empty orderform result.
11. **Output** (`escribir_filas`, `registrar_corrida`, `evaluar_corrida`, `escribir_manifiesto`) — one
    immutable folder per run, one long CSV inside it. The time-series property is no longer "append to a
    file" but "accumulate folders": reading the whole history is a glob over `run_*/filas.csv`. **Never
    overwrite a past run's folder.** The manifest records script/schema version, run ID, requests per
    phase, per-branch tallies, `descubrimiento` / `medicion` / `stock_cadena` completeness blocks,
    `seleccion` (including `modo_seleccion` and, for `--skus`, which SKUs were absent versus never asked
    about), `auditoria_mayorista`, and `resumen.corrida_completa` + `motivos_fallo_global`.
12. **`main()`** — CLI parsing, browser lifecycle, per-run global state reset (`MEDICION`,
    `STOCK_CADENA_ESTADO`, `ALARMA_FIRMA_DISPARADA` — they're module globals so `escribir_manifiesto` can
    read them; keep them cleared per run so a second call in one process starts clean), a warning if pre-v11
    CSVs are still sitting in the old flat output root (it never moves them), auto request-cap sizing, and the
    final `evaluar_corrida` verdict + exit code.

## Scaling to 20+ branches

More branches, same architecture — most machinery already scales because it iterates `NODOS`:

- **Free**: `identificar_nodo`'s search loop, the measurement loop, one more `node_id` value inside the
  single long CSV (`docs/decisiones_1.1.0.md` §8), the manifest's per-branch breakdown, and request-cap
  auto-sizing.
- **Grows linearly, needs planning**: requests per run (SKUs × branches, ~×4 worst case) and wall-clock time
  (requests are strictly sequential at `--intervalo`, by design — "respeto al servidor" in the module
  docstring). 2 → 20 branches is roughly a 10x run. Size `--muestra` and `--intervalo` accordingly instead of
  raising `--tope` blindly — and note that `--categoria` is the other lever: at 20 branches, narrowing the
  universe to the categories that matter commercially beats measuring a wide, shallow slice of everything.
- **The real risk when onboarding a branch**: a wrong or incomplete `NODOS` entry doesn't error — VTEX simply
  never returns that signature, every row becomes `node_resolved="OTHER"` / `OPERADOR_EXTERNO`, and the branch
  looks "covered" in row counts while contributing zero verified prices. The v13 per-node alarm now warns
  during the run, but still: **before trusting a new branch, run a small `--muestra` against just that node and
  confirm rows come back `MATCH` / `MATCH_SELLER_RAIZ`.**
- **Out of scope here**: multi-retailer adapters and a retailer-agnostic domain model (Nivel 3+ of the
  correctness-before-coverage ladder). Don't start building those abstractions speculatively — the
  one-namespace-per-collector split is the only concession the engine makes to that future.

## Output files

One immutable folder per run, named exactly like the `run_id` (`docs/decisiones_1.1.0.md` §7):

```
data/makro_plazavea/
├── run_20260821_201821/
│   ├── filas.csv          one row per (SKU, branch, run) — long format, node_id is a column
│   ├── run.json           this run's manifest
│   └── raw.jsonl.gz       compressed raw VTEX responses
├── run_20260822_001329/   another run, same day or not
├── runs.jsonl             append-only index, one flat line per run
└── last_run.json          copy of the newest manifest, so you don't have to glob
```

- **Fixed names inside, `run_id` only on the folder.** One identifier, impossible to desynchronise.
- **`YYYYMMDD_HHMMSS`, so alphabetical order is chronological.** Reading the whole history is
  `read_csv('data/makro_plazavea/run_*/filas.csv')`.
- **Never one CSV per branch.** The node is the column `node_id`, so a 21st branch adds values, not files,
  and never changes the schema (§8). Measured on `golden_v5.csv`: 24 of the 56 engine columns differ
  between 359 and 360 for at least one SKU, and only 11 of those are node identity — the wide format gets
  more expensive with every branch, not less.
- **`runs.jsonl` is the only append-only thing left**, on purpose: it is an index, not a dataset, and it
  stays readable with `tail` at a thousand runs.
- The engine **never writes exports**. Per-branch or per-category slices come from a separate command.

The analyst does the cross-branch comparison downstream (SQL/pandas over the long CSV) — do not add
cross-branch comparison logic to this engine, at 2 branches or at 20.

## Notes for future sessions

- **This file and `docs/decisiones_1.1.0.md` are the only authoritative context documents in-repo.** No
  other narrative or roadmap document exists — don't cite section numbers from one.
- `CLAUDE.md` is **tracked by git**: every edit lands in the history and in a PR diff. Treat it as source,
  not as scratch. `.gitignore` covers `.env`, `data/`, `graphify-out/`, `.vscode/` and build artifacts.
- `graphify-out/` holds a generated knowledge graph of this repo (`graph.html`, `GRAPH_REPORT.md`) — useful
  for orientation, but the engine file itself is always the source of truth.
- `docs/decisiones_1.1.0.md` — closed inventory of what 1.1.0 shipped: verified bi-price mechanism, the 22
  columns, known bugs, acceptance criterion and scope. It owns those numbers and definitions; don't restate
  them here, where the two copies would drift apart. Consult it before proposing changes. Its §9 bug table
  is **not** fully cleared: the collector-side bugs are fixed, the probe-side ones (v1/v4 pointing at
  `mk_scraping_engine_0.1.0.py`, v2/v3 at `v1.py`, the probe renaming) are still open. Its §12 lists
  `--categorias` as out of scope — that was true for 1.1.0 and stopped being true in 1.2.0, which shipped
  it as `--categoria`. The document is an inventory of *that* release; don't read it as the current roadmap.
- `docs/contradicciones.md` — the audit behind these corrections, with its `## Resoluciones` section. Read
  it before re-adding anything this file used to say.