# Decisiones para 1.1.0 — `SCHEMA_VERSION 4`

Inventario cerrado de lo que entra en la versión con precio mayorista. Todo lo de acá está
verificado contra evidencia; lo que es supuesto está marcado como tal.

**Alcance:** agregar el precio mayorista y corregir bugs conocidos. Nada más.

---

## 1. Bi-precio: mecanismo verificado

```
precio_mayorista = commertialOffer.Price − PromotionalPriceTableItemsDiscount
umbral           = CantidadBiPrecioMK   (specification del producto)
```

Verificado al centavo en 5 SKUs, remedidos contra checkout:

| Price | Descuento | Reconstruido | Medido a qty=umbral |
|---|---|---|---|
| 13.70 | 0.90 | 12.80 | 12.80 |
| 13.30 | 0.27 | 13.03 | 13.03 |
| 33.80 | 1.80 | 32.00 | 32.00 (qty 2) |
| 12.50 | 1.00 | 11.50 | 11.50 (qty 4) |
| 15.50 | 0.52 | 14.98 | 14.98 (qty 3) |

**Reglas:**

- El **umbral** solo existe en la respuesta de **catálogo**. El **descuento** viene en catálogo
  y en `simulation`. Ninguna fuente basta sola: hay que unir por `sku_id`.
- **Un solo escalón.** `CantidadTriPrecioMK` está declarado y **no se aplica** en checkout
  (medido en qty 1/3/5/6/7: el precio no vuelve a bajar). Se registra como
  `tri_umbral_declarado` y nunca se usa para calcular.
- `priceDefinition.reason = "priceTable"` aparece **siempre**, incluso en qty=1 sin descuento.
  **No es detector.** El detector de aplicación es `priceTags`: vacío en qty=1, con
  `discount@price-table-bipreciomakro-…` desde el umbral.
- El régimen VTEX se lee del **teaser**, no de `rateAndBenefitsIdentifiers`: ese array está
  **vacío en qty=1** y el motor mide a qty=1, así que como fuente es inutilizable.
  `promo_regime_id` = `teaser.id`; `promo_regime_name` = `teaser.name`. El catálogo trae el
  teaser **sin `id`** (solo nombre) y `simulation` lo trae con `id`: la medición es la fuente
  preferida, el catálogo el respaldo.
- Un solo régimen cubre todo el bi-precio de Makro: `225a92ff-a721-4f76-8856-2cba133c12d8`,
  de nombre **`MAKRO-Bi-Precio|Vigente Oculto`** — las 24 filas con bi-precio de `golden_v5.csv`
  lo llevan. Identifica el régimen, no agrupa productos. Si cambia, Makro reconfiguró su esquema
  mayorista. *(El nombre "Bi Precio Vigente Regular MAKRO" que circulaba antes no existe: era una
  fusión de dos nombres distintos, el de arriba y `Precio Vigente Regular MAKRO`, que es otro
  régimen — 6 filas del golden, sin bi-precio.)*
- `PaymentMethodId = 4` es **informativo, no restrictivo**: el descuento se aplica sin enviar
  método de pago. Además, `4` no corresponde a ninguno de los sistemas disponibles
  (206, 208, 209, 210).
- Buscar `PromotionalPriceTableItemsDiscount` **por nombre de parámetro, a cualquier
  profundidad**. Nunca por índice `Parameters[1]`: el orden del array no está garantizado.
- El sufijo tras `#` en el nombre del `priceTag` es único por respuesta. No usar como llave.
- Los descuentos con tres dígitos decimales tienen el tercero en cero (`0.270` = `0.27`).
  Los 26 casos observados dan centavo exacto. **No hay problema de redondeo.**

**El umbral sigue al empaque físico, no a la categoría:** 24 en latas (una caja), 12 en
botellas medianas, 6 en litro, 2 en galoneras, 15 en botellas chicas de lejía. Makro premia
no abrir el bulto.

