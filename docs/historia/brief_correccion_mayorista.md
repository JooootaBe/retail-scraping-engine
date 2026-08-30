# Brief — corrección de la fórmula del precio mayorista

> **CERRADO — shipeó en v18, commit `a84a3e5`.** (nota agregada 2026-08-26)
>
> Se lee como orden de trabajo abierta y no lo es. `precio_mayorista` se calcula desde
> `list_price` desde v18, `SCHEMA_VERSION` pasó de 4 a 5 sin agregar una columna, y las 23 fichas
> del storefront quedaron fijadas como regresión en
> `tests/makro_plazavea/test_precio_mayorista.py` (23/23). Se conserva porque registra la
> evidencia que forzó el cambio y por qué la fórmula vieja pudo estar mal seis semanas sin que
> nada la delatara. **No hay nada que ejecutar acá.**

Para pasar a Claude Code. Contexto: `retail_scraping_engine`, colector `makro_plazavea`.

---

## El error

`docs/decisiones_1_1_0.md` §1 define:

```
precio_mayorista = commertialOffer.Price − PromotionalPriceTableItemsDiscount
```

La base es la equivocada. El descuento se resta sobre el **precio de lista**:

```
precio_mayorista = commertialOffer.ListPrice − PromotionalPriceTableItemsDiscount
```

Y hay una segunda regla, que antes no existía:

```
si precio_mayorista >= price:
    el escalón NO se publica ni se cobra
    → la promoción unitaria le gana al escalón mayorista
    → precio_mayorista queda VACÍO, con estado propio
```

**Por qué pasó desapercibido seis semanas:** en 2021 de las 2328 filas `COMPLETO` no hay
promoción unitaria, así que `price == list_price` y las dos fórmulas colapsan en la misma. La
auditoría de v1.1.0 tomó 3 SKUs, 2 sin promo (pasaron) y 1 con promo (falló, y se atribuyó a
redondeo). La fórmula nunca estuvo probada donde podía fallar.

## La evidencia

`tests/fixtures/makro_plazavea/fichas_publicadas_20260822.csv` — 23 fichas de producto observadas
a mano en el storefront el 2026-08-22. La fórmula corregida las reproduce **23 de 23**: 11 que
publican escalón, con el precio exacto al centavo, y 12 que no publican, todas predichas por la
regla `>= price`.

Reinterpretación de la auditoría de la corrida `run_20260822_020027`:

```
SKU 10012680 — Aceite HUERTO ALAMEIN, qty=3, medido 88.00
  fórmula vieja:  88.00 − 0.10 =  87.90   ✗
  fórmula nueva: 118.50 − 0.10 = 118.40  ≥ 88.00 → no aplica → se paga price = 88.00  ✓
```

La auditoría pasa **3/3**. No hubo discrepancia nunca; el motor esperaba el número equivocado.

## Impacto sobre `run_20260822_020027`

| | filas |
|---|---|
| `biprecio_status = COMPLETO` | 2328 |
| cambian de valor | 307 |
| pasan a "no publicado" | 233 |
| sin cambio (no tenían promo unitaria) | 2021 |

---

## Cambios pedidos

### 1. La fórmula
Un solo punto en el código. Buscar dónde se calcula `precio_mayorista` y cambiar la base de
`Price` a `ListPrice`. **No duplicar la aritmética en ningún otro archivo.**

### 2. Estado nuevo en el enum de `biprecio_status`

`BIPRECIO_SUPERADO_POR_PROMO` — hay umbral y descuento declarados, pero
`list_price − descuento >= price`. Se conoce el umbral; no se ofrece precio mayorista.

`precio_mayorista`, `precio_mayorista_cents`, `descuento_mayorista_pct` y
`precio_mayorista_por_unidad_base` van **vacíos** en ese estado. `bi_umbral` **se conserva**
(ver punto 3).

### 3. Reescribir la regla §6.2

Hoy dice: si `biprecio_status != COMPLETO`, se vacían `precio_mayorista*` **y** `bi_umbral`.

Eso destruye evidencia. En `SIN_DESCUENTO` (236 filas) y en el estado nuevo (233 filas) el umbral
**sí se conoce y es real** — lo que no existe es el precio. Vaciar el precio no debe obligar a
vaciar el umbral.

Nueva regla: `bi_umbral` se vacía **solo** cuando el catálogo no lo declara.

### 4. `descuento_mayorista_pct`

Definir explícitamente la base y documentarla. Propuesta: ahorro respecto de `price` —lo que el
cliente deja de pagar comprando de a uno— y no respecto de `list_price`. Antes daba igual; ahora
difiere en 307 filas.

### 5. La auditoría del mayorista

`auditoria_mayorista` compara lo medido contra el reconstruido. Ahora tiene que esperar dos cosas
distintas según el estado:

- escalón publicado → espera `list_price − descuento`
- `BIPRECIO_SUPERADO_POR_PROMO` → espera `price` (el escalón no aplica)

Sin esto va a seguir marcando discrepancias inexistentes.

Y **estratificar la muestra**: hoy toma 3 SKUs de 2328 (0.13%) sin criterio. Debe cubrir ambos
lados del eje "con promo unitaria / sin promo unitaria", que es donde vivía el bug.

### 6. Propagar el veredicto de la auditoría al nodo gemelo

Hoy la auditoría corre solo contra el nodo 359 y el 360 se queda con el reconstruido. En
`run_20260822_020027` el SKU 10012680 quedó con **dos precios mayoristas distintos en la misma
corrida** (88.00 en 359 con flag, 87.90 en 360 sin flag). Lo que la auditoría aprende es una
propiedad del SKU, no del nodo.

### 7. `SCHEMA_VERSION` 4 → 5

No es opcional. La columna `precio_mayorista` cambia de significado y la serie histórica va a
tener filas de las dos épocas conviviendo. Sin el bump no hay forma de distinguirlas.

### 8. Documentación

- `docs/decisiones_1_1_0.md` §1 — corregir la fórmula, dejando constancia de cuál era la anterior
  y por qué era incorrecta.
- `docs/contradicciones.md` — entrada nueva: la fórmula estuvo mal seis semanas y el costo de
  descubrirlo fue cero **porque `raw.jsonl.gz` guardaba el crudo**. Es la primera vez que la
  decisión de no descartar evidencia se paga sola.

---

## Recálculo de la corrida existente

`raw.jsonl.gz` de `run_20260822_020027` debería tener `ListPrice` y
`PromotionalPriceTableItemsDiscount` por SKU. Si es así, **la corrida se recalcula sin volver a
scrapear**: 3814 requests que no hay que repetir.

**Verificar primero** que el crudo guarde la respuesta del catálogo y no solo la de `simulation`.
Si solo guarda `simulation`, no hay recálculo posible y hay que volver a correr.

Dos condiciones:

1. **Implementarlo como `--recalcular <ruta_del_run>` dentro del colector**, no como script
   aparte. Así la fórmula existe una sola vez en el código; una copia en `tools/` diverge en tres
   meses y nadie sabrá cuál generó qué.
2. **Escribir un run nuevo, nunca encima del viejo.** La corrida con la fórmula mala se queda
   donde está: es la evidencia de que el bug existió y de cómo se veían los datos antes.

---

## Tests

### Regresión — permanente

Contra `fichas_publicadas_20260822.csv`. Exige **23/23**. Es lo único que impide que el bug
vuelva.

Cada fila trae los insumos de la API (`price_api`, `list_price_api`, `descuento_api`,
`bi_umbral_api`) y la verdad observada (`umbral_ficha`, `precio_escalon_ficha`,
`publica_escalon`, `mayorista_esperado`). El test computa desde los insumos y compara contra lo
observado — es autocontenido, no depende de `filas.csv` ni de la red.

Este fixture es la **única referencia externa** del proyecto. Todos los demás salen de la misma
API que se está midiendo; si VTEX miente, mienten con él. Estas 23 filas salieron de lo que Makro
le imprime al cliente, y costaron una tarde de capturas que no se puede reproducir
automáticamente.

### Sonda — `tests/probes/makro_plazavea/precio_mayorista_corregidov6.py`

Siguiendo la convención de la carpeta. Documenta *cómo* se descubrió: toma las 23 fichas, corre
las dos fórmulas y muestra que la vieja acierta 11/23 y la nueva 23/23.

Nota sobre el nombre: `precio_mayorista_encontradov5.py` afirma en el nombre del archivo que el
precio fue encontrado, y estuvo mal seis semanas. **No borrarlo** — es el registro de que se creyó
haberlo encontrado. Pero conviene que "encontrado" quede reservado para lo validado contra verdad
externa, que es lo que recién pasó hoy.

---

## Fuera de alcance de esta corrección

Van aparte, no mezclar en el mismo commit:

- **Precio en quiebre** — 143 filas con `price == list_price` y `discount_pct = 0.00`. VTEX no
  evalúa promociones sin stock y devuelve lista; la fila afirma un precio que nadie puede pagar.
- **Falso positivo de truncamiento** — una categoría con total múltiplo exacto de `VENTANA` se
  marca truncada. Dejó la corrida como `INCOMPLETO_NO_PLANEADO` sin serlo.
- **Parser de presentación** — 8 SKUs donde ignora el conteo y toma solo el peso de la pieza
  (`Caja 20un x 24g` → 24g en vez de 480g). Error de 3× a 16× en `precio_por_unidad_base`.
