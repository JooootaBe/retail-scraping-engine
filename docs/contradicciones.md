# Contradicciones en la documentación — auditoría previa a 1.1.0

Verificación hecha el 2026-08-20 contra el árbol en `83c9ca4`. Cada fila cita archivo y línea.
Nada acá viene de memoria: lo que no se pudo contrastar contra un archivo está en
**Indeterminadas**.

**Método.** Documentos leídos completos. El motor NO se leyó entero: mapa con
`grep -n "^def \|^class "`, bloques puntuales con `sed -n`. El golden se midió con `csv` +
`COLUMNAS` importado del motor. Sin requests de red.

**Hechos base medidos** (se citan repetidamente más abajo):

| Hecho | Medición |
|---|---|
| `COLUMNAS` del motor | 56 (`makro_plazavea.py:523`, `list(Fila().__dict__.keys())`) |
| `golden_v5.csv` | 78 columnas, 40 filas de datos, 20 SKUs × nodos 359/360 |
| Relación | las 56 del motor son prefijo exacto del golden; el golden agrega **22**, no 25 |
| `SCHEMA_VERSION` motor | `"3"` (`makro_plazavea.py:166`); golden trae `schema_version="3"` en las 40 filas |
| `SALIDA` efectiva | `src/retail_engine/collectors/salida/makro` (`makro_plazavea.py:233,240,241`) |

---

## Bloqueantes

Contradicciones que inducirían a escribir código incorrecto, y donde la evidencia sí permite
decidir cuál lado es el correcto.

