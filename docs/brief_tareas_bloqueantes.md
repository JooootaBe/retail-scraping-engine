# Brief — las dos correcciones que no son retroactivas

Para pasar a Claude Code. Contexto: `retail_scraping_engine`, colector `makro_plazavea`,
después de la corrección de la fórmula del mayorista (`SCHEMA_VERSION 5`).

**Por qué estas dos y no las otras.** Como `raw.jsonl.gz` archiva la evidencia cruda, casi todos
los defectos del motor se reparan hacia atrás: se arregla el código, se recalcula la serie y los
días viejos quedan bien. El bug de la fórmula lo demostró — seis semanas equivocado, costo de
reparación cero.

Estas dos no. Cada corrida que se haga sin ellas produce un día que **no se puede reparar
después**, y la serie temporal es el activo irreemplazable del proyecto.

---

## TAREA A — el precio en quiebre

### El hecho medido

En `run_20260822_020027`, las 143 filas con `availability = withoutStock` tienen
`price == list_price` y `discount_pct = 0.00`. Las 143, sin una sola excepción.

Entre las filas con stock, el 19.8% trae promoción. Si el quiebre fuera independiente de la
promoción esperaríamos ~28 de esas 143 con descuento; salieron cero. Bajo independencia eso tiene
probabilidad del orden de 10⁻¹⁴.

**VTEX no evalúa promociones cuando no hay stock en el nodo y devuelve el precio de lista.** El
storefront hace lo mismo: muestra el precio de lista en gris, sin botón de compra. Verificado a
mano en CHIZITOS (Santa Anita) y en Aceite de Oliva ARO 2L (Surco).

### Por qué se volvió urgente hoy

La corrección de la fórmula introdujo la regla `list_price − descuento >= price → no se publica`.

En una fila en quiebre `price == list_price`, así que `list − descuento` queda **siempre** por
debajo de `price` mientras el descuento sea positivo. Medido sobre la corrida: de las 82 filas en
quiebre con `biprecio_status = COMPLETO`, **82 se marcarían como "escalón publicado" y 0 como
suprimido** — por construcción aritmética, sin haber medido nada.

Antes de hoy el defecto era pasivo. Ahora es un falso positivo sistemático con dirección conocida.

### Qué cambiar

**1. Columna nueva `price_origin`**

- `MEDIDO` — hubo stock y la simulación evaluó promociones
- `LISTA_SIN_PROMO` — no hubo stock; `price` es el precio de lista, no una oferta

El `price` **se conserva** (regla del proyecto: los precios nunca se descartan). Lo que se agrega
es la procedencia, que hoy no está en ningún lado.

**2. `discount_pct` vacío cuando `price_origin = LISTA_SIN_PROMO`**

Hoy dice `0.00`, que afirma "este producto no tiene descuento". Eso no se midió: no se evaluó
ninguna promoción. Vacío ≠ cero (regla §6.2).

**3. El bi-precio en filas sin stock — el punto delicado**

Hay que separar dos cosas que la fórmula nueva mezcla:

- `precio_mayorista = list_price − descuento` **sigue siendo válido**. No depende del stock:
  `list_price` es dato de catálogo y el descuento viene del teaser.
- La regla de publicación (`>= price`) **no se puede evaluar**, porque `price` no es el precio
  real de oferta.

Entonces: calcular el mayorista, y **no afirmar** si se publica o no. Estado nuevo
`BIPRECIO_PUBLICACION_INDETERMINADA`, o el flag `DQ_SIN_PRECIO_UNITARIO_MEDIDO` — pero en ningún
caso dejar que caiga en "publicado" por defecto.

Es exactamente la regla del proyecto sobre conservar la ambigüedad como ambigüedad: se sabe el
umbral, se sabe el descuento, no se sabe si el escalón está vigente.

**4. `stock_signal`**

Hoy las 143 llevan `QUIEBRE_LOCAL`, que afirma que es temporal. Puede ser que el SKU no esté en el
surtido de ese nodo. Se verificó un solo caso a mano (SKU 11566889: la búsqueda en Surco lo
devuelve, así que sí es quiebre). Un caso, no 143.

