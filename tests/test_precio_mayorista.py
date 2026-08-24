#!/usr/bin/env python3
"""
Regresión del precio mayorista contra verdad EXTERNA.

Qué prueba
----------
`fichas_publicadas_20260822.csv` son 23 fichas de producto capturadas a mano
del storefront de Makro el 2026-08-22. Costaron una tarde de capturas que no
se puede reproducir automáticamente. Es la única referencia del proyecto que
NO sale de la misma API que el motor está midiendo: si VTEX miente, todos los
demás fixtures mienten con él. Estas 23 filas son lo que Makro le imprime al
cliente.

Cada fila trae los insumos de la API (`price_api`, `list_price_api`,
`descuento_api`, `bi_umbral_api`) y la verdad observada (`publica_escalon`,
`mayorista_esperado`). La columna `biprecio_status_motor_20260822` NO es
verdad observada: es lo que el motor respondía ese día, y queda como registro,
no como criterio. El test computa desde los insumos con la lógica del
MOTOR y compara contra lo observado. Es autocontenido: no lee `filas.csv`, no
toca la red.

Cómo se engancha al motor
-------------------------
Importa `calcular_mayorista` de
`retail_engine.collectors.makro_plazavea` — la función pura donde vive la
regla — y `a_centavos`, que es como el motor convierte soles a centavos. NO
reimplementa la aritmética: si este archivo copia la fórmula, deja de ser un
test del motor y pasa a ser un test de sí mismo.

Se corre solo (`python3 tests/test_precio_mayorista.py`, imprime el informe y
sale 0/1) o bajo pytest.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

if str(RAIZ / "src") not in sys.path:
    sys.path.insert(0, str(RAIZ / "src"))

from retail_engine.collectors.makro_plazavea import (  # noqa: E402
    a_centavos,
    calcular_mayorista,
)

FIXTURE = RAIZ / "tests" / "fixtures" / "makro_plazavea" / "fichas_publicadas_20260822.csv"


def dec(valor: str | None) -> Decimal | None:
    """Celda vacía -> None. Vacío no es cero."""

    limpio = (valor or "").strip()

    return Decimal(limpio) if limpio else None


def ent(valor: str | None) -> int | None:
    limpio = (valor or "").strip()

    return int(limpio) if limpio else None


@dataclass
class Resultado:
    sku_id: str
    product_name: str
    estado: str
    # predicho por el motor
    publica_pred: str
    mayorista_pred: Decimal | None
    # observado en la ficha
    publica_obs: str
    mayorista_obs: Decimal | None

    @property
    def ok(self) -> bool:
        return (
            self.publica_pred == self.publica_obs
            and self.mayorista_pred == self.mayorista_obs
        )


def evaluar_ficha(fila: dict[str, str]) -> Resultado:
    """
    Una ficha -> el veredicto del motor contra lo que Makro publicó.

    `publica_escalon` se traduce a "el motor entregó un precio mayorista":
    con la lógica actual eso es exactamente `estado == COMPLETO`, que es la
    única condición bajo la cual `enriquecer_fila` escribe
    `precio_mayorista`.
    """

    veredicto = calcular_mayorista(
        a_centavos(dec(fila["price_api"])),
        a_centavos(dec(fila["list_price_api"])),
        ent(fila["bi_umbral_api"]),
        dec(fila["descuento_api"]),
    )

    cents = veredicto["mayorista_cents"]
    completo = veredicto["estado"] == "COMPLETO"

    mayorista_pred = (
        Decimal(cents) / 100 if (completo and cents is not None) else None
    )

    return Resultado(
        sku_id=fila["sku_id"],
        product_name=fila["product_name"],
        estado=veredicto["estado"],
        publica_pred="SI" if completo else "NO",
        mayorista_pred=mayorista_pred,
        publica_obs=fila["publica_escalon"].strip(),
        mayorista_obs=dec(fila["mayorista_esperado"]),
    )


def evaluar_fixture() -> list[Resultado]:
    with FIXTURE.open(encoding="utf-8") as fuente:
        return [evaluar_ficha(fila) for fila in csv.DictReader(fuente)]


def informe(resultados: list[Resultado]) -> str:
    fallan = [r for r in resultados if not r.ok]
    lineas = [
        f"fixture: {FIXTURE.relative_to(RAIZ)}",
        f"pasan:   {len(resultados) - len(fallan)}/{len(resultados)}",
    ]

    if not fallan:
        lineas.append("")
        lineas.append("sin fallas")
        return "\n".join(lineas)

    def money(valor: Decimal | None) -> str:
        return f"{valor:.2f}" if valor is not None else "(vacío)"

    lineas.append("")
    lineas.append(f"fallan ({len(fallan)}):")
    lineas.append("")
    lineas.append(
        f"  {'sku_id':<10} {'estado':<12} "
        f"{'predicho':>10} {'observado':>10}  producto"
    )

    for r in fallan:
        predicho = f"{r.publica_pred} {money(r.mayorista_pred)}"
        observado = f"{r.publica_obs} {money(r.mayorista_obs)}"
        lineas.append(
            f"  {r.sku_id:<10} {r.estado:<12} "
            f"{predicho:>10} {observado:>10}  {r.product_name}"
        )

    lineas.append("")
    lineas.append("skus que fallan: " + ",".join(r.sku_id for r in fallan))

    return "\n".join(lineas)


def test_fichas_publicadas() -> None:
    """23/23 es el criterio. Menos que eso es el bug de la fórmula vivo."""

    resultados = evaluar_fixture()
    fallan = [r for r in resultados if not r.ok]

    assert not fallan, "\n" + informe(resultados)


def main() -> int:
    resultados = evaluar_fixture()
    print(informe(resultados))

    return 0 if all(r.ok for r in resultados) else 1


if __name__ == "__main__":
    raise SystemExit(main())