| # | Qué dice | Archivo:línea | Con qué contradice | Cuál es correcta | Por qué |
|---|---|---|---|---|---|
| **B1** | «one CSV per branch (`nodo.archivo`)»; la salida son `makro_359_santa_anita.csv` y `makro_360_surco.csv`, uno por sucursal | `CLAUDE.md:101-102`, `CLAUDE.md:257`, `CLAUDE.md:274-275` | «**Un solo archivo, formato largo.** […] **Nunca** un CSV por sucursal» — `docs/decisiones_1.1.0.md:228-231` | **decisiones §8** | El golden ya es un único archivo largo: 40 filas con `node_id` ∈ {359, 360} como columna, y `tests/fixtures/makro_plazavea/README.md:6` lo declara «Formato largo, clave (run_id, node_id, sku_id)». El motor actual sí escribe por sucursal (`makro_plazavea.py:2818` `destino = SALIDA / nodo.archivo`; `NODOS` en `:342` y `:361`), y `decisiones §9:244-245` lista ese comportamiento entre los bugs a corregir. CLAUDE.md lo presenta como *guiding principle* vigente, sin marca de obsolescencia |
| **B2** | Toda la salida cuelga de `salida/makro/`: CSVs, `ultima_corrida.json` y `raw/<date>/<run_id>.jsonl.gz` | `CLAUDE.md:33`, `:68`, `:81`, `:194`, `:213`, `:250`, `:269`, `:272-279` | «`data/makro_plazavea/run_<run_id>/` con `raw.jsonl.gz`, `run.json` y `filas.csv`; nombres fijos adentro» — `docs/decisiones_1.1.0.md:202-222` | **decisiones §7** | La ruta de CLAUDE.md no existe: `salida/` fue eliminado del árbol y ya no figura en `.gitignore` (grep sin resultados). El motor hoy resuelve `RAIZ_SALIDA = BASE_DIR/"salida"` (`makro_plazavea.py:240`) → `src/retail_engine/collectors/salida/makro`, *dentro del paquete*, y `decisiones §9:245` lo marca explícitamente como bug a corregir derivando de `pyproject.toml` |
| **B3** | «Clasificación de fulfillment: tienda, raíz, **operador externo**» listado como entregado en **1.0.0** | `CHANGELOG.md:27` | `clasificar_fulfillment` devuelve `tienda`, `tienda_raiz`, `proveedor`, `generico_pv`, `desconocido` (`makro_plazavea.py:2076-2089`); `decisiones §9:247` lo lista como **corrección pendiente** de 1.1.0; `CLAUDE.md:233` enumera el set correcto | **motor + decisiones §9** | `grep -n "operador_externo"` sobre el motor devuelve **cero** ocurrencias del valor de `fulfillment_type` (las 4 coincidencias de `OPERADOR_EXTERNO` en `:198`, `:1587`, `:2517`, `:3552` son de `logistics_status`, otro campo, asignado en `:2517`). Quien lea el CHANGELOG lo da por hecho y salta la corrección |
| **B4** | «Debe reproducir las 40 filas **columna por columna**» contra `golden_v5.csv` | `docs/decisiones_1.1.0.md:256-259` | «`SCHEMA_VERSION` sube a 4» — `docs/decisiones_1.1.0.md:198` (regla dura §6.7) | **§6.7; el criterio §10 está mal redactado** | El golden trae `schema_version="3"` en las 40 filas. Si 1.1.0 escribe `"4"`, esa columna difiere siempre y el `diff` literal nunca puede pasar. Lo mismo con `run_id` (golden: `v5_20260820_000016` en las 40) y `timestamp`. §6.7 es inevitable; §10 tiene que excluir explícitamente las columnas volátiles o el criterio de aceptación es insatisfacible por construcción |
| **B5** | «This file provides guidance […] `CLAUDE.md`, `salida/`, `.env`, and generated CSV/JSONL artifacts **are gitignored**, so edits to this file are not protected by git history» | `CLAUDE.md:289-290` | Estado real del repo | **el estado real** | `git ls-files --error-unmatch CLAUDE.md` → tracked; `git log -1 -- CLAUDE.md` → `83c9ca4`; `git check-ignore CLAUDE.md` → no ignorado; `.gitignore` (38 líneas) no menciona `CLAUDE.md` ni `salida/`. Es bloqueante y no cosmético: la frase autoriza a editar CLAUDE.md como scratch, y hoy cada edición entra al historial y al diff de un PR |
| **B6** | «There is no `src/`, no test suite, no packaging — the whole engine is one file», con el inventario `engines/… · main.py · requirements.txt · README.md · historia.md · salida/makro/` | `CLAUDE.md:24-34` | Árbol real | **el árbol real** | Existen `src/` (paquete instalable), `tests/` (5 sondas + fixture) y `pyproject.toml:1-14` (`retail-engine` 1.0.0, `where = ["src"]`). De los 6 ítems del inventario, **5 no existen**: `engines/mk_scraping_engine_0.1.0.py`, `main.py`, `requirements.txt`, `historia.md`, `salida/`. Es el primer bloque descriptivo del documento y describe un repo que ya no es este |

---

## No bloqueantes

Rutas obsoletas, referencias rotas, idioma y números sueltos. No cambian qué código se escribe.

