#!/usr/bin/env python3
"""
El descuento del bi-precio se elige por RÉGIMEN, no por posición en el array.

El bug
------
`leer_descuento` recorría la respuesta entera y devolvía el PRIMER
`PromotionalPriceTableItemsDiscount` que encontrara, y `leer_regimen` tomaba
el primer teaser con descuento. Ninguna de las dos sabía a qué régimen
pertenecía el número que estaba leyendo.

Cuando un SKU trae, además del bi-precio, un teaser condicionado a tarjeta
(`Promo Oh-Pay MAKRO` / `TARJETA OH - MAKRO`), VTEX pone el de tarjeta
PRIMERO en `ratesAndBenefitsData.teaser[]`. Su descuento entraba en
`descuento_monto`, viajaba a `calcular_mayorista` y pisaba el del bi-precio,
que se perdía sin dejar rastro. Medido sobre el crudo en disco:

    SKU 11776631 · 2026-08-26 · solo bi-precio 0.90
        -> descuento_monto 0.90, precio_mayorista 29.10   correcto
    SKU 11776631 · 2026-08-27 · tarjeta 6.00 + bi-precio 0.90
        -> descuento_monto 6.00, precio_mayorista 24.00   MAL

S/ 5,10 de salto en el precio mayorista de un día para el otro sin que el
escalón cambiara: lo único que cambió fue que apareció un teaser de tarjeta
y quedó primero en una lista.

Por qué el discriminante es `PaymentMethodId`
---------------------------------------------
VTEX aplica el teaser NO condicionado a tarjeta. En
`run_20260827_021218` línea 3447 (SKU 10012708, `qty=2`, tres teasers, sin
tarjeta seleccionada) se aplicó SOLO el de `PaymentMethodId = "4"`:
`sellingPrice` 1990 con un `priceTag` de la tabla `bipreciomakro`. Los dos
teasers de tarjeta no se aplicaron nunca — 0 de 17 apariciones en las dos
corridas en disco.

La comparación es por IGUALDAD EXACTA de la cadena completa, nunca por
substring: `"208"` suelto aparece más de 13.000 veces por corrida dentro de
`paymentData.paymentSystems` (el catálogo de medios de pago del storefront,
que no tiene nada que ver con promociones). Un `in` daría miles de falsos
positivos. `test_igualdad_exacta_no_substring` es el que guarda esa regla.

Fixtures
--------
Los tres arrays de teasers son copia VERBATIM del crudo de
`run_20260827_021218` (líneas 139, 659 y 2215) y el de catálogo de la línea
22 de la misma corrida. No se generan: si VTEX cambia la forma, este archivo
tiene que cambiar a mano, que es exactamente lo que se quiere.

Nada sale a la red y nada lee un `filas.csv`. Se corre solo
(`python3 tests/makro_plazavea/test_teaser_de_tarjeta.py`) o bajo pytest.
"""

from __future__ import annotations

import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

# tests/makro_plazavea/<archivo>.py -> raíz del repo: dos niveles.
RAIZ = Path(__file__).resolve().parents[2]

if str(RAIZ / "src") not in sys.path:
    sys.path.insert(0, str(RAIZ / "src"))

from retail_engine.collectors.makro_plazavea import (  # noqa: E402
    NODOS,
    PAYMENT_METHOD_IDS_TARJETA,
    Producto,
    calcular_mayorista,
    construir_fila,
    leer_descuento,
    leer_regimen,
)

NODO = NODOS["359"]
MOMENTO = datetime(2026, 8, 28, 12, 0, 0)


# ===========================================================================
# FIXTURES — copia verbatim del crudo
# ===========================================================================