No hace falta resolverlo ahora — la serie lo resuelve sola: un SKU que nunca aparece con stock en
un nodo durante 30 días es, con alta probabilidad, ausencia de surtido. Pero conviene que el
nombre no afirme más de lo medido.

**5. Nota al margen: `surtido_makro` es redundante**

Es 100% colineal con `availability`: 3031 `SI`/`available` y 143 `NO`/`withoutStock`, cero
excepciones. Lee `seller_chain`, que viene vacío justamente cuando no hay logística que resolver.
No mide surtido, mide disponibilidad con otro nombre. No urge, pero documentarlo.

### Test

Un caso por cada rama, con filas construidas a mano:

- fila con stock y promo → `price_origin = MEDIDO`, `discount_pct` con valor
- fila sin stock → `price_origin = LISTA_SIN_PROMO`, `discount_pct` vacío
- fila sin stock con bi-precio → mayorista calculado, publicación **no** afirmada
- que ninguna fila sin stock salga marcada como escalón publicado

---

## TAREA B — el crudo del catálogo

### El hecho

`guardar_evidencia` solo se invoca sobre respuestas de medición. En `run_20260822_020027`:
3174 `simulation`, 158 `orderform_auditoria`, 3 `simulation_qtyN`. **Cero respuestas de catálogo.**

La cadena `CantidadBiPrecioMK` aparece **0 veces** en `raw.jsonl.gz`.

### Por qué importa

La fase de descubrimiento es la única fuente de `CantidadBiPrecioMK`, el teaser del descuento y
los metadatos de campaña. Nada de eso queda archivado.

El único registro de esos campos es `filas.csv`, y el CSV les aplica la regla §6.2, que los borra.
Resultado concreto y ya verificado: el umbral de los SKUs 10907796, 1062 y 10907803 **no existe en
ninguna fuente local**. El sistema observó un umbral, no lo archivó, y después lo destruyó.

Consecuencia estructural: **el CSV no puede reproducir su propio veredicto.**
`biprecio_status = SIN_DESCUENTO` afirma que hubo un umbral declarado, y la misma fila lo vació.
Nadie puede reconstruir esa decisión leyendo la salida del motor.

### Qué cambiar

Que la fase de descubrimiento escriba también a `raw.jsonl.gz`, con su propio tipo de registro
(`"catalogo"`) para poder filtrarlo de las respuestas de medición.

Estimación de volumen: 123 requests de descubrimiento contra 3174 de medición. El costo en disco
es marginal.

**No es recuperable hacia atrás.** La corrida del 22 ya perdió ese crudo. Aplica de la próxima en
adelante, y por eso va antes de empezar a acumular días.

### Propiedad que se gana

Con esto, la salida del motor pasa a ser reconstruible desde el crudo. Vale la pena dejarlo como
test de propiedad más adelante: **round-trip** — el crudo archivado debe contener lo suficiente
para regenerar el CSV y sus veredictos. Hoy no se cumple.

---

## Orden

1. TAREA B primero. Es más simple y no toca lógica de negocio.
2. TAREA A después, con sus tests.
3. Recién ahí, la primera corrida de la serie.

## Fuera de alcance

Van aparte, sin apuro, porque **sí** se reparan hacia atrás recalculando desde el crudo:

- Auditoría del mayorista: esperar `price` cuando el escalón está suprimido; estratificar la
  muestra por el eje "con promo / sin promo"; propagar el veredicto al nodo gemelo
- Falso positivo de truncamiento (categoría con total múltiplo exacto de `VENTANA`)
- Parser de presentación: 8 SKUs donde ignora el conteo (`Caja 20un x 24g` → 24g en vez de 480g)
- Regenerar `golden_v5.csv` con `SCHEMA_VERSION 5` (2 filas quedaron mal: SKU 10926867)
- Sonda `precio_mayorista_corregidov6.py`