| # | Qué dice | Archivo:línea | Con qué contradice | Cuál es correcta | Por qué |
|---|---|---|---|---|---|
| **N1** | Todos los comandos de ejecución invocan `python3 engines/mk_scraping_engine_0.1.0.py …`; la sección de arquitectura se titula con esa ruta | `CLAUDE.md:28`, `:50-55`, `:210` | El archivo está en `src/retail_engine/collectors/makro_plazavea.py` | el árbol real | Las 6 líneas de «Running it» fallan tal cual. Los datos *sobre* el archivo sí siguen siendo ciertos: 3779 líneas (`wc -l`), `VERSION = "2026.08.13-13"` (`:157`), `SCHEMA_VERSION = "3"` (`:166`) |
| **N2** | «`python -m pip install -r requirements.txt`» / «`requirements.txt` playwright==1.62.0» | `CLAUDE.md:30`, `:47` | `requirements.txt` fue borrado; la instalación es `pip install -e .` con `dependencies = ["playwright>=1.62.0"]` | `pyproject.toml:9-11` | El archivo no existe en el árbol. Queda además un puntero vivo a él en `tests/probes/makro_plazavea/precio_mayorista_encontradov5.py:1977` (mensaje de error) |
| **N3** | «**`main.py` caveat:** it defines `draw_banner()` but never calls it…», 3 líneas dedicadas | `CLAUDE.md:29`, `:40-42` | `main.py` no existe en el árbol | el árbol real | Advertencia sobre un archivo ausente |
| **N4** | «`historia.md` tells the long version…» / «`README.md` (spec) and `historia.md` (narrative) are the authoritative context documents in-repo» | `CLAUDE.md:11`, `:32`, `:286-288` | `historia.md` no existe; `README.md` pesa **0 bytes** | el árbol real | CLAUDE.md cita sus dos fuentes autoritativas y ninguna tiene contenido. Además cita secciones concretas — «`README.md` §2 and §6» (`:11`) y «README §6, Nivel 3+» (`:268`) — de un archivo vacío. Irónico dado `:287-288`, que advierte justamente contra citar secciones de un archivo que no está en el repo |
| **N5** | «`salida/makro/` all output (**gitignored**)» | `CLAUDE.md:33` | `.gitignore` ignora `data/` (`:32`), no `salida/` | `.gitignore:32` | `grep -n salida .gitignore` no devuelve nada |
| **N6** | Enlaces de comparación a `github.com/JooootaBe/**retail-scraping-engine**` | `CHANGELOG.md:32-33` | `git remote get-url origin` → `https://github.com/JooootaBe/retail_web_scraping_engine.git` | el remoto real | Los dos enlaces al pie del CHANGELOG dan 404. El proyecto se llama de 4 formas distintas: directorio `retail_scraping_engine`, remoto `retail_web_scraping_engine`, enlaces `retail-scraping-engine`, paquete `retail-engine` (`pyproject.toml:6`) |
| **N7** | «Justificación medida: de 56 columnas, **solo 11 difieren** entre 359 y 360, y son **todas** de identidad del nodo» | `docs/decisiones_1.1.0.md:234-235` | Medido sobre `golden_v5.csv`, restringido a las 56 columnas del motor: **24** difieren en al menos un SKU | la medición | 11 son de identidad del nodo (`branch`, `courier_id`, `courier_name`, `delivery_channel`, `dock_id`, `node_id`, `node_resolved`, `polygon_name`, `postal_sent`, `seller_chain`, `warehouse_id`) y **13 no lo son**: `availability`, `error_class`, `fulfillment_confirmed`, `fulfillment_type`, `logistics_status`, `price_status`, `shipping_cost`, `shipping_estimate`, `sla_count`, `sla_name`, `sla_status`, `stock_signal`, `timestamp`. El «11» coincide exacto con el subconjunto de identidad, así que parece que se contó ese subconjunto y se escribió «todas». La conclusión (formato largo) no se debilita: si algo, 24 columnas divergentes la refuerzan |
| **N8** | «Medido: **50%** GS1_GLOBAL, **25%** INTERNO_RESTRINGIDO, **25%** FALTANTE» | `docs/decisiones_1.1.0.md:152` | Golden: 22/40 = **55%**, 8/40 = **20%**, 10/40 = **25%** | — | El documento no nombra la fuente de esos porcentajes, así que puede ser otra muestra. Se deja anotado el desvío, no se corrige |
| **N9** | «`price_valid_until` — `3000-01-02` es el centinela de "sin vencimiento". Una fecha real sería vencimiento de campaña» | `docs/decisiones_1.1.0.md:135-136` | Golden: `3000-01-02T05:00:00Z` (34 filas), `2026-09-01T05:00:00Z` (4) y **`2099-01-02T05:00:00Z` (2)** | — | `2099-01-02` tiene forma de segundo centinela y no está documentado. Bajo la regla de §5 se clasificaría como «vencimiento de campaña» real, en el año 2099 |
| **N10** | «Verificado al centavo en **5 SKUs**, remedidos contra checkout» (tabla de 5 filas) | `docs/decisiones_1.1.0.md:17-25` | «Auditoría del precio mayorista: **3/3** exactas al centavo» — `tests/fixtures/makro_plazavea/README.md:8` | — | Consistente con el golden, que tiene 3 filas con `precio_mayorista_verificado="SI"` (21 `"NO"`, 16 vacío). Son dos ejercicios distintos, pero los dos se presentan como «la verificación» sin distinguirlos. Ninguno de los 5 SKUs de la tabla §1 es identificable en el golden: los precios de la tabla (13.70, 13.30, 33.80, 12.50, 15.50) no se pueden cruzar sin `sku_id` |
| **N11** | El CHANGELOG «Sin publicar» lista columnas nuevas y `SCHEMA_VERSION 3 → 4` | `CHANGELOG.md:10-20` | Omite tres cambios rompientes de `decisiones_1.1.0.md` | decisiones | No aparecen: el paso de un CSV por sucursal a archivo único largo (§8:228-232), el layout nuevo `data/<colector>/run_<id>/` (§7:202-222), ni `MOTOR = "makro"` → `makro_plazavea` (§9:244). Son exactamente los cambios que rompen a cualquier consumidor aguas abajo |
| **N12** | CLAUDE.md no tiene sección `## Pendiente para 1.1.0`; el puntero a `decisiones_1.1.0.md` son 3 líneas al final, dentro de «Notes for future sessions» | `CLAUDE.md:294-296` (últimas del archivo) | Las 12 secciones «Guiding principle» / «Architecture» (`:89-282`) siguen describiendo 1.0.0 | — | Ninguna de esas secciones lleva marca de obsolescencia. Un implementador que lea CLAUDE.md de arriba abajo se topa con B1 y B2 mucho antes de llegar al puntero de la línea 294 |
| **N13** | Idioma dividido por documento: `CLAUDE.md` íntegramente en inglés; `decisiones_1.1.0.md`, `CHANGELOG.md` y el README de fixtures íntegramente en español | `CLAUDE.md:1-296` vs `docs/decisiones_1.1.0.md:1-290` | — | — | Dentro de cada documento el idioma es consistente: el único español en CLAUDE.md es la glosa deliberada «per-branch (sucursal)» (`:22`); en decisiones no hay prosa en inglés. La mezcla real está en `CHANGELOG.md`, con prosa española bajo encabezados ingleses `### Added` / `### Changed` (`:10`, `:17`, `:24`) — pero eso lo impone Keep a Changelog, citado en `:5`, así que es probablemente deliberado |