---

## 2. Surtido Makro vs Plaza Vea

El endpoint de catálogo **no tiene contexto de sucursal**: devuelve el catálogo de la cuenta
VTEX, que es de Plaza Vea. Una sonda que usó solo catálogo produjo 1157 filas con
`seller_chain = "1"` y `node_id = SIN_CONTEXTO_SUCURSAL`. Ninguna verificada.

- `sellerName = "Makro Plazavea"` sobre `sellerId = 1` **no prueba nada**. Es el nombre de la
  tienda VTEX completa.
- La única prueba de surtido Makro es `sellerChain` conteniendo `plazaveamko<nodo>`
  **después** de medir con `simulation`.
- Las filas que no lo cumplen **se escriben igual**, marcadas. No se descartan en silencio.

---

## 3. Precio sin cobertura resuelta

Un precio devuelto con `cannotBeDelivered` o sin logística resuelta **no es un precio
regional**: es el precio por defecto del seller. VTEX lo devuelve siempre, haya o no cobertura.

Las comparaciones entre nodos solo usan filas `VERIFIED` / `VERIFIED_SELLER_RAIZ`.

---

## 4. Peso variable — corrección a lógica existente

Cuando `unit_multiplier != 1`, `list_price` **no es precio tachado**: es el precio por unidad
base.

```
Maracuyá:  price = 1.23  (pieza de 0.2 kg)   list_price = 6.19  (el kilo)
Bife Ancho: price = 68.85 (pieza de 1.5 kg)  list_price = 46.50 (el kilo)
```

El Bife Ancho lo confirma: `price` > `list_price`, imposible en un precio tachado.

- `precio_por_unidad_base` se **lee** de `list_price`. **No** se calcula por división:
  `1.23 ÷ 0.2 = 6.15` y el real es `6.19`. El redondeo de VTEX se amplifica al dividir por un
  multiplicador menor a 1.
- `discount_pct` va **vacío** en peso variable: calcularlo daría 80% de descuento falso.
- Guardar además la reconstrucción como control: si difiere de `list_price` en más de un
  céntimo, es señal de que el multiplicador declarado no corresponde al precio.
- El peso es un **promedio declarado** ("0.2 kg aprox."), no el peso real de la pieza. Por eso
  `price` en peso variable es estimado y `list_price` es contractual.

**El golden v5 no aplica esta regla, y no se regenera.** La corrección se decidió *después* de
que la sonda v5 corriera: `golden_v5.csv` guarda `6.1500` para el Maracuyá — la división que este
apartado señala como equivocada — y lo mismo en las 8/8 filas de peso variable. El golden queda
como está; `precio_por_unidad_base` y `precio_mayorista_por_unidad_base` se **excluyen** del
criterio de aceptación (§10). La regla es la de arriba, no la del golden.

---

## 5. Columnas nuevas (22)

9 + 4 + 4 + 5. Con las 56 del motor: 56 + 22 = **78**, que es exactamente el header de
`golden_v5.csv`.

### Bi-precio (9)

```
bi_umbral                      int      CantidadBiPrecioMK. Vacío si no hay
tri_umbral_declarado           int      se registra, NUNCA se aplica
descuento_monto                Decimal  PromotionalPriceTableItemsDiscount
descuento_monto_cents          int      canónico para cálculo
precio_mayorista               Decimal  legible
precio_mayorista_cents         int      price_cents − descuento_monto_cents
descuento_mayorista_pct        Decimal  legible: 25.50, no 0.2550
biprecio_status                enum
precio_mayorista_verificado    enum     SI (medido a qty≥umbral) | NO (reconstruido)
```

`biprecio_status`:

```
SIN_BIPRECIO    sin umbral y sin descuento
COMPLETO        umbral + descuento + precio unitario verificado (price_status = VERIFIED)
SIN_DESCUENTO   umbral declarado, teaser ausente → mayorista DESCONOCIDO
SIN_UMBRAL      descuento sin umbral
SIN_MEDICION    descubierto, la simulation falló
INCONSISTENTE   mayorista ≤ 0, mayor que unitario, o no da centavo exacto
```

«Precio verificado» en `COMPLETO` es el **unitario**: `price_status = VERIFIED`. **No** es
`precio_mayorista_verificado = SI`, que mide otro eje — si el mayorista se midió a qty≥umbral o
se reconstruyó con la fórmula de §1. Los dos ejes son independientes: un `COMPLETO` con
`precio_mayorista_verificado = NO` es normal, y así están 21 de las 24 filas `COMPLETO` del
golden. Leer `COMPLETO` como si exigiera medición del mayorista produciría 3 filas `COMPLETO`
donde el baseline espera 24.

### Régimen promocional (4)

```
promo_regime_id       teaser.id — NO rateAndBenefitsIdentifiers (vacío en qty=1, §1)
promo_regime_name     teaser.name — ej. "MAKRO-Bi-Precio|Vigente Oculto"
payment_method_id     se registra, no se interpreta
price_valid_until     3000-01-02 es el centinela de "sin vencimiento".
                      Una fecha real sería vencimiento de campaña en la fuente
```

### Identidad y calidad (4)

```
ean_type          GS1_GLOBAL | INTERNO_RESTRINGIDO | FALTANTE | INVALIDO
category_id       /399/604/612/  — estable; el texto de categoría no lo es
sales_channel     el sc de la petición. Constante 9 hoy
surtido_makro     SI solo si seller_chain contiene plazaveamko<node_id>
```

`ean_type`: prefijo 20-29 → `INTERNO_RESTRINGIDO` (rango GS1 de distribución restringida,
códigos que genera la tienda para peso variable y producción interna). Longitud 8/12/13/14 con
dígito verificador correcto → `GS1_GLOBAL`. Vacío → `FALTANTE`. Resto → `INVALIDO`.

Medido: **50% GS1_GLOBAL, 25% INTERNO_RESTRINGIDO, 25% FALTANTE**. La mitad del catálogo no
cruza contra otro retailer. Sin esta columna, ese 50% produce falsos negativos silenciosos.

### Presentación (5)

```
unidad_base                        kg | l | un
cantidad_base                      Decimal
presentacion_origen                VTEX | NOMBRE | DESCONOCIDO
precio_por_unidad_base             Decimal
precio_mayorista_por_unidad_base   Decimal
```

Regla de dos ramas:

- `measurement_unit != 'un'` → usar `unit_multiplier` de VTEX. `presentacion_origen = VTEX`.
  **Autoritativo** (peso variable: carnes, frutas, verduras).
- `measurement_unit == 'un'` → parsear del nombre. `presentacion_origen = NOMBRE`. **Heurística.**
- Si no se puede → `DESCONOCIDO` y las derivadas vacías.

### Marca y confianza — **no entra en 1.1.0, pasa a 1.1.1**

`presentacion_confianza`, `brand_dq` y `es_marca_propia` se propusieron sin ninguna medición
detrás y no están en el golden. Fuera del conteo de 22 y fuera del esquema de 1.1.0 (§12).

Lo que sí rige desde ya: la sucursal **nunca** va en `brand`. `brand` es quién fabrica; el nodo
ya está en seis columnas.

---

## 6. Reglas duras del esquema

1. Todo cálculo monetario en **centavos enteros o `Decimal`**. `float` solo para lectura humana.
2. **Vacío ≠ cero.** Si `biprecio_status != COMPLETO`, `precio_mayorista*` y `bi_umbral` van
   vacíos. Nunca cero, nunca igual al unitario.
3. El precio nunca se descarta. El juicio va en `price_status`, el porqué en
   `logistics_status`, la duda en `dq_flags`.
