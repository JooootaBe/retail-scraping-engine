#!/usr/bin/env python3
"""
Invariantes sobre una corrida real, recalculada con la lógica nueva.

Qué prueba
----------
`tests/makro_plazavea/test_precio_en_quiebre.py` prueba seis ramas con filas armadas a mano.
Esto prueba lo otro: que sobre las 3174 filas de `run_20260822_020027` —una
corrida de verdad, con la mezcla de casos que trae el catálogo real— la
lógica nueva no deja pasar ninguna de las cuatro cosas que TAREA A vino a
cerrar.

    1. toda fila withoutStock tiene price == list_price
    2. ninguna fila withoutStock queda con escalón publicado
    3. ninguna fila withoutStock tiene discount_pct = 0.00
    4. toda fila con precio_mayorista no vacío tiene bi_umbral no vacío

La 2 es la que vale. Con la lógica de v18 esas filas salían publicadas por
construcción aritmética (`price == list_price` hace que `list − descuento`
quede siempre por debajo de `price`), y el test lo demuestra recalculando
las MISMAS filas con el origen ignorado: ahí salen 82 de 82 publicadas. Sin
ese control, la invariante 2 podría estar pasando porque no hay filas que
la puedan romper.

De dónde salen los insumos
--------------------------
De `raw.jsonl.gz` — precios, `availability` y el descuento del teaser se
releen con los mismos `extraer_item` y `leer_descuento` que usa el motor.
Recalcular desde el crudo en vez de desde el CSV es el punto: es lo que hace
que un bug de interpretación se repare hacia atrás.

Salvo DOS insumos, y las dos excepciones son la misma cosa: esa corrida es
anterior a TAREA B, así que su `raw.jsonl.gz` no tiene una sola respuesta de
catálogo. Los dos insumos que solo el catálogo conoce hay que sacarlos del
CSV, que es el único registro que queda.

`bi_umbral`. El CSV lo vacía en todo estado != COMPLETO por la §6.2 de
entonces, así que para las 236 filas SIN_DESCUENTO el valor ya no existe en
ninguna fuente local. Eso no invalida el recálculo, por una razón concreta:
con `descuento is None` la rama SIN_DESCUENTO de `calcular_mayorista` se
dispara ANTES de que el valor del umbral entre en cualquier cuenta. Se pasa
un centinela y `test_6` verifica que el estado salga SIN_DESCUENTO, que es lo
único que esas filas podían decir.

`descuento`. Acá el recálculo encontró algo que conviene dejar escrito,
porque es la premisa de TAREA A confirmada desde otro ángulo: en las 143
filas sin stock la respuesta de `simulation` NO TRAE TEASER. Ninguno. No es
que traiga un teaser con descuento cero — es que VTEX no evaluó promociones y
no hay nada que leer. Por eso el descuento de esas filas venía del catálogo
(`Producto.descuento_catalogo`), y son exactamente las 82 que tenían
`descuento_monto` en el CSV y ningún teaser en el crudo.

O sea que sin TAREA B esas 82 filas no se pueden recalcular desde el crudo, y
son justo las que demuestran el bug. Se toma `descuento_monto` del CSV como
respaldo, replicando el mismo orden de fuentes que usa `enriquecer_fila`
(medición primero, catálogo después) con el CSV haciendo de catálogo.

De la corrida siguiente en adelante este archivo puede leer las dos cosas del
crudo y no necesita ni centinela ni respaldo.

Se corre solo (`python3 tests/makro_plazavea/test_propiedades_corrida.py`) o bajo pytest.
La corrida vive en `data/`, que está en `.gitignore`: si no está, se saltea.
"""

from __future__ import annotations

import csv
import gzip
import json
import sys
from collections import Counter
from pathlib import Path

# tests/makro_plazavea/<archivo>.py -> raíz del repo: dos niveles.
RAIZ = Path(__file__).resolve().parents[2]

if str(RAIZ / "src") not in sys.path:
    sys.path.insert(0, str(RAIZ / "src"))

from retail_engine.collectors.makro_plazavea import (  # noqa: E402
    ESTADOS_CON_MAYORISTA,
    Producto,
    calcular_descuento_pct,
    calcular_mayorista,
    clasificar_origen_precio,
    dec,
    entero,
    extraer_item,
    leer_descuento,
    soles,
)

CORRIDA = RAIZ / "data" / "makro_plazavea" / "run_20260822_020027"

# Centinela para las filas cuyo umbral el CSV destruyó. Ver el docstring:
# solo aparece en filas con `descuento is None`, donde la rama SIN_DESCUENTO
# se dispara antes de que el valor se use para nada.
UMBRAL_PERDIDO = -1


