#!/usr/bin/env python3
"""
La auditoría del mayorista: expectativa, propagación y estratos (corrección 2).

Qué prueba
----------
Tres defectos independientes de la misma fase, los tres medidos sobre
corridas reales.

**2a — esperaba el valor equivocado cuando el escalón está suprimido.**
Con la fórmula de v18 hay DOS expectativas de qué debe cobrar VTEX a
`qty = bi_umbral`, no una:

    COMPLETO                            -> list_price − descuento
    BIPRECIO_SUPERADO_POR_PROMO         -> price (el escalón no aplica)
    BIPRECIO_PUBLICACION_INDETERMINADA  -> no auditable, se excluye

La segunda fila está medida: el SKU 10012680 de `run_20260822_020027` se
remidió a qty=umbral y VTEX cobró 88.00, que es exactamente su `price`
(`list_price` 118.50). Bajo la expectativa única eso salió `DISCREPA` con
`DQ_MAYORISTA_DISCREPA`, cuando lo que pasó es que la promoción unitaria le
ganaba al escalón — justo lo que `BIPRECIO_SUPERADO_POR_PROMO` afirma.

**2b — el veredicto no se propagaba al nodo gemelo.** La auditoría corre
contra un solo nodo, y lo que mide es propiedad del SKU. Ese mismo SKU salió
de la misma corrida con 88.00 en el 359 (medido, con flag) y 87.90 en el 360
(reconstruido, sin flag), y ésa era la única diferencia de mayorista entre
nodos en los 1482 SKUs con stock en ambos.

Pero se propaga por RECLAMO COMPLETO, no por `sku_id`: una gemela en quiebre
tiene `price` de lista y estado INDETERMINADA. No hizo la misma afirmación y
la auditoría no la contesta.

**2c — la muestra no cubría el eje donde la fórmula falla.** En el nodo 359
de `run_20260824_154502` hay 35 filas COMPLETO con promoción unitaria sobre
1041 (3.4%): tres extracciones sin estratificar tienen ~90% de no tocar
ninguna, y sin promoción unitaria `price == list_price` y las dos fórmulas
dan el mismo número. Las tres auditorías del 22 y las tres del 24 cayeron,
las seis, del lado ciego.

Cómo se engancha al motor
-------------------------
Contra las funciones puras donde vive cada regla: `esperado_en_auditoria`,
`reclamo_mayorista`, `aplicar_veredicto_mayorista` y
`elegir_auditables_mayorista`. `auditar_mayorista` es la única parte que sale
a la red y es glue: pide, y le pasa el resultado a las tres de arriba.

Nada sale a la red y nada lee un `filas.csv`. Se corre solo
(`python3 tests/makro_plazavea/test_auditoria_mayorista.py`) o bajo pytest.
"""

from __future__ import annotations

import sys
from pathlib import Path

# tests/makro_plazavea/<archivo>.py -> raíz del repo: dos niveles.
RAIZ = Path(__file__).resolve().parents[2]

if str(RAIZ / "src") not in sys.path:
    sys.path.insert(0, str(RAIZ / "src"))

from retail_engine.collectors.makro_plazavea import (  # noqa: E402
    Fila,
    aplicar_veredicto_mayorista,
    elegir_auditables_mayorista,
    es_auditable_mayorista,
    esperado_en_auditoria,
    hay_promo_unitaria,
    reclamo_mayorista,
)


def fila(
    sku_id: str = "10012680",
    node_id: str = "359",
    estado: str = "COMPLETO",
    price: str = "88.00",
    list_price: str = "118.50",
    umbral: str = "4",
    mayorista_cents: str = "8790",
    descuento_cents: str = "3060",
    price_origin: str = "MEDIDO",
) -> Fila:
    """Una fila de medición armada a mano, sin pasar por la red."""

    f = Fila()
    f.sku_id = sku_id
    f.node_id = node_id
    f.product_name = f"PRODUCTO {sku_id}"
    f.price = price
    f.price_cents = str(int(round(float(price) * 100))) if price else ""
    f.list_price = list_price
    f.price_origin = price_origin
    f.bi_umbral = umbral
    f.descuento_monto_cents = descuento_cents
    f.precio_mayorista_cents = mayorista_cents
    f.precio_mayorista = (
        f"{int(mayorista_cents) / 100:.2f}" if mayorista_cents else ""
    )
    f.biprecio_status = estado
    f.precio_mayorista_verificado = "NO" if mayorista_cents else ""

    return f


# ===========================================================================
# 2a — DOS EXPECTATIVAS, NO UNA
# ===========================================================================


