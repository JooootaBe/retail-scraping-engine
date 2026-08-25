#!/usr/bin/env python3
"""
El estado por-corrida arranca completo (el KeyError de v22).

Qué prueba
----------
`MEDICION`, `STOCK_CADENA_ESTADO`, `AUDITORIA_MAYORISTA` y
`ALARMA_FIRMA_DISPARADA` son globales de módulo para que
`escribir_manifiesto()` los lea sin pasarlos por parámetro. `main()` los
reinicia al empezar cada corrida, porque una segunda corrida en el mismo
proceso tiene que arrancar limpia.

EL HECHO: hasta v22 ese reinicio era una copia literal del diccionario,
escrita adentro de `main()`. O sea que cada forma vivía DOS veces. v22
agregó `propagadas` y `estratos` a `AUDITORIA_MAYORISTA`, actualizó el
literal de módulo y no la copia — y la corrida en vivo murió con
`KeyError: 'propagadas'` en el bucle de auditoría, con los 53 tests en
verde.

POR QUÉ NINGÚN TEST LO VIO: el reinicio vivía adentro de una `main()`
async de 600 líneas que abre Playwright y sale a la red antes de llegar a
la fase de auditoría. No había forma de ejecutarlo sin una corrida real, y
los tests de la auditoría construyen sus propios diccionarios. La fase
estaba cubierta; su INICIALIZACIÓN no, y era la única parte que ningún
stub podía sustituir.

Lo que lo cierra no es este archivo solo: es que `reiniciar_estado_por_corrida`
salió de `main()` y que la forma de cada diccionario vive en una sola
función. Este archivo verifica que siga siendo así.

Qué se verifica
---------------
1. Después de reiniciar, cada diccionario tiene EXACTAMENTE las claves de
   su función de forma — ni de menos (el KeyError) ni de más (basura de la
   corrida anterior).
2. Los contadores que el motor incrementa con `+=` admiten `+=` recién
   reiniciados. Es la operación exacta que reventó.
3. Reiniciar borra lo de la corrida anterior, que es para lo que existe.
4. El diccionario de módulo y el reinicio no pueden divergir, porque salen
   de la misma función.

Nada sale a la red. Se corre solo
(`python3 tests/makro_plazavea/test_estado_por_corrida.py`) o bajo pytest.
"""

from __future__ import annotations

import sys
from pathlib import Path

# tests/makro_plazavea/<archivo>.py -> raíz del repo: dos niveles.
RAIZ = Path(__file__).resolve().parents[2]

if str(RAIZ / "src") not in sys.path:
    sys.path.insert(0, str(RAIZ / "src"))

import retail_engine.collectors.makro_plazavea as mk  # noqa: E402

# (diccionario, función de forma). Si alguna vez se agrega un quinto
# diccionario de estado por-corrida, va acá.
ESTADOS = (
    ("MEDICION", "estado_medicion_inicial"),
    ("STOCK_CADENA_ESTADO", "estado_stock_cadena_inicial"),
    ("AUDITORIA_MAYORISTA", "estado_auditoria_mayorista_inicial"),
)

# Contadores que el motor incrementa con `+=` en algún punto de la corrida.
# `propagadas` es el que reventó en vivo.
CONTADORES = (
    ("AUDITORIA_MAYORISTA", "propagadas"),
    ("AUDITORIA_MAYORISTA", "solicitadas"),
    ("AUDITORIA_MAYORISTA", "realizadas"),
    ("AUDITORIA_MAYORISTA", "coinciden"),
    ("AUDITORIA_MAYORISTA", "discrepan"),
    ("AUDITORIA_MAYORISTA", "fallidas"),
    ("MEDICION", "esperadas"),
    ("MEDICION", "realizadas"),
    ("MEDICION", "pendientes_total"),
)


def test_1_reiniciar_deja_todas_las_claves_de_la_forma():
    """El bug exacto: una clave del literal que el reinicio no traía."""

    mk.reiniciar_estado_por_corrida()

    for nombre, fabrica in ESTADOS:
        real = getattr(mk, nombre)
        esperadas = set(getattr(mk, fabrica)())
        presentes = set(real)

        assert presentes == esperadas, (
            f"{nombre}: faltan {sorted(esperadas - presentes)}, "
            f"sobran {sorted(presentes - esperadas)}"
        )


def test_2_los_contadores_admiten_incremento_recien_reiniciados():
    """`AUDITORIA_MAYORISTA['propagadas'] += len(...)` — el KeyError de v22."""

    mk.reiniciar_estado_por_corrida()

    for nombre, clave in CONTADORES:
        estado = getattr(mk, nombre)

        try:
            estado[clave] += 1
        except KeyError:  # pragma: no cover - es lo que el test evita
            raise AssertionError(
                f"{nombre}['{clave}'] no existe tras reiniciar: el motor la "
                "incrementa con += y la corrida muere ahí"
            )

    mk.reiniciar_estado_por_corrida()