4. Ninguna columna derivada reemplaza a su origen: `descuento_monto` se guarda aunque
   `precio_mayorista_cents` ya lo incorpore.
5. Ninguna columna existente se elimina. `seller_id` **se queda**: es campo de origen
   (`items[].seller`), y si aparece un tercero de marketplace va a traer otro valor.
6. Una columna vacía hoy no se elimina hoy. Medir tasa de llenado 30 días:
   `base_price`, `postal_resolved`, `neighborhood_resolved`.
7. `SCHEMA_VERSION` sube a 4.

---

## 7. Salida de datos: una carpeta por corrida

```
data/makro_plazavea/
├── run_20260820_051500/
│   ├── raw.jsonl.gz
│   ├── run.json          manifiesto: flags, categorías, nodos, filas, requests, duración, estado
│   └── filas.csv
├── run_20260820_140200/  segunda corrida del mismo día
├── runs.jsonl            append-only, una línea por corrida
└── last_run.json         copia del manifiesto de la última
```

- La carpeta se llama **igual que el `run_id`**: un solo identificador, imposible de
  desincronizar.
- Formato `YYYYMMDD_HHMMSS`: alfabético = cronológico.
- Nombres **fijos** adentro, sin timestamp repetido. Leer toda la historia es un glob:
  `read_csv('data/makro_plazavea/run_*/filas.csv')`.
- Una sola función `carpeta_corrida(run_id)` construye la ruta. **Nadie escribe fuera de ella.**
- El motor **nunca** escribe exports. Los por sucursal o categoría salen de un comando aparte
  hacia `data/exports/`.

---

## 8. Formato del CSV

**Un solo archivo, formato largo.** Clave primaria `(run_id, node_id, sku_id)`: una fila por
SKU por nodo.

Nunca un CSV por sucursal. Nunca columnas tipo `precio_359` / `precio_360`: cada nodo nuevo
cambiaría el esquema.

Justificación medida sobre `golden_v5.csv` (20 SKUs × 2 nodos): de las 56 columnas del motor,
**24 difieren** entre 359 y 360 en al menos un SKU. Once son de identidad del nodo (`branch`,
`courier_id`, `courier_name`, `delivery_channel`, `dock_id`, `node_id`, `node_resolved`,
`polygon_name`, `postal_sent`, `seller_chain`, `warehouse_id`); las otras **trece no lo son**:
`availability`, `error_class`, `fulfillment_confirmed`, `fulfillment_type`, `logistics_status`,
`price_status`, `shipping_cost`, `shipping_estimate`, `sla_count`, `sla_name`, `sla_status`,
`stock_signal`, `timestamp`. *(El "solo 11" anterior se midió sobre los CSV del smoke test —
10 SKUs — no sobre el golden, y coincidía exacto con el subconjunto de identidad.)*

Cuantas más columnas divergen por nodo, más caro sale el formato ancho: 24 lo refuerzan, no lo
debilitan. El ancho para reportes se proyecta con un pivot en la capa de consolidación, no lo
produce el motor.

---

## 9. Bugs conocidos a corregir

| Bug | Corrección |
|---|---|
| `MOTOR = "makro"` produce `data/makro/` | → `makro_plazavea`. El colector se identifica por **fuente**, no por retailer: `makro_pe` es el mismo retailer |
| `RAIZ_SALIDA = BASE_DIR/"salida"` resuelve dentro del paquete (`src/retail_engine/collectors/salida/`) | derivar de la raíz del repo, buscando `pyproject.toml` hacia arriba |
| Parser de presentación con multipack tras la medida: `600ml Paquete 6un` da 0.6 L, son 3.6 L | regla de multiplicación por el conteo posterior |
| `fulfillment_type = "desconocido"` con courier + dock + SLA resueltos y almacén no-Makro | → `operador_externo`. Reservar `desconocido` para falta de información |
| Sondas v1/v4: `MOTOR_PY` apunta a `mk_scraping_engine_0.1.0.py` | ruta al colector actual |
| Sondas v2/v3: `V1_PY` busca `v1.py`, el archivo tiene otro nombre | renombrar sondas con ordinal: `01_…` … `05_…` |
| `pyproject.toml` vacío | metadata + `where = ["src"]` + `pip install -e .` |
| `--reiniciar` borra los CSVs para "reiniciar la serie": con una carpeta inmutable por corrida (§7) no hay archivo acumulado que borrar, y el flag apuntaría a historia ya cerrada | definir semántica nueva y explícita (¿vaciar `data/<colector>/` entero?, ¿solo `runs.jsonl` + `last_run.json`?) o **eliminar el flag**. Lo que no puede quedar es el comportamiento viejo con el layout nuevo |

