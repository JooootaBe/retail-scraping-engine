# Brief — tres correcciones rápidas

> **CERRADO — las tres shipearon.** (nota agregada 2026-08-26)
>
> (1) El falso positivo de truncamiento en v21, commit `8569056`, con la decisión extraída a
> `evaluar_fin_de_paginado` y `test_truncamiento.py`. (2) La auditoría del mayorista en v22,
> commit `03e2189`, con `test_auditoria_mayorista.py`. (3) `surtido_makro` se resolvió como
> documentación en `a2b6775`: se midió colineal con `availability`, y **no se eliminó** — §6.6
> pide 30 días de medición antes de sacar una columna. Ésa es la única de las tres que dejó algo
> pendiente: **revisar a los 30 días, o al salir de abarrotes.**
>
> El conteo de "los 21 tests actuales" que pide más abajo es de la fecha del brief; hoy son 59.
> **No hay nada que ejecutar acá**, salvo la revisita de `surtido_makro`.

Para pasar a Claude Code. Contexto: `retail_scraping_engine`, colector `makro_plazavea`,
después de `SCHEMA_VERSION 6`.

Ninguna de las tres es urgente: las tres se reparan hacia atrás recalculando desde `raw.jsonl.gz`.
Van juntas porque son acotadas y porque dejan el motor sin defectos conocidos.

**Condición para las tres:** los 21 tests actuales tienen que seguir en verde antes y después.
Si alguno se pone rojo, el cambio está mal, no el test.

**Commits separados.** Tocan tres fases distintas (descubrimiento, auditoría, documentación) y
conviene poder revertir una sin arrastrar las otras.

---

## 1. Falso positivo de truncamiento

### El hecho

`run_20260822_020027` y `run_20260824_154502` quedaron ambas clasificadas como
`INCOMPLETO_NO_PLANEADO` por `TRUNCAMIENTO_VTEX`, con un único fallo:

```
"categoria": "Fideos Largos",
"error": "quedó truncada en 50 productos: alcanzó el techo de paginado (~2450)
          sin una página corta que confirmara el final"
```

El mensaje se contradice: 50 no es 2450.

### La causa

En el bucle de paginado de `descubrir_catalogo` (en la versión anterior estaba alrededor de la
línea 1241; verificar la posición actual):

```python
techo = min(total, TOPE_VTEX) if total_conocido else TOPE_VTEX

if desde >= techo:
    if not pagina_corta:
        truncada = True
    break
```

Cuando el total declarado por la cabecera `resources` es un **múltiplo exacto de `VENTANA`**, la
última página vuelve llena, `pagina_corta` queda en `False`, `desde` alcanza el techo — y se marca
truncada aunque se haya leído la categoría entera.

Para "Fideos Largos": `total = 50`, `techo = 50`, `desde = 50`, página de 50 items.
`categorias_resources_desconocido = 0` confirma que el total sí se conocía.

### El arreglo

Distinguir de qué techo se trata. Solo el techo de paginado de VTEX indica truncamiento; alcanzar
el total declarado es el final legítimo.

```python
techo_de_vtex = (not total_conocido) or total > TOPE_VTEX

if desde >= techo:
    if not pagina_corta and techo_de_vtex:
        truncada = True
    break
```

Casos que tienen que seguir funcionando:

| situación | `truncada` |
|---|---|
| total conocido = 50, página llena de 50 | **False** (hoy da True — el bug) |
| total conocido = 2500 > TOPE, página llena | True |
| total desconocido, página llena | True |
| página corta en cualquier caso | False |

### Test

`tests/test_truncamiento.py`, casos construidos a mano sobre la función de paginado (no sobre la
red). Los cuatro de la tabla. Si la función no es invocable sin requests, extraer primero la
decisión a una función pura.

---

## 2. La auditoría del mayorista

Dos defectos independientes en la misma fase.

### 2a. Espera el valor equivocado cuando el escalón está suprimido

La auditoría remide a `qty = bi_umbral` y compara contra el reconstruido. Con la fórmula corregida
hay **dos expectativas distintas** según el estado:

| `biprecio_status` | qué debe cobrar VTEX a qty=umbral |
|---|---|
| escalón publicado | `list_price − descuento` |
| `BIPRECIO_SUPERADO_POR_PROMO` | `price` (el escalón no aplica) |
| `BIPRECIO_PUBLICACION_INDETERMINADA` | **no auditable** — excluir de la muestra |

Sin esto, cualquier SKU suprimido que caiga en la muestra va a reportar una discrepancia
inexistente. Evidencia de que la regla es correcta: el SKU 10012680 en `run_20260822_020027` se
midió en 88.00 = su `price`, exactamente lo que predice la segunda fila.

### 2b. El veredicto no se propaga al nodo gemelo

`auditoria_mayorista` corre solo contra el nodo 359. En `run_20260822_020027` el SKU 10012680
quedó con **dos precios mayoristas distintos en la misma corrida**: 88.00 en el 359 (medido, con
`DQ_MAYORISTA_DISCREPA`) y 87.90 en el 360 (reconstruido, sin flag).

Lo que la auditoría aprende es una propiedad del SKU —qué cobra VTEX a esa cantidad—, no del nodo.
El resultado debe propagarse a todas las filas del mismo `sku_id`, con el flag correspondiente.

Verificación: en `run_20260822_020027`, esa era la **única** diferencia de mayorista entre nodos
en los 1482 SKUs con stock en ambos. O sea que el 100% de la varianza mayorista entre sucursales
era artefacto de la auditoría.

### 2c. Estratificar la muestra

Hoy toma 3 SKUs de ~2000 `COMPLETO` (0.15%) sin criterio. El bug de la fórmula sobrevivió seis
semanas precisamente porque la muestra no cubría el eje donde fallaba.

Estratificar por el eje que importa: **con promoción unitaria** (`price < list_price`) contra
**sin promoción unitaria**. Ambos lados representados en cada corrida.

Nota: no hace falta subir mucho el número. La muestra es aleatoria por corrida, así que la serie
acumula cobertura sola — en `run_20260824_154502` salieron tres SKUs distintos a los del 22. Con
que cada corrida cubra ambos lados del eje, en un mes hay cobertura amplia sin costo extra.

---

## 3. `surtido_makro`

### El hecho

Es **100% colineal con `availability`** en `run_20260822_020027`: 3031 filas `SI`/`available` y
143 `NO`/`withoutStock`, cero excepciones. Lo mismo en la corrida del 24.

La causa es estructural: lee `seller_chain`, que viene vacío justamente cuando no hay logística
que resolver, o sea cuando no hay stock. No mide surtido — mide disponibilidad con otro nombre.

### Qué hacer

**No es un cambio de código.** Es documentación, y la decisión es de Joan:

- documentar en `CLAUDE.md` que `surtido_makro` es un alias de `availability` y que la pregunta
  "¿este SKU pertenece al surtido de este nodo?" **hoy no está respondida por ninguna columna**, o
- redefinir su fuente para que mida surtido de verdad (requiere consultar el catálogo bajo el
  contexto del nodo, no la simulación), o
- eliminarla.

Anotar también que la pregunta se puede responder sin código: un SKU que nunca aparece con stock
en un nodo durante 30 días es, con alta probabilidad, ausencia de surtido. La serie lo resuelve
sola.

---

## Fuera de alcance

- **Parser de empaques.** 8 SKUs donde ignora el conteo (`Caja 20un x 24g` → 24g en vez de 480g).
  Va aparte porque el regex acierta en 156 de 164 nombres anidados: tocarlo sin un fixture con los
  164 casos arriesga romper los que funcionan. Primero el fixture, después el arreglo.
- **Que el descubrimiento pese en `corrida_completa`.** Hoy `exit_code 0` y
  `corrida_completa: true` conviven con `clasificacion: INCOMPLETO_NO_PLANEADO`. Después de la
  corrección 1 el truncamiento va a ser raro, pero cuando sea real debería contar. Decisión de
  diseño, no defecto.
- Regenerar `golden_v5.csv` con `SCHEMA_VERSION 6`.
