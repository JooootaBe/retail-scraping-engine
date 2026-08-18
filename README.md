
# mk_scraping_engine_0.1.0
**Motor de Adquisición y Validación de Price Intelligence para Makro Perú**

*Documento de Especificación Arquitectónica y Operativa* *Basado en la versión del motor: `extractor_makro_v13.py` (2026.08.13-13) / Conceptualmente: `mk_scraping_engine_0.1.0.py`, *

---

## 1. Abstracto / Resumen Ejecutivo

**mk_scraping_engine_0.1.0** no es un scraper convencional, sino un **Motor de Extracción, Resolución, Validación y Normalización de Observaciones Comerciales**. Diseñado para el proyecto *Retail Web Scrapinf Engine*, su propósito central es la adquisición de inteligencia de precios logrando un grado de exactitud (Correctness) que permita a un analista de precios confiar en que la atribución de una sucursal es hermética antes de escalar la cobertura.

La unidad atómica de este sistema es la **Observación Comercial Contextualizada**, donde no basta con obtener un precio (`observed_price`), sino que se exige una confirmación logística rigurosa (`validated_price`).

---

## 2. Filosofía Arquitectónica: Nivel 1 (Correctness) vs Nivel 2 (Coverage)

Todo diseño en esta base de código obedece a un principio fundamental: **Un dataset grande con atribución difusa es inferior a un dataset pequeño con atribución irrefutable.**

### 2.1. El Paradigma de la Atribución de Sucursal
El motor prioriza el "Nivel 1: Correctness" por encima del "Nivel 2: Coverage". Para que un precio sea considerado válido, la firma logística de la respuesta de VTEX debe coincidir exactamente con el nodo físico esperado. 

No se infiere la sucursal por el código enviado ni por el precio. Se valida estrictamente contra las firmas logísticas:
* **Nodo 359 — Santa Anita:** `warehouseId = SW-359-MKO`, `dockId = DC-359-MKO`, `courierId = DD-359-MKO`, `courierName = DD-Santa-Anita-Makro`
* **Nodo 360 — Surco:** `warehouseId = SW-360-MKO`, `dockId = DC-360-MKO`, `courierId = DD-360-MKO`, `courierName = DD-Surco-Makro`

---

## 3. Metodología de Adquisición (Diseño de la v13 (actualmente versión 0.1.0))

El motor golpea directamente la API de VTEX del storefront `www.makro.plazavea.com.pe` prescindiendo completamente de *page scraping* (parseo de HTML). 

* **Tecnología Base:** `Playwright` utilizando `APIRequestContext`.
* **Simulación Estricta:** No realiza compras, únicamente maneja simulaciones y sesiones de `orderForm` temporales.
* **Gestión de Excepciones Globales:** Tolerancia a fallos a nivel de SKU. El motor aísla los fallos locales (por ejemplo, categorías sin stock) del agotamiento de presupuesto global.

---

## 4. Auditoría, Diagnóstico y Telemetría

Para garantizar que cada corrida sea auditable y reproducible, el sistema cuenta con estrictos mecanismos de control analizados durante el ciclo de *debugging* de la v12 y consolidados en la v13 por los escuadrones de revisión (Claude, Codex, Hermes).

### 4.1. Presupuesto y Límites de Seguridad
* Presupuestos separados para las fases de descubrimiento y auditoría, garantizando que el "agotamiento del presupuesto" no sea tratado falsamente como un "fallo de categoría".
* Las verificaciones externas (`verificador_smoke_v13.py` (prueba con 10 skus del motor v13)) contrastan los manifiestos contra los archivos generados.

### 4.2. Contratos de Completitud
Una corrida sólo es válida si sus estados explícitos son verdaderos:
* `medicion.completa = true`
* `stock_cadena.completo = true`
* `corrida_completa = true`
* `exit_code = 0`

---

## 5. Artefactos y Sistema de Salida (`salida/`)

El modelo de datos almacena todo en esquemas *append-only* que previenen la mutación destructiva del historial:

1.  **Archivos CSV por Sucursal (`makro_359_santa_anita.csv`, `makro_360_surco.csv`):** Una fila por (SKU, sucursal, corrida). Esquema inmutable y particionado naturalmente por nodo.
2.  **`panel.json`:** Registro del basket congelado de SKUs bajo monitoreo en el tiempo.
3.  **`ultima_corrida.json`:** Manifiesto de salud, auditoría y telemetría de la última corrida (conteos por sucursal, estadísticas de muestreo, alertas de deriva de contrato).
4.  **Raw Evidence (`raw/<date>/<run_id>.jsonl.gz`):** Respuestas puras de la API de VTEX comprimidas. Permite *replays* y correcciones retrospectivas sin volver a estresar los servidores de Makro.

---

## 6. Evolución y Escalabilidad (Hacia 0.1.1+)

1.  **Consolidación Makro:** Expandir a 10-20 sucursales sin alterar el diseño de catálogos o `NODOS`.
2.  **Adquisición Multi-Retailer (Nivel 3+):** Introducción de *Retailer Adapters* y un *Domain Model* agnóstico.
3.  **Análisis Histórico y Competitivo (Niveles 4 y 5):** Transición a almacenes analíticos (DuckDB/Parquet) para evaluar tendencias, cobertura y estrategia comercial.

> **Nota Final:** "El scraper responde '¿Qué precio encontré?'. El motor responde '¿Qué precio encontré, dónde, para qué producto, bajo qué fulfillment, con qué seller, en qué zona, en qué momento, con qué evidencia, con qué nivel de confianza y cómo se compara históricamente?'"
README_mk_engine.md
Mostrando README_mk_engine.md.