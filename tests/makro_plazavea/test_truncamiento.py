#!/usr/bin/env python3
"""
Alcanzar el total declarado no es truncamiento (corrección 1).

Qué prueba
----------
`descubrir_catalogo` marca una categoría como truncada cuando el paginado
llega al techo sin que una página corta confirme el final. El techo efectivo
es `min(total, TOPE_VTEX)` cuando la cabecera `resources` se pudo leer, y
`TOPE_VTEX` cuando no — pero llegar a uno u otro NO significa lo mismo, y
hasta v20 se trataban igual.

EL HECHO: `run_20260822_020027` y `run_20260824_154502` quedaron las dos
clasificadas `INCOMPLETO_NO_PLANEADO` por `TRUNCAMIENTO_VTEX` con un único
fallo, la categoría "Fideos Largos", y el mensaje se contradecía solo:

    quedó truncada en 50 productos: alcanzó el techo de paginado (~2450)
    sin una página corta que confirmara el final

50 no es 2450. El total declarado era 50 —múltiplo exacto de `VENTANA`— así
que la última página volvió llena por aritmética, no por corte: la página
corta que confirmaría el final NO PUEDE existir cuando el total cabe justo.
`categorias_resources_desconocido = 0` confirma que el total sí se conocía.

Los cuatro casos de la tabla del brief
--------------------------------------
    total conocido = 50, página llena de 50    -> False  (era el bug)
    total conocido = 2500 > TOPE, página llena -> True
    total desconocido, página llena            -> True
    página corta en cualquier caso             -> False

Los tres últimos son el control: el arreglo tiene que apagar el falso
positivo SIN apagar los truncamientos reales, incluido el de v11 (cabecera
`resources` ausente leída como total=0, categoría cortada a la primera
página en silencio).

Cómo se engancha al motor
-------------------------
Contra `evaluar_fin_de_paginado`, la función pura donde vive la decisión.
Se extrajo del bucle de `descubrir_catalogo` precisamente para esto: la
regla no se podía probar sin salir a la red. Nada sale a la red y nada lee
un `filas.csv`. Se corre solo (`python3 tests/makro_plazavea/test_truncamiento.py`) o bajo
pytest.
"""

from __future__ import annotations

import sys
from pathlib import Path

# tests/makro_plazavea/<archivo>.py -> raíz del repo: dos niveles.
RAIZ = Path(__file__).resolve().parents[2]

if str(RAIZ / "src") not in sys.path:
    sys.path.insert(0, str(RAIZ / "src"))

from retail_engine.collectors.makro_plazavea import (  # noqa: E402
    evaluar_fin_de_paginado,
)

# Los mismos valores que usa `descubrir_catalogo`. Se repiten acá porque son
# locales de esa función: si alguna vez se mueven a constantes de módulo,
# este test debería importarlas en vez de copiarlas.
TOPE_VTEX = 2450
VENTANA = 50


def evaluar(
    desde: int,
    total: int,
    total_conocido: bool,
    pagina_corta: bool,
) -> tuple[bool, bool]:
    return evaluar_fin_de_paginado(
        desde=desde,
        total=total,
        total_conocido=total_conocido,
        pagina_corta=pagina_corta,
        tope_vtex=TOPE_VTEX,
    )


# ===========================================================================
# LOS CUATRO CASOS DE LA TABLA
# ===========================================================================


def test_1_total_declarado_multiplo_de_ventana_no_es_truncamiento():
    """Fideos Largos: total 50, una página llena de 50, se leyó entera."""

    terminar, truncada = evaluar(
        desde=VENTANA, total=50, total_conocido=True, pagina_corta=False
    )

    assert terminar, "al alcanzar el total declarado el paginado termina"
    assert not truncada, (
        "leer los 50 productos que la categoría declara tener es el final "
        "legítimo, no un truncamiento: no falta ninguno"
    )


def test_2_total_mayor_al_tope_de_vtex_si_es_truncamiento():
    """2500 declarados, VTEX no deja pasar de ~2450: 50 quedan afuera."""

    terminar, truncada = evaluar(
        desde=TOPE_VTEX, total=2500, total_conocido=True, pagina_corta=False
    )

    assert terminar
    assert truncada, (
        "hay productos que existen y no se pudieron pedir — el dataset "
        "miente por omisión si esto no se dice"
    )


def test_3_total_desconocido_con_pagina_llena_si_es_truncamiento():
    """Sin cabecera `resources` legible no hay con qué afirmar el final."""

    terminar, truncada = evaluar(
        desde=TOPE_VTEX, total=0, total_conocido=False, pagina_corta=False
    )

    assert terminar
    assert truncada, (
        "el bug de v11: sin total legible el único techo es el de VTEX, y "
        "llegar ahí con la página llena no distingue final real de corte"
    )


def test_4_una_pagina_corta_nunca_es_truncamiento():
    """La página corta ES la confirmación del final, venga como venga."""

    casos = [
        # (desde, total, total_conocido) — al techo y antes del techo
        (VENTANA, 50, True),
        (TOPE_VTEX, 2500, True),
        (TOPE_VTEX, 0, False),
        (VENTANA, 1832, True),
        (VENTANA, 0, False),
    ]

    for desde, total, total_conocido in casos:
        terminar, truncada = evaluar(
            desde=desde,
            total=total,
            total_conocido=total_conocido,
            pagina_corta=True,
        )

        assert terminar, f"una página corta siempre termina ({desde=} {total=})"
        assert not truncada, f"una página corta nunca trunca ({desde=} {total=})"


# ===========================================================================
# LO QUE NO ESTÁ EN LA TABLA PERO EL BUCLE NECESITA
# ===========================================================================


def test_5_lejos_del_techo_el_paginado_sigue():
    """Sin esto el arreglo podría 'no truncar' cortando el paginado antes."""

    terminar, truncada = evaluar(
        desde=VENTANA, total=1832, total_conocido=True, pagina_corta=False
    )

    assert not terminar, "quedan 1782 productos por pedir"
    assert not truncada


def test_6_el_total_exacto_no_es_solo_el_caso_de_una_pagina():
    """Múltiplo de VENTANA en cualquier cantidad de páginas, y sin stock."""

    for total in (100, 250, 2450):
        terminar, truncada = evaluar(
            desde=total, total=total, total_conocido=True, pagina_corta=False
        )

        assert terminar, f"{total=} alcanzado"
        assert not truncada, f"{total=} declarado y leído entero"


def test_7_total_declarado_cero_no_trunca():
    """`total_desde_resources` separa 'no sé' de 'sé que es cero' (v11)."""

    terminar, truncada = evaluar(
        desde=0, total=0, total_conocido=True, pagina_corta=False
    )

    assert terminar
    assert not truncada, (
        "una categoría vacía declarada como vacía no esconde nada; "
        "'no sé' es total_conocido=False y ese caso lo cubre el test 3"
    )


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
