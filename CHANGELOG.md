# Changelog

Todos los cambios notables de este proyecto se documentan en este archivo.

El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/)
y el versionado sigue [Semantic Versioning](https://semver.org/lang/es/).

## [Sin publicar]

### Added
- Precio mayorista (bi-precio): umbral, monto de descuento y precio reconstruido
- `ean_type` para distinguir EAN globales de códigos internos (prefijo 20-29)
- Presentación normalizada: `unidad_base`, `cantidad_base`, `presentacion_origen`
- Régimen promocional: `promo_regime_id`, `price_valid_until`
- `surtido_makro` verificado por `seller_chain`

### Changed
- **ROMPE consumidores.** Un CSV por sucursal → **un solo archivo en formato largo**, clave
  `(run_id, node_id, sku_id)`. `makro_359_santa_anita.csv` y `makro_360_surco.csv` dejan de
  existir; el nodo pasa a ser la columna `node_id` (`docs/decisiones_1.1.0.md` §8)
- **ROMPE rutas.** Layout de salida: una carpeta por corrida,
  `data/<colector>/run_<run_id>/` con `raw.jsonl.gz`, `run.json` y `filas.csv` de nombre fijo,
  más `runs.jsonl` y `last_run.json` al lado. Leer toda la historia es un glob
  `data/makro_plazavea/run_*/filas.csv` (§7)
- **ROMPE rutas.** `MOTOR = "makro"` → `makro_plazavea`: el colector se identifica por fuente,
  no por retailer, y la carpeta de salida cambia de nombre (§9)
- `--reiniciar` queda sin semántica con el layout nuevo — no hay archivo acumulado que borrar.
  Se redefine o se elimina antes de publicar; decisión abierta (§9)
- `SCHEMA_VERSION` 3 → 4
- En peso variable, `precio_por_unidad_base` se lee de `list_price` en lugar de
  calcularse por división: la división arrastra el redondeo de VTEX

### Fixed
- `fulfillment_type` devolvía `desconocido` con courier, dock y SLA resueltos y almacén
  no-Makro; ahora se clasifica como `operador_externo`. `desconocido` queda reservado para
  falta de información (§9)
- `RAIZ_SALIDA` resolvía dentro del paquete (`src/retail_engine/collectors/salida/`); ahora
  se deriva de la raíz del repo, buscando `pyproject.toml` hacia arriba (§9)
- Parser de presentación con multipack tras la medida: `600ml Paquete 6un` daba 0.6 L en lugar
  de 3.6 L; se multiplica por el conteo posterior (§9)
- Sondas v1/v4: `MOTOR_PY` apuntaba a `mk_scraping_engine_0.1.0.py`, ruta que ya no existe (§9)
- Sondas v2/v3: `V1_PY` buscaba `v1.py`, nombre que ningún archivo tiene; las sondas se
  renombran con ordinal `01_…` … `05_…` (§9)
- `pyproject.toml` estaba vacío: ahora lleva metadata, `where = ["src"]` e instalación
  con `pip install -e .` (§9)

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