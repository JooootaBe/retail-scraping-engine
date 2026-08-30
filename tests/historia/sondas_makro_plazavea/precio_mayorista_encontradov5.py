#!/usr/bin/env python3
"""
v5 — ESQUEMA COMPLETO, DOS NODOS, FORMATO LARGO
================================================

Sonda de validación del esquema propuesto para `SCHEMA_VERSION = 4`.
NO reimplementa el motor: lo importa y lo usa.

QUÉ PREGUNTA RESPONDE
---------------------
Antes de congelar 22 columnas nuevas en el motor, hay que saber cuáles se
llenan con datos reales. Una columna que sale vacía en 40 filas contra el
servidor de verdad no es una columna: es una intención. El entregable
principal de esta sonda NO es el CSV, es la tasa de llenado del MD.

QUÉ REUSA DEL MOTOR (sin tocarlo)
---------------------------------
    Cliente                intervalo, backoff, tope duro, contadores por fase
    NODOS / Nodo           firmas de sucursal y direcciones
    consultar_simulation   la medición qty=1
    construir_fila         LAS 56 COLUMNAS, con su cascada de veredictos
    extraer_item           precio / disponibilidad / sellerChain / unidad
    extraer_logistica      firma logística con contexto de selección de SLA
    identificar_nodo       búsqueda de firma (no "¿es el que mandé?")
    clasificar_fulfillment quién despacha de verdad
    evaluar_calidad        las tres reglas DQ
    parsear_producto       catálogo crudo -> Producto
    refrescar_stock_cadena stock de cadena batcheado
    guardar_evidencia      JSONL.gz crudo, como el motor

Lo único que esta sonda calcula por su cuenta son las 22 columnas nuevas.
Si algo de eso ya existía en el motor, es un bug de esta sonda.

FORMATO LARGO, UN SOLO CSV
--------------------------
Clave primaria `(run_id, node_id, sku_id)`. 20 SKUs x 2 nodos = 40 filas.
Nunca un CSV por sucursal, nunca `precio_359` / `precio_360`: eso obliga a
tocar el esquema cada vez que entra una sucursal, y este proyecto va a 20.

LAS DOS FUENTES DEL PRECIO MAYORISTA
------------------------------------
    catálogo    CantidadBiPrecioMK (umbral)                   sin sucursal
    simulation  precio verificado + descuento + firma logística  por sucursal

El umbral SOLO existe en el catálogo. El precio verificado SOLO existe en
la medición. Se unen por `sku_id`.

    precio_mayorista_cents = price_cents - descuento_monto_cents

Un solo escalón. `CantidadTriPrecioMK` se REGISTRA (`tri_umbral_declarado`)
y no se aplica: está declarado en el catálogo y v2 comprobó que checkout no
lo honra. Registrarlo sin aplicarlo es la única forma de que la próxima
persona no tenga que redescubrir eso.

DISCIPLINA
----------
  · `PromotionalPriceTableItemsDiscount` se busca POR NOMBRE a cualquier
    profundidad, en las tres formas que VTEX usa para el mismo dato
    (`Name`, `name`, `<Name>k__BackingField`). Nunca por índice de array.
  · Todo cálculo monetario en centavos enteros o `Decimal`. Ningún float
    toca un precio.
  · `biprecio_status != COMPLETO` -> `precio_mayorista*` y `bi_umbral`
    VACÍOS. Nunca cero, nunca el unitario repetido: un mayorista igual al
    unitario es una mentira que se filtra sola en una hoja de cálculo.
  · Vacío != cero. Vacío es desconocido, y el porqué vive en la columna de
    estado de al lado.
  · Ninguna fila se descarta. Las que no son surtido Makro se escriben
    marcadas `surtido_makro = NO`.
  · Celda vacía antes que valor inventado.

USO
---
    python3 test/v5.py
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import csv
import hashlib
import importlib.util
import re
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
MOTOR_PY = RAIZ /"src"/"retail_engine"/"collectors"/ "makro_plazavea.py"


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

# Los dos nodos, en orden estable. El CSV se ordena por (sku_id, node_id)
# para que cada SKU salga con sus dos filas pegadas y se puedan comparar
# de un vistazo.
NODOS = [MK.NODOS["359"], MK.NODOS["360"]]


# ===========================================================================
# PARÁMETROS DE LA CORRIDA
# ===========================================================================

TOPE_REQUESTS = 70
INTERVALO = 1.5
N_SKUS = 20
POR_CATEGORIA = 4
VENTANA = 30          # productos pedidos por categoría (1 request cada una)
N_AUDITORIA = 3

# Categorías objetivo por ruta de NOMBRES, resueltas contra el árbol vivo.
# Hardcodear el ID sería fijar hoy un número que VTEX puede reorganizar
# mañana sin avisar.
#
# Carnes y Frutas no son decorativas: son las únicas donde aparece
# `unit_multiplier != 1.0` (peso variable), que es la rama VTEX de la regla
# de presentación. Sin ellas, media columna nueva quedaría sin probar.
CATEGORIAS_OBJETIVO = [
    ("Abarrotes", "Snacks y Piqueos"),
    ("Bebidas", "Gaseosas"),
    ("Limpieza", "Limpieza de Cocina"),
    ("Carnes, Aves y Pescados", "Res"),
    ("Frutas y Verduras", "Frutas"),
]

PARAM_DESCUENTO = "PromotionalPriceTableItemsDiscount"
PARAM_PAGO = "PaymentMethodId"

MARCA = datetime.now().strftime("%Y%m%d_%H%M%S")
SALIDA = RAIZ / "data" / MK.MOTOR

CSV_SALIDA = SALIDA / f"v5_catalogo_completo_{MARCA}.csv"
MD_SALIDA = SALIDA / f"v5_analisis_{MARCA}.md"

# La evidencia cruda va donde la deja el motor, pero con un run_id que
# empieza con `v5_`: así una corrida de sonda nunca se puede confundir con
# una corrida del motor al leer `raw/`.
RUN_ID = f"v5_{MARCA}"

CERO = Decimal("0")
CENTAVO = Decimal("0.01")
CUATRO = Decimal("0.0001")


def log(mensaje: str = "") -> None:
    print(mensaje, flush=True)


# ===========================================================================
# DECIMAL — ningún float toca un precio
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


def entero(valor: Any) -> int | None:
    """Solo si el número es entero exacto. 2.5 unidades no es un umbral."""

    numero = dec(valor)

    if numero is None:
        return None

    try:
        return int(numero) if numero == numero.to_integral_value() else None
    except (InvalidOperation, ArithmeticError):
        return None


def money(valor: Decimal | None) -> str:
    return "" if valor is None else f"{valor.quantize(CENTAVO)}"


def money4(valor: Decimal | None) -> str:
    return "" if valor is None else f"{valor.quantize(CUATRO)}"


def a_centavos(valor: Decimal | None) -> int | None:
    """
    Soles -> centavos enteros. `None` si no cae exacto en el centavo.

    Un descuento de 0.205 soles no existe; si VTEX lo manda, es un dato que
    no entiendo y prefiero dejar la celda vacía a redondear en silencio.
    """

    if valor is None:
        return None

    escalado = valor * 100

    if escalado != escalado.to_integral_value():
        return None

    return int(escalado)


# ===========================================================================
# BÚSQUEDA POR NOMBRE (nunca por índice)
# ===========================================================================


def clave_normal(clave: Any) -> str:
    """
    `<Name>k__BackingField` -> `name`. `Name` -> `name`.

    VTEX devuelve el MISMO dato con tres serializaciones distintas según el
    endpoint (catálogo antiguo, catálogo nuevo, checkout). Normalizar la
    clave es lo que permite escribir una sola vez cada regla.
    """

    return str(clave).replace("k__BackingField", "").strip("<> ").strip().lower()


def parametros_por_nombre(objeto: Any, nombre: str) -> list[Any]:
    """
    Busca `{"Name": <nombre>, "Value": X}` a CUALQUIER profundidad.

    Nunca por índice: el orden de los parámetros del teaser lo decide VTEX.
    Anclar en `Parameters[1]` funciona hasta que agregan un parámetro
    adelante, y ese día se lee el ID del producto como si fuera un descuento.
    """

    encontrados: list[Any] = []

    def recorrer(nodo: Any) -> None:
        if isinstance(nodo, dict):
            claves = {clave_normal(k): k for k in nodo.keys()}

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


def campo(nodo: Any, nombre: str) -> str:
    """Un campo de un dict, tolerando las tres serializaciones de VTEX."""

    if not isinstance(nodo, dict):
        return ""

    for clave in nodo:
        if clave_normal(clave) == nombre:
            return MK.s(nodo[clave])

    return ""


def teasers_con_descuento(objeto: Any) -> list[dict]:
    """
    Todos los teasers que llevan un `PromotionalPriceTableItemsDiscount`.

    Se recorren TODAS las listas de teasers que existan en la respuesta
    (`teaser` en checkout, `PromotionTeasers` / `Teasers` en catálogo) y se
    filtra por contenido, no por dónde estaban: el régimen se identifica
    porque carga el descuento, no porque esté en la posición que esperaba.
    """

    candidatos: list[dict] = []

    for clave in ("teaser", "PromotionTeasers", "Teasers", "teasers"):
        for bloque in MK.buscar_todo(objeto, clave):
            if isinstance(bloque, list):
                candidatos.extend(t for t in bloque if isinstance(t, dict))
            elif isinstance(bloque, dict):
                candidatos.append(bloque)

    return [t for t in candidatos if parametros_por_nombre(t, PARAM_DESCUENTO)]


def leer_regimen(objeto: Any) -> dict[str, str]:
    """
    Régimen promocional que gobierna el bi-precio: id, nombre, medio de pago.

    El catálogo trae el teaser sin `id` (solo nombre); `simulation` trae el
    mismo teaser CON `id`. Por eso la medición es la fuente preferida y el
    catálogo el respaldo: entre las dos, la columna se llena.
    """

    salida = {"promo_regime_id": "", "promo_regime_name": "", "payment_method_id": ""}

    for teaser in teasers_con_descuento(objeto):
        salida["promo_regime_id"] = salida["promo_regime_id"] or campo(teaser, "id")
        salida["promo_regime_name"] = salida["promo_regime_name"] or campo(teaser, "name")

        pagos = parametros_por_nombre(teaser, PARAM_PAGO)

        if pagos and not salida["payment_method_id"]:
            salida["payment_method_id"] = MK.s(pagos[0])

    # Respaldo del id: la promoción aplicada de verdad aparece en
    # `rateAndBenefitsIdentifiers` cuando la cantidad alcanza el umbral.
    if not salida["promo_regime_id"]:
        for bloque in MK.buscar_todo(objeto, "rateAndBenefitsIdentifiers"):
            for regimen in bloque if isinstance(bloque, list) else []:
                nombre = campo(regimen, "name")

                if "PRECIO" in nombre.upper().replace(" ", ""):
                    salida["promo_regime_id"] = campo(regimen, "id")
                    salida["promo_regime_name"] = salida["promo_regime_name"] or nombre
                    break

    return salida


def leer_descuento(objeto: Any) -> Decimal | None:
    """El monto de descuento por unidad, en soles. `None` si no está."""

    for valor in parametros_por_nombre(objeto, PARAM_DESCUENTO):
        numero = dec(valor)

        if numero is not None:
            return numero

    return None


# ===========================================================================
# EAN — tipo y dígito verificador
# ===========================================================================


def clasificar_ean(ean: str) -> str:
    """
    GS1_GLOBAL | INTERNO_RESTRINGIDO | FALTANTE | INVALIDO

    Por qué importa en un motor de precios: un EAN interno (prefijo 20-29)
    es un código que la propia tienda se inventó — sirve dentro de Makro y
    NO sirve para cruzar el producto contra otro retailer. Tratar los dos
    como "el EAN" arruina cualquier match de catálogo cruzado, y el
    porcentaje de internos es justo lo que hay que saber antes de prometer
    ese cruce.

    El dígito verificador es GS1 estándar (mod 10, pesos 3/1 desde la
    derecha) y aplica igual a EAN-8, UPC-A, EAN-13 y GTIN-14.
    """

    limpio = (ean or "").strip()

    if not limpio:
        return "FALTANTE"

    if not limpio.isdigit() or len(limpio) not in (8, 12, 13, 14):
        return "INVALIDO"

    cuerpo, verificador = limpio[:-1], int(limpio[-1])

    suma = 0

    for posicion, digito in enumerate(reversed(cuerpo)):
        suma += int(digito) * (3 if posicion % 2 == 0 else 1)

    if (10 - suma % 10) % 10 != verificador:
        return "INVALIDO"

    # Prefijo 20-29: rango reservado por GS1 para numeración interna del
    # comercio. No es global aunque el dígito verificador cierre.
    prefijo = limpio[:2] if len(limpio) >= 13 else limpio[:2].zfill(2)

    if len(limpio) >= 12 and prefijo.isdigit() and 20 <= int(prefijo) <= 29:
        return "INTERNO_RESTRINGIDO"

    return "GS1_GLOBAL"


# ===========================================================================
# PRESENTACIÓN — regla de dos ramas
# ===========================================================================

# Factor a la unidad base canónica (kg o l).
UNIDADES: dict[str, tuple[str, Decimal]] = {
    "kg": ("kg", Decimal("1")),
    "kgs": ("kg", Decimal("1")),
    "kilo": ("kg", Decimal("1")),
    "kilos": ("kg", Decimal("1")),
    "kilogramo": ("kg", Decimal("1")),
    "kilogramos": ("kg", Decimal("1")),
    "g": ("kg", Decimal("0.001")),
    "gr": ("kg", Decimal("0.001")),
    "grs": ("kg", Decimal("0.001")),
    "gramo": ("kg", Decimal("0.001")),
    "gramos": ("kg", Decimal("0.001")),
    "mg": ("kg", Decimal("0.000001")),
    "l": ("l", Decimal("1")),
    "lt": ("l", Decimal("1")),
    "lts": ("l", Decimal("1")),
    "litro": ("l", Decimal("1")),
    "litros": ("l", Decimal("1")),
    "ml": ("l", Decimal("0.001")),
    "cc": ("l", Decimal("0.001")),
}

# Palabras que cuentan piezas dentro de un empaque ("x 12un", "12 Bolsas").
ENVASES = r"(?:un|und|unid|unidades|u|pack|packs|bolsas?|botellas?|latas?|sobres?|paquetes?|cajas?|frascos?|barras?|piezas?)"

MEDIDA = r"(\d+(?:[.,]\d+)?)\s*(" + "|".join(sorted(UNIDADES, key=len, reverse=True)) + r")\b"

# "26g x 12un"  /  "473ml x 6"
RE_MULTI_A = re.compile(MEDIDA + r"\s*(?:x|por)\s*(\d+)\s*" + ENVASES + r"?", re.I)
# "12 Bolsas 17g"  /  "6 botellas de 473ml"
RE_MULTI_B = re.compile(r"(?:x\s*)?(\d+)\s*" + ENVASES + r"\s*(?:de\s*)?" + MEDIDA, re.I)
# "600ml Paquete 6un"  /  "4L Paquete 4un" — el conteo va DESPUÉS de la
# medida y sin `x` de por medio. El relleno entre ambos no puede contener
# dígitos: en "Deli 1Lt D2 Sf Pp 25un" ese "D2" es un código de producto, y
# sin el corte la regla ataría el litro con las 25 unidades cruzando basura.
RE_MULTI_C = re.compile(MEDIDA + r"[^\d]{0,18}?(\d+)\s*" + ENVASES + r"\b", re.I)
# "5Kg", "4.8kg", "500g", "473ml"
RE_SIMPLE = re.compile(MEDIDA, re.I)
# Solo conteo: "x 12un" sin peso ni volumen en ningún lado.
RE_CONTEO = re.compile(r"(?:x\s*)?(\d+)\s*" + ENVASES + r"\b", re.I)


def _medida(cantidad: str, unidad: str, veces: str = "1") -> tuple[str, Decimal] | None:
    numero = dec(cantidad.replace(",", "."))
    multiplo = dec(veces)
    base = UNIDADES.get(unidad.lower())

    if numero is None or multiplo is None or base is None:
        return None

    total = numero * base[1] * multiplo

    return None if total <= CERO else (base[0], total)


def parsear_presentacion_nombre(nombre: str) -> tuple[str, Decimal, str] | None:
    """
    Heurística: saca la presentación del NOMBRE del producto.

    Es heurística y se etiqueta como tal (`presentacion_origen = NOMBRE`).
    Un analista tiene que poder filtrar `presentacion_origen = 'VTEX'` y
    quedarse solo con lo que el servidor afirmó.

    El caso multipack se multiplica a propósito: el precio que trae la fila
    es el del empaque completo, así que "26g x 12un" son 312 g y no 26. Si
    no se multiplicara, el precio por kilo de todo pack saldría 12 veces
    más caro de lo que es.
    """

    texto = (nombre or "").strip()

    if not texto:
        return None

    for expresion, orden, regla in (
        (RE_MULTI_A, "medida_primero", "multipack_medida_x_conteo"),
        (RE_MULTI_B, "conteo_primero", "multipack_conteo_medida"),
        (RE_MULTI_C, "medida_primero", "multipack_medida_conteo"),
    ):
        encontrados = expresion.findall(texto)

        if not encontrados:
            continue

        crudo = encontrados[-1]

        if orden == "medida_primero":
            resultado = _medida(crudo[0], crudo[1], crudo[2])
        else:
            resultado = _medida(crudo[1], crudo[2], crudo[0])

        if resultado:
            return resultado[0], resultado[1], regla

    simples = RE_SIMPLE.findall(texto)

    if simples:
        resultado = _medida(simples[-1][0], simples[-1][1])

        if resultado:
            return resultado[0], resultado[1], "medida_simple"

    # Sin peso ni volumen: si el nombre declara un conteo de piezas, la
    # unidad base es la pieza. Es menos informativo que un kilo, pero es
    # verdad, y hace comparable un pack de 12 contra uno de 6.
    conteos = RE_CONTEO.findall(texto)

    if conteos:
        cantidad = dec(conteos[-1])

        if cantidad and cantidad > CERO:
            return "un", cantidad, "solo_conteo"

    return None


def resolver_presentacion(
    measurement_unit: str,
    unit_multiplier: str,
    nombre: str,
) -> tuple[str, str, str, str]:
    """
    Regla de dos ramas. Devuelve (unidad_base, cantidad_base, origen, regla).

    `regla` no es una columna del CSV: es trazabilidad para el análisis.
    Sirve para separar en el MD lo que salió de una medida escrita tal cual
    ("500g") de lo que salió de multiplicar un multipack, que es donde el
    parser puede equivocarse sin que se note.

    Rama VTEX  — `measurement_unit != 'un'`: el producto se vende por peso o
                 volumen variable (carnes, frutas, verduras) y VTEX declara
                 el tamaño real de la pieza en `unit_multiplier`. Es
                 AUTORITATIVO: nadie va a adivinar mejor que el servidor
                 cuánto pesa el trozo que se despacha.
    Rama NOMBRE — `measurement_unit == 'un'`: el producto es envasado y su
                 tamaño solo vive en el texto del nombre. Heurística.
    Ninguna     — DESCONOCIDO y las derivadas VACÍAS. Un `1` inventado en
                 `cantidad_base` convierte el precio del empaque en un
                 "precio por kilo" falso, que es peor que no tenerlo.
    """

    unidad = (measurement_unit or "").strip().lower()

    if unidad and unidad != "un":
        multiplicador = dec(unit_multiplier)
        base = UNIDADES.get(unidad)

        if multiplicador is not None and multiplicador > CERO and base:
            return base[0], f"{(multiplicador * base[1]).normalize():f}", "VTEX", "vtex"

        # measurement_unit raro y sin tabla: no se inventa una equivalencia.
        return "", "", "DESCONOCIDO", "unidad_no_reconocida"

    parseado = parsear_presentacion_nombre(nombre)

    if parseado:
        return parseado[0], f"{parseado[1].normalize():f}", "NOMBRE", parseado[2]

    return "", "", "DESCONOCIDO", "sin_medida_en_el_nombre"


# ===========================================================================
# COLUMNAS
# ===========================================================================

# Las 56 del motor, tal cual, sin quitar ninguna: si esta sonda produjera
# un subconjunto, el CSV no probaría el esquema que se quiere congelar.
COLUMNAS_MOTOR = list(MK.COLUMNAS)

COLUMNAS_MAYORISTA = [
    "bi_umbral",
    "tri_umbral_declarado",
    "descuento_monto",
    "descuento_monto_cents",
    "precio_mayorista",
    "precio_mayorista_cents",
    "descuento_mayorista_pct",
    "biprecio_status",
    "precio_mayorista_verificado",
]

COLUMNAS_PROMO = [
    "promo_regime_id",
    "promo_regime_name",
    "payment_method_id",
    "price_valid_until",
]

COLUMNAS_IDENTIDAD = [
    "ean_type",
    "category_id",
    "sales_channel",
    "surtido_makro",
]

COLUMNAS_PRESENTACION = [
    "unidad_base",
    "cantidad_base",
    "presentacion_origen",
    "precio_por_unidad_base",
    "precio_mayorista_por_unidad_base",
]

COLUMNAS_NUEVAS = (
    COLUMNAS_MAYORISTA + COLUMNAS_PROMO + COLUMNAS_IDENTIDAD + COLUMNAS_PRESENTACION
)

COLUMNAS = COLUMNAS_MOTOR + COLUMNAS_NUEVAS


# ===========================================================================
# FASE 1 — CATÁLOGO (umbral, tri, descuento declarado, category_id)
# ===========================================================================


def leer_catalogo(crudo: dict, categoria: dict,
                  producto: Any = None) -> dict[str, Any] | None:
    """
    Lo que SOLO el catálogo sabe, más el `Producto` del motor.

    `parsear_producto` es del motor y ya descarta basura y productos sin
    stock de cadena. Acá encima se leen los tres campos que el checkout no
    devuelve: el umbral bi, el umbral tri y la ruta de IDs de categoría.

    `producto` se puede pasar ya armado: el modo `--skus` necesita medir
    también lo que `parsear_producto` descartaría (ver `producto_forzado`),
    y todo lo demás de esta función se lee igual.
    """

    if producto is None:
        producto = MK.parsear_producto(crudo)

    if not producto or not producto.sku_id:
        return None

    # El catálogo YA declara la unidad de medida y el multiplicador; leerlos
    # acá permite priorizar el peso variable en la selección sin gastar una
    # medición para descubrirlo.
    items = crudo.get("items") or []
    item = items[0] if items else {}

    def especificacion(nombre: str) -> Any:
        valor = crudo.get(nombre)

        if isinstance(valor, list):
            return valor[0] if valor else None

        return valor

    # `categoriesIds` trae todas las rutas; la más larga es la más
    # específica, y es la que identifica al producto sin depender del texto
    # (que VTEX renombra sin avisar).
    rutas = [MK.s(r) for r in (crudo.get("categoriesIds") or []) if MK.s(r)]
    category_id = max(rutas, key=len) if rutas else ""

    return {
        "producto": producto,
        "sku_id": producto.sku_id,
        "bi_umbral": entero(especificacion("CantidadBiPrecioMK")),
        "tri_umbral": entero(especificacion("CantidadTriPrecioMK")),
        "measurement_unit": MK.s(item.get("measurementUnit")),
        "unit_multiplier": MK.s(item.get("unitMultiplier")),
        "descuento_catalogo": leer_descuento(crudo),
        "regimen_catalogo": leer_regimen(crudo),
        "category_id": category_id,
        "categoria": categoria["ruta_nombres"],
    }


def aplanar(arbol: Any, ruta: list[str], ruta_ids: list[str], nivel: int,
            acumulado: list[dict]) -> list[dict]:
    """Igual que `MK.aplanar_categorias`, pero conservando los NOMBRES.

    El motor solo necesita la ruta de IDs para el `fq=C:/...`. Acá hace
    falta además la ruta de nombres, porque las categorías objetivo se
    declaran por nombre a propósito (ver CATEGORIAS_OBJETIVO).
    """

    for rama in arbol or []:
        if not isinstance(rama, dict) or not rama.get("id"):
            continue

        nombre = MK.s(rama.get("name"))
        id_categoria = MK.s(rama.get("id"))

        camino = ruta + [nombre]
        camino_ids = ruta_ids + [id_categoria]

        acumulado.append(
            {
                "id": id_categoria,
                "ruta_nombres": tuple(camino),
                "ruta": " > ".join(camino),
                "ruta_ids": "/" + "/".join(camino_ids) + "/",
            }
        )

        aplanar(rama.get("children") or [], camino, camino_ids, nivel + 1, acumulado)

    return acumulado


def orden_estable(sku_id: str) -> str:
    """Desempate determinista, mismo criterio que `seleccionar` del motor."""

    return hashlib.md5(f"v5:{sku_id}".encode()).hexdigest()


def es_peso_variable(registro: dict) -> bool:
    """VTEX declara el tamaño de la pieza: carnes, frutas, verduras a granel."""

    if (registro["measurement_unit"] or "un") == "un":
        return False

    multiplicador = dec(registro["unit_multiplier"])

    return multiplicador is not None and multiplicador != Decimal("1")


def elegir_de_categoria(
    candidatos: list[dict],
    cuantos: int,
    cuota_peso: int = 2,
) -> list[dict]:
    """
    Prioriza lo que hace falta PROBAR, no lo que hace falta contar.

    La CUOTA DE PESO VARIABLE va primero, y por una razón concreta: en la
    primera corrida de esta sonda el criterio era solo "primero los que
    tienen bi-precio", y las cuatro plazas de Carnes se las llevaron cuatro
    empaques de 500 g. Salieron 40 filas impecables con `unit_multiplier`
    igual a 1.0 en las 40 — o sea, la rama VTEX de la regla de presentación
    quedó sin probar justo en las dos categorías que se agregaron para
    probarla. Un criterio que optimiza el conteo puede dejar sin evidencia
    exactamente lo que se quería medir.

    Después de la cuota:

    1. con umbral Y descuento -> son los únicos que pueden dar
       `biprecio_status = COMPLETO`, que es el caso que valida 9 columnas.
    2. el resto               -> el denominador. Sin filas sin bi-precio no
       se puede saber si `SIN_BIPRECIO` se distingue de un fallo del parser.

    Dentro de cada grupo el orden es el hash, no el que VTEX devolvió: así
    dos corridas del mismo día eligen lo mismo.
    """

    def con_biprecio(registro: dict) -> bool:
        return (
            registro["bi_umbral"] is not None
            and registro["descuento_catalogo"] is not None
        )

    # Dentro de la cuota, los que además traen bi-precio: cubren las dos
    # cosas con una sola plaza.
    pesados = sorted(
        (r for r in candidatos if es_peso_variable(r)),
        key=lambda r: (not con_biprecio(r), orden_estable(r["sku_id"])),
    )

    elegidos = pesados[: min(cuota_peso, cuantos)]
    ya = {r["sku_id"] for r in elegidos}

    resto = sorted(
        (r for r in candidatos if r["sku_id"] not in ya),
        key=lambda r: (not con_biprecio(r), orden_estable(r["sku_id"])),
    )

    elegidos.extend(resto[: cuantos - len(elegidos)])

    return elegidos


# ===========================================================================
# FASE 2 — MEDICIÓN Y ENRIQUECIMIENTO
# ===========================================================================


def leer_price_valid_until(datos: Any, sku_id: str) -> str:
    """`priceValidUntil` del ítem pedido, no del primero que aparezca."""

    items = datos.get("items") if isinstance(datos, dict) else None

    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and MK.s(item.get("id")) == sku_id:
            return MK.s(item.get("priceValidUntil"))

    return ""


def calcular_mayorista(
    price_cents: int | None,
    umbral: int | None,
    descuento: Decimal | None,
) -> dict[str, Any]:
    """
    El único lugar donde se decide si hay precio mayorista y por qué.

    Devuelve el estado SIEMPRE, y los números solo cuando el estado es
    COMPLETO. Esa asimetría es deliberada: el estado explica la celda vacía
    de al lado, así que nunca puede faltar.
    """

    descuento_cents = a_centavos(descuento)

    if price_cents is None:
        estado = "SIN_MEDICION"

    elif umbral is None and descuento is None:
        estado = "SIN_BIPRECIO"

    elif umbral is not None and descuento is None:
        estado = "SIN_DESCUENTO"

    elif descuento is not None and umbral is None:
        estado = "SIN_UMBRAL"

    elif descuento_cents is None:
        # El descuento no cae exacto en el centavo: no se redondea, se
        # marca. Redondear acá sería inventar medio centavo por unidad.
        estado = "INCONSISTENTE"

    else:
        mayorista_cents = price_cents - descuento_cents

        inconsistente = (
            mayorista_cents <= 0
            or mayorista_cents > price_cents
            or umbral < 2          # un "bi-precio" a partir de 1 unidad no es un bi-precio
        )

        estado = "INCONSISTENTE" if inconsistente else "COMPLETO"

    if estado != "COMPLETO":
        return {
            "estado": estado,
            "descuento_cents": descuento_cents,
            "descuento": descuento,
            "mayorista_cents": None,
        }

    return {
        "estado": estado,
        "descuento_cents": descuento_cents,
        "descuento": descuento,
        "mayorista_cents": price_cents - descuento_cents,
    }


def enriquecer(fila: Any, registro: dict, nodo: Any, datos: Any) -> dict[str, str]:
    """
    Toma la `Fila` del motor (56 columnas) y le añade las 22 nuevas.

    La `Fila` NO se modifica: se serializa tal cual salió de
    `construir_fila`. Si esta función tocara una columna del motor, el CSV
    dejaría de ser una prueba de que el motor y el esquema nuevo conviven.
    """

    salida = dict(fila.__dict__)

    producto = registro["producto"]

    # ---- MAYORISTA ------------------------------------------------------
    #
    # El descuento se lee PRIMERO de la medición (es por sucursal y está
    # vivo) y solo si no vino, del catálogo (sin contexto de sucursal). El
    # umbral solo existe en el catálogo.
    descuento = leer_descuento(datos)
    fuente_descuento = "simulation"

    if descuento is None:
        descuento = registro["descuento_catalogo"]
        fuente_descuento = "catalogo" if descuento is not None else ""

    price_cents = entero(salida.get("price_cents"))
    umbral = registro["bi_umbral"]

    veredicto = calcular_mayorista(price_cents, umbral, descuento)
    completo = veredicto["estado"] == "COMPLETO"

    mayorista_cents = veredicto["mayorista_cents"]
    mayorista = Decimal(mayorista_cents) / 100 if mayorista_cents is not None else None

    pct = None

    if veredicto["descuento_cents"] is not None and price_cents:
        pct = Decimal(veredicto["descuento_cents"]) / Decimal(price_cents) * 100

    salida.update(
        {
            # Regla dura: sin COMPLETO, el umbral tampoco se publica. Un
            # umbral suelto invita a construir el mayorista a mano.
            "bi_umbral": str(umbral) if (completo and umbral is not None) else "",
            # El tri se REGISTRA siempre que exista, y no se aplica nunca.
            "tri_umbral_declarado": (
                str(registro["tri_umbral"]) if registro["tri_umbral"] is not None else ""
            ),
            # El descuento es un hecho de VTEX: se publica aunque el
            # mayorista no se pueda armar. Es lo que hace diagnosticable un
            # SIN_UMBRAL.
            "descuento_monto": money(veredicto["descuento"]),
            "descuento_monto_cents": (
                str(veredicto["descuento_cents"])
                if veredicto["descuento_cents"] is not None
                else ""
            ),
            "precio_mayorista": money(mayorista) if completo else "",
            "precio_mayorista_cents": str(mayorista_cents) if completo else "",
            "descuento_mayorista_pct": (
                f"{pct.quantize(CENTAVO)}" if (completo and pct is not None) else ""
            ),
            "biprecio_status": veredicto["estado"],
            # Se rellena en la fase de auditoría. Vacío mientras no haya
            # mayorista que verificar.
            "precio_mayorista_verificado": "NO" if completo else "",
        }
    )

    # ---- RÉGIMEN PROMOCIONAL -------------------------------------------
    regimen = leer_regimen(datos)
    respaldo = registro["regimen_catalogo"]

    salida.update(
        {
            "promo_regime_id": regimen["promo_regime_id"] or respaldo["promo_regime_id"],
            "promo_regime_name": regimen["promo_regime_name"] or respaldo["promo_regime_name"],
            "payment_method_id": regimen["payment_method_id"] or respaldo["payment_method_id"],
            "price_valid_until": leer_price_valid_until(datos, producto.sku_id),
        }
    )

    # ---- IDENTIDAD Y CALIDAD -------------------------------------------
    cadena = salida.get("seller_chain", "")

    salida.update(
        {
            "ean_type": clasificar_ean(producto.ean),
            "category_id": registro["category_id"],
            "sales_channel": MK.SALES_CHANNEL,
            # La ÚNICA prueba de surtido Makro de ESTA sucursal.
            "surtido_makro": "SI" if f"plazaveamko{nodo.node_id}" in cadena else "NO",
        }
    )

    # ---- PRESENTACIÓN ---------------------------------------------------
    unidad_base, cantidad_base, origen, regla = resolver_presentacion(
        salida.get("measurement_unit", ""),
        salida.get("unit_multiplier", ""),
        producto.product_name or salida.get("product_name", ""),
    )

    cantidad = dec(cantidad_base)

    por_unidad = ""
    mayorista_por_unidad = ""

    if cantidad and cantidad > CERO:
        if price_cents:
            por_unidad = money4(Decimal(price_cents) / 100 / cantidad)

        if completo and mayorista_cents is not None:
            mayorista_por_unidad = money4(Decimal(mayorista_cents) / 100 / cantidad)

    salida.update(
        {
            "unidad_base": unidad_base,
            "cantidad_base": cantidad_base,
            "presentacion_origen": origen,
            "precio_por_unidad_base": por_unidad,
            "precio_mayorista_por_unidad_base": mayorista_por_unidad,
        }
    )

    # Columnas de trabajo del análisis: el `_` las mantiene fuera de
    # COLUMNAS, así que no llegan al CSV.
    salida["_fuente_descuento"] = fuente_descuento
    salida["_regla_presentacion"] = regla

    return salida


# ===========================================================================
# FASE 3 — AUDITORÍA DEL PRECIO MAYORISTA
# ===========================================================================


def elegir_auditables(filas: list[dict], node_id: str, cuantos: int) -> list[dict]:
    """
    Tres SKUs COMPLETO del nodo dado, cubriendo el RANGO de umbrales.

    Uno de umbral bajo (2-3) y uno de umbral alto (12-24): si los tres
    salieran del mismo umbral, la auditoría solo probaría que la fórmula
    funciona para ese umbral. El riesgo real es que VTEX aplique otra regla
    (otro escalón, otro redondeo) cuando la cantidad crece.
    """

    disponibles = [
        f for f in filas
        if f["node_id"] == node_id
        and f["biprecio_status"] == "COMPLETO"
        and entero(f["bi_umbral"])
    ]

    if not disponibles:
        return []

    por_umbral = sorted(disponibles, key=lambda f: (entero(f["bi_umbral"]), f["sku_id"]))

    elegidos: list[dict] = []

    bajos = [f for f in por_umbral if 2 <= entero(f["bi_umbral"]) <= 3]
    altos = [f for f in por_umbral if 12 <= entero(f["bi_umbral"]) <= 24]

    if bajos:
        elegidos.append(bajos[0])

    if altos and altos[-1] not in elegidos:
        elegidos.append(altos[-1])

    # Si el rango pedido no existe en la muestra, se completa con los
    # extremos disponibles, prefiriendo umbrales todavía no cubiertos: tres
    # mediciones al mismo umbral solo prueban que la fórmula funciona para
    # ese umbral, que es la mitad de lo que la auditoría tiene que decir.
    cubiertos = {entero(f["bi_umbral"]) for f in elegidos}

    for preferir_nuevo in (True, False):
        for candidato in [por_umbral[-1], por_umbral[0]] + por_umbral:
            if len(elegidos) >= cuantos:
                break

            if candidato in elegidos:
                continue

            umbral = entero(candidato["bi_umbral"])

            if preferir_nuevo and umbral in cubiertos:
                continue

            elegidos.append(candidato)
            cubiertos.add(umbral)

    return elegidos[:cuantos]


async def auditar_mayorista(cliente, fila: dict, nodo, registros: dict) -> dict:
    """
    Mide el MISMO SKU a `qty = bi_umbral` y compara contra lo reconstruido.

    Es la única prueba de que `price_cents - descuento` es lo que el
    cliente pagaría de verdad. Todo lo demás son dos números del catálogo
    restados con confianza.
    """

    umbral = entero(fila["bi_umbral"])
    producto = registros[fila["sku_id"]]["producto"]

    resultado = {
        "sku_id": fila["sku_id"],
        "product_name": fila["product_name"],
        "umbral": umbral,
        "unitario_cents": entero(fila["price_cents"]),
        "reconstruido_cents": entero(fila["precio_mayorista_cents"]),
        "real_cents": None,
        "coincide": "",
        "detalle": "",
    }

    cuerpo = {
        "items": [
            {
                "id": int(producto.sku_id),
                "quantity": umbral,
                "seller": producto.seller_id or "1",
            }
        ],
        "country": nodo.country,
        "postalCode": nodo.postal_code,
        "geoCoordinates": [nodo.longitude, nodo.latitude],
    }

    try:
        status, datos, _ = await cliente.pedir(
            f"{MK.BASE_URL}/api/checkout/pub/orderforms/simulation?sc={MK.SALES_CHANNEL}",
            metodo="POST",
            body=cuerpo,
        )
    except MK.TopeAgotadoError:
        resultado["detalle"] = "sin presupuesto de requests"
        return resultado
    except Exception as exc:
        resultado["detalle"] = f"{type(exc).__name__}"
        return resultado

    MK.guardar_evidencia(producto, nodo, f"simulation_qty{umbral}", status, datos)

    if status >= 400:
        resultado["detalle"] = f"HTTP {status}"
        return resultado

    item = MK.extraer_item(datos, producto)
    real = entero(item.get("sellingPrice"))

    resultado["real_cents"] = real

    if real is None:
        resultado["detalle"] = "simulation no devolvió sellingPrice"
        return resultado

    resultado["coincide"] = "SI" if real == resultado["reconstruido_cents"] else "NO"

    if resultado["coincide"] == "NO":
        resultado["detalle"] = (
            f"diferencia {real - (resultado['reconstruido_cents'] or 0)} centavos"
        )

    return resultado


# ===========================================================================
# ORQUESTACIÓN
# ===========================================================================


# ===========================================================================
# FASE 1 — MODO --skus: LISTA FIJA, SIN DESCUBRIMIENTO
# ===========================================================================
#
# Por qué existe
# --------------
# El descubrimiento elige los SKUs con `elegir_de_categoria`, que depende
# del catálogo VIVO: dos corridas separadas por días no eligen lo mismo, así
# que no se puede volver a medir una corrida vieja. Para comparar contra un
# golden hace falta lo contrario: los SKUs entran por parámetro y el
# catálogo solo se consulta para los campos que únicamente él conoce
# (umbral bi/tri, descuento declarado, category_id).
#
# Esto NO reintroduce el "archivo de entrada" que v11 sacó del motor: la
# lista viaja en la línea de comandos, no en disco, y solo la usa esta
# sonda. El motor sigue descubriendo cada corrida.

LOTE_SKUS = 10        # `fq=skuId:` se acumulan en una sola consulta


def parsear_lista_skus(texto: str) -> list[str]:
    """Acepta comas, espacios y saltos de línea; conserva el orden y dedupe."""

    crudos = [t.strip() for t in re.split(r"[,\s]+", texto or "") if t.strip()]

    vistos: set[str] = set()
    lista: list[str] = []

    for sku in crudos:
        if sku in vistos:
            continue

        vistos.add(sku)
        lista.append(sku)

    return lista


def ordenar_items(crudo: dict, sku_id: str) -> bool:
    """
    Pone el SKU pedido en `items[0]`, que es donde el motor lo va a buscar.

    `fq=skuId:` filtra por SKU pero devuelve el PRODUCTO entero, con todas
    sus variantes. `parsear_producto` lee `items[0]`: sin este reordenamiento
    un producto multivariante se mediría en la variante equivocada y la fila
    saldría con otro `sku_id` — parecería una diferencia contra el golden y
    sería un bug de esta sonda.
    """

    items = crudo.get("items") or []

    for posicion, item in enumerate(items):
        if MK.s(item.get("itemId")) == sku_id:
            if posicion:
                items.insert(0, items.pop(posicion))

            return True

    return False


def producto_forzado(crudo: dict) -> Any:
    """
    El `Producto` del motor, saltando el filtro de stock de cadena.

    `parsear_producto` descarta lo que la cadena no tiene disponible. Al
    DESCUBRIR eso es correcto —no tiene sentido medir lo que nadie puede
    comprar—, pero al REMEDIR es al revés: un SKU del golden que se quedó
    sin stock hay que medirlo igual, porque perder la fila esconde
    justamente el cambio que se quería ver.

    La disponibilidad se fuerza sobre una COPIA del crudo y después se borra
    `stock_catalog`, para no dejar registrado un número que el servidor no
    dijo. La disponibilidad real de la corrida sigue saliendo de la medición
    (`availability`) y del stock de cadena refrescado (`chain_stock`).
    """

    copia = copy.deepcopy(crudo)

    items = copia.get("items") or []

    if not items:
        return None

    vendedores = items[0].get("sellers") or []

    if not vendedores:
        return None

    oferta = vendedores[0].setdefault("commertialOffer", {})

    if not oferta.get("AvailableQuantity"):
        oferta["AvailableQuantity"] = 1

    producto = MK.parsear_producto(copia)

    if producto is not None:
        producto.stock_catalog = ""

    return producto


async def traer_por_skus(cliente, sku_ids: list[str],
                         estado: dict) -> list[dict] | None:
    """
    FASE 1 del modo `--skus`: el catálogo de una lista fija, en pocas requests.

    Mismo truco que `refrescar_stock_cadena` del motor: varios `fq=` en una
    sola consulta. 20 SKUs salen en 2 requests en vez de 20, que es lo que
    deja presupuesto para las 40 mediciones.

    Todo SKU que no vuelva queda anotado en `avisos`. Un SKU que desaparece
    del catálogo no es un error de la sonda: es el dato.
    """

    log(f"FASE 1 — catálogo de {len(sku_ids)} SKUs fijos (sin descubrimiento)")

    categoria = {"ruta_nombres": ("(lista fija)",), "ruta": "(lista fija)"}

    por_sku: dict[str, dict] = {}
    forzados: list[str] = []

    lotes = [sku_ids[i:i + LOTE_SKUS] for i in range(0, len(sku_ids), LOTE_SKUS)]

    for numero, lote in enumerate(lotes, 1):
        filtros = "&".join(f"fq=skuId:{sku}" for sku in lote)

        url = (
            f"{MK.BASE_URL}/api/catalog_system/pub/products/search"
            f"?{filtros}&_from=0&_to={len(lote) * 2 - 1}&sc={MK.SALES_CHANNEL}"
        )

        try:
            status, datos, _ = await cliente.pedir(url)
        except MK.TopeAgotadoError:
            # Igual que en el motor: el presupuesto agotado es una condición
            # de CORRIDA. No se inventa un catálogo a medias.
            raise
        except Exception as exc:
            estado["avisos"].append(
                f"Lote {numero}: {type(exc).__name__} al pedir el catálogo."
            )
            continue

        if status >= 400 or not isinstance(datos, list):
            estado["avisos"].append(f"Lote {numero}: HTTP {status}.")
            continue

        pedidos = set(lote)

        for crudo in datos:
            if not isinstance(crudo, dict):
                continue

            # Un producto puede traer varias variantes; solo interesan las
            # que se pidieron, y cada una se lee con SU item en cabeza.
            # `ordenar_items` MUTA el crudo, así que se llama una vez por
            # SKU y justo antes de leerlo, nunca por adelantado para todos.
            for sku in lote:
                if sku in por_sku or not ordenar_items(crudo, sku):
                    continue

                producto = producto_forzado(crudo)

                if producto is None or producto.sku_id != sku:
                    continue

                if not MK.parsear_producto(crudo):
                    forzados.append(sku)

                registro = leer_catalogo(crudo, categoria, producto=producto)

                if registro:
                    por_sku[sku] = registro

        faltan = pedidos - set(por_sku)

        log(f"  lote {numero}/{len(lotes)}: {len(lote) - len(faltan)}/{len(lote)} "
            f"resueltos  req={cliente.contador}/{cliente.tope}")

    seleccion = [por_sku[sku] for sku in sku_ids if sku in por_sku]

    ausentes = [sku for sku in sku_ids if sku not in por_sku]

    if ausentes:
        estado["avisos"].append(
            f"{len(ausentes)} SKUs no volvieron del catálogo y quedaron sin medir: "
            + ", ".join(ausentes)
        )

    if forzados:
        estado["avisos"].append(
            f"{len(forzados)} SKUs sin stock de cadena se midieron igual "
            "(`parsear_producto` los habría descartado): " + ", ".join(forzados)
        )

    estado["skus_ausentes"] = ausentes
    estado["skus_forzados"] = forzados

    if not seleccion:
        estado["avisos"].append("Ningún SKU de la lista existe en el catálogo.")
        return None

    return seleccion


async def descubrir_seleccion(cliente, argumentos, estado) -> list[dict] | None:
    """
    FASE 1 clásica: recorre las categorías objetivo y elige los SKUs.

    Devuelve `None` cuando la fase falló de un modo que deja la corrida
    sin nada que medir; el aviso ya quedó en `estado`.
    """

    log("FASE 1 — catálogo (umbral bi/tri, descuento declarado, category_id)")

    status, arbol, _ = await cliente.pedir(
        f"{MK.BASE_URL}/api/catalog_system/pub/category/tree/3"
    )

    if status >= 400 or not isinstance(arbol, list):
        estado["avisos"].append(f"El árbol de categorías respondió HTTP {status}.")
        return None

    por_ruta = {c["ruta_nombres"]: c for c in aplanar(arbol, [], [], 1, [])}

    seleccion: list[dict] = []

    for objetivo in CATEGORIAS_OBJETIVO:
        categoria = por_ruta.get(objetivo)

        if not categoria:
            estado["avisos"].append(
                f"La categoría {' > '.join(objetivo)} no existe en el árbol vivo."
            )
            continue

        # RUTA COMPLETA DE IDS: `fq=C:/604/` devuelve HTTP 200 con
        # cero productos, sin error. La forma que filtra de verdad
        # es `fq=C:/399/604/`.
        url = (
            f"{MK.BASE_URL}/api/catalog_system/pub/products/search"
            f"?fq=C:{categoria['ruta_ids']}"
            f"&_from=0&_to={VENTANA - 1}&sc={MK.SALES_CHANNEL}"
        )

        try:
            status, datos, _ = await cliente.pedir(url)
        except Exception as exc:
            estado["avisos"].append(
                f"{categoria['ruta']}: {type(exc).__name__} al pedir el catálogo."
            )
            continue

        if status >= 400 or not isinstance(datos, list):
            estado["avisos"].append(f"{categoria['ruta']}: HTTP {status}.")
            continue

        candidatos: list[dict] = []

        for crudo in datos:
            if not isinstance(crudo, dict):
                continue

            registro = leer_catalogo(crudo, categoria)

            if registro:
                candidatos.append(registro)

        elegidos = elegir_de_categoria(candidatos, POR_CATEGORIA)
        seleccion.extend(elegidos)

        con_bi = sum(
            1 for r in elegidos
            if r["bi_umbral"] is not None and r["descuento_catalogo"] is not None
        )

        estado["categorias"].append(
            {
                "ruta": categoria["ruta"],
                "id": categoria["id"],
                "candidatos": len(candidatos),
                "elegidos": len(elegidos),
                "con_biprecio_catalogo": con_bi,
            }
        )

        log(f"  [{categoria['id']:>5}] {categoria['ruta'][:42]:<42} "
            f"candidatos={len(candidatos):>3} elegidos={len(elegidos)} "
            f"bi={con_bi}  req={cliente.contador}/{argumentos.tope}")

    # Deduplicar por SKU y recortar a N_SKUS, sin perder el orden
    # por categoría (las últimas categorías son las de peso
    # variable y no pueden quedar fuera por un empate).
    vistos: set[str] = set()
    unicos: list[dict] = []

    for registro in seleccion:
        if registro["sku_id"] in vistos:
            continue

        vistos.add(registro["sku_id"])
        unicos.append(registro)

    seleccion = unicos[:N_SKUS]

    return seleccion


async def correr(argumentos) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    estado: dict[str, Any] = {
        "filas": [],
        "registros": {},
        "auditoria": [],
        "categorias": [],
        "avisos": [],
        "requests": 0,
        "por_fase": {},
        "stock_cadena": 0,
    }

    cliente = None

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
                tope=argumentos.tope,
            )

            # ---------------- FASE 1: catálogo ----------------
            cliente.fase = "descubrimiento"

            if argumentos.skus:
                seleccion = await traer_por_skus(cliente, argumentos.skus, estado)
            else:
                seleccion = await descubrir_seleccion(cliente, argumentos, estado)

            if seleccion is None:
                return estado

            estado["registros"] = {r["sku_id"]: r for r in seleccion}

            if not seleccion:
                estado["avisos"].append("No se seleccionó ningún SKU: no hay nada que medir.")
                return estado

            log(f"  seleccionados {len(seleccion)} SKUs")

            # ---------------- Stock de cadena ----------------
            cliente.fase = "stock_cadena"

            log("")

            try:
                stock = await MK.refrescar_stock_cadena(
                    cliente, [r["producto"] for r in seleccion]
                )
                MK.STOCK_CADENA.clear()
                MK.STOCK_CADENA.update(stock)
                estado["stock_cadena"] = len(stock)
            except Exception as exc:
                estado["avisos"].append(
                    f"Stock de cadena no disponible ({type(exc).__name__}): "
                    "chain_stock y stock_signal salen vacíos."
                )

            # ---------------- FASE 2: medición ----------------
            cliente.fase = "medicion"

            log("")
            log(f"FASE 2 — simulation qty=1, {len(seleccion)} SKUs x {len(NODOS)} nodos")

            for numero, registro in enumerate(seleccion, 1):
                producto = registro["producto"]

                for nodo in NODOS:
                    momento = MK.ahora()

                    try:
                        status, datos = await MK.consultar_simulation(
                            cliente, producto, nodo
                        )
                    except MK.TopeAgotadoError as exc:
                        # Igual que en el motor: el presupuesto agotado es
                        # una condición de CORRIDA, no un SKU malo. No se
                        # inventa una fila.
                        estado["avisos"].append(
                            f"Presupuesto agotado en {producto.sku_id}/{nodo.node_id}: "
                            f"{exc}. Quedaron {len(seleccion) - numero + 1} SKUs sin medir."
                        )
                        return estado
                    except Exception as exc:
                        status, datos = 0, {"__error": f"{type(exc).__name__}: {exc}"}

                    MK.guardar_evidencia(producto, nodo, "simulation", status, datos)

                    fila = MK.construir_fila(
                        producto, nodo, status, datos, "simulation", momento
                    )

                    estado["filas"].append(enriquecer(fila, registro, nodo, datos))

                fila_359 = estado["filas"][-len(NODOS)]

                log(f"  {numero:>2}/{len(seleccion)} {producto.sku_id:>9} "
                    f"{(producto.product_name or '')[:38]:<38} "
                    f"{fila_359['biprecio_status']:<14} "
                    f"{fila_359['logistics_status'][:22]:<22} "
                    f"req={cliente.contador}/{argumentos.tope}")

            # ---------------- FASE 3: auditoría ----------------
            cliente.fase = "auditoria"

            log("")
            log(f"FASE 3 — auditoría del mayorista: {N_AUDITORIA} SKUs a qty=bi_umbral "
                f"contra el nodo {NODOS[0].node_id}")

            auditables = elegir_auditables(estado["filas"], NODOS[0].node_id, N_AUDITORIA)

            if not auditables:
                estado["avisos"].append(
                    "Ningún SKU salió con biprecio_status = COMPLETO: "
                    "no hubo nada que auditar."
                )

            for fila in auditables:
                resultado = await auditar_mayorista(
                    cliente, fila, NODOS[0], estado["registros"]
                )

                estado["auditoria"].append(resultado)

                # La marca va SOLO en la fila que se midió de verdad. El
                # mayorista del otro nodo sigue siendo reconstruido, y
                # decir lo contrario sería inventar evidencia.
                fila["precio_mayorista_verificado"] = "SI"

                log(f"  {resultado['sku_id']:>9} qty={resultado['umbral']:<3} "
                    f"reconstruido={resultado['reconstruido_cents']} "
                    f"real={resultado['real_cents']} "
                    f"coincide={resultado['coincide'] or '-'} {resultado['detalle']}")

        finally:
            # La contabilidad de requests se cierra acá y no al final del
            # camino feliz: si la corrida se corta por presupuesto agotado,
            # el gasto que ya se le hizo al servidor tiene que quedar
            # registrado igual.
            if cliente is not None:
                estado["requests"] = cliente.contador
                estado["por_fase"] = dict(cliente.por_fase)
                estado["reintentos"] = cliente.reintentos_usados

            await navegador.close()

    return estado


# ===========================================================================
# SALIDA — CSV
# ===========================================================================


def escribir_csv(filas: list[dict]) -> Path:
    SALIDA.mkdir(parents=True, exist_ok=True)

    # (sku_id, node_id): cada SKU con sus dos filas pegadas.
    ordenadas = sorted(filas, key=lambda f: (f["sku_id"], f["node_id"]))

    with CSV_SALIDA.open("w", newline="", encoding="utf-8") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=COLUMNAS, extrasaction="ignore")
        escritor.writeheader()

        for fila in ordenadas:
            escritor.writerow({c: fila.get(c, "") for c in COLUMNAS})

    return CSV_SALIDA


# ===========================================================================
# SALIDA — MD
# ===========================================================================


def tabla(cabecera: list[str], filas: list[list[str]]) -> str:
    lineas = ["| " + " | ".join(cabecera) + " |",
              "|" + "|".join(["---"] * len(cabecera)) + "|"]

    for fila in filas:
        lineas.append("| " + " | ".join(str(c) for c in fila) + " |")

    return "\n".join(lineas)


def distribucion(filas: list[dict], columna: str) -> str:
    cuenta = Counter(f.get(columna) or "(vacío)" for f in filas)

    return tabla(
        [columna, "filas", "%"],
        [
            [valor, n, f"{n / len(filas) * 100:.0f}%"]
            for valor, n in cuenta.most_common()
        ],
    )


def escribir_md(estado: dict, argumentos) -> Path:
    filas = estado["filas"]
    registros = estado["registros"]
    total = len(filas)

    partes: list[str] = []

    partes.append(f"# v5 — validación del esquema completo ({total} filas, {len(COLUMNAS)} columnas)")
    partes.append("")
    partes.append(
        f"`{RUN_ID}` · {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
        f"motor `{MOTOR_PY.name}` v{MK.VERSION} · "
        f"{estado.get('requests', 0)}/{argumentos.tope} requests · "
        f"intervalo {argumentos.intervalo}s"
    )
    partes.append("")
    partes.append(
        f"Formato largo, un solo CSV. Clave primaria `(run_id, node_id, sku_id)`: "
        f"{len(registros)} SKUs × {len(NODOS)} nodos "
        f"({', '.join(n.node_id + ' ' + n.branch for n in NODOS)})."
    )
    partes.append("")
    partes.append(f"Requests por fase: `{estado.get('por_fase', {})}`.")
    partes.append("")

    if not total:
        partes.append("**La corrida no produjo filas.** Avisos:")
        partes.append("")

        for aviso in estado["avisos"]:
            partes.append(f"- {aviso}")

        MD_SALIDA.parent.mkdir(parents=True, exist_ok=True)
        MD_SALIDA.write_text("\n".join(partes) + "\n", encoding="utf-8")

        return MD_SALIDA

    # ---- 1. Tasa de llenado --------------------------------------------
    partes.append("## 1. Tasa de llenado de las columnas nuevas")
    partes.append("")
    partes.append(
        "El objetivo del ejercicio. Una columna que nunca se llena contra el "
        "servidor real no es una columna: es una intención, y no debe entrar "
        "al motor."
    )
    partes.append("")

    grupos = [
        ("Precio mayorista", COLUMNAS_MAYORISTA),
        ("Régimen promocional", COLUMNAS_PROMO),
        ("Identidad y calidad", COLUMNAS_IDENTIDAD),
        ("Presentación", COLUMNAS_PRESENTACION),
    ]

    llenado: dict[str, int] = {}

    for columna in COLUMNAS_NUEVAS:
        llenado[columna] = sum(1 for f in filas if str(f.get(columna, "")).strip())

    for titulo, columnas in grupos:
        partes.append(f"### {titulo}")
        partes.append("")
        partes.append(
            tabla(
                ["columna", "no vacías", "% llenado", "valores distintos"],
                [
                    [
                        f"`{c}`",
                        llenado[c],
                        f"{llenado[c] / total * 100:.0f}%",
                        len({f.get(c, "") for f in filas if str(f.get(c, "")).strip()}),
                    ]
                    for c in columnas
                ],
            )
        )
        partes.append("")

    # ---- 2. Auditoría del mayorista ------------------------------------
    partes.append("## 2. Auditoría del precio mayorista")
    partes.append("")
    partes.append(
        f"Medidos con `simulation` a `qty = bi_umbral` contra el nodo "
        f"{NODOS[0].node_id} ({NODOS[0].branch}). "
        "`reconstruido = price_cents − descuento_monto_cents`; "
        "`real = sellingPrice` devuelto a esa cantidad."
    )
    partes.append("")

    if estado["auditoria"]:
        partes.append(
            tabla(
                ["sku_id", "producto", "umbral", "unitario ¢", "reconstruido ¢",
                 "real ¢", "coincide", "detalle"],
                [
                    [
                        a["sku_id"],
                        (a["product_name"] or "")[:34],
                        a["umbral"],
                        a["unitario_cents"] if a["unitario_cents"] is not None else "",
                        a["reconstruido_cents"] if a["reconstruido_cents"] is not None else "",
                        a["real_cents"] if a["real_cents"] is not None else "",
                        a["coincide"] or "—",
                        a["detalle"] or "",
                    ]
                    for a in estado["auditoria"]
                ],
            )
        )
        partes.append("")

        aciertos = sum(1 for a in estado["auditoria"] if a["coincide"] == "SI")
        partes.append(
            f"**{aciertos} de {len(estado['auditoria'])} coinciden exactamente.** "
            + (
                "La fórmula de reconstrucción se sostiene en el rango de umbrales auditado."
                if aciertos == len(estado["auditoria"])
                else "Hay al menos una discrepancia: la fórmula NO se puede dar por buena."
            )
        )
        partes.append("")

        # Qué rango se pudo cubrir DE VERDAD. El pedido era uno bajo (2-3)
        # y uno alto (12-24); si la muestra no tiene umbrales altos, la
        # auditoría no los cubrió, y eso hay que decirlo acá y no dejar que
        # se lea como si el rango completo estuviera probado.
        umbrales_muestra = sorted(
            {entero(f["bi_umbral"]) for f in filas if entero(f["bi_umbral"])}
        )
        auditados = sorted({a["umbral"] for a in estado["auditoria"] if a["umbral"]})

        partes.append(
            f"Umbrales auditados: {auditados}. Umbrales presentes en toda la muestra: "
            f"{umbrales_muestra}."
            + (
                ""
                if any(12 <= u <= 24 for u in auditados)
                else " **Ningún SKU de la muestra tiene umbral en 12-24**, así que el "
                "extremo alto del rango quedó SIN auditar: lo que se probó es el "
                "extremo bajo."
            )
        )
    else:
        partes.append("_Sin SKUs `COMPLETO` en la muestra: no hubo nada que auditar._")

    partes.append("")

    # ---- 3. Peso variable ----------------------------------------------
    partes.append("## 3. Peso variable (`unit_multiplier != 1.0`)")
    partes.append("")

    variables = [
        f for f in filas
        if (dec(f.get("unit_multiplier")) or Decimal("1")) != Decimal("1")
    ]
    skus_variables = {f["sku_id"] for f in variables}

    partes.append(
        f"**{len(skus_variables)} de {len(registros)} SKUs** tienen "
        f"`unit_multiplier != 1.0` ({len(variables)} filas de {total})."
    )
    partes.append("")

    if variables:
        partes.append(
            tabla(
                ["sku_id", "producto", "m_unit", "u_mult", "price", "price_per_unit",
                 "unidad_base", "cantidad_base", "precio_por_unidad_base", "origen"],
                [
                    [
                        f["sku_id"],
                        (f["product_name"] or "")[:30],
                        f["measurement_unit"],
                        f["unit_multiplier"],
                        f["price"],
                        f["price_per_unit"],
                        f["unidad_base"] or "—",
                        f["cantidad_base"] or "—",
                        f["precio_por_unidad_base"] or "—",
                        f["presentacion_origen"],
                    ]
                    for f in sorted(variables, key=lambda x: (x["sku_id"], x["node_id"]))
                    if f["node_id"] == NODOS[0].node_id
                ],
            )
        )
        partes.append("")
        partes.append(
            "`price_per_unit` es del motor (`sellingPrice/100/unit_multiplier`) y "
            "`precio_por_unidad_base` es de esta sonda. En la rama VTEX ambos deben "
            "coincidir: si difieren, una de las dos está mal."
        )

        discrepan = [
            f for f in variables
            if f["price_per_unit"] and f["precio_por_unidad_base"]
            and dec(f["price_per_unit"]) != dec(f["precio_por_unidad_base"])
        ]

        partes.append("")
        partes.append(
            f"Filas donde difieren: **{len(discrepan)}**."
            + ("" if not discrepan else " Revisar antes de congelar el esquema.")
        )
    else:
        partes.append(
            "_Ningún SKU con peso variable en la muestra: la rama VTEX de la regla "
            "de presentación quedó sin probar._"
        )

    partes.append("")

    # ---- 4. Parseo de presentación -------------------------------------
    partes.append("## 4. Parseo de presentación (envasados, rama NOMBRE)")
    partes.append("")
    partes.append(
        "Ojo con lo que mide esta sección: es **cobertura** (el parser "
        "devolvió algo) y no **acierto** (lo que devolvió es correcto). "
        "Nada en la respuesta de VTEX permite verificar automáticamente que "
        "\"600ml Paquete 6un\" son 3.6 L: el servidor no publica el "
        "contenido del empaque para productos con `measurement_unit = un`. "
        "Por eso la tabla trae la REGLA que disparó, y las derivadas de "
        "multipack son las que hay que mirar a ojo."
    )
    partes.append("")

    envasados = [
        f for f in filas
        if f["node_id"] == NODOS[0].node_id
        and (f.get("measurement_unit") or "un") == "un"
    ]
    resueltos = [f for f in envasados if f["presentacion_origen"] == "NOMBRE"]
    fallados = [f for f in envasados if f["presentacion_origen"] == "DESCONOCIDO"]

    if envasados:
        partes.append(
            f"**Cobertura: {len(resueltos)} de {len(envasados)} envasados resueltos "
            f"({len(resueltos) / len(envasados) * 100:.0f}%).**"
        )
        partes.append("")
        partes.append(
            tabla(
                ["sku_id", "nombre", "unidad_base", "cantidad_base", "regla"],
                [
                    [f["sku_id"], (f["product_name"] or "")[:52],
                     f["unidad_base"], f["cantidad_base"],
                     f.get("_regla_presentacion", "")]
                    for f in resueltos
                ],
            )
        )
        partes.append("")

        multipack = [
            f for f in resueltos
            if str(f.get("_regla_presentacion", "")).startswith("multipack")
        ]

        partes.append(
            f"De los {len(resueltos)} resueltos, **{len(multipack)} salieron de "
            f"multiplicar un multipack** y {len(resueltos) - len(multipack)} de una "
            "medida escrita tal cual. Los multipack son los que pueden salir "
            "12 veces mal sin que nada falle."
        )
        partes.append("")

        if fallados:
            partes.append("Nombres que el parser NO pudo resolver:")
            partes.append("")

            for f in fallados:
                partes.append(f"- `{f['sku_id']}` — {f['product_name']}")
        else:
            partes.append("_Ningún nombre quedó sin resolver en esta muestra._")
    else:
        partes.append("_Sin productos envasados en la muestra._")

    partes.append("")

    # ---- 5. Comparación 359 vs 360 -------------------------------------
    partes.append(f"## 5. Comparación {NODOS[0].node_id} vs {NODOS[1].node_id}")
    partes.append("")

    por_sku: dict[str, dict[str, dict]] = defaultdict(dict)

    for f in filas:
        por_sku[f["sku_id"]][f["node_id"]] = f

    pares = [
        (v[NODOS[0].node_id], v[NODOS[1].node_id])
        for v in por_sku.values()
        if NODOS[0].node_id in v and NODOS[1].node_id in v
    ]

    # `timestamp` cambia entre dos requests consecutivas por construcción:
    # contarlo como diferencia entre sucursales sería ruido.
    IGNORADAS = {"timestamp", "run_id"}

    diferencias: list[tuple[str, int]] = []

    for columna in COLUMNAS:
        if columna in IGNORADAS:
            continue

        n = sum(1 for a, b in pares if str(a.get(columna, "")) != str(b.get(columna, "")))

        if n:
            diferencias.append((columna, n))

    partes.append(f"{len(pares)} SKUs con fila en ambos nodos.")
    partes.append("")

    if diferencias:
        partes.append(
            tabla(
                ["columna", "SKUs que difieren", "% de los pares"],
                [
                    [f"`{c}`", n, f"{n / len(pares) * 100:.0f}%"]
                    for c, n in sorted(diferencias, key=lambda x: -x[1])
                ],
            )
        )
    else:
        partes.append("_Ninguna columna difiere entre nodos._")

    partes.append("")

    iguales_mayorista = sum(
        1 for a, b in pares
        if a["precio_mayorista_cents"] == b["precio_mayorista_cents"]
    )

    partes.append(
        f"`precio_mayorista_cents` igual en ambos nodos: "
        f"**{iguales_mayorista} de {len(pares)}** SKUs."
    )
    partes.append("")

    # ---- 6 y 7. Distribuciones -----------------------------------------
    partes.append("## 6. `ean_type`")
    partes.append("")
    partes.append(distribucion(filas, "ean_type"))
    partes.append("")

    partes.append("## 7. `biprecio_status`")
    partes.append("")
    partes.append(distribucion(filas, "biprecio_status"))
    partes.append("")
    partes.append("Y `presentacion_origen`, que gobierna las tres columnas derivadas:")
    partes.append("")
    partes.append(distribucion(filas, "presentacion_origen"))
    partes.append("")

    # ---- 8. Columnas vacías --------------------------------------------
    partes.append("## 8. Columnas 100% vacías — candidatas a no entrar al motor")
    partes.append("")

    vacias_nuevas = [c for c in COLUMNAS_NUEVAS if llenado[c] == 0]
    vacias_motor = [
        c for c in COLUMNAS_MOTOR
        if not any(str(f.get(c, "")).strip() for f in filas)
    ]

    if vacias_nuevas:
        partes.append("**Columnas nuevas sin un solo valor:**")
        partes.append("")

        for c in vacias_nuevas:
            partes.append(f"- `{c}`")
    else:
        partes.append("**Ninguna columna nueva quedó 100% vacía.**")

    partes.append("")
    partes.append(
        "Columnas del motor 0.1.0 vacías en esta corrida (contexto, no "
        "candidatas a eliminar: esta sonda mide en un solo modo y no todas "
        "las rutas del motor se ejercitan):"
    )
    partes.append("")

    if vacias_motor:
        partes.append(", ".join(f"`{c}`" for c in vacias_motor))
    else:
        partes.append("_Ninguna._")

    partes.append("")

    # ---- 9. Límites -----------------------------------------------------
    partes.append("## 9. Límites de este análisis")
    partes.append("")

    fuentes = Counter(f.get("_fuente_descuento") or "(ninguna)" for f in filas)

    limites = [
        f"**Muestra chica y dirigida.** {len(registros)} SKUs de "
        f"{len(estado['categorias'])} subcategorías elegidas a mano, con "
        "prioridad explícita a los que el catálogo declara con bi-precio. Las "
        "tasas de llenado de esta tabla son un TECHO, no un promedio del "
        "catálogo: en una corrida completa `biprecio_status = SIN_BIPRECIO` "
        "será mucho más frecuente.",

        f"**La auditoría cubre {len(estado['auditoria'])} SKUs de un solo nodo "
        f"({NODOS[0].node_id}).** `precio_mayorista_verificado = SI` marca solo "
        "la fila que se midió de verdad. La fila del otro nodo tiene el mismo "
        "precio mayorista, pero reconstruido: no se midió, y marcarla como "
        "verificada sería inventar evidencia.",

        "**`tri_umbral_declarado` se registra y no se aplica.** El catálogo lo "
        "declara; v2 comprobó que checkout no lo honra. Esta corrida no vuelve "
        "a probarlo: la columna documenta lo que VTEX dice, no lo que cobra.",

        "**El parseo del nombre es heurística, y su tasa de acierto depende de "
        "cómo escriba Makro sus nombres.** Vale para las categorías medidas; "
        "no se puede extrapolar a categorías con otra convención de nombres "
        "(electrohogar, ferretería). `presentacion_origen` existe justamente "
        "para poder separar lo heurístico de lo autoritativo aguas abajo.",

        "**Multipacks: la cantidad se multiplica.** \"26g x 12un\" se resuelve "
        "como 0.312 kg porque el precio de la fila es el del empaque completo. "
        "Es una decisión de interpretación, no un dato del servidor, y no hay "
        "forma de verificarla contra VTEX (ver sección 4).",

        "**El parser sub-cuenta a propósito cuando el nombre mete un código "
        "entre la medida y el conteo.** En \"Deli 1Lt D2 Sf Pp 25un\", ese "
        "\"D2\" corta la regla y la presentación queda en 1 L en vez de 25 L. "
        "Es deliberado: la alternativa es dejar que la regla salte por encima "
        "de basura arbitraria, y ahí empieza a inventar. Prefiero la medida "
        "chica y verdadera del envase a un total adivinado.",

        f"**Origen del descuento:** `{dict(fuentes)}`. Se prefiere el de "
        "`simulation` (por sucursal, vivo) sobre el del catálogo (sin contexto "
        "de sucursal). Si la mayoría viniera del catálogo, la columna no sería "
        "realmente por sucursal.",

        "**Un solo modo de medición (`simulation`, qty=1) y una sola corrida.** "
        "No hay reconciliación contra `orderForm`, ni serie de tiempo, ni "
        "segunda toma: nada acá dice si estos valores son estables entre días.",

        "**Esta sonda no toca el motor.** Las 56 columnas salen de "
        "`construir_fila` sin modificar; las 22 nuevas se calculan encima. Que "
        "una columna se llene acá no prueba que se llene igual dentro del "
        "motor hasta que se implemente ahí.",
    ]

    if estado["avisos"]:
        limites.append(
            "**Avisos de la corrida:** " + " · ".join(estado["avisos"])
        )

    for limite in limites:
        partes.append(f"- {limite}")

    partes.append("")

    MD_SALIDA.parent.mkdir(parents=True, exist_ok=True)
    MD_SALIDA.write_text("\n".join(partes) + "\n", encoding="utf-8")

    return MD_SALIDA


# ===========================================================================
# MAIN
# ===========================================================================


# ===========================================================================
# COMPARACIÓN CONTRA UNA CORRIDA DE REFERENCIA
# ===========================================================================
#
# La pregunta que responde esta sección no es "¿coinciden?", es "¿en QUÉ
# coinciden?". Dos corridas del mismo SKU separadas por días tienen que
# diferir en precio —eso es el negocio— y NO tienen que diferir en la firma
# logística, el umbral bi ni el tipo de EAN. Por eso las columnas se
# reparten en familias antes de contar nada: un total de diferencias sin
# familia no distingue "Makro movió precios" de "el baseline no sirve".

FAMILIA_PRECIO = {
    "price", "list_price", "base_price", "price_cents", "discount_pct",
    "price_per_unit", "descuento_monto", "descuento_monto_cents",
    "precio_mayorista", "precio_mayorista_cents", "descuento_mayorista_pct",
    "precio_por_unidad_base", "precio_mayorista_por_unidad_base",
    "price_valid_until",
}

# Lo que define QUÉ se midió y CÓMO lo resolvió VTEX. Si algo de acá se
# mueve, el golden dejó de describir el mismo objeto.
FAMILIA_ESTRUCTURA = {
    # identidad del SKU
    "schema_version", "retailer", "branch", "node_id", "product_id", "sku_id",
    "sku_ref", "ean", "ean_type", "product_name", "brand", "category",
    "category_id", "seller_id", "url", "currency", "sales_channel", "method",
    # unidad y presentación
    "measurement_unit", "unit_multiplier", "unidad_base", "cantidad_base",
    "presentacion_origen",
    # firma logística y su veredicto
    "warehouse_id", "dock_id", "courier_id", "courier_name", "seller_chain",
    "polygon_name", "polygon_drift", "node_resolved", "logistics_status",
    "fulfillment_type", "surtido_makro", "postal_sent", "postal_resolved",
    "neighborhood_resolved",
    # forma del bi-precio (el umbral es del catálogo, no del precio)
    "bi_umbral", "tri_umbral_declarado", "biprecio_status",
}

# Ni precio ni estructura: el estado del mundo en el momento de medir.
# Cambia solo con que la tienda venda una unidad.
FAMILIA_ESTADO = {
    "availability", "chain_stock", "stock_signal", "price_status",
    "fulfillment_confirmed", "sla_selected", "sla_count", "sla_status",
    "sla_name", "delivery_channel", "shipping_cost", "shipping_estimate",
    "dq_flags", "http_status", "error_class", "error", "recon_status",
    "precio_mayorista_verificado", "promo_regime_id", "promo_regime_name",
    "payment_method_id",
}


def familia(columna: str) -> str:
    if columna in FAMILIA_PRECIO:
        return "PRECIO"

    if columna in FAMILIA_ESTRUCTURA:
        return "ESTRUCTURA"

    if columna in FAMILIA_ESTADO:
        return "ESTADO"

    # Una columna sin familia no se esconde en un total: se nombra.
    return "SIN_CLASIFICAR"


def comparar_contra(filas: list[dict], referencia: Path,
                    ignorar: list[str]) -> None:
    """
    Diferencia celda por celda contra un CSV de referencia. NO escribe nada.

    Clave `(sku_id, node_id)`, que es la del formato largo. Las columnas de
    `ignorar` se saltan porque son volátiles POR CONSTRUCCIÓN (run_id,
    timestamp, fecha): incluirlas haría que las 40 filas difirieran siempre
    y la comparación no diría nada.
    """

    log("")
    log("=" * 78)
    log(f"COMPARACIÓN contra {referencia}")
    log(f"ignorando: {', '.join(ignorar) or '(nada)'}")
    log("=" * 78)

    if not referencia.exists():
        log(f"No existe {referencia}: no hay contra qué comparar.")
        return

    with referencia.open(newline="", encoding="utf-8") as archivo:
        lector = csv.DictReader(archivo)
        golden = {(f["sku_id"], f["node_id"]): f for f in lector}
        columnas_ref = list(lector.fieldnames or [])

    actual = {(f["sku_id"], f["node_id"]): f for f in filas}

    solo_ref = sorted(set(golden) - set(actual))
    solo_act = sorted(set(actual) - set(golden))

    faltan_col = [c for c in columnas_ref if c not in COLUMNAS]
    sobran_col = [c for c in COLUMNAS if c not in columnas_ref]

    if faltan_col or sobran_col:
        log("ESQUEMA distinto — la comparación celda por celda solo cubre las "
            "columnas comunes:")

        if faltan_col:
            log(f"  solo en la referencia: {', '.join(faltan_col)}")

        if sobran_col:
            log(f"  solo en esta corrida:  {', '.join(sobran_col)}")

        log("")

    comparables = [
        c for c in COLUMNAS if c in columnas_ref and c not in set(ignorar)
    ]

    claves = sorted(set(golden) & set(actual))

    exactas = 0
    por_columna: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)

    for clave in claves:
        sku_id, node_id = clave
        difiere = False

        for columna in comparables:
            antes = (golden[clave].get(columna) or "").strip()
            ahora = str(actual[clave].get(columna) or "").strip()

            if antes != ahora:
                difiere = True
                por_columna[columna].append((sku_id, node_id, antes, ahora))

        if not difiere:
            exactas += 1

    total_ref = len(golden)

    log(f"filas en la referencia : {total_ref}")
    log(f"filas en esta corrida  : {len(actual)}")
    log(f"filas comparadas       : {len(claves)}  "
        f"({len(comparables)} columnas comparables)")
    log("")
    log(f"COINCIDEN EXACTO: {exactas}/{total_ref}  "
        f"(difieren {len(claves) - exactas}, ausentes {len(solo_ref)})")

    if solo_ref:
        log(f"  en la referencia y no acá: "
            + ", ".join(f"{s}/{n}" for s, n in solo_ref))

    if solo_act:
        log(f"  acá y no en la referencia: "
            + ", ".join(f"{s}/{n}" for s, n in solo_act))

    log("")

    if not por_columna:
        log("Ninguna columna comparable difiere.")
        log("=" * 78)
        return

    log("COLUMNAS QUE DIFIEREN")
    log("-" * 78)

    orden = {"ESTRUCTURA": 0, "SIN_CLASIFICAR": 1, "PRECIO": 2, "ESTADO": 3}

    for columna in sorted(por_columna,
                          key=lambda c: (orden[familia(c)], -len(por_columna[c]), c)):
        casos = por_columna[columna]

        log(f"[{familia(columna):<14}] {columna}  — {len(casos)} filas")

        for sku_id, node_id, antes, ahora in casos:
            log(f"    {sku_id:>9}/{node_id}  {antes or '(vacío)'!s:>18}"
                f"  ->  {ahora or '(vacío)'!s}")

    log("")
    log("VEREDICTO")
    log("-" * 78)

    tocadas = {familia(c) for c in por_columna}

    for nombre in ("PRECIO", "ESTRUCTURA", "ESTADO", "SIN_CLASIFICAR"):
        columnas = sorted(c for c in por_columna if familia(c) == nombre)

        log(f"{nombre:<14} {len(columnas):>2} columnas"
            + (f": {', '.join(columnas)}" if columnas else ""))

    log("")

    if "ESTRUCTURA" in tocadas or "SIN_CLASIFICAR" in tocadas:
        log("Hay diferencias ESTRUCTURALES: el golden ya no describe el mismo")
        log("objeto medido, así que no sirve como baseline de precios sin")
        log("revisar antes qué cambió de forma.")
    elif "PRECIO" in tocadas:
        log("Las diferencias son SOLO de precio (y estado de la tienda si")
        log("aparece arriba). La estructura se sostiene: el golden sigue")
        log("sirviendo de baseline y lo que se movió son los precios.")
    else:
        log("Solo cambió el estado de la tienda (stock/SLA). Ni precio ni")
        log("estructura se movieron.")

    log("=" * 78)


def parsear_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="v5 — validación del esquema completo contra dos nodos."
    )

    parser.add_argument("--tope", type=int, default=TOPE_REQUESTS)
    parser.add_argument("--intervalo", type=float, default=INTERVALO)
    parser.add_argument("--reintentos", type=int, default=3)
    parser.add_argument("--canal", default="chrome")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--sin-evidencia", action="store_true")

    parser.add_argument(
        "--skus",
        default="",
        help="Lista fija de sku_id (comas o espacios). Salta el descubrimiento "
             "y mide SOLO esos, contra los mismos dos nodos. Sirve para "
             "remedir una corrida vieja; el descubrimiento no es reproducible "
             "porque depende del catálogo vivo.",
    )
    parser.add_argument(
        "--comparar",
        default="",
        help="CSV de referencia contra el que diferenciar las filas de esta "
             "corrida, celda por celda. Solo imprime; no toca el archivo.",
    )
    parser.add_argument(
        "--ignorar",
        default="run_id,timestamp,fecha",
        help="Columnas excluidas de --comparar (por defecto las volátiles "
             "por construcción).",
    )
    parser.add_argument(
        "--sin-archivos",
        action="store_true",
        help="No escribe el CSV ni el MD de la corrida. Para una remedición "
             "que solo tiene que responder una pregunta en consola.",
    )

    argumentos = parser.parse_args()
    argumentos.skus = parsear_lista_skus(argumentos.skus)

    return argumentos


async def principal() -> int:
    argumentos = parsear_argumentos()

    # El motor archiva la evidencia en SU `SALIDA` (que cuelga de
    # `engines/`). Se reapunta a la carpeta real del proyecto para que el
    # crudo de esta sonda caiga junto al del motor, con un run_id `v5_*`
    # que nunca se puede confundir con una corrida del motor.
    MK.SALIDA = SALIDA
    MK.RUN_ID = RUN_ID
    MK.GUARDAR_EVIDENCIA = not argumentos.sin_evidencia

    log("=" * 78)
    log(f"v5 — esquema completo · {len(COLUMNAS)} columnas "
        f"({len(COLUMNAS_MOTOR)} del motor + {len(COLUMNAS_NUEVAS)} nuevas)")
    log(f"motor {MOTOR_PY.name} v{MK.VERSION} · run_id {RUN_ID}")
    log(f"nodos: {', '.join(n.node_id + ' ' + n.branch for n in NODOS)}")
    log(f"tope {argumentos.tope} requests · intervalo {argumentos.intervalo}s")

    if argumentos.skus:
        log(f"modo LISTA FIJA: {len(argumentos.skus)} SKUs, sin descubrimiento")
    log("=" * 78)
    log("")

    try:
        estado = await correr(argumentos)
    except KeyboardInterrupt:
        log("\nInterrumpido.")
        return 130

    log("")

    log("=" * 78)

    if argumentos.sin_archivos:
        log(f"sin archivos: {len(estado['filas'])} filas × {len(COLUMNAS)} columnas "
            "quedaron solo en memoria (--sin-archivos)")
    else:
        ruta_csv = escribir_csv(estado["filas"])
        ruta_md = escribir_md(estado, argumentos)

        log(f"CSV  {ruta_csv}  ({len(estado['filas'])} filas × {len(COLUMNAS)} columnas)")
        log(f"MD   {ruta_md}")

    if MK.GUARDAR_EVIDENCIA:
        log(f"raw  {SALIDA / 'raw' / MK.ahora().strftime('%Y-%m-%d') / (RUN_ID + '.jsonl.gz')}")

    log(f"requests: {estado.get('requests', 0)}/{argumentos.tope} "
        f"{estado.get('por_fase', {})}")

    for aviso in estado["avisos"]:
        log(f"aviso: {aviso}")

    log("=" * 78)

    if argumentos.comparar:
        comparar_contra(
            estado["filas"],
            Path(argumentos.comparar),
            parsear_lista_skus(argumentos.ignorar),
        )

    esperadas = len(estado["registros"]) * len(NODOS)

    # El criterio de aceptación es 40 filas: 20 SKUs, dos veces cada uno.
    # Si salieron menos, la corrida entregó menos de lo que prometió.
    if not estado["filas"] or len(estado["filas"]) != esperadas:
        return 1

    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(principal()))
    except ModuleNotFoundError as exc:
        log(f"Falta una dependencia: {exc}. Instalar con `pip install -r requirements.txt`.")
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)