class SinCorrida(Exception):
    """La corrida no está en disco (`data/` está en .gitignore)."""


def cargar() -> list[dict]:
    """
    Relee la corrida y recalcula cada fila con la lógica actual del motor.

    Devuelve, por fila, lo viejo (lo que el CSV dice) y lo nuevo (lo que el
    motor diría hoy), para poder contar los cambios además de afirmar las
    invariantes.
    """

    if not (CORRIDA / "filas.csv").exists():
        raise SinCorrida(f"no está {CORRIDA.relative_to(RAIZ)}")

    csv.field_size_limit(10_000_000)

    with (CORRIDA / "filas.csv").open(encoding="utf-8") as fuente:
        viejas = {
            (f["sku_id"], f["node_id"]): f for f in csv.DictReader(fuente)
        }

    filas: list[dict] = []

    with gzip.open(CORRIDA / "raw.jsonl.gz", "rt", encoding="utf-8") as crudo:
        for linea in crudo:
            registro = json.loads(linea)

            # Solo la medición por defecto: `orderform_auditoria` y
            # `simulation_qtyN` son segundas lecturas del mismo par
            # (sku, nodo) y duplicarían las filas.
            if registro.get("method") != "simulation":
                continue

            clave = (registro["sku_id"], registro["node_id"])
            vieja = viejas.get(clave)

            if vieja is None:
                continue

            producto = Producto(
                product_id=registro["product_id"], sku_id=registro["sku_id"]
            )
            item = extraer_item(registro["response"], producto)

            price = soles(item.get("sellingPrice"))
            list_price = soles(item.get("listPrice"))
            availability = item.get("availability", "")

            origen = clasificar_origen_precio(availability, price)

            # Mismo orden de fuentes que `enriquecer_fila`: la medición
            # primero, el catálogo después. Acá el catálogo no está en el
            # crudo (corrida anterior a TAREA B), así que su lugar lo ocupa
            # `descuento_monto` del CSV, que es lo que el motor escribió.
            descuento = leer_descuento(registro["response"])

            if descuento is None:
                descuento = dec(vieja["descuento_monto"])

            # El único insumo que no se puede releer del crudo de esta
            # corrida. Ver el docstring del módulo.
            umbral = entero(vieja["bi_umbral"])

            if umbral is None and vieja["biprecio_status"] == "SIN_DESCUENTO":
                umbral = UMBRAL_PERDIDO

            nuevo = calcular_mayorista(
                entero(item.get("sellingPrice")),
                entero(item.get("listPrice")),
                umbral,
                descuento,
                origen,
            )

            # Control: el MISMO recálculo con el origen ignorado, que es
            # exactamente lo que hacía v18.
            v18 = calcular_mayorista(
                entero(item.get("sellingPrice")),
                entero(item.get("listPrice")),
                umbral,
                descuento,
                "MEDIDO",
            )

            filas.append(
                {
                    "sku_id": registro["sku_id"],
                    "node_id": registro["node_id"],
                    "availability": availability,
                    "price": price,
                    "list_price": list_price,
                    "price_origin": origen,
                    "discount_pct": calcular_descuento_pct(
                        item.get("listPrice"),
                        item.get("sellingPrice"),
                        item.get("unitMultiplier"),
                        origen,
                    ),
                    "bi_umbral": (
                        "" if umbral in (None, UMBRAL_PERDIDO) else str(umbral)
                    ),
                    "biprecio_status": nuevo["estado"],
                    "precio_mayorista_cents": nuevo["mayorista_cents"],
                    "biprecio_status_v18": v18["estado"],
                    "viejo_discount_pct": vieja["discount_pct"],
                    "viejo_biprecio_status": vieja["biprecio_status"],
                    "viejo_stock_signal": vieja["stock_signal"],
                }
            )

    return filas


def sin_stock(filas: list[dict]) -> list[dict]:
    return [f for f in filas if f["availability"] == "withoutStock"]


# ===========================================================================
# INVARIANTES
# ===========================================================================


def test_0_la_corrida_se_releyo_entera() -> None:
    """3174 filas. Si el recálculo pierde filas, todo lo de abajo miente."""

    filas = cargar()

    assert len(filas) == 3174, f"se recalcularon {len(filas)} filas, no 3174"
    assert len(sin_stock(filas)) == 143


