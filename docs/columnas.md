# Diccionario de columnas — `filas.csv`

> `SCHEMA_VERSION` **6** · **79 columnas** · motor `makro_plazavea.py` **VERSION 2026.08.25-23** (v23)
>
> Todos los ejemplos salen de **`run_20260826_021034`**: 3.162 filas (1.581 SKUs × 2 nodos),
> modo `simulation`, alcance `--categoria /431/` (Abarrotes), 83 categorías recorridas,
> corrida del 2026-08-26. Los conteos que aparecen en cada entrada están medidos sobre esa
> corrida y **solo sobre ella** — una corrida con otro alcance puede llenar columnas que acá
> salen vacías.

Este documento es autoritativo sobre **una sola cosa: qué significa cada celda**. La mecánica del
bi-precio vive en `docs/decisiones_1.1.0.md` §1 y §5; las razones de diseño del motor viven en
`CLAUDE.md`; la historia de cada cambio vive en `CHANGELOG.md` y en el bloque `CAMBIOS` del propio
motor. Acá se cita, no se copia.

Tres reglas del proyecto que se aplican a la lectura de cualquier celda:

1. **Vacío nunca es cero.** Una celda vacía dice *no se sabe*. Un `0.00` dice *se midió y dio
   cero*. En los campos monetarios y de porcentaje esa distinción es el dato.
2. **Ninguna fila se descarta.** Una fila con `price_status = UNVERIFIED` es señal, no basura.
3. **Un cambio de significado es un cambio de esquema aunque la cabecera no cambie.** Las columnas
   marcadas con ⚠ **cambiaron de definición** entre versiones sin que el CSV se viera distinto.
   Antes de apilar corridas de fechas distintas, mirá `schema_version` y esas entradas.

Columnas con ⚠ (significado cambiado, no agregado): `discount_pct`, `chain_stock`, `stock_signal`,
`bi_umbral`, `precio_mayorista`, `precio_mayorista_cents`, `descuento_mayorista_pct`,
`precio_mayorista_por_unidad_base`, `precio_mayorista_verificado`.

---

## IDENTIDAD

### run_id
- **Qué responde** — de qué corrida salió esta fila.
- **Por qué existe** — la serie se lee como un glob sobre `run_*/filas.csv`. Sin el identificador
  dentro de la fila, una vez concatenadas todas las corridas no hay forma de volver a separarlas ni
  de encontrar el `run.json` que las explica. Es la mitad del par que hace la fila auto-explicativa.
- **Valores posibles** — `run_YYYYMMDD_HHMMSS`, el mismo nombre de la carpeta que contiene el CSV.
  Un solo valor por archivo.
- **Vacía** — nunca en un CSV escrito por el motor. `RUN_ID` se fija al arrancar `main()`; una fila
  con este campo vacío fue construida fuera de una corrida (un test, un replay).
- **Ejemplo** — `run_20260826_021034`.
- **Dónde vive** — `makro_plazavea.py:6103` (se fija), `:4751` (se escribe en la fila).

### schema_version
- **Qué responde** — bajo qué versión de esquema se evaluó esta fila.
- **Por qué existe** — es la otra mitad del par. Hasta 1.0.0 un guardián renombraba el CSV viejo
  cuando cambiaba la cabecera; con una carpeta inmutable por corrida ese guardián desapareció y su
  trabajo lo hace esta columna. Es lo único que le dice a una capa de consolidación qué corridas
  puede unir con `UNION` (`CLAUDE.md`, "un cambio de esquema no debe corromper la historia").
- **Valores posibles** — `"3"` (v13/v14), `"4"` (v15, 78 columnas), `"5"` (v18, mismas 78 columnas
  con `precio_mayorista` redefinido), `"6"` (v20 a v23, 79 columnas), `"7"` (v24 en adelante,
  mismas 79 columnas con `descuento_monto` / `precio_mayorista` **descontaminados**). Hoy siempre
  `7`. El bump de v24 es el tercer motivo distinto por el que esta columna se movió: v15 cambió la
  **forma**, v18 y v20 el **significado**, y v24 el **valor** — la cabecera y la definición quedan
  idénticas, pero hasta v23 esas dos columnas podían traer el descuento de un teaser condicionado
  a tarjeta en vez del bi-precio. Dos poblaciones bajo el mismo nombre, y sin este bump nada las
  separa.
- **Vacía** — nunca: tiene default de clase.
- **Ejemplo** — `7`.
- **Dónde vive** — `makro_plazavea.py:198` (constante), `:941` (default del campo).

### timestamp
- **Qué responde** — el instante exacto en que se midió este SKU en esta sucursal.
- **Por qué existe** — una corrida completa dura horas (ésta, 5.783 s = 1h36) y los precios pueden
  moverse dentro de ella. `fecha` no alcanza para ordenar dentro de una corrida ni para cruzar
  contra una promoción que arrancó a media mañana.
- **Valores posibles** — ISO-8601 con offset, resolución de segundo, hora local de Lima (`-05:00`).
- **Vacía** — nunca por el camino normal: se fija en la construcción de toda `Fila`, incluidas las
  de error y las de `SKU_NO_ENCONTRADO`.
- **Ejemplo** — `2026-08-26T02:15:25-05:00`. 3.156 valores distintos en 3.162 filas: el motor es
  estrictamente secuencial, una request cada 1,5 s.
- **Dónde vive** — `makro_plazavea.py:4734`, `:2678`, `:5141`.

### fecha
- **Qué responde** — el día de la medición.
- **Por qué existe** — conveniencia de agrupación. Es `timestamp` truncado; existe para no obligar
  a un `substr()` en cada `GROUP BY` de la serie diaria.
- **Valores posibles** — `YYYY-MM-DD`.
- **Vacía** — nunca. Una corrida que cruce la medianoche produciría dos valores en un mismo
  `run_id`; en esta corrida hay uno solo.
- **Ejemplo** — `2026-08-26`.
- **Dónde vive** — `makro_plazavea.py:4735`.

### retailer
- **Qué responde** — de qué retailer es el precio.
- **Por qué existe** — el nombre de la carpeta identifica al *colector* (`makro_plazavea`), no al
  retailer. `RETAILER` viaja dentro de la fila para que un `UNION` de varios colectores siga
  sabiendo de quién es cada precio. Constante hoy, y deliberadamente una columna y no un supuesto.
- **Valores posibles** — `Makro`. Único valor que este colector puede producir.
- **Vacía** — nunca.
- **Ejemplo** — `Makro`.
- **Dónde vive** — `makro_plazavea.py:752` (constante), `:944` (default).

### branch
- **Qué responde** — el nombre legible de la sucursal que se estaba midiendo.
- **Por qué existe** — etiqueta humana de `node_id`, para no tener que memorizar que 359 es Santa
  Anita. **Es la sucursal que se PREGUNTÓ, no la que respondió** — eso lo dice `node_resolved`.
- **Valores posibles** — hoy `Santa Anita`, `Surco`. Crece con cada entrada de `NODOS`.
- **Vacía** — nunca: siempre se mide contra un nodo concreto.
- **Ejemplo** — `Santa Anita` (1.581 filas), `Surco` (1.581).
- **Dónde vive** — `makro_plazavea.py:4736`, desde `Nodo.branch` (`:790`).

### node_id
- **Qué responde** — el código de la sucursal preguntada.
- **Por qué existe** — es **la clave que hace que agregar una sucursal no cambie el esquema**. La
  salida es un CSV largo con clave `(run_id, node_id, sku_id)`: la sucursal 21 agrega valores a esta
  columna, no un archivo ni una columna nueva (`docs/decisiones_1.1.0.md` §8).
- **Valores posibles** — las claves de `NODOS`: hoy `359`, `360`.
- **Vacía** — nunca.
- **Ejemplo** — `359`.
- **Dónde vive** — `makro_plazavea.py:4737`.

---

## PRODUCTO

### product_id
- **Qué responde** — qué producto de VTEX es, en el nivel de *producto* (no de variante).
- **Por qué existe** — es la clave con la que se pide el stock de cadena (`fq=productId:`) y con la
  que se agrupan variantes del mismo producto. `sku_id` es la variante; ésta es el padre.
- **Valores posibles** — numérico como texto.
- **Vacía** — en una fila `SKU_NO_ENCONTRADO` (`--skus` pidió un SKU y el catálogo no lo devolvió):
  ahí no hubo producto que nombrar. Cero veces en esta corrida.
- **Ejemplo** — `99922606`.
- **Dónde vive** — `makro_plazavea.py:4738`, desde `parsear_producto` (`:1684`).

### sku_id
- **Qué responde** — qué SKU exacto se midió.
- **Por qué existe** — es el eje del producto en la clave de la serie, y el único identificador que
  el motor usa para *verificar que VTEX devolvió lo que se pidió* (`DQ_SKU_DISTINTO`).
- **Valores posibles** — numérico como texto.
- **Vacía** — nunca: sin SKU no hay medición que registrar.
- **Ejemplo** — `10012678`. 1.581 distintos, cada uno en 2 filas (una por nodo).
- **Dónde vive** — `makro_plazavea.py:4739`.

### sku_ref
- **Qué responde** — el código de referencia interno que Makro le puso al SKU.
- **Por qué existe** — **no hay razón documentada.** Sale de `items[].referenceId[].Value` en el
  catálogo, se escribe y nada del motor lo lee después. Existe desde v01, ningún `CAMBIOS` lo
  menciona y `docs/decisiones_1.1.0.md` tampoco. Es plausible que sea el código de góndola o de ERP
  —los valores de esta corrida son todos `2014xxxx`/`2017xxxx`, un rango propio distinto del
  `sku_id`— pero **eso es una hipótesis, no algo que el código diga**. Ver "Columnas cuyo propósito
  no pude determinar".
- **Valores posibles** — numérico como texto; el primer `referenceId` con valor.
- **Vacía** — cuando el producto no trae ningún `referenceId` con `Value`. Cero veces acá.
- **Ejemplo** — `20148211` (SKU 10012678).
- **Dónde vive** — `makro_plazavea.py:1664-1669` (se lee), `:4740` (se escribe).

### ean
- **Qué responde** — el código de barras del producto, tal como lo publica el catálogo.
- **Por qué existe** — es el único identificador que puede cruzar este catálogo contra el de otro
  retailer. Se guarda **crudo**, sin juicio: el juicio va en `ean_type`.
- **Valores posibles** — dígitos, largo variable. No está validado ni normalizado acá.
- **Vacía** — cuando el catálogo no publica EAN para ese SKU. **734 de 3.162 filas (23%)** en esta
  corrida; el vacío es exactamente el conjunto `ean_type = FALTANTE`.
- **Ejemplo** — `7750106000024` (Galleta Animalitos SAN JORGE 1 kg).
- **Dónde vive** — `makro_plazavea.py:1687` (se lee), `:4741` (se escribe).

### product_name
- **Qué responde** — cómo se llama el producto en el storefront.
- **Por qué existe** — legibilidad, y además es **insumo de una regla**: cuando el producto es
  envasado (`measurement_unit = un`), el tamaño del empaque solo vive en el texto del nombre y
  `resolver_presentacion` lo parsea de ahí. No es decorativo.
- **Valores posibles** — texto libre de VTEX.
- **Vacía** — solo en una fila `SKU_NO_ENCONTRADO`. Si el catálogo no lo trajo pero la simulación
  sí, se rellena desde el ítem de la respuesta (`:4795-4796`).
- **Ejemplo** — `Aceite de Oliva HUERTO ALAMEIN Extra Virgen Botella 2L`.
- **Dónde vive** — `makro_plazavea.py:4742` y `:4795`.

### brand
- **Qué responde** — la marca declarada por el catálogo.
- **Por qué existe** — eje de análisis de pricing: marca propia (`ARO`, 402 filas acá) contra marca
  de proveedor es la comparación que sostiene media estrategia de surtido. Sin la columna hay que
  inferirla del nombre.
- **Valores posibles** — texto libre; 256 distintos en esta corrida.
- **Vacía** — cuando el catálogo no declara marca, o en `SKU_NO_ENCONTRADO`. Cero veces acá.
- **Ejemplo** — `ARO`.
- **Dónde vive** — `makro_plazavea.py:4743`.

### category
- **Qué responde** — la ruta de categorías legible del producto.
- **Por qué existe** — es la versión humana de `category_id`. Las dos conviven a propósito: **VTEX
  renombra categorías sin avisar**, así que el texto sirve para leer y el id para agrupar
  (`:1673-1676`).
- **Valores posibles** — la primera ruta de `categories`, con `/` cambiado por ` > `.
- **Vacía** — si el producto no trae `categories`. Cero veces acá.
- **Ejemplo** — `Abarrotes > Galletas y Golosinas > Galletas Dulces` (284 filas).
- **Dónde vive** — `makro_plazavea.py:1690-1692` (se arma), `:4744` (se escribe).

### seller_id
- **Qué responde** — qué vendedor publica la oferta en el catálogo.
- **Por qué existe** — `docs/decisiones_1.1.0.md` §6.5 lo dejó explícitamente: es campo de origen
  (`items[].sellers[0].sellerId`) y **si aparece un tercero de marketplace va a traer otro valor**.
  Hoy es constante `1` y esa constancia es el hallazgo, no un defecto.
- **Valores posibles** — el id del primer seller, o `1` por defecto si el catálogo no lo trae.
- **Vacía** — nunca: el parser cae en `"1"`. Ojo con esto: un `1` puede ser el valor real o el
  default, y la columna no los distingue.
- **Ejemplo** — `1`, en las 3.162 filas.
- **Dónde vive** — `makro_plazavea.py:1693`, `:4745`.

