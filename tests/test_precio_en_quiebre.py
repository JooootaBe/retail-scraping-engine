#!/usr/bin/env python3
"""
El precio en quiebre no es una oferta (TAREA A).

Qué prueba
----------
VTEX no evalúa promociones cuando no hay stock en el nodo: devuelve el precio
de LISTA. Hasta v19 el motor escribía ese número en `price` sin distinguirlo
de un precio cotizado, `discount_pct` salía `0.00` —afirmando "no tiene
descuento" sobre una promoción que nadie evaluó— y la regla de publicación
del bi-precio (`list_price − descuento >= price`) se evaluaba contra un
`price` que no era el precio de oferta.

Ese último es el que importa: en una fila en quiebre `price == list_price`,
así que `list − descuento` queda SIEMPRE por debajo de `price` mientras el
descuento sea positivo. La comparación se cumple por construcción
aritmética. Medido sobre `run_20260822_020027`: de las 82 filas en quiebre
con `biprecio_status = COMPLETO`, 82 se marcarían como escalón publicado y 0
como suprimido, sin haber medido nada.

Seis ramas, una por caso, construidas A MANO
--------------------------------------------
    1. con stock + promo             -> MEDIDO, discount_pct con valor
    2. con stock + sin promo         -> MEDIDO, discount_pct = 0.00
    3. sin stock + sin bi-precio     -> LISTA_SIN_PROMO, discount_pct vacío
    4. sin stock + con bi-precio     -> mayorista calculado, publicación
                                        NO afirmada
    5. sin stock + price < list      -> no rompe; INDETERMINADA igual, y
                                        DQ_SIN_STOCK_CON_DESCUENTO
    6. con stock + list−desc >= price -> BIPRECIO_SUPERADO_POR_PROMO

El caso 2 es el que hace la regla no trivial. `discount_pct = 0.00` es
LEGÍTIMO con stock (se evaluó la promoción y no había: eso es un hecho
medido) y es MENTIRA sin stock (no se evaluó nada). La misma celda quiere
decir dos cosas según `price_origin`, y un test que no distinga eso estaría
probando que la columna cambió de nombre, no que la regla existe.

Cómo se engancha al motor
-------------------------
Dos niveles, a propósito:

  * las FUNCIONES PURAS (`clasificar_origen_precio`, `calcular_descuento_pct`,
    `calcular_mayorista`, `calcular_stock_signal`) — la regla misma;
  * `construir_fila`, con una respuesta de `simulation` armada a mano, que es
    lo que verifica el CABLEADO: que el origen se calcule antes que el
    descuento, y que `enriquecer_fila` no vuelva a confundir "hay precio
    mayorista" con "el escalón se publica".

Nada sale a la red y nada lee un `filas.csv`. Se corre solo
(`python3 tests/test_precio_en_quiebre.py`) o bajo pytest.
"""

from __future__ import annotations

import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

if str(RAIZ / "src") not in sys.path:
    sys.path.insert(0, str(RAIZ / "src"))

from retail_engine.collectors.makro_plazavea import (  # noqa: E402
    NODOS,
    Fila,
    Producto,
    a_centavos,
    calcular_descuento_pct,
    calcular_mayorista,
    calcular_stock_signal,
    clasificar_origen_precio,
    construir_fila,
)

NODO = NODOS["359"]
MOMENTO = datetime(2026, 8, 24, 12, 0, 0)


# ===========================================================================
# ARMADO A MANO
# ===========================================================================


def simulacion(
    sku_id: str,
    selling_price: int,
    list_price: int,
    availability: str,
) -> dict:
    """
    Una respuesta de `simulation` con lo mínimo que el motor lee del ítem.

    Precios en CÉNTIMOS, que es como los devuelve VTEX. Sin `logisticsInfo`:
    la logística no entra en ninguna de estas seis ramas, y agregarla haría
    que un cambio en la clasificación de nodos rompiera un test de precios.
    """

    return {
        "items": [
            {
                "id": sku_id,
                "price": list_price,
                "sellingPrice": selling_price,
                "listPrice": list_price,
                "availability": availability,
                "measurementUnit": "un",
                "unitMultiplier": 1,
                "sellerChain": [NODO.seller_chain],
            }
        ]
    }


def producto(sku_id: str, bi_umbral: str = "", descuento: str = "") -> Producto:
    """
    El producto tal como sale del descubrimiento.

    `bi_umbral` y `descuento_catalogo` son los dos insumos del bi-precio que
    SOLO existen en el catálogo — por eso `Producto` los arrastra hasta la
    medición (§1).
    """

    return Producto(
        product_id=f"P{sku_id}",
        sku_id=sku_id,
        product_name=f"Producto de prueba {sku_id}",
        bi_umbral=bi_umbral,
        descuento_catalogo=descuento,
    )


def fila_de(
    sku_id: str,
    selling_price: int,
    list_price: int,
    availability: str,
    bi_umbral: str = "",
    descuento: str = "",
) -> Fila:
    """Una `Fila` completa, sin red: el motor entero menos el transporte."""

    return construir_fila(
        producto(sku_id, bi_umbral, descuento),
        NODO,
        200,
        simulacion(sku_id, selling_price, list_price, availability),
        "simulation",
        MOMENTO,
    )


# ===========================================================================
# LAS SEIS RAMAS
# ===========================================================================


def test_1_con_stock_con_promo() -> None:
    """El caso normal: se cotizó, había promoción, el descuento es un hecho."""

    fila = fila_de("A1", selling_price=8000, list_price=10000, availability="available")

    assert fila.price == "80.00"
    assert fila.list_price == "100.00"
    assert fila.price_origin == "MEDIDO"
    assert fila.discount_pct == "20.00"


def test_2_con_stock_sin_promo() -> None:
    """
    `0.00` LEGÍTIMO: se evaluó la promoción y no había ninguna.

    Es el caso que hace la regla no trivial. Un cero acá es un hecho medido,
    y vaciarlo destruiría información tan real como la del caso 1.
    """

    fila = fila_de("A2", selling_price=10000, list_price=10000, availability="available")

    assert fila.price_origin == "MEDIDO"
    assert fila.discount_pct == "0.00", "con stock, 0.00 es una medición"


def test_2b_el_mismo_cero_significa_cosas_distintas() -> None:
    """
    La simetría, sobre la función pura y con los MISMOS números.

    Idénticos insumos de precio; lo único que cambia es el origen. Si esto
    diera lo mismo en los dos lados, la columna `price_origin` sería
    decorativa.
    """

    con_stock = calcular_descuento_pct("100.00", "100.00", "1", "MEDIDO")
    sin_stock = calcular_descuento_pct("100.00", "100.00", "1", "LISTA_SIN_PROMO")

    assert con_stock == "0.00"
    assert sin_stock == ""
    assert con_stock != sin_stock


def test_3_sin_stock_sin_biprecio() -> None:
    """
    Sin stock: `price` es el precio de lista y el descuento va VACÍO.

    El precio SE CONSERVA — los precios nunca se descartan. Lo que se agrega
    es la procedencia.
    """

    fila = fila_de("A3", selling_price=10000, list_price=10000, availability="withoutStock")

    assert fila.price == "100.00", "el precio no se descarta"
    assert fila.price_origin == "LISTA_SIN_PROMO"
    assert fila.discount_pct == "", "vacío != cero: no se evaluó ninguna promoción"
    assert fila.biprecio_status == "SIN_BIPRECIO"


def test_4_sin_stock_con_biprecio() -> None:
    """
    El punto delicado: mayorista CALCULADO, publicación NO afirmada.

    `precio_mayorista = list_price − descuento` no depende del stock —
    `list_price` es dato de catálogo y el descuento viene del teaser. Lo que
    no se puede evaluar es la regla de publicación, porque `price` no es el
    precio real de oferta.
    """

    fila = fila_de(
        "A4",
        selling_price=10000,
        list_price=10000,
        availability="withoutStock",
        bi_umbral="3",
        descuento="4.00",
    )

    assert fila.price_origin == "LISTA_SIN_PROMO"
    assert fila.biprecio_status == "BIPRECIO_PUBLICACION_INDETERMINADA"

    # El mayorista SÍ se entrega: se conoce.
    assert fila.precio_mayorista == "96.00"
    assert fila.precio_mayorista_cents == "9600"
    assert fila.bi_umbral == "3"
    assert fila.descuento_monto == "4.00"

    # Lo que no se sabe, no se dice.
    assert fila.descuento_mayorista_pct == "", (
        "el ahorro se mide contra `price`, y acá `price` es el precio de lista"
    )
    assert fila.discount_pct == ""


def test_4b_sin_stock_nunca_queda_como_escalon_publicado() -> None:
    """
    El falso positivo que TAREA A cierra, sobre la función pura.

    Con `price == list_price` y descuento positivo, `list − descuento` SIEMPRE
    queda por debajo de `price`: con la lógica de v18 estas filas salían
    COMPLETO por construcción aritmética. Se barren descuentos y umbrales
    para que el test no dependa de un número elegido con suerte.
    """

    for descuento in ("0.10", "1.00", "4.00", "25.00", "99.99"):
        for umbral in (2, 3, 12, 24):
            veredicto = calcular_mayorista(
                10000,
                10000,
                umbral,
                Decimal(descuento),
                "LISTA_SIN_PROMO",
            )

            assert veredicto["estado"] != "COMPLETO", (
                f"descuento={descuento} umbral={umbral}: una fila sin stock "
                "no puede afirmar que el escalón se publica"
            )
            assert veredicto["estado"] == "BIPRECIO_PUBLICACION_INDETERMINADA"
            assert veredicto["mayorista_cents"] == 10000 - a_centavos(Decimal(descuento))


def test_5_sin_stock_con_precio_menor_a_lista() -> None:
    """
    No ocurre hoy: 0 de 143 filas en quiebre de `run_20260822_020027`.

    QUÉ DECIDE Y POR QUÉ. El origen sigue siendo `LISTA_SIN_PROMO`, porque la
    regla se apoya en `availability` y no en la relación entre los precios: un
    `price` más bajo no se vuelve el precio de oferta por el solo hecho de
    haber bajado, y la comparación de publicación sigue sin ser evaluable.

    Pero el caso CONTRADICE la premisa sobre la que se apoya LISTA_SIN_PROMO
    —que sin stock VTEX devuelve el precio de lista— así que no se resuelve
    en silencio: se marca `DQ_SIN_STOCK_CON_DESCUENTO` y se deja que el dato
    diga que algo no cierra. No sabemos cuál de los dos lados miente, y el
    motor no puede saberlo solo.
    """

    fila = fila_de(
        "A5",
        selling_price=8000,
        list_price=10000,
        availability="withoutStock",
        bi_umbral="3",
        descuento="4.00",
    )

    assert fila.price == "80.00", "el precio se conserva igual"
    assert fila.price_origin == "LISTA_SIN_PROMO"
    assert fila.discount_pct == ""
    assert fila.biprecio_status == "BIPRECIO_PUBLICACION_INDETERMINADA", (
        "aunque 96.00 < 80.00 sea falso, la comparación no es evaluable"
    )
    assert "DQ_SIN_STOCK_CON_DESCUENTO" in fila.dq_flags


def test_6_con_stock_escalon_superado_por_la_promo() -> None:
    """
    La regla de v18, intacta: con stock la publicación SÍ se evalúa.

    list_price − descuento = 100 − 4 = 96 >= price = 90, así que el escalón
    no le gana a la promoción unitaria y no se publica. El umbral sobrevive
    (§6.2): se conoce y es real; lo que no existe es el precio.
    """

    fila = fila_de(
        "A6",
        selling_price=9000,
        list_price=10000,
        availability="available",
        bi_umbral="3",
        descuento="4.00",
    )

    assert fila.price_origin == "MEDIDO"
    assert fila.biprecio_status == "BIPRECIO_SUPERADO_POR_PROMO"
    assert fila.precio_mayorista == ""
    assert fila.precio_mayorista_cents == ""
    assert fila.bi_umbral == "3", "el umbral se conserva: es un hecho del catálogo"
    assert fila.descuento_monto == "4.00"


# ===========================================================================
# BORDES DEL ORIGEN Y stock_signal
# ===========================================================================


def test_origen_vacio_cuando_no_se_midio_nada() -> None:
    """Sin precio o sin availability no hay procedencia que declarar."""

    assert clasificar_origen_precio("", "100.00") == ""
    assert clasificar_origen_precio("available", "") == ""
    assert clasificar_origen_precio("", "") == ""


def test_origen_de_estados_no_disponibles() -> None:
    """
    Cualquier estado declarado que no sea `available` es LISTA_SIN_PROMO.

    Incluye los que VTEX invente mañana: si el ítem no se puede comprar en
    ese nodo, tampoco se le evaluaron promociones.
    """

    assert clasificar_origen_precio("available", "10.00") == "MEDIDO"
    assert clasificar_origen_precio("withoutStock", "10.00") == "LISTA_SIN_PROMO"
    assert clasificar_origen_precio("cannotBeDelivered", "10.00") == "LISTA_SIN_PROMO"
    assert clasificar_origen_precio("unaCosaNueva", "10.00") == "LISTA_SIN_PROMO"


def test_stock_signal_no_afirma_quiebre() -> None:
    """
    Punto 4: el nombre no puede afirmar más de lo medido.

    Sin stock local se sabe que no hay stock; NO se sabe si es un
    desabastecimiento temporal o ausencia de surtido. El nombre dice lo
    observado y deja la causa al analista, que tiene el eje del tiempo.
    """

    assert calcular_stock_signal("available", "12") == "DISPONIBLE"
    assert calcular_stock_signal("withoutStock", "12") == "SIN_STOCK_LOCAL_CADENA_CON_STOCK"
    assert calcular_stock_signal("withoutStock", "0") == "SIN_STOCK_CADENA"
    assert calcular_stock_signal("withoutStock", "") == "SIN_STOCK_LOCAL_CADENA_DESCONOCIDA"
    assert calcular_stock_signal("", "12") == ""

    for señal in (
        calcular_stock_signal("withoutStock", "12"),
        calcular_stock_signal("withoutStock", "0"),
        calcular_stock_signal("withoutStock", ""),
    ):
        assert "QUIEBRE" not in señal


# ===========================================================================
# CORREDOR
# ===========================================================================


def main() -> int:
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

    return 1 if fallan else 0


if __name__ == "__main__":
    raise SystemExit(main())
