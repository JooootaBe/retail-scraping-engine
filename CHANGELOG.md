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
- `SCHEMA_VERSION` 3 → 4
- En peso variable, `precio_por_unidad_base` se lee de `list_price` en lugar de
  calcularse por división: la división arrastra el redondeo de VTEX

## [1.0.0] - 2026-08-18

### Added
- Motor de extracción para Makro Perú vía API VTEX
- Identificación de sucursal por firma logística (warehouse + dock + courier)
- Clasificación de fulfillment: tienda, raíz, operador externo
- Señales de stock: `chain_stock`, `stock_signal`, `QUIEBRE_LOCAL`
- Archivado de evidencia cruda en `raw/<fecha>/<run_id>.jsonl.gz`
- Auditoría muestreada `simulation` vs `orderForm`

[Sin publicar]: https://github.com/JooootaBe/retail-scraping-engine/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/JooootaBe/retail-scraping-engine/releases/tag/v1.0.0