#!/usr/bin/env python3
"""
v1 — SONDA DE BI-PRECIO (precio escalado por cantidad) — MAKRO PERÚ / VTEX
==========================================================================

PREGUNTA QUE RESPONDE
---------------------
Makro muestra en la web precio mayorista por tramo de cantidad:

    Papel Toalla ARO Gigante Bolsa 1un    ->  1 un S/ 11.90  |  2 un S/ 11.50 x un
    Papel Toalla ARO Profesional Paquete  ->  1 un S/  7.90  |  2 un S/  7.60 x un

El motor de producción NO extrae ese tramo. Un diagnóstico previo sobre la
evidencia archivada estableció que el tramo NO viaja en las respuestas de
MEDICIÓN (simulation / orderForm) — verificado anclando por valor en 18
rutas del JSON. Pero ese diagnóstico NO tenía ni una sola respuesta de
DESCUBRIMIENTO (catálogo) archivada: la hipótesis "el tramo viaja en el
catálogo" quedó SIN PROBAR, no refutada.

Además, el único test de cantidad previo se hizo sobre el SKU 10051346
(Súper Banco POLINPLAST), que NO tiene bi-precio — o sea que su resultado
plano no dice nada sobre esta pregunta.

Esta sonda mide, por primera vez, productos que SÍ tienen bi-precio.

QUÉ HACE (y en qué orden)
-------------------------
  1. Resuelve los dos productos objetivo por el MISMO endpoint de catálogo
     que usa el motor en su fase de descubrimiento
     (/api/catalog_system/pub/products/search) y archiva la respuesta
     CRUDA COMPLETA. Es la primera respuesta de descubrimiento archivada
     en el proyecto.
  2. Rastrea POR VALOR los números 11.50 / 1150 / 11.90 / 1190 / 7.60 /
     760 / 7.90 / 790 dentro de esa respuesta y reporta la RUTA EXACTA de
     cada aparición. No se adivinan nombres de campo: se persigue el número.
  3. Mide ambos SKUs con `simulation` en qty = 1, 2, 3, 4 contra el nodo
     359 (Santa Anita, postal 150137) y archiva cada respuesta cruda.
  4. Reporta, por respuesta: precio total, unitario derivado, teasers,
     priceTags y priceDefinition.sellingPrices[] completo.

DISCIPLINA
----------
  · Toda respuesta se ARCHIVA EN CRUDO ANTES de parsear nada.
  · Tope duro de 12 requests, intervalo mínimo 1.5s: el servidor no es
    nuestro (ver docstring del motor, "RESPETO AL SERVIDOR").
  · Reutiliza `Cliente` del motor: el rate limiting, el backoff y el tope
    son los del motor, no una copia.
  · SOLO escribe archivos nuevos. No toca engines/, ni los CSV, ni nada
    preexistente en salida/.
  · La ambigüedad se reporta como ambigüedad. Lo que la respuesta no dice,
    esta sonda no lo infiere.

USO
---
    python3 test/v1.py
    python3 test/v1.py --intervalo 2.0 --canal chromium
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


# ===========================================================================
# CARGA DEL MOTOR (reuso, no duplicación)
# ===========================================================================

def raiz_repo() -> Path:
    """Raíz del repo por marca (pyproject.toml), no por contar niveles."""

    p = Path(__file__).resolve()
    for padre in p.parents:
        if (padre / "pyproject.toml").exists():
            return padre
    raise SystemExit("No encuentro la raíz del repo")


RAIZ = raiz_repo()
MOTOR_PY = RAIZ / "engines" / "mk_scraping_engine_0.1.0.py"


def cargar_motor():
    """
    Importa el motor de producción como módulo.

    El nombre del archivo lleva la versión (`mk_scraping_engine_0.1.0.py`),
    que no es un identificador Python válido, así que hay que cargarlo por
    ruta. El motor no ejecuta nada al importarse: su `main()` está detrás
    de `if __name__ == "__main__"`.
    """

    if not MOTOR_PY.exists():
        raise SystemExit(f"No encuentro el motor en {MOTOR_PY}")

    spec = importlib.util.spec_from_file_location("mk_engine", MOTOR_PY)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["mk_engine"] = modulo
    spec.loader.exec_module(modulo)

    return modulo


MK = cargar_motor()


# ===========================================================================
# PARÁMETROS DE LA SONDA
# ===========================================================================

NODO = MK.NODOS["359"]          # Santa Anita, postal 150137
CANTIDADES = [1, 2, 3, 4]
TOPE_REQUESTS = 12

# Los números del enunciado, en soles y en centavos. Se rastrean POR VALOR.
VALORES_BUSCADOS = [11.50, 1150, 11.90, 1190, 7.60, 760, 7.90, 790]

# Claves cuyo nombre huele a tramo/escala/mayorista. Se listan aparte del
# rastreo por valor: si el precio de hoy cambió respecto al del enunciado,
# el rastreo por valor no encuentra nada y esto sigue sirviendo.
CLAVES_SOSPECHOSAS = re.compile(
    r"tier|scale|escal|bulk|wholesale|mayor|pricetable|price_table|"
    r"quantity|minqty|min_qty|teaser|pricetag|sellingprices|discount|"
    r"promotion|benefit|unitmultiplier",
    re.IGNORECASE,
)

CLAVES_PRECIO = re.compile(r"price|precio|valor|value|total", re.IGNORECASE)

# Búsqueda de catálogo. La primaria intenta traer los dos productos de una
# sola request; los fallbacks solo se disparan si alguno no apareció.
BUSQUEDA_PRIMARIA = "papel toalla aro"

OBJETIVOS = [
    {
        "clave": "gigante",
        "descripcion": "Papel Toalla ARO Gigante Bolsa 1un",
        "requeridos": ["papel", "toalla", "aro", "gigante"],
        "ft_fallback": "papel toalla aro gigante",
        "esperado_qty1": 11.90,
        "esperado_qty2": 11.50,
    },
    {
        "clave": "profesional",
        "descripcion": "Papel Toalla ARO Profesional Paquete",
        "requeridos": ["papel", "toalla", "aro", "profesional"],
        "ft_fallback": "papel toalla aro profesional",
        "esperado_qty1": 7.90,
        "esperado_qty2": 7.60,
    },
]


# ===========================================================================
# INFORME (se imprime y se guarda; nadie debería tener que abrir un JSON)
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


def normalizar(texto: str) -> str:
    """Minúsculas sin acentos, para comparar nombres de producto."""

    limpio = unicodedata.normalize("NFKD", texto or "")
    limpio = "".join(c for c in limpio if not unicodedata.combining(c))

    return limpio.lower()


def soles(centavos: Any) -> str:
    try:
        return f"S/ {int(centavos) / 100:.2f}"
    except (TypeError, ValueError):
        return "-"


# ===========================================================================
# RASTREO POR VALOR Y POR CLAVE
# ===========================================================================


def caminar(objeto: Any, ruta: str = "") -> Iterator[tuple[str, Any]]:
    """
    Recorre el JSON entero emitiendo (ruta, valor) para cada hoja.

    La ruta se construye con índices reales
    (`[3].items[0].sellers[0].commertialOffer.Price`) para que se pueda
    volver al JSON archivado y verificar a mano.
    """

    if isinstance(objeto, dict):
        for clave, valor in objeto.items():
            yield from caminar(valor, f"{ruta}.{clave}" if ruta else str(clave))

    elif isinstance(objeto, list):
        for indice, valor in enumerate(objeto):
            yield from caminar(valor, f"{ruta}[{indice}]")

    else:
        yield ruta, objeto


def como_numero(valor: Any) -> float | None:
    """Convierte a float lo que sea comparable como número. None si no lo es."""

    if isinstance(valor, bool):
        return None

    if isinstance(valor, (int, float)):
        return float(valor)

    if isinstance(valor, str):
        try:
            return float(valor.strip().replace(",", "."))
        except ValueError:
            return None

    return None


def rastrear_valores(
    objeto: Any,
    valores: list[float],
    prefijo: str = "",
) -> dict[float, list[tuple[str, Any]]]:
    """Devuelve, por cada valor buscado, todas las rutas donde aparece."""

    hallazgos: dict[float, list[tuple[str, Any]]] = {v: [] for v in valores}

    for ruta, valor in caminar(objeto, prefijo):
        numero = como_numero(valor)

        if numero is None:
            continue

        for buscado in valores:
            if abs(numero - buscado) < 1e-6:
                hallazgos[buscado].append((ruta, valor))

    return hallazgos


def rastrear_claves(objeto: Any, patron: re.Pattern, prefijo: str = "") -> list[tuple[str, Any]]:
    """Rutas cuyo NOMBRE de clave matchea el patrón (con su valor)."""

    encontrados = []

    for ruta, valor in caminar(objeto, prefijo):
        ultimo = ruta.split(".")[-1].split("[")[0]

        if patron.search(ultimo):
            encontrados.append((ruta, valor))

    return encontrados


def ramas_no_vacias(objeto: Any, patron: re.Pattern, prefijo: str = "") -> list[str]:
    """
    Rutas de CONTENEDORES (dict/list) cuyo nombre matchea el patrón,
    anotando si vienen vacíos. Un `priceTable: null` y un `priceTable` con
    tramos adentro son respuestas opuestas a la misma pregunta.
    """

    salida: list[str] = []

    def recorrer(nodo: Any, ruta: str) -> None:
        if isinstance(nodo, dict):
            for clave, valor in nodo.items():
                sub = f"{ruta}.{clave}" if ruta else str(clave)

                if patron.search(clave):
                    if isinstance(valor, (dict, list)):
                        estado = "VACÍO" if not valor else f"{len(valor)} elemento(s)"
                        salida.append(f"{sub}  ->  {estado}")
                    else:
                        salida.append(f"{sub}  ->  {json.dumps(valor, ensure_ascii=False)}")

                recorrer(valor, sub)

        elif isinstance(nodo, list):
            for indice, valor in enumerate(nodo):
                recorrer(valor, f"{ruta}[{indice}]")

    recorrer(objeto, prefijo)

    return salida


# ===========================================================================
# ARCHIVADO CRUDO (siempre ANTES de parsear)
# ===========================================================================

MARCA = datetime.now().strftime("%Y%m%d_%H%M%S")
DIR_RAW = RAIZ / "data" / MK.MOTOR / "raw" / datetime.now().strftime("%Y-%m-%d")

ARCHIVADOS: list[Path] = []


def archivar(nombre: str, contenido: Any) -> Path:
    """
    Escribe la respuesta cruda a disco y devuelve la ruta.

    Se llama ANTES de mirar el contenido. Si el parseo de más abajo tiene
    un bug, la evidencia ya está en disco y se puede reprocesar sin gastar
    una sola request más — misma lógica que `guardar_evidencia` del motor.
    """

    DIR_RAW.mkdir(parents=True, exist_ok=True)

    destino = DIR_RAW / nombre

    destino.write_text(
        json.dumps(contenido, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    ARCHIVADOS.append(destino)

    return destino


# ===========================================================================
# FASE 1 — CATÁLOGO
# ===========================================================================


async def buscar_en_catalogo(cliente, termino: str, etiqueta: str) -> tuple[int, Any, Path]:
    """
    Una búsqueda de texto completo por el endpoint de descubrimiento del
    motor. Devuelve (status, datos, ruta del archivo crudo).
    """

    from urllib.parse import quote

    url = (
        f"{MK.BASE_URL}/api/catalog_system/pub/products/search"
        f"?ft={quote(termino)}&_from=0&_to=49&sc={MK.SALES_CHANNEL}"
    )

    status, datos, cabeceras = await cliente.pedir(url)

    # ARCHIVAR PRIMERO. Nada de parseo antes de esta línea.
    nombre = (
        f"v1_catalogo_{MARCA}.json"
        if etiqueta == "primaria"
        else f"v1_catalogo_{etiqueta}_{MARCA}.json"
    )

    ruta = archivar(
        nombre,
        {
            "sonda": "v1_bi_precio",
            "fase": "descubrimiento",
            "termino": termino,
            "url": url,
            "http_status": status,
            "resources": cabeceras.get("resources", ""),
            "momento": datetime.now().isoformat(timespec="seconds"),
            "respuesta": datos,
        },
    )

    return status, datos, ruta


def elegir_candidatos(datos: Any, objetivo: dict) -> list[tuple[int, dict]]:
    """Productos del catálogo cuyo nombre contiene todas las palabras requeridas."""

    if not isinstance(datos, list):
        return []

    elegidos = []

    for indice, crudo in enumerate(datos):
        if not isinstance(crudo, dict):
            continue

        nombre = normalizar(MK.s(crudo.get("productName")))

        if all(palabra in nombre for palabra in objetivo["requeridos"]):
            elegidos.append((indice, crudo))

    return elegidos


def resumir_oferta(crudo: dict) -> dict[str, Any]:
    """Campos de precio del primer item/seller, tal como vienen."""

    items = crudo.get("items") or []
    item = items[0] if items else {}
    vendedores = item.get("sellers") or []
    oferta = (vendedores[0].get("commertialOffer") or {}) if vendedores else {}

    return {
        "product_id": MK.s(crudo.get("productId")),
        "sku_id": MK.s(item.get("itemId")),
        "product_name": MK.s(crudo.get("productName")),
        "brand": MK.s(crudo.get("brand")),
        "seller_id": MK.s(vendedores[0].get("sellerId")) if vendedores else "1",
        "measurement_unit": MK.s(item.get("measurementUnit")),
        "unit_multiplier": item.get("unitMultiplier"),
        "oferta": oferta,
    }


# ===========================================================================
# FASE 2 — MEDICIÓN CON simulation A DISTINTAS CANTIDADES
# ===========================================================================


async def simular(cliente, sku_id: str, seller: str, cantidad: int) -> tuple[int, Any, Path]:
    """
    `simulation` con quantity = N.

    Mismo endpoint y mismo cuerpo que `consultar_simulation` del motor; lo
    único que cambia es `quantity`, que allá está fijo en 1. La simulación
    no guarda estado (la geolocalización viaja en el body), así que las
    cuatro cantidades se pueden pedir sobre el mismo contexto sin
    contaminarse entre sí.
    """

    cuerpo = {
        "items": [
            {
                "id": int(sku_id),
                "quantity": cantidad,
                "seller": seller or "1",
            }
        ],
        "country": NODO.country,
        "postalCode": NODO.postal_code,
        "geoCoordinates": [NODO.longitude, NODO.latitude],
    }

    status, datos, _ = await cliente.pedir(
        f"{MK.BASE_URL}/api/checkout/pub/orderforms/simulation?sc={MK.SALES_CHANNEL}",
        metodo="POST",
        body=cuerpo,
    )

    # ARCHIVAR PRIMERO.
    ruta = archivar(
        f"v1_sim_{sku_id}_qty{cantidad}_{MARCA}.json",
        {
            "sonda": "v1_bi_precio",
            "fase": "medicion",
            "sku": sku_id,
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


def leer_medicion(datos: Any, sku_id: str, cantidad: int) -> dict[str, Any]:
    """Extrae de la respuesta de simulation lo que hay que reportar."""

    lectura: dict[str, Any] = {
        "item_encontrado": False,
        "item_id": "",
        "availability": "",
        "price": None,
        "list_price": None,
        "selling_price": None,
        "price_definition": None,
        "selling_prices": [],
        "price_tags": [],
        "teasers": [],
        "total_items_cent": None,
        "unitario_cent": None,
    }

    if not isinstance(datos, dict):
        return lectura

    for item in datos.get("items") or []:
        if not isinstance(item, dict):
            continue

        if MK.s(item.get("id")) != sku_id:
            continue

        definicion = item.get("priceDefinition") or {}

        lectura.update(
            {
                "item_encontrado": True,
                "item_id": MK.s(item.get("id")),
                "availability": MK.s(item.get("availability")),
                "price": item.get("price"),
                "list_price": item.get("listPrice"),
                "selling_price": item.get("sellingPrice"),
                "price_definition": definicion,
                "selling_prices": definicion.get("sellingPrices") or [],
                "price_tags": item.get("priceTags") or [],
                "quantity_devuelta": item.get("quantity"),
                "unit_multiplier": item.get("unitMultiplier"),
            }
        )

        # El total del item según VTEX. `priceDefinition.total` es el
        # importe de la línea completa; si falta, se cae al totalizador.
        total = definicion.get("total")

        if total is None:
            for totalizador in datos.get("totals") or []:
                if isinstance(totalizador, dict) and totalizador.get("id") == "Items":
                    total = totalizador.get("value")
                    break

        lectura["total_items_cent"] = total

        if isinstance(total, (int, float)) and cantidad:
            lectura["unitario_cent"] = total / cantidad

        break

    beneficios = datos.get("ratesAndBenefitsData") or {}

    if isinstance(beneficios, dict):
        lectura["teasers"] = beneficios.get("teaser") or []
        lectura["identificadores"] = beneficios.get("rateAndBenefitsIdentifiers") or []

    return lectura


# ===========================================================================
# ORQUESTACIÓN
# ===========================================================================


async def correr(argumentos) -> int:
    from playwright.async_api import async_playwright

    titulo("SONDA v1 — BI-PRECIO (PRECIO ESCALADO POR CANTIDAD) — MAKRO PERÚ")
    r(f"Momento    : {datetime.now().isoformat(timespec='seconds')}")
    r(f"Motor leído: {MOTOR_PY.name}  v{MK.VERSION}")
    r(f"Nodo       : {NODO.node_id} {NODO.branch} · postal {NODO.postal_code} · sc={MK.SALES_CHANNEL}")
    r(f"Cantidades : {CANTIDADES}")
    r(f"Tope       : {TOPE_REQUESTS} requests · intervalo {argumentos.intervalo}s")
    r(f"Evidencia  : {DIR_RAW}")
    r("")
    r("Esta sonda SOLO escribe archivos nuevos: no toca engines/, ni los CSV,")
    r("ni nada preexistente en salida/.")

    catalogo_bruto: list[dict] = []
    resueltos: dict[str, dict] = {}
    no_resueltos: list[str] = []
    mediciones: dict[str, dict[int, dict]] = {}
    respuestas_sim: dict[str, dict[int, Any]] = {}
    catalogo_por_objetivo: dict[str, dict] = {}

    async with async_playwright() as playwright:
        navegador = await MK.abrir_navegador(playwright, argumentos.canal, not argumentos.headed)

        try:
            contexto = await navegador.new_context(
                locale="es-PE",
                timezone_id="America/Lima",
                viewport={"width": 1440, "height": 900},
            )

            # Cliente del motor: rate limiting, backoff y tope duro heredados.
            cliente = MK.Cliente(
                contexto.request,
                intervalo=argumentos.intervalo,
                reintentos=argumentos.reintentos,
                tope=TOPE_REQUESTS,
            )
            cliente.fase = "descubrimiento"

            # ---------------- FASE 1: CATÁLOGO ----------------
            titulo("FASE 1 — CATÁLOGO (endpoint de descubrimiento del motor)")

            status, datos, ruta = await buscar_en_catalogo(
                cliente, BUSQUEDA_PRIMARIA, "primaria"
            )

            r(f"ft='{BUSQUEDA_PRIMARIA}' -> HTTP {status} · "
              f"{len(datos) if isinstance(datos, list) else 'no-lista'} productos")
            r(f"  crudo: {ruta}")

            if status >= 400 or not isinstance(datos, list):
                r("")
                r("El catálogo respondió algo que no es una lista de productos.")
                r("Puede ser que el endpoint exija cookies de sesión: se siembra")
                r("la home una vez y se reintenta (1 request extra).")

                try:
                    pagina = await contexto.new_page()
                    await pagina.goto(MK.BASE_URL, wait_until="domcontentloaded", timeout=45000)
                    await pagina.wait_for_timeout(1200)
                    await pagina.close()
                except Exception as exc:
                    r(f"  no se pudo sembrar la home: {type(exc).__name__}")

                status, datos, ruta = await buscar_en_catalogo(
                    cliente, BUSQUEDA_PRIMARIA, "reintento"
                )

                r(f"reintento -> HTTP {status} · "
                  f"{len(datos) if isinstance(datos, list) else 'no-lista'} productos")
                r(f"  crudo: {ruta}")

            if isinstance(datos, list):
                catalogo_bruto = datos

            for objetivo in OBJETIVOS:
                candidatos = elegir_candidatos(catalogo_bruto, objetivo)

                # Fallback: una búsqueda más específica, solo si hizo falta.
                if not candidatos and cliente.contador + len(OBJETIVOS) * len(CANTIDADES) < TOPE_REQUESTS:
                    status2, datos2, ruta2 = await buscar_en_catalogo(
                        cliente, objetivo["ft_fallback"], objetivo["clave"]
                    )

                    r(f"ft='{objetivo['ft_fallback']}' -> HTTP {status2} · "
                      f"{len(datos2) if isinstance(datos2, list) else 'no-lista'} productos")
                    r(f"  crudo: {ruta2}")

                    if isinstance(datos2, list):
                        candidatos = elegir_candidatos(datos2, objetivo)

                        if candidatos:
                            # El rastreo por valor se hace sobre la respuesta
                            # donde el producto realmente apareció.
                            catalogo_por_objetivo[objetivo["clave"]] = {
                                "datos": datos2,
                                "archivo": ruta2,
                            }

                r("")
                r(f"OBJETIVO '{objetivo['clave']}' — {objetivo['descripcion']}")

                if not candidatos:
                    no_resueltos.append(objetivo["clave"])
                    r("  NO RESUELTO: ningún producto del catálogo tiene todas las")
                    r(f"  palabras {objetivo['requeridos']} en su nombre.")
                    continue

                if objetivo["clave"] not in catalogo_por_objetivo:
                    catalogo_por_objetivo[objetivo["clave"]] = {
                        "datos": catalogo_bruto,
                        "archivo": ruta,
                    }

                r(f"  candidatos: {len(candidatos)}")

                for indice, crudo in candidatos:
                    resumen = resumir_oferta(crudo)
                    oferta = resumen["oferta"]
                    r(f"    [{indice}] sku={resumen['sku_id']:<10} "
                      f"Price={soles(oferta.get('Price') and oferta.get('Price') * 100)} "
                      f"ListPrice={soles(oferta.get('ListPrice') and oferta.get('ListPrice') * 100)} "
                      f"| {resumen['product_name'][:60]}")

                indice, crudo = candidatos[0]
                resumen = resumir_oferta(crudo)
                resumen["indice_en_respuesta"] = indice
                resumen["crudo"] = crudo
                resueltos[objetivo["clave"]] = resumen

                if len(candidatos) > 1:
                    r("  AMBIGÜEDAD: hay más de un candidato. Se mide el primero;")
                    r("  los demás quedan listados arriba y en el JSON archivado.")

                r(f"  elegido: sku {resumen['sku_id']} (productId {resumen['product_id']}) "
                  f"seller {resumen['seller_id']}")

            # ---------------- FASE 2: MEDICIÓN ----------------
            titulo("FASE 2 — simulation A qty 1/2/3/4 CONTRA EL NODO 359")

            cliente.fase = "medicion"

            for clave, resumen in resueltos.items():
                mediciones[clave] = {}
                respuestas_sim[clave] = {}

                r("")
                r(f"SKU {resumen['sku_id']} — {resumen['product_name'][:70]}")

                for cantidad in CANTIDADES:
                    if cliente.contador >= TOPE_REQUESTS:
                        r(f"  qty {cantidad}: SIN PRESUPUESTO (tope de "
                          f"{TOPE_REQUESTS} requests alcanzado) — no medido")
                        continue

                    try:
                        status, datos_sim, ruta_sim = await simular(
                            cliente, resumen["sku_id"], resumen["seller_id"], cantidad
                        )
                    except MK.TopeAgotadoError as exc:
                        r(f"  qty {cantidad}: tope agotado ({exc})")
                        break
                    except Exception as exc:
                        r(f"  qty {cantidad}: ERROR {type(exc).__name__}: {str(exc)[:90]}")
                        continue

                    lectura = leer_medicion(datos_sim, resumen["sku_id"], cantidad)
                    lectura["http_status"] = status
                    lectura["archivo"] = ruta_sim

                    mediciones[clave][cantidad] = lectura
                    respuestas_sim[clave][cantidad] = datos_sim

                    r(f"  qty {cantidad}: HTTP {status} · "
                      f"total={soles(lectura['total_items_cent'])} · "
                      f"unitario={soles(lectura['unitario_cent'])} · "
                      f"sellingPrice={soles(lectura['selling_price'])} · "
                      f"avail={lectura['availability'] or '-'}")
                    r(f"           crudo: {ruta_sim.name}")

            r("")
            r(f"Requests gastadas: {cliente.contador}/{TOPE_REQUESTS} "
              f"(por fase: {cliente.por_fase}) · reintentos: {cliente.reintentos_usados}")

        finally:
            await navegador.close()

    # ---------------- INFORMES ----------------
    informe_catalogo(catalogo_por_objetivo, resueltos)
    informe_mediciones(resueltos, mediciones, respuestas_sim)
    informe_promocion(catalogo_por_objetivo, respuestas_sim)
    veredicto(resueltos, mediciones, no_resueltos, catalogo_por_objetivo)

    return 0


# ===========================================================================
# INFORME 1 — ¿EL TRAMO ESTÁ EN EL CATÁLOGO?
# ===========================================================================


def informe_catalogo(catalogo_por_objetivo: dict, resueltos: dict) -> None:
    titulo("PREGUNTA 1 — ¿EL TRAMO POR CANTIDAD VIAJA EN LA RESPUESTA DE CATÁLOGO?")

    if not catalogo_por_objetivo:
        r("No hubo respuesta de catálogo utilizable. Sin evidencia.")
        return

    for clave, paquete in catalogo_por_objetivo.items():
        datos = paquete["datos"]

        r("")
        r("-" * 100)
        r(f"RESPUESTA DE CATÁLOGO donde apareció '{clave}'  ({paquete['archivo'].name})")
        r("-" * 100)

        r("")
        r("a) RASTREO POR VALOR en la respuesta COMPLETA (11.50/1150/11.90/1190/7.60/760/7.90/790):")

        hallazgos = rastrear_valores(datos, VALORES_BUSCADOS)
        hubo = False

        for buscado in VALORES_BUSCADOS:
            rutas = hallazgos[buscado]

            if not rutas:
                r(f"   {buscado:>8}  -> NO APARECE")
                continue

            hubo = True
            r(f"   {buscado:>8}  -> {len(rutas)} aparición(es):")

            for ruta, valor in rutas[:12]:
                r(f"               {ruta} = {valor!r}")

            if len(rutas) > 12:
                r(f"               ... y {len(rutas) - 12} más (ver JSON archivado)")

        if not hubo:
            r("   Ninguno de los ocho números aparece en la respuesta de catálogo.")
            r("   OJO: eso puede significar que el tramo no viaja, o que el precio")
            r("   de hoy ya no es el del enunciado. Ver el bloque (b) y (c).")

        # Subárbol del producto elegido: precios reales de hoy.
        resumen = resueltos.get(clave)

        if resumen:
            r("")
            r(f"b) commertialOffer COMPLETO del SKU elegido ({resumen['sku_id']}), "
              "ruta real en la respuesta:")

            prefijo = (
                f"[{resumen['indice_en_respuesta']}].items[0].sellers[0].commertialOffer"
            )

            oferta = resumen["oferta"]

            for campo in sorted(oferta.keys()):
                valor = oferta[campo]

                if isinstance(valor, (dict, list)):
                    estado = "VACÍO" if not valor else f"{len(valor)} elemento(s)"
                    r(f"   {prefijo}.{campo} = <{type(valor).__name__} {estado}>")
                else:
                    r(f"   {prefijo}.{campo} = {json.dumps(valor, ensure_ascii=False)}")

            r("")
            r(f"   items[0].measurementUnit = {resumen['measurement_unit']!r} · "
              f"unitMultiplier = {resumen['unit_multiplier']!r}")

            r("")
            r("c) CLAVES SOSPECHOSAS (tier/scale/priceTable/quantity/teaser/discount/…) "
              "dentro del producto elegido:")

            ramas = ramas_no_vacias(resumen["crudo"], CLAVES_SOSPECHOSAS, prefijo=f"[{resumen['indice_en_respuesta']}]")

            if not ramas:
                r("   Ninguna. El producto no trae ningún contenedor con nombre de tramo.")
            else:
                for linea in ramas[:40]:
                    r(f"   {linea}")

                if len(ramas) > 40:
                    r(f"   ... y {len(ramas) - 40} más")

            r("")
            r("d) TODOS los campos numéricos del producto elegido cuyo nombre "
              "suena a precio (por si el tramo viaja con otro nombre):")

            precios = [
                (ruta, valor)
                for ruta, valor in rastrear_claves(
                    resumen["crudo"], CLAVES_PRECIO, prefijo=f"[{resumen['indice_en_respuesta']}]"
                )
                if como_numero(valor) is not None
            ]

            vistos = set()

            for ruta, valor in precios:
                if (ruta, str(valor)) in vistos:
                    continue

                vistos.add((ruta, str(valor)))
                r(f"   {ruta} = {valor!r}")

            if not precios:
                r("   Ninguno.")


# ===========================================================================
# INFORME 2 — MEDICIONES
# ===========================================================================


def informe_mediciones(resueltos: dict, mediciones: dict, respuestas_sim: dict) -> None:
    titulo("PREGUNTA 2/3/5 — MEDICIONES simulation POR CANTIDAD (DETALLE COMPLETO)")

    if not mediciones:
        r("No se midió nada.")
        return

    for clave, por_cantidad in mediciones.items():
        resumen = resueltos[clave]

        r("")
        r("-" * 100)
        r(f"SKU {resumen['sku_id']} — {resumen['product_name']}")
        r("-" * 100)

        for cantidad in CANTIDADES:
            lectura = por_cantidad.get(cantidad)

            r("")
            r(f"  ── qty {cantidad} ──  ({lectura['archivo'].name if lectura else 'NO MEDIDO'})")

            if not lectura:
                r("     no medido")
                continue

            if not lectura["item_encontrado"]:
                r(f"     HTTP {lectura['http_status']} pero el item no volvió en la respuesta.")
                continue

            r(f"     availability          : {lectura['availability']}")
            r(f"     quantity devuelta     : {lectura.get('quantity_devuelta')}")
            r(f"     price (unitario cent) : {lectura['price']}  ({soles(lectura['price'])})")
            r(f"     listPrice             : {lectura['list_price']}  ({soles(lectura['list_price'])})")
            r(f"     sellingPrice          : {lectura['selling_price']}  ({soles(lectura['selling_price'])})")
            r(f"     priceDefinition.total : {lectura['total_items_cent']}  "
              f"({soles(lectura['total_items_cent'])})")
            r(f"     UNITARIO DERIVADO     : total/{cantidad} = "
              f"{soles(lectura['unitario_cent'])}")

            r("     priceDefinition.sellingPrices[] COMPLETO:")

            if not lectura["selling_prices"]:
                r("       (vacío)")
            else:
                for entrada in lectura["selling_prices"]:
                    r(f"       {json.dumps(entrada, ensure_ascii=False)}")

            definicion = lectura["price_definition"] or {}
            r(f"     priceDefinition.reason: {definicion.get('reason')!r} · "
              f"calculatedSellingPrice: {definicion.get('calculatedSellingPrice')}")

            r("     priceTags[]:")

            if not lectura["price_tags"]:
                r("       (vacío)")
            else:
                for etiqueta in lectura["price_tags"]:
                    r(f"       {json.dumps(etiqueta, ensure_ascii=False)}")

            r("     ratesAndBenefitsData.teaser[]:")

            if not lectura["teasers"]:
                r("       (vacío)")
            else:
                for teaser in lectura["teasers"]:
                    nombre = teaser.get("name") if isinstance(teaser, dict) else teaser
                    r(f"       name = {nombre!r}")
                    r(f"       {json.dumps(teaser, ensure_ascii=False)[:900]}")

            identificadores = lectura.get("identificadores") or []

            if identificadores:
                r("     rateAndBenefitsIdentifiers[]:")

                for ident in identificadores:
                    r(f"       {json.dumps(ident, ensure_ascii=False)[:400]}")

        # Rastreo por valor sobre las respuestas de medición.
        r("")
        r("  RASTREO POR VALOR en las respuestas de simulation de este SKU:")

        for cantidad in CANTIDADES:
            datos = respuestas_sim.get(clave, {}).get(cantidad)

            if datos is None:
                continue

            hallazgos = rastrear_valores(datos, VALORES_BUSCADOS)
            encontrados = {v: rutas for v, rutas in hallazgos.items() if rutas}

            if not encontrados:
                r(f"    qty {cantidad}: ninguno de los ocho números aparece.")
                continue

            for valor, rutas in encontrados.items():
                muestras = ", ".join(ruta for ruta, _ in rutas[:6])
                r(f"    qty {cantidad}: {valor} en {len(rutas)} ruta(s) -> {muestras}"
                  + (" ..." if len(rutas) > 6 else ""))

        # Serie de unitarios.
        r("")
        r("  SERIE DE PRECIO UNITARIO (total/qty):")

        for cantidad in CANTIDADES:
            lectura = por_cantidad.get(cantidad)

            if not lectura or lectura["unitario_cent"] is None:
                r(f"    qty {cantidad}: -")
            else:
                r(f"    qty {cantidad}: {soles(lectura['unitario_cent'])}")


# ===========================================================================
# INFORME 3 — LA PROMOCIÓN NOMBRADA EN EL BACKEND
# ===========================================================================


def informe_promocion(catalogo_por_objetivo: dict, respuestas_sim: dict) -> None:
    titulo("PREGUNTA 4 — 'MAKRO-Bi-Precio' Y PromotionalPriceTableItemsDiscount")

    patron = re.compile(r"PromotionalPriceTable|Bi-Precio|BiPrecio|priceTable", re.IGNORECASE)

    def escanear(objeto: Any, etiqueta: str) -> int:
        golpes = 0

        for ruta, valor in caminar(objeto):
            texto_ruta = ruta
            texto_valor = valor if isinstance(valor, str) else ""

            if patron.search(texto_ruta) or (texto_valor and patron.search(texto_valor)):
                golpes += 1

                if golpes <= 25:
                    r(f"   [{etiqueta}] {ruta} = {json.dumps(valor, ensure_ascii=False)[:220]}")

        if golpes > 25:
            r(f"   [{etiqueta}] ... y {golpes - 25} apariciones más")

        if golpes == 0:
            r(f"   [{etiqueta}] sin apariciones")

        return golpes

    r("")
    r("Apariciones en CATÁLOGO:")

    for clave, paquete in catalogo_por_objetivo.items():
        escanear(paquete["datos"], f"catalogo/{clave}")

    r("")
    r("Apariciones en MEDICIÓN (simulation):")

    for clave, por_cantidad in respuestas_sim.items():
        for cantidad, datos in por_cantidad.items():
            escanear(datos, f"sim/{clave}/qty{cantidad}")

    r("")
    r("Lectura: el valor de PromotionalPriceTableItemsDiscount solo puede")
    r("interpretarse como UMBRAL DE CANTIDAD o como MONTO/PORCENTAJE DE")
    r("DESCUENTO si el propio JSON lo dice (nombre del parámetro, unidad, o")
    r("coincidencia con la aritmética observada). Si arriba no hay ninguna")
    r("aparición, esta corrida NO tiene evidencia para decidirlo y así se")
    r("reporta: ambiguo.")


# ===========================================================================
# VEREDICTO
# ===========================================================================


def derivar_tramo_catalogo(resumen: dict) -> dict[str, Any] | None:
    """
    Reconstruye el tramo mayorista a partir de la respuesta de CATÁLOGO.

    El catálogo no trae el número mayorista literal (no hay un "11.50" en
    ninguna ruta). Trae sus tres ingredientes, y eso alcanza:

        Price                                                -> 11.90
        specification CantidadBiPrecioMK                      -> "2"   (umbral)
        Teasers[].Effects.Parameters.PromotionalPriceTableItemsDiscount
                                                             -> "0.40" (descuento POR UNIDAD, en soles)

        precio mayorista = Price - descuento  a partir de CantidadBiPrecioMK unidades

    Buscar solo por valor literal contesta que "no está" y esa respuesta
    es engañosa: el dato está, en forma derivable y sin un request extra.
    Por eso el veredicto usa esta reconstrucción y la CONTRASTA contra lo
    medido — si la aritmética no reprodujera el unitario medido, la
    reconstrucción se reporta como fallida, no como confirmada.
    """

    crudo = resumen.get("crudo") or {}
    oferta = resumen.get("oferta") or {}
    indice = resumen.get("indice_en_respuesta")

    precio = como_numero(oferta.get("Price"))
    umbral = None
    descuento = None
    rutas: list[str] = []

    cantidades = crudo.get("CantidadBiPrecioMK")

    if isinstance(cantidades, list) and cantidades:
        umbral = como_numero(cantidades[0])
        rutas.append(f"[{indice}].CantidadBiPrecioMK[0] = {cantidades[0]!r}")

    for posicion, teaser in enumerate(oferta.get("PromotionTeasers") or []):
        if not isinstance(teaser, dict):
            continue

        parametros = ((teaser.get("Effects") or {}).get("Parameters")) or []

        for orden, parametro in enumerate(parametros):
            if not isinstance(parametro, dict):
                continue

            if MK.s(parametro.get("Name")) == "PromotionalPriceTableItemsDiscount":
                descuento = como_numero(parametro.get("Value"))
                rutas.append(
                    f"[{indice}].items[0].sellers[0].commertialOffer."
                    f"PromotionTeasers[{posicion}].Effects.Parameters[{orden}].Value "
                    f"= {parametro.get('Value')!r}"
                )

    if precio is None or descuento is None:
        return None

    rutas.insert(
        0,
        f"[{indice}].items[0].sellers[0].commertialOffer.Price = {oferta.get('Price')!r}",
    )

    return {
        "precio_lista": precio,
        "umbral": umbral,
        "descuento_unitario": descuento,
        "mayorista_derivado": round(precio - descuento, 2),
        "rutas": rutas,
    }


def veredicto(resueltos: dict, mediciones: dict, no_resueltos: list, catalogo_por_objetivo: dict) -> None:
    titulo("VEREDICTO — RESPUESTAS CORTAS")

    if no_resueltos:
        r("")
        r(f"PRODUCTOS NO RESUELTOS: {', '.join(no_resueltos)}")
        r("Para esos objetivos esta corrida no dice nada.")

    # --- P1: ¿el tramo está en el catálogo?
    r("")
    r("P1 · ¿El tramo por cantidad viaja en la respuesta de catálogo?")

    tramo_en_catalogo = False
    rutas_tramo: list[str] = []

    for clave, resumen in resueltos.items():
        por_cantidad = mediciones.get(clave, {})
        unitarios = {
            c: l["unitario_cent"]
            for c, l in por_cantidad.items()
            if l.get("unitario_cent") is not None
        }

        if not unitarios:
            continue

        base = unitarios.get(1)
        mayoristas = {c: v for c, v in unitarios.items() if c > 1 and base and v < base - 0.5}

        paquete = catalogo_por_objetivo.get(clave)

        if not paquete or not mayoristas:
            continue

        # ¿Alguno de los unitarios mayoristas observados aparece en el
        # catálogo, en el subárbol del producto?
        objetivo_valores = []

        for valor in mayoristas.values():
            objetivo_valores.extend([round(valor, 2), round(valor / 100.0, 2)])

        hallazgos = rastrear_valores(
            resumen["crudo"], objetivo_valores, prefijo=f"[{resumen['indice_en_respuesta']}]"
        )

        for valor, rutas in hallazgos.items():
            for ruta, _ in rutas:
                tramo_en_catalogo = True
                rutas_tramo.append(f"{clave}: {ruta} = {valor}")

    # Reconstrucción: el catálogo puede traer los INGREDIENTES del tramo
    # sin traer el número. Se contrasta contra el unitario medido.
    derivados: dict[str, dict] = {}

    for clave, resumen in resueltos.items():
        derivado = derivar_tramo_catalogo(resumen)

        if not derivado:
            continue

        por_cantidad = mediciones.get(clave, {})
        medido = (por_cantidad.get(2) or {}).get("unitario_cent")
        derivado["medido_qty2"] = medido
        derivado["coincide"] = (
            medido is not None
            and abs(derivado["mayorista_derivado"] * 100 - medido) < 1.0
        )
        derivados[clave] = derivado

    coincidentes = [c for c, d in derivados.items() if d["coincide"]]

    if coincidentes:
        r("   SÍ, DE FORMA DERIVABLE — sin un solo request extra por SKU.")
        r("   El catálogo NO trae el número mayorista literal, trae sus")
        r("   ingredientes, y la aritmética reproduce EXACTAMENTE el unitario")
        r("   medido a qty>=umbral:")

        for clave in coincidentes:
            derivado = derivados[clave]
            r("")
            r(f"     {clave}: Price {derivado['precio_lista']:.2f} - descuento "
              f"{derivado['descuento_unitario']:.2f} = "
              f"{derivado['mayorista_derivado']:.2f}  ==  medido a qty2 "
              f"{soles(derivado['medido_qty2'])}  · umbral "
              f"CantidadBiPrecioMK = {derivado['umbral']}")

            for ruta in derivado["rutas"]:
                r(f"       {ruta}")

        no_coinciden = [c for c in derivados if c not in coincidentes]

        if no_coinciden:
            r("")
            r(f"   OJO — la reconstrucción NO reprodujo lo medido en: "
              f"{', '.join(no_coinciden)}. Para esos SKUs el catálogo no")
            r("   basta y así queda reportado.")

    if tramo_en_catalogo:
        r("")
        r("   Además, el valor mayorista aparece LITERAL en el catálogo, en:")

        for linea in rutas_tramo[:20]:
            r(f"     {linea}")
    elif coincidentes:
        pass
    elif not resueltos:
        r("   SIN EVIDENCIA (no se resolvió ningún producto).")
    else:
        r("   NO ESTÁ. Ninguna ruta de la respuesta de catálogo contiene el")
        r("   precio mayorista. Ver el bloque de la PREGUNTA 1 para el rastreo")
        r("   completo por valor y las claves sospechosas (priceTable, teasers,")
        r("   etc.) con su estado real (vacío / con contenido).")

    # --- P2: ¿baja el unitario a qty=2?
    r("")
    r("P2 · ¿A qty=2 el unitario baja?")

    for clave, resumen in resueltos.items():
        por_cantidad = mediciones.get(clave, {})
        u1 = (por_cantidad.get(1) or {}).get("unitario_cent")
        u2 = (por_cantidad.get(2) or {}).get("unitario_cent")

        objetivo = next(o for o in OBJETIVOS if o["clave"] == clave)

        if u1 is None or u2 is None:
            r(f"   {clave} (sku {resumen['sku_id']}): sin datos suficientes.")
            continue

        if u2 < u1 - 0.5:
            r(f"   {clave} (sku {resumen['sku_id']}): SÍ. {soles(u1)} -> {soles(u2)} "
              f"(esperado por la web: S/ {objetivo['esperado_qty1']:.2f} -> "
              f"S/ {objetivo['esperado_qty2']:.2f})")
        else:
            r(f"   {clave} (sku {resumen['sku_id']}): NO. {soles(u1)} -> {soles(u2)} "
              f"(plano; la web anuncia S/ {objetivo['esperado_qty2']:.2f} a 2 un)")

    # --- P3: teaser en qty=1
    r("")
    r("P3 · ¿El teaser aparece ya en qty=1? (si sí, detectar qué SKUs tienen")
    r("     bi-precio sale gratis, sin request extra)")

    for clave, resumen in resueltos.items():
        por_cantidad = mediciones.get(clave, {})

        for cantidad in CANTIDADES:
            lectura = por_cantidad.get(cantidad)

            if not lectura:
                continue

            nombres = [
                MK.s(t.get("name")) for t in lectura["teasers"] if isinstance(t, dict)
            ]
            etiquetas = [
                MK.s(t.get("name")) for t in lectura["price_tags"] if isinstance(t, dict)
            ]

            r(f"   {clave} qty {cantidad}: teasers={nombres or '[]'} · "
              f"priceTags={etiquetas or '[]'}")

    # --- P5: ¿cuántos escalones?
    r("")
    r("P5 · ¿Hay un tercer nivel en qty=3 o 4, o el escalón es único?")

    for clave, resumen in resueltos.items():
        por_cantidad = mediciones.get(clave, {})
        serie = [
            (c, (por_cantidad.get(c) or {}).get("unitario_cent")) for c in CANTIDADES
        ]
        serie_ok = [(c, v) for c, v in serie if v is not None]

        if len(serie_ok) < 2:
            r(f"   {clave}: sin serie suficiente.")
            continue

        niveles = sorted({round(v, 2) for _, v in serie_ok}, reverse=True)
        detalle = " · ".join(f"qty{c}={soles(v)}" for c, v in serie_ok)

        if len(niveles) == 1:
            r(f"   {clave}: UN SOLO NIVEL (precio plano en {len(serie_ok)} cantidades). {detalle}")
        elif len(niveles) == 2:
            r(f"   {clave}: DOS NIVELES — un solo escalón, consistente con 'bi'. {detalle}")
        else:
            r(f"   {clave}: {len(niveles)} NIVELES — hay más de un escalón. {detalle}")

    # --- CASO (a)/(b)/(c)
    r("")
    r("-" * 100)
    r("CASO REAL (criterio de aceptación)")
    r("-" * 100)

    hay_escalon = False
    teaser_en_qty1 = False

    for clave in resueltos:
        por_cantidad = mediciones.get(clave, {})
        u1 = (por_cantidad.get(1) or {}).get("unitario_cent")
        u2 = (por_cantidad.get(2) or {}).get("unitario_cent")

        if u1 is not None and u2 is not None and u2 < u1 - 0.5:
            hay_escalon = True

        lectura1 = por_cantidad.get(1)

        if lectura1 and (lectura1["teasers"] or lectura1["price_tags"]):
            teaser_en_qty1 = True

    if not resueltos:
        r("SIN VEREDICTO: no se resolvió ningún producto objetivo.")
    elif tramo_en_catalogo or coincidentes:
        r("(a) EL TRAMO ESTÁ EN EL CATÁLOGO — se obtiene sin requests")
        r("    adicionales por SKU: la fase de descubrimiento que el motor ya")
        r("    hace lo trae de arriba.")
        if coincidentes and not tramo_en_catalogo:
            r("    No como número literal, sino como Price - "
              "PromotionalPriceTableItemsDiscount")
            r("    a partir de CantidadBiPrecioMK unidades — aritmética")
            r("    verificada contra la medición de esta misma corrida.")
    elif hay_escalon and teaser_en_qty1:
        r("(b) HACE FALTA UN REQUEST A qty=2, pero solo en los SKUs marcados")
        r("    en qty=1: el teaser/priceTag ya visible a qty=1 identifica qué")
        r("    SKUs tienen bi-precio, así que el segundo request se paga solo")
        r("    para esos.")
    elif hay_escalon and not teaser_en_qty1:
        r("(b') HACE FALTA UN REQUEST A qty=2 Y NO HAY MARCA EN qty=1: el")
        r("     escalón existe y se mide, pero nada en la respuesta de qty=1")
        r("     dice qué SKUs lo tienen. Sin una marca previa, detectar")
        r("     bi-precio cuesta un request extra POR SKU, no solo por los")
        r("     marcados. Es una variante de (b), más cara.")
    else:
        r("(c) NO SE OBTIENE POR API con lo probado acá: el catálogo no trae")
        r("    el tramo y `simulation` devuelve precio plano en las cuatro")
        r("    cantidades medidas contra el nodo 359. Lo que la web muestra no")
        r("    aparece en ninguna de las dos respuestas archivadas.")
        r("    ALCANCE de esta afirmación: cubre `simulation` a qty 1-4 y el")
        r("    endpoint de catálogo products/search. NO cubre otros endpoints")
        r("    (p. ej. GraphQL de la tienda) que esta corrida no tocó.")

    r("")
    r("Evidencia cruda archivada:")

    for ruta in ARCHIVADOS:
        r(f"  {ruta}")


# ===========================================================================


def parsear_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sonda v1: cómo se obtiene el bi-precio de Makro por API."
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

    destino = DIR_RAW / f"v1_informe_{MARCA}.txt"

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