### url
- **Qué responde** — dónde ver la ficha del producto en el storefront.
- **Por qué existe** — verificación manual. Cuando una fila se ve rara, esto es lo que se abre en el
  navegador; las 23 fichas de `fichas_publicadas_20260822.csv` —la única verdad externa del repo—
  se capturaron así.
- **Valores posibles** — URL absoluta del storefront.
- **Vacía** — en `SKU_NO_ENCONTRADO`. Cero veces acá.
- **Ejemplo** — `https://www.makro.plazavea.com.pe/palillo-sibarita-bolsa-250g/p`.
- **Dónde vive** — `makro_plazavea.py:4746`.

---

## PRECIO

> La trampa de este bloque son los productos por peso: ahí `list_price` es el precio por kilo y
> `price` el de la pieza, así que no son comparables entre sí. El comentario del dataclass
> (`:961-977`) tiene el caso completo. En esta corrida —solo abarrotes— `unit_multiplier` es `1.0`
> en las 3.162 filas, así que la trampa no se activa; en carnes o frutas sí.

### currency
- **Qué responde** — en qué moneda están todos los números monetarios de la fila.
- **Por qué existe** — para que los precios puedan ir como número plano. Un CSV con `S/ 212.90`
  obliga a pandas a leer la columna como texto (`soles()`, `:1144-1152`). La moneda va aparte.
- **Valores posibles** — `PEN`.
- **Vacía** — nunca.
- **Ejemplo** — `PEN`.
- **Dónde vive** — `makro_plazavea.py:753` (constante), `:978` (default).

### price
- **Qué responde** — cuánto cobra VTEX hoy por una unidad de este SKU en esta sucursal.
- **Por qué existe** — es **el hecho**, y el motor lo escribe siempre que VTEX lo devuelva, aunque
  la validación logística haya fallado. Es `sellingPrice`, es decir el precio *después* de las
  promociones que la simulación evaluó. Las tres columnas que lo acompañan son deliberadamente
  separadas: el hecho (`price`), la procedencia (`price_origin`) y el juicio (`price_status`).
- **Valores posibles** — número plano con dos decimales.
- **Vacía** — cuando VTEX no devolvió el ítem: HTTP ≥ 400, `__error` en el cuerpo, excepción por
  SKU, o `SKU_NO_ENCONTRADO`. En esos casos `price_status = NO_PRICE`. Cero veces acá.
- **Ejemplo** — `88.55` (Aceite de Oliva HUERTO ALAMEIN 2L, nodo 359). Ojo: en las 132 filas sin
  stock `price` **no es una oferta**, es el precio de lista — lo dice `price_origin`.
- **Dónde vive** — `makro_plazavea.py:4761`.

### list_price
- **Qué responde** — el precio de lista (tachado) que declara VTEX.
- **Por qué existe** — es el denominador de `discount_pct` y, desde v18, **la base del precio
  mayorista**. En productos por peso además cambia de sentido: pasa a ser el precio por unidad de
  medida publicado por el servidor, y por eso `price_per_unit` lo lee en vez de dividir.
- **Valores posibles** — número plano con dos decimales.
- **Vacía** — junto con `price`, en las mismas condiciones.
- **Ejemplo** — `118.50` contra un `price` de `88.55`. En 2.740 de 3.162 filas es **igual** a
  `price` (sin promoción unitaria, o sin stock); en 422 es mayor. Nunca es menor en esta corrida
  — si lo fuera saltaría `DQ_PRECIO_MAYOR`.
- **Dónde vive** — `makro_plazavea.py:4762`.

### base_price
- **Qué responde** — el campo `price` crudo del ítem de VTEX, que no es ni el de venta ni el de
  lista sino un tercero del modelo de VTEX.
- **Por qué existe** — **no hay razón documentada de por qué se guarda.** Se escribe desde v01 y
  ningún consumidor lo lee. `docs/decisiones_1.1.0.md` §6.6 lo puso en la lista de columnas a
  medir 30 días antes de decidir si se borra, y §11 dejó su tasa de llenado como pregunta abierta.
  **Esta corrida contesta la pregunta y el resultado es incómodo**: se llena en el 100% de las
  filas y es **idéntico a `list_price` en las 3.162**, así que no aporta un solo bit. Aplica la
  misma cautela que con `surtido_makro` (`CLAUDE.md`): un solo alcance —abarrotes, dos nodos— es
  evidencia fina para borrar una columna. Ver "Columnas derivables".
- **Valores posibles** — número plano con dos decimales.
- **Vacía** — junto con `price`.
- **Ejemplo** — `118.50`, el mismo `list_price` de esa fila.
- **Dónde vive** — `makro_plazavea.py:4763`.

### price_cents
- **Qué responde** — `price` en céntimos enteros.
- **Por qué existe** — **es la forma canónica, no la conveniencia**. VTEX responde en céntimos; el
  motor hace toda la aritmética del bi-precio en enteros para no arrastrar error de coma flotante,
  y `price` en soles es lo derivado. La auditoría del mayorista compara céntimo contra céntimo.
- **Valores posibles** — entero como texto.
- **Vacía** — junto con `price`.
- **Ejemplo** — `8855`.
- **Dónde vive** — `makro_plazavea.py:4764`.

### discount_pct ⚠
- **Qué responde** — qué porcentaje de descuento unitario tiene hoy este SKU en esta sucursal.
- **Por qué existe** — `(list_price − selling_price) / list_price × 100`. Se extrajo a una función
  pura para poder probarla sin red; la regla no cambió al extraerla.
- **⚠ Cambio de significado (v20, `SCHEMA_VERSION` 5 → 6)** — antes, una fila sin stock traía
  `0.00`. Ese cero afirmaba *"este producto no tiene descuento"* sobre una promoción que **nadie
  evaluó**: VTEX no evalúa promociones cuando el nodo no tiene stock. Desde v20 esa celda va
  **vacía**. Un `0.00` de una corrida vieja y uno de hoy no quieren decir lo mismo, y la cabecera
  no lo dice. Ver `CHANGELOG.md` `[Sin publicar]` → `price_origin`.
- **Valores posibles** — `0.00` a `100.00` con dos decimales, o vacío.
- **Vacía y qué significa** — tres causas distintas:
  1. `price_origin = LISTA_SIN_PROMO` (sin stock): **no se evaluó ninguna promoción**. 132 filas acá.
  2. `unit_multiplier != 1` (producto por peso): `list_price` y `price` están en bases distintas y
     restarlos daría un descuento inventado. Cero filas acá (abarrotes).
  3. Falta alguno de los dos precios, o `list_price < price`.
- **Ejemplo** — `25.27` (HUERTO ALAMEIN 2L). `0.00` en 2.608 filas: ahí sí hubo stock, sí se
  evaluó y no había promoción — es una medición, no un relleno.
- **Dónde vive** — `makro_plazavea.py:4785`, regla en `calcular_descuento_pct` (`:3570`).

### measurement_unit
- **Qué responde** — en qué unidad vende VTEX este ítem.
- **Por qué existe** — es el interruptor entre las dos ramas de `resolver_presentacion`: `un`
  significa producto envasado (y el tamaño hay que sacarlo del nombre), cualquier otra cosa
  significa peso/volumen variable y entonces VTEX es autoritativo sobre el tamaño de la pieza.
- **Valores posibles** — lo que devuelva VTEX: `un`, `kg`, `g`, `l`... El motor solo distingue `un`
  de "no `un`", y traduce las demás con la tabla `UNIDADES`.
- **Vacía** — si el ítem no vino o no declara la unidad.
- **Ejemplo** — `un` en las 3.162 filas de esta corrida.
- **Dónde vive** — `makro_plazavea.py:4766`.

### unit_multiplier
- **Qué responde** — cuánto mide/pesa la pieza que se está cotizando, en `measurement_unit`.
- **Por qué existe** — es el número que hace comparable un precio por pieza contra un precio por
  kilo. Sin él, 152.55 contra 33.90 parece "cuatro veces más caro" y es una pieza de 4,5 kg contra
  un kilo. También es el guardián de `discount_pct` y de `price_per_unit`.
- **Valores posibles** — decimal como texto; `1.0` en producto envasado.
- **Vacía** — si el ítem no vino.
- **Ejemplo** — `1.0` en las 3.162 filas.
- **Dónde vive** — `makro_plazavea.py:4767`.

### price_per_unit
- **Qué responde** — cuánto cuesta una unidad de medida (un kilo, un litro) de este producto.
- **Por qué existe** — es **la columna con la que se compara contra otro retailer**. Tiene dos
  ramas y la segunda es la interesante: con `unit_multiplier != 1` **no se divide**, se lee
  `list_price`, porque dividir amplifica el redondeo de VTEX (Maracuyá: 1.23 ÷ 0.2 = 6.15 cuando
  el kilo real es 6.19). La división se conserva solo como control, y si difiere más de lo que el
  peso promedio declarado explica, la fila sale marcada `DQ_UNIDAD_INCONSISTENTE`.
- **Valores posibles** — número con **cuatro** decimales.
- **Vacía** — si falta el precio, si el multiplicador es ≤ 0, o si el precio de venta es ≤ 0.
- **Ejemplo** — `6.5000`. Con `unit_multiplier = 1` es exactamente `price`, que es lo esperado.
- **Dónde vive** — `makro_plazavea.py:4772`, regla en `calcular_precio_por_unidad` (`:4016`).

---

## DISPONIBILIDAD Y ENTREGA

> Dos niveles de stock que responden preguntas distintas y **no tienen por qué coincidir**:
> `availability` viene del checkout con la dirección de la sucursal; `chain_stock` viene del
> catálogo sin contexto de sucursal.

### availability
- **Qué responde** — ¿esta sucursal me lo puede despachar hoy?
- **Por qué existe** — es el estado que VTEX declara para el ítem en el checkout, y es **la causa
  de la que cuelgan otras cuatro columnas**: `price_origin`, `discount_pct`, `stock_signal` y el
  veredicto `SIN_STOCK` de `logistics_status`. Se sube temprano en `construir_fila` justamente
  porque sin saber si hubo stock no se puede decir si `price` es una oferta.
- **Valores posibles** — lo que VTEX devuelva. Conocidos: `available`, `withoutStock`,
  `cannotBeDelivered`. El motor no cierra la lista: cualquier estado nuevo se registra tal cual y
  produce `logistics_status = NO_DISPONIBLE_<estado>`.
- **Vacía** — cuando no vino el ítem (HTTP ≥ 400, `__error`, excepción, `SKU_NO_ENCONTRADO`).
- **Ejemplo** — `available` (3.030 filas), `withoutStock` (132). `cannotBeDelivered` **no apareció**
  en la medición de esta corrida, aunque sí en las respuestas de orderForm de la auditoría — ver
  `recon_status`.
- **Dónde vive** — `makro_plazavea.py:4781`.

### chain_stock ⚠
- **Qué responde** — cuántas unidades le quedan a **la cadena entera**, sin mirar sucursal.
- **Por qué existe** — es la mitad que convierte un quiebre local en una oportunidad comercial: el
  producto existe, la cadena lo tiene, esta tienda no. Se refresca por lotes en pocas requests
  (`fq=productId:`, 40 por consulta), no una por SKU.
- **⚠ Cambio de significado (v08, y definitivo en v11)** — hasta v07 este número salía de
  `panel.json`, una foto tomada al crear el panel y **nunca refrescada**: en la corrida del 13-ago,
  0 de 100 SKUs cambiaron entre corridas. Parecía stock vivo y era historia. Una fila anterior a
  v08 con esta columna llena no está diciendo lo que dice hoy.
- **Valores posibles** — entero como texto. Es `AvailableQuantity` del canal, **no de la sucursal**:
  dice cuánto queda, no dónde está.
- **Vacía y qué significa** — el SKU no volvió del catálogo en la fase de stock de cadena. Puede
  ser una baja del catálogo o un lote que falló. **Se deja vacío, nunca en cero**: cero afirmaría
  que no queda nada. Cero veces acá — los 1.581 SKUs volvieron.
- **Ejemplo** — `13` (HUERTO ALAMEIN 2L), `8639` (Aceite de Soya SOYA 900 ml).
- **Dónde vive** — `makro_plazavea.py:4747`, fuente en `refrescar_stock_cadena` (`:3112`).

### stock_signal ⚠
- **Qué responde** — cruzando los dos niveles de stock, ¿qué está pasando con este SKU acá?
- **Por qué existe** — para no obligar a cada consulta a rehacer el cruce. El valor que vale dinero
  es `SIN_STOCK_LOCAL_CADENA_CON_STOCK`: demanda real, quiebre visible, comparable contra el
  competidor.
- **⚠ Cambio de nombre de los valores (v20)** — eran `QUIEBRE_LOCAL`, `QUIEBRE_CADENA` y
  `QUIEBRE_LOCAL_CADENA_DESCONOCIDA`. "Quiebre" **afirma** un desabastecimiento temporal de algo
  que la sucursal normalmente vende, y una foto de un día no distingue eso de que el SKU
  simplemente no esté en el surtido de esa tienda. Los nombres nuevos describen lo observado y
  dejan la causa sin afirmar. Una corrida anterior a v20 trae los literales viejos: un `WHERE
  stock_signal = 'SIN_STOCK_LOCAL_CADENA_CON_STOCK'` no los va a encontrar.
- **Valores posibles** — `DISPONIBLE`, `SIN_STOCK_LOCAL_CADENA_CON_STOCK`, `SIN_STOCK_CADENA`,
  `SIN_STOCK_LOCAL_CADENA_DESCONOCIDA`. Los dos últimos **no aparecieron** en esta corrida.
