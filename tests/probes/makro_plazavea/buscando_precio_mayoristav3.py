#!/usr/bin/env python3
"""
v3 — BARRIDO DE BI-PRECIO POR CATEGORÍA — MAKRO PERÚ (VTEX)
===========================================================

Última sonda de la serie. v1 encontró el mecanismo, v2 lo verificó contra
checkout, v3 mide QUÉ TAN GENERALIZADO está.

MECANISMO YA DESCIFRADO — esta sonda NO lo re-investiga
-------------------------------------------------------
Todo viaja en la respuesta de CATÁLOGO (descubrimiento). Cero `simulation`:

    commertialOffer.Price                                  precio unitario
    CantidadBiPrecioMK        (specification)              umbral del tramo
    CantidadTriPrecioMK       (specification)              declarado, SIN EFECTO
    PromotionTeasers[].Effects.Parameters
        PromotionalPriceTableItemsDiscount                 monto S/ por unidad
    PromotionTeasers[].Conditions.Parameters
        PaymentMethodId = "4"                              informativo, no restrictivo

    precio_mayorista = Price - PromotionalPriceTableItemsDiscount

Verificado en v2 contra checkout, en centavos: 13.70-0.90=12.80 y
13.30-0.27=13.03 (`sellingPrice` 1280 y 1303). Un solo escalón: medido en
qty 1/3/5/6/7 el precio no vuelve a bajar, así que `CantidadTriPrecioMK`
está declarado y no produce efecto observable — por eso
`precio_tri_reconstruido` va SIEMPRE VACÍO y `tri_verificado` va siempre
`NO_VERIFICADO`. No es pereza: es no afirmar un precio que nadie observó.

POR QUÉ `fq=C:/<id>/` Y NO `ft=`
--------------------------------
`ft=` es búsqueda por relevancia: el motor de VTEX decide qué entra y uno
no se entera de qué omitió — inservible para medir cobertura, porque el
denominador queda indefinido. `fq=C:/<ruta de ids>/` es pertenencia exacta al árbol,
y la cabecera `resources: 0-49/64` da el TOTAL real de la categoría: con eso
la cobertura se calcula, no se estima.

LO QUE EL CATÁLOGO NO TRAE (y por eso queda vacío en el CSV)
------------------------------------------------------------
  · `promo_regime_id` y `matched_parameters` — existen en la respuesta de
    `simulation` (v2: régimen ca697c7e-…, "Bi Precio Vigente Regular
    MAKRO"), NO en la de catálogo. Lo que el catálogo sí trae es el NOMBRE
    del teaser, que viaja en `promo_regime_name`.
  · contexto de sucursal — la request de catálogo va con `sc=9` y sin
    código postal, así que estos precios son de cadena, no de una sucursal.
    `node_id` lo dice explícitamente: SIN_CONTEXTO_SUCURSAL. Poner "359"
    ahí sería exactamente el error que el motor existe para no cometer.

DISCIPLINA
----------
  · Cada respuesta se ARCHIVA EN CRUDO ANTES de parsear nada.
  · Tope duro de 60 requests, intervalo mínimo 1.5s, `Cliente` del motor.
  · `PromotionalPriceTableItemsDiscount` se busca POR NOMBRE a cualquier
    profundidad, nunca por índice de array.
  · Todo el cálculo monetario es `Decimal`. Ningún float toca un precio.
  · Ningún producto se descarta del CSV: sin el denominador no hay cobertura.
  · Celda vacía antes que valor inventado.
  · SOLO escribe archivos nuevos. No toca engines/ ni nada preexistente.

USO
---
    python3 test/v3.py
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import importlib.util
import json
import statistics
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
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
    """Importa `test/v1.py` (que carga el motor). No ejecuta nada al importar."""

    if not V1_PY.exists():
        raise SystemExit(f"No encuentro la sonda v1 en {V1_PY}")

    spec = importlib.util.spec_from_file_location("v1_sonda", V1_PY)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["v1_sonda"] = modulo
    spec.loader.exec_module(modulo)

    return modulo


V1 = cargar_v1()
MK = V1.MK


# ===========================================================================
# PARÁMETROS DEL BARRIDO
# ===========================================================================

TOPE_REQUESTS = 60
VENTANA = 50                 # el catálogo no pagina más de 50 por consulta
PAGINAS_POR_CATEGORIA = 10   # 500 SKUs por categoría como techo
#
# Con 4 páginas (corrida 23:43) tres categorías quedaron truncadas —
# Abarrotes y Limpieza entre ellas, que son justo el núcleo mayorista — y
# sobraban 36 requests del tope. Un porcentaje de cobertura calculado sobre
# una categoría cortada describe las primeras 200 filas que devolvió VTEX,
# no la categoría; con 10 páginas entran enteras y el denominador es real.
CATEGORIAS_OBJETIVO = 12     # >= 8 exigidas, con margen

# Raíces de nivel 1 obligatorias — el núcleo del negocio mayorista. El
# orden importa: el round-robin reparte el presupuesto entre estas primero.
RAICES_OBLIGATORIAS = ["abarrote", "bebida", "limpieza"]

PARAM_DESCUENTO = "PromotionalPriceTableItemsDiscount"
PARAM_PAGO = "PaymentMethodId"

SENTINELA_VTEX = "3000-01-02"        # "sin vencimiento" en VTEX
# Segundo centinela observado en el barrido: 79 SKUs con 2099-01-02. Un año
# 2099 no es una campaña, es otro "nunca vence" escrito distinto. Contarlo
# como fecha de campaña infla el hallazgo más interesante del barrido con
# 79 falsos positivos, así que se clasifica aparte.
SENTINELA_LEJANO = "2099-01-02"
NODE_ID = "SIN_CONTEXTO_SUCURSAL"    # el catálogo no lleva sucursal

COLUMNAS = [
    "timestamp", "run_id", "node_id",
    "category_id", "category_path",
    "product_id", "sku_id", "ean", "product_name", "brand", "vendido_por",
    "price", "list_price", "price_valid_until",
    "bi_umbral", "tri_umbral_declarado", "tri_verificado", "familia_escalera",
    "descuento_monto", "descuento_pct",
    "precio_bi_reconstruido", "precio_tri_reconstruido",
    "promo_regime_id", "promo_regime_name", "payment_method_id",
    "matched_parameters", "seller_chain", "biprecio_status",
]

MARCA = datetime.now().strftime("%Y%m%d_%H%M%S")
RUN_ID = f"v3_{MARCA}"

SALIDA = RAIZ / "data" / MK.MOTOR
DIR_RAW = SALIDA / "raw" / datetime.now().strftime("%Y-%m-%d")

CSV_SALIDA = SALIDA / f"v3_biprecio_{MARCA}.csv"
MD_SALIDA = SALIDA / f"v3_analisis_{MARCA}.md"

CERO = Decimal("0")
CENTAVO = Decimal("0.01")


# ===========================================================================
# HERRAMIENTAS
# ===========================================================================


def log(mensaje: str = "") -> None:
    print(mensaje, flush=True)


def normalizar(texto: str) -> str:
    limpio = unicodedata.normalize("NFKD", texto or "")

    return "".join(c for c in limpio if not unicodedata.combining(c)).lower()


def dec(valor: Any) -> Decimal | None:
    """
    Convierte a Decimal pasando por `str`: nunca `Decimal(float)`.

    El JSON llega ya parseado por el cliente del motor, con los precios como
    float. `Decimal(13.3)` arrastra la basura binaria del float;
    `Decimal(str(13.3))` da 13.3 exacto, que es lo que el servidor dijo.
    """

    if valor is None or isinstance(valor, bool):
        return None

    try:
        return Decimal(str(valor).strip())
    except (InvalidOperation, ValueError, ArithmeticError):
        return None


def entero(valor: Any) -> int | None:
    numero = dec(valor)

    if numero is None:
        return None

    try:
        return int(numero) if numero == numero.to_integral_value() else None
    except (InvalidOperation, ArithmeticError):
        return None


def es_centavo_exacto(valor: Decimal | None) -> bool:
    """¿El monto cae en un centavo entero? 0.270 sí; 0.2725 no."""

    if valor is None:
        return False

    return (valor * 100) == (valor * 100).to_integral_value()


def money(valor: Decimal | None) -> str:
    return "" if valor is None else f"{valor.quantize(CENTAVO)}"


def primera_spec(producto: dict, nombre: str) -> Any:
    """Las specifications de VTEX vienen como lista, incluso con un valor."""

    valor = producto.get(nombre)

    if isinstance(valor, list):
        return valor[0] if valor else None

    return valor


def parametros_por_nombre(objeto: Any, nombre: str) -> list[Any]:
    """
    Busca `{"Name": <nombre>, "Value": X}` a cualquier profundidad y en
    cualquier capitalización, y devuelve los valores.

    Nunca por índice: el orden de los parámetros lo decide VTEX. Anclar en
    `Parameters[1]` funciona hasta el día en que agregan un parámetro
    adelante, y ese día se lee el ID del producto como si fuera un descuento.
    """

    encontrados: list[Any] = []

    def recorrer(nodo: Any) -> None:
        if isinstance(nodo, dict):
            # Normaliza tanto `Name` como `<Name>k__BackingField`: VTEX
            # serializa el mismo teaser de las dos formas en la misma
            # respuesta (`PromotionTeasers` vs `Teasers`).
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


# ===========================================================================
# ÁRBOL DE CATEGORÍAS
# ===========================================================================


def aplanar_con_ids(arbol: Any, ruta: list[str], ruta_ids: list[str],
                    nivel: int, acumulado: list[dict]) -> list[dict]:
    """
    Aplana el árbol conservando el ID de cada categoría.

    `aplanar_categorias` del motor construye la ruta de NOMBRES (que es lo
    que necesita `fq=C:/Limpieza/Limpieza de Cocina/`). Acá hace falta el
    ID, porque el barrido usa `fq=C:/604/` y porque el ID es la llave
    estable con la que el CSV se cruza después. El barrido usa la RUTA de
    ids (`fq=C:/399/604/`): el id suelto devuelve cero productos.
    """

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
                "name": nombre,
                "nivel": nivel,
                "ruta": " > ".join(camino),
                "raiz": camino[0],
                "ruta_ids": "/" + "/".join(camino_ids) + "/",
            }
        )

        aplanar_con_ids(rama.get("children") or [], camino, camino_ids,
                        nivel + 1, acumulado)

    return acumulado


def elegir_categorias(categorias: list[dict]) -> tuple[list[dict], list[str]]:
    """
    Elige categorías de NIVEL 2, repartidas por raíz en round-robin.

    Round-robin y no "las primeras N": si se toman en orden de árbol, las 12
    salen todas de la primera raíz alfabética y el barrido mide una góndola,
    no el catálogo. Las raíces obligatorias (abarrotes, bebidas, limpieza)
    van primero, así que si el presupuesto se corta, se corta en las
    opcionales.
    """

    nivel2 = [c for c in categorias if c["nivel"] == 2]

    por_raiz: dict[str, list[dict]] = defaultdict(list)

    for categoria in nivel2:
        por_raiz[categoria["raiz"]].append(categoria)

    obligatorias: list[str] = []
    faltantes: list[str] = []

    for palabra in RAICES_OBLIGATORIAS:
        coincide = [raiz for raiz in por_raiz if palabra in normalizar(raiz)]

        if coincide:
            obligatorias.extend(sorted(coincide))
        else:
            faltantes.append(palabra)

    resto = sorted(raiz for raiz in por_raiz if raiz not in obligatorias)
    orden_raices = obligatorias + resto

    elegidas: list[dict] = []
    vuelta = 0

    while len(elegidas) < CATEGORIAS_OBJETIVO:
        agregado = False

        for raiz in orden_raices:
            if len(elegidas) >= CATEGORIAS_OBJETIVO:
                break

            candidatas = por_raiz.get(raiz) or []

            if vuelta < len(candidatas):
                elegidas.append(candidatas[vuelta])
                agregado = True

        if not agregado:
            break

        vuelta += 1

    return elegidas, faltantes


# ===========================================================================
# ARCHIVADO CRUDO (siempre ANTES de parsear)
# ===========================================================================

ARCHIVADOS: list[Path] = []


def archivar(nombre: str, contenido: Any) -> Path:
    DIR_RAW.mkdir(parents=True, exist_ok=True)

    destino = DIR_RAW / nombre
    destino.write_text(
        json.dumps(contenido, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    ARCHIVADOS.append(destino)

    return destino


# ===========================================================================
# PARSEO DE UN PRODUCTO -> UNA FILA
# ===========================================================================


def construir_fila(crudo: dict, categoria: dict, momento: str) -> dict[str, str]:
    """
    Un producto del catálogo -> una fila del CSV.

    Nunca levanta por un producto raro: un campo que no se puede leer queda
    vacío y la fila igual se escribe. Un SKU ilegible sigue contando en el
    denominador de la cobertura.
    """

    items = crudo.get("items") or []
    item = items[0] if isinstance(items, list) and items else {}
    vendedores = item.get("sellers") or []
    vendedor = vendedores[0] if vendedores else {}
    oferta = vendedor.get("commertialOffer") or {}

    precio = dec(oferta.get("Price"))
    lista = dec(oferta.get("ListPrice"))

    bi = entero(primera_spec(crudo, "CantidadBiPrecioMK"))
    tri = entero(primera_spec(crudo, "CantidadTriPrecioMK"))

    teasers = oferta.get("PromotionTeasers") or []

    descuentos = parametros_por_nombre(teasers, PARAM_DESCUENTO)
    pagos = parametros_por_nombre(teasers, PARAM_PAGO)

    descuento = dec(descuentos[0]) if descuentos else None

    nombres_teaser = []

    for teaser in teasers:
        if isinstance(teaser, dict):
            nombre = teaser.get("Name") or teaser.get("name")

            if nombre:
                nombres_teaser.append(MK.s(nombre))

    # --- derivadas -------------------------------------------------------
    familia = ""

    if bi is not None:
        familia = "PAR" if bi % 2 == 0 else "IMPAR"

    pct = ""
    reconstruido = None

    if descuento is not None and precio is not None and precio > CERO:
        pct = f"{(descuento / precio).quantize(Decimal('0.0001'))}"
        reconstruido = precio - descuento

    # --- estado ----------------------------------------------------------
    tiene_umbral = bi is not None
    tiene_descuento = descuento is not None

    if not tiene_umbral and not tiene_descuento:
        estado = "SIN_BIPRECIO"
    elif tiene_umbral and not tiene_descuento:
        estado = "SIN_DESCUENTO"
    elif tiene_descuento and not tiene_umbral:
        estado = "SIN_UMBRAL"
    else:
        estado = "COMPLETO"

    if tiene_descuento:
        inconsistente = (
            reconstruido is None
            or reconstruido <= CERO
            or (precio is not None and reconstruido > precio)
            or not es_centavo_exacto(descuento)
            or not es_centavo_exacto(reconstruido)
        )

        if inconsistente:
            estado = "INCONSISTENTE"

    vendido_por = primera_spec(crudo, "Vendido por")

    return {
        "timestamp": momento,
        "run_id": RUN_ID,
        "node_id": NODE_ID,
        "category_id": categoria["id"],
        "category_path": categoria["ruta"],
        "product_id": MK.s(crudo.get("productId")),
        "sku_id": MK.s(item.get("itemId")),
        "ean": MK.s(item.get("ean")),
        "product_name": MK.s(crudo.get("productName")),
        "brand": MK.s(crudo.get("brand")),
        "vendido_por": MK.s(vendido_por),
        "price": money(precio),
        "list_price": money(lista),
        "price_valid_until": MK.s(oferta.get("PriceValidUntil")),
        "bi_umbral": "" if bi is None else str(bi),
        "tri_umbral_declarado": "" if tri is None else str(tri),
        "tri_verificado": "NO_VERIFICADO",
        "familia_escalera": familia,
        "descuento_monto": "" if descuento is None else f"{descuento}",
        "descuento_pct": pct,
        "precio_bi_reconstruido": money(reconstruido),
        # El tri no se aplica en checkout (v2, qty 1/3/5/6/7). Vacío a
        # propósito: no se publica un precio que nadie observó.
        "precio_tri_reconstruido": "",
        # No viaja en la respuesta de catálogo; v2 lo leyó en simulation.
        "promo_regime_id": "",
        "promo_regime_name": " | ".join(nombres_teaser),
        "payment_method_id": " | ".join(MK.s(p) for p in pagos),
        # Tampoco viaja en catálogo (está en rateAndBenefitsIdentifiers).
        "matched_parameters": "",
        # El catálogo trae el seller, no la cadena: la cadena
        # (`1 > plazaveamko359`) solo existe en simulation, con sucursal.
        "seller_chain": MK.s(vendedor.get("sellerId")),
        "biprecio_status": estado,
    }


# ===========================================================================
# BARRIDO
# ===========================================================================


async def barrer(argumentos) -> tuple[list[dict], list[dict], list[str], dict]:
    from playwright.async_api import async_playwright

    filas: list[dict] = []
    resumen_categorias: list[dict] = []
    faltantes: list[str] = []
    gasto = {"requests": 0, "tope": TOPE_REQUESTS, "reintentos": 0, "duplicados": 0}

    vistos: set[str] = set()

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
            cliente.fase = "descubrimiento"

            # ---------------- árbol ----------------
            log("Leyendo árbol de categorías...")

            status, arbol, _ = await cliente.pedir(
                f"{MK.BASE_URL}/api/catalog_system/pub/category/tree/3"
            )

            archivar(f"v3_arbol_{MARCA}.json",
                     {"sonda": "v3", "http_status": status, "respuesta": arbol})

            if status >= 400 or not isinstance(arbol, list):
                log(f"El árbol respondió HTTP {status}. Sin árbol no hay barrido.")
                return filas, resumen_categorias, faltantes, gasto

            categorias = aplanar_con_ids(arbol, [], [], 1, [])
            elegidas, faltantes = elegir_categorias(categorias)

            log(f"Categorías en el árbol: {len(categorias)} · "
                f"nivel 2 elegidas: {len(elegidas)}")

            if faltantes:
                log(f"AVISO: no hay raíz de nivel 1 que coincida con: "
                    f"{', '.join(faltantes)}")

            for categoria in elegidas:
                log(f"  [{categoria['id']}] {categoria['ruta']}")

            # ---------------- barrido por categoría ----------------
            for indice, categoria in enumerate(elegidas):
                pendientes = len(elegidas) - indice - 1   # reserva: 1 página c/u

                total_categoria = None
                barridos = 0
                paginas = 0
                incompleta_por = ""

                for pagina in range(PAGINAS_POR_CATEGORIA):
                    # La reserva garantiza que toda categoría elegida reciba
                    # al menos su primera página: sin eso, una categoría
                    # grande al principio se come el presupuesto y las
                    # últimas quedan sin denominador ni fila.
                    if pagina > 0 and cliente.contador + 1 > TOPE_REQUESTS - pendientes:
                        incompleta_por = "PRESUPUESTO"
                        break

                    desde = pagina * VENTANA
                    url = (
                        f"{MK.BASE_URL}/api/catalog_system/pub/products/search"
                        # RUTA COMPLETA DE IDS, no el id suelto.
                        #
                        # Verificado el 18-ago: `fq=C:/604/` devuelve HTTP 200
                        # con `resources: 0-49/0` — cero productos, sin error,
                        # sin aviso. La forma que filtra de verdad es la ruta
                        # entera (`fq=C:/399/604/`, 505 SKUs) y además es
                        # jerárquica: incluye las subcategorías de nivel 3.
                        # Un id suelto no "falla": miente en silencio con una
                        # lista vacía, que es exactamente el modo de error que
                        # esta serie de sondas existe para no comerse.
                        f"?fq=C:{categoria['ruta_ids']}"
                        f"&_from={desde}&_to={desde + VENTANA - 1}&sc={MK.SALES_CHANNEL}"
                    )

                    try:
                        status, datos, cabeceras = await cliente.pedir(url)
                    except MK.TopeAgotadoError:
                        incompleta_por = "PRESUPUESTO"
                        break
                    except Exception as exc:
                        incompleta_por = f"ERROR: {type(exc).__name__}"
                        break

                    # ARCHIVAR PRIMERO. Nada de parseo antes de esta línea.
                    archivar(
                        f"v3_catalogo_cat{categoria['id']}_p{pagina}_{MARCA}.json",
                        {
                            "sonda": "v3_barrido_biprecio",
                            "fase": "descubrimiento",
                            "category_id": categoria["id"],
                            "category_path": categoria["ruta"],
                            "pagina": pagina,
                            "url": url,
                            "http_status": status,
                            "resources": cabeceras.get("resources", ""),
                            "momento": datetime.now().isoformat(timespec="seconds"),
                            "respuesta": datos,
                        },
                    )

                    paginas += 1

                    if pagina == 0:
                        total_categoria = MK.total_desde_resources(
                            cabeceras.get("resources", "")
                        )

                    if status >= 400 or not isinstance(datos, list):
                        incompleta_por = f"HTTP {status}"
                        break

                    momento = datetime.now().isoformat(timespec="seconds")

                    for crudo in datos:
                        if not isinstance(crudo, dict):
                            continue

                        fila = construir_fila(crudo, categoria, momento)
                        barridos += 1

                        if not fila["sku_id"]:
                            continue

                        if fila["sku_id"] in vistos:
                            gasto["duplicados"] += 1
                            continue

                        vistos.add(fila["sku_id"])
                        filas.append(fila)

                    if len(datos) < VENTANA:
                        break

                    if pagina == PAGINAS_POR_CATEGORIA - 1:
                        incompleta_por = "TECHO_DE_PAGINAS"

                resumen_categorias.append(
                    {
                        "id": categoria["id"],
                        "ruta": categoria["ruta"],
                        "raiz": categoria["raiz"],
                        "total_vtex": total_categoria,
                        "barridos": barridos,
                        "paginas": paginas,
                        "incompleta_por": incompleta_por,
                    }
                )

                log(f"  [{categoria['id']:>5}] {categoria['ruta'][:52]:<52} "
                    f"total={total_categoria if total_categoria is not None else '?':>5} "
                    f"barridos={barridos:>4} "
                    f"req={cliente.contador}/{TOPE_REQUESTS}"
                    + (f"  ({incompleta_por})" if incompleta_por else ""))

                if cliente.contador >= TOPE_REQUESTS:
                    log("  Tope de requests alcanzado: el barrido se detiene acá.")
                    break

            gasto["requests"] = cliente.contador
            gasto["reintentos"] = cliente.reintentos_usados
            gasto["categorias_elegidas"] = len(elegidas)
            gasto["categorias_barridas"] = len(resumen_categorias)

        finally:
            await navegador.close()

    return filas, resumen_categorias, faltantes, gasto


# ===========================================================================
# SALIDA 1 — CSV
# ===========================================================================


def escribir_csv(filas: list[dict]) -> Path:
    SALIDA.mkdir(parents=True, exist_ok=True)

    with CSV_SALIDA.open("w", encoding="utf-8", newline="") as manejador:
        escritor = csv.DictWriter(manejador, fieldnames=COLUMNAS)
        escritor.writeheader()

        for fila in filas:
            escritor.writerow({columna: fila.get(columna, "") for columna in COLUMNAS})

    return CSV_SALIDA


# ===========================================================================
# SALIDA 2 — ANÁLISIS EN MARKDOWN
# ===========================================================================


def tabla_md(cabeceras: list[str], filas: list[list[str]]) -> list[str]:
    lineas = [
        "| " + " | ".join(cabeceras) + " |",
        "|" + "|".join("---" for _ in cabeceras) + "|",
    ]

    for fila in filas:
        lineas.append("| " + " | ".join(str(c) for c in fila) + " |")

    return lineas


def escribir_md(filas: list[dict], categorias: list[dict],
                faltantes: list[str], gasto: dict) -> Path:

    L: list[str] = []

    con_bi = [f for f in filas if f["biprecio_status"] != "SIN_BIPRECIO"]
    completos = [f for f in filas if f["biprecio_status"] == "COMPLETO"]

    total = len(filas)
    pct_total = (len(con_bi) / total * 100) if total else 0

    L.append(f"# Bi-precio Makro — barrido por categoría ({MARCA})")
    L.append("")
    L.append(f"- **{total} SKUs** de **{len(categorias)} categorías** de nivel 2 "
             f"({gasto.get('requests', 0)}/{gasto.get('tope', TOPE_REQUESTS)} requests, "
             f"solo descubrimiento, cero `simulation`)")
    L.append(f"- **{len(con_bi)} con bi-precio declarado ({pct_total:.1f}%)**, "
             f"de los cuales **{len(completos)} con precio mayorista reconstruible**")
    L.append(f"- CSV: `{CSV_SALIDA.name}` · evidencia cruda: "
             f"`raw/{DIR_RAW.name}/v3_catalogo_cat*_{MARCA}.json`")

    if gasto.get("duplicados"):
        L.append(f"- {gasto['duplicados']} apariciones repetidas del mismo SKU en más de "
                 f"una categoría: se conserva la primera (una fila por SKU)")

    if faltantes:
        L.append(f"- **AVISO**: no se encontró raíz de nivel 1 para: "
                 f"{', '.join(faltantes)}")

    # ---------------- 1. cobertura ----------------
    L.append("")
    L.append("## 1. Cobertura")
    L.append("")

    filas_tabla = []

    por_categoria: dict[str, list[dict]] = defaultdict(list)

    for fila in filas:
        por_categoria[fila["category_id"]].append(fila)

    for categoria in categorias:
        propias = por_categoria.get(categoria["id"], [])
        con = [f for f in propias if f["biprecio_status"] != "SIN_BIPRECIO"]
        total_vtex = categoria["total_vtex"]

        cobertura = (
            f"{len(propias) / total_vtex * 100:.0f}%"
            if total_vtex else "?"
        )

        filas_tabla.append([
            categoria["id"],
            categoria["ruta"][:46],
            total_vtex if total_vtex is not None else "?",
            len(propias),
            cobertura,
            len(con),
            f"{len(con) / len(propias) * 100:.0f}%" if propias else "-",
            categoria["incompleta_por"] or "completa",
        ])

    L.extend(tabla_md(
        ["cat", "categoría", "SKUs (VTEX)", "barridos", "cobertura",
         "con bi-precio", "% bi-precio", "corte"],
        filas_tabla,
    ))

    L.append("")
    L.append(f"**Total barrido: {total} SKUs · {len(con_bi)} con bi-precio "
             f"({pct_total:.1f}%).** La columna *SKUs (VTEX)* sale de la cabecera "
             "`resources` de la primera página: es el total real de la categoría, "
             "no una estimación. *corte* dice por qué una categoría quedó sin barrer "
             "entera.")

    # ---------------- 2. distribuciones ----------------
    L.append("")
    L.append("## 2. Distribución de umbrales")
    L.append("")

    bis = Counter(f["bi_umbral"] for f in filas if f["bi_umbral"])
    tris = Counter(f["tri_umbral_declarado"] for f in filas if f["tri_umbral_declarado"])

    L.append("**`bi_umbral`** (umbral que sí se aplica en checkout):")
    L.append("")
    L.extend(tabla_md(
        ["umbral", "SKUs", "%"],
        [[k, v, f"{v / len(con_bi) * 100:.0f}%" if con_bi else "-"]
         for k, v in sorted(bis.items(), key=lambda x: int(x[0]))],
    ))

    L.append("")
    L.append("**`tri_umbral_declarado`** (declarado en el catálogo; v2 comprobó "
             "que **no produce un segundo escalón** en checkout):")
    L.append("")
    L.extend(tabla_md(
        ["umbral", "SKUs"],
        [[k, v] for k, v in sorted(tris.items(), key=lambda x: int(x[0]))],
    ) if tris else ["_Ningún SKU declara `CantidadTriPrecioMK` en esta muestra._"])

    # ---------------- 3. tri = 2 x bi ----------------
    L.append("")
    L.append("## 3. ¿Se cumple `tri = 2 × bi`?")
    L.append("")

    con_ambos = [f for f in filas if f["bi_umbral"] and f["tri_umbral_declarado"]]
    cumplen = [f for f in con_ambos
               if int(f["tri_umbral_declarado"]) == 2 * int(f["bi_umbral"])]
    no_cumplen = [f for f in con_ambos if f not in cumplen]

    L.append(f"- SKUs con ambos umbrales: **{len(con_ambos)}**")
    L.append(f"- Cumplen `tri = 2 × bi`: **{len(cumplen)}**")
    L.append(f"- No cumplen: **{len(no_cumplen)}**")

    if no_cumplen:
        L.append("")
        L.append("Lista completa de los que **no** cumplen:")
        L.append("")
        L.extend(tabla_md(
            ["sku_id", "producto", "bi", "tri", "categoría"],
            [[f["sku_id"], f["product_name"][:40], f["bi_umbral"],
              f["tri_umbral_declarado"], f["category_path"][:30]]
             for f in no_cumplen],
        ))

    # ---------------- 4. par vs impar ----------------
    L.append("")
    L.append("## 4. Familia PAR vs IMPAR")
    L.append("")

    familias = Counter(f["familia_escalera"] for f in filas if f["familia_escalera"])

    L.extend(tabla_md(
        ["familia", "SKUs", "%"],
        [[k, v, f"{v / sum(familias.values()) * 100:.0f}%"]
         for k, v in familias.most_common()],
    ))

    L.append("")
    L.append("Por categoría (solo SKUs con umbral):")
    L.append("")

    filas_fam = []

    for categoria in categorias:
        propias = [f for f in por_categoria.get(categoria["id"], []) if f["familia_escalera"]]

        if not propias:
            continue

        cuenta = Counter(f["familia_escalera"] for f in propias)
        filas_fam.append([
            categoria["ruta"][:40],
            cuenta.get("PAR", 0),
            cuenta.get("IMPAR", 0),
            "PAR" if cuenta.get("PAR", 0) > cuenta.get("IMPAR", 0)
            else ("IMPAR" if cuenta.get("IMPAR", 0) > cuenta.get("PAR", 0) else "empate"),
        ])

    L.extend(tabla_md(["categoría", "PAR", "IMPAR", "predomina"], filas_fam)
             if filas_fam else ["_Sin SKUs con umbral._"])

    marcas_fam = Counter(
        (f["brand"], f["familia_escalera"]) for f in filas if f["familia_escalera"]
    )
    marcas_mixtas = defaultdict(set)

    for (marca, familia), _ in marcas_fam.items():
        marcas_mixtas[marca].add(familia)

    mixtas = [m for m, fams in marcas_mixtas.items() if len(fams) > 1]

    L.append("")
    L.append(f"Marcas con SKUs en **ambas** familias: **{len(mixtas)}** de "
             f"{len(marcas_mixtas)} marcas con umbral"
             + (f" ({', '.join(sorted(mixtas)[:8])}…)" if mixtas else ""))
    L.append("")
    L.append("Si una misma marca aparece en las dos familias, el umbral no es un "
             "atributo de la marca sino del SKU (o del formato/empaque), y no se "
             "puede predecir por marca.")

    # ---------------- 5. descuento ----------------
    L.append("")
    L.append("## 5. Descuento")
    L.append("")

    pcts = [Decimal(f["descuento_pct"]) for f in filas if f["descuento_pct"]]

    if pcts:
        ordenados = sorted(pcts)
        mediana = statistics.median([float(p) for p in ordenados])

        L.append(f"- SKUs con descuento: **{len(pcts)}**")
        L.append(f"- Mediana: **{mediana * 100:.2f}%** · "
                 f"mínimo: **{float(ordenados[0]) * 100:.2f}%** · "
                 f"máximo: **{float(ordenados[-1]) * 100:.2f}%**")

        cluster = Counter(f"{float(p) * 100:.1f}%" for p in pcts)

        L.append("")
        L.append("Agrupamiento (redondeado a 0.1 punto):")
        L.append("")
        L.extend(tabla_md(
            ["descuento %", "SKUs"],
            [[k, v] for k, v in cluster.most_common(12)],
        ))

        L.append("")
        L.append("Cruce descuento × categoría:")
        L.append("")

        filas_pct = []

        for categoria in categorias:
            propias = [f for f in por_categoria.get(categoria["id"], []) if f["descuento_pct"]]

            if not propias:
                continue

            valores = [float(Decimal(f["descuento_pct"])) * 100 for f in propias]
            distintos = Counter(f"{v:.1f}%" for v in valores)
            dominante, cuenta_dom = distintos.most_common(1)[0]

            filas_pct.append([
                categoria["ruta"][:38],
                len(propias),
                f"{min(valores):.1f}%",
                f"{statistics.median(valores):.1f}%",
                f"{max(valores):.1f}%",
                len(distintos),
                f"{dominante} ({cuenta_dom / len(propias) * 100:.0f}%)",
            ])

        L.extend(tabla_md(
            ["categoría", "SKUs", "mín", "mediana", "máx", "valores distintos", "dominante"],
            filas_pct,
        ))

        L.append("")
        L.append("Si una categoría concentra casi todos sus SKUs en un mismo "
                 "porcentaje, el descuento se define por categoría y es predecible; "
                 "si la columna *valores distintos* es alta, se define por SKU y hay "
                 "que leerlo de cada uno.")
    else:
        L.append("_Ningún SKU trae descuento en esta muestra._")

    # ---------------- 6. estados ----------------
    L.append("")
    L.append("## 6. Estado del bi-precio")
    L.append("")

    estados = Counter(f["biprecio_status"] for f in filas)

    L.extend(tabla_md(
        ["biprecio_status", "SKUs", "%"],
        [[k, v, f"{v / total * 100:.1f}%"] for k, v in estados.most_common()],
    ))

    for estado in ("SIN_DESCUENTO", "SIN_UMBRAL", "INCONSISTENTE"):
        casos = [f for f in filas if f["biprecio_status"] == estado]

        L.append("")

        if not casos:
            L.append(f"**{estado}**: ninguno.")
            continue

        L.append(f"**{estado}** ({len(casos)}):")
        L.append("")
        L.extend(tabla_md(
            ["sku_id", "producto", "price", "bi", "descuento", "categoría"],
            [[c["sku_id"], c["product_name"][:36], c["price"], c["bi_umbral"] or "-",
              c["descuento_monto"] or "-", c["category_path"][:28]]
             for c in casos[:40]],
        ))

        if len(casos) > 40:
            L.append("")
            L.append(f"_… y {len(casos) - 40} más en el CSV "
                     f"(`biprecio_status = {estado}`)._")

    # ---------------- 7-10. campos de régimen ----------------
    L.append("")
    L.append("## 7. Régimen promocional")
    L.append("")

    regimenes = Counter(f["promo_regime_name"] for f in filas if f["promo_regime_name"])

    L.extend(tabla_md(
        ["promo_regime_name (teaser)", "SKUs"],
        [[k, v] for k, v in regimenes.most_common()],
    ) if regimenes else ["_Ningún teaser en la muestra._"])

    L.append("")
    L.append(f"**{len(regimenes)} nombre(s) de teaser distinto(s).** "
             "`promo_regime_id` va vacío en el CSV a propósito: el ID del régimen "
             "(`ca697c7e-…`, *Bi Precio Vigente Regular MAKRO*) **no viaja en la "
             "respuesta de catálogo** — v2 lo leyó en `simulation`. Lo que el "
             "catálogo sí identifica es el nombre del teaser.")

    L.append("")
    L.append("## 8. `payment_method_id`")
    L.append("")

    pagos = Counter(f["payment_method_id"] for f in filas if f["payment_method_id"])

    L.extend(tabla_md(["payment_method_id", "SKUs"],
                      [[k, v] for k, v in pagos.most_common()])
             if pagos else ["_Sin condición de pago en la muestra._"])

    L.append("")
    L.append("v2 comprobó que este parámetro es **informativo, no restrictivo**: el "
             "descuento se aplicó en `simulation` sin enviar método de pago alguno.")

    L.append("")
    L.append("## 9. `vendido_por`")
    L.append("")

    vendidos = Counter(f["vendido_por"] or "(vacío)" for f in filas)

    L.extend(tabla_md(
        ["vendido_por", "SKUs", "%"],
        [[k, v, f"{v / total * 100:.1f}%"] for k, v in vendidos.most_common(10)],
    ))

    sellers = Counter(f["seller_chain"] or "(vacío)" for f in filas)

    L.append("")
    L.append(f"`seller_chain` en el catálogo (= `sellers[].sellerId`): "
             + " · ".join(f"`{k}` ×{v}" for k, v in sellers.most_common(5)))
    L.append("")
    L.append("`vendido_por` es una *specification* editorial del producto; el seller "
             "real es el del catálogo. La cadena completa (`1 > plazaveamko359`) solo "
             "aparece en `simulation`, que es la única llamada con contexto de "
             "sucursal. Que digan cosas distintas no es contradicción: describen "
             "capas distintas.")

    L.append("")
    L.append("## 10. `price_valid_until`")
    L.append("")

    def clase_fecha(valor: str) -> str:
        if not valor:
            return "vacío"
        if valor.startswith(SENTINELA_VTEX):
            return f"centinela {SENTINELA_VTEX}"
        if valor.startswith(SENTINELA_LEJANO):
            return f"centinela {SENTINELA_LEJANO}"

        return "fecha real"

    clases = Counter(clase_fecha(f["price_valid_until"]) for f in filas)

    L.extend(tabla_md(
        ["clase", "SKUs", "%"],
        [[k, v, f"{v / total * 100:.1f}%"] for k, v in clases.most_common()],
    ))

    reales = [f for f in filas if clase_fecha(f["price_valid_until"]) == "fecha real"]

    L.append("")
    L.append(f"`{SENTINELA_LEJANO}` se cuenta aparte de las fechas reales: un "
             "vencimiento en el año 2099 es otro *nunca vence*, no una campaña. "
             "Mezclarlos convertiría 79 SKUs en falsos positivos del único hallazgo "
             "temporal del barrido.")

    if reales:
        con_bi_fechado = [f for f in reales if f["biprecio_status"] == "COMPLETO"]

        L.append("")
        L.append(f"**{len(reales)} SKUs con fecha real**, "
                 f"de los cuales **{len(con_bi_fechado)} tienen bi-precio "
                 f"(`COMPLETO`)**.")
        L.append("")
        L.extend(tabla_md(
            ["fecha", "SKUs", "de esos con bi-precio"],
            [[fecha, cuenta,
              sum(1 for f in reales
                  if f["price_valid_until"].startswith(fecha)
                  and f["biprecio_status"] == "COMPLETO")]
             for fecha, cuenta in Counter(
                 f["price_valid_until"][:10] for f in reales).most_common()],
        ))
        L.append("")

        if con_bi_fechado:
            L.append("Estas son las primeras fechas de campaña visibles dentro de la "
                     "API **sobre precios mayoristas**: sirven para saber hasta cuándo "
                     "vale un tramo.")
        else:
            L.append("**Ninguna de esas fechas cae sobre un SKU con bi-precio.** O sea: "
                     "la API sí expone vencimientos, pero no para los precios "
                     "mayoristas — cuándo empieza o termina un tramo sigue siendo "
                     "invisible desde acá, y solo se reconstruye midiendo en el tiempo.")
    else:
        L.append("")
        L.append("Ninguna fecha real: en esta muestra la API **no expone fechas de "
                 "campaña**.")

    # ---------------- 11. límites ----------------
    L.append("")
    L.append("## 11. Límites de este análisis")
    L.append("")
    L.append(f"- **Muestra, no censo.** {total} SKUs de {len(categorias)} categorías de "
             "nivel 2, con techo de "
             f"{PAGINAS_POR_CATEGORIA} páginas ({PAGINAS_POR_CATEGORIA * VENTANA} SKUs) "
             "por categoría. Las categorías marcadas con *corte* en §1 quedaron "
             "parciales: sus porcentajes describen lo barrido, no la categoría.")
    L.append("- **Sin contexto de sucursal.** El catálogo se pide con `sc=9` y sin "
             "código postal: `node_id` es `SIN_CONTEXTO_SUCURSAL`. Que 359 y 360 "
             "tengan estos mismos precios y descuentos **no está verificado acá**.")
    L.append("- **Precio mayorista reconstruido, no medido.** La fórmula se validó "
             "contra checkout en **2 SKUs** (v2). Las filas `COMPLETO` de este CSV "
             "son la fórmula aplicada, no mediciones.")
    L.append("- **`tri_verificado = NO_VERIFICADO` en toda fila.** El segundo escalón "
             "se probó en un solo SKU (qty 1/3/5/6/7) y no apareció. Un SKU no prueba "
             "que `CantidadTriPrecioMK` sea inerte en los demás.")
    L.append("- **Los `SIN_DESCUENTO` no tienen precio mayorista desconocido \"por "
             "ahora\": lo tienen desconocido.** Declaran umbral y no traen teaser; "
             "para esos, el precio del tramo solo se obtiene midiendo con "
             "`simulation` a qty ≥ umbral.")
    L.append("- **Una foto de un momento.** Sin serie temporal no se puede distinguir "
             "un descuento estructural de una campaña que arranca mañana, y §10 "
             "muestra que la API no da fechas.")
    L.append("- **`promo_regime_id` y `matched_parameters` van vacíos**: no existen en "
             "la respuesta de catálogo. Quien los necesite tiene que pagar una "
             "`simulation`.")

    L.append("")
    L.append("---")
    L.append(f"_Generado por `test/v3.py` · run `{RUN_ID}` · "
             f"{gasto.get('requests', 0)} requests · "
             f"{len(ARCHIVADOS)} respuestas crudas archivadas._")

    SALIDA.mkdir(parents=True, exist_ok=True)
    MD_SALIDA.write_text("\n".join(L) + "\n", encoding="utf-8")

    return MD_SALIDA


# ===========================================================================


def parsear_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sonda v3: barrido de bi-precio por categoría (solo catálogo)."
    )

    parser.add_argument("--intervalo", type=float, default=1.5,
                        help="Segundos mínimos entre requests (default 1.5).")
    parser.add_argument("--reintentos", type=int, default=3,
                        help="Reintentos ante 429/5xx (default 3).")
    parser.add_argument("--canal", default="chrome",
                        help="Canal Playwright: chrome | msedge | chromium.")
    parser.add_argument("--headed", action="store_true",
                        help="Muestra el navegador.")

    parser.add_argument("--desde-evidencia", default="",
                        help="Marca de tiempo de una corrida ya archivada "
                             "(YYYYMMDD_HHMMSS): reconstruye CSV y análisis "
                             "desde raw/ sin tocar la red.")

    return parser.parse_args()


def reconstruir_desde_evidencia(marca: str) -> tuple[list[dict], list[dict], list[str], dict]:
    """
    Rehace CSV y análisis desde las respuestas crudas ya archivadas.

    Existe por la misma razón que `guardar_evidencia` en el motor: cuando el
    parseo tiene un defecto, arreglarlo no debería costar ni una request más.
    La evidencia es la fuente; el CSV es una lectura de ella.
    """

    archivos = sorted(DIR_RAW.glob(f"v3_catalogo_cat*_{marca}.json"))

    if not archivos:
        raise SystemExit(f"No hay evidencia de la corrida {marca} en {DIR_RAW}")

    filas: list[dict] = []
    vistos: set[str] = set()
    por_categoria: dict[str, dict] = {}
    duplicados = 0

    for archivo in archivos:
        paquete = json.loads(archivo.read_text(encoding="utf-8"))

        categoria = {
            "id": paquete["category_id"],
            "ruta": paquete["category_path"],
            "raiz": paquete["category_path"].split(" > ")[0],
        }

        resumen = por_categoria.setdefault(
            categoria["id"],
            {**categoria, "total_vtex": None, "barridos": 0,
             "paginas": 0, "incompleta_por": ""},
        )

        if paquete.get("pagina") == 0:
            resumen["total_vtex"] = MK.total_desde_resources(paquete.get("resources", ""))

        resumen["paginas"] += 1

        datos = paquete.get("respuesta")

        if not isinstance(datos, list):
            continue

        for crudo in datos:
            if not isinstance(crudo, dict):
                continue

            fila = construir_fila(crudo, categoria, paquete.get("momento", ""))
            resumen["barridos"] += 1

            if not fila["sku_id"]:
                continue

            if fila["sku_id"] in vistos:
                duplicados += 1
                continue

            vistos.add(fila["sku_id"])
            filas.append(fila)

    categorias = list(por_categoria.values())

    for resumen in categorias:
        total = resumen["total_vtex"]

        if total is not None and resumen["barridos"] < total:
            resumen["incompleta_por"] = "TECHO_DE_PAGINAS"

    gasto = {
        "requests": 0,
        "tope": TOPE_REQUESTS,
        "reintentos": 0,
        "duplicados": duplicados,
        "categorias_barridas": len(categorias),
        "reconstruida": True,
    }

    return filas, categorias, [], gasto


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

    if argumentos.desde_evidencia:
        global MARCA, RUN_ID, CSV_SALIDA, MD_SALIDA

        MARCA = argumentos.desde_evidencia
        RUN_ID = f"v3_{MARCA}"
        CSV_SALIDA = SALIDA / f"v3_biprecio_{MARCA}.csv"
        MD_SALIDA = SALIDA / f"v3_analisis_{MARCA}.md"

        print(f"Reconstruyendo desde la evidencia de {MARCA} (cero requests)...")

        filas, categorias, faltantes, gasto = reconstruir_desde_evidencia(MARCA)
    else:
        filas, categorias, faltantes, gasto = asyncio.run(barrer(argumentos))

    if not filas:
        print("El barrido no produjo filas. No se escribe CSV ni análisis.")
        return 1

    ruta_csv = escribir_csv(filas)
    ruta_md = escribir_md(filas, categorias, faltantes, gasto)

    print("")
    print(f"CSV      : {ruta_csv}  ({len(filas)} filas)")
    print(f"Análisis : {ruta_md}")
    print(f"Requests : {gasto.get('requests', 0)}/{TOPE_REQUESTS}"
          + ("  (reconstruido desde evidencia)" if gasto.get("reconstruida") else ""))

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.")
        sys.exit(130)
