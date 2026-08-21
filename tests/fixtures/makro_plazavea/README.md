# Fixtures — makro_plazavea

## golden_v5.csv

20 SKUs × 2 nodos (359 Santa Anita, 360 Surco) = 40 filas, 78 columnas.
Formato largo, clave (run_id, node_id, sku_id).
Corrida de la sonda v5, 20260820_000016.
Auditoría del precio mayorista: 3/3 exactas al centavo.

El criterio de aceptación de 1.1.0 contra este golden —qué columnas deben coincidir exacto y
cuáles se excluyen por volátiles o por cambio de diseño— lo fija `docs/decisiones_1.1.0.md` §10.
Es la única fuente: no repetirlo acá, dos copias divergen.
No reemplazar este archivo sin dejar constancia en CHANGELOG.md.