def test_3_estratos_existe_y_tiene_sus_dos_lados():
    """La otra clave de v22. No revienta, pero se pierde del manifiesto."""

    mk.reiniciar_estado_por_corrida()

    # La pertenencia se comprueba antes de leer: un KeyError crudo acá
    # abortaría el corredor en vez de reportar una falla, y este archivo
    # existe justamente para el caso en que la clave no está.
    assert "estratos" in mk.AUDITORIA_MAYORISTA, (
        "estratos no sobrevive al reinicio: no rompe la corrida como "
        "propagadas, se pierde callado del manifiesto"
    )

    estratos = mk.AUDITORIA_MAYORISTA["estratos"]

    assert set(estratos) == {"con_promo_unitaria", "sin_promo_unitaria"}
    assert all(valor == 0 for valor in estratos.values())


def test_4_reiniciar_borra_la_corrida_anterior():
    """Para lo que existe: dos corridas en el mismo proceso no se mezclan."""

    mk.reiniciar_estado_por_corrida()

    mk.AUDITORIA_MAYORISTA["detalle"].append({"sku_id": "de la corrida vieja"})
    mk.AUDITORIA_MAYORISTA["propagadas"] = 7
    # Se asigna el sub-diccionario entero en vez de indexar dentro: si la
    # clave no existe, ensuciarla no debe reventar antes de llegar al assert.
    mk.AUDITORIA_MAYORISTA["estratos"] = {
        "con_promo_unitaria": 3,
        "sin_promo_unitaria": 1,
    }
    mk.MEDICION["realizadas"] = 99
    mk.MEDICION["completa"] = False
    mk.STOCK_CADENA_ESTADO["motivo"] = "presupuesto"
    mk.ALARMA_FIRMA_DISPARADA["359"] = True

    mk.reiniciar_estado_por_corrida()

    assert mk.AUDITORIA_MAYORISTA["detalle"] == []
    assert mk.AUDITORIA_MAYORISTA.get("propagadas") == 0
    assert mk.AUDITORIA_MAYORISTA.get("estratos", {}).get("con_promo_unitaria") == 0
    assert mk.MEDICION["realizadas"] == 0
    assert mk.MEDICION["completa"] is True
    assert mk.STOCK_CADENA_ESTADO["motivo"] == ""
    assert mk.ALARMA_FIRMA_DISPARADA == {}


def test_5_la_forma_no_se_comparte_entre_corridas():
    """Las estructuras anidadas se rehacen, no se reciclan.

    Si `estado_*_inicial` devolviera una constante de módulo, dos corridas
    compartirían la MISMA lista `detalle` y la segunda arrancaría con las
    filas de la primera aunque el reinicio hubiera corrido.
    """

    mk.reiniciar_estado_por_corrida()
    primera_detalle = mk.AUDITORIA_MAYORISTA["detalle"]
    primera_estratos = mk.AUDITORIA_MAYORISTA.get("estratos")

    mk.reiniciar_estado_por_corrida()

    assert mk.AUDITORIA_MAYORISTA["detalle"] is not primera_detalle
    assert mk.AUDITORIA_MAYORISTA.get("estratos") is not primera_estratos


def test_6_main_no_reinicia_por_su_cuenta():
    """El reinicio salió de `main()`; si vuelve a entrar, vuelve el bug.

    Lo que se busca es un `.update(` con un literal de diccionario sobre
    alguno de estos globales dentro del archivo: ésa es la forma exacta que
    se duplicó y quedó desincronizada en v22.
    """

    fuente = (
        RAIZ / "src" / "retail_engine" / "collectors" / "makro_plazavea.py"
    ).read_text(encoding="utf-8")

    for nombre, _fabrica in ESTADOS:
        assert f"{nombre}.update(\n" not in fuente, (
            f"{nombre} se vuelve a reiniciar con un literal en línea: la "
            "forma queda escrita dos veces y pueden divergir otra vez. Usá "
            f"la función de forma correspondiente."
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

    # A diferencia de los otros archivos, acá se atrapa Exception y no solo
    # AssertionError: el modo de falla que este archivo prueba ES un
    # KeyError, y dejarlo escapar abortaría el corredor a mitad del informe
    # en vez de reportar qué prueba falló.
    fallan: list[tuple[str, Exception]] = []

    for nombre, funcion in pruebas:
        try:
            funcion()
            print(f"  ok    {nombre}")
        except Exception as exc:
            fallan.append((nombre, exc))
            print(f"  FALLA {nombre}  ({type(exc).__name__})")

    print()
    print(f"pasan: {len(pruebas) - len(fallan)}/{len(pruebas)}")

    for nombre, exc in fallan:
        print()
        print(f"{nombre}: {exc}")

    return 1 if fallan else 0


if __name__ == "__main__":
    raise SystemExit(main())
