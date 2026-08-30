# Fixtures — makro_plazavea

## golden_v5.csv

20 SKUs × 2 nodos (359 Santa Anita, 360 Surco) = 40 filas, 78 columnas.
Formato largo, clave (run_id, node_id, sku_id).
Corrida de la sonda v5, 20260820_000016.
Auditoría del precio mayorista: 3/3 exactas al centavo.

El criterio de aceptación de 1.1.0 contra este golden —qué columnas deben coincidir exacto y
cuáles se excluyen por volátiles o por cambio de diseño— lo fija `docs/historia/decisiones_1.1.0.md` §10.
Es la única fuente: no repetirlo acá, dos copias divergen.
No reemplazar este archivo sin dejar constancia en CHANGELOG.md.

**Es pre-v18: las columnas de mayorista de 2 de las 40 filas están desactualizadas a propósito**,
porque el golden nació con `precio_mayorista = price − descuento` y desde v18 la base es
`list_price`. El criterio cierra hoy en 38/40. Cuáles son esas dos filas y por qué el archivo no se
regenera lo fija la nota de `docs/historia/decisiones_1.1.0.md` §10, que es la misma única fuente de arriba.

## fichas_publicadas_20260822.csv

23 fichas de producto del storefront, capturadas **a mano** el 2026-08-22.

Es la única verdad EXTERNA del repo: todo lo demás sale de la misma API VTEX que el motor está
midiendo, así que no puede delatar un error de interpretación de esa API. Éstas sí — son lo que
Makro le muestra a un cliente.

Las consume `tests/makro_plazavea/test_precio_mayorista.py`, que exige 23/23. Es la evidencia que
cerró la corrección de v18: la fórmula vieja acierta 11/23 y la corregida 23/23. Ese margen es la
razón de ser del archivo, y por eso el conteo no se relaja.
