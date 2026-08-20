#!/usr/bin/env python3
"""
v2 — SONDA DE TRI-PRECIO, REDONDEO Y MÉTODO DE PAGO — MAKRO PERÚ (VTEX)
=======================================================================

Continuación de `test/v1.py`. v1 dejó probado que el escalón mayorista de
Makro se puede derivar del CATÁLOGO sin un request extra por SKU:

    precio mayorista = Price - PromotionalPriceTableItemsDiscount
                       a partir de CantidadBiPrecioMK unidades

y que el teaser `MAKRO-Bi-Precio|Vigente Oculto` ya viaja en qty=1, así que
detectar qué SKUs tienen bi-precio es gratis.

Un análisis posterior del catálogo ya archivado
(`v1_catalogo_20260818_213208.json`, 50 productos) abrió tres agujeros en
esa reconstrucción. Esta sonda los cierra con 8 requests:

  A — LA ESCALERA PUEDE TENER TRES NIVELES, NO DOS.
      Existe `CantidadTriPrecioMK` (17/50 productos, valores 4 y 6) junto a
      `CantidadBiPrecioMK`. Pero el teaser trae UN SOLO
      `PromotionalPriceTableItemsDiscount`: no se sabe si ese descuento es
      el del tramo bi, el del tri, o si el tri usa otro que el catálogo no
      muestra. Se mide el SKU 11531720 (Removedor Papel ARO, P=13.70,
      bi=3, tri=6, desc=0.90) en qty 1/3/5/6/7. Si el tri existe, el
      unitario de qty 3-5 tiene que ser distinto del de qty 6-7.

  B — EL REDONDEO NO ESTÁ RESUELTO.
      Trece SKUs traen el descuento con TRES decimales (0.270, 0.210,
      0.330, 0.140, 0.520, 0.190, 0.160 — todos ≈2% del precio). La
      reconstrucción de v1 da precios que no existen en centavos: 13.03,
      10.09, 16.37. Ninguno de esos SKUs fue medido nunca. Se mide el SKU
      11531674 (Bolsas Papel Kraft #6, P=13.30, desc=0.270) en qty 1/3/6 y
      se reporta el centavo exacto que devuelve VTEX.

  C — EL TEASER ESTÁ CONDICIONADO A UN MÉTODO DE PAGO.
      `Conditions.Parameters.PaymentMethodId = "4"` en los 26 productos que
      traen teaser (y `Conditions.MinimumQuantity` es 0: NO es el umbral).
      `simulation` no envía método de pago alguno. Si el descuento igual se
      aplica, ese parámetro es informativo, no restrictivo. Se responde con
      la evidencia de A y B — CERO requests extra.

DISCIPLINA (igual que v1)
-------------------------
  · Toda respuesta se ARCHIVA EN CRUDO ANTES de parsear nada.
  · Tope duro de 10 requests, intervalo mínimo 1.5s.
  · Reutiliza `Cliente` del motor: rate limiting, backoff y tope son los
    del motor, no una copia.
  · `PromotionalPriceTableItemsDiscount` se busca SIEMPRE POR NOMBRE,
    nunca por posición `Parameters[1]`: el orden de los parámetros es de
    VTEX, no un contrato.
  · SOLO escribe archivos nuevos. No toca engines/, ni los CSV, ni nada
    preexistente en salida/.
  · Lo que la respuesta no diga, esta sonda no lo infiere: un tramo que no
    se puede determinar se reporta como no determinado.

USO
---
    python3 test/v2.py
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


# ===========================================================================
# REUSO: v1 (que a su vez carga el motor de producción)
# ===========================================================================

def raiz_repo() -> Path:
    """Raíz del repo por marca (pyproject.toml), no por contar niveles."""

    p = Path(__file__).resolve()
    for padre in p.parents:
        if (padre / "pyproject.toml").exists():
            return padre
    raise SystemExit("No encuentro la raíz del repo")


RAIZ = raiz_repo()
V1_PY = Path(__file__).resolve().parent / "v1.py"


def cargar_v1():
    """
    Importa `test/v1.py` como módulo.

    v1 no ejecuta nada al importarse (su `main()` está detrás de
    `if __name__ == "__main__"`), y trae ya resueltos el recorrido genérico
    del JSON (`caminar`), la conversión numérica tolerante (`como_numero`) y
    el handle al motor (`MK`). Duplicar eso acá sería tener dos versiones de
    la misma lógica de rastreo y que una se quede vieja.
    """

    if not V1_PY.exists():
        raise SystemExit(f"No encuentro la sonda v1 en {V1_PY}")

    spec = importlib.util.spec_from_file_location("v1_sonda", V1_PY)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["v1_sonda"] = modulo
    spec.loader.exec_module(modulo)

    return modulo


V1 = cargar_v1()
MK = V1.MK
caminar = V1.caminar
como_numero = V1.como_numero
soles = V1.soles


# ===========================================================================
# PARÁMETROS
# ===========================================================================

NODO = MK.NODOS["359"]          # Santa Anita, postal 150137
TOPE_REQUESTS = 10

# Lo que el catálogo archivado por v1 declara de cada objetivo. Se guarda
# acá para CONTRASTAR contra lo medido, no para reemplazarlo: si la
# medición contradice al catálogo, gana la medición y se reporta el choque.
OBJETIVOS = [
    {
        "clave": "A",
        "pregunta": "¿El tri-precio existe y cuánto vale?",
        "sku": "11531720",
        "seller": "1",
        "nombre": "Removedor Papel ARO x250un",
        "precio_catalogo": 13.70,
        "bi": 3,
        "tri": 6,
        "descuento_catalogo": 0.90,
        "cantidades": [1, 3, 5, 6, 7],
    },
    {
        "clave": "B",
        "pregunta": "¿Cómo redondea Makro un descuento de tres decimales?",
        "sku": "11531674",
        "seller": "1",
        "nombre": "Bolsas Papel Kraft ARO #6 100un",
        "precio_catalogo": 13.30,
        "bi": 3,
        "tri": 6,
        "descuento_catalogo": 0.270,
        "cantidades": [1, 3, 6],
    },
]

# Nombre del parámetro del teaser que lleva el descuento. Se busca POR
# NOMBRE en cualquier profundidad; nunca por índice.
PARAM_DESCUENTO = "PromotionalPriceTableItemsDiscount"
PARAM_ITEMS = "PromotionalPriceTableItemsIds"


# ===========================================================================
# INFORME
# ===========================================================================

INFORME: list[str] = []


def r(linea: str = "") -> None:
    print(linea)
    INFORME.append(linea)


def titulo(texto: str) -> None:
    r("")
    r("=" * 100)
    r(texto)
    r("=" * 100)


def centavos(valor: Any) -> str:
    """Centavos crudos, sin convertir. El redondeo se discute en centavos."""

    return "-" if valor is None else f"{valor}"


# ===========================================================================
# ARCHIVADO CRUDO (siempre ANTES de parsear)
# ===========================================================================

MARCA = datetime.now().strftime("%Y%m%d_%H%M%S")
DIR_RAW = RAIZ / "data" / MK.MOTOR / "raw" / datetime.now().strftime("%Y-%m-%d")

ARCHIVADOS: list[Path] = []


def archivar(nombre: str, contenido: Any) -> Path:
    """Escribe la respuesta cruda a disco ANTES de que nadie la mire."""

    DIR_RAW.mkdir(parents=True, exist_ok=True)

    destino = DIR_RAW / nombre
    destino.write_text(
        json.dumps(contenido, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    ARCHIVADOS.append(destino)

    return destino


# ===========================================================================
# BÚSQUEDA POR NOMBRE (nunca por índice)
# ===========================================================================


def parametros_por_nombre(objeto: Any, nombre: str) -> list[tuple[str, Any]]:
    """
    Busca `{"name": <nombre>, "value": X}` en cualquier profundidad y en
    cualquier capitalización (`name`/`Name`), y devuelve (ruta, valor).

    Por qué así y no `Parameters[1].Value`: el orden de los parámetros del
    teaser lo decide VTEX. Anclar en el índice 1 funciona hasta el día en
    que VTEX agrega un parámetro adelante, y ese día el motor lee el ID del
    producto como si fuera un descuento y nadie se entera.
    """

    encontrados: list[tuple[str, Any]] = []

    def recorrer(nodo: Any, ruta: str) -> None:
        if isinstance(nodo, dict):
            claves = {k.lower(): k for k in nodo.keys()}

            if "name" in claves and "value" in claves:
                if MK.s(nodo[claves["name"]]) == nombre:
                    encontrados.append(
                        (f"{ruta}.{claves['value']}", nodo[claves["value"]])
                    )

            for clave, valor in nodo.items():
                recorrer(valor, f"{ruta}.{clave}" if ruta else str(clave))

        elif isinstance(nodo, list):
            for indice, valor in enumerate(nodo):
                recorrer(valor, f"{ruta}[{indice}]")

    recorrer(objeto, "")

    return encontrados


# ===========================================================================
# MEDICIÓN
# ===========================================================================


async def simular(cliente, sku: str, seller: str, cantidad: int) -> tuple[int, Any, Path]:
    """
    `simulation` con quantity = N contra el nodo 359.

    Mismo endpoint y mismo cuerpo que `consultar_simulation` del motor. Lo
    único que cambia es `quantity`. Y lo que NO cambia importa para la
    pregunta C: el cuerpo no lleva ningún método de pago.
    """

    cuerpo = {
        "items": [{"id": int(sku), "quantity": cantidad, "seller": seller or "1"}],
        "country": NODO.country,
        "postalCode": NODO.postal_code,
        "geoCoordinates": [NODO.longitude, NODO.latitude],
    }

    status, datos, _ = await cliente.pedir(
        f"{MK.BASE_URL}/api/checkout/pub/orderforms/simulation?sc={MK.SALES_CHANNEL}",
        metodo="POST",
        body=cuerpo,
    )

    # ARCHIVAR PRIMERO. Nada de parseo antes de esta línea.
    ruta = archivar(
        f"v2_sim_{sku}_qty{cantidad}_{MARCA}.json",
        {
            "sonda": "v2_tri_precio_redondeo_pago",
            "fase": "medicion",
            "sku": sku,
            "nodo": NODO.node_id,
            "branch": NODO.branch,
            "postal_code": NODO.postal_code,
            "quantity": cantidad,
            "http_status": status,
            "momento": datetime.now().isoformat(timespec="seconds"),
            "request_body": cuerpo,
            "respuesta": datos,
        },
    )

    return status, datos, ruta


def leer(datos: Any, sku: str, cantidad: int) -> dict[str, Any]:
    """Lo que hay que reportar de una respuesta de simulation."""

    lectura: dict[str, Any] = {
        "item_encontrado": False,
        "availability": "",
        "quantity_devuelta": None,
        "price": None,
        "list_price": None,
        "selling_price": None,
        "total_cent": None,
        "unitario_cent": None,
        "selling_prices": [],
        "price_tags": [],
        "reason": None,
        "teasers": [],
        "descuentos_teaser": [],
        "payment_data": {},
    }

    if not isinstance(datos, dict):
        return lectura

    for item in datos.get("items") or []:
        if not isinstance(item, dict) or MK.s(item.get("id")) != sku:
            continue

        definicion = item.get("priceDefinition") or {}
        total = definicion.get("total")

        if total is None:
            for totalizador in datos.get("totals") or []:
                if isinstance(totalizador, dict) and totalizador.get("id") == "Items":
                    total = totalizador.get("value")
                    break

        lectura.update(
            {
                "item_encontrado": True,
                "availability": MK.s(item.get("availability")),
                "quantity_devuelta": item.get("quantity"),
                "price": item.get("price"),
                "list_price": item.get("listPrice"),
                "selling_price": item.get("sellingPrice"),
                "total_cent": total,
                "selling_prices": definicion.get("sellingPrices") or [],
                "price_tags": item.get("priceTags") or [],
                "reason": definicion.get("reason"),
                "calculado": definicion.get("calculatedSellingPrice"),
            }
        )

        if isinstance(total, (int, float)) and cantidad:
            lectura["unitario_cent"] = total / cantidad

        break

    beneficios = datos.get("ratesAndBenefitsData") or {}

    if isinstance(beneficios, dict):
        lectura["teasers"] = beneficios.get("teaser") or []
        lectura["identificadores"] = beneficios.get("rateAndBenefitsIdentifiers") or []

    # POR NOMBRE, en toda la respuesta.
    lectura["descuentos_teaser"] = parametros_por_nombre(datos, PARAM_DESCUENTO)
    lectura["ids_teaser"] = parametros_por_nombre(datos, PARAM_ITEMS)

    pago = datos.get("paymentData") or {}

    if isinstance(pago, dict):
        lectura["payment_data"] = {
            "payments": pago.get("payments"),
            "installmentOptions_n": len(pago.get("installmentOptions") or []),
            "paymentSystems_n": len(pago.get("paymentSystems") or []),
        }

    return lectura


# ===========================================================================
# ORQUESTACIÓN
# ===========================================================================


async def correr(argumentos) -> int:
    from playwright.async_api import async_playwright

    titulo("SONDA v2 — TRI-PRECIO · REDONDEO · MÉTODO DE PAGO — MAKRO PERÚ")
    r(f"Momento    : {datetime.now().isoformat(timespec='seconds')}")
    r(f"Motor leído: {MK.__name__} v{MK.VERSION}  ·  sonda previa: {V1_PY.name}")
    r(f"Nodo       : {NODO.node_id} {NODO.branch} · postal {NODO.postal_code} · sc={MK.SALES_CHANNEL}")
    r(f"Tope       : {TOPE_REQUESTS} requests · intervalo {argumentos.intervalo}s")
    r(f"Evidencia  : {DIR_RAW}")
    r("")
    r("Plan de gasto:")

    planeadas = 0

    for objetivo in OBJETIVOS:
        planeadas += len(objetivo["cantidades"])
        r(f"  {objetivo['clave']} · sku {objetivo['sku']} ({objetivo['nombre'][:40]}) "
          f"qty {objetivo['cantidades']} -> {len(objetivo['cantidades'])} requests")

    r(f"  total planeado: {planeadas}/{TOPE_REQUESTS}")

    resultados: dict[str, dict[int, dict]] = {}
    respuestas: dict[str, dict[int, Any]] = {}

    async with async_playwright() as playwright:
        navegador = await MK.abrir_navegador(playwright, argumentos.canal, not argumentos.headed)

        try:
            contexto = await navegador.new_context(
                locale="es-PE",
                timezone_id="America/Lima",
                viewport={"width": 1440, "height": 900},
            )

            cliente = MK.Cliente(
                contexto.request,
                intervalo=argumentos.intervalo,
                reintentos=argumentos.reintentos,
                tope=TOPE_REQUESTS,
            )
            cliente.fase = "medicion"

            for objetivo in OBJETIVOS:
                clave = objetivo["clave"]
                resultados[clave] = {}
                respuestas[clave] = {}

                titulo(f"BLOQUE {clave} — {objetivo['pregunta']}")
                r(f"SKU {objetivo['sku']} — {objetivo['nombre']}")
                r(f"Catálogo declara: Price {objetivo['precio_catalogo']:.2f} · "
                  f"CantidadBiPrecioMK={objetivo['bi']} · "
                  f"CantidadTriPrecioMK={objetivo['tri']} · "
                  f"{PARAM_DESCUENTO}={objetivo['descuento_catalogo']}")
                r("")

                for cantidad in objetivo["cantidades"]:
                    if cliente.contador >= TOPE_REQUESTS:
                        r(f"  qty {cantidad}: SIN PRESUPUESTO (tope {TOPE_REQUESTS}) — NO MEDIDO")
                        continue

                    try:
                        status, datos, ruta = await simular(
                            cliente, objetivo["sku"], objetivo["seller"], cantidad
                        )
                    except MK.TopeAgotadoError as exc:
                        r(f"  qty {cantidad}: tope agotado ({exc}) — se corta el bloque")
                        break
                    except Exception as exc:
                        r(f"  qty {cantidad}: ERROR {type(exc).__name__}: {str(exc)[:90]}")
                        continue

                    lectura = leer(datos, objetivo["sku"], cantidad)
                    lectura["http_status"] = status
                    lectura["archivo"] = ruta

                    resultados[clave][cantidad] = lectura
                    respuestas[clave][cantidad] = datos

                    r(f"  qty {cantidad}: HTTP {status} · total={soles(lectura['total_cent'])} "
                      f"({centavos(lectura['total_cent'])} cent) · "
                      f"unitario={soles(lectura['unitario_cent'])} · "
                      f"priceTags={len(lectura['price_tags'])} · "
                      f"teasers={len(lectura['teasers'])} · {ruta.name}")

            r("")
            r(f"Requests gastadas: {cliente.contador}/{TOPE_REQUESTS} "
              f"(por fase: {cliente.por_fase}) · reintentos: {cliente.reintentos_usados}")

        finally:
            await navegador.close()

    tabla(resultados)
    detalle(resultados)
    responder_a(resultados)
    responder_b(resultados)
    responder_c(resultados)

    r("")
    r("Evidencia cruda archivada:")

    for ruta in ARCHIVADOS:
        r(f"  {ruta}")

    return 0


# ===========================================================================
# ENTREGABLE 1 — TABLA POR SKU
# ===========================================================================


def tabla(resultados: dict) -> None:
    titulo("TABLA POR SKU — qty · total · unitario · priceTags · teasers")

    for objetivo in OBJETIVOS:
        clave = objetivo["clave"]
        por_cantidad = resultados.get(clave, {})

        r("")
        r(f"SKU {objetivo['sku']} — {objetivo['nombre']}  "
          f"(Price catálogo {objetivo['precio_catalogo']:.2f}, "
          f"bi={objetivo['bi']}, tri={objetivo['tri']}, "
          f"desc={objetivo['descuento_catalogo']})")
        r("")
        r(f"  {'qty':>4}  {'total':>10}  {'total(cent)':>12}  {'unitario':>10}  "
          f"{'unit(cent)':>11}  {'priceTags':>9}  {'teasers':>8}")
        r(f"  {'-'*4}  {'-'*10}  {'-'*12}  {'-'*10}  {'-'*11}  {'-'*9}  {'-'*8}")

        for cantidad in objetivo["cantidades"]:
            lectura = por_cantidad.get(cantidad)

            if not lectura or not lectura["item_encontrado"]:
                r(f"  {cantidad:>4}  {'NO MEDIDO' if not lectura else 'ITEM AUSENTE':>10}")
                continue

            unitario = lectura["unitario_cent"]

            r(f"  {cantidad:>4}  {soles(lectura['total_cent']):>10}  "
              f"{centavos(lectura['total_cent']):>12}  "
              f"{soles(unitario):>10}  "
              f"{(f'{unitario:.2f}' if unitario is not None else '-'):>11}  "
              f"{len(lectura['price_tags']):>9}  {len(lectura['teasers']):>8}")

        # Nombres de lo que hay en esas columnas, que un número no cuenta.
        for cantidad in objetivo["cantidades"]:
            lectura = por_cantidad.get(cantidad)

            if not lectura or not lectura["item_encontrado"]:
                continue

            etiquetas = [MK.s(t.get("name")) for t in lectura["price_tags"] if isinstance(t, dict)]
            teasers = [MK.s(t.get("name")) for t in lectura["teasers"] if isinstance(t, dict)]

            r(f"    qty {cantidad}: priceTags={etiquetas or '[]'}")
            r(f"    qty {cantidad}: teasers={teasers or '[]'}")


# ===========================================================================
# ENTREGABLE 2 — DETALLE CRUDO POR RESPUESTA
# ===========================================================================


def detalle(resultados: dict) -> None:
    titulo("DETALLE POR RESPUESTA (sellingPrices, priceTags, teaser POR NOMBRE)")

    for objetivo in OBJETIVOS:
        clave = objetivo["clave"]

        r("")
        r("-" * 100)
        r(f"SKU {objetivo['sku']} — {objetivo['nombre']}")
        r("-" * 100)

        for cantidad in objetivo["cantidades"]:
            lectura = resultados.get(clave, {}).get(cantidad)

            r("")
            r(f"  ── qty {cantidad} ──  "
              f"({lectura['archivo'].name if lectura else 'NO MEDIDO'})")

            if not lectura:
                continue

            if not lectura["item_encontrado"]:
                r(f"     HTTP {lectura['http_status']} pero el item no volvió.")
                continue

            r(f"     availability={lectura['availability']} · "
              f"quantity devuelta={lectura['quantity_devuelta']}")
            r(f"     price={lectura['price']} · listPrice={lectura['list_price']} · "
              f"sellingPrice={lectura['selling_price']} · "
              f"calculatedSellingPrice={lectura.get('calculado')}")
            r(f"     priceDefinition.total={lectura['total_cent']} "
              f"({soles(lectura['total_cent'])}) · reason={lectura['reason']!r}")
            r(f"     UNITARIO DERIVADO = total/{cantidad} = "
              f"{lectura['unitario_cent']!r} cent ({soles(lectura['unitario_cent'])})")

            r("     priceDefinition.sellingPrices[]:")

            for entrada in lectura["selling_prices"] or [None]:
                r(f"       {json.dumps(entrada, ensure_ascii=False) if entrada else '(vacío)'}")

            r("     priceTags[]:")

            for etiqueta in lectura["price_tags"] or [None]:
                r(f"       {json.dumps(etiqueta, ensure_ascii=False) if etiqueta else '(vacío)'}")

            r(f"     {PARAM_DESCUENTO} (búsqueda POR NOMBRE en toda la respuesta):")

            for ruta, valor in lectura["descuentos_teaser"] or [(None, None)]:
                r(f"       {ruta} = {valor!r}" if ruta else "       (no aparece)")

            r("     teaser[].conditions:")

            for teaser in lectura["teasers"] or []:
                if isinstance(teaser, dict):
                    r(f"       {MK.s(teaser.get('name'))} -> "
                      f"{json.dumps(teaser.get('conditions'), ensure_ascii=False)}")

            if not lectura["teasers"]:
                r("       (sin teasers)")


# ===========================================================================
# RESPUESTA A — ¿EXISTE EL TRI-PRECIO?
# ===========================================================================


def niveles_de(por_cantidad: dict, cantidades: list[int]) -> list[tuple[int, float]]:
    return [
        (c, por_cantidad[c]["unitario_cent"])
        for c in cantidades
        if por_cantidad.get(c) and por_cantidad[c].get("unitario_cent") is not None
    ]


def responder_a(resultados: dict) -> None:
    objetivo = OBJETIVOS[0]
    por_cantidad = resultados.get("A", {})

    titulo("RESPUESTA A — ¿EL TRI-PRECIO EXISTE Y CUÁNTO VALE?")

    serie = niveles_de(por_cantidad, objetivo["cantidades"])

    if len(serie) < 2:
        r("NO DETERMINADO: no hay mediciones suficientes de este SKU.")
        return

    r(f"SKU {objetivo['sku']} · catálogo: Price {objetivo['precio_catalogo']:.2f} · "
      f"bi={objetivo['bi']} · tri={objetivo['tri']} · desc={objetivo['descuento_catalogo']}")
    r("")

    for cantidad, unitario in serie:
        r(f"  qty {cantidad}: unitario {soles(unitario)}  ({unitario:.2f} cent) · "
          f"priceTags={len(por_cantidad[cantidad]['price_tags'])}")

    distintos = sorted({round(u, 2) for _, u in serie}, reverse=True)

    r("")
    r(f"Niveles de precio distintos observados: {len(distintos)} -> "
      + " · ".join(soles(v) for v in distintos))

    # Tramos tal como los declara el catálogo.
    bi, tri = objetivo["bi"], objetivo["tri"]

    unit_bajo_bi = [u for c, u in serie if c < bi]
    unit_bi = [u for c, u in serie if bi <= c < tri]
    unit_tri = [u for c, u in serie if c >= tri]

    r("")
    r(f"Agrupado por los tramos que declara el catálogo (bi={bi}, tri={tri}):")
    r(f"  qty <  {bi}     : " + (" · ".join(soles(u) for u in unit_bajo_bi) or "sin datos"))
    r(f"  qty {bi}..{tri - 1}    : " + (" · ".join(soles(u) for u in unit_bi) or "sin datos"))
    r(f"  qty >= {tri}    : " + (" · ".join(soles(u) for u in unit_tri) or "sin datos"))

    r("")

    if not unit_bi or not unit_tri:
        r("VEREDICTO A: NO DETERMINADO — falta al menos un tramo medido.")
        return

    nivel_bi = round(min(unit_bi), 2)
    nivel_tri = round(min(unit_tri), 2)

    # ¿Un segundo priceTag cuando entra el tramo tri?
    tags_bi = max((len(por_cantidad[c]["price_tags"]) for c, _ in serie if bi <= c < tri), default=0)
    tags_tri = max((len(por_cantidad[c]["price_tags"]) for c, _ in serie if c >= tri), default=0)

    if nivel_tri < nivel_bi - 0.5:
        r(f"VEREDICTO A: EL TRI-PRECIO EXISTE Y SE APLICA.")
        r(f"  tramo bi  (qty {bi}-{tri - 1}) = {soles(nivel_bi)} por unidad")
        r(f"  tramo tri (qty >= {tri})   = {soles(nivel_tri)} por unidad")
        r(f"  escalón adicional = {soles(nivel_bi - nivel_tri)} por unidad")
        r("")
        r(f"  El catálogo trae UN SOLO {PARAM_DESCUENTO} "
          f"({objetivo['descuento_catalogo']}). Contra lo medido:")
        r(f"    Price - desc = "
          f"{objetivo['precio_catalogo'] - objetivo['descuento_catalogo']:.2f} "
          f"-> {'coincide con el tramo bi' if abs((objetivo['precio_catalogo'] - objetivo['descuento_catalogo']) * 100 - nivel_bi) < 1 else 'NO coincide con el tramo bi'}"
          f"; {'coincide con el tramo tri' if abs((objetivo['precio_catalogo'] - objetivo['descuento_catalogo']) * 100 - nivel_tri) < 1 else 'NO coincide con el tramo tri'}")
        r("    O sea: ese único descuento NO alcanza para reconstruir la")
        r("    escalera completa. El valor del tramo tri no está en el")
        r("    catálogo — hay que medirlo.")
    else:
        r("VEREDICTO A: EL TRI-PRECIO NO SE OBSERVA.")
        r(f"  El catálogo declara CantidadTriPrecioMK={tri}, pero el unitario a")
        r(f"  qty >= {tri} ({soles(nivel_tri)}) no baja respecto al tramo bi "
          f"({soles(nivel_bi)}).")
        r("  Declarado en el catálogo != aplicado por checkout. Con esta")
        r("  evidencia, `CantidadTriPrecioMK` no puede tomarse como un tramo real.")

    r("")
    r(f"Segundo priceTag: máximo de priceTags en el tramo bi = {tags_bi}; "
      f"en el tramo tri = {tags_tri}.")

    if tags_tri > tags_bi:
        r("  Sí aparece un priceTag adicional al entrar el tramo tri.")
    elif tags_tri == tags_bi and tags_tri > 0:
        r("  NO aparece un segundo priceTag: el mismo único priceTag cambia de")
        r("  monto. El tramo se lee del monto, no de la cantidad de tags.")
    else:
        r("  Sin priceTags que comparar.")


# ===========================================================================
# RESPUESTA B — REDONDEO
# ===========================================================================


def responder_b(resultados: dict) -> None:
    objetivo = OBJETIVOS[1]
    por_cantidad = resultados.get("B", {})

    titulo("RESPUESTA B — ¿CÓMO REDONDEA MAKRO UN DESCUENTO DE TRES DECIMALES?")

    r(f"SKU {objetivo['sku']} · catálogo: Price {objetivo['precio_catalogo']:.2f} · "
      f"desc={objetivo['descuento_catalogo']} · bi={objetivo['bi']} · tri={objetivo['tri']}")

    prediccion = objetivo["precio_catalogo"] - objetivo["descuento_catalogo"]

    r(f"Reconstrucción de v1: {objetivo['precio_catalogo']:.2f} - "
      f"{objetivo['descuento_catalogo']} = {prediccion:.3f}  "
      f"(un precio que no existe en centavos)")
    r("")

    serie = niveles_de(por_cantidad, objetivo["cantidades"])

    if not serie:
        r("NO DETERMINADO: no hay mediciones de este SKU.")
        return

    for cantidad, unitario in serie:
        lectura = por_cantidad[cantidad]
        r(f"  qty {cantidad}: total {lectura['total_cent']} cent "
          f"({soles(lectura['total_cent'])}) · unitario {unitario:.4f} cent "
          f"({soles(unitario)}) · sellingPrice {lectura['selling_price']} cent")

    con_descuento = [
        (c, u) for c, u in serie if c >= objetivo["bi"]
    ]

    r("")

    if not con_descuento:
        r("VEREDICTO B: NO DETERMINADO — no se midió ninguna cantidad dentro")
        r(f"del tramo con descuento (qty >= {objetivo['bi']}).")
        return

    cantidad_ref, unitario_ref = con_descuento[0]
    lectura_ref = por_cantidad[cantidad_ref]

    r(f"VEREDICTO B — a qty {cantidad_ref} (primer tramo con descuento):")
    r(f"  total real          : {lectura_ref['total_cent']} cent = "
      f"{soles(lectura_ref['total_cent'])}")
    r(f"  unitario real       : {unitario_ref:.4f} cent = {soles(unitario_ref)}")
    r(f"  sellingPrice        : {lectura_ref['selling_price']} cent = "
      f"{soles(lectura_ref['selling_price'])}")

    candidatos = {
        "13.03 (predicción literal de v1)": round(prediccion, 2),
        "13.00 (redondeo del descuento hacia arriba: 0.30)": objetivo["precio_catalogo"] - 0.30,
        "13.05 (redondeo del descuento hacia abajo: 0.25)": objetivo["precio_catalogo"] - 0.25,
        "13.10 (descuento truncado a 0.20)": objetivo["precio_catalogo"] - 0.20,
    }

    r("")
    r("  Contra los candidatos:")

    acertado = None

    for etiqueta, valor in candidatos.items():
        coincide = abs(valor * 100 - unitario_ref) < 0.51
        r(f"    {etiqueta:<52} = {valor:.2f}  -> "
          f"{'ES ESTE' if coincide else 'no'}")

        if coincide and acertado is None:
            acertado = etiqueta

    descuento_real_unit = (objetivo["precio_catalogo"] * 100 - unitario_ref) / 100
    descuento_total = objetivo["precio_catalogo"] * 100 * cantidad_ref - (lectura_ref["total_cent"] or 0)

    r("")
    r(f"  Descuento REAL por unidad  : {descuento_real_unit:.4f} soles "
      f"(catálogo declara {objetivo['descuento_catalogo']})")
    r(f"  Descuento REAL de la línea : {descuento_total:.0f} cent para "
      f"{cantidad_ref} unidades")

    if lectura_ref["price_tags"]:
        for etiqueta in lectura_ref["price_tags"]:
            if isinstance(etiqueta, dict):
                r(f"  priceTag: value={etiqueta.get('value')} "
                  f"rawValue={etiqueta.get('rawValue')} "
                  f"isPercentual={etiqueta.get('isPercentual')}")

    r("")

    if acertado:
        r(f"  RESULTADO: {acertado.split(' (')[0]}. "
          f"El unitario que devuelve VTEX es {soles(unitario_ref)}.")
    else:
        r(f"  RESULTADO: OTRO — {soles(unitario_ref)}. No es 13.03, ni 13.00,")
        r("  ni 13.05. El valor exacto está arriba y en el JSON archivado.")

    # ¿El total es divisible en centavos exactos?
    if lectura_ref["total_cent"] is not None:
        exacto = abs(unitario_ref - round(unitario_ref)) < 1e-9

        r("")

        if exacto:
            r("  El total se reparte en centavos exactos por unidad: el redondeo")
            r("  ocurre ANTES de multiplicar por la cantidad (precio unitario")
            r("  redondeado × qty).")
        else:
            r(f"  El total NO se reparte en centavos exactos por unidad "
              f"({unitario_ref:.4f} cent): el redondeo ocurre sobre el TOTAL de")
            r("  la línea, no sobre el precio unitario. Un unitario 'lindo' no")
            r("  existe acá — lo que VTEX cobra es el total.")

    otros = [(c, u) for c, u in serie if c > cantidad_ref and c >= objetivo["bi"]]

    if otros:
        r("")
        r("  Consistencia en otras cantidades del mismo SKU:")

        for cantidad, unitario in otros:
            r(f"    qty {cantidad}: unitario {soles(unitario)} "
              f"({unitario:.4f} cent) · total "
              f"{por_cantidad[cantidad]['total_cent']} cent"
              + ("  <- distinto tramo (tri)" if cantidad >= objetivo["tri"] else ""))


# ===========================================================================
# RESPUESTA C — MÉTODO DE PAGO
# ===========================================================================


def responder_c(resultados: dict) -> None:
    titulo("RESPUESTA C — ¿EL DESCUENTO APLICA SIN EL MÉTODO DE PAGO 4?")

    r("El cuerpo que envía esta sonda (y el que envía el motor en")
    r("`consultar_simulation`) NO contiene ningún método de pago: solo items,")
    r("country, postalCode y geoCoordinates. Si el descuento igual se aplicó,")
    r("`PaymentMethodId = 4` no es una condición que haya que satisfacer para")
    r("ver el precio mayorista.")
    r("")

    aplicados: list[str] = []
    no_aplicados: list[str] = []

    for objetivo in OBJETIVOS:
        por_cantidad = resultados.get(objetivo["clave"], {})

        base = None
        lectura1 = por_cantidad.get(1)

        if lectura1 and lectura1.get("unitario_cent") is not None:
            base = lectura1["unitario_cent"]

        for cantidad in objetivo["cantidades"]:
            lectura = por_cantidad.get(cantidad)

            if not lectura or lectura.get("unitario_cent") is None:
                continue

            if cantidad < objetivo["bi"]:
                continue

            hubo_descuento = base is not None and lectura["unitario_cent"] < base - 0.5
            etiquetas = len(lectura["price_tags"])

            linea = (
                f"  sku {objetivo['sku']} qty {cantidad}: unitario "
                f"{soles(lectura['unitario_cent'])} vs qty1 {soles(base)} · "
                f"priceTags={etiquetas} · pago enviado=NINGUNO · "
                f"paymentData.payments={lectura['payment_data'].get('payments')}"
            )

            if hubo_descuento:
                aplicados.append(linea)
            else:
                no_aplicados.append(linea)

    if aplicados:
        r("DESCUENTO APLICADO SIN ENVIAR MÉTODO DE PAGO:")

        for linea in aplicados:
            r(linea)

    if no_aplicados:
        r("")
        r("SIN DESCUENTO en estas cantidades (dentro del tramo declarado):")

        for linea in no_aplicados:
            r(linea)

    r("")

    if aplicados:
        r("VEREDICTO C: `PaymentMethodId = 4` es INFORMATIVO, no restrictivo —")
        r("al menos para el precio que devuelve `simulation`. El descuento se")
        r("aplicó en todas las cantidades listadas arriba sin que la request")
        r("declarara método de pago alguno.")
        r("")
        r("ALCANCE: esto dice qué precio devuelve la API, no qué cobra la caja.")
        r("Una condición de pago podría seguir existiendo en el checkout real")
        r("(o en otro medio de pago) sin que esta evidencia la contradiga.")
    elif no_aplicados:
        r("VEREDICTO C: NO SE OBSERVÓ descuento en ninguna cantidad medida")
        r("dentro del tramo declarado. Con esta evidencia no se puede decir si")
        r("la causa es el método de pago ausente u otra cosa: son dos hipótesis")
        r("que esta corrida no separa.")
    else:
        r("VEREDICTO C: NO DETERMINADO — sin mediciones utilizables.")


# ===========================================================================


def parsear_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sonda v2: tri-precio, redondeo y método de pago en Makro."
    )

    parser.add_argument("--intervalo", type=float, default=1.5,
                        help="Segundos mínimos entre requests (default 1.5).")
    parser.add_argument("--reintentos", type=int, default=3,
                        help="Reintentos ante 429/5xx (default 3).")
    parser.add_argument("--canal", default="chrome",
                        help="Canal Playwright: chrome | msedge | chromium.")
    parser.add_argument("--headed", action="store_true",
                        help="Muestra el navegador.")

    return parser.parse_args()


def main() -> int:
    argumentos = parsear_argumentos()

    try:
        import playwright  # noqa: F401
    except ImportError:
        print("Falta Playwright:")
        print("  python -m pip install playwright")
        print("  playwright install chromium")
        return 2

    if argumentos.intervalo < 1.5:
        print("El intervalo mínimo de esta sonda es 1.5s. Se fuerza a 1.5s.")
        argumentos.intervalo = 1.5

    codigo = asyncio.run(correr(argumentos))

    destino = DIR_RAW / f"v2_informe_{MARCA}.txt"

    try:
        DIR_RAW.mkdir(parents=True, exist_ok=True)
        destino.write_text("\n".join(INFORME) + "\n", encoding="utf-8")
        print(f"\nInforme guardado en: {destino}")
    except OSError as exc:
        print(f"\nNo se pudo guardar el informe: {exc}")

    return codigo


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.")
        sys.exit(130)
