# Sondas archivadas — makro_plazavea

Cinco scripts de exploración, archivados el **2026-08-30**. **No corren, no se mantienen y no son
tests.** Estaban en `tests/probes/makro_plazavea/`.

## Qué eran

Fueron el trabajo de campo que descubrió el **bi-precio** de Makro: que un SKU baja de precio al
pasar un umbral declarado (`CantidadBiPrecioMK`), y que ese umbral vive solo en la respuesta del
catálogo mientras el descuento aparece también en `simulation`. Ese hallazgo es la razón de existir
de 1.1.0 y de las 22 columnas mayoristas del motor.

| archivo | qué hizo |
|---|---|
| `buscando_precio_mayoristav1.py` … `v4.py` | la búsqueda: cuatro intentos de encontrar dónde declara VTEX el precio mayorista |
| `precio_mayorista_encontradov5.py` | la sonda que lo encontró y produjo `tests/fixtures/makro_plazavea/golden_v5.csv` |

## Por qué se archivan y no se borran

`golden_v5.csv` —el baseline del criterio de aceptación de 1.1.0— **salió de la sonda v5**. Sin la
sonda, el golden es un CSV sin procedencia. Se archivan juntos: el fixture sigue vivo en
`tests/fixtures/`, la herramienta que lo generó queda acá.

## Por qué no corren

Apuntan a nombres de archivo que ya no existen (`mk_scraping_engine_0.1.0.py` en v1/v4, `v1.py` en
v2/v3): es el punto abierto de la tabla de bugs de `docs/historia/decisiones_1.1.0.md` §9. Al
archivarlas, ese bug pasa de *abierto* a *irrelevante* — pero **vuelve si alguien revive una sonda**.

## Si el rumbo nuevo necesita explorar

Carpeta nueva, no esta. Una sonda se edita y se tira; un test de regresión es una promesa. Mezclarlas
ya rompió la suite dos veces (ver `CHANGELOG.md`).