- **Vacía** — cuando `availability` está vacío: sin saber si la sucursal lo tiene, no hay señal que
  calcular.
- **Ejemplo** — `SIN_STOCK_LOCAL_CADENA_CON_STOCK` (132 filas; p. ej. Aceituna Negra OLIVALLE 1 kg
  en Surco, sin stock local y 49 unidades en la cadena).
- **Dónde vive** — `makro_plazavea.py:4792`, regla en `calcular_stock_signal` (`:3212`).

### delivery_channel
- **Qué responde** — por qué canal se entrega (domicilio, retiro en tienda).
- **Por qué existe** — parte del bloque logístico que se conserva para poder auditar el despacho.
  Un precio de retiro en tienda y uno de delivery no son la misma oferta.
- **Valores posibles** — lo que declare el SLA (`deliveryChannel`) o, si falta, el
  `selectedDeliveryChannel` del bloque. Hoy solo se observó `delivery`.
- **Vacía** — cuando no hubo bloque logístico, es decir cuando no hubo SLA: sin stock, sin
  cobertura, o error. 132 filas acá, exactamente las de `withoutStock`.
- **Ejemplo** — `delivery`.
- **Dónde vive** — `makro_plazavea.py:4805`.

### shipping_cost
- **Qué responde** — cuánto cuesta el envío de ese SLA.
- **Por qué existe** — el costo de entrega es parte del precio que el cliente paga y cambia entre
  sucursales y zonas. Se guarda en soles planos, como el resto.
- **Valores posibles** — número plano con dos decimales.
- **Vacía** — sin bloque logístico. Mismas 132 filas.
- **Ejemplo** — `19.90` en 3.028 filas, `30.00` en 2 (Goma de Mascar BUBBALOO caja 70 un, en ambos
  nodos, con `shipping_estimate = 14h` en vez de `0d`). Ese par es la única variación de tarifa de
  la corrida y merece mirarse.
- **Dónde vive** — `makro_plazavea.py:4806`.

### shipping_estimate
- **Qué responde** — en cuánto tiempo llega, según VTEX.
- **Por qué existe** — mismo motivo que el costo: es parte de la oferta, y un cambio de `0d` a
  `14h` en un SKU suelto es una anomalía que conviene poder ver.
- **Valores posibles** — el formato de VTEX (`0d`, `14h`, `3bd`...). Se guarda crudo, sin parsear.
- **Vacía** — sin bloque logístico. Mismas 132 filas.
- **Ejemplo** — `0d` (3.028), `14h` (2).
- **Dónde vive** — `makro_plazavea.py:4807`.

### sla_name
- **Qué responde** — cómo se llama el método de despacho que se usó para cotizar.
- **Por qué existe** — identifica *cuál* de las opciones ofrecidas es la que produjo esta fila.
  Es lo que se compara contra `sla_selected` para decidir `fulfillment_confirmed`.
- **Valores posibles** — el `name` del SLA, o su `id` si no tiene nombre.
- **Vacía** — sin bloque logístico. Mismas 132 filas.
- **Ejemplo** — `Despacho a Domicilio Makro Lima Moderna`, en las 3.030 filas con logística.
- **Dónde vive** — `makro_plazavea.py:4804`.

---

## VALIDACIÓN

> Este bloque es el que justifica el proyecto: **identificar la sucursal, no suponerla**. La
> sucursal no se infiere del código postal que se envió; se lee de la firma logística que VTEX
> devolvió y se busca en `NODOS`. `CLAUDE.md` tiene el principio completo.

