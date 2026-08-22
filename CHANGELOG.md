# Changelog

Todos los cambios notables de este proyecto se documentan en este archivo.

El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/)
y el versionado sigue [Semantic Versioning](https://semver.org/lang/es/).

## [Sin publicar]

## [1.2.0] - 2026-08-22

### Added
- **`--categoria "/399/,/77/"`**: acota el universo del descubrimiento a una o varias ramas del
  árbol. Hasta 1.1.0 el alcance de una corrida lo decidía el orden del árbol y no el analista:
  `descubrir_catalogo` recorre las ~3.400 categorías en orden fijo y corta al llegar a
  `--catalogo N`, así que una corrida acotada medía siempre las primeras (Packs Limpieza, Packs
  Desayunos, Packs Vinos) y abarrotes podía no entrar nunca
- El filtro es un match de prefijo **por segmento** sobre la ruta completa desde la raíz, así que
  pedir un padre incluye a sus hijos y pedir `/39/` no arrastra `/399/`. Acepta la ruta con o sin
  barras
- Se aplica **antes** de recorrer: las categorías descartadas no cuestan requests. Medido sobre
  `/399/` (Limpieza): 47 categorías recorridas, 3.353 saltadas sin una sola request
- Una ruta que no existe en el árbol es un error de argumentos (exit 2), no un aviso — una corrida
  que mide cero categorías por un typo no puede terminar en 0 con un CSV vacío. Se reportan todas
  las rutas inválidas juntas
- Antes de recorrer se listan en consola las categorías seleccionadas con su ruta y su nombre, y
  tras el descubrimiento se estima el costo de la medición, para poder abortar mientras abortar
  todavía ahorra algo
- El manifiesto registra `categoria_filtro`, `alcance`, `categorias_seleccionadas` y
  `categorias_a_recorrer`: sin eso no se puede reconstruir el alcance de una corrida vieja, porque
  dos corridas con el mismo número de SKUs pueden haber mirado ramas distintas

### Changed
- `completo=True` en el descubrimiento pasa a significar "completo respecto de lo pedido" cuando
  hay `--categoria`. Se distingue con la clasificación nueva **`COMPLETO_EN_CATEGORIAS`**:
  llamarlo `COMPLETO` a secas haría que una serie armada sobre una rama se lea después como
  cobertura del catálogo. Con `--por-categoria` sigue mandando el contrato viejo: nunca completo
- `--categoria` es incompatible con `--skus`, que ya nombra exactamente qué medir
- `pyproject.toml` sube a 1.2.0 (venía declarando 1.0.0 desde antes de 1.1.0)

## [1.1.0] - 2026-08-22

### Added
- **Precio mayorista (bi-precio)**: umbral (`CantidadBiPrecioMK`), monto de descuento y precio
  reconstruido — `precio_mayorista = price − descuento`, un solo escalón. `CantidadTriPrecioMK` se
  registra en `tri_umbral_declarado` y **nunca** se aplica: está declarado y se midió que checkout
  no lo honra (`docs/decisiones_1.1.0.md` §1)
- 22 columnas nuevas en total, `SCHEMA_VERSION` 3 → 4 (56 → 78 columnas): 9 de bi-precio, 4 de
  régimen promocional, 4 de identidad, 5 de presentación (§5)
- `ean_type` para distinguir EAN globales de códigos internos (prefijo 20-29). La mitad del catálogo
  no cruza contra otro retailer; sin esta columna eso son falsos negativos silenciosos
- Presentación normalizada: `unidad_base`, `cantidad_base`, `presentacion_origen` — dos ramas,
  `VTEX` (autoritativa) y `NOMBRE` (heurística, etiquetada como tal)
- Régimen promocional leído del teaser, no de `rateAndBenefitsIdentifiers` (vacío en qty=1):
  `promo_regime_id`, `promo_regime_name`, `payment_method_id`, `price_valid_until`
- `surtido_makro` verificado por `seller_chain`
- **Fase de auditoría del precio mayorista** (`--auditoria-mayorista N`, default 3): remide N SKUs a
  `qty = bi_umbral` y contrasta medido contra reconstruido. Las filas auditadas pasan a
  `precio_mayorista_verificado = SI`; una discrepancia marca `DQ_MAYORISTA_DISCREPA` y grita en
  consola. Verificada exacta al centavo en umbrales 2, 3, 4, 12, 15 y 24 — el rango completo
  observado en este catálogo (§11)
- `--skus <lista>` (máx 20): mide una lista explícita y salta el descubrimiento. Incompatible con
  `--catalogo` / `--muestra`. Un SKU pedido que el catálogo no devuelve se escribe igual, como
  `SKU_NO_ENCONTRADO` — su ausencia sería indistinguible de no haberlo pedido (§8.1)
- `--dry-run`: mide, imprime y no escribe nada — ni CSV, ni manifiesto, ni evidencia, ni la carpeta
- `DQ_UNIDAD_INCONSISTENTE`: cuarta regla de calidad, marca peso variable donde
  `sellingPrice / unit_multiplier` no reconstruye `list_price`

### Changed
- **ROMPE consumidores.** Un CSV por sucursal → **un solo archivo en formato largo**, clave
  `(run_id, node_id, sku_id)`. `makro_359_santa_anita.csv` y `makro_360_surco.csv` dejan de
  existir; el nodo pasa a ser la columna `node_id` (§8)
- **ROMPE rutas.** Layout de salida: una carpeta inmutable por corrida,
  `data/<colector>/run_<run_id>/` con `raw.jsonl.gz`, `run.json` y `filas.csv` de nombre fijo,
  más `runs.jsonl` y `last_run.json` al lado. La propiedad de serie de tiempo deja de ser "apendear
  a un archivo" y pasa a ser "acumular carpetas": leer toda la historia es un glob
  `data/makro_plazavea/run_*/filas.csv` (§7)
- **ROMPE rutas.** `MOTOR = "makro"` → `makro_plazavea`: el colector se identifica por fuente,
  no por retailer, y la carpeta de salida cambia de nombre (§9)
- **ELIMINADO `--reiniciar`.** Borraba los CSV para "reiniciar la serie", que tenía sentido con un
  archivo mutable. Con carpetas inmutables no hay archivo acumulado que reiniciar y lo único que el
  flag podría borrar es historia ya cerrada (§9)
- **ELIMINADA la guardia `archivar_si_cambio_el_esquema()`.** Protegía el modo append de cabeceras
  desalineadas; sin append, dos esquemas ya no comparten archivo. Lo que la guardia protegía lo
  sigue dando `schema_version`, escrito en cada fila
- El manifiesto pasa de `ultima_corrida.json` en la raíz a `run.json` dentro de la carpeta de la
  corrida, y gana `modo_seleccion`, `auditoria_mayorista` y `filas_escritas`
- En peso variable, `price_per_unit` y `precio_por_unidad_base` se **leen** de `list_price` en lugar
  de calcularse por división: la división arrastra y amplifica el redondeo de VTEX (§4)

### Fixed
- `fulfillment_type` devolvía `desconocido` con courier, dock y SLA resueltos y almacén
  no-Makro; ahora se clasifica como `operador_externo`. `desconocido` queda reservado para
  falta de información (§9)
- `RAIZ_SALIDA` resolvía dentro del paquete (`src/retail_engine/collectors/salida/`); ahora
  se deriva de la raíz del repo, buscando `pyproject.toml` hacia arriba (§9)
- Parser de presentación con multipack tras la medida: `600ml Paquete 6un` daba 0.6 L en lugar
  de 3.6 L; se multiplica por el conteo posterior (§9)
- `fq=skuId:` devuelve el producto entero con todas sus variantes y `parsear_producto` lee
  `items[0]`: un multivariante se medía en la variante equivocada. Se reordena por el SKU pedido
  antes de parsear (§9)
- `parsear_producto` descartaba lo que la cadena no tiene en stock — correcto al descubrir, pero al
  medir una lista explícita escondía justo el caso que se quería ver (§9)

### Known issues
Del inventario de bugs de §9 quedan sin corregir, y no bloquean la versión:
- Sondas v1/v4: `MOTOR_PY` sigue apuntando a `engines/mk_scraping_engine_0.1.0.py`, ruta que no
  existe. Sonda v3: `V1_PY` sigue buscando `v1.py`. Las sondas **no** se renombraron con ordinal
- El docstring del módulo todavía muestra un ejemplo con `extractor_makro_v11.py --reiniciar`
- `precio_mayorista_verificado` solo puede decir `SI` en el nodo auditado: la auditoría corre contra
  un solo nodo, así que "umbral y descuento aplican igual en todas las sucursales" sigue asumido (§11)
- Umbral 12 quedó verificado, pero el rango probado es el observado en este catálogo, no todo umbral
  posible

## [1.0.0] - 2026-08-18

### Added
- Motor de extracción para Makro Perú vía API VTEX
- Identificación de sucursal por firma logística (warehouse + dock + courier)
- Clasificación de fulfillment: `tienda`, `tienda_raiz`, `proveedor`, `generico_pv`,
  `desconocido`
- Señales de stock: `chain_stock`, `stock_signal`, `QUIEBRE_LOCAL`
- Archivado de evidencia cruda en `raw/<fecha>/<run_id>.jsonl.gz`
- Auditoría muestreada `simulation` vs `orderForm`

<!-- Enlaces de comparación: PENDIENTES, a propósito.
     Los que había apuntaban a github.com/JooootaBe/retail-scraping-engine, que no es el
     remoto de este repo y da 404. El proyecto se nombra hoy de cuatro formas distintas
     (directorio, remoto, estos enlaces, paquete en pyproject.toml): hay que fijar el nombre
     canónico antes de publicar y recién ahí escribir las URLs, con esta forma:
       [Sin publicar]: <repo>/compare/v1.0.0...HEAD
       [1.0.0]:        <repo>/releases/tag/v1.0.0
     No inventar la URL. -->