def teaser_checkout(nombre: str, pago: str, sku: str, descuento: str) -> dict:
    """
    La forma de `ratesAndBenefitsData.teaser[]` (checkout), verbatim.

    Se escribe una vez y se parametriza porque los tres SKUs de los fixtures
    la repiten campo por campo; los valores que cambian son los cuatro que
    esta función recibe. La forma COMPLETA —las siete claves, incluidas las
    que el motor no lee— está anclada en `TEASERS_10012708` abajo.
    """

    return {
        "featured": True,
        "id": IDS_REGIMEN[nombre],
        "name": nombre,
        "generalValues": {},
        "conditions": {
            "parameters": [{"name": "PaymentMethodId", "value": pago}],
            "minimumQuantity": 0,
        },
        "effects": {
            "parameters": [
                {"name": "PromotionalPriceTableItemsIds", "value": sku},
                {"name": "PromotionalPriceTableItemsDiscount", "value": descuento},
            ]
        },
        "teaserType": "Profiler",
    }


# Los ids de régimen, estables en las dos corridas en disco.
IDS_REGIMEN = {
    "Promo Oh-Pay MAKRO": "439099e5-5362-4a59-8b55-fb7c6cae8fbc",
    "TARJETA OH - MAKRO": "d596d6e5-577e-4910-aa13-3e0e47daaeb5",
    "MAKRO-Bi-Precio|Vigente Oculto": "225a92ff-a721-4f76-8856-2cba133c12d8",
    "MAKRO-Bi-Precio|Oh-Pay Oculto": "bbe4c641-182a-467b-b600-bd2db5e6fcd7",
}

PAGO_SIP_1 = "208,202,210"
PAGO_SIP_2 = "203,502,501,210"
PAGO_BIPRECIO = "4"

# run_20260827_021218 línea 139 · SKU 10012708 (Besos de Moza) · nodo 359.
# Tres teasers: los dos de tarjeta primero, el bi-precio último.
TEASERS_10012708 = [
    teaser_checkout("Promo Oh-Pay MAKRO", PAGO_SIP_1, "10012708", "1.50"),
    teaser_checkout("TARJETA OH - MAKRO", PAGO_SIP_2, "10012708", "1.50"),
    teaser_checkout("MAKRO-Bi-Precio|Vigente Oculto", PAGO_BIPRECIO, "10012708", "1.20"),
]

# run_20260827_021218 línea 2215 · SKU 11776631 · nodo 359.
# Nótese `"6.0"`, con UN decimal: el mismo SKU en el nodo 360 trae `"6.00"`.
# El formato de la cadena no es estable y por eso se compara como Decimal.
TEASERS_11776631 = [
    teaser_checkout("Promo Oh-Pay MAKRO", PAGO_SIP_1, "11776631", "6.0"),
    teaser_checkout("TARJETA OH - MAKRO", PAGO_SIP_2, "11776631", "6.0"),
    teaser_checkout("MAKRO-Bi-Precio|Vigente Oculto", PAGO_BIPRECIO, "11776631", "0.90"),
]

# run_20260827_021218 línea 659 · SKU 10705758 (Vizzio Mix) · nodo 359.
# Un solo teaser, sin tarjeta: el caso que YA estaba bien y no debe moverse.
TEASERS_10705758 = [
    teaser_checkout("MAKRO-Bi-Precio|Vigente Oculto", PAGO_BIPRECIO, "10705758", "0.10"),
]

# run_20260827_021218 línea 22 · catálogo · SKU 11776631.
# Serialización C# del MISMO contenido: `parsear_producto` lee por acá, así
# que el filtro tiene que funcionar en las dos formas o el respaldo de
# catálogo vuelve a meter el descuento de tarjeta por la puerta de atrás.
TEASERS_CATALOGO_11776631 = [
    {
        "<Name>k__BackingField": "Promo Oh-Pay MAKRO",
        "<GeneralValues>k__BackingField": {},
        "<Conditions>k__BackingField": {
            "<MinimumQuantity>k__BackingField": 0,
            "<Parameters>k__BackingField": [
                {
                    "<Name>k__BackingField": "PaymentMethodId",
                    "<Value>k__BackingField": "208,202,210",
                }
            ],
        },
        "<Effects>k__BackingField": {
            "<Parameters>k__BackingField": [
                {
                    "<Name>k__BackingField": "PromotionalPriceTableItemsIds",
                    "<Value>k__BackingField": "11776631",
                },
                {
                    "<Name>k__BackingField": "PromotionalPriceTableItemsDiscount",
                    "<Value>k__BackingField": "6.00",
                },
            ]
        },
    },
    {
        "<Name>k__BackingField": "MAKRO-Bi-Precio|Vigente Oculto",
        "<GeneralValues>k__BackingField": {},
        "<Conditions>k__BackingField": {
            "<MinimumQuantity>k__BackingField": 0,
            "<Parameters>k__BackingField": [
                {
                    "<Name>k__BackingField": "PaymentMethodId",
                    "<Value>k__BackingField": "4",
                }
            ],
        },
        "<Effects>k__BackingField": {
            "<Parameters>k__BackingField": [
                {
                    "<Name>k__BackingField": "PromotionalPriceTableItemsIds",
                    "<Value>k__BackingField": "11776631",
                },
                {
                    "<Name>k__BackingField": "PromotionalPriceTableItemsDiscount",
                    "<Value>k__BackingField": "0.90",
                },
            ]
        },
    },
]


def simulacion(sku_id: str, list_price: int, selling_price: int, teasers: list) -> dict:
    """
    Una respuesta de `simulation` con el ítem y sus teasers.

    Precios en CÉNTIMOS, como los devuelve VTEX. Sin `logisticsInfo`: acá se
    prueban precios, y meter logística haría que un cambio en la
    clasificación de nodos rompiera un test de descuentos.
    """

    return {
        "items": [
            {
                "id": sku_id,
                "quantity": 1,
                "price": list_price,
                "listPrice": list_price,
                "sellingPrice": selling_price,
                "availability": "available",
                "measurementUnit": "un",
                "unitMultiplier": 1,
            }
        ],
        "ratesAndBenefitsData": {
            "rateAndBenefitsIdentifiers": [],
            "teaser": teasers,
            "campaigns": [],
        },
    }


def producto(sku_id: str, bi_umbral: str = "2") -> Producto:
    return Producto(
        product_id="P" + sku_id,
        sku_id=sku_id,
        product_name="Producto " + sku_id,
        bi_umbral=bi_umbral,
    )


def fila_de(sku_id: str, list_price: int, selling_price: int, teasers: list):
    return construir_fila(
        producto(sku_id),
        NODO,
        200,
        simulacion(sku_id, list_price, selling_price, teasers),
        "simulation",
        MOMENTO,
    )


# ===========================================================================
# EL BUG: el teaser de tarjeta no debe pisar al bi-precio
# ===========================================================================


def test_11776631_el_teaser_de_tarjeta_no_pisa_el_biprecio() -> None:
    """
    El caso del 27 de agosto: tarjeta 6.00 + bi-precio 0.90, con promo unitaria.

    Lo que entra a la fórmula es 0.90. Y con 0.90 la regla de publicación se
    ACTIVA —30.00 − 0.90 = 29.10, por encima de los 25.00 que ya cobra la
    promoción unitaria— así que el escalón no se publica y no hay precio
    mayorista que declarar.

    El bug no solo corrompía el número: le cambiaba el ESTADO a la fila. Con
    6.00 daba 30.00 − 6.00 = 24.00, por debajo de 25.00, o sea `COMPLETO` con
    un mayorista de 24.00 — una fila afirmando un escalón publicado que no
    existe. `test_..._con_el_descuento_de_tarjeta_el_estado_se_falsea` fija
    ese contraste.
    """

    datos = simulacion("11776631", 3000, 2500, TEASERS_11776631)

    assert leer_descuento(datos) == Decimal("0.90"), (
        "leyó el descuento del teaser de tarjeta (6.00) en vez del bi-precio"
    )

    veredicto = calcular_mayorista(2500, 3000, 2, leer_descuento(datos), "MEDIDO")

    assert veredicto["estado"] == "BIPRECIO_SUPERADO_POR_PROMO"
    assert veredicto["mayorista_cents"] is None


def test_11776631_con_el_descuento_de_tarjeta_el_estado_se_falsea() -> None:
    """
    El daño exacto del bug, escrito como contraste.

    No es un test de la fórmula —`calcular_mayorista` no cambia— sino de por
    qué importa cuál descuento se le pasa: los mismos precios con el
    descuento equivocado producen un estado distinto y un precio inventado.
    Es lo que quedó escrito en `filas.csv` de run_20260827_021218.
    """

    veredicto = calcular_mayorista(2500, 3000, 2, Decimal("6.00"), "MEDIDO")

    assert veredicto["estado"] == "COMPLETO"
    assert veredicto["mayorista_cents"] == 2400


def test_11776631_del_26_reconstruye_su_mayorista() -> None:
    """
    El mismo SKU el 26: sin teaser de tarjeta y SIN promo unitaria.

    30.00 − 0.90 = 29.10, por debajo del unitario de 30.00, así que el
    escalón sí se publica. Es el valor que el motor escribió ese día, y el
    fix tiene que dejarlo intacto: la corrección se valida contra su propio
    pasado, no contra un número elegido a mano.
    """

    solo_biprecio = [
        teaser_checkout(
            "MAKRO-Bi-Precio|Vigente Oculto", PAGO_BIPRECIO, "11776631", "0.90"
        )
    ]

    datos = simulacion("11776631", 3000, 3000, solo_biprecio)

    veredicto = calcular_mayorista(3000, 3000, 2, leer_descuento(datos), "MEDIDO")

    assert veredicto["estado"] == "COMPLETO"
    assert veredicto["mayorista_cents"] == 2910


def test_10012708_besos_lee_el_biprecio_no_el_de_tarjeta() -> None:
    """
    Besos de Moza: tarjeta 1.50, bi-precio 1.20.

    El mayorista reconstruido es 20.50 − 1.20 = 19.30, que es EXACTAMENTE el
    número que el motor escribió el 26 de agosto, cuando este SKU no traía
    teaser de tarjeta. La corrección se valida contra su propio pasado.
    """

    datos = simulacion("10012708", 2050, 2000, TEASERS_10012708)

    assert leer_descuento(datos) == Decimal("1.20")

    veredicto = calcular_mayorista(2000, 2050, 2, leer_descuento(datos), "MEDIDO")

    assert veredicto["estado"] == "COMPLETO"
    assert veredicto["mayorista_cents"] == 1930


def test_el_regimen_reportado_es_el_del_biprecio() -> None:
    """
    `promo_regime_*` y `payment_method_id` describen el escalón, no la tarjeta.

    Si estas tres columnas siguieran diciendo `Promo Oh-Pay MAKRO` / `208,202,210`
    mientras `descuento_monto` ya trae el bi-precio, la fila se contradiría a
    sí misma: el régimen nombrado no sería el que produjo el número de al lado.
    """

    regimen = leer_regimen(simulacion("10012708", 2050, 2000, TEASERS_10012708))

    assert regimen["promo_regime_name"] == "MAKRO-Bi-Precio|Vigente Oculto"
    assert regimen["promo_regime_id"] == IDS_REGIMEN["MAKRO-Bi-Precio|Vigente Oculto"]
    assert regimen["payment_method_id"] == PAGO_BIPRECIO


def test_el_catalogo_tambien_filtra() -> None:
    """
    La serialización C# del catálogo pasa por el mismo filtro.

    `parsear_producto` lee de acá para `descuento_catalogo`, que es el
    respaldo cuando la medición no trae teaser. Sin el filtro en esta forma,
    el descuento de tarjeta volvería a entrar por el respaldo.
    """

    catalogo = {
        "productId": "P11776631",
        "items": [
            {
                "itemId": "11776631",
                "sellers": [
                    {
                        "sellerId": "1",
                        "commertialOffer": {"Teasers": TEASERS_CATALOGO_11776631},
                    }
                ],
            }
        ],
    }

    assert leer_descuento(catalogo) == Decimal("0.90")
    assert leer_regimen(catalogo)["payment_method_id"] == PAGO_BIPRECIO


# ===========================================================================
# NO-REGRESIÓN: lo que ya estaba bien no se mueve
# ===========================================================================


def test_10705758_sin_teaser_de_tarjeta_queda_identico() -> None:
    """
    Vizzio Mix: un solo teaser, `PaymentMethodId = "4"`.

    Es el 99,8 % del dataset. El filtro no puede tocarlo: 9.50 − 0.10 = 9.40,
    igual antes y después del fix.
    """

    datos = simulacion("10705758", 950, 950, TEASERS_10705758)

    assert leer_descuento(datos) == Decimal("0.10")

    veredicto = calcular_mayorista(950, 950, 2, leer_descuento(datos), "MEDIDO")

    assert veredicto["estado"] == "COMPLETO"
    assert veredicto["mayorista_cents"] == 940


def test_sin_teasers_sigue_sin_biprecio() -> None:
    """Sin teaser no hay descuento, y sin descuento ni umbral no hay bi-precio."""

    datos = simulacion("SIN", 1000, 1000, [])

    assert leer_descuento(datos) is None
    assert leer_regimen(datos) == {
        "promo_regime_id": "",
        "promo_regime_name": "",
        "payment_method_id": "",
    }

    veredicto = calcular_mayorista(1000, 1000, None, None, "MEDIDO")

    assert veredicto["estado"] == "SIN_BIPRECIO"
    assert veredicto["mayorista_cents"] is None


def test_dos_teasers_no_tarjeta_conserva_el_primero() -> None:
    """
    Con `Vigente Oculto` y `Oh-Pay Oculto` (los dos `PaymentMethodId = "4"`)
    se sigue tomando el primero, como antes del fix.

    Los dos son no-tarjeta, así que el filtro no elige entre ellos: el fix
    separa regímenes por medio de pago, no arbitra dentro del mismo medio.
    Cuál de los dos cobra VTEX es una pregunta abierta y distinta; dejarla
    abierta acá es deliberado, para que este test siga probando UNA regla.
    """

    teasers = [
        teaser_checkout("MAKRO-Bi-Precio|Vigente Oculto", PAGO_BIPRECIO, "X", "1.80"),
        teaser_checkout("MAKRO-Bi-Precio|Oh-Pay Oculto", PAGO_BIPRECIO, "X", "2.80"),
    ]

    datos = simulacion("X", 2980, 2700, teasers)

    assert leer_descuento(datos) == Decimal("1.80")
    assert leer_regimen(datos)["promo_regime_name"] == "MAKRO-Bi-Precio|Vigente Oculto"


# ===========================================================================
# EL FILTRO EN SÍ
# ===========================================================================


def test_solo_teasers_de_tarjeta_no_dejan_descuento() -> None:
    """
    Si TODOS los teasers son de tarjeta, el descuento de bi-precio es vacío.

    Existe en el crudo: 8 respuestas traen los dos teasers de tarjeta y
    ninguno más. Vacío, no cero — no se midió ningún escalón, y §6.2 ya dice
    que un mayorista igual al unitario es una mentira que se filtra sola.
    """

    teasers = [
        teaser_checkout("Promo Oh-Pay MAKRO", PAGO_SIP_1, "Y", "2.10"),
        teaser_checkout("TARJETA OH - MAKRO", PAGO_SIP_2, "Y", "2.10"),
    ]

    datos = simulacion("Y", 5300, 5300, teasers)

    assert leer_descuento(datos) is None
    assert leer_regimen(datos)["promo_regime_name"] == ""

    veredicto = calcular_mayorista(5300, 5300, 2, leer_descuento(datos), "MEDIDO")

    assert veredicto["estado"] == "SIN_DESCUENTO"
    assert veredicto["mayorista_cents"] is None


def test_igualdad_exacta_no_substring() -> None:
    """
    El filtro compara la CADENA COMPLETA. `"208"` suelto NO es de tarjeta.

    Es el guardarraíl del fix. `"208"` aparece más de 13.000 veces por
    corrida como `paymentSystems[].stringId` — el catálogo de medios de pago
    del storefront, no una promoción. Un filtro por substring descartaría
    teasers legítimos y devolvería el bi-precio a estar vacío, que es el
    mismo daño con otro signo.
    """

    for pago in ("208", "202", "210", "4", "203", "208,202", "210,202,208"):
        teasers = [teaser_checkout("MAKRO-Bi-Precio|Vigente Oculto", pago, "Z", "0.50")]

        assert leer_descuento(simulacion("Z", 1000, 1000, teasers)) == Decimal("0.50"), (
            f"PaymentMethodId={pago!r} se descartó como si fuera de tarjeta; "
            "el filtro tiene que ser por igualdad exacta de la cadena entera"
        )


def test_la_constante_es_el_conjunto_medido() -> None:
    """
    Las dos cadenas de tarjeta observadas, ni una más.

    Se fija acá porque agregar una cadena a esa constante EXCLUYE filas del
    bi-precio: es un cambio con consecuencias en el dataset, no una lista de
    conveniencia. Cualquier agregado tiene que llegar con su evidencia y
    romper este test primero.
    """

    assert PAYMENT_METHOD_IDS_TARJETA == {"208,202,210", "203,502,501,210"}


# ===========================================================================
# CABLEADO: la `Fila` completa, no solo las funciones puras
# ===========================================================================


def test_la_fila_de_11776631_sale_con_el_descuento_del_biprecio() -> None:
    """
    El motor entero menos el transporte: `construir_fila` sobre el payload real.

    Las funciones puras pueden estar bien y el pegamento seguir pasándole a
    la fórmula el descuento equivocado. Esto es lo que verifica que
    `descuento_monto` y `precio_mayorista` —las dos columnas que el bug
    contaminaba— salgan corregidas en la fila que se escribe al CSV.
    """

    fila = fila_de("11776631", 3000, 2500, TEASERS_11776631)

    assert fila.descuento_monto == "0.90", "el 6.00 de la tarjeta llegó al CSV"
    assert fila.descuento_monto_cents == "90"
    assert fila.promo_regime_name == "MAKRO-Bi-Precio|Vigente Oculto"
    assert fila.payment_method_id == PAGO_BIPRECIO

    # Con el descuento correcto la promo unitaria le gana al escalón, así que
    # el mayorista se suprime — §6.2, vacío y no cero. Lo que el bug escribía
    # acá era `COMPLETO` con 24.00.
    assert fila.biprecio_status == "BIPRECIO_SUPERADO_POR_PROMO"
    assert fila.precio_mayorista == ""
    assert fila.precio_mayorista_cents == ""

    # El umbral sobrevive a la supresión del precio (§6.2 reescrita en v18).
    assert fila.bi_umbral == "2"

    # El precio unitario es un hecho de VTEX y no lo toca este fix.
    assert fila.price == "25.00"
    assert fila.list_price == "30.00"


def test_la_fila_de_10705758_no_cambia() -> None:
    """No-regresión de punta a punta sobre el caso mayoritario."""

    fila = fila_de("10705758", 950, 950, TEASERS_10705758)

    assert fila.descuento_monto == "0.10"
    assert fila.precio_mayorista == "9.40"
    assert fila.biprecio_status == "COMPLETO"
    assert fila.promo_regime_name == "MAKRO-Bi-Precio|Vigente Oculto"


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
