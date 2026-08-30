# Los Bodegueros

Espacio del rumbo nuevo, abierto el **2026-08-30**. Hoy está vacío a propósito: marca el lugar,
todavía no la solución.

## Qué es

Dar a **bodegueros y minimarkets** información de precios de Makro Perú para que **compren más
barato** y **descubran oportunidades de surtido**. Es la Capa 1 de una plataforma de retail peruano
más grande; el foco inmediato es ese nicho y nada más.

## Qué cambia y qué no

**Cambia el destinatario.** Hasta ahora el motor se construyó para "un analista de pricing" genérico,
sin cliente definido. Esa etapa se cerró: su registro está en `docs/historia/`.

**No cambia el motor.** `src/retail_engine/collectors/makro_plazavea.py` es la base del rumbo nuevo,
no lastre de la etapa vieja. Con sus principios intactos, empezando por el que más cuesta respetar:
**Nivel 1 correctitud antes que Nivel 2 cobertura**. Un dataset más grande con atribución de sucursal
difusa vale menos que uno chico en el que se puede confiar. Eso no se relaja porque ahora haya cliente.

## Qué del motor ya sirve directo a este producto

Vale conocerlo antes de proponer construir algo nuevo:

- **`precio_mayorista` y las 22 columnas del bi-precio** — es, literalmente, "que compren barato".
  Ojo con la epistemología: a `qty=1` el descuento no se aplica, así que el precio mayorista está
  **reconstruido por resta**, no observado. `precio_mayorista_verificado = NO` es la respuesta honesta
  por defecto, no un hueco. Ver `CLAUDE.md`, "the wholesale price is reconstructed, not observed".
- **`stock_signal = SIN_STOCK_LOCAL_CADENA_CON_STOCK`** — es, literalmente, "oportunidad de surtido":
  la sucursal no lo tiene y la cadena sí. Demanda real, quiebre visible.
- **`docs/columnas.md`** — qué dice cada una de las 79 celdas y qué significa que esté vacía.
  Vacío nunca es cero.

## Regla que este producto hereda y no puede romper

El motor **nunca compara sucursales ni descarta filas**. La comparación es aguas abajo. Un producto
para bodegueros va a querer justamente comparar y rankear — eso se construye **sobre** el CSV largo,
no metiéndole lógica de comparación al extractor. A 2 sucursales o a 20.