---

## 10. Criterio de aceptación

Correr el motor 1.1.0 sobre los **mismos 20 SKUs y 2 nodos** de la sonda v5 y hacer `diff`
contra `tests/fixtures/makro_plazavea/golden_v5.csv` (40 filas, 78 columnas: las 56 del motor
más las 22 de §5).

Debe reproducir las 40 filas columna por columna, **con estas exclusiones explícitas**. Sin
ellas el criterio es insatisfacible por construcción:

- **Volátiles** — cambian en cada corrida y nunca pueden coincidir: `run_id` (el golden trae
  `v5_20260820_000016` en las 40 filas), `timestamp`, `fecha`, y `schema_version` (§6.7 lo sube
  a `"4"`; el golden nació con `"3"`).
- **Cambiadas por diseño** — el golden es anterior a la decisión y no se regenera:
  `precio_por_unidad_base` y `precio_mayorista_por_unidad_base` (§4: se leen de `list_price`, el
  golden los dividió), `discount_pct` en las filas de peso variable (§4: va vacío) y
  `fulfillment_type` donde 1.1.0 reclasifica `desconocido` → `operador_externo` (§9).

El resto debe coincidir **exacto**, valor por valor. Si no, no está listo.

Una diferencia fuera de esa lista es un fallo, no un ajuste del criterio: el golden es la
evidencia de qué medía el motor antes de estos cambios y no se reemplaza para acomodar al código.

---

## 11. Verificaciones pendientes — no bloquean 1.1.0

- **Auditoría de la fórmula en umbral alto.** Solo se probó en umbrales 2, 3 y 4. Los altos
  (12, 15, 24) son donde están los descuentos grandes: MONSTER 7.34%, Lejía SAPOLIO 770g
  25.5%. Un request lo cierra.
- **Tasa de llenado** de `base_price`, `postal_resolved`, `neighborhood_resolved` tras 30 días.
- **Corrección del parseo de presentación** no es verificable: VTEX no publica el contenido
  total de un empaque `measurement_unit = un`. Se mide cobertura, no acierto.
- **Cobertura real del bi-precio.** Las muestras de las sondas sobre-representan a propósito
  los SKUs con umbral declarado. El 56% medido no es cobertura del catálogo.
- **Que umbral y descuento apliquen igual en todos los nodos** está asumido, no verificado:
  vienen del catálogo, que no tiene sucursal.

---

## 12. Explícitamente fuera de alcance

No entra en 1.1.0, aunque parezca cercano:

- `assortment_status` y descubrimiento por nodo (distingue *no listado* de *no despachable*)
- Flag `--categorias` y descubrimiento del árbol de categorías
- Parquet y PostgreSQL
- Colector `makro_pe`
- `presentacion_confianza`, `brand_dq` y `es_marca_propia` — propuestas sin medición detrás;
  quedan para 1.1.1 (§5)
- Tests de golden file automatizados
- Abstracciones multi-retailer

**Una versión, una capacidad.** Esquema y estrategia de cobertura son dos cosas distintas: si
fallan juntas, un error se esconde detrás del otro.