def test_2a_completo_espera_el_mayorista_reconstruido():
    resultado = esperado_en_auditoria(fila(estado="COMPLETO"))

    assert resultado["caso"] == "ESCALON_PUBLICADO"
    assert resultado["esperado_cents"] == 8790, (
        "un escalón publicado tiene que cobrar list_price − descuento"
    )


def test_2a_suprimido_espera_el_precio_unitario():
    """El caso de 10012680: VTEX cobró 88.00, que es su price."""

    f = fila(estado="BIPRECIO_SUPERADO_POR_PROMO", mayorista_cents="")

    resultado = esperado_en_auditoria(f)

    assert resultado["caso"] == "ESCALON_SUPRIMIDO"
    assert resultado["esperado_cents"] == 8800, (
        "si el escalón no aplica, a qty=umbral se paga el unitario — "
        "esperar el reconstruido reporta una discrepancia inexistente"
    )


def test_2a_indeterminada_no_es_auditable():
    """Sin stock VTEX no cotiza a ninguna cantidad: no hay qué preguntar."""

    f = fila(
        estado="BIPRECIO_PUBLICACION_INDETERMINADA",
        price="118.50",
        price_origin="LISTA_SIN_PROMO",
    )

    assert esperado_en_auditoria(f)["caso"] == "NO_AUDITABLE"
    assert not es_auditable_mayorista(f)


def test_2a_los_estados_sin_mayorista_tampoco_son_auditables():
    for estado in ("SIN_BIPRECIO", "SIN_DESCUENTO", "SIN_UMBRAL",
                   "INCONSISTENTE", "SIN_MEDICION", ""):
        f = fila(estado=estado, mayorista_cents="")

        assert not es_auditable_mayorista(f), estado


def test_2a_sin_umbral_util_no_es_auditable():
    """Un 'bi-precio' que arranca en 1 unidad no es un bi-precio."""

    assert not es_auditable_mayorista(fila(umbral=""))
    assert not es_auditable_mayorista(fila(umbral="1"))


def test_2a_un_suprimido_siempre_trae_promo_unitaria():
    """Por construcción: list − desc >= price con price == list pediría
    un descuento negativo. Es lo que lo vuelve el estrato escaso."""

    f = fila(estado="BIPRECIO_SUPERADO_POR_PROMO", mayorista_cents="")

    assert hay_promo_unitaria(f)
    assert not hay_promo_unitaria(fila(price="5.39", list_price="5.39"))


# ===========================================================================
# EL VEREDICTO: QUÉ QUEDA ESCRITO
# ===========================================================================


def test_veredicto_completo_que_coincide_verifica_el_precio():
    f = fila()

    veredicto = aplicar_veredicto_mayorista(f, 8790, 8790, "ESCALON_PUBLICADO")

    assert veredicto == "COINCIDE"
    assert f.precio_mayorista_verificado == "SI"
    assert f.precio_mayorista == "87.90"
    assert "DQ_MAYORISTA_DISCREPA" not in f.dq_flags


def test_veredicto_completo_que_discrepa_gana_el_medido_y_marca():
    f = fila()

    veredicto = aplicar_veredicto_mayorista(f, 8800, 8790, "ESCALON_PUBLICADO")

    assert veredicto == "DISCREPA"
    assert f.precio_mayorista == "88.00", "el medido es lo que el cliente paga"
    assert "DQ_MAYORISTA_DISCREPA" in f.dq_flags
    assert f.descuento_monto_cents == "3060", (
        "descuento_monto NO se toca: que lo declarado y lo cobrado no cierren "
        "ES el hallazgo, y borrar un lado lo borraría"
    )


def test_veredicto_suprimido_que_coincide_no_publica_un_mayorista():
    """Lo verificado es la AUSENCIA del escalón, no un precio.

    Escribir el medido (= el unitario) en precio_mayorista publicaría un
    mayorista idéntico al unitario, que es lo que §6.2 evita dejando la
    celda vacía.
    """

    f = fila(estado="BIPRECIO_SUPERADO_POR_PROMO", mayorista_cents="")

    veredicto = aplicar_veredicto_mayorista(f, 8800, 8800, "ESCALON_SUPRIMIDO")

    assert veredicto == "COINCIDE"
    assert f.precio_mayorista == ""
    assert f.precio_mayorista_cents == ""
    assert f.precio_mayorista_verificado == ""
    assert "DQ_MAYORISTA_DISCREPA" not in f.dq_flags


def test_veredicto_suprimido_que_discrepa_si_escribe():
    """Apareció un precio que la fila negaba: eso sí es un hallazgo."""

    f = fila(estado="BIPRECIO_SUPERADO_POR_PROMO", mayorista_cents="")

    veredicto = aplicar_veredicto_mayorista(f, 8500, 8800, "ESCALON_SUPRIMIDO")

    assert veredicto == "DISCREPA"
    assert f.precio_mayorista == "85.00"
    assert f.precio_mayorista_verificado == "SI"
    assert "DQ_MAYORISTA_DISCREPA" in f.dq_flags


def test_veredicto_el_porcentaje_no_contradice_al_precio():
    f = fila()

    aplicar_veredicto_mayorista(f, 8000, 8790, "ESCALON_PUBLICADO")

    assert f.precio_mayorista == "80.00"
    assert f.descuento_mayorista_pct == "9.09", (
        "el ahorro se recalcula contra el precio realmente cobrado"
    )


# ===========================================================================
# 2b — PROPAGACIÓN AL NODO GEMELO
# ===========================================================================


def test_2b_el_reclamo_ignora_el_nodo():
    """Dos nodos que llegaron al mismo número por el mismo camino."""

    assert reclamo_mayorista(fila(node_id="359")) == reclamo_mayorista(
        fila(node_id="360")
    )


def test_2b_una_gemela_en_quiebre_no_hizo_la_misma_afirmacion():
    quiebre = fila(
        node_id="360",
        estado="BIPRECIO_PUBLICACION_INDETERMINADA",
        price="118.50",
        price_origin="LISTA_SIN_PROMO",
    )

    assert reclamo_mayorista(fila()) != reclamo_mayorista(quiebre)


def test_2b_una_gemela_con_otro_precio_tampoco():
    assert reclamo_mayorista(fila()) != reclamo_mayorista(
        fila(node_id="360", price="90.00")
    )


def test_2b_el_mismo_sku_no_sale_con_dos_mayoristas_distintos():
    """El caso 10012680 tal como salió de run_20260822_020027."""

    n359 = fila(node_id="359")
    n360 = fila(node_id="360")

    reclamo = reclamo_mayorista(n359)

    aplicar_veredicto_mayorista(n359, 8800, 8790, "ESCALON_PUBLICADO")

    gemelas = [f for f in (n360,) if reclamo_mayorista(f) == reclamo]

    assert gemelas == [n360], "la gemela hizo la misma afirmación"

    for gemela in gemelas:
        aplicar_veredicto_mayorista(gemela, 8800, 8790, "ESCALON_PUBLICADO")

    assert n359.precio_mayorista == n360.precio_mayorista == "88.00"
    assert "DQ_MAYORISTA_DISCREPA" in n360.dq_flags, (
        "el flag viaja con el veredicto: una fila con el precio medido y sin "
        "la marca afirma una verificación limpia que no ocurrió"
    )
    assert n360.precio_mayorista_verificado == "SI"


# ===========================================================================
# 2c — MUESTRA ESTRATIFICADA
# ===========================================================================


def catalogo_sesgado(con_promo: int, sin_promo: int) -> list[Fila]:
    """Reproduce la proporción real: 35 con promo sobre 1041 (3.4%)."""

    filas: list[Fila] = []

    for i in range(sin_promo):
        filas.append(
            fila(
                sku_id=f"S{i:04d}",
                price="5.39",
                list_price="5.39",
                umbral=str(2 + i % 3),
                mayorista_cents="525",
            )
        )

    for i in range(con_promo):
        filas.append(
            fila(
                sku_id=f"C{i:04d}",
                price="88.00",
                list_price="118.50",
                umbral=str(12 + i % 3),
                mayorista_cents="8790",
            )
        )

    return filas


def test_2c_ambos_lados_del_eje_quedan_representados():
    elegidos = elegir_auditables_mayorista(catalogo_sesgado(35, 1041), "359", 3)

    assert len(elegidos) == 3
    assert any(hay_promo_unitaria(f) for f in elegidos), (
        "sin estratificar, tres extracciones de 3.4% no tocan ninguna el 90% "
        "de las veces — es lo que dejó pasar el bug de v18 seis semanas"
    )
    assert any(not hay_promo_unitaria(f) for f in elegidos)


def test_2c_con_una_sola_auditoria_gana_el_lado_que_puede_fallar():
    elegidos = elegir_auditables_mayorista(catalogo_sesgado(35, 1041), "359", 1)

    assert len(elegidos) == 1
    assert hay_promo_unitaria(elegidos[0])


def test_2c_si_un_estrato_esta_vacio_no_se_pierden_auditorias():
    elegidos = elegir_auditables_mayorista(catalogo_sesgado(0, 40), "359", 3)

    assert len(elegidos) == 3
    assert not any(hay_promo_unitaria(f) for f in elegidos)


def test_2c_sigue_cubriendo_el_rango_de_umbrales():
    """Dentro de cada estrato el criterio viejo sigue mandando."""

    elegidos = elegir_auditables_mayorista(catalogo_sesgado(35, 1041), "359", 3)
    umbrales = sorted(int(f.bi_umbral) for f in elegidos)

    assert len(set(umbrales)) == 3, f"tres umbrales distintos, salieron {umbrales}"
    assert min(umbrales) <= 3, "un umbral bajo, el caso ya verificado"
    assert max(umbrales) >= 12, "un umbral alto, el hueco de §11"


def catalogo_realista() -> list[Fila]:
    """
    La forma que tienen los datos de verdad, que es la que rompe.

    Sobre el nodo 359 de `run_20260822_020027`: umbrales
    {2: 707, 3: 283, 4: 128, 6: 29, 10: 11, 12: 2, 15: 1, 20: 3}, y del lado
    con promoción unitaria {2: 86, 3: 28, 4: 34, 6: 1, 10: 4, 12: 2, 20: 1}.
    Lo que importa de esa forma: los dos estratos están llenos de umbrales
    BAJOS, así que una estratificación ingenua gasta las tres auditorías en
    2 y 3 y pierde el alto.
    """

    filas: list[Fila] = []

    for umbral, cuantas in ((2, 40), (3, 20), (4, 10), (12, 1), (20, 2)):
        for i in range(cuantas):
            filas.append(
                fila(
                    sku_id=f"S{umbral:02d}{i:03d}",
                    price="5.39",
                    list_price="5.39",
                    umbral=str(umbral),
                    mayorista_cents="525",
                )
            )

    for umbral, cuantas in ((2, 8), (3, 4), (4, 3)):
        for i in range(cuantas):
            filas.append(
                fila(
                    sku_id=f"C{umbral:02d}{i:03d}",
                    price="88.00",
                    list_price="118.50",
                    umbral=str(umbral),
                    mayorista_cents="8790",
                )
            )

    return filas


def test_2c_el_umbral_alto_no_se_pierde_por_estratificar():
    """Los dos ejes a la vez: promoción unitaria Y rango de umbrales.

    Con la primera versión de la estratificación esto salía 2, 3, 3: los dos
    estratos aportaban un umbral bajo y el relleno traía otro bajo. El hueco
    de §11 —12/15/24, donde viven los descuentos grandes— se perdía, y es la
    razón por la que esta fase existe. Medido sobre las dos corridas reales,
    no sobre este fixture.
    """

    elegidos = elegir_auditables_mayorista(catalogo_realista(), "359", 3)
    umbrales = sorted(int(f.bi_umbral) for f in elegidos)

    assert any(hay_promo_unitaria(f) for f in elegidos), "el eje del bug"
    assert max(umbrales) >= 12, f"el hueco de §11 se perdió: {umbrales}"
    assert min(umbrales) <= 3, f"el caso ya verificado se perdió: {umbrales}"


def test_2c_es_determinista():
    catalogo = catalogo_sesgado(35, 1041)

    primera = elegir_auditables_mayorista(catalogo, "359", 3)
    segunda = elegir_auditables_mayorista(list(catalogo), "359", 3)

    assert [f.sku_id for f in primera] == [f.sku_id for f in segunda]


def test_2c_los_suprimidos_entran_a_la_muestra():
    """Su afirmación —que el escalón no se cobra— no la verificaba nada."""

    catalogo = catalogo_sesgado(0, 40) + [
        fila(
            sku_id="SUP1",
            estado="BIPRECIO_SUPERADO_POR_PROMO",
            mayorista_cents="",
            umbral="12",
        )
    ]

    elegidos = elegir_auditables_mayorista(catalogo, "359", 3)

    assert "SUP1" in [f.sku_id for f in elegidos]


def test_2c_los_indeterminados_nunca_entran():
    catalogo = catalogo_sesgado(0, 40) + [
        fila(
            sku_id="IND1",
            estado="BIPRECIO_PUBLICACION_INDETERMINADA",
            price="118.50",
            price_origin="LISTA_SIN_PROMO",
            umbral="12",
        )
    ]

    elegidos = elegir_auditables_mayorista(catalogo, "359", 3)

    assert "IND1" not in [f.sku_id for f in elegidos]


def test_2c_solo_audita_el_nodo_pedido():
    catalogo = catalogo_sesgado(35, 1041)

    for f in catalogo[:20]:
        f.node_id = "360"

    elegidos = elegir_auditables_mayorista(catalogo, "359", 3)

    assert all(f.node_id == "359" for f in elegidos)


def test_2c_sin_nada_auditable_devuelve_vacio():
    catalogo = [fila(estado="SIN_BIPRECIO", mayorista_cents="")]

    assert elegir_auditables_mayorista(catalogo, "359", 3) == []
    assert elegir_auditables_mayorista(catalogo_sesgado(5, 5), "359", 0) == []


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