def test_1_sin_stock_el_precio_es_el_de_lista() -> None:
    """
    La premisa de la que cuelga todo lo demás: 143 de 143.

    Es el hecho medido, no una regla del motor. Si un día deja de valer, lo
    que hay que revisar es `price_origin`, no este test.
    """

    filas = sin_stock(cargar())
    rotas = [f for f in filas if f["price"] != f["list_price"]]

    assert not rotas, (
        f"{len(rotas)} de {len(filas)} filas withoutStock con "
        f"price != list_price: {[f['sku_id'] for f in rotas[:10]]}"
    )


def test_2_ninguna_fila_sin_stock_queda_como_escalon_publicado() -> None:
    """
    EL PUNTO DE TAREA A.

    `COMPLETO` es la única condición bajo la cual el motor afirma que el
    escalón se publica. Ninguna fila sin stock puede llegar ahí: la
    comparación que lo decide no es evaluable cuando `price` es el precio de
    lista.
    """

    filas = sin_stock(cargar())
    publicadas = [f for f in filas if f["biprecio_status"] == "COMPLETO"]

    assert not publicadas, (
        f"{len(publicadas)} filas sin stock afirman escalón publicado: "
        f"{[f['sku_id'] for f in publicadas[:10]]}"
    )


def test_2b_control_con_la_logica_de_v18_salian_82_de_82() -> None:
    """
    Que la invariante 2 no pase por falta de casos.

    Las mismas filas, el mismo recálculo, el origen ignorado: 82 de 82
    publicadas. Ese es el falso positivo, y es sistemático — no una fila
    desafortunada.
    """

    filas = sin_stock(cargar())
    v18 = [f for f in filas if f["biprecio_status_v18"] == "COMPLETO"]
    indeterminadas = [
        f
        for f in filas
        if f["biprecio_status"] == "BIPRECIO_PUBLICACION_INDETERMINADA"
    ]

    assert len(v18) == 82, f"el control esperaba 82 y dio {len(v18)}"
    assert len(indeterminadas) == 82, (
        "las 82 que v18 publicaba tienen que salir INDETERMINADA, no "
        f"desaparecer: salieron {len(indeterminadas)}"
    )

    # Y son exactamente las mismas: el mayorista no se pierde, se deja de
    # afirmar su vigencia.
    assert {(f["sku_id"], f["node_id"]) for f in v18} == {
        (f["sku_id"], f["node_id"]) for f in indeterminadas
    }

    for fila in indeterminadas:
        assert fila["precio_mayorista_cents"] is not None, (
            "INDETERMINADA entrega el precio mayorista; lo que no afirma es "
            "que el escalón esté vigente"
        )


def test_3_ninguna_fila_sin_stock_dice_descuento_cero() -> None:
    """
    `0.00` sin stock afirma que no hay promoción, y nadie la evaluó.

    Vacío != cero (§6.2).
    """

    filas = sin_stock(cargar())
    ceros = [f for f in filas if f["discount_pct"] == "0.00"]

    assert not ceros, (
        f"{len(ceros)} filas sin stock con discount_pct = 0.00: "
        f"{[f['sku_id'] for f in ceros[:10]]}"
    )
    assert all(f["discount_pct"] == "" for f in filas)


def test_3b_con_stock_el_cero_sigue_existiendo() -> None:
    """
    La otra mitad de la regla, sin la cual el test 3 se pasa vaciando todo.

    Con stock, `0.00` es un hecho medido y tiene que sobrevivir.
    """

    filas = [f for f in cargar() if f["availability"] == "available"]
    ceros = [f for f in filas if f["discount_pct"] == "0.00"]

    assert ceros, "con stock, 0.00 es una medición y no puede desaparecer"
    assert all(f["discount_pct"] != "" for f in filas), (
        "ninguna fila con stock puede quedar sin descuento calculado"
    )


def test_4_todo_precio_mayorista_tiene_umbral() -> None:
    """
    Un precio mayorista sin umbral no se puede usar: no se sabe desde cuántas
    unidades rige.
    """

    filas = cargar()
    huerfanas = [
        f
        for f in filas
        if f["precio_mayorista_cents"] is not None and not f["bi_umbral"]
    ]

    assert not huerfanas, (
        f"{len(huerfanas)} filas con precio mayorista y sin bi_umbral: "
        f"{[f['sku_id'] for f in huerfanas[:10]]}"
    )


def test_5_los_estados_con_mayorista_son_los_unicos_con_numero() -> None:
    """Coherencia interna: el estado explica la celda de al lado (§6.2)."""

    for fila in cargar():
        tiene = fila["precio_mayorista_cents"] is not None
        deberia = fila["biprecio_status"] in ESTADOS_CON_MAYORISTA

        assert tiene == deberia, (
            f"{fila['sku_id']}/{fila['node_id']}: estado "
            f"{fila['biprecio_status']} con mayorista={tiene}"
        )


def test_6_las_filas_perdidas_solo_pudieron_salir_sin_descuento() -> None:
    """
    Cierra el centinela del docstring.

    Las 236 filas cuyo umbral el CSV destruyó tienen `descuento is None`, así
    que la rama SIN_DESCUENTO se dispara antes de que el valor del umbral
    entre en cualquier cuenta. Si alguna saliera con otro estado, el
    centinela estaría contaminando el recálculo.
    """

    perdidas = [
        f for f in cargar() if f["viejo_biprecio_status"] == "SIN_DESCUENTO"
    ]

    assert len(perdidas) == 236
    assert all(f["biprecio_status"] == "SIN_DESCUENTO" for f in perdidas)
    assert all(f["precio_mayorista_cents"] is None for f in perdidas)


# ===========================================================================
# INFORME
# ===========================================================================


def informe(filas: list[dict]) -> str:
    lineas = [
        f"corrida: {CORRIDA.name}  ({len(filas)} filas recalculadas)",
        "",
        "price_origin (columna NUEVA)",
    ]

    for valor, cuantas in Counter(f["price_origin"] for f in filas).most_common():
        lineas.append(f"  {valor or '(vacío)':<34} {cuantas:>6}")

    lineas += ["", "discount_pct: viejo -> nuevo (solo lo que cambia)"]

    cambios = Counter(
        (f["viejo_discount_pct"], f["discount_pct"])
        for f in filas
        if f["viejo_discount_pct"] != f["discount_pct"]
    )

    if not cambios:
        lineas.append("  (sin cambios)")

    for (viejo, nuevo), cuantas in cambios.most_common():
        lineas.append(
            f"  {viejo or '(vacío)':<14} -> {nuevo or '(vacío)':<15} {cuantas:>6}"
        )

    lineas += ["", "biprecio_status: viejo (v17, tal como quedó en el CSV) -> nuevo"]

    for (viejo, nuevo), cuantas in Counter(
        (f["viejo_biprecio_status"], f["biprecio_status"]) for f in filas
    ).most_common():
        marca = "   <-- cambia" if viejo != nuevo else ""
        lineas.append(f"  {viejo:<16} -> {nuevo:<36} {cuantas:>6}{marca}")

    lineas += [
        "",
        "biprecio_status: v18 -> nuevo  (aísla lo que cambia TAREA A sola)",
    ]

    for (v18, ahora), cuantas in Counter(
        (f["biprecio_status_v18"], f["biprecio_status"]) for f in filas
    ).most_common():
        marca = "   <-- lo cambia TAREA A" if v18 != ahora else ""
        lineas.append(f"  {v18:<28} -> {ahora:<36} {cuantas:>6}{marca}")

    lineas += ["", "stock_signal: viejo -> nuevo"]

    renombre = {
        "DISPONIBLE": "DISPONIBLE",
        "QUIEBRE_LOCAL": "SIN_STOCK_LOCAL_CADENA_CON_STOCK",
        "QUIEBRE_CADENA": "SIN_STOCK_CADENA",
        "QUIEBRE_LOCAL_CADENA_DESCONOCIDA": "SIN_STOCK_LOCAL_CADENA_DESCONOCIDA",
    }

    for viejo, cuantas in Counter(
        f["viejo_stock_signal"] for f in filas
    ).most_common():
        nuevo = renombre.get(viejo, viejo)
        marca = "   <-- cambia" if viejo != nuevo else ""
        lineas.append(f"  {viejo:<34} -> {nuevo:<36} {cuantas:>6}{marca}")

    return "\n".join(lineas)


def main() -> int:
    try:
        filas = cargar()
    except SinCorrida as exc:
        print(f"SALTEADO: {exc}")
        return 0

    pruebas = [
        (nombre, funcion)
        for nombre, funcion in sorted(globals().items())
        if nombre.startswith("test_") and callable(funcion)
    ]

    fallan: list[tuple[str, AssertionError]] = []

    for nombre, funcion in pruebas:
        try:
            funcion()
            print(f"  ok    {nombre}")
        except AssertionError as exc:
            fallan.append((nombre, exc))
            print(f"  FALLA {nombre}")

    print()
    print(f"pasan: {len(pruebas) - len(fallan)}/{len(pruebas)}")

    for nombre, exc in fallan:
        print()
        print(f"{nombre}: {exc}")

    print()
    print(informe(filas))

    return 1 if fallan else 0


if __name__ == "__main__":
    raise SystemExit(main())