### node_resolved
- **Qué responde** — qué sucursal contestó de verdad.
- **Por qué existe** — es la contracara de `node_id`. Preguntamos por 359; si VTEX resolvió otra
  cosa, la fila **lo dice** en vez de etiquetar mal el dato. `identificar_nodo` recorre `NODOS`
  comparando `warehouseId` + `dockId` + `courierId` + `courierName`: es una búsqueda ("¿de quién es
  esta firma?"), no una confirmación de lo que esperábamos. Eso es lo que hace que pasar de 2 a 20
  sucursales sea gratis.
- **Valores posibles** — un `node_id` de `NODOS` (`359`, `360`), `OTHER` (firma completa que no es
  de ninguna sucursal conocida), `NONE` (no hubo logística que leer).
- **Vacía** — nunca: el default de clase es `NONE`, no vacío. Un `NONE` con precio significa que
  VTEX cotizó sin ofrecer despacho — 132 filas acá, todas sin stock.
- **Ejemplo** — `359` (1.524), `360` (1.506), `NONE` (132). `OTHER` **no apareció**.
- **Dónde vive** — `makro_plazavea.py:4875`, regla en `identificar_nodo` (`:4307`).

### logistics_status
- **Qué responde** — **por qué** el precio quedó como quedó: el diagnóstico logístico de la fila.
- **Por qué existe** — para que un precio no atribuible siga existiendo con su explicación al lado.
  Sin esta columna, "no hay dato" y "hay dato de otro origen" se ven igual.
- **Valores posibles** — todo lo que el código puede producir:
  `MATCH`, `MATCH_SELLER_RAIZ`, `MATCH_SIN_CONFIRMAR`, `MISMATCH_RESOLVED_<node_id>`,
  `OPERADOR_EXTERNO`, `SIN_STOCK`, `NO_COVERAGE`, `NO_DISPONIBLE_<availability>`, `NO_LOGISTICS`,
  `ITEM_NOT_RETURNED`, `HTTP_ERROR`, `EXCEPTION`, `SKU_NO_ENCONTRADO`, y `SIN_EVALUAR` como default
  de clase. **En esta corrida solo aparecieron dos: `MATCH` (3.030) y `SIN_STOCK` (132).** Los
  otros once no se observaron.
- **Vacía** — nunca; el default es `SIN_EVALUAR`, que solo sobreviviría si algo devolviera una
  `Fila` sin pasar por los veredictos.
- **Ejemplo** — `SIN_STOCK` en Aceituna Negra OLIVALLE 1 kg (Surco).
- **Dónde vive** — `makro_plazavea.py:4859-4925`.

### price_status
- **Qué responde** — **el juicio**: ¿este precio es atribuible a esta sucursal?
- **Por qué existe** — separa el hecho del juicio. Un filtro de análisis debería usar esta columna,
  no `price` a secas. `VERIFIED_SELLER_RAIZ` existe para que un `WHERE price_status = 'VERIFIED'`
  no mezcle sin querer los precios que pone el seller principal.
- **Valores posibles** — `VERIFIED` (`MATCH`), `VERIFIED_SELLER_RAIZ` (`MATCH_SELLER_RAIZ`),
  `QUALIFIED` (`MATCH_SIN_CONFIRMAR` — ni verificado ni descartado, **la ambigüedad se conserva
  como ambigüedad**), `UNVERIFIED` (hay precio, no es atribuible), `NO_PRICE` (no hay precio). En
  esta corrida solo `VERIFIED` (3.030) y `UNVERIFIED` (132); `QUALIFIED`, `VERIFIED_SELLER_RAIZ` y
  `NO_PRICE` **no aparecieron**.
- **Vacía** — nunca; el default de clase es `NO_PRICE`.
- **Ejemplo** — `UNVERIFIED` en las 132 filas sin stock: hay precio de lista, y no es atribuible.
- **Dónde vive** — `makro_plazavea.py:4928-4942`.

### fulfillment_confirmed
- **Qué responde** — ¿el despacho que se usó para cotizar es el que el cliente realmente recibiría?
- **Por qué existe** — **encontrar el almacén no es confirmar el despacho.** Hasta v08 el motor
  tomaba el primer SLA cuyo almacén coincidía y lo llamaba `MATCH`, o sea sobreinterpretaba la
  evidencia. Se confirma solo si VTEX eligió explícitamente ese SLA, o si había uno solo (y
  entonces no hay elección que confirmar).
- **Valores posibles** — `SI`, `NO`.
- **Vacía y qué significa** — no hubo bloque logístico: no hay despacho del que opinar. Es distinto
  de `NO`, que significa *hubo opciones y no se pudo confirmar cuál*. 132 filas vacías acá; `NO`
  no apareció.
- **Ejemplo** — `SI` en 3.030 filas, todas vía `SLA_UNICO`.
- **Dónde vive** — `makro_plazavea.py:4835`.

### sla_selected
- **Qué responde** — cuál SLA marcó VTEX como seleccionado.
- **Por qué existe** — es la evidencia directa de la confirmación de despacho: sin conservarla,
  "elegimos el SLA que coincide con lo que buscábamos" es sesgo de confirmación con forma de
  código (v09).
- **Valores posibles** — el nombre del SLA seleccionado.
- **Vacía y qué significa** — **100% vacía en esta corrida, y no es un camino muerto.**
  `simulation` **nunca** marca `selectedSla`: es una propiedad del endpoint, no una ambigüedad —
  verificado contra captura real, donde orderForm trae
  `selectedSla="Despacho a Domicilio Makro Lima Moderna"` y simulation lo trae nulo con el mismo
  SLA en la lista. **Se llenaría** con `--modo orderform`, o en el fallback por SKU a orderForm.
  Que esté vacía es justamente por qué un SLA único cuenta como confirmado.
- **Ejemplo** — no hay: 3.162 de 3.162 vacías.
- **Dónde vive** — `makro_plazavea.py:4813`.

### sla_count
- **Qué responde** — cuántas opciones de despacho ofreció VTEX para ese ítem.
- **Por qué existe** — es lo que convierte la ausencia de selección en algo interpretable. Con una
  sola opción, que VTEX no elija no significa nada; con tres, sí.
- **Valores posibles** — entero como texto.
- **Vacía** — sin bloque logístico. Mismas 132 filas.
- **Ejemplo** — `1` en las 3.030 filas con logística.
- **Dónde vive** — `makro_plazavea.py:4814`.

### sla_status
- **Qué responde** — en qué situación de selección quedó el SLA usado.
- **Por qué existe** — narra el porqué de `fulfillment_confirmed` en vez de dejarlo en un `SI`/`NO`
  sin contexto.
- **Valores posibles** — `SLA_SELECTED_MATCH` (VTEX eligió y es el que usamos), `SLA_UNICO` (una
  sola opción), `SLA_SELECTION_UNKNOWN_ENTRE_<n>` (varias y VTEX no eligió — el que elige es el
  motor, y hay que decirlo), `SLA_AVAILABLE_NOT_SELECTED` (VTEX eligió otro). **Solo apareció
  `SLA_UNICO`**; los otros tres no.
- **Vacía** — sin bloque logístico. Mismas 132 filas.
- **Ejemplo** — `SLA_UNICO`.
- **Dónde vive** — `makro_plazavea.py:4838-4848`.

### polygon_drift
- **Qué responde** — ¿VTEX cambió el nombre del polígono de reparto de esta sucursal?
- **Por qué existe** — `polygonName` queda **fuera** de la firma que identifica al nodo
  (`Nodo.firma_core`), porque VTEX lo versiona (`_V2`, `-V2`) sin que la sucursal cambie. Si
  entrara en la firma, un versionado rompería la identificación de todas las filas. Se excluye de
  la comparación y se registra acá.
- **Valores posibles** — `esperado=<X> observado=<Y>` cuando difieren.
- **Vacía y qué significa** — **100% vacía en esta corrida, y correcto**: el polígono devuelto es
  idéntico al registrado en `NODOS` en las 3.030 filas con logística; en las 132 sin logística no
  hay nada que comparar. **Se llenaría** el día que VTEX suba el polígono a `_V3` — y eso es
  precisamente lo que hay que enterarse sin que se rompa la atribución.
- **Ejemplo** — no hay.
- **Dónde vive** — `makro_plazavea.py:4876`, comparación en `identificar_nodo` (`:4386-4391`).

### fulfillment_type
- **Qué responde** — quién despacha realmente el producto.
- **Por qué existe** — el mismo sitio vende cosas que salen de lugares muy distintos, y meterlas
  todas en una métrica de "catálogo Makro" engaña. Caso real capturado (Crepera VENTUS):
  `warehouseId STKPRVVNT` / `dockId DCKGNRCPV-MK` — se compra en el sitio de Makro y no sale de
  Makro.
- **Valores posibles** — `tienda` (almacén y seller de la sucursal), `tienda_raiz` (almacén de la
  sucursal, seller principal), `proveedor` (dropshipping), `generico_pv` (operación genérica de
  PlazaVea), `operador_externo` (origen **plenamente identificado** y ajeno — se exige firma
  completa), `desconocido` (**reservado para lo que de verdad falta**). Solo apareció `tienda`.
- **Vacía** — sin bloque logístico: no hay de dónde clasificar. Mismas 132 filas. **Vacío no es
  `desconocido`**: `desconocido` afirma que hubo logística incompleta.
- **Ejemplo** — `tienda` (3.030).
- **Dónde vive** — `makro_plazavea.py:4877`, regla en `clasificar_fulfillment` (`:4241`).

---

## CALIDAD DE DATO

### dq_flags
- **Qué responde** — qué reglas de calidad falló esta fila.
- **Por qué existe** — cinco reglas, todas con un caso real detrás; el motor **avisa y no corrige**,
  porque cuando dos fuentes se contradicen no sabe cuál miente. `DQ_PRECIO_MAYOR` nació además de
  un falso positivo propio: marcaba 7 de 100 SKUs por confundir una diferencia de unidad (pieza vs
  kilo) con un dato mentiroso, y por eso hoy solo aplica con `unit_multiplier == 1`.
- **Valores posibles** — separados por `|`: `DQ_SKU_DISTINTO` (VTEX devolvió otro SKU: el precio es
  de otro producto), `DQ_PRECIO_MAYOR`, `DQ_PRECIO_CERO`, `DQ_UNIDAD_INCONSISTENTE`,
  `DQ_SIN_STOCK_CON_DESCUENTO` (contradice la premisa de `LISTA_SIN_PROMO`; **nunca disparó**), y
  `DQ_MAYORISTA_DISCREPA`, que **no lo pone `evaluar_calidad`** sino la auditoría del mayorista.
- **Vacía y qué significa** — la fila pasó las cinco reglas. 3.162 de 3.162 acá.
- **Ejemplo** — no hay en esta corrida. **Es la columna trampa**: en `run_20260822_020027` trajo
  `DQ_MAYORISTA_DISCREPA` en exactamente 1 fila de 3.174. Una corrida sola la muestra 100% vacía y
  un inferidor de tipos la va a tipar mal. **Tipala desde `Fila`, nunca desde una muestra.**
- **Dónde vive** — `makro_plazavea.py:4952` (`evaluar_calidad`, `:4082`) y `:5615` (auditoría).

---

## AUDITORÍA

> Este bloque no analiza: **conserva la evidencia** de qué contestó VTEX, para poder reprocesar sin
> volver a salir a la red. Tres bugs de parseo distintos ya costaron historia re-scrapeada.

### warehouse_id
- **Qué responde** — de qué almacén saldría el despacho.
- **Por qué existe** — es el primero de los cuatro identificadores que **componen la firma** con la
  que se identifica la sucursal. Guardarlos crudos es lo que permite auditar la atribución y lo que
  hace diagnosticable una entrada mal cargada en `NODOS`.
- **Valores posibles** — el `warehouseId` de VTEX.
- **Vacía** — sin bloque logístico. Mismas 132 filas.
- **Ejemplo** — `SW-359-MKO` (1.524), `SW-360-MKO` (1.506).
- **Dónde vive** — `makro_plazavea.py:4799`.

### dock_id
- **Qué responde** — de qué muelle sale el despacho.
- **Por qué existe** — segundo componente de la firma. Ver `warehouse_id`.
- **Valores posibles** — el `dockId` de VTEX.
- **Vacía** — sin bloque logístico.
- **Ejemplo** — `DC-359-MKO`.
- **Dónde vive** — `makro_plazavea.py:4800`.

### courier_id
- **Qué responde** — qué transportista se usaría.
- **Por qué existe** — tercer componente de la firma, y el que más habla en los casos ajenos
  (`DD-PV-GNRC` delata una operación genérica de PlazaVea).
- **Valores posibles** — el `courierId` de VTEX.
- **Vacía** — sin bloque logístico.
- **Ejemplo** — `DD-359-MKO`.
- **Dónde vive** — `makro_plazavea.py:4801`.

### courier_name
- **Qué responde** — el nombre legible del transportista.
- **Por qué existe** — cuarto componente de la firma. Se incluye en la comparación (no es
  decorativo): `firma_core()` lo exige igual que a los ids.
- **Valores posibles** — el `courierName` de VTEX.
- **Vacía** — sin bloque logístico.
- **Ejemplo** — `DD-Santa-Anita-Makro`.
- **Dónde vive** — `makro_plazavea.py:4802`.

### seller_chain
- **Qué responde** — la cadena de vendedores que VTEX resolvió para el ítem.
- **Por qué existe** — desde v07 **informa y no descarta**. Hasta v06, un producto con firma
  logística perfecta de Santa Anita pero con `sellerChain: ["1"]` se tiraba como nodo desconocido,
  y era un error: ese producto sí sale de ese almacén, solo que su precio lo pone el seller
  principal. Esa distinción es hoy `MATCH` vs `MATCH_SELLER_RAIZ`.
- **Valores posibles** — la lista unida con ` > `.
- **Vacía** — cuando el ítem no vino. **Ojo: sin stock no viene vacía, viene `1`** — VTEX
  *colapsa* al seller raíz. Ese detalle es el mecanismo entero de `surtido_makro`.
- **Ejemplo** — `1 > plazaveamko359` (1.524), `1 > plazaveamko360` (1.506), `1` (132).
- **Dónde vive** — `makro_plazavea.py:4793`.

### polygon_name
- **Qué responde** — el polígono de reparto que devolvió VTEX.
- **Por qué existe** — se guarda crudo aunque **no** participe de la identificación del nodo, para
  poder ver la deriva. La comparación contra `NODOS` es lo que produce `polygon_drift`.
- **Valores posibles** — el `polygonName` del SLA.
- **Vacía** — sin bloque logístico.
- **Ejemplo** — `Lima-SantaAnita-DD-359_V2` y `Lima-Surco-DD-360-V2`. Los dos ya vienen versionados
  y con convenciones distintas (`_V2` vs `-V2`), que es exactamente por qué se lo excluyó de la
  firma.
- **Dónde vive** — `makro_plazavea.py:4803`.

### postal_sent
- **Qué responde** — qué código postal se **envió** para pedir la cotización.
- **Por qué existe** — es el insumo, no el resultado. La sucursal **nunca** se infiere de acá; se
  guarda para poder reproducir la petición y para detectar el caso en que se manda uno y responde
  otra sucursal.
- **Valores posibles** — el `postal_code` de la entrada de `NODOS`.
- **Vacía** — nunca: siempre se envía uno.
- **Ejemplo** — `150137` (Santa Anita), `150140` (Surco).
- **Dónde vive** — `makro_plazavea.py:4748`.

### postal_resolved
- **Qué responde** — qué código postal dice el backend haber usado.
- **Por qué existe** — es la comprobación de que el envío se interpretó como se esperaba; el par
  enviado/resuelto es lo que delataría una normalización silenciosa de VTEX.
- **Valores posibles** — el `postalCode` de `shippingData.address`.
- **Vacía y qué significa** — **100% vacía, y no es camino muerto.** `simulation` **no devuelve
  `shippingData`**, así que el modo por defecto no la puede llenar nunca. **Se llenaría** con
  `--modo orderform` o en el fallback por SKU a orderForm. Las respuestas de orderForm que trae
  `--auditoria` sí traen la dirección poblada, pero se leen para reconciliar y se descartan: el
  valor existe, no es de esta fila. Su tasa de llenado es pregunta abierta desde
  `docs/decisiones_1.1.0.md` §11.
- **Ejemplo** — no hay: 3.162 de 3.162 vacías.
- **Dónde vive** — `makro_plazavea.py:4809`, lectura en `extraer_direccion` (`:4210`).

### neighborhood_resolved
- **Qué responde** — qué barrio/distrito resolvió el backend.
- **Por qué existe** — misma verificación que `postal_resolved`, en el campo que un humano puede
  leer de un vistazo.
- **Valores posibles** — el `neighborhood` de `shippingData.address`.
- **Vacía** — misma condición exacta que `postal_resolved`: solo la llena una respuesta de
  orderForm. 100% vacía acá.
- **Ejemplo** — no hay.
- **Dónde vive** — `makro_plazavea.py:4810`.

### http_status
- **Qué responde** — con qué código HTTP respondió VTEX a la medición.
- **Por qué existe** — separa "el retailer no tiene el producto" de "nuestra petición falló". Un
  200 con `__error` en el cuerpo existe y por eso el status **no alcanza solo** — ver `error`.
- **Valores posibles** — el código como texto; `429` y `5xx` solo llegan acá si agotaron los
  reintentos.
- **Vacía** — en la fila de excepción (no hubo respuesta) y en `SKU_NO_ENCONTRADO`.
- **Ejemplo** — `200` en las 3.162 filas.
- **Dónde vive** — `makro_plazavea.py:4750`.

### method
- **Qué responde** — por qué vía se midió esta fila: `simulation` (1 request) o `orderform` (3).
- **Por qué existe** — las dos vías no son idénticas —orderForm es la única que devuelve dirección
  resuelta y `selectedSla`— así que sin esta columna varias otras tienen un vacío inexplicable. Es
  también lo que permite filtrar cuando el modo por defecto cayó al fallback por SKU.
- **Valores posibles** — `simulation`, `orderform`.
- **Vacía** — nunca por el camino normal.
- **Ejemplo** — `simulation` en las 3.162 filas.
- **Dónde vive** — `makro_plazavea.py:4749`.

### error_class
- **Qué responde** — de qué **tipo** fue el problema, si lo hubo.
- **Por qué existe** — literalmente para "distinguir *Makro no tiene el producto* de *nuestro
  extractor falló*" (comentario del dataclass, `:1048`). Sin esa separación, un problema comercial
  y un bug se cuentan juntos en cualquier métrica de salud de la corrida.
- **Valores posibles** — `BUSINESS_UNAVAILABLE` (estado comercial del retailer: `SIN_STOCK`,
  `NO_COVERAGE`, `ITEM_NOT_RETURNED`), `ITEM_NOT_RETURNED` (`__error` en un 200), `HTTP_ERROR`,
  `RATE_LIMITED` (429), `NETWORK_ERROR` (excepción con `Timeout` en el nombre), `UNKNOWN`
  (cualquier otra excepción), `NOT_FOUND` (`--skus` pidió un SKU que el catálogo no devolvió).
  Solo apareció `BUSINESS_UNAVAILABLE`.
- **Vacía y qué significa** — la fila salió limpia. 3.030 filas acá.
- **Ejemplo** — `BUSINESS_UNAVAILABLE` en las 132 filas sin stock. **No es un fallo del motor**, y
  esta columna es lo que lo dice.
- **Dónde vive** — `makro_plazavea.py:4855`, `:4863`, `:4950`, `:5161`, `:2688`.

### error
- **Qué responde** — el mensaje del problema, en texto.
- **Por qué existe** — los errores lógicos de VTEX viajan **con HTTP 200 y un `__error` en el
  cuerpo**. Mirando solo el status ese mensaje se pierde y la fila queda vacía sin explicación.
- **Valores posibles** — texto libre, recortado a 400 caracteres. Tres fuentes: el `__error` del
  cuerpo, el cuerpo entero de una respuesta ≥ 400, o `TipoDeExcepción: mensaje`.
- **Vacía y qué significa** — **100% vacía en esta corrida, y ése es el buen resultado, no un
  hueco.** No hubo HTTP ≥ 400, ni `__error` en ningún cuerpo, ni una sola excepción por SKU.
  **Se llenaría** con cualquiera de esas tres. No es camino muerto: los tres caminos son
  alcanzables y `TopeAgotadoError` es la única excepción que deliberadamente **no** llega acá —
  es condición de corrida, no de SKU.
- **Ejemplo** — no hay.
- **Dónde vive** — `makro_plazavea.py:4854`, `:4862`, `:5162`, `:2689`.

### recon_status
- **Qué responde** — cuando esta fila se midió por **las dos vías**, ¿coincidieron?
- **Por qué existe** — el modo por defecto confía en `simulation` porque cuesta 1 request en vez de
  3, **y esa confianza nunca había sido verificada**. `--auditoria` (5% por defecto) mide una
  muestra por ambas vías y deja constancia. No corrige nada: solo registra.
- **Valores posibles** — `COINCIDEN`, o las marcas que difieran unidas por ` | `:
  `PRECIO_DIFIERE(sim=…,of=…)`, `DISPONIBILIDAD_DIFIERE(sim=…,of=…)`, `ALMACEN_DIFIERE(sim=…,of=…)`,
  `SELLER_DIFIERE`. Las tres primeras no aparecieron salvo disponibilidad.
- **Vacía y qué significa** — **la fila no fue auditada**. No dice nada sobre su calidad. 3.004 de
  3.162 filas.
- **Ejemplo** — `COINCIDEN` (152 filas) y `DISPONIBILIDAD_DIFIERE(sim=withoutStock,of=cannotBeDelivered)`
  (6 filas, p. ej. Panela EL GUARANGUITO 500 g en Santa Anita). Ese desacuerdo es real y vale
  registrarlo: `simulation` dice "sin stock" donde orderForm dice "no se despacha a esa zona", y son
  dos veredictos comerciales distintos.
- **Dónde vive** — `makro_plazavea.py:5013`, regla en `reconciliar` (`:3062`).

---

## BI-PRECIO (1.1.0)

> **Regla dura de §6.2: vacío nunca es cero, y nunca es el unitario repetido.** Si no hay precio
> mayorista, las columnas de precio van vacías y `biprecio_status` explica por qué.
>
> El mecanismo (umbral del catálogo + descuento del teaser, un solo escalón) está en
> `docs/decisiones_1.1.0.md` §1 y §5, con la corrección de la fórmula en
> `docs/brief_correccion_mayorista.md`. Acá va solo qué dice cada celda.
>
> **Lo que hay que entender antes de leer cualquiera de estas nueve:** el motor mide siempre a
> `qty = 1`, y a `qty = 1` el descuento del escalón **no se aplica**. Así que el precio mayorista
> está **reconstruido por resta**, no observado. Lo único que lo observa es
> `--auditoria-mayorista`, y eso es lo que declara `precio_mayorista_verificado`.

### bi_umbral ⚠
- **Qué responde** — a partir de cuántas unidades Makro aplica el precio mayorista.
- **Por qué existe** — es **el número que un analista negocia**, y solo existe en el catálogo
  (`CantidadBiPrecioMK` como `specification`): checkout no lo devuelve. Que viaje desde el
  descubrimiento hasta la medición dentro de `Producto` es el mecanismo entero — y no es un
  regreso de `panel.json`, porque nada se lee de disco: es la respuesta de catálogo de **esta**
  corrida viajando en memoria hasta la medición de **esta** corrida.
- **⚠ Cambio de significado (v18, `SCHEMA_VERSION` 4 → 5)** — antes se **vaciaba en todo estado
  distinto de `COMPLETO`**, y eso destruía evidencia real: en `SIN_DESCUENTO` y en
  `BIPRECIO_SUPERADO_POR_PROMO` el umbral se conoce y es un hecho del catálogo; lo que no existe es
  el precio. Desde v18 se vacía **solo si el catálogo no lo declara**. En un CSV anterior a v18 un
  umbral vacío puede querer decir "el catálogo no lo declaró" **o** "sí lo declaró pero el precio no
  se pudo armar"; desde v18 significa solo lo primero.
- **Valores posibles** — entero ≥ 2 como texto. Un "bi-precio" que arranca en 1 no es un bi-precio y
  se marca `INCONSISTENTE`. Acá: `2` (1.532), `3` (644), `4` (278), `6` (60), `10` (24), `20` (6),
  `12` (4), y algunos más.
- **Vacía y qué significa** — el catálogo no declaró `CantidadBiPrecioMK` para ese SKU. 610 filas.
- **Ejemplo** — `3` (Galleta Animalitos SAN JORGE 1 kg).
- **Dónde vive** — `makro_plazavea.py:4638`, origen en `parsear_producto` (`:1696`).

### tri_umbral_declarado
- **Qué responde** — qué segundo escalón declara el catálogo (`CantidadTriPrecioMK`).
- **Por qué existe** — **se registra SIEMPRE y NUNCA se aplica.** Está declarado en el catálogo y se
  midió que checkout no lo honra (§1). El nombre dice `declarado` justamente para que nadie lo lea
  como un escalón vigente. Si algún día empieza a cobrarse, la columna ya tiene la serie histórica.
- **Valores posibles** — entero como texto: `4` (742), `6` (306), `12` (278), `5`, `20`, `8`, `24`…
- **Vacía y qué significa** — el catálogo no lo declara. 1.468 filas.
- **Ejemplo** — `6` (HUERTO ALAMEIN 2L, con `bi_umbral = 3`).
- **Dónde vive** — `makro_plazavea.py:4640`.

### descuento_monto
- **Qué responde** — cuántos soles por unidad descuenta el escalón, según lo que VTEX declara.
- **Por qué existe** — **es un hecho de VTEX y se publica aunque el mayorista no se pueda armar**;
  es lo que hace diagnosticable un `SIN_UMBRAL` (§6.4). Se lee primero de la medición (viene con
  contexto de sucursal y está vivo) y solo si no vino, del catálogo.
- **De qué teaser se lee (v24)** — del teaser **no condicionado a tarjeta**. Una respuesta puede
  traer hasta tres teasers, y los condicionados a tarjeta (`PaymentMethodId` en
  `PAYMENT_METHOD_IDS_TARJETA` = `{"208,202,210", "203,502,501,210"}`, los regímenes
  `Promo Oh-Pay MAKRO` y `TARJETA OH - MAKRO`) **se excluyen**: su descuento se anuncia y no se
  cobra sin la tarjeta. Está medido, no supuesto — en `run_20260827_021218` línea 3447 (SKU
  10012708, `qty=2`, tres teasers, sin tarjeta seleccionada) VTEX aplicó solo el de
  `PaymentMethodId = "4"`, y los de tarjeta no se aplicaron en ninguna de sus 17 apariciones. La
  comparación es por **igualdad exacta de la cadena entera**, nunca por substring: `"208"` suelto
  aparece 13.000+ veces por corrida en `paymentData.paymentSystems`, que es el catálogo de medios
  de pago del storefront y no una promoción. Hasta v23 se tomaba el **primero del array** y VTEX
  pone los de tarjeta primero, así que su descuento pisaba al del bi-precio: 6 filas por corrida en
  las dos corridas en disco. **El descuento de tarjeta no tiene columna propia todavía** — hoy
  simplemente no entra acá; su columna dedicada es un paso posterior.
- **Valores posibles** — número plano con dos decimales.
- **Vacía y qué significa** — ni la medición ni el catálogo trajeron un teaser con descuento. 876
  filas, exactamente el estado `SIN_BIPRECIO` (602) más el resto sin descuento (274 `SIN_DESCUENTO`).
- **Ejemplo** — `0.30` (Galleta Animalitos SAN JORGE 1 kg, umbral 3). Y `5.00` en Aceite Vegetal
  ARO 18L, que es `SIN_UMBRAL`: hay descuento declarado y no hay escalón — esa es la fila que esta
  columna existe para poder diagnosticar.
- **Dónde vive** — `makro_plazavea.py:4641`, lectura en `leer_descuento` (`:3459`).

### descuento_monto_cents
- **Qué responde** — lo mismo, en céntimos enteros.
- **Por qué existe** — es la forma con la que el motor **realmente calcula**: toda la aritmética del
  bi-precio es entera. Si el descuento declarado no cae exacto en el céntimo, el motor **no
  redondea, marca** `INCONSISTENTE` — redondear inventaría medio céntimo por unidad.
- **Valores posibles** — entero como texto.
- **Vacía** — misma condición que `descuento_monto`. 876 filas.
- **Ejemplo** — `30`.
- **Dónde vive** — `makro_plazavea.py:4642`.

### precio_mayorista ⚠
- **Qué responde** — cuánto costaría cada unidad comprando al menos `bi_umbral` unidades.
- **Por qué existe** — es la razón de ser de 1.1.0: el precio que un analista de pricing negocia de
  verdad. **Está reconstruido, no observado** (el motor mide a qty=1).
- **Cómo se reconstruye** — `list_price − descuento_monto`, **resta directa y nada más**. VTEX ya
  entrega el descuento **prorrateado por unidad** en `PromotionalPriceTableItemsDiscount`, así que
  el motor **no divide por `bi_umbral`** y no debe hacerlo: dividir sería prorratear dos veces algo
  que ya venía prorrateado. Verificado el 2026-08-27 contra el `sellingPrice` que la simulación
  cobra al alcanzar el escalón —el `raw.jsonl.gz` de `run_20260827_021218`, no una inferencia— en
  tres umbrales distintos:

  | SKU | producto | umbral | reconstruido | cobra VTEX |
  |---|---|---|---|---|
  | 10012695 | Galleta Animalitos SAN JORGE 1 kg | 3 | 7.50 − 0.30 = **7.20** | **7.20** ✓ |
  | 597 | Aceite de Soya SOYA 900 ml | 20 | 5.70 − 0.10 = **5.60** | **5.60** ✓ |
  | 10012708 | Chocolate BESOS DE MOZA caja 20un | 2 | 20.50 − 1.20 = 19.30 | **19.90** ✗ |

  La fila de Besos **cambió con v24 y sigue sin cerrar**, y las dos mitades de eso importan por
  separado. El `1.50` que decía esta tabla era el descuento del teaser de tarjeta, leído por el bug
  de posición; el del bi-precio es `1.20`, así que la reconstrucción pasa de 19.00 a 19.30 y la
  discrepancia contra el cobro real baja de 90 a 60 céntimos. Pero **no desaparece**: VTEX declara
  `1.20` en el teaser y cobra `0.60` por unidad a `qty=2`. Eso es una contradicción de VTEX consigo
  mismo, independiente del bug de posición, y es exactamente lo que `DQ_MAYORISTA_DISCREPA` existe
  para registrar — la bandera nombra una contradicción, no un culpable. Sigue abierta, con `n=1`:
  los otros dos umbrales auditados cierran exacto.

- **⚠ Cambio de significado (v18, `SCHEMA_VERSION` 4 → 5, cabecera idéntica byte a byte)** — lo que
  v18 corrigió fue la **base de la resta**: `price − descuento` pasó a `list_price − descuento`, y
  esa corrección sigue vigente (es la fórmula que la tabla de arriba acaba de confirmar contra el
  cobro real). Estuvo mal seis semanas y nada lo delataba, porque donde no hay promoción unitaria
  `price == list_price` y las dos fórmulas colapsan en el mismo número: en `run_20260822_020027`,
  2.021 de 2.328 filas `COMPLETO` no tenían promoción unitaria, o sea el bug era invisible en el 87%
  del dataset. Lo que lo probó fue verdad **externa** —23 fichas del storefront capturadas a mano— y
  no otra respuesta de la misma API: la fórmula vieja acierta 11/23, la nueva 23/23. **Este es el
  caso testigo del proyecto: una serie que mezcle filas anteriores y posteriores a v18 promedia dos
  definiciones distintas de la misma columna, y el CSV no se ve diferente.** `schema_version` es lo
  único que las separa.
- **La tercera fila de la tabla no refutaba la fórmula: fallaba la *fuente* del descuento —
  ARREGLADO en v24.** El diagnóstico era correcto: VTEX reporta el descuento del teaser de tarjeta
  (`Promo Oh-Pay MAKRO`, 1.50) **en el mismo campo** `PromotionalPriceTableItemsDiscount` del que
  sale el escalón, y `leer_descuento` devolvía el primer teaser que cargara ese parámetro sin mirar
  de qué régimen venía — el campo correcto con el valor del régimen equivocado. No era un caso
  único: son 6 filas por corrida en las dos corridas en disco, 3 SKUs × 2 nodos cada vez, con
  conjuntos de SKUs **disjuntos** entre el 26 y el 27 porque la promoción de tarjeta rota a diario.
- **El criterio que este documento proponía habría sido el equivocado**, y vale dejarlo escrito.
  Descartar por `teaserType: "Profiler"` habría vaciado el bi-precio en **todo** el dataset: los
  cuatro regímenes de precio son `Profiler`, incluido `MAKRO-Bi-Precio|Vigente Oculto`, que es el
  que hay que conservar. El discriminante que sí separa es `PaymentMethodId` por igualdad exacta
  (ver `descuento_monto`), y lo que lo respalda es que VTEX **aplica** el teaser no condicionado a
  tarjeta y nunca los de tarjeta.
- **Y el ticket de tienda le da la razón al fix.** El carrito web real cobró **19.30** por unidad a
  qty=2 — exactamente lo que el motor reconstruye desde v24, y no los 19.00 de antes ni los 19.90
  que devuelve la simulación. La verdad externa coincide con la fórmula alimentada con el descuento
  correcto; lo que queda descolgado es la simulación a `qty=umbral`, que es otra pregunta.
- **Y la auditoría hizo exactamente lo que tiene que hacer.** `DQ_MAYORISTA_DISCREPA` no es un bug
  del motor: es la alarma que detectó la única fila donde el cálculo no reproduce lo medido, 1 de
  3.164. Al dispararse, el precio **medido** gana (es lo que el cliente paga), `descuento_monto`
  queda intacto —borrar cualquiera de los dos lados borraría el hallazgo— y `precio_mayorista` pasa
  a valer 19.90. Como dice `CLAUDE.md`, la bandera nombra una contradicción, no un culpable.
- **Observación abierta, y el motor no la resuelve hoy** — el carrito web real cobró 19.30 por
  unidad a qty=2 (S/57.90 por 3 unidades) y la **simulación** cobra 19.90. En este SKU ni siquiera
  las dos fuentes de VTEX coinciden entre sí, y por eso `DQ_MAYORISTA_DISCREPA` sigue disparando
  después de v24 — la discrepancia baja de 90 a 60 céntimos pero no cierra. Con el fix, el lado que
  queda descolgado es la simulación, no la reconstrucción. Pregunta abierta del mismo caso, `n=1`,
  no algo a corregir en la fórmula.
- **Valores posibles** — número plano con dos decimales.
- **Vacía y qué significa** — solo hay precio en dos estados (`COMPLETO` y
  `BIPRECIO_PUBLICACION_INDETERMINADA`). El vacío tiene una causa distinta por estado, y
  `biprecio_status` en la columna de al lado dice cuál. Acá 1.006 filas: `SIN_BIPRECIO` (602, no hay
  ni umbral ni descuento), `SIN_DESCUENTO` (274, hay umbral y no hay descuento),
  `BIPRECIO_SUPERADO_POR_PROMO` (122, **hay umbral y hay descuento pero el escalón no le gana a la
  promoción unitaria, así que no existe tal precio**) y `SIN_UMBRAL` (8).
- **Ejemplo** — `7.20` (Galleta Animalitos SAN JORGE 1 kg, umbral 3): `list_price` 7.50 − descuento
  0.30, resta directa. El 0.30 **ya es por unidad**; no se divide entre 3.
- **Dónde vive** — `makro_plazavea.py:4647`, regla en `calcular_mayorista` (`:3626`), fuente del
  descuento en `leer_descuento` (`:3459`), y `:5594` cuando la auditoría lo sobrescribe con lo
  medido. La mecánica del bi-precio, en `docs/decisiones_1.1.0.md` §1 y §5 (con su fórmula anotada
  como superada); la historia del cambio, en `CHANGELOG.md`.

### precio_mayorista_cents ⚠
- **Qué responde** — lo mismo, en céntimos enteros.
- **Por qué existe** — igual que `price_cents`: es lo canónico. La auditoría compara céntimos, y la
  expectativa de `esperado_en_auditoria` se expresa en esta unidad.
- **⚠** — hereda el cambio de base de v18. Mismo caso testigo.
- **Valores posibles** — entero como texto.
- **Vacía** — idéntica condición que `precio_mayorista`; las dos van juntas siempre.
- **Ejemplo** — `720`.
- **Dónde vive** — `makro_plazavea.py:4648`, `:5593`.

### descuento_mayorista_pct ⚠
- **Qué responde** — cuánto ahorra por unidad quien compra el escalón, **contra `price`**.
- **Por qué existe** — la base es explícita y deliberada (§6.5): mide el ahorro contra **lo que hoy
  se paga**, no contra `list_price`. Si hay promoción unitaria encima, `list_price` es un precio que
  nadie paga y un porcentaje medido contra él exagera el beneficio del escalón.
- **⚠ Cambio de significado (v18)** — hasta v17 daba lo mismo, porque con la base vieja el ahorro
  **era** el descuento y podía escribirse como `descuento / price`. Con la base corregida son dos
  números distintos en toda fila con promoción unitaria.
- **Valores posibles** — porcentaje con dos decimales.
- **Vacía y qué significa** — dos causas, y la segunda es sutil:
  1. No hay precio mayorista (mismos 1.006 casos de arriba).
  2. **Hay precio mayorista pero el estado es `BIPRECIO_PUBLICACION_INDETERMINADA`** — 75 filas.
     El porcentaje está atado a `COMPLETO`, no a "hay mayorista": en una fila sin stock `price` es
     el precio de lista, así que el ahorro saldría contra un número que nadie paga. Se conoce el
     precio mayorista y **no se conoce contra qué compararlo**.
- **Ejemplo** — `4.00` (7.50 → 7.20).
- **Dónde vive** — `makro_plazavea.py:4649`, y `:5601` cuando la auditoría lo recalcula contra el
  precio realmente cobrado, para que precio y porcentaje no se contradigan dentro de la misma fila.

### biprecio_status
- **Qué responde** — **por qué las columnas de mayorista dicen lo que dicen.** Es la columna que
  explica los vacíos de las cuatro anteriores, así que nunca puede faltar.
- **Por qué existe** — sin ella, un mayorista vacío y un mayorista ausente por promoción se ven
  iguales; y un `COMPLETO` con `NO` en `precio_mayorista_verificado` parecería una carencia cuando
  es lo normal.
- **Valores posibles** — **6 observados y al menos 3 estados más alcanzables**:

  | valor | qué afirma | en esta corrida |
  |---|---|---|
  | `COMPLETO` | hay precio mayorista **y** el escalón se publica | 2.081 |
  | `SIN_BIPRECIO` | ni umbral ni descuento | 602 |
  | `SIN_DESCUENTO` | hay umbral declarado, no hay descuento | 274 |
  | `BIPRECIO_SUPERADO_POR_PROMO` | `list_price − descuento >= price`: el escalón existe en el catálogo y **no le gana a la promoción unitaria**, así que ni se publica ni se cobra | 122 |
  | `BIPRECIO_PUBLICACION_INDETERMINADA` | sin stock: el precio se calcula igual, pero la regla de publicación **no se puede evaluar** | 75 |
  | `SIN_UMBRAL` | hay descuento y no hay umbral | 8 |
  | `SIN_MEDICION` | **no observado.** No hubo `price_cents`: HTTP ≥ 400 o excepción. La identidad del producto se escribe igual | 0 |
  | `INCONSISTENTE` | **no observado.** Tres causas distintas: el descuento no cae exacto en el céntimo, falta `list_price` (y **no** se cae de vuelta a `price` — ese atajo *es* el bug de v18), o el mayorista sale ≤ 0 o el umbral < 2 | 0 |
  | *(vacío)* | **no observado.** Una fila `SKU_NO_ENCONTRADO` nunca pasa por `enriquecer_fila`, así que este campo se queda en su default vacío | 0 |

  `BIPRECIO_SUPERADO_POR_PROMO` llegó en v18 y `BIPRECIO_PUBLICACION_INDETERMINADA` en v20: un CSV
  anterior no puede contenerlos, y sus filas equivalentes estaban mal clasificadas como `COMPLETO`.
- **Vacía** — solo en la fila de SKU ausente descrita arriba.
- **Ejemplo** — `BIPRECIO_SUPERADO_POR_PROMO` en HUERTO ALAMEIN 2L: `price` 88.55, `list_price`
  118.50, descuento 0.10 → el escalón daría 118.40, muy por encima de lo que ya se paga.
- **Dónde vive** — `makro_plazavea.py:4652`, regla en `calcular_mayorista` (`:3700-3757`).

### precio_mayorista_verificado
- **Qué responde** — ¿el precio mayorista de esta fila se **midió** contra VTEX, o está reconstruido
  por resta?
- **Por qué existe** — es **otro eje** que `biprecio_status`, y confundirlos es fácil. Aquél dice si
  hay precio y si el escalón se publica; éste dice de dónde salió el número. Un `COMPLETO` con `NO`
  es lo normal, no una carencia: el motor mide a qty=1 siempre, así que por defecto reconstruye.
  Decir `SI` sin haber medido a `qty ≥ umbral` sería inventar evidencia.
- **Valores posibles** — **tres estados: `SI`, `NO` y vacío**, y los tres significan cosas
  distintas.
  - `NO` (2.152 filas) — hay un precio mayorista y está **reconstruido**. Es el default honesto.
  - `SI` (4 filas) — se remidió a `qty = bi_umbral` y el número escrito es **observado**.
  - vacío (1.006 filas) — ver abajo.
- **Vacía y qué significa — al menos tres cosas distintas**:
  1. **No hay precio mayorista que verificar.** El campo sigue a "hay mayorista", no a `COMPLETO`,
     así que se vacía en `SIN_BIPRECIO`, `SIN_DESCUENTO`, `SIN_UMBRAL`, `SIN_MEDICION` e
     `INCONSISTENTE`. Es la causa de la enorme mayoría.
  2. **Se midió contra la red y lo verificado fue una AUSENCIA.** Caso real de esta corrida: SKU
     **10020888** (Galletas de Maíz SALMAS 126 g), `BIPRECIO_SUPERADO_POR_PROMO`, umbral 2. La
     auditoría lo remidió a qty=2, VTEX cobró 790 céntimos —exactamente su `price`— y el veredicto
     fue `COINCIDE`: se confirmó que **el escalón no se cobra**. Escribir eso en `precio_mayorista`
     publicaría un mayorista idéntico al unitario, que es justo la mentira que §6.2 evita, así que
     `aplicar_veredicto_mayorista` **retorna antes de tocar la fila** y el campo se queda vacío. En
     el CSV esta fila es **indistinguible** del caso 1; lo que la distingue vive en
     `run.json` → `auditoria_mayorista.detalle[]`, con `caso: ESCALON_SUPRIMIDO`.
  3. **La fila nunca pasó por `enriquecer_fila`** — una fila `SKU_NO_ENCONTRADO`. No observado acá.
- **`SI` tampoco distingue todo lo que se cree.** No dice si esta fila fue auditada **directamente**
  o si el veredicto le llegó **propagado** desde el otro nodo. La propagación es deliberada: lo que
  se mide es qué cobra VTEX a esa cantidad, y eso es propiedad del SKU, no del nodo — sin propagar,
  el mismo SKU sale de la misma corrida con dos mayoristas distintos. **Esa distinción vive solo en
  `run.json`**, en `auditoria_mayorista.detalle[].node_id` (el auditado) y `propagado_a[]` (los
  demás). En esta corrida los 3 SKUs se auditaron en el nodo **359** y se propagaron al **360**; las
  4 filas con `SI` son 2 SKUs × 2 nodos, y 2 de ellas nunca tocaron la red.
- **Ejemplo** — `SI` en SKU 10012695 (Galleta Animalitos SAN JORGE, umbral 3, 7.20 medido) y SKU
  597 (Aceite de Soya SOYA 900 ml, umbral **20**, 5.60 medido), en ambos nodos.
- **Dónde vive** — `makro_plazavea.py:4657` (el `NO`/vacío por defecto), `:5592` (el `SI` de la
  auditoría) y `:5589-5590` (el retorno temprano que deja el vacío en un `ESCALON_SUPRIMIDO` que
  coincide).

---

## RÉGIMEN PROMOCIONAL (1.1.0)

### promo_regime_id
- **Qué responde** — el identificador de la promoción que gobierna el bi-precio.
- **Por qué existe** — es lo que permite agrupar SKUs bajo la misma campaña y detectar cuándo una
  campaña cambia. **No se lee de `rateAndBenefitsIdentifiers`**: ese array viene vacío a qty=1 y el
  motor mide a qty=1 siempre, así que como fuente principal es inutilizable (§1). Se lee del teaser.
- **De qué teaser (v24)** — del mismo que `descuento_monto`: el **no condicionado a tarjeta**, con
  los de `PAYMENT_METHOD_IDS_TARJETA` excluidos. Las tres columnas de esta sección describen el
  régimen que produjo el descuento de al lado, así que tienen que mirar el mismo teaser o la fila
  se contradice sola. Hasta v23 podían nombrar `Promo Oh-Pay MAKRO` junto a un descuento que
  también salía de ahí; desde v24 nombran el bi-precio, que es el que se cobra.
- **Valores posibles** — UUID. Acá tres: `225a92ff-…` (2.205, el bi-precio), `57827689-…` (227,
  precio regular) y `439099e5-…` (6, Oh-Pay).
- **Vacía y qué significa** — no hubo teaser con id ni en la medición ni en el catálogo. 724 filas.
  **Ojo con la asimetría**: hay 75 filas con `promo_regime_name` lleno y este campo vacío, porque el
  catálogo trae el teaser **sin `id`** y `simulation` lo trae **con** `id`. Esas 75 son las filas sin
  stock, donde la medición no aportó teaser y quedó solo el respaldo del catálogo.
- **Ejemplo** — `225a92ff-a721-4f76-8856-2cba133c12d8`.
- **Dónde vive** — `makro_plazavea.py:4662`, lectura en `leer_regimen` (`:3471`).

### promo_regime_name
- **Qué responde** — el nombre legible de esa promoción.
- **Por qué existe** — es lo que hace legible el régimen sin cruzar contra nada, y llega en más
  casos que el id (el catálogo lo trae aunque no traiga id).
- **Valores posibles** — texto de VTEX. Acá: `MAKRO-Bi-Precio|Vigente Oculto` (2.280),
  `Precio Vigente Regular MAKRO` (227), `Promo Oh-Pay MAKRO` (6).
- **Vacía y qué significa** — ninguna de las dos fuentes trajo teaser. 649 filas.
- **Ejemplo** — `MAKRO-Bi-Precio|Vigente Oculto`. El "Vigente Oculto" es de VTEX, no del motor.
- **Dónde vive** — `makro_plazavea.py:4665`.

### payment_method_id
- **Qué responde** — con qué medio de pago está condicionada la promoción, si lo está.
- **Por qué existe** — se registraba sin interpretarse, y **desde v24 sí se interpreta**: es el
  discriminante que separa el teaser del bi-precio del teaser de tarjeta (ver `descuento_monto`).
  Lo que se compara es la cadena entera contra `PAYMENT_METHOD_IDS_TARJETA`, por igualdad exacta.
  Que el dato ya estuviera en la serie desde 1.1.0 —registrado antes de tener uso— es lo que
  permitió medir el bug sobre el crudo archivado en vez de tener que volver a scrapear.
- **Ojo: no está resuelto a qué apunta cada número.** `paymentData.paymentSystems` mapea
  `208 → Agora-Visa`, `210 → Tarjeta-Oh-Cuotas`, y **no contiene** el `4` ni el `202`/`203`/`501`/
  `502`. No se sabe si `PaymentMethodId` y `paymentSystem.stringId` son el mismo espacio de ids;
  el endpoint público que lo diría responde 404. Por eso el motor usa estos valores como
  **literales opacos** y no interpreta ninguno: para separar los regímenes observados no hace falta
  saber qué significan, y para afirmar "esto es Tarjeta Oh!" sí haría falta.
- **Valores posibles** — el o los ids del teaser, como texto. Acá `4` (2.280) y `208,202,210` (6).
- **Vacía y qué significa** — no hubo teaser con parámetro de pago. 876 filas — exactamente las
  mismas en las que `descuento_monto` está vacío: los dos salen del mismo teaser.
- **Ejemplo** — `208,202,210` en Aceite Vegetal ARO 18L, la promoción Oh-Pay. Esos 6 casos son
  todos `SIN_UMBRAL`: descuento atado a medio de pago, no a cantidad.
- **Dónde vive** — `makro_plazavea.py:4668`.

### price_valid_until
- **Qué responde** — hasta cuándo declara VTEX que este precio es válido.
- **Por qué existe** — para enterarse de un vencimiento de campaña **antes** de que el precio cambie
  solo. Se lee del ítem **pedido**, no del primero que aparezca en la respuesta.
- **Valores posibles** — timestamp ISO. **`3000-01-02` es el centinela de VTEX para "sin
  vencimiento"** — no es una fecha, es un valor mágico, y hay que tratarlo como tal. `2099-01-02`
  parece ser un segundo centinela del mismo tipo, aunque el código no lo declara.
- **Vacía** — si el ítem pedido no volvió en la respuesta. Cero veces acá.
- **Ejemplo** — `3000-01-02T05:00:00Z` (3.050 filas, o sea sin vencimiento), y fechas reales en 57:
  `2026-08-29`, `2026-09-01` (Pack Galletas OREO 12 un × 2 un, `Precio Vigente Regular MAKRO`).
  Esas 57 son las únicas con caducidad declarada y son las que valen mirar.
- **Dónde vive** — `makro_plazavea.py:4671`, lectura en `leer_price_valid_until` (`:3511`).

---

## IDENTIDAD Y CALIDAD (1.1.0)

### ean_type
- **Qué responde** — ¿este código de barras sirve para cruzar el producto contra otro retailer?
- **Por qué existe** — un EAN con prefijo 20-29 es un código **que la propia tienda se inventó**:
  sirve dentro de Makro y no cruza contra nada. Tratar los dos como "el EAN" arruina cualquier match
  de catálogo cruzado, y produce falsos negativos silenciosos —"el producto no existe en el
  competidor"— cuando lo que pasa es que se lo busca por un código que solo existe acá. El dígito
  verificador se valida con el estándar GS1 (mod 10), que aplica igual a EAN-8, UPC-A, EAN-13 y
  GTIN-14.
- **Valores posibles** — `GS1_GLOBAL` (2.170), `FALTANTE` (734), `INTERNO_RESTRINGIDO` (192),
  `INVALIDO` (66). Los cuatro aparecieron.
- **Vacía** — nunca: la función siempre devuelve una de las cuatro etiquetas. **`FALTANTE` es el
  vacío, y está nombrado a propósito.**
- **Ejemplo** — `INTERNO_RESTRINGIDO` en Endulzante SPLENDA caja 700 un (`2200201722383`), e
  `INVALIDO` en Sopa MARUCHAN Ramen 85 g (`41789002991`: 11 dígitos, un largo que no existe).
  **26% del catálogo de esta corrida no cruza** (`FALTANTE` + `INTERNO` + `INVALIDO`).
- **Dónde vive** — `makro_plazavea.py:4674`, regla en `clasificar_ean` (`:3776`).

### category_id
- **Qué responde** — la ruta de categoría **estable** del producto.
- **Por qué existe** — VTEX **renombra categorías sin avisar**, así que el texto de `category` no
  sirve para agrupar en una serie de meses. De todas las rutas que trae el producto se toma **la más
  larga**, que es la más específica. Es además la clave con la que trabaja `--categoria`.
- **Valores posibles** — ruta con barras, `/431/1037/41/`. 68 distintas acá.
- **Vacía** — si el producto no trae `categoriesIds`. Cero veces acá.
- **Ejemplo** — `/431/1037/41/` (282 filas). El `431` inicial es Abarrotes, que es el alcance de esta
  corrida.
- **Dónde vive** — `makro_plazavea.py:4675`, origen en `parsear_producto` (`:1698`).

### sales_channel
- **Qué responde** — contra qué canal de venta se hizo la consulta.
- **Por qué existe** — es el `sc` de la petición (§5: "constante 9 hoy"). Un precio de VTEX es
  siempre precio *de un canal*; dejarlo implícito haría incomparables dos corridas si algún día se
  consulta otro. Es un parámetro registrado, no una medición.
- **Valores posibles** — `9`.
- **Vacía** — nunca; es constante del motor.
- **Ejemplo** — `9`.
- **Dónde vive** — `makro_plazavea.py:751` (constante), `:4676`.

### surtido_makro
- **Qué responde** — nominalmente, "¿este SKU es parte del surtido de ESTA sucursal?". **En los
  hechos responde otra cosa** — ver abajo.
- **Por qué existe** — `docs/decisiones_1.1.0.md` §2 la declaró "la ÚNICA prueba de surtido Makro de
  esta sucursal". **Medido, no lo es.** La regla es
  `"SI" if f"plazaveamko{node_id}" in seller_chain else "NO"`, y VTEX solo agrega el seller de la
  sucursal cuando resolvió un vendedor que va a despachar — cosa que requiere stock. Así que lo que
  pregunta de verdad es **"¿VTEX resolvió esta sucursal como vendedor hoy?"**, que es cierto si y
  solo si hay stock. En esta corrida es **colineal con `availability` en las 3.162 filas, sin una
  excepción** (`SI` = 3.030 = `available`; `NO` = 132 = `withoutStock`), igual que en las dos
  corridas anteriores.
- **Colineal no es sinónimo, y la diferencia importa.** Una fila con stock despachada por el seller
  raíz o por un tercero (`MATCH_SELLER_RAIZ`, `OPERADOR_EXTERNO`) vendría `available` con
  `surtido_makro = NO`. En abarrotes sobre 359 y 360 eso no pasó nunca. La redundancia es propiedad
  de **esta muestra**, no de la definición — apuntar la columna a otro campo sería arreglar la cosa
  equivocada. `CLAUDE.md` tiene la decisión completa: **no se borra**; se revisa a los 30 días o
  cuando el alcance salga de abarrotes.
- **Valores posibles** — `SI`, `NO`.
- **Vacía** — nunca: la expresión siempre devuelve uno de los dos. **Esto es una trampa propia**: un
  `NO` puede significar "VTEX no resolvió esta sucursal" o "no hubo `seller_chain` en absoluto"
  (fila de error), y la columna no los distingue.
- **Ejemplo** — `NO` en Aceituna Negra OLIVALLE 1 kg (Surco), con `seller_chain = 1`.
- **Dónde vive** — `makro_plazavea.py:4677`.

---

## PRESENTACIÓN (1.1.0)

> Dos ramas, y saber en cuál cayó la fila es la mitad del dato: con `measurement_unit != un` el
> tamaño lo declara VTEX y es autoritativo; con `un` hay que parsearlo del nombre y es heurística.
> **Un `1` inventado en `cantidad_base` convierte el precio del empaque en un precio por kilo falso,
> que es peor que no tener el dato** — por eso el caso sin resolver deja las derivadas vacías.

### unidad_base
- **Qué responde** — en qué unidad se expresa el contenido del producto.
- **Por qué existe** — normaliza `250g`, `1kg` y `0.9L` a una unidad común para poder comparar
  precios por unidad entre presentaciones distintas.
- **Valores posibles** — las de la tabla `UNIDADES`: acá `kg` (2.400), `un` (456), `l` (300).
- **Vacía y qué significa** — no se pudo resolver la presentación: `presentacion_origen =
  DESCONOCIDO`. 6 filas (3 SKUs × 2 nodos).
- **Ejemplo** — `kg` en Galletas SALMAS 126 g (con `cantidad_base = 0.126`).
- **Dónde vive** — `makro_plazavea.py:4688`, regla en `resolver_presentacion` (`:3945`).

### cantidad_base
- **Qué responde** — cuánto contiene el empaque, en `unidad_base`.
- **Por qué existe** — es el divisor de las dos columnas por unidad base. Sin él, comparar un
  paquete de 250 g contra uno de 1 kg es comparar dos números que no significan lo mismo.
- **Valores posibles** — decimal como texto, normalizado.
- **Vacía** — misma condición que `unidad_base`: las dos van juntas. 6 filas.
- **Ejemplo** — `0.126`; `1` en Galleta Animalitos SAN JORGE Paquete 1 kg.
- **Dónde vive** — `makro_plazavea.py:4689`.

### presentacion_origen
- **Qué responde** — de dónde salió la presentación, y por lo tanto cuánto se le puede creer.
- **Por qué existe** — separa el dato autoritativo del heurístico. `VTEX` significa que lo declaró
  el servidor; `NOMBRE` significa que un parser lo sacó del texto del título y **puede
  equivocarse**, sobre todo en multipacks. Sin esta columna las dos calidades se ven iguales.
- **Valores posibles** — `VTEX`, `NOMBRE`, `DESCONOCIDO`. Acá `NOMBRE` (3.156) y `DESCONOCIDO` (6);
  **`VTEX` no apareció** porque no hay productos por peso en abarrotes.
- **Vacía** — nunca: siempre devuelve una de las tres. `DESCONOCIDO` es el "no sé", nombrado.
- **Ejemplo** — `DESCONOCIDO` en `Snack de Tortillas Fritas de Maíz con Sal`,
  `Olivos Del Sur Aceite Aceite De Oliva Puro C` y `Cerezas Marrasquino PENNAT Frasco 28Oz`. Los
  tres nombres, o no traen medida, o la traen en una unidad que la tabla no conoce (`28Oz`).
- **Dónde vive** — `makro_plazavea.py:4690`.

### precio_por_unidad_base
- **Qué responde** — cuánto cuesta un kilo / un litro / una unidad de este producto, al precio
  unitario.
- **Por qué existe** — es el comparable real entre presentaciones y entre retailers. Reusa la misma
  función pura que `price_per_unit` en la rama VTEX, a propósito: repetir el criterio arriesgaría
  que las dos columnas se separen con el tiempo.
- **Valores posibles** — número con **cuatro** decimales.
- **Vacía y qué significa** — no hay `cantidad_base` con que dividir (`DESCONOCIDO`), o no hay
  precio. 6 filas.
- **Ejemplo** — `62.6984` en Galletas SALMAS 126 g: el paquete cuesta 7.90 y el kilo sale casi 63
  soles. Esa es exactamente la comparación que la columna existe para hacer visible.
- **Dónde vive** — `makro_plazavea.py:4701-4709`.

### precio_mayorista_por_unidad_base ⚠
- **Qué responde** — lo mismo, al precio mayorista.
- **Por qué existe** — cierra el par: compara el escalón contra el unitario en la misma base.
  **Siempre se deriva**, nunca se lee del servidor: VTEX no publica un `list_price` del precio
  mayorista. El código lo deja dicho para que nadie le atribuya el mismo respaldo que a la columna
  de al lado.
- **⚠** — hereda el cambio de base de v18 vía `precio_mayorista`.
- **Valores posibles** — número con cuatro decimales.
- **Vacía y qué significa** — dos causas: no hay precio mayorista (1.006 filas), o lo hay y no hay
  `cantidad_base` con que dividir (**4 filas**: `Snack de Tortillas Fritas de Maíz con Sal` y
  `Cerezas Marrasquino PENNAT 28Oz`, en los dos nodos). Total 1.010. Sigue a "hay mayorista", no a
  `COMPLETO`: es el mismo precio dividido, y su vigencia la declara `biprecio_status`.
- **Ejemplo** — `7.2000` (Galleta Animalitos SAN JORGE 1 kg).
- **Dónde vive** — `makro_plazavea.py:4718-4721`, y `:5608` cuando la auditoría lo recalcula.

---

## PROCEDENCIA DEL PRECIO

### price_origin
- **Qué responde** — ¿`price` es un precio **cotizado**, o el precio de lista devuelto porque no
  había nada que cotizar?
- **Por qué existe** — **VTEX no evalúa promociones cuando el nodo no tiene stock: devuelve el
  precio de lista**, y hasta v19 ese número entraba en `price` sin forma de distinguirlo de uno
  cotizado. Un promedio de la serie mezclaba precios cobrados con precios de lista. La evidencia:
  las 143 filas `withoutStock` de `run_20260822_020027` tienen `price == list_price` y
  `discount_pct = 0.00`, **las 143 sin una excepción**, contra 19,8% de incidencia de promoción
  entre las filas con stock — bajo independencia se esperarían ~28 con descuento y salieron cero.
  Verificado además a mano en el storefront, donde esos productos salen en gris y sin botón de
  compra.
- **La regla mira `availability`, nunca la relación `price`/`list_price`.** Esa igualdad también le
  pasa a una fila **con** stock y sin promoción; el stock es la causa y la igualdad de precios su
  consecuencia, y una consecuencia no puede ser el criterio.
- **Es la columna de la que cuelgan otras tres:** vacía `discount_pct`, hace inevaluable la regla de
  publicación del escalón (`BIPRECIO_PUBLICACION_INDETERMINADA`) y es lo que
  `DQ_SIN_STOCK_CON_DESCUENTO` protege.
- **Valores posibles** — `MEDIDO` (3.030), `LISTA_SIN_PROMO` (132), vacío.
- **Vacía y qué significa** — no hay precio, o no hay `availability`: no se midió nada y declarar
  una procedencia sería inventarla. Cero veces acá.
- **Ejemplo** — `LISTA_SIN_PROMO` en Aceituna Negra OLIVALLE 1 kg (Surco): `price` 40.60 =
  `list_price` 40.60, `discount_pct` vacío, `precio_mayorista` 39.38 con estado
  `BIPRECIO_PUBLICACION_INDETERMINADA`.
- **Dónde vive** — `makro_plazavea.py:4782`, regla en `clasificar_origen_precio` (`:3529`).
- **Nota de esquema** — llegó en v20 y subió `SCHEMA_VERSION` a 6. Está **al final del dataclass a
  propósito**, aunque semánticamente pertenezca junto a `price`: `COLUMNAS` se deriva del orden del
  dataclass, y meterla en su lugar natural correría las 78 columnas de `golden_v5.csv` sin cambiar
  un solo dato. Un CSV anterior a v20 no la tiene, y no se puede reconstruir con certeza salvo
  releyendo su `raw.jsonl.gz`.

---

## Cierre 1 — Columnas cuyo propósito no pude determinar desde el código

Dos, y las dos se escriben sin que nada las lea.

**`sku_ref`** — sale de `items[].referenceId[].Value` (`:1664-1669`), se copia a la fila (`:4740`) y
ahí termina. No hay entrada de `CAMBIOS` que la introduzca, `docs/decisiones_1.1.0.md` no la
menciona y ningún test la usa. Se llena en las 3.162 filas con valores de un rango propio
(`20148211`, `20178701`…) distinto del `sku_id`, lo que **sugiere** un código de góndola o de ERP —
pero eso es inferencia mía sobre los datos, no algo que el código diga. Si sirve para cruzar contra
un sistema interno de Makro, ese es el propósito y hay que anotarlo; si no, es una columna que
nadie pidió.

**`base_price`** — el campo `price` crudo del ítem de VTEX (`:4763`). Nada lo lee.
`docs/decisiones_1.1.0.md` §6.6 la puso en la lista de "medir tasa de llenado 30 días antes de
decidir si se borra" y §11 dejó esa medición como pregunta abierta. **Esta corrida la contesta y el
resultado es peor que un vacío**: se llena en el 100% de las filas y es idéntica a `list_price` en
las 3.162, así que no aporta información alguna. Aplica la misma cautela que con `surtido_makro`:
un solo alcance (abarrotes, dos nodos, sin productos por peso) es evidencia fina para borrar. El
caso a mirar antes de decidir es un producto por peso, donde `price` y `listPrice` de VTEX podrían
divergir.

Nota aparte, que no es "propósito indeterminado" pero sí una ambigüedad estructural:
**`seller_id`** siempre vale `1` y no se puede saber si es el valor real o el default del parser
(`s(...) or "1"`, `:1693`). Su razón de existir sí está documentada (§6.5: si aparece un tercero de
marketplace va a traer otro valor).

---

## Cierre 2 — Columnas derivables de otras

En cada par, **la canónica es la que el motor usa para calcular** y la otra es conveniencia de
lectura. `docs/decisiones_1.1.0.md` §6.4 lo fija como regla: ninguna columna derivada reemplaza a
su origen.

| conveniencia | canónica | relación |
|---|---|---|
| `price` | **`price_cents`** | `price = price_cents / 100`. VTEX responde en céntimos y toda la aritmética del bi-precio es entera; los soles existen para que el CSV se lea. |
| `descuento_monto` | **`descuento_monto_cents`** | ídem. Si el descuento declarado no cae exacto en el céntimo, el estado va `INCONSISTENTE` en vez de redondear. |
| `precio_mayorista` | **`precio_mayorista_cents`** | ídem. La auditoría compara céntimo contra céntimo. |
| `discount_pct` | `list_price` + `price` + `price_origin` | derivable **solo con los tres**: sin `price_origin` no se puede distinguir un `0.00` medido de un `0.00` que nadie evaluó, y sin `unit_multiplier` no se sabe si los dos precios están en la misma base. |
| `descuento_mayorista_pct` | `price` + `precio_mayorista_cents` | `(price_cents − mayorista_cents) / price_cents`. Derivable, **pero la base es `price` y no `list_price`** — el error fácil es recalcularla contra el precio de lista y obtener otro número. |
| `price_per_unit` | `price` / `list_price` + `unit_multiplier` | **no siempre derivable por división.** Con `unit_multiplier != 1` el valor **se lee de `list_price`**, porque dividir amplifica el redondeo de VTEX. Dos ramas distintas, no una fórmula. |
| `precio_por_unidad_base` | `price_cents` + `cantidad_base` | derivable, salvo en la rama VTEX donde vuelve a leerse del servidor. |
| `precio_mayorista_por_unidad_base` | `precio_mayorista_cents` + `cantidad_base` | **siempre** derivada; VTEX no publica un equivalente. |
| `fecha` | `timestamp` | truncamiento. |
| `branch` | `node_id` | etiqueta legible de la clave. |
| `stock_signal` | `availability` + `chain_stock` | cruce de dos columnas, sin información nueva. |
| `error_class` | `logistics_status` + `http_status` | clasificación, sin información nueva. |
| `ean_type` | `ean` | función pura del EAN. Su valor está en que **niega** la utilidad del EAN en 26% de las filas. |
| `surtido_makro` | `seller_chain` + `node_id` | test de subcadena. Ver su entrada: la redundancia con `availability` es de esta muestra, no de la definición. |
| `base_price` | `list_price` | idéntica en las 3.162 filas de esta corrida. Ver Cierre 1. |

Las columnas de firma (`warehouse_id`, `dock_id`, `courier_id`, `courier_name`, `polygon_name`) **no
son derivables entre sí ni de `node_resolved`**: son la evidencia cruda de la que `node_resolved` es
la conclusión. Borrar la evidencia porque existe la conclusión es exactamente lo que impide auditar
una atribución dudosa.

---

## Cierre 3 — Invariantes que un lector podría asumir y que NO se cumplen

Cada uno está medido sobre `run_20260826_021034`.

**1. "Si `precio_mayorista` está lleno, `descuento_mayorista_pct` también."** Falso: **75 filas**
tienen precio mayorista y porcentaje vacío. Son todas `BIPRECIO_PUBLICACION_INDETERMINADA`. El
porcentaje mide el ahorro contra `price`, y en una fila sin stock `price` es el precio de lista: se
conoce el precio mayorista y no se conoce contra qué compararlo.

**2. "Si `bi_umbral` está lleno, hay `precio_mayorista`."** Falso: **396 filas**. 274 son
`SIN_DESCUENTO` (el catálogo declara el umbral y no hay descuento que restar) y 122 son
`BIPRECIO_SUPERADO_POR_PROMO` (hay umbral **y** descuento, y aun así no existe tal precio porque el
escalón no le gana a la promoción unitaria). Desde v18 el umbral sobrevive aunque el precio no: es
un hecho del catálogo, y vaciar el precio no puede obligar a vaciar el umbral.

**3. "Si hay `descuento_monto`, hay `precio_mayorista`."** Falso: **130 filas** — las 122
`BIPRECIO_SUPERADO_POR_PROMO` más 8 `SIN_UMBRAL`. El descuento es un hecho de VTEX y se publica
aunque el escalón no se pueda armar; es justamente lo que hace diagnosticable un `SIN_UMBRAL`.

**4. "`list_price − descuento_monto = precio_mayorista` en toda fila que tenga los tres."** Cierto
en las 3.162 filas de **esta** corrida, y **falso en la corrida siguiente**. Una fila auditada que
`DISCREPA` escribe el precio **medido** en `precio_mayorista` y **no toca `descuento_monto`**: ahí
la resta no cierra, a propósito. El desacuerdo entre lo que VTEX declara y lo que cobra **es** el
hallazgo, y borrar cualquiera de los dos lados lo borraría. La fila sale marcada
`DQ_MAYORISTA_DISCREPA`, y **ocurrió dos veces, con culpables distintos y con dos grados de
evidencia que no hay que confundir**:

| caso | corrida | evidencia | quién estaba mal |
|---|---|---|---|
| SKU 10012680 | `run_20260822_020027` | **no verificable hoy** | la auditoría |
| SKU 10012708 (Besos de Moza) | `run_20260827_021218` | **reproducible** | la fuente del descuento |

- **El caso histórico no se puede volver a mirar.** De `run_20260822_020027` no queda ni la carpeta
  ni una línea en `runs.jsonl`: la corrida fue borrada, así que su `filas.csv` y su `raw.jsonl.gz`
  ya no existen en disco. Lo que sigue en pie es el **registro** de lo que se midió entonces —el
  escalón declarado quedó por debajo de la promoción unitaria que ya corría a qty=1, VTEX cobró
  88.00 (su propio `price`, contra un `list_price` de 118.50) y **el lado equivocado era la
  auditoría**, no VTEX: comparaba contra una expectativa que no aplicaba. Léelo con el mismo criterio
  que `docs/decisiones_1.1.0.md` §11: es constancia de una verificación pasada, no algo que se pueda
  correr de nuevo. Si mañana alguien duda del número, **no hay contra qué chequearlo**.
- **El caso nuevo sí se puede reproducir.** SKU 10012708 en `run_20260827_021218`, en los dos nodos:
  `list_price` 20.50 − descuento 1.50 = 19.00, y VTEX cobra 19.90. El `raw.jsonl.gz` de esa corrida
  sigue en disco y muestra por qué —un profiler de medio de pago (`Promo Oh-Pay MAKRO`,
  `teaserType: "Profiler"`) cargando su descuento en el mismo campo que el escalón—; el diagnóstico
  completo vive en la entrada de `precio_mayorista` y no se repite acá. Acá importa solo que la
  invariante tiene un **contraejemplo verificable**, no hipotético.

En los dos casos la lectura correcta es la misma: *"uno de estos dos lados está mal"*, nunca *"VTEX
está mal"*. Los culpables salieron distintos justamente porque la bandera nombra una contradicción,
no un responsable.

**5. "`precio_mayorista_verificado` vacío significa que no se auditó."** Falso, y es el caso más
sutil del esquema. Puede significar (a) no hay precio mayorista que verificar, (b) **se midió
contra la red y lo verificado fue una AUSENCIA** — SKU 10020888 de esta corrida, remedido a qty=2,
`COINCIDE`, y el vacío se conservó porque escribir el precio unitario ahí publicaría un mayorista
igual al unitario — o (c) la fila nunca pasó por `enriquecer_fila`. En el CSV los tres son
idénticos; solo `run.json` los separa.

**6. "`precio_mayorista_verificado = SI` significa que esta fila se midió."** Falso: **2 de las 4
filas con `SI`** recibieron el veredicto **propagado** desde el otro nodo. La auditoría corre contra
un solo nodo (359 en esta corrida) y el veredicto se propaga porque lo medido es propiedad del SKU,
no del nodo. La distinción vive solo en `run.json` → `auditoria_mayorista.detalle[].propagado_a`.

**7. "`biprecio_status` tiene 6 valores."** Falso: tiene **8 valores más el vacío**, y esta corrida
mostró 6. `SIN_MEDICION`, `INCONSISTENTE` y el vacío de una fila `SKU_NO_ENCONTRADO` son
alcanzables y no se observaron. Un `CASE` que enumere solo los 6 observados va a caer en su `ELSE`
el día que aparezca un HTTP 500.

**8. "`logistics_status` y `price_status` tienen dos valores."** Falso: `logistics_status` tiene 13
formas alcanzables y `price_status` 5. Esta corrida mostró 2 de cada uno porque salió bien. Lo
mismo con `stock_signal` (4 posibles, 2 vistos), `fulfillment_type` (6 + vacío, 1 visto) y
`sla_status` (4, 1 visto). **Tipar cualquiera de estos enums desde una muestra es un error**;
tipalos desde el código.

**9. "`dq_flags` está siempre vacía."** Falso, y es la trampa que un muestreador casi seguro va a
morder. En esta corrida está vacía en las 3.162 filas, pero en `run_20260822_020027` trajo
`DQ_MAYORISTA_DISCREPA` en **1 fila de 3.174**. Una capa de consolidación que infiera tipos de una
muestra la va a tipar como float/`NaN` y después comparar contra `""` sin encontrar nada. Tipala
desde `Fila`.

**10. "Cinco columnas están siempre vacías, así que se pueden borrar."** Falso: `postal_resolved`,
`neighborhood_resolved`, `sla_selected`, `polygon_drift` y `error` vienen 100% vacías acá y
**ninguna es camino muerto**. Las tres primeras se llenan con `--modo orderform` (o el fallback por
SKU); `polygon_drift` se llena el día que VTEX versione el polígono; `error` se llena con cualquier
HTTP ≥ 400, `__error` en un 200 o excepción por SKU. Que estén vacías describe **esta** corrida:
salió por `simulation`, sin errores y sin deriva.

**11. "`node_id` es la sucursal que respondió."** Falso: `node_id` es la que se **preguntó**. La que
respondió es `node_resolved`, y en 132 filas de esta corrida vale `NONE` con precio presente. Todo
el proyecto existe por esta distinción.

**12. "`price` es lo que cobra la tienda."** Falso en 132 filas: con `price_origin =
LISTA_SIN_PROMO` es el precio de lista de un producto que la sucursal no puede despachar. Filtrar
por `price_status = 'VERIFIED'` o por `price_origin = 'MEDIDO'` **antes** de promediar cualquier
cosa.

**13. "Un `0.00` en `discount_pct` significa que no hay descuento."** Cierto solo con
`price_origin = MEDIDO`. En una corrida anterior a v20 ese mismo `0.00` aparece también en las filas
sin stock, donde no significa nada. Misma celda, dos significados, separados por `schema_version`.

**14. "Todas las filas de un mismo SKU en una corrida traen el mismo `precio_mayorista`."** Falso,
y hay que separar dos fuentes de varianza que se parecen y no son lo mismo.

- **La varianza que era artefacto ya no existe.** Antes de v22, una fila auditada se sobrescribía
  con lo medido en el nodo auditado y la otra quedaba reconstruida: el SKU 10012680 salió 88.00 en
  el 359 (medido, con flag) y 87.90 en el 360 (reconstruido, sin flag) **en la misma corrida**, y
  el 100% de la varianza de mayorista entre sucursales era artefacto de la auditoría, no una
  diferencia real de precio. Ese ejemplo es **histórico y no verificable hoy**: salió de
  `run_20260822_020027`, de la que no queda ni la carpeta ni una línea en `runs.jsonl` (ver la
  trampa 4). Vale como registro de lo que se midió entonces, con el criterio de
  `docs/decisiones_1.1.0.md` §11, no como algo que se pueda volver a correr. Lo que **sí** es
  verificable es que la propagación de v22 hace su trabajo: el SKU 10012708 de
  `run_20260827_021218` trae 19.90 y `DQ_MAYORISTA_DISCREPA` en los **dos** nodos, no solo en el
  auditado.
- **La varianza que queda es real y no tiene que ver con la auditoría.** Medido: **4 SKUs** en
  `run_20260826_021034` y **3** en `run_20260827_021218` traen distinto `precio_mayorista` según el
  nodo, y los siete casos tienen la misma forma —un nodo con stock y promoción unitaria, el otro sin
  stock—. Ejemplo: SKU 12165681, `list_price` 5.00, descuento 0.50; en el 359 (`available`, `price`
  2.80) el escalón no le gana a la promoción y la celda queda **vacía**
  (`BIPRECIO_SUPERADO_POR_PROMO`); en el 360 (`withoutStock`) VTEX no evalúa promociones y devuelve
  el precio de lista, así que `price == list_price` y la celda sale **4.50**
  (`BIPRECIO_PUBLICACION_INDETERMINADA`). No es que el mayorista difiera entre sucursales: es que en
  una de las dos **no se pudo evaluar**, y `biprecio_status` lo dice. Comparar mayorista entre nodos
  sin filtrar por ese estado mezcla un precio medido con uno que nadie pudo verificar.