---

## Indeterminadas

Contradicciones reales donde la evidencia disponible **no permite decidir** cuál lado es el
correcto. No se elige. Las cuatro **bloquean** la implementación: hay que resolverlas antes de
escribir código, no durante.

### I1 — `precio_por_unidad_base`: leído de `list_price` o calculado por división · **BLOQUEA**

- **Dice:** «`precio_por_unidad_base` se **lee** de `list_price`. **No** se calcula por división:
  `1.23 ÷ 0.2 = 6.15` y el real es `6.19`» — `docs/decisiones_1.1.0.md:91-93`.
  Repetido en `CHANGELOG.md:19-20`.
- **Contradice:** el propio `golden_v5.csv`, que `docs/decisiones_1.1.0.md:256-259` designa como
  criterio de aceptación.
- **Medición:** en las **8/8** filas de peso variable del golden, `precio_por_unidad_base` es
  exactamente `price / unit_multiplier`, y en **0/8** es `list_price`:

  | SKU | producto | `price` | `unit_multiplier` | `list_price` | `precio_por_unidad_base` |
  |---|---|---|---|---|---|
  | 11576526 | Maracuyá | 1.23 | 0.2 | **6.19** | **6.1500** |
  | 11576536 | Granadilla | 1.45 | 0.15 | 9.69 | 9.6667 |
  | 11542387 | Huachalomo TERNEZ x kg | 20.29 | 0.7 | 28.99 | 28.9857 |
  | 11542391 | Guiso Económico TERNEZ x kg | 39.88 | 1.4 | 28.49 | 28.4857 |

  La fila de Maracuyá es literalmente el ejemplo que `:85` y `:91` usan para argumentar la
  regla: el documento dice que el valor correcto es 6.19 y el golden guarda 6.1500, el número
  que el documento señala como equivocado. En las 40 filas, `precio_por_unidad_base` coincide
  con la columna vieja `price_per_unit` en 18 (todas las de peso variable, más algunas de
  `measurement_unit='un'` sin presentación parseable).
