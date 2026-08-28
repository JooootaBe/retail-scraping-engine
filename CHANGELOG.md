# Changelog

Todos los cambios notables de este proyecto se documentan en este archivo.

El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/)
y el versionado sigue [Semantic Versioning](https://semver.org/lang/es/).

## [Sin publicar]

Seis versiones del motor desde 1.2.0 — v18 a v23 — y dos de ellas mueven `SCHEMA_VERSION`
(4 → 5 → 6). Nada de esto está publicado todavía.

### Added
- **`price_origin`** (v20): `MEDIDO` (hubo stock, la simulación evaluó promociones) /
  `LISTA_SIN_PROMO` (no hubo stock, `price` es el precio de lista) / vacío (no se midió).
  `SCHEMA_VERSION` 5 → 6, 78 → 79 columnas. VTEX no evalúa promociones cuando el nodo no tiene
  stock y devuelve el precio de lista, y hasta v19 ese número entraba en `price` sin forma de
  distinguirlo de uno cotizado. Medido: las 143 filas `withoutStock` de `run_20260822_020027`
  tienen `price == list_price` y `discount_pct = 0.00`, las 143 sin una excepción, contra 19.8% de
  incidencia de promoción entre las filas con stock. La regla mira `availability`, **nunca** la
  relación `price`/`list_price`: esa igualdad también le pasa a una fila con stock y sin promoción,
  y una consecuencia no puede ser el criterio
- **Estado `BIPRECIO_PUBLICACION_INDETERMINADA`** (v20): sin stock el precio mayorista se calcula
  igual —`list_price` es catálogo y el descuento viene del teaser, ninguno depende del stock— pero
  la regla de publicación no se puede evaluar. Se entrega el precio y no se afirma su vigencia
- **Estado `BIPRECIO_SUPERADO_POR_PROMO`** (v18): si `list_price − descuento >= price`, el escalón
  no se publica ni se cobra
- **Quinta regla de calidad `DQ_SIN_STOCK_CON_DESCUENTO`** (v20): fila sin stock con
  `sellingPrice < listPrice`, que contradice la premisa de `LISTA_SIN_PROMO`. No ocurre hoy (0 de
  143) y no se corrige: se avisa
- **La fase de descubrimiento archiva su crudo** en `raw.jsonl.gz` (v19): el árbol de categorías
  (`category_tree`), cada página de categoría (`category_page`, con ruta/nivel/ventana/`resources`)
  y cada lote de `--skus` (`skus_lookup`). `guardar_evidencia` solo se llamaba sobre respuestas de
  medición, y el descubrimiento es la **única** fuente de `CantidadBiPrecioMK`, del teaser del
  descuento y de los metadatos de campaña: la simulación no los devuelve. En
  `run_20260822_020027` la cadena `CantidadBiPrecioMK` aparecía cero veces en el archivo, así que
  el CSV no podía reproducir su propio veredicto. Un campo `tipo` (`catalogo` / `medicion`) separa
  las dos puertas. **No es recuperable hacia atrás**
- **Suite de regresión permanente** en `tests/makro_plazavea/`, 59 casos, ninguno toca la red:
  `test_precio_mayorista.py` (23 fichas del storefront capturadas a mano — la única verdad
  EXTERNA del repo, la que no sale de la misma API que el motor está midiendo),
  `test_precio_en_quiebre.py` (las seis ramas de `price_origin`, filas armadas a mano),
  `test_propiedades_corrida.py` (invariantes sobre una corrida real, releída desde
  `raw.jsonl.gz`), `test_truncamiento.py` (los cuatro casos del techo de paginado),
  `test_auditoria_mayorista.py` (expectativa, propagación y estratos de la auditoría) y
  `test_estado_por_corrida.py` (los globales por-corrida, cuyo reinicio vivía inalcanzable dentro de
  `main()` hasta v23)

- **`ops/`, una frontera nueva en el árbol.** Herramientas operativas que corren a mano o por cron,
  nunca modifican el motor y nunca son importadas por él, y responden una pregunta *sobre* el
  catálogo o sobre una sucursal en lugar de extraer precios. Viven en su propia carpeta
  justamente para que ese límite se vea. Dos hasta ahora:
  - `ops/arbol_categorias.py` (`1b80d3b`) saca un snapshot del árbol de categorías y lo diffea
    contra el anterior. Una categoría nueva no es ruido técnico, es señal comercial —alguien del
    otro lado decidió empezar a vender algo— y hasta ahora entraba al recorrido en silencio
  - `ops/obtener_nodo_logistico_mk.py` (`1cefa70`) captura la firma logística real de una sucursal
    desde un checkout vivo, con navegador headed. Es la evidencia que hace falta ANTES de confiar
    en una entrada nueva de `NODOS`: una firma mal cargada no da error, simplemente hace que VTEX
    nunca la devuelva y que la sucursal parezca cubierta aportando cero precios verificados

### Changed
- **`precio_mayorista = list_price − descuento`**, no `price − descuento` (v18).
  `SCHEMA_VERSION` 4 → 5 **sin agregar una sola columna**: la columna cambia de significado y la
  serie va a tener filas de las dos épocas conviviendo, así que un promedio que las mezcle es un
  promedio de dos definiciones. La fórmula vieja estuvo mal seis semanas sin que nada la delatara,
  porque donde no hay promoción unitaria `price == list_price` y las dos colapsan en el mismo
  número: 2021 de las 2328 filas `COMPLETO` de `run_20260822_020027`. Contra las 23 fichas del
  storefront, la vieja acierta 11/23 y ésta 23/23
- `descuento_mayorista_pct` pasa a medir el ahorro contra `price`, con base explícita, y `bi_umbral`
  se vacía solo si el catálogo no lo declara (§6.2 reescrita)
- **`discount_pct` puede ir vacío** (v20), bajo `LISTA_SIN_PROMO`. `0.00` ahí afirma "no tiene
  descuento" sobre una promoción que nadie evaluó. Con stock, `0.00` sigue siendo una medición
  legítima y sobrevive: la misma celda quiere decir dos cosas según el origen
- **`stock_signal` deja de decir "quiebre"** (v20): `SIN_STOCK_LOCAL_CADENA_CON_STOCK`,
  `SIN_STOCK_CADENA`, `SIN_STOCK_LOCAL_CADENA_DESCONOCIDA`. "Quiebre" afirma un desabastecimiento
  temporal de algo que la sucursal normalmente vende, y eso no se midió: puede ser ausencia de
  surtido. De las 143 filas se verificó **una** a mano (SKU 11566889). Los nombres nuevos dicen lo
  observado y dejan la causa sin afirmar; la serie la resuelve sola con 30 días de eje temporal
- `calcular_descuento_pct` y `clasificar_origen_precio` se extraen como funciones puras, para que
  las reglas se puedan probar sin red. La extracción fue un commit aparte, sin cambio de
  comportamiento, verificada replayando 3174/3174 filas desde `raw.jsonl.gz`
- Los cuatro archivos de regresión se mudan de `tests/probes/makro_plazavea/` a
  `tests/makro_plazavea/`: `probes/` es para sondas exploratorias. En la ubicación vieja no corrían
  —`RAIZ` resolvía dos niveles arriba de donde debía— y `pytest tests/ -q` terminaba en tres
  errores de colección

### Fixed
- **El estado por-corrida se reinicia desde una sola definición** (v23). La primera corrida en vivo de
  v22 murió con `KeyError: 'propagadas'` en el bucle de auditoría, con los 53 tests en verde.
  `MEDICION`, `STOCK_CADENA_ESTADO` y `AUDITORIA_MAYORISTA` se escribían dos veces —el literal de
  módulo y una copia dentro de `main()` para reiniciarlos entre corridas— y v22 agregó `propagadas` y
  `estratos` solo al literal. `propagadas` mata la corrida porque el motor la incrementa con `+=`;
  `estratos` no rompe nada y se pierde del manifiesto, que es el modo de falla peor de los dos. Ningún
  test lo vio porque el reinicio vivía dentro de una `main()` async de 600 líneas que abre Playwright y
  sale a la red antes de llegar a la auditoría: la fase estaba cubierta y su inicialización no. Ahora
  la forma de cada diccionario vive en una función que se usa al importar y al reiniciar, y el reinicio
  salió de `main()` a `reiniciar_estado_por_corrida()`, que un test puede llamar sin red
- **La auditoría del mayorista esperaba el número equivocado, no propagaba su veredicto y muestreaba
  del lado ciego** (v22). Tres defectos de la misma fase. (a) Con la fórmula de v18 hay **dos**
  expectativas de qué cobra VTEX a `qty = bi_umbral`, no una: un `COMPLETO` debe cobrar
  `list_price − descuento` y un `BIPRECIO_SUPERADO_POR_PROMO` debe cobrar `price`, porque su escalón no
  aplica. El SKU 10012680 de `run_20260822_020027` se remidió, VTEX cobró 88.00 —exactamente su `price`,
  con `list_price` 118.50— y salió marcado `DQ_MAYORISTA_DISCREPA` cuando lo que ocurrió es que la
  promoción unitaria le ganaba al escalón. Los `SUPERADO` pasan a ser auditables: ese estado afirma algo
  y era la única afirmación del bi-precio que nada verificaba; `INDETERMINADA` queda excluida, porque sin
  stock VTEX no cotiza a ninguna cantidad. (b) El veredicto se **propaga** a las filas gemelas: lo que se
  mide es propiedad del SKU, no del nodo, y la auditoría corre contra uno solo. Ese SKU salió de la misma
  corrida con 88.00 en el 359 y 87.90 en el 360, y ésa era la única diferencia de mayorista entre nodos en
  los 1482 SKUs con stock en ambos — el 100% de la varianza mayorista entre sucursales era artefacto. Se
  propaga por reclamo completo y no por `sku_id`: una gemela en quiebre no hizo la misma afirmación.
  (c) La muestra se **estratifica** por promoción unitaria, que es el eje donde la fórmula falló: 35 filas
  `COMPLETO` con promoción sobre 1041 en el nodo 359 (3.4%), así que tres extracciones sin estratificar
  tienen ~90% de no tocar ninguna. Las tres auditorías del 22 y las tres del 24 cayeron, las seis, del
  lado ciego. El manifiesto gana `auditoria_mayorista.propagadas` y `.estratos`
- **Alcanzar el total declarado no es truncamiento** (v21). `run_20260822_020027` y
  `run_20260824_154502` quedaron las dos clasificadas `INCOMPLETO_NO_PLANEADO` por
  `TRUNCAMIENTO_VTEX` con un único fallo, la categoría "Fideos Largos", y el mensaje se contradecía
  a sí mismo: "quedó truncada en 50 productos: alcanzó el techo de paginado (~2450)". Cuando el
  total declarado es múltiplo exacto de la ventana, la última página vuelve llena por aritmética y
  no por corte: la página corta que confirmaría el final no puede existir. Solo el techo de VTEX
  indica truncamiento — ahí hay productos que existen y no se pudieron pedir. La decisión sale del
  bucle a `evaluar_fin_de_paginado`, pura. No cambia ninguna columna
- `-p no:cacheprovider`: pytest ya no deja un `.pytest_cache` en la raíz del repo

### Documentación
- **`docs/columnas.md`, nuevo: el diccionario de las 79 columnas de `Fila`.** Una entrada por
  columna con qué responde, por qué existe, todos los valores que el código puede producir (no solo
  los observados), qué significa su vacío y dónde se asigna. Salió de leer el motor y de perfilar
  `run_20260826_021034` (3.162 filas, `SCHEMA_VERSION 6`). Es autoritativo **solo** sobre semántica
  de columnas: la mecánica del bi-precio la sigue poseyendo `docs/decisiones_1.1.0.md` §1/§5 y las
  razones de diseño `CLAUDE.md`. Lo que no existía en ninguna parte:
  - **Qué columnas cambiaron de SIGNIFICADO sin cambiar de nombre**, marcadas con ⚠. Nueve:
    `precio_mayorista` y sus tres derivadas (base `price` → `list_price`, v18), `discount_pct` (el
    `0.00` de una fila sin stock pasó a vacío, v20), `chain_stock` (foto de `panel.json` → dato
    refrescado, v08), `stock_signal` (`QUIEBRE_*` → `SIN_STOCK_*`, v20), `bi_umbral` (dejó de
    vaciarse en todo estado ≠ `COMPLETO`, v18) y `precio_mayorista_verificado`
  - **Los tres significados del vacío de `precio_mayorista_verificado`**, incluido el que no se
    puede ver desde el CSV: SKU 10020888 de esta corrida se midió a `qty=2`, coincidió, y lo
    verificado fue una **ausencia** de escalón — el vacío se conservó a propósito (`:5589-5590`) y
    es indistinguible de "no había nada que verificar". Y que un `SI` **no** distingue auditado de
    propagado: 2 de las 4 filas con `SI` nunca tocaron la red. Esa distinción solo vive en `run.json`
  - **14 invariantes que un lector asumiría y que no se cumplen**, todos medidos: `precio_mayorista`
    lleno con `descuento_mayorista_pct` vacío (75 filas), `bi_umbral` lleno sin precio mayorista
    (396), `descuento_monto` sin precio mayorista (130), y la resta que **no cierra a propósito** en
    una fila `DQ_MAYORISTA_DISCREPA`
  - **Dos columnas sin propósito determinable desde el código**: `sku_ref`, que nadie lee y que
    ningún documento menciona, y `base_price`, cuya tasa de llenado era pregunta abierta de §11 —
    esta corrida la contesta: 100% llena e **idéntica a `list_price` en las 3.162 filas**. No se
    borra ninguna: misma cautela que con `surtido_makro`, un solo alcance es evidencia fina
- **`CLAUDE.md` se sincroniza contra el código, no contra sí mismo.** Se declaraba "el único
  documento mantenido al día por diseño" mientras `README.md` estaba más actualizado que él.
  Verificado contra la fuente: 7085 líneas de colector, `VERSION 2026.08.25-23`,
  `SCHEMA_VERSION 6`, 79 columnas (`len(Fila().__dict__)`), paquete 1.2.0, 59 funciones `test_`,
  19 flags de argparse. `README.md` quedó correcto en los seis valores y no se tocó
- La cronología de `SCHEMA_VERSION` llegaba hasta v15 (`4`, 56 → 78). Se completa: **v18 sube a
  `5` sin agregar una sola columna** —`precio_mayorista` cambia de significado— y v20 sube a `6`
  con `price_origin` (78 → 79). El caso que se olvida es el de v18: un cambio de *significado*
  cuenta como cambio de esquema aunque el header salga idéntico byte a byte
- La sección de verificación arrancaba con "no hay tooling de lint/test/build". Ahora arranca con
  la suite —es el primer paso— y la corrida chica en vivo queda como complemento explícito, no
  como sustituto. v23 justifica el orden en los dos sentidos: los 53 tests de v22 en verde y su
  primera corrida en vivo muerta en el bucle de auditoría
- El párrafo de flags cubría 16 de los 19; entran `--modo`, `--salida` y `--version`, que solo
  vivían en el bloque de ejemplos
- **`DQ_MAYORISTA_DISCREPA` sí se disparó**, y `CLAUDE.md` afirmaba que no. Una vez: SKU 10012680
  de `run_20260822_020027`, umbral 3, y el lado equivocado era la auditoría (v22). La epistemología
  de `BIPRECIO_SUPERADO_POR_PROMO` —que hay **dos** expectativas a `qty = bi_umbral`, no una, y que
  por eso ese estado es auditable mientras `BIPRECIO_PUBLICACION_INDETERMINADA` no lo es— no estaba
  en ninguna parte de `CLAUDE.md`. Se agrega ahí como regla, no como lista de columnas: esas las
  posee `docs/decisiones_1.1.0.md` §5
- **El rango de umbrales verificado bajo la fórmula actual es más chico de lo que dice §11.** Su
  evidencia de 12 / 15 / 24 se midió con la base pre-v18: una auditoría aprobada de una fórmula
  superseded no se hereda. Las auditorías en disco desde v18 cubren 2, 3, 4 y 20, contra un catálogo
  que declara 2, 3, 4, 6, 10, 12, 13, 15 y 20. Queda anotado como ítem abierto; §11 **no** se editó
- **Columnas estructuralmente vacías**, nueva nota en la sección de salida. Medido sobre 9.528 filas
  de las cuatro corridas con `filas.csv`: `postal_resolved`, `neighborhood_resolved`, `sla_selected`,
  `polygon_drift` y `error` están vacías en el 100% de las filas, las cinco declaradas `str = ""` en
  `Fila`. Ninguna es camino muerto —las tres primeras dependen de orderForm o de que VTEX elija SLA,
  y la respuesta de orderForm de `--auditoria` sí trae la dirección, se lee para reconciliar y se
  descarta. Se documenta porque una capa de consolidación que infiera tipos desde una muestra las va
  a tipar float/`NaN`, y acá vacío = desconocido. **`dq_flags` no es una de ellas**: no vacía 1 de
  9.528 veces, que es el caso más difícil para un inferidor de tipos

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