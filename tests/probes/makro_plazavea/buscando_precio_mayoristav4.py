#!/usr/bin/env python3
"""
v4 — PRECIO MAYORISTA MAKRO, VERIFICADO POR SUCURSAL
=====================================================

Reemplaza a `test/v3.py`.

POR QUÉ v3 NO SIRVE
-------------------
v3 barrió solo el endpoint de catálogo. Ese endpoint NO tiene contexto de
sucursal: devuelve el catálogo de la cuenta VTEX, que es de Plaza Vea. Sus
1157 filas salieron con `seller_chain = "1"` y `node_id =
SIN_CONTEXTO_SUCURSAL`: ni una verificada como Makro.

Y el catálogo tienta a creer lo contrario, porque trae
`sellers[0].sellerName = "Makro Plazavea"` sobre `sellerId = 1`. Eso NO es
prueba de nada. La única prueba de que un precio es de Makro Santa Anita es
que `sellerChain` contenga `plazaveamko359` DESPUÉS de medir contra el nodo.

LAS DOS FUENTES, Y POR QUÉ HACEN FALTA LAS DOS
----------------------------------------------
    catálogo    CantidadBiPrecioMK (umbral) + teaser (descuento)   sin sucursal
    simulation  precio verificado + sellerChain + firma logística   sin umbral

Ninguna basta sola: el umbral y el descuento solo existen en el catálogo, y
la verificación de sucursal solo existe en la medición. Se unen por `sku_id`.

    precio_mayorista = precio_verificado - descuento_monto
    unidades_minimas = CantidadBiPrecioMK

El precio que entra en la resta es SIEMPRE el verificado de la fase 2, nunca
el del catálogo. Un solo escalón: `CantidadTriPrecioMK` está declarado en el
catálogo y v2 comprobó que no se aplica en checkout — se ignora.

DISCIPLINA
----------
  · Fase 2 reutiliza el flujo del motor tal cual: `consultar_simulation`,
    `extraer_item`, `extraer_logistica`, `identificar_nodo`. Acá no se
    reimplementa parsing de logística ni de firmas.
  · `PromotionalPriceTableItemsDiscount` se busca POR NOMBRE a cualquier
    profundidad, nunca por índice de array.
  · Todo cálculo monetario en `Decimal`. Ningún float toca un precio.
  · Las filas que NO son surtido Makro se escriben igual, marcadas
    `surtido_makro = NO`. Borrarlas escondería el problema que hundió a v3.
  · Si `biprecio_status` != COMPLETO, `precio_mayorista` y
    `unidades_minimas` van VACÍOS. Nunca cero, nunca el unitario repetido:
    un mayorista igual al unitario es una mentira que se filtra sola en una
    hoja de cálculo.
  · Tres archivos de salida, ninguno crudo. Tope de 40 requests,
    intervalo 1.5s, `Cliente` del motor.

USO
---
    python3 test/v4.py
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import importlib.util
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


# ===========================================================================
# REUSO DEL MOTOR
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
    """El nombre del motor lleva la versión, así que se carga por ruta."""

    if not MOTOR_PY.exists():
        raise SystemExit(f"No encuentro el motor en {MOTOR_PY}")

    spec = importlib.util.spec_from_file_location("mk_engine", MOTOR_PY)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["mk_engine"] = modulo
    spec.loader.exec_module(modulo)

    return modulo


MK = cargar_motor()

NODO = MK.NODOS["359"]        # Santa Anita, postal 150137


# ===========================================================================
# PARÁMETROS
# ===========================================================================

TOPE_REQUESTS = 40
POR_CATEGORIA = 10
PAGINAS_MAX = 2
VENTANA = 50

# Categorías objetivo, por ruta de nombres. Se resuelven contra el árbol
# vivo para sacar el ID: hardcodear el ID sería fijar hoy un número que
# VTEX puede reorganizar mañana sin avisar.
CATEGORIAS_OBJETIVO = [
    ("Abarrotes", "Snacks y Piqueos"),
    ("Bebidas", "Bebidas Funcionales"),
    ("Limpieza", "Cuidado del Hogar"),
]

PARAM_DESCUENTO = "PromotionalPriceTableItemsDiscount"

COLUMNAS = [
    "product_name", "precio_unitario", "precio_mayorista", "unidades_minimas",
    "descuento_pct", "surtido_makro", "seller_chain", "node_id",
    "logistics_status", "price_status", "availability", "category_path",
    "sku_id", "ean", "brand", "list_price", "descuento_monto", "biprecio_status",
]

MARCA = datetime.now().strftime("%Y%m%d_%H%M%S")
SALIDA = RAIZ / "data" / MK.MOTOR

CSV_SALIDA = SALIDA / f"v4_biprecio_{MARCA}.csv"
MD_SALIDA = SALIDA / f"v4_analisis_{MARCA}.md"
MUESTRA_SALIDA = SALIDA / f"v4_muestra_etiquetas_{MARCA}.md"

CERO = Decimal("0")
CENTAVO = Decimal("0.01")


def log(mensaje: str = "") -> None:
    print(mensaje, flush=True)


# ===========================================================================
# DECIMAL
# ===========================================================================


def dec(valor: Any) -> Decimal | None:
    """
    A Decimal pasando por `str`: nunca `Decimal(float)`.

    El JSON llega parseado con los precios como float; `Decimal(13.3)`
    arrastra la basura binaria, `Decimal("13.3")` es lo que el servidor dijo.
    """

    if valor is None or isinstance(valor, bool) or valor == "":
        return None

    try:
        return Decimal(str(valor).strip())
    except (InvalidOperation, ValueError, ArithmeticError):
        return None


def de_centavos(valor: Any) -> Decimal | None:
    """Los precios de `simulation` vienen en centavos enteros."""

    numero = dec(valor)

    return None if numero is None else (numero / 100)


def entero(valor: Any) -> int | None:
    numero = dec(valor)

    if numero is None:
        return None

    try:
        return int(numero) if numero == numero.to_integral_value() else None
    except (InvalidOperation, ArithmeticError):
        return None


def es_centavo_exacto(valor: Decimal | None) -> bool:
    if valor is None:
        return False

    return (valor * 100) == (valor * 100).to_integral_value()


def money(valor: Decimal | None) -> str:
    return "" if valor is None else f"{valor.quantize(CENTAVO)}"


# ===========================================================================
# BÚSQUEDA POR NOMBRE (nunca por índice)
# ===========================================================================


def parametros_por_nombre(objeto: Any, nombre: str) -> list[Any]:
    """
    Busca `{"Name": <nombre>, "Value": X}` a cualquier profundidad.

    Nunca por índice: el orden de los parámetros del teaser lo decide VTEX.
    Anclar en `Parameters[1]` funciona hasta que agregan un parámetro
    adelante, y ese día se lee el ID del producto como si fuera un descuento.
    """

    encontrados: list[Any] = []

    def recorrer(nodo: Any) -> None:
        if isinstance(nodo, dict):
            claves = {
                k.lower().replace("k__backingfield", "").strip("<> "): k
                for k in nodo.keys()
            }

            if "name" in claves and "value" in claves:
                if MK.s(nodo[claves["name"]]) == nombre:
                    encontrados.append(nodo[claves["value"]])

            for valor in nodo.values():
                recorrer(valor)

        elif isinstance(nodo, list):
            for valor in nodo:
                recorrer(valor)

    recorrer(objeto)

    return encontrados


def caminar(objeto: Any, ruta: str = "") -> Any:
    """Recorre el JSON emitiendo (ruta, valor) con índices reales."""

    if isinstance(objeto, dict):
        for clave, valor in objeto.items():
            yield from caminar(valor, f"{ruta}.{clave}" if ruta else str(clave))

    elif isinstance(objeto, list):
        for indice, valor in enumerate(objeto):
            yield from caminar(valor, f"{ruta}[{indice}]")

    else:
        yield ruta, objeto


def rutas_de(objeto: Any, claves: tuple[str, ...], prefijo: str = "",
             tope: int = 6, bajo: str = "", excluir: tuple[str, ...] = ()) -> list[tuple[str, Any]]:
    """
    Rutas COMPLETAS cuya última clave es una de las buscadas.

    `bajo` y `excluir` existen por una razón concreta: `simulation` repite
    los mismos nombres de campo en ramas que NO son el precio del producto.
    `logisticsInfo[0].slas[0].listPrice = 1990` es el costo del envío, y
    puesto en una tabla junto a `items[0].listPrice = 1190` invita
    exactamente al error que este documento existe para prevenir.
    """

    salida: list[tuple[str, Any]] = []
    cuenta: Counter = Counter()

    for ruta, valor in caminar(objeto, prefijo):
        if bajo and not ruta.startswith(bajo):
            continue

        if any(trozo in ruta for trozo in excluir):
            continue

        ultima = ruta.split(".")[-1].split("[")[0]

        if ultima in claves and cuenta[ultima] < tope:
            cuenta[ultima] += 1
            salida.append((ruta, valor))

    return salida


# ===========================================================================
# FASE 1 — CATÁLOGO
# ===========================================================================


def aplanar(arbol: Any, ruta: list[str], ruta_ids: list[str], nivel: int,
            acumulado: list[dict]) -> list[dict]:
    for rama in arbol or []:
        if not isinstance(rama, dict):
            continue

        nombre = MK.s(rama.get("name"))
        id_categoria = MK.s(rama.get("id"))

        if not id_categoria:
            continue

        camino = ruta + [nombre]
        camino_ids = ruta_ids + [id_categoria]

        acumulado.append(
            {
                "id": id_categoria,
                "nombre": nombre,
                "nivel": nivel,
                "ruta": " > ".join(camino),
                "ruta_nombres": tuple(camino),
                "ruta_ids": "/" + "/".join(camino_ids) + "/",
            }
        )

        aplanar(rama.get("children") or [], camino, camino_ids, nivel + 1, acumulado)

    return acumulado


def leer_catalogo(crudo: dict, categoria: dict) -> dict[str, Any] | None:
    """
    Un producto del catálogo -> lo que el catálogo (y solo él) sabe.

    Devuelve None si el producto no tiene SKU legible: sin `sku_id` no hay
    forma de unirlo con la medición, que es de lo que se trata todo esto.
    """

    items = crudo.get("items") or []
    item = items[0] if items else {}
    vendedores = item.get("sellers") or []
    vendedor = vendedores[0] if vendedores else {}
    oferta = vendedor.get("commertialOffer") or {}

    sku = MK.s(item.get("itemId"))

    if not sku:
        return None

    def spec(nombre: str) -> Any:
        valor = crudo.get(nombre)

        if isinstance(valor, list):
            return valor[0] if valor else None

        return valor

    teasers = oferta.get("PromotionTeasers") or []
    descuentos = parametros_por_nombre(teasers, PARAM_DESCUENTO)

    return {
        "sku_id": sku,
        "product_id": MK.s(crudo.get("productId")),
        "seller_id": MK.s(vendedor.get("sellerId")) or "1",
        "product_name": MK.s(crudo.get("productName")),
        "brand": MK.s(crudo.get("brand")),
        "ean": MK.s(item.get("ean")),
        "precio_catalogo": dec(oferta.get("Price")),
        "list_price": dec(oferta.get("ListPrice")),
        "umbral": entero(spec("CantidadBiPrecioMK")),
        "descuento": dec(descuentos[0]) if descuentos else None,
        "category_path": categoria["ruta"],
        "category_id": categoria["id"],
        "crudo": crudo,
    }


# ===========================================================================
# FASE 2 — MEDICIÓN (flujo del motor)
# ===========================================================================


def clasificar(datos: Any, status: int, producto_mk, item: dict,
               logistica: dict, node_resolved: str,
               seller_confirmado: bool) -> tuple[str, str]:
    """
    logistics_status / price_status siguiendo las reglas del motor.

    Es la misma cascada de `construir_fila` (v0.1.0, líneas ~2455-2540),
    recortada a lo que esta sonda mide: qty=1, un nodo, sin auditoría. Las
    etiquetas se mantienen idénticas a propósito — un CSV de esta sonda
    tiene que poder leerse con el mismo diccionario que los del motor.
    """

    if status >= 400:
        return "HTTP_ERROR", ("UNVERIFIED" if item.get("price") else "NO_PRICE")

    if isinstance(datos, dict) and datos.get("__error"):
        return "EXCEPTION", "NO_PRICE"

    if not logistica:
        if not item:
            estado = "ITEM_NOT_RETURNED"
        elif item.get("availability") == "withoutStock":
            estado = "SIN_STOCK"
        elif item.get("availability") == "cannotBeDelivered":
            estado = "NO_COVERAGE"
        elif item.get("availability") and item.get("availability") != "available":
            estado = f"NO_DISPONIBLE_{item.get('availability')}"
        else:
            estado = "NO_LOGISTICS"

    elif node_resolved == NODO.node_id:
        # `simulation` no elige SLA: `selectedSla` viene vacío por diseño del
        # endpoint. Por eso la ausencia de selección solo es ambigua cuando
        # hay más de una opción (v09 del motor).
        try:
            cuantos = int(logistica.get("slaCount") or 0)
        except (TypeError, ValueError):
            cuantos = 0

        seleccionado = MK.s(logistica.get("slaSeleccionado"))
        elegido_explicito = logistica.get("esSeleccionado") == "SI"
        unico_sin_ambiguedad = not seleccionado and cuantos == 1

        if not (elegido_explicito or unico_sin_ambiguedad):
            estado = "MATCH_SIN_CONFIRMAR"
        else:
            estado = "MATCH" if seller_confirmado else "MATCH_SELLER_RAIZ"

    elif node_resolved == "OTHER":
        estado = "OPERADOR_EXTERNO"

    else:
        estado = f"MISMATCH_RESOLVED_{node_resolved}"

    if not item.get("sellingPrice") and not item.get("price"):
        precio_estado = "NO_PRICE"
    elif estado == "MATCH":
        precio_estado = "VERIFIED"
    elif estado == "MATCH_SELLER_RAIZ":
        precio_estado = "VERIFIED_SELLER_RAIZ"
    elif estado == "MATCH_SIN_CONFIRMAR":
        precio_estado = "QUALIFIED"
    else:
        precio_estado = "UNVERIFIED"

    return estado, precio_estado


def construir_fila(catalogo: dict, medicion: dict) -> dict[str, str]:
    """Une catálogo (umbral + descuento) y medición (precio verificado)."""

    precio = medicion.get("precio_unitario")
    descuento = catalogo["descuento"]
    umbral = catalogo["umbral"]

    mayorista = None
    estado = ""

    if not medicion.get("medido") or precio is None:
        estado = "SIN_MEDICION"
    elif umbral is None and descuento is None:
        estado = "SIN_BIPRECIO"
    elif umbral is not None and descuento is None:
        estado = "SIN_DESCUENTO"
    elif descuento is not None and umbral is None:
        estado = "SIN_UMBRAL"
    else:
        mayorista = precio - descuento

        inconsistente = (
            mayorista <= CERO
            or mayorista > precio
            or not es_centavo_exacto(descuento)
            or not es_centavo_exacto(mayorista)
        )

        estado = "INCONSISTENTE" if inconsistente else "COMPLETO"

    completo = estado == "COMPLETO"

    pct = ""

    if descuento is not None and precio and precio > CERO:
        pct = f"{(descuento / precio * 100).quantize(CENTAVO)}"

    return {
        "product_name": catalogo["product_name"],
        "precio_unitario": money(precio),
        # Vacío si no es COMPLETO: ni cero ni el unitario repetido.
        "precio_mayorista": money(mayorista) if completo else "",
        "unidades_minimas": str(umbral) if (completo and umbral is not None) else "",
        "descuento_pct": pct,
        "surtido_makro": medicion.get("surtido_makro", ""),
        "seller_chain": medicion.get("seller_chain", ""),
        "node_id": medicion.get("node_id", ""),
        "logistics_status": medicion.get("logistics_status", ""),
        "price_status": medicion.get("price_status", ""),
        "availability": medicion.get("availability", ""),
        "category_path": catalogo["category_path"],
        "sku_id": catalogo["sku_id"],
        "ean": catalogo["ean"],
        "brand": catalogo["brand"],
        "list_price": money(medicion.get("list_price") or catalogo["list_price"]),
        "descuento_monto": "" if descuento is None else f"{descuento}",
        "biprecio_status": estado,
    }


# ===========================================================================
# ORQUESTACIÓN
# ===========================================================================


async def correr(argumentos) -> tuple[list[dict], list[dict], dict, dict]:
    from playwright.async_api import async_playwright

    seleccion: list[dict] = []
    filas: list[dict] = []
    muestra: dict = {}
    gasto = {"requests": 0, "tope": TOPE_REQUESTS, "categorias": [], "avisos": []}

    async with async_playwright() as playwright:
        navegador = await MK.abrir_navegador(playwright, argumentos.canal,
                                             not argumentos.headed)

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

            # ---------------- FASE 1: catálogo ----------------
            cliente.fase = "descubrimiento"

            log("FASE 1 — catálogo (umbral + descuento; sin contexto de sucursal)")

            status, arbol, _ = await cliente.pedir(
                f"{MK.BASE_URL}/api/catalog_system/pub/category/tree/3"
            )

            if status >= 400 or not isinstance(arbol, list):
                gasto["avisos"].append(f"El árbol respondió HTTP {status}.")
                return filas, seleccion, muestra, gasto

            categorias = aplanar(arbol, [], [], 1, [])
            por_ruta = {c["ruta_nombres"]: c for c in categorias}

            for objetivo in CATEGORIAS_OBJETIVO:
                categoria = por_ruta.get(objetivo)

                if not categoria:
                    gasto["avisos"].append(
                        f"No existe la categoría {' > '.join(objetivo)} en el árbol."
                    )
                    continue

                elegidos: list[dict] = []
                reserva: list[dict] = []
                total_vtex = None

                for pagina in range(PAGINAS_MAX):
                    desde = pagina * VENTANA

                    # RUTA COMPLETA DE IDS: `fq=C:/604/` (id suelto) devuelve
                    # HTTP 200 con cero productos, sin error. La forma que
                    # filtra de verdad es `fq=C:/399/604/`, y además incluye
                    # las subcategorías.
                    url = (
                        f"{MK.BASE_URL}/api/catalog_system/pub/products/search"
                        f"?fq=C:{categoria['ruta_ids']}"
                        f"&_from={desde}&_to={desde + VENTANA - 1}&sc={MK.SALES_CHANNEL}"
                    )

                    try:
                        status, datos, cabeceras = await cliente.pedir(url)
                    except Exception as exc:
                        gasto["avisos"].append(
                            f"{categoria['ruta']}: {type(exc).__name__} al paginar."
                        )
                        break

                    if pagina == 0:
                        total_vtex = MK.total_desde_resources(
                            cabeceras.get("resources", "")
                        )

                    if status >= 400 or not isinstance(datos, list) or not datos:
                        break

                    for crudo in datos:
                        if not isinstance(crudo, dict):
                            continue

                        producto = leer_catalogo(crudo, categoria)

                        if not producto:
                            continue

                        # Prioridad: los que el catálogo declara con umbral Y
                        # descuento. El resto queda de reserva para completar
                        # los 10 si no alcanzan — una fila sin bi-precio sigue
                        # siendo información, y sin ella no hay denominador.
                        if producto["umbral"] is not None and producto["descuento"] is not None:
                            if len(elegidos) < POR_CATEGORIA:
                                elegidos.append(producto)
                        elif len(reserva) < POR_CATEGORIA:
                            reserva.append(producto)

                    if len(elegidos) >= POR_CATEGORIA or len(datos) < VENTANA:
                        break

                faltan = POR_CATEGORIA - len(elegidos)

                if faltan > 0:
                    elegidos.extend(reserva[:faltan])

                    gasto["avisos"].append(
                        f"{categoria['ruta']}: solo {POR_CATEGORIA - faltan} de "
                        f"{POR_CATEGORIA} traen umbral+descuento en el catálogo; "
                        f"se completó con {min(faltan, len(reserva))} sin bi-precio."
                    )

                seleccion.extend(elegidos)

                gasto["categorias"].append(
                    {
                        "ruta": categoria["ruta"],
                        "id": categoria["id"],
                        "total_vtex": total_vtex,
                        "elegidos": len(elegidos),
                        "con_biprecio_catalogo": POR_CATEGORIA - max(faltan, 0),
                    }
                )

                log(f"  [{categoria['id']:>5}] {categoria['ruta'][:44]:<44} "
                    f"elegidos={len(elegidos)}  req={cliente.contador}/{TOPE_REQUESTS}")

            # ---------------- FASE 2: medición ----------------
            cliente.fase = "medicion"

            log("")
            log(f"FASE 2 — simulation qty=1 contra nodo {NODO.node_id} "
                f"({NODO.branch}, postal {NODO.postal_code})")

            for numero, catalogo in enumerate(seleccion, 1):
                medicion: dict[str, Any] = {"medido": False}

                if cliente.contador >= TOPE_REQUESTS:
                    medicion["logistics_status"] = "SIN_PRESUPUESTO"
                    filas.append(construir_fila(catalogo, medicion))
                    continue

                producto_mk = MK.Producto(
                    product_id=catalogo["product_id"],
                    sku_id=catalogo["sku_id"],
                    product_name=catalogo["product_name"],
                    brand=catalogo["brand"],
                    seller_id=catalogo["seller_id"],
                )

                try:
                    # Flujo del motor, sin tocarlo.
                    status, datos = await MK.consultar_simulation(
                        cliente, producto_mk, NODO
                    )
                except MK.TopeAgotadoError:
                    medicion["logistics_status"] = "SIN_PRESUPUESTO"
                    filas.append(construir_fila(catalogo, medicion))
                    continue
                except Exception as exc:
                    medicion["logistics_status"] = "EXCEPTION"
                    medicion["availability"] = f"{type(exc).__name__}"
                    filas.append(construir_fila(catalogo, medicion))
                    continue

                item = MK.extraer_item(datos, producto_mk)
                logistica = MK.extraer_logistica(datos, NODO)
                node_resolved, deriva, seller_confirmado = MK.identificar_nodo(
                    logistica, item.get("sellerChain", "")
                )

                estado, precio_estado = clasificar(
                    datos, status, producto_mk, item, logistica,
                    node_resolved, seller_confirmado,
                )

                cadena = item.get("sellerChain", "")

                medicion.update(
                    {
                        "medido": True,
                        "precio_unitario": de_centavos(
                            item.get("sellingPrice") or item.get("price")
                        ),
                        "list_price": de_centavos(item.get("listPrice")),
                        "availability": item.get("availability", ""),
                        "seller_chain": cadena,
                        # La ÚNICA prueba de surtido Makro Santa Anita.
                        "surtido_makro": "SI" if NODO.seller_chain in cadena else "NO",
                        "node_id": node_resolved,
                        "logistics_status": estado,
                        "price_status": precio_estado,
                        "polygon_drift": deriva,
                        "warehouse_id": logistica.get("warehouseId", ""),
                        "courier_name": logistica.get("courierName", ""),
                    }
                )

                fila = construir_fila(catalogo, medicion)
                filas.append(fila)

                log(f"  {numero:>2}/{len(seleccion)} {catalogo['sku_id']:<10} "
                    f"{catalogo['product_name'][:38]:<38} "
                    f"{fila['precio_unitario']:>7} -> "
                    f"{fila['precio_mayorista'] or '-':>7} "
                    f"x{fila['unidades_minimas'] or '-':<3} "
                    f"{medicion['surtido_makro']} {estado}")

                # El primer COMPLETO que además es surtido Makro se guarda
                # entero para el documento de inspección visual.
                if (not muestra and fila["biprecio_status"] == "COMPLETO"
                        and medicion["surtido_makro"] == "SI"):
                    muestra = {
                        "catalogo": catalogo,
                        "medicion": medicion,
                        "fila": fila,
                        "respuesta_sim": datos,
                        "logistica": logistica,
                    }

            gasto["requests"] = cliente.contador
            gasto["reintentos"] = cliente.reintentos_usados

        finally:
            await navegador.close()

    return filas, seleccion, muestra, gasto


# ===========================================================================
# SALIDA 1 — CSV
# ===========================================================================


def escribir_csv(filas: list[dict]) -> Path:
    SALIDA.mkdir(parents=True, exist_ok=True)

    with CSV_SALIDA.open("w", encoding="utf-8", newline="") as manejador:
        escritor = csv.DictWriter(manejador, fieldnames=COLUMNAS)
        escritor.writeheader()

        for fila in filas:
            escritor.writerow({c: fila.get(c, "") for c in COLUMNAS})

    return CSV_SALIDA


# ===========================================================================
# SALIDA 2 — ANÁLISIS
# ===========================================================================


def tabla_md(cabeceras: list[str], filas: list[list[Any]]) -> list[str]:
    lineas = [
        "| " + " | ".join(cabeceras) + " |",
        "|" + "|".join("---" for _ in cabeceras) + "|",
    ]

    for fila in filas:
        lineas.append("| " + " | ".join(str(c) for c in fila) + " |")

    return lineas


def escribir_md(filas: list[dict], gasto: dict) -> Path:
    L: list[str] = []

    total = len(filas)
    makro = [f for f in filas if f["surtido_makro"] == "SI"]
    completos = [f for f in filas if f["biprecio_status"] == "COMPLETO"]

    L.append(f"# Precio mayorista Makro Santa Anita ({MARCA})")
    L.append("")
    L.append(f"- **{total} productos** medidos con `simulation` qty=1 contra el nodo "
             f"**{NODO.node_id} {NODO.branch}** (postal {NODO.postal_code}) · "
             f"{gasto.get('requests', 0)}/{TOPE_REQUESTS} requests")
    L.append(f"- **{len(makro)} de {total} son surtido Makro** "
             f"(`sellerChain` contiene `{NODO.seller_chain}`); "
             f"**{total - len(makro)} no lo son**")
    L.append(f"- **{len(completos)} con precio mayorista reconstruible** "
             "(umbral del catálogo + descuento del teaser + precio verificado)")

    for aviso in gasto.get("avisos", []):
        L.append(f"- AVISO: {aviso}")

    # --- surtido ---
    L.append("")
    L.append("## Surtido: ¿Makro o Plaza Vea?")
    L.append("")

    cadenas = Counter(f["seller_chain"] or "(vacío)" for f in filas)

    L.extend(tabla_md(
        ["seller_chain (medido)", "productos", "surtido_makro"],
        [[k, v, "SI" if NODO.seller_chain in k else "NO"]
         for k, v in cadenas.most_common()],
    ))

    L.append("")
    L.append(f"El catálogo dice `sellerName = \"Makro Plazavea\"` sobre `sellerId = 1` "
             f"en todos. Eso no prueba nada: la cadena de arriba es la medida, y es "
             f"la única evidencia de que el precio sale del almacén de "
             f"{NODO.branch}.")

    # --- tabla legible ---
    L.append("")
    L.append("## Precios")
    L.append("")

    filas_tabla = []

    for fila in sorted(filas, key=lambda f: (f["category_path"], f["product_name"])):
        filas_tabla.append([
            fila["product_name"][:44],
            fila["precio_unitario"] or "-",
            fila["precio_mayorista"] or "—",
            fila["unidades_minimas"] or "—",
            f"{fila['descuento_pct']}%" if fila["descuento_pct"] else "—",
            fila["surtido_makro"] or "-",
        ])

    L.extend(tabla_md(
        ["producto", "unitario", "mayorista", "un. mín.", "% desc.", "Makro"],
        filas_tabla,
    ))

    L.append("")
    L.append("`—` significa que ese dato no existe para ese SKU, no que valga cero.")

    # --- distribuciones ---
    L.append("")
    L.append("## Umbral y descuento por categoría")
    L.append("")

    por_categoria: dict[str, list[dict]] = defaultdict(list)

    for fila in filas:
        por_categoria[fila["category_path"]].append(fila)

    filas_dist = []

    for ruta, propias in por_categoria.items():
        umbrales = [f["unidades_minimas"] for f in propias if f["unidades_minimas"]]
        pcts = [float(f["descuento_pct"]) for f in propias if f["descuento_pct"]]

        filas_dist.append([
            ruta[:38],
            len(propias),
            sum(1 for f in propias if f["surtido_makro"] == "SI"),
            sum(1 for f in propias if f["biprecio_status"] == "COMPLETO"),
            " · ".join(f"{u}×{c}" for u, c in Counter(umbrales).most_common()) or "—",
            f"{min(pcts):.2f}–{max(pcts):.2f}" if pcts else "—",
            f"{statistics.median(pcts):.2f}" if pcts else "—",
        ])

    L.extend(tabla_md(
        ["categoría", "SKUs", "Makro", "COMPLETO", "unidades mínimas",
         "% desc. rango", "% desc. mediana"],
        filas_dist,
    ))

    # --- estados ---
    L.append("")
    L.append("## Estado del bi-precio")
    L.append("")

    estados = Counter(f["biprecio_status"] for f in filas)

    L.extend(tabla_md(
        ["biprecio_status", "SKUs", "%"],
        [[k, v, f"{v / total * 100:.0f}%"] for k, v in estados.most_common()],
    ))

    for estado in ("SIN_DESCUENTO", "SIN_UMBRAL", "INCONSISTENTE",
                   "SIN_MEDICION", "SIN_BIPRECIO"):
        casos = [f for f in filas if f["biprecio_status"] == estado]

        if not casos:
            continue

        L.append("")
        L.append(f"**{estado}** ({len(casos)}): "
                 + ", ".join(f"`{c['sku_id']}`" for c in casos))

    logisticos = Counter(f["logistics_status"] for f in filas)
    precios = Counter(f["price_status"] for f in filas)

    L.append("")
    L.append("## Verificación logística")
    L.append("")
    L.extend(tabla_md(
        ["logistics_status", "SKUs"],
        [[k, v] for k, v in logisticos.most_common()],
    ))
    L.append("")
    L.extend(tabla_md(
        ["price_status", "SKUs"],
        [[k, v] for k, v in precios.most_common()],
    ))

    # --- límites ---
    L.append("")
    L.append("## Límites de este análisis")
    L.append("")
    L.append(f"- **{total} productos de 3 categorías**, elegidos priorizando los que "
             "el catálogo declara con umbral y descuento. **No es una muestra "
             "aleatoria**: sobre-representa a propósito los SKUs con bi-precio, así "
             "que el % de cobertura de esta tabla no se puede leer como cobertura "
             "del catálogo.")
    L.append(f"- **Un solo nodo ({NODO.node_id} {NODO.branch}) y un solo momento.** "
             "Que Surco (360) tenga estos precios y estos descuentos no está "
             "verificado acá.")
    L.append("- **El precio mayorista es reconstruido, no medido.** El unitario sí "
             "está verificado contra checkout; el mayorista es "
             "`unitario - descuento`, fórmula que v2 validó en centavos sobre **2 "
             "SKUs**. Medirlo de verdad exige una `simulation` a qty = umbral por "
             "cada SKU.")
    L.append("- **El umbral y el descuento vienen del catálogo, que no tiene "
             "sucursal.** Se asume que aplican igual en Santa Anita; el precio "
             "verificado es lo único con sucursal confirmada.")
    L.append("- **Un solo escalón.** `CantidadTriPrecioMK` se ignora porque v2 no lo "
             "vio aplicarse (qty 1/3/5/6/7 en un SKU). Un SKU no prueba que sea "
             "inerte en todos.")
    L.append("- **`SIN_DESCUENTO` no es \"sin bi-precio\"**: el umbral está declarado "
             "y el descuento no viaja. Para esos, el precio del tramo solo se obtiene "
             "midiendo a qty ≥ umbral.")

    L.append("")
    L.append("---")
    L.append(f"_`test/v4.py` · {gasto.get('requests', 0)} requests · "
             f"nodo {NODO.node_id} · {MARCA}_")

    SALIDA.mkdir(parents=True, exist_ok=True)
    MD_SALIDA.write_text("\n".join(L) + "\n", encoding="utf-8")

    return MD_SALIDA


# ===========================================================================
# SALIDA 3 — MUESTRA DE ETIQUETAS
# ===========================================================================


def escribir_muestra(muestra: dict) -> Path | None:
    if not muestra:
        return None

    catalogo = muestra["catalogo"]
    medicion = muestra["medicion"]
    fila = muestra["fila"]
    datos = muestra["respuesta_sim"]

    L: list[str] = []

    L.append(f"# Muestra de etiquetas — {catalogo['product_name']}")
    L.append("")
    L.append(f"SKU `{catalogo['sku_id']}` · categoría {catalogo['category_path']} · "
             f"nodo {NODO.node_id} {NODO.branch} · {MARCA}")
    L.append("")
    L.append(f"**Lectura final**: {fila['precio_unitario']} la unidad · "
             f"**{fila['precio_mayorista']} desde {fila['unidades_minimas']} "
             f"unidades** ({fila['descuento_pct']}% de descuento).")
    L.append("")
    L.append("Las rutas de abajo son las del JSON tal como responde VTEX. Sirven "
             "para verificar a mano que cada número del CSV salió de donde dice.")

    # ---- catálogo ----
    L.append("")
    L.append("## Bloque 1 — catálogo (umbral y descuento; SIN sucursal)")
    L.append("")
    L.append("`GET /api/catalog_system/pub/products/search"
             f"?fq=C:/…/{catalogo['category_id']}/&sc={MK.SALES_CHANNEL}` "
             "→ el producto en la posición `[i]` del array.")
    L.append("")

    crudo = catalogo["crudo"]

    lineas: list[list[str]] = []

    for ruta, valor in rutas_de(crudo, ("Price", "ListPrice", "PriceValidUntil"),
                                prefijo="[i]"):
        lineas.append([f"`{ruta}`", f"`{valor}`"])

    umbral = crudo.get("CantidadBiPrecioMK")
    lineas.append(["`[i].CantidadBiPrecioMK`",
                   f"`{umbral}`" if umbral else "_(ausente)_"])

    tri = crudo.get("CantidadTriPrecioMK")
    lineas.append(["`[i].CantidadTriPrecioMK`",
                   (f"`{tri}` — declarado, **ignorado**: v2 no lo vio aplicarse"
                    if tri else "_(ausente)_")])

    for ruta, valor in rutas_de(crudo, ("Name", "Value"),
                                prefijo="[i]", tope=8):
        if "PromotionTeasers" in ruta:
            lineas.append([f"`{ruta}`", f"`{valor}`"])

    L.extend(tabla_md(["ruta", "valor"], lineas))

    L.append("")
    L.append(f"El descuento (`{catalogo['descuento']}`) se lee buscando el parámetro "
             f"**por nombre** `{PARAM_DESCUENTO}`, no por índice: el orden de "
             "`Parameters` lo decide VTEX.")

    # ---- simulation ----
    L.append("")
    L.append("## Bloque 2 — simulation (precio verificado y firma de sucursal)")
    L.append("")
    L.append(f"`POST /api/checkout/pub/orderforms/simulation?sc={MK.SALES_CHANNEL}` "
             f"con `postalCode = {NODO.postal_code}`, "
             f"`geoCoordinates = [{NODO.longitude}, {NODO.latitude}]`, `quantity = 1`.")
    L.append("")

    lineas = []

    # Solo la rama del ítem: los mismos nombres viven también bajo
    # logisticsInfo (costo de envío) y purchaseConditions (espejo).
    for ruta, valor in rutas_de(
        datos,
        ("price", "listPrice", "sellingPrice", "sellerChain", "availability",
         "quantity", "reason", "calculatedSellingPrice", "total", "value"),
        tope=4,
        bajo="items",
        excluir=("availableDeliveryWindows",),
    ):
        lineas.append([f"`{ruta}`", f"`{valor}`"])

    # priceTags y priceDefinition se muestran SIEMPRE, incluso vacíos: que
    # `priceTags` venga vacío a qty=1 no es un dato faltante, es el dato —
    # v2 lo vio aparecer recién a qty >= umbral, con el monto del tramo.
    item_sim = {}

    for item in (datos.get("items") or []) if isinstance(datos, dict) else []:
        if isinstance(item, dict) and MK.s(item.get("id")) == catalogo["sku_id"]:
            item_sim = item
            break

    etiquetas = item_sim.get("priceTags")
    definicion = item_sim.get("priceDefinition")

    lineas.append([
        "`items[0].priceTags`",
        "_(vacío a qty=1 — la etiqueta del tramo aparece a qty ≥ umbral)_"
        if not etiquetas else f"`{etiquetas}`"[:150],
    ])
    lineas.append([
        "`items[0].priceDefinition`",
        f"`{definicion}`"[:200] if definicion else "_(ausente)_",
    ])

    for ruta, valor in rutas_de(
        datos, ("warehouseId", "dockId", "courierId", "courierName", "polygonName"),
        tope=1, bajo="logisticsInfo", excluir=("pickupStoreInfo",),
    ):
        lineas.append([f"`{ruta}`", f"`{valor}`"])

    L.extend(tabla_md(["ruta", "valor"], lineas))

    L.append("")
    L.append("El bloque de arriba está acotado a la rama `items[…]` y a la firma "
             "logística. Los mismos nombres (`price`, `listPrice`) reaparecen bajo "
             "`logisticsInfo` — ahí son el **costo del envío**, no el del producto — "
             "y bajo `purchaseConditions`, que es un espejo.")
    L.append("")
    L.append("### Lo que hace válida a esta fila")
    L.append("")
    L.extend(tabla_md(
        ["comprobación", "esperado", "medido", "veredicto"],
        [
            ["`sellerChain` contiene la cadena de la sucursal",
             f"`{NODO.seller_chain}`", f"`{medicion['seller_chain']}`",
             "OK" if medicion["surtido_makro"] == "SI" else "NO"],
            ["`warehouseId`", f"`{NODO.warehouse_id}`",
             f"`{medicion['warehouse_id']}`",
             "OK" if medicion["warehouse_id"] == NODO.warehouse_id else "NO"],
            ["`courierName`", f"`{NODO.courier_name}`",
             f"`{medicion['courier_name']}`",
             "OK" if medicion["courier_name"] == NODO.courier_name else "NO"],
            ["nodo identificado por firma", f"`{NODO.node_id}`",
             f"`{medicion['node_id']}`",
             "OK" if medicion["node_id"] == NODO.node_id else "NO"],
        ],
    ))

    if medicion.get("polygon_drift"):
        L.append("")
        L.append(f"`polygonName` derivó: {medicion['polygon_drift']}. No invalida la "
                 "firma — VTEX versiona el polígono sin que cambie la sucursal.")

    L.append("")
    L.append("## Cómo se arma el número final")
    L.append("")
    L.append("```")
    L.append(f"precio verificado (simulation)   {fila['precio_unitario']}")
    L.append(f"descuento por unidad (catálogo) - {catalogo['descuento']}")
    L.append(f"                                 {'-' * 8}")
    L.append(f"precio mayorista                 {fila['precio_mayorista']}  "
             f"desde {fila['unidades_minimas']} unidades")
    L.append("```")
    L.append("")
    L.append("El precio que entra en la resta es el **verificado**, no el del "
             "catálogo: el catálogo no tiene sucursal.")

    SALIDA.mkdir(parents=True, exist_ok=True)
    MUESTRA_SALIDA.write_text("\n".join(L) + "\n", encoding="utf-8")

    return MUESTRA_SALIDA


# ===========================================================================


def parsear_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="v4: precio mayorista Makro verificado por sucursal."
    )

    parser.add_argument("--intervalo", type=float, default=1.5)
    parser.add_argument("--reintentos", type=int, default=3)
    parser.add_argument("--canal", default="chrome")
    parser.add_argument("--headed", action="store_true")

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

    filas, seleccion, muestra, gasto = asyncio.run(correr(argumentos))

    if not filas:
        print("Sin filas: no se escribe nada.")

        for aviso in gasto.get("avisos", []):
            print(f"  {aviso}")

        return 1

    ruta_csv = escribir_csv(filas)
    ruta_md = escribir_md(filas, gasto)
    ruta_muestra = escribir_muestra(muestra)

    print("")
    print(f"CSV      : {ruta_csv}  ({len(filas)} filas)")
    print(f"Análisis : {ruta_md}")
    print(f"Muestra  : {ruta_muestra or 'NO SE GENERÓ (ningún COMPLETO con surtido Makro)'}")
    print(f"Requests : {gasto.get('requests', 0)}/{TOPE_REQUESTS}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.")
        sys.exit(130)
