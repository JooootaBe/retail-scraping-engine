# Historia — etapa "motor de inteligencia de precios sin cliente"

Esto es **archivo, no hoja de ruta**. Ninguno de estos documentos describe trabajo pendiente,
aunque tres de ellos estén escritos en imperativo y se lean como órdenes de trabajo abiertas.
Se archivaron el **2026-08-30**, al cerrar la etapa en que el motor se construía como producto
general, y al reenfocar el proyecto en **Los Bodegueros** (`docs/bodegueros/README.md`).

## Por qué no se borra nada de acá

Cada archivo registra **qué evidencia obligó a un cambio**. El código dice qué hace el motor hoy;
`CHANGELOG.md` dice cuándo cambió; estos documentos dicen *por qué no podía seguir como estaba*.
Esa es la parte que no se puede reconstruir leyendo el código, y es la que se pierde primero.

## Qué hay

| archivo | qué es | estado |
|---|---|---|
| `decisiones_1.1.0.md` | inventario cerrado de lo que entregó 1.1.0: mecánica del bi-precio, las 22 columnas mayoristas, criterio de aceptación, alcance | **cerrado, pero sigue siendo citado**: `CLAUDE.md` y `docs/columnas.md` lo tratan como dueño de la mecánica del bi-precio (§1, §5, §8, §10, §11). Sus partes superadas están anotadas en el propio texto |
| `brief_correccion_mayorista.md` | la corrección de la fórmula mayorista (`price − descuento` → `list_price − descuento`) | cerrado — entregado en v18 (`a84a3e5`) |
| `brief_tareas_bloqueantes.md` | TAREA A (precio en quiebre) y TAREA B (crudo de descubrimiento) | cerrado — v19 (`10c0983`) y v20 |
| `brief_tres_correcciones.md` | truncamiento, auditoría y `surtido_makro` | cerrado — `8569056`, `03e2189`, `a2b6775` |
| `contradicciones.md` | la auditoría de documentación previa a 1.1.0, con su sección `## Resoluciones` | cerrado — leerlo antes de volver a agregar algo que `CLAUDE.md` ya sacó |

## Dos pendientes reales sobreviven a estos documentos

No están acá porque estén archivados; están acá porque nacieron acá y siguen abiertos:

1. **`surtido_makro` se midió redundante y NO se borró.** Es colineal con `availability` en las
   9.528 filas medidas. Se revisa a los 30 días, o cuando el alcance salga de abarrotes.
2. **Umbrales 6, 10, 12, 13 y 15 no tienen auditoría** bajo la fórmula mayorista posterior a v18.
   El "resuelta en todo el rango observado (2 a 24)" de §11 se midió con la fórmula vieja.

Ambos están explicados en `CLAUDE.md`, que es donde se van a resolver.