- **Por qué no se puede decidir:** las dos partes son el mismo documento. §4 argumenta la regla
  con evidencia externa y un porqué (el redondeo de VTEX se amplifica al dividir); §10 congela
  como baseline una corrida que no la aplica. O la regla es correcta y hay que regenerar el
  golden, o el golden es correcto y §4 y `CHANGELOG.md:19-20` describen algo que no se
  implementó. Nada en el repo distingue los dos casos.
- **Nota aparte:** la premisa de §4 sí queda corroborada por el golden. `:89` dice «El Bife Ancho
  lo confirma: `price` > `list_price`, imposible en un precio tachado». Ese SKU no está en el
  golden, pero *Guiso Económico TERNEZ* sí cumple `price` (39.88) > `list_price` (28.49). Lo que
  está en disputa es la fórmula, no el diagnóstico.

### I2 — 25 columnas nuevas o 22 · **BLOQUEA**

- **Dice:** «## 5. Columnas nuevas (**25**)» — `docs/decisiones_1.1.0.md:102`, desglosadas en
  9 + 4 + 4 + 5 + 3 (`:104`, `:129`, `:139`, `:155`, `:172`). Suman 25, correcto.
  `CLAUDE.md:295` repite «25 new columns».
- **Contradice:** `tests/fixtures/makro_plazavea/README.md:5` («78 columnas») y el archivo, que
  tiene 78. Con 56 del motor: 56 + 25 = **81 ≠ 78**.
- **Medición:** de las 25 declaradas, el golden trae 22. Faltan las **3 de «Marca y confianza»**
  (`docs/decisiones_1.1.0.md:172-179`): `presentacion_confianza`, `brand_dq`, `es_marca_propia`.
  Las otras 22 están todas presentes, y las 56 del motor son prefijo exacto del header.
- **Por qué no se puede decidir:** o esas 3 columnas entran en 1.1.0 y el golden queda obsoleto
  como baseline «columna por columna» (§10), o quedan fuera de alcance y §5 debe decir 22. El
  repo no contiene ninguna medición de `brand_dq` ni de `es_marca_propia` con la que romper el
  empate. Sí es un dato que el golden fue producido por la sonda v5
  (`tests/probes/makro_plazavea/precio_mayorista_encontradov5.py`), no por el motor, así que su
  esquema es una propuesta de la sonda y no un contrato ya acordado.

### I3 — `biprecio_status = COMPLETO` exige precio medido o solo consistente · **BLOQUEA**

- **Dice:** «`COMPLETO` — umbral + descuento + **precio verificado**» — `docs/decisiones_1.1.0.md:122`,
  junto a «`precio_mayorista_verificado` — SI (medido a qty≥umbral) | NO (reconstruido)» (`:115`).
- **Contradice:** el golden cruza así:

  | `biprecio_status` | `precio_mayorista_verificado` | filas |
  |---|---|---|
  | COMPLETO | SI | 3 |
  | COMPLETO | NO | **21** |
  | SIN_BIPRECIO | (vacío) | 16 |

- **Por qué no se puede decidir:** si «verificado» en `:122` significa `precio_mayorista_verificado="SI"`,
  entonces 21 filas del golden están mal etiquetadas y un implementador que siga §5 al pie
  producirá 3 COMPLETO donde el baseline espera 24. Si significa «pasó los controles de
  consistencia» — lectura compatible con `INCONSISTENTE` en `:126` («mayorista ≤ 0, mayor que
  unitario, o no da centavo exacto») — el golden es correcto y la redacción de `:122` es
  ambigua. El documento usa la palabra «verificado» con los dos sentidos y no define ninguno.

### I4 — `promo_regime_id`: qué UUID es el régimen del bi-precio · **BLOQUEA**

- **Dice:** «Un solo régimen VTEX cubre todo el bi-precio de Makro:
  `rateAndBenefitsIdentifiers[0].id = ca697c7e-dcd3-40bb-8dd0-c21a1163ea22` ("Bi Precio Vigente
  Regular MAKRO"). […] **Si cambia, Makro reconfiguró su esquema mayorista**» —
  `docs/decisiones_1.1.0.md:37-40`.
- **Contradice:** ese UUID aparece **0 veces** en `golden_v5.csv`. Lo que hay:

  | `promo_regime_id` | `promo_regime_name` | filas |
  |---|---|---|
  | `225a92ff-a721-4f76-8856-2cba133c12d8` | `MAKRO-Bi-Precio\|Vigente Oculto` | 24 |
  | `57827689-dbca-4402-9f5c-25f6150105f8` | `Precio Vigente Regular MAKRO` | 6 |
  | (vacío) | (vacío) | 10 |

  Las 24 filas con bi-precio (`bi_umbral` y `precio_mayorista` no vacíos: 24 y 24) llevan todas
  `225a92ff-…`. El nombre citado en el documento, «Bi Precio Vigente Regular MAKRO», no existe
  como tal: parece una fusión de los dos nombres que sí están.
- **Por qué no se puede decidir:** la afirmación **estructural** de §1 sí se sostiene — un único
  régimen cubre las 24 filas de bi-precio. Lo que no se puede resolver es el literal: el UUID del
  documento puede venir de una captura anterior que no está en el repo (`data/` está vacío y
  gitignoreado, no hay `raw/` archivado), o puede ser un error de transcripción. Importa porque
  §1:40 propone tratar ese valor como centinela de reconfiguración: con el UUID equivocado, la
  alarma dispara en la primera corrida.

---

## Veredicto

**NO LISTO PARA 1.1.0 — 6 bloqueantes determinados (B1-B6) y 4 indeterminadas que también
bloquean (I1-I4): 10 puntos a resolver antes de escribir código.**

---

## Resoluciones

### I1 — `precio_por_unidad_base`
Gana el documento. La corrección de peso variable se decidió DESPUÉS de que v5 corriera;
por eso el golden guarda 6.15. El golden NO se regenera: se excluye esa columna del
criterio de aceptación.

### I2 — Son 22 columnas, no 25
`presentacion_confianza`, `brand_dq` y `es_marca_propia` pasan a 1.1.1: fueron propuestas
sin ninguna medición detrás. 56 + 22 = 78, cuadra con el golden.

### I3 — `COMPLETO`
"precio verificado" significa `price_status = VERIFIED` (el precio UNITARIO), no
`precio_mayorista_verificado = SI`. El golden es correcto: 24 COMPLETO.

### I4 — `promo_regime_id`
No es qué UUID, es qué campo. `rateAndBenefitsIdentifiers` está VACÍO en qty=1 y el motor
mide a qty=1: ese campo es inutilizable. `promo_regime_id` se toma de `teaser.id`.
Nombre correcto: `MAKRO-Bi-Precio|Vigente Oculto`. "Bi Precio Vigente Regular MAKRO" era
una fusión errónea de dos nombres distintos.

### B4 — Criterio de aceptación
Excluye columnas volátiles (`run_id`, `timestamp`, `fecha`, `schema_version`) y las que
cambiaron por diseño (`precio_por_unidad_base`, `precio_mayorista_por_unidad_base`,
`fulfillment_type`, `discount_pct` en peso variable). El resto debe coincidir exacto.

### N7 — Columnas que difieren entre nodos
Son 24, no 11. Las 11 se midieron sobre los CSV del smoke test (10 SKUs), no sobre el
golden. La conclusión del formato largo se refuerza, no se debilita.
