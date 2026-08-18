#!/usr/bin/env python3
"""
EXTRACTOR DE PRECIOS POR SUCURSAL — MAKRO PERÚ (VTEX)
=====================================================

Proyecto SW-359-MKO · Price Intelligence · Mercado peruano

QUÉ PRODUCE
-----------
    salida/makro/
      ├── makro_359_santa_anita.csv   ← precios de Santa Anita
      ├── makro_360_surco.csv         ← precios de Surco
      ├── ultima_corrida.json         ← salud de la última ejecución
      └── raw/                        ← evidencia cruda comprimida

Cada motor escribe en SU PROPIA carpeta (`salida/<motor>/`). Cuando
existan tres o cuatro motores, ninguna salida se confunde con otra: el
orquestador recorre un solo raíz y sabe de quién es cada archivo. La
identidad además viaja DENTRO del dato — cada fila lleva `retailer` y el
manifiesto lleva la versión y el nombre exacto del script.

Un CSV por sucursal, mismo esquema en ambos. El analista hace la
comparación en SQL/pandas con un UNION; el extractor NO compara.


PRINCIPIO RECTOR
----------------
    Producto + precio + disponibilidad + firma logística + nodo

NO:

    Producto + precio  ->  adivinar la sucursal

La sucursal se IDENTIFICA leyendo la firma que devuelve VTEX y buscándola
en un catálogo de firmas conocidas. No se asume por el código postal que
se envió. Si VTEX resuelve otro nodo, la fila lo dice explícitamente.


EL PRECIO NUNCA SE DESCARTA
---------------------------
Si VTEX devolvió un precio, ese precio SE GUARDA, incluso si la
validación logística falló. Perder data no es una opción.

La separación es:

    price            -> el HECHO (lo que VTEX respondió)
    price_status     -> el JUICIO (¿es atribuible a esta sucursal?)
    logistics_status -> el PORQUÉ

Un precio con price_status=UNVERIFIED sigue siendo información: te dice
que ese SKU no tiene cobertura desde esa sucursal, que es exactamente el
tipo de señal competitiva que un pricing manager necesita.


SIN ARCHIVOS DE ENTRADA (v11)
-----------------------------
El motor NO lee ningún archivo para saber qué medir. Cada corrida
descubre el catálogo de la API y mide lo que encuentra hoy.

Hasta v10 existía `panel.json`: una canasta congelada en disco. Nació
para un problema real — si cada corrida sortea 100 SKUs de 20.000, dos
corridas no comparten productos y no hay serie de tiempo — pero era una
muleta del MUESTREO, no del motor, y trajo su propia clase de bug: el
stock de cadena salía de esa foto vieja y parecía dato vivo (0 de 100
SKUs cambiaban entre corridas). Cualquier campo leído del panel es
pasado disfrazado de presente.

Reproducibilidad sin archivo
----------------------------
Mientras se prueba con una muestra en vez del catálogo entero, la
selección es DETERMINÍSTICA en vez de guardada:

    orden = md5(f"{semilla}:{sku_id}")   ->  se toman los N primeros

Misma semilla y mismo catálogo dan exactamente la misma muestra, sin
persistir nada. Y cuando Makro agrega productos, entran solos al sorteo
sin que nadie regenere un panel.

Los únicos archivos que el motor escribe son SALIDA: CSV, manifiesto y
evidencia. Ninguno se vuelve a leer para decidir qué medir.


RESPETO AL SERVIDOR
-------------------
El servidor no es nuestro. El script:
  · corre SIEMPRE en secuencia, nunca en paralelo
  · espera un mínimo configurable entre requests (default 1.5s)
  · reintenta con backoff exponencial ante 429 / 5xx
  · respeta la cabecera Retry-After cuando VTEX la envía
  · tiene un tope duro de requests por corrida
  · reutiliza el contexto del navegador (la simulación no guarda estado,
    así que no hace falta recargar la página por cada medición)


FIRMAS LOGÍSTICAS CONOCIDAS
---------------------------
NODO 359 — Makro Santa Anita        NODO 360 — Makro Surco
  SW-359-MKO / DC-359-MKO             SW-360-MKO / DC-360-MKO
  DD-359-MKO                          DD-360-MKO
  DD-Santa-Anita-Makro                DD-Surco-Makro
  Lima-SantaAnita-DD-359_V2           Lima-Surco-DD-360-V2
  plazaveamko359                      plazaveamko360

Para agregar una sucursal nueva basta con añadir una entrada a NODOS.
El resto del script no cambia.


USO
---
    # prueba rápida: descubre 300 SKUs anchos y mide 100
    python3 extractor_makro_v11.py --catalogo 300 --por-categoria 3 --muestra 100

    # catálogo completo (lento: hay que subir el tope a mano, a propósito)
    python3 extractor_makro_v11.py --tope 60000

    python3 extractor_makro_v11.py --reiniciar          # borra CSV y empieza
    python3 extractor_makro_v11.py --modo orderform     # flujo completo
    python3 extractor_makro_v11.py --salida /otra/ruta  # cambia la carpeta
    python3 extractor_makro_v11.py --version            # versión, sin red

DEPENDENCIAS
    python -m pip install playwright
    playwright install chromium

No realiza compras. Solo simulaciones y orderForms temporales.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import gzip
import hashlib
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# Marcador de versión.
#
# Existe porque ya pasó: se corrió una copia vieja del script durante horas
# y los CSV salieron con la taxonomía anterior sin que nada lo delatara.
# La descarga del navegador había creado "extractor_makro_nodos (1).py"
# al lado del original, y el comando seguía apuntando al viejo.
#
# Por eso ahora hay TRES defensas, no una:
#   1. el nombre del archivo lleva la versión  -> no puede colisionar
#   2. la versión se imprime en la cabecera    -> se ve al ejecutar
#   3. queda grabada en ultima_corrida.json    -> cada dataset se rastrea
#
# Y la cabecera muestra el nombre real del archivo que se está ejecutando,
# que es el dato que faltaba para notar que se corría el que no era.
VERSION = "2026.08.13-13"

# Versión del esquema de salida. Se graba en CADA fila: cuando el CSV
# termine en Parquet/PostgreSQL, una fila vieja tiene que poder decir con
# qué reglas nació sin depender de un manifiesto aparte.
#
# v13 NO cambia ningún campo de `Fila` — solo corrige control de flujo y
# amplía el manifiesto — así que SCHEMA_VERSION se queda en "3". Un CSV
# de v12 y uno de v13 se apendean sin disparar `archivar_si_cambio_el_esquema`.
SCHEMA_VERSION = "3"

CAMBIOS = [
    "13  síntesis de hallazgos de Hermes/Codex/ChatGPT sobre la corrida real"
    " en Kali (ver aporte_hermes.txt, aporte_codex.txt,"
    " feedbacks/feedback_conjunto_codex_chatgpt_para_claude_v12.txt)."
    " Corrige la semántica de agotamiento de presupuesto, la más grave de"
    " las dos: (1) TopeAgotadoError durante medición/auditoría/stock de"
    " cadena YA NO se disfraza de EXCEPTION/NO_PRICE de un SKU — es un fallo"
    " GLOBAL de la corrida: se detiene la medición, se conserva lo ya"
    " medido, y las mediciones pendientes quedan listadas en"
    " manifiesto.medicion (no se inventa una fila por cada una) ·"
    " (2) --por-categoria activo ya NUNCA deja completo=true (defecto A,"
    " confirmado por código por Codex pese a que la lectura de Hermes decía"
    " lo contrario) — motivo LIMITE_POR_CATEGORIA ·"
    " (3) presupuesto de descubrimiento subordinado al tope global:"
    " --presupuesto-descubrimiento (o el cálculo automático cuando hay"
    " --muestra) reserva margen para medición/auditoría en vez de dejar que"
    " el árbol de categorías (78% vacío) se coma el tope entero — motivo"
    " LIMITE_PRESUPUESTO_DESCUBRIMIENTO, distinto de un agotamiento real ·"
    " (4) el manifiesto distingue explícitamente catálogo completo / muestra"
    " deliberada / incompleto no planeado (descubrimiento.clasificacion),"
    " agrega medicion{} y stock_cadena{} con el mismo criterio, y un"
    " resumen.corrida_completa + motivos_fallo_global de nivel superior ·"
    " (5) exit code ya no es 0 fijo: una corrida cuya medición o stock de"
    " cadena se cortó por presupuesto agotado devuelve 1 — un límite"
    " deliberado de descubrimiento (--catalogo, --por-categoria,"
    " --presupuesto-descubrimiento) sigue devolviendo 0, porque cumplió lo"
    " que prometía medir · (6) alarma temprana por nodo: si un nodo acumula"
    " 5+ mediciones sin ningún MATCH/MATCH_SELLER_RAIZ/MATCH_SIN_CONFIRMAR,"
    " se avisa en caliente en vez de descubrirlo recién al leer el CSV — así"
    " una firma NODOS mal cargada no puede gastar una corrida entera"
    " produciendo solo OPERADOR_EXTERNO en silencio.",
    "12  revisión independiente de Codex, 5 hallazgos confirmados con evidencia"
    " de código y corregidos: (1) agotar el tope de requests durante el"
    " descubrimiento ya NO se confunde con una categoría fallida — corta la"
    " corrida entera y el manifiesto dice completo=false,"
    " motivo=REQUEST_BUDGET_EXHAUSTED · (2) una categoría sin cabecera"
    " `resources` parseable ya NO se trunca en silencio a 50 productos: la"
    " paginación sigue por longitud de página, no por un total asumido en 0 ·"
    " (3) el tope de requests ahora es duro POR INTENTO, no solo al entrar a"
    " pedir() — antes un intento con varios reintentos podía pasarse del tope"
    " hasta en 3 requests · (4) la auditoría separa SKUs solicitados de"
    " mediciones (SKU×nodo) realizadas, con desglose por nodo · (5) el aviso de"
    " CSV viejos en la carpeta raíz queda grabado en el manifiesto, no solo en"
    " consola",
    "11  motor sin archivos de entrada: muere panel.json, el catálogo se"
    " descubre de la API en cada corrida y la muestra se elige por hash"
    " (reproducible sin persistir) · salida propia por motor: salida/makro/ ·"
    " la auditoría reporta en el RESUMEN · requests desglosados por fase",
    "10  orderForm con contexto propio (el carrito es estado y se contaminaba) ·"
    " productos por peso: measurement_unit / unit_multiplier / price_per_unit",
    "09  fulfillment_confirmed (encontrar el almacén ≠ confirmar el despacho) ·"
    " estados de SLA · evidencia cruda · run_id · reglas DQ ·"
    " reconciliación muestreada · alarma de cambio de contrato VTEX",
    "08  chain_stock refrescado por lotes (stock de cadena vs de sucursal) ·"
    " stock_signal · guardia de esquema al apendear",
    "07  MATCH_SELLER_RAIZ · fulfillment_type · SIN_STOCK vs NO_COVERAGE ·"
    " landing SEO con cupo 1 · tope de requests automático",
    "06  el nombre del archivo lleva la versión: extractor_makro_v06.py",
    "05  muestreo ancho (máx N SKUs por subcategoría) + estadísticas de muestreo",
    "04  fallback no destructivo + NO_COVERAGE / ITEM_NOT_RETURNED",
    "03  APIResponse.headers (no all_headers) + los bugs no se reintentan",
    "02  ruta completa en fq=C:/ + offset aleatorio con cabecera resources",
    "01  versión inicial: panel fijo, un CSV por sucursal",
]

BASE_DIR = Path(__file__).resolve().parent

# Slug del motor. Nombra la carpeta de salida y es lo que impide que la
# salida de Makro se confunda con la de Tottus o Sodimac cuando existan
# sus propios motores: un solo raíz `salida/`, una subcarpeta por motor.
MOTOR = "makro"

RAIZ_SALIDA = BASE_DIR / "salida"
SALIDA = RAIZ_SALIDA / MOTOR
MANIFEST_FILE = SALIDA / "ultima_corrida.json"

BASE_URL = "https://www.makro.plazavea.com.pe"
SALES_CHANNEL = "9"
RETAILER = "Makro"
MONEDA = "PEN"

ORDERFORM_SECTIONS = [
    "items",
    "totalizers",
    "clientProfileData",
    "shippingData",
    "paymentData",
    "sellers",
    "messages",
    "marketingData",
    "clientPreferencesData",
    "storePreferencesData",
    "giftRegistryData",
    "ratesAndBenefitsData",
    "openTextField",
    "commercialConditionData",
    "customData",
]

NOMBRES_BASURA = (
    "tienda no dispobile",
    "tienda no disponible",
    "no inactivar",
    "test producto",
    "producto prueba",
)


# ===========================================================================
# FIRMAS DE SUCURSAL
# ===========================================================================


@dataclass
class Nodo:
    node_id: str
    branch: str
    archivo: str

    postal_code: str
    number: str
    neighborhood: str
    city: str
    state: str
    country: str
    longitude: float
    latitude: float

    warehouse_id: str
    dock_id: str
    courier_id: str
    courier_name: str
    seller_chain: str
    polygon_name: str

    def firma_core(self) -> dict[str, str]:
        """
        Identificadores que definen la sucursal.

        polygonName queda FUERA a propósito: VTEX lo versiona
        (`_V2`, `-V2`, `V3`) y un cambio de versión no significa que la
        sucursal sea otra. Se registra aparte como `polygon_drift`.
        """

        return {
            "warehouseId": self.warehouse_id,
            "dockId": self.dock_id,
            "courierId": self.courier_id,
            "courierName": self.courier_name,
            "sellerChain": self.seller_chain,
        }

    def direccion(self) -> dict[str, Any]:
        return {
            "addressType": "residential",
            "receiverName": "",
            "addressId": None,
            "postalCode": self.postal_code,
            "city": self.city,
            "state": self.state,
            "country": self.country,
            "street": None,
            "number": self.number,
            "neighborhood": self.neighborhood,
            "complement": "",
            "reference": "",
            "geoCoordinates": [self.longitude, self.latitude],
        }


NODOS: dict[str, Nodo] = {
    "359": Nodo(
        node_id="359",
        branch="Santa Anita",
        archivo="makro_359_santa_anita.csv",
        postal_code="150137",
        number="15007",
        neighborhood="Santa Anita",
        city="Lima",
        state="Lima",
        country="PER",
        longitude=-76.97036729999999,
        latitude=-12.0433031,
        warehouse_id="SW-359-MKO",
        dock_id="DC-359-MKO",
        courier_id="DD-359-MKO",
        courier_name="DD-Santa-Anita-Makro",
        seller_chain="plazaveamko359",
        polygon_name="Lima-SantaAnita-DD-359_V2",
    ),
    "360": Nodo(
        node_id="360",
        branch="Surco",
        archivo="makro_360_surco.csv",
        postal_code="150140",
        number="15023",
        neighborhood="Santiago De Surco",
        city="Lima",
        state="Lima",
        country="PER",
        longitude=-76.9856739,
        latitude=-12.122978,
        warehouse_id="SW-360-MKO",
        dock_id="DC-360-MKO",
        courier_id="DD-360-MKO",
        courier_name="DD-Surco-Makro",
        seller_chain="plazaveamko360",
        polygon_name="Lima-Surco-DD-360-V2",
    ),
}


# ===========================================================================
# MODELOS
# ===========================================================================


@dataclass
class Producto:
    product_id: str
    sku_id: str
    sku_ref: str = ""
    ean: str = ""
    product_name: str = ""
    brand: str = ""
    category: str = ""
    seller_id: str = "1"
    url: str = ""
    stock_catalog: str = ""


@dataclass
class Fila:
    """Una medición: un SKU, en una sucursal, en un momento."""

    # --- IDENTIDAD -------------------------------------------------------
    #
    # run_id + schema_version hacen cada fila auto-explicativa: dentro de
    # seis meses, en PostgreSQL, se puede responder "¿de qué corrida salió
    # esta fila y con qué reglas se evaluó?" sin buscar en ningún lado.
    run_id: str = ""
    schema_version: str = SCHEMA_VERSION
    timestamp: str = ""
    fecha: str = ""
    retailer: str = RETAILER
    branch: str = ""
    node_id: str = ""

    # --- PRODUCTO --------------------------------------------------------
    product_id: str = ""
    sku_id: str = ""
    sku_ref: str = ""
    ean: str = ""
    product_name: str = ""
    brand: str = ""
    category: str = ""
    seller_id: str = ""
    url: str = ""

    # --- PRECIO ----------------------------------------------------------
    #
    # PRODUCTOS POR PESO — la trampa que arruinaría toda comparación.
    #
    # En un producto vendido por kilo, VTEX usa los campos así:
    #
    #     listPrice      = precio por KILO       (33.90)
    #     sellingPrice   = precio de la PIEZA    (152.55)
    #     unitMultiplier = peso de la pieza (kg) (4.50)
    #
    # `price` NO es comparable contra el precio por kilo de otra tienda:
    # 152.55 vs 33.90 no es "cuatro veces más caro", es una pieza de 4.5 kg
    # contra un kilo. Sin esta distinción, cualquier análisis de carnes,
    # quesos o frutas a granel sale absurdo — y en el panel de 100 SKUs
    # son 7, o sea el 7% del catálogo.
    #
    #     price           lo que se paga por la pieza
    #     price_per_unit  lo que se paga por unidad de medida (comparable)
    #     unit_multiplier tamaño de la pieza en esa unidad
    currency: str = MONEDA
    price: str = ""
    list_price: str = ""
    base_price: str = ""
    price_cents: str = ""
    discount_pct: str = ""
    measurement_unit: str = ""
    unit_multiplier: str = ""
    price_per_unit: str = ""

    # --- DISPONIBILIDAD Y ENTREGA ----------------------------------------
    #
    # DOS NIVELES DE STOCK, deliberadamente separados:
    #
    #   availability  -> viene del CHECKOUT con la dirección de la sucursal.
    #                    Responde "¿esta tienda me lo puede despachar hoy?".
    #   chain_stock   -> viene del CATÁLOGO, sin contexto de sucursal.
    #                    Responde "¿cuánto queda en toda la cadena?".
    #
    # No se contradicen: son preguntas distintas. Que Surco diga
    # `withoutStock` mientras chain_stock=37 significa que Surco se quedó
    # sin unidades pero la cadena todavía tiene — probablemente en otra
    # tienda. Esa combinación es señal competitiva, no un error de datos.
    availability: str = ""
    chain_stock: str = ""
    stock_signal: str = ""
    delivery_channel: str = ""
    shipping_cost: str = ""
    shipping_estimate: str = ""
    sla_name: str = ""

    # --- VALIDACIÓN ------------------------------------------------------
    #
    # ENCONTRAR EL ALMACÉN NO ES CONFIRMAR EL DESPACHO.
    #
    # VTEX puede ofrecer varios SLAs para un mismo ítem. Que uno de ellos
    # salga del almacén que esperábamos no significa que sea el que el
    # cliente recibiría: eso lo dice `selectedSla`. Hasta v08 tomábamos el
    # primer SLA cuyo almacén coincidía y lo llamábamos MATCH — es decir,
    # sobreinterpretábamos la evidencia.
    #
    #   fulfillment_confirmed = SI   el SLA usado ES el seleccionado
    #   fulfillment_confirmed = NO   el almacén calza, el despacho no está
    #                                confirmado -> price_status = QUALIFIED
    node_resolved: str = "NONE"
    logistics_status: str = "SIN_EVALUAR"
    price_status: str = "NO_PRICE"
    fulfillment_confirmed: str = ""
    sla_selected: str = ""
    sla_count: str = ""
    sla_status: str = ""
    polygon_drift: str = ""
    fulfillment_type: str = ""

    # --- CALIDAD DE DATO -------------------------------------------------
    # Reglas que fallaron, separadas por "|". Vacío = fila limpia.
    dq_flags: str = ""

    # --- AUDITORÍA -------------------------------------------------------
    warehouse_id: str = ""
    dock_id: str = ""
    courier_id: str = ""
    courier_name: str = ""
    seller_chain: str = ""
    polygon_name: str = ""
    postal_sent: str = ""
    postal_resolved: str = ""
    neighborhood_resolved: str = ""
    http_status: str = ""
    method: str = ""
    # Distingue "Makro no tiene el producto" de "nuestro extractor falló".
    error_class: str = ""
    error: str = ""
    # Resultado del contraste simulation vs orderForm (solo filas auditadas).
    recon_status: str = ""


COLUMNAS = list(Fila().__dict__.keys())


# ===========================================================================
# UTILIDADES
# ===========================================================================


def s(valor: Any) -> str:
    return "" if valor is None else str(valor)


def soles(centavos: Any) -> str:
    """
    Céntimos VTEX -> número plano.

    Devuelve "212.90", NUNCA "S/ 212.90".

    Un CSV que trae el símbolo de moneda pegado al número obliga a
    pandas a leer la columna como texto y a Excel a tratarla como
    string. La moneda va en su propia columna (`currency`).
    """

    if centavos in (None, ""):
        return ""

    try:
        return f"{int(centavos) / 100:.2f}"
    except (TypeError, ValueError):
        return ""


def ahora() -> datetime:
    return datetime.now().astimezone()


def log(mensaje: str = "") -> None:
    print(mensaje, flush=True)


def es_basura(nombre: str) -> bool:
    bajo = (nombre or "").lower()
    return any(m in bajo for m in NOMBRES_BASURA)


def es_landing_seo(nombre: str) -> bool:
    """
    Distingue una categoría real de una landing page de SEO.

    El árbol de VTEX mezcla las dos cosas. De las 39 categorías que
    produjeron SKUs el 13-ago, 14 eran landings:

        absolut vodka · whisky gold label etiqueta dorada · cepillo oral b
        shampoo con clorhexidina para perros · garnier fructis shampoo ...

    No son categorías: son términos de búsqueda con página propia. Por eso
    el árbol tiene 3354 nodos y el 78% viene vacío.

    El patrón es simple y estable: la taxonomía real va en Title Case
    ("Queso Cheddar", "Sartenes y Woks"), las landings van en minúscula.

    No se descartan — a veces son la única puerta a un producto que la
    taxonomía no lista bien — pero se les da cupo 1 en vez de 3, para que
    no distorsionen el panel con tres vodkas de la misma marca.
    """

    limpio = (nombre or "").strip()

    if not limpio:
        return True

    return limpio[0].islower()


def buscar_todo(objeto: Any, clave: str) -> list[Any]:
    encontrados: list[Any] = []

    if isinstance(objeto, dict):
        if clave in objeto:
            encontrados.append(objeto[clave])

        for valor in objeto.values():
            encontrados.extend(buscar_todo(valor, clave))

    elif isinstance(objeto, list):
        for valor in objeto:
            encontrados.extend(buscar_todo(valor, clave))

    return encontrados


# ===========================================================================
# CLIENTE HTTP CON BUENOS MODALES
# ===========================================================================


# Excepciones que delatan un bug del script, no un problema de red.
# Reintentarlas es contraproducente: esconden la causa real.
ERRORES_DE_CODIGO = (
    AttributeError,
    TypeError,
    NameError,
    KeyError,
    IndexError,
    ImportError,
)


class TopeAgotadoError(RuntimeError):
    """
    Se llegó al tope de requests de la corrida.

    Por qué es una clase propia y no un RuntimeError genérico (bug
    confirmado en v11, revisión de Codex del 13-ago)
    ------------------------------------------------------------------
    `descubrir_catalogo()` atrapaba CUALQUIER excepción de `cliente.pedir()`
    con un `except Exception` y la trataba como "esta categoría falló": la
    sumaba a `categorias_fallidas` y seguía con la próxima. Si lo que pasó
    fue que se acabó el presupuesto de requests, eso NO es una categoría
    fallida — es la corrida entera quedándose sin plata a mitad de camino.
    Con la excepción genérica, el manifiesto terminaba diciendo
    `completo: true` después de haberse quedado sin tope, porque nada
    distinguía un HTTP 500 puntual de "no queda presupuesto para nada más".

    Con esta clase separada, quien la atrapa sabe que no tiene sentido
    seguir probando la próxima categoría — todas van a fallar igual,
    instantáneamente, e inflar `fallos` con miles de entradas idénticas.
    """


class TopeAgotadoAlEntrarError(TopeAgotadoError):
    """El tope ya estaba agotado ANTES de intentar la request."""


class TopeAgotadoEnReintentoError(TopeAgotadoError):
    """El tope se agotó DURANTE los reintentos de una request en curso."""


class Cliente:
    """
    Envoltorio sobre el APIRequestContext de Playwright.

    Responsabilidades:
      · garantizar un intervalo mínimo entre requests
      · reintentar con backoff exponencial ante 429 y 5xx
      · respetar Retry-After
      · cortar la corrida si se pasa del tope de requests

    Todo esto existe por una razón simple: el servidor no es nuestro y
    no queremos que una corrida nuestra le haga daño a nadie.
    """

    def __init__(
        self,
        api,
        intervalo: float = 1.5,
        reintentos: int = 3,
        tope: int = 400,
    ):
        self.api = api
        self.intervalo = intervalo
        self.reintentos = reintentos
        self.tope = tope

        self.contador = 0
        self.reintentos_usados = 0
        self._ultima = 0.0

        # Contabilidad por fase.
        #
        # En la corrida v10 salieron 233 requests donde la cuenta a mano
        # daba ~218. Quince requests sin explicar sobre 233 es anecdótico;
        # el mismo 7% sobre los ~40.000 del catálogo completo son 45
        # minutos de servidor ajeno que nadie sabe a qué se fueron. Si el
        # servidor no es nuestro, el gasto tiene que ser auditable.
        #
        # Los reintentos SÍ se cuentan: son requests reales que el
        # servidor atendió, no un detalle interno del cliente.
        self.fase = "otros"
        self.por_fase: dict[str, int] = {}

    async def _esperar_turno(self) -> None:
        transcurrido = time.monotonic() - self._ultima
        falta = self.intervalo - transcurrido

        if falta > 0:
            await asyncio.sleep(falta)

        self._ultima = time.monotonic()

    async def pedir(
        self,
        url: str,
        metodo: str = "GET",
        body: Any = None,
    ) -> tuple[int, Any, dict[str, str]]:

        if self.contador >= self.tope:
            raise TopeAgotadoAlEntrarError(
                f"Tope de {self.tope} requests alcanzado. "
                "Corrida detenida para no saturar el servidor."
            )

        espera = 2.0
        ultimo_error = ""

        for intento in range(self.reintentos + 1):

            # Tope duro POR INTENTO, no solo al entrar a pedir().
            #
            # Bug confirmado en v11 (revisión de Codex, 13-ago): el chequeo
            # de arriba corre UNA vez, antes del bucle. Adentro,
            # `self.contador += 1` se ejecuta en cada reintento sin volver a
            # mirar el tope. Una sola llamada que caiga en 429/5xx en sus
            # `reintentos` intentos podía terminar hasta `reintentos`
            # requests por encima del tope antes de que la PRÓXIMA llamada
            # lo notara. Acá se vuelve a chequear antes de cada intento
            # real, así que el tope nunca se pasa, ni siquiera a mitad de
            # una secuencia de reintentos.
            if self.contador >= self.tope:
                raise TopeAgotadoEnReintentoError(
                    f"Tope de {self.tope} requests alcanzado durante los "
                    f"reintentos (intento {intento + 1}). Corrida detenida."
                )

            await self._esperar_turno()
            self.contador += 1
            self.por_fase[self.fase] = self.por_fase.get(self.fase, 0) + 1

            try:
                cabeceras = {"Accept": "application/json"}

                if metodo == "GET":
                    respuesta = await self.api.get(
                        url,
                        headers=cabeceras,
                        timeout=45000,
                    )
                else:
                    cabeceras["Content-Type"] = "application/json; charset=UTF-8"
                    respuesta = await self.api.post(
                        url,
                        data=json.dumps(body or {}),
                        headers=cabeceras,
                        timeout=45000,
                    )

                status = respuesta.status

                # OJO: Playwright tiene DOS clases de respuesta distintas.
                #
                #   Response     (page.on("response"))  -> await all_headers()
                #   APIResponse  (context.request.*)    -> .headers  (propiedad)
                #
                # Acá usamos context.request, así que es APIResponse y las
                # cabeceras son una propiedad sincrónica, no una corrutina.
                # Las claves ya vienen en minúscula.
                salida_cabeceras = dict(respuesta.headers or {})

                texto = await respuesta.text()

                # 429 = nos están pidiendo que bajemos el ritmo.
                # 5xx = el servidor está sufriendo. En ambos casos: esperar.
                if status in (429, 500, 502, 503, 504) and intento < self.reintentos:

                    retry_after = salida_cabeceras.get("retry-after", "")

                    try:
                        pausa = float(retry_after) if retry_after else espera
                    except ValueError:
                        pausa = espera

                    pausa = min(pausa, 60.0)

                    log(
                        f"      HTTP {status}. Esperando {pausa:.0f}s "
                        f"(intento {intento + 1}/{self.reintentos})"
                    )

                    self.reintentos_usados += 1

                    await asyncio.sleep(pausa)
                    espera *= 2.5

                    continue

                try:
                    return status, json.loads(texto), salida_cabeceras
                except json.JSONDecodeError:
                    return status, {"__raw": texto[:1200]}, salida_cabeceras

            except ERRORES_DE_CODIGO:
                # Un bug del script NO se reintenta. Reintentar un
                # AttributeError solo gasta 19 segundos y entierra la
                # causa real bajo tres mensajes de "error de red".
                raise

            except Exception as exc:
                ultimo_error = f"{type(exc).__name__}: {exc}"

                if intento < self.reintentos:
                    log(f"      Error de red. Esperando {espera:.0f}s. {ultimo_error[:90]}")

                    self.reintentos_usados += 1

                    await asyncio.sleep(espera)
                    espera *= 2.5

                    continue

                raise RuntimeError(ultimo_error) from exc

        raise RuntimeError(
            f"Agotados {self.reintentos} reintentos. Último: {ultimo_error}"
        )


# ===========================================================================
# CATÁLOGO: DESCUBRIMIENTO Y SELECCIÓN
# ===========================================================================


def total_desde_resources(recursos: str) -> int | None:
    """
    `0-24/1832` -> 1832. `None` si la cabecera falta o no se puede leer.

    Antes devolvía 0 en ambos casos: "la cabecera no vino" y "vino y el
    total es cero". Bug confirmado en v11 (revisión de Codex, 13-ago): el
    llamador usaba ese 0 como si fuera un total real, y una categoría con
    miles de productos pero sin cabecera `resources` legible se leía como
    "categoría de 0 productos" y se truncaba a la primera página de 50 sin
    dejar ningún rastro — ni en `fallos`, ni en `categorias_truncadas_por_vtex`.
    Separar "no sé" de "sé que es cero" es lo que permite que el llamador
    decida distinto en cada caso.
    """

    if "/" not in recursos:
        return None

    try:
        return int(recursos.rsplit("/", 1)[1])
    except (ValueError, IndexError):
        return None


def aplanar_categorias(
    arbol: Any,
    prefijo: str = "",
    nivel: int = 1,
    acumulado: list[dict] | None = None,
) -> list[dict]:
    """
    Aplana el árbol VTEX construyendo la RUTA COMPLETA de cada categoría.

    El filtro `fq=C:/{...}/` necesita la ruta desde la raíz:

        Correcto   -> fq=C:/1/13/152/
        Incorrecto -> fq=C:/152/      (devuelve 0 productos, siempre)
    """

    if acumulado is None:
        acumulado = []

    if not isinstance(arbol, list):
        arbol = [arbol] if isinstance(arbol, dict) else []

    for rama in arbol:
        if not isinstance(rama, dict) or not rama.get("id"):
            continue

        id_categoria = s(rama.get("id"))
        ruta = f"{prefijo}/{id_categoria}" if prefijo else id_categoria

        acumulado.append(
            {
                "id": id_categoria,
                "ruta": ruta,
                "name": s(rama.get("name")),
                "nivel": nivel,
            }
        )

        aplanar_categorias(rama.get("children") or [], ruta, nivel + 1, acumulado)

    return acumulado


def parsear_producto(crudo: dict) -> Producto | None:
    items = crudo.get("items") or []

    if not items:
        return None

    item = items[0]
    vendedores = item.get("sellers") or []

    if not vendedores:
        return None

    oferta = vendedores[0].get("commertialOffer") or {}

    if not oferta.get("AvailableQuantity"):
        return None

    nombre = s(crudo.get("productName"))

    if es_basura(nombre):
        return None

    sku_ref = ""

    for referencia in item.get("referenceId") or []:
        if isinstance(referencia, dict) and referencia.get("Value"):
            sku_ref = s(referencia.get("Value"))
            break

    categorias = crudo.get("categories") or []

    return Producto(
        product_id=s(crudo.get("productId")),
        sku_id=s(item.get("itemId")),
        sku_ref=sku_ref,
        ean=s(item.get("ean")),
        product_name=nombre,
        brand=s(crudo.get("brand")),
        category=(
            s(categorias[0]).strip("/").replace("/", " > ") if categorias else ""
        ),
        seller_id=s(vendedores[0].get("sellerId")) or "1",
        url=s(crudo.get("link")),
        stock_catalog=s(oferta.get("AvailableQuantity")),
    )


# Diagnóstico del último descubrimiento. Se vuelca en ultima_corrida.json
# para que quede registro de cuántas categorías se tocaron y cuántas
# fallaron: una categoría perdida por un HTTP 500 no puede desaparecer en
# silencio del registro.
ESTADISTICAS_CATALOGO: dict[str, Any] = {}


async def descubrir_catalogo(
    cliente: Cliente,
    limite: int = 0,
    por_categoria: int = 0,
    presupuesto_fase: int = 0,
) -> list[Producto]:
    """
    Enumera el catálogo vivo recorriendo el árbol de categorías de VTEX.

    Qué cambió respecto a v10
    -------------------------
    v10 MUESTREABA: sorteaba categorías, sorteaba una ventana dentro de
    cada una y paraba al juntar N SKUs. Eso servía para armar un panel
    de 100 productos, no para extraer un catálogo. v11 ENUMERA: recorre
    las categorías en orden fijo y pagina cada una hasta agotarla.

    Sin azar. El orden es determinístico (ruta de categoría, luego el
    orden que devuelve VTEX), así que dos corridas con el mismo catálogo
    descubren lo mismo en el mismo orden, sin guardar nada en disco.

    Parámetros
    ----------
    limite            corta el descubrimiento al llegar a N SKUs.
                      0 = catálogo completo. Sirve para probar sin gastar
                      una hora de requests.
    por_categoria     máximo de SKUs por subcategoría. 0 = sin límite (el
                      catálogo de verdad). Con un valor bajo la muestra sale
                      ANCHA — pocos SKUs de muchas categorías — que es lo que
                      se quiere cuando `limite` está activo: sin este tope,
                      los primeros 300 SKUs salen todos de las dos primeras
                      categorías del árbol y no describen nada.

                      IMPORTANTE (v13, defecto A confirmado por Codex en
                      aporte_codex.txt §2.7 pese a que otra lectura creía
                      que ya estaba resuelto): con `por_categoria > 0` el
                      descubrimiento NUNCA puede marcarse `completo=True`,
                      incluso si ninguna categoría llegó a tocar su cupo en
                      esta corrida puntual. El contrato del flag es
                      "muestra ancha", no "catálogo real" — completo=True
                      bajo ese régimen afirmaría algo que el propio diseño
                      del flag contradice.
    presupuesto_fase  tope de requests reservado SOLO para esta fase,
                      subordinado siempre a `cliente.tope` (nunca lo
                      supera: es un freno adicional, más temprano, no un
                      presupuesto aparte). 0 = sin freno propio; el
                      descubrimiento solo se detiene cuando lo hace
                      `cliente.tope` (comportamiento de v12). Existe porque
                      el árbol de VTEX es ~78% categorías vacías: sin este
                      freno, descubrir puede gastarse TODO el tope antes de
                      medir un solo SKU (confirmado en Kali: 286/300
                      requests en descubrimiento para una muestra de 10).
                      Al activarse marca `completo=False` con motivo
                      LIMITE_PRESUPUESTO_DESCUBRIMIENTO — distinto de
                      REQUEST_BUDGET_EXHAUSTED, que significa que fue el
                      tope GLOBAL el que se acabó, no una reserva de fase.

    Costo
    -----
    El árbol tiene ~3354 nodos y ~78% vuelven vacíos bajo sc=9. Una
    request por categoría más el paginado de las productivas. Descubrir
    es barato comparado con atribuir: la atribución cuesta una request
    por SKU y por sucursal.
    """

    log("  Leyendo árbol de categorías...")

    status, arbol, _ = await cliente.pedir(
        f"{BASE_URL}/api/catalog_system/pub/category/tree/3"
    )

    categorias = aplanar_categorias(arbol) if status < 400 else []

    # Nivel 1 es demasiado amplio, nivel 4+ suele tener 2 o 3 productos.
    utiles = [c for c in categorias if 2 <= c["nivel"] <= 3] or categorias

    # Orden fijo: el descubrimiento no puede depender del azar si el
    # motor tiene que ser reproducible sin guardar el resultado.
    utiles.sort(key=lambda c: c["ruta"])

    log(
        f"  Categorías a recorrer: {len(utiles)} | "
        + (f"objetivo: {limite} SKUs" if limite else "objetivo: catálogo completo")
        + (f" · máx {por_categoria} por categoría" if por_categoria else "")
    )

    encontrados: dict[str, Producto] = {}
    usadas: list[str] = []

    stats: dict[str, Any] = {
        "limite": limite,
        "por_categoria": por_categoria,
        "categorias_en_arbol": len(categorias),
        "categorias_consultadas": 0,
        "categorias_productivas": 0,
        "categorias_vacias": 0,
        "categorias_sin_candidatos": 0,
        "categorias_fallidas": 0,
        "categorias_truncadas_por_vtex": 0,
        "categorias_resources_desconocido": 0,
        "landings_seo": 0,
        "completo": True,
        # Por qué `completo` es false, si lo es. Puede tener más de un
        # motivo (agotar presupuesto Y encontrar una categoría truncada
        # en la misma corrida). Antes solo existía --catalogo como causa;
        # revisión de Codex del 13-ago confirmó dos más que quedaban sin
        # marcar: presupuesto agotado y truncamiento de VTEX.
        "motivos_incompleto": [],
        "fallos": [],
    }

    # Defecto A (v13): --por-categoria es, por diseño, una muestra ancha,
    # nunca el catálogo real — se marca ANTES de recorrer nada, no
    # condicionado a que alguna categoría concreta llegara a su cupo. Ver
    # docstring del parámetro más arriba.
    if por_categoria:
        stats["completo"] = False
        stats["motivos_incompleto"].append("LIMITE_POR_CATEGORIA")

    # VTEX no pagina más allá de ~2500 en esta API. Una categoría más
    # grande que eso queda truncada y hay que DECIRLO: si no, el dataset
    # miente por omisión y nadie se entera.
    TOPE_VTEX = 2450
    VENTANA = 50

    presupuesto_agotado = False
    presupuesto_fase_agotado = False

    for categoria in utiles:

        if limite and len(encontrados) >= limite:
            stats["completo"] = False
            stats["motivos_incompleto"].append("LIMITE_CATALOGO")
            break

        if presupuesto_fase and cliente.contador >= presupuesto_fase:
            # Freno de fase, no agotamiento global: hay presupuesto en
            # cliente.tope, pero esta fase reservó menos para dejarle
            # margen a medición/auditoría. Mismo tratamiento que
            # LIMITE_CATALOGO — un corte a propósito, no una emergencia.
            presupuesto_fase_agotado = True
            stats["completo"] = False
            if "LIMITE_PRESUPUESTO_DESCUBRIMIENTO" not in stats["motivos_incompleto"]:
                stats["motivos_incompleto"].append("LIMITE_PRESUPUESTO_DESCUBRIMIENTO")
            log(
                "  Presupuesto reservado para descubrimiento "
                f"({presupuesto_fase}) alcanzado — se detiene aquí para "
                "dejar margen a medición/auditoría."
            )
            break

        stats["categorias_consultadas"] += 1

        base = (
            f"{BASE_URL}/api/catalog_system/pub/products/search"
            f"?fq=C:/{categoria['ruta']}/&sc={SALES_CHANNEL}"
        )

        landing = es_landing_seo(categoria["name"])

        # Cuando se está muestreando (por_categoria > 0) las landings de
        # SEO reciben cupo 1: son tan específicas ("absolut vodka") que
        # llenarían la muestra de variantes del mismo producto. En modo
        # catálogo completo no se limita nada — el dedupe por sku_id ya
        # evita contar dos veces lo que aparece en dos ramas.
        if por_categoria:
            cupo_categoria = 1 if landing else por_categoria
        else:
            cupo_categoria = 0

        nuevos = 0
        total = 0
        total_conocido = False
        resources_desconocido = False
        truncada = False
        desde = 0

        while True:

            if limite and len(encontrados) >= limite:
                break

            if cupo_categoria and nuevos >= cupo_categoria:
                break

            if presupuesto_fase and cliente.contador >= presupuesto_fase:
                # Mismo freno de fase que el chequeo entre categorías, pero
                # DENTRO de la paginación: sin esto, una sola categoría muy
                # grande podría rebasar la reserva antes de que el chequeo
                # de arriba tuviera oportunidad de detenerla. Se marca acá
                # mismo (no solo se levanta la bandera) porque esta
                # categoría puede seguir siendo "productiva" más abajo
                # (`if nuevos:`) y ese camino no vuelve a pasar por el
                # chequeo de tope de categoría.
                presupuesto_fase_agotado = True
                stats["completo"] = False
                if "LIMITE_PRESUPUESTO_DESCUBRIMIENTO" not in stats["motivos_incompleto"]:
                    stats["motivos_incompleto"].append("LIMITE_PRESUPUESTO_DESCUBRIMIENTO")
                break

            try:
                status, datos, cabeceras = await cliente.pedir(
                    f"{base}&_from={desde}&_to={desde + VENTANA - 1}"
                )
            except TopeAgotadoError as exc:
                # Bug confirmado en v11 (revisión de Codex, 13-ago): esto
                # caía en el `except Exception` de abajo y se contaba como
                # "esta categoría falló". No es eso — es que no queda
                # presupuesto para NADA más, ni esta categoría ni las que
                # siguen. Se corta la corrida entera, no solo esta página.
                presupuesto_agotado = True
                stats["fallos"].append(
                    {
                        "categoria": categoria["name"],
                        "error": f"presupuesto de requests agotado: {exc}",
                    }
                )
                log(
                    f"    ✗ {categoria['name'][:34]:<34} "
                    "SIN PRESUPUESTO — se corta el descubrimiento aquí"
                )
                break
            except Exception as exc:
                stats["categorias_fallidas"] += 1
                stats["fallos"].append(
                    {"categoria": categoria["name"], "error": str(exc)[:200]}
                )
                log(f"    ✗ {categoria['name'][:34]:<34} {str(exc)[:60]}")
                break

            if status >= 400 or not isinstance(datos, list):
                stats["categorias_fallidas"] += 1
                stats["fallos"].append(
                    {"categoria": categoria["name"], "error": f"HTTP {status}"}
                )
                break

            if desde == 0:
                total_leido = total_desde_resources(cabeceras.get("resources", ""))
                total_conocido = total_leido is not None
                total = total_leido if total_conocido else 0
                resources_desconocido = not total_conocido

            if not datos:
                break

            for crudo in datos:
                if not isinstance(crudo, dict):
                    continue

                producto = parsear_producto(crudo)

                if not producto or not producto.sku_id:
                    continue

                if producto.sku_id in encontrados:
                    continue

                encontrados[producto.sku_id] = producto
                nuevos += 1

                if cupo_categoria and nuevos >= cupo_categoria:
                    break

                if limite and len(encontrados) >= limite:
                    break

            pagina_corta = len(datos) < VENTANA
            desde += VENTANA

            # Techo efectivo de esta categoría. Si conocemos el total real
            # (cabecera `resources` legible), es el menor entre ese total y
            # lo que VTEX deja paginar. Si NO lo conocemos — bug confirmado
            # en v11: la cabecera ausente se leía como total=0 y la
            # categoría se truncaba a la primera página en silencio — el
            # único límite disponible es el techo de paginado de VTEX:
            # seguimos pidiendo páginas mientras vengan llenas, en vez de
            # asumir un total de cero.
            techo = min(total, TOPE_VTEX) if total_conocido else TOPE_VTEX

            if desde >= techo:
                # Llegamos al techo sin ver una página corta antes: no hay
                # forma de saber si eso fue el final real de la categoría o
                # si el techo (de VTEX o nuestro, cuando el total no se
                # conocía) cortó antes de tiempo.
                if not pagina_corta:
                    truncada = True
                break

            if pagina_corta:
                break

        if resources_desconocido:
            stats["categorias_resources_desconocido"] += 1
            log(
                f"    ⚠ {categoria['name'][:34]:<34} sin cabecera 'resources' "
                "legible — paginado por longitud de página, no por total"
            )

        if truncada:
            stats["categorias_truncadas_por_vtex"] += 1
            stats["fallos"].append(
                {
                    "categoria": categoria["name"],
                    "error": (
                        f"quedó truncada en {desde} productos: alcanzó el "
                        f"techo de paginado (~{TOPE_VTEX}) sin una página "
                        "corta que confirmara el final"
                    ),
                }
            )

        if nuevos:
            stats["categorias_productivas"] += 1

            if landing:
                stats["landings_seo"] += 1

            usadas.append(categoria["name"])

            total_texto = str(total) if total_conocido else "?"

            log(
                f"    [{categoria['nivel']}]{'*' if landing else ' '} "
                f"{categoria['name'][:33]:<33} "
                f"total={total_texto:<6} +{nuevos:<4} "
                f"acumulado={len(encontrados)}"
                + (f"/{limite}" if limite else "")
            )
        elif presupuesto_agotado or presupuesto_fase_agotado:
            # Se cortó antes de saber si la categoría tenía algo. No es
            # "vacía" (eso afirmaría que se confirmó que no hay nada) ni
            # "sin candidatos" (eso afirmaría que se vio el catálogo y no
            # sobrevivió nada al filtro). Ninguna de las dos es cierta acá.
            pass
        elif total_conocido and total == 0:
            stats["categorias_vacias"] += 1
        else:
            # La categoría TENÍA productos y ninguno sobrevivió al filtro
            # (sin stock, nombre basura, o ya visto en otra rama). No es
            # lo mismo que estar vacía y no puede contarse como si lo
            # fuera: es el único lugar donde se vería un filtro demasiado
            # agresivo comiéndose catálogo real.
            stats["categorias_sin_candidatos"] += 1

        if presupuesto_agotado:
            stats["completo"] = False
            if "REQUEST_BUDGET_EXHAUSTED" not in stats["motivos_incompleto"]:
                stats["motivos_incompleto"].append("REQUEST_BUDGET_EXHAUSTED")
            break

        if presupuesto_fase_agotado:
            # Ya quedó marcado LIMITE_PRESUPUESTO_DESCUBRIMIENTO antes de
            # entrar a esta categoría (o dentro de su paginación); acá solo
            # cortamos el recorrido de categorías, igual que arriba.
            break

        if truncada:
            stats["completo"] = False
            if "TRUNCAMIENTO_VTEX" not in stats["motivos_incompleto"]:
                stats["motivos_incompleto"].append("TRUNCAMIENTO_VTEX")

    stats["skus_descubiertos"] = len(encontrados)
    stats["categorias_usadas"] = sorted(set(usadas))

    ESTADISTICAS_CATALOGO.clear()
    ESTADISTICAS_CATALOGO.update(stats)

    log(
        f"  Descubiertos: {len(encontrados)} SKUs de "
        f"{stats['categorias_productivas']} subcategorías "
        f"({stats['landings_seo']} landings SEO*, "
        f"{stats['categorias_vacias']} vacías, "
        f"{stats['categorias_fallidas']} fallidas)"
    )

    if stats["categorias_truncadas_por_vtex"]:
        log(
            f"  AVISO: {stats['categorias_truncadas_por_vtex']} categorías "
            f"superan el tope de paginado de VTEX (~{TOPE_VTEX}) y quedaron "
            "incompletas. Detalle en el manifiesto."
        )

    if stats["categorias_resources_desconocido"]:
        log(
            f"  AVISO: {stats['categorias_resources_desconocido']} categorías "
            "no trajeron cabecera 'resources' legible. Se paginaron igual "
            "por longitud de página; revisa si VTEX cambió el contrato."
        )

    # Motivos, no solo el booleano. Antes esta línea asumía que la única
    # causa de "no completo" era `--catalogo` — bug confirmado en v11:
    # tanto agotar el presupuesto de requests como una categoría truncada
    # por VTEX dejaban el manifiesto en `completo: true` porque nada más
    # los reportaba.
    if not stats["completo"]:
        motivos = ", ".join(stats["motivos_incompleto"]) or "desconocido"
        log(f"  Descubrimiento INCOMPLETO. Motivos: {motivos}.")

        if "LIMITE_CATALOGO" in stats["motivos_incompleto"]:
            log(f"    · cortado a propósito por --catalogo {limite}.")

        if "LIMITE_POR_CATEGORIA" in stats["motivos_incompleto"]:
            log(
                f"    · muestra ANCHA a propósito por --por-categoria "
                f"{por_categoria}: esto es una muestra, no el catálogo real."
            )

        if "LIMITE_PRESUPUESTO_DESCUBRIMIENTO" in stats["motivos_incompleto"]:
            log(
                "    · se detuvo antes de tiempo a propósito, para reservar "
                "presupuesto de medición/auditoría (--presupuesto-"
                "descubrimiento). No es un agotamiento del tope global."
            )

        if "REQUEST_BUDGET_EXHAUSTED" in stats["motivos_incompleto"]:
            log(
                "    · se agotó el tope GLOBAL de requests a mitad del "
                "descubrimiento. Lo ya encontrado se mide igual, pero esto"
                " NO es el catálogo completo."
            )

        if "TRUNCAMIENTO_VTEX" in stats["motivos_incompleto"]:
            log(
                "    · una o más categorías superaron lo que VTEX deja "
                "paginar. Detalle en 'fallos' del manifiesto."
            )

    # Clasificación explícita (v13, Prioridad 3) — para que un verificador
    # o un analista no tengan que interpretar una lista de strings libres:
    #
    #   COMPLETO                   se recorrió el árbol entero sin cortes.
    #   LIMITADO_DELIBERADAMENTE   se cortó por una decisión consciente
    #                              (del usuario vía --catalogo/--por-
    #                              categoria, o del propio motor protegiendo
    #                              presupuesto de medición). El catálogo es
    #                              parcial A PROPÓSITO, no por falla.
    #   INCOMPLETO_NO_PLANEADO     se cortó por agotar el tope GLOBAL o por
    #                              un techo estructural de VTEX. Nadie lo
    #                              pidió así.
    MOTIVOS_DELIBERADOS = {
        "LIMITE_CATALOGO",
        "LIMITE_POR_CATEGORIA",
        "LIMITE_PRESUPUESTO_DESCUBRIMIENTO",
    }
    MOTIVOS_NO_PLANEADOS = {"REQUEST_BUDGET_EXHAUSTED", "TRUNCAMIENTO_VTEX"}

    motivos_set = set(stats["motivos_incompleto"])

    if not motivos_set:
        stats["clasificacion"] = "COMPLETO"
    elif motivos_set & MOTIVOS_NO_PLANEADOS:
        stats["clasificacion"] = "INCOMPLETO_NO_PLANEADO"
    elif motivos_set & MOTIVOS_DELIBERADOS:
        stats["clasificacion"] = "LIMITADO_DELIBERADAMENTE"
    else:
        stats["clasificacion"] = "INCOMPLETO_NO_PLANEADO"

    ESTADISTICAS_CATALOGO["clasificacion"] = stats["clasificacion"]

    if not encontrados:
        # El motivo real importa. Encontrado durante la verificación de
        # v12: con un tope tan bajo que se agota ANTES de terminar la
        # primera categoría, el flujo llegaba hasta acá igual — sin
        # levantar `TopeAgotadoError`, porque esa excepción se atrapa
        # adentro del bucle para poder registrar lo ya encontrado — y el
        # mensaje genérico de "revisa conexión" mandaría a buscar un
        # problema de red que no existe.
        if "REQUEST_BUDGET_EXHAUSTED" in stats["motivos_incompleto"]:
            raise TopeAgotadoError(
                f"Tope de {cliente.tope} requests agotado antes de "
                "descubrir un solo producto. No es un problema de red: "
                "subí --tope o bajá --intervalo si el presupuesto es "
                "insuficiente incluso para el árbol de categorías."
            )

        raise RuntimeError(
            "No se obtuvo ningún producto del catálogo. "
            "Revisa conexión o si el sitio está bloqueando."
        )

    return list(encontrados.values())


def seleccionar(
    productos: list[Producto],
    muestra: int,
    semilla: Any,
) -> list[Producto]:
    """
    Elige qué SKUs medir, de forma reproducible y SIN guardar nada.

    El problema que resuelve
    ------------------------
    Mientras se prueba con 100 SKUs en vez de 20.000, la selección no
    puede ser al azar: dos corridas medirían productos distintos y no
    habría con qué comparar. La solución de v10 fue congelar la canasta
    en `panel.json` — un archivo de entrada, justo lo que este motor no
    quiere tener.

    La solución sin archivo
    -----------------------
    Se ordena por `md5(semilla:sku_id)` y se toman los N primeros. Es
    determinístico: misma semilla + mismo catálogo = misma muestra, sin
    persistir una línea. Y es estable frente al crecimiento del catálogo:
    un SKU nuevo solo entra si su hash cae entre los N más bajos, así que
    agregar productos no revuelve la selección entera.

    Cambiar `--semilla` da otra muestra, también reproducible.
    """

    ordenados = sorted(productos, key=lambda p: p.sku_id)

    if muestra <= 0 or muestra >= len(ordenados):
        return ordenados

    def clave(producto: Producto) -> str:
        crudo = f"{semilla}:{producto.sku_id}".encode("utf-8")
        return hashlib.md5(crudo).hexdigest()

    elegidos = sorted(ordenados, key=clave)[:muestra]

    return sorted(elegidos, key=lambda p: p.sku_id)


# Stock de cadena de la corrida actual: sku_id -> unidades.
# Se refresca al inicio de cada corrida, NO se hereda del panel.
STOCK_CADENA: dict[str, str] = {}

# Identificador de la corrida. Se estampa en cada fila y en la evidencia
# cruda, para que una fila del CSV se pueda rastrear hasta el JSON exacto
# que la produjo.
RUN_ID = ""
GUARDAR_EVIDENCIA = True

# Navegador de la corrida. `orderForm` necesita un contexto limpio por
# llamada porque el carrito es estado de sesión (ver consultar_orderform).
NAVEGADOR = None

# Contadores de contrato: si VTEX cambia la forma del JSON, dejaríamos de
# parsear logística y el dataset diría "nadie despacha nada" en vez de
# "el proveedor cambió el formato". Se comparan al final de la corrida.
CONTRATO = {"respuestas_ok": 0, "con_logistica": 0}

# Resultado de la auditoría muestreada.
#
# En v10 la auditoría medía y no reportaba: la consola anunciaba
# "Auditando 5 SKUs por ambas vías" y el RESUMEN no decía nada. Una
# auditoría que solo escribe una columna del CSV no cumple su función —
# nadie va a abrir el CSV a buscar discrepancias que no sabe que existen.
# Si `simulation` un día empieza a mentir, tiene que gritar.
#
# Unidades separadas (bug confirmado en v11, revisión de Codex, 13-ago):
# la auditoría ELIGE por SKU, pero MIDE por SKU × nodo. Con 5 SKUs
# elegidos y 2 nodos, `solicitadas` contaba 5 y `realizadas` llegaba a 10
# — la consola imprimía cosas como "10/5 SKUs contrastados", más de 100%
# y sin sentido. `skus_solicitados` y `mediciones_esperadas` quedan
# separados a propósito para que nunca se vuelvan a mezclar.
AUDITORIA: dict[str, Any] = {
    "skus_solicitados": 0,
    "mediciones_esperadas": 0,
    "mediciones_realizadas": 0,
    "coinciden": 0,
    "discrepan": 0,
    "fallidas": 0,
    "por_nodo": {},
    "detalle": [],
}

# Avisos que hasta v11 solo existían en la consola de una corrida que
# nadie estaba mirando (bug confirmado en v11, revisión de Codex, 13-ago:
# el aviso de CSV viejos en la carpeta raíz, migración de salida). Una
# corrida desatendida (cron, por ejemplo) no dejaba rastro de que el aviso
# hubiera ocurrido. Ahora también quedan en el manifiesto.
AVISOS: list[str] = []

# Cómo se eligió qué medir. Queda en el manifiesto porque sin panel en
# disco, esto es lo ÚNICO que permite reproducir la selección de una
# corrida vieja: semilla + tamaño + total descubierto.
SELECCION: dict[str, Any] = {}

# Estado de la fase de MEDICIÓN (v13).
#
# Por qué existe
# --------------
# Hasta v12, si `TopeAgotadoError` aparecía a mitad de la medición, `medir()`
# la atrapaba en su `except Exception` genérico y la convertía en una Fila
# más: `logistics_status=EXCEPTION`, `price_status=NO_PRICE`. El bucle de
# `main()` seguía llamando a `medir()` para cada SKU×nodo que quedaba, cada
# una repitiendo el mismo diagnóstico. El resultado en Kali (smoke de 10
# SKU, 13-ago) fueron filas EXCEPTION/NO_PRICE que un analista podría leer
# como "VTEX no tiene precio para este SKU" cuando la verdad es "nunca se
# intentó — no quedaba presupuesto". Confirmado por Codex (aporte_codex.txt
# §2.8) como BUG CONFIRMADO EN EJECUCIÓN REAL.
#
# `TopeAgotadoError` es un estado de la CORRIDA, no del SKU. v13 la deja
# propagar fuera de `medir()` (ver el bloque `except TopeAgotadoError: raise`
# antes del `except Exception` genérico) y el bucle de medición en `main()`
# la atrapa UNA vez: deja de pedir, conserva lo ya medido, y registra qué
# pares (sku_id, node_id) quedaron sin intentar — en vez de inventarles una
# fila de "excepción" que nunca ocurrió.
MEDICION: dict[str, Any] = {
    "esperadas": 0,
    "realizadas": 0,
    "completa": True,
    "motivo": "",
    "pendientes_total": 0,
    "pendientes_muestra": [],
}

# Estado de la fase de STOCK DE CADENA (v13). Mismo criterio que MEDICION:
# si el presupuesto se agota a mitad del refresco de stock, es un fallo de
# fase, no algo que se deba disfrazar de "aviso" genérico y seguir como si
# nada (ver refrescar_stock_cadena y la llamada en main()).
STOCK_CADENA_ESTADO: dict[str, Any] = {
    "completo": True,
    "motivo": "",
}

# Umbral de alarma de firma por nodo (v13, Prioridad 5).
#
# Un NODO nuevo o con una firma mal transcrita en `NODOS` no produce un
# error: VTEX simplemente nunca devuelve esa combinación de
# warehouseId/dockId/courierId/courierName, y CADA fila de ese nodo sale
# `OPERADOR_EXTERNO` / `UNVERIFIED` sin que nada lo grite. Eso es
# exactamente la lección de Atlassian citada en el brief de v13: una
# operación de gran alcance (medir un catálogo entero contra un nodo)
# no debe poder producir cientos de resultados inválidos sin que alguien
# se entere a mitad de la corrida.
#
# Esto NO es un preflight que corra antes del bucle grande (eso exigiría
# infraestructura de testing que el brief pide explícitamente evitar); es
# un freno que mira lo que ya se midió: si un nodo acumula
# UMBRAL_ALARMA_FIRMA mediciones sin un solo MATCH/MATCH_SELLER_RAIZ/
# MATCH_SIN_CONFIRMAR, se avisa una vez, fuerte, con la corrida todavía
# corriendo — no se aborta (un nodo puede legítimamente no tener cobertura
# para una muestra chica; abortar perdería datos válidos del otro nodo).
UMBRAL_ALARMA_FIRMA = 5
MATCHES_VALIDOS = {"MATCH", "MATCH_SELLER_RAIZ", "MATCH_SIN_CONFIRMAR"}
ALARMA_FIRMA_DISPARADA: dict[str, bool] = {}


def guardar_evidencia(
    producto: Producto,
    nodo: Nodo,
    metodo: str,
    status: int,
    datos: Any,
) -> None:
    """
    Archiva la respuesta cruda de VTEX, comprimida, una línea por medición.

    Por qué
    -------
    Llevamos tres bugs de parser (all_headers, el fallback destructivo, el
    sellerChain que descartaba nodos válidos). Cada uno costó datos que
    hubo que volver a scrapear. Con la evidencia cruda guardada, un bug de
    interpretación se arregla reprocesando el archivo: cero requests
    nuevos, cero molestias al servidor, y la historia se recupera.

    También es lo que permite extraer mañana un campo que hoy ignoramos
    sin tener que volver atrás en el tiempo.

    Formato: un JSONL comprimido por corrida. Un archivo por medición
    serían 200 archivos diminutos por día.

    Nunca se guardan cabeceras: las cookies y tokens de sesión no deben
    tocar el disco.
    """

    if not RUN_ID or not GUARDAR_EVIDENCIA:
        return

    destino = SALIDA / "raw" / ahora().strftime("%Y-%m-%d")
    destino.mkdir(parents=True, exist_ok=True)

    registro = {
        "run_id": RUN_ID,
        "captured_at": ahora().isoformat(timespec="seconds"),
        "sku_id": producto.sku_id,
        "product_id": producto.product_id,
        "node_id": nodo.node_id,
        "method": metodo,
        "http_status": status,
        "response": datos,
    }

    archivo = destino / f"{RUN_ID}.jsonl.gz"

    try:
        with gzip.open(archivo, "at", encoding="utf-8") as salida:
            salida.write(json.dumps(registro, ensure_ascii=False) + "\n")
    except Exception as exc:
        # La evidencia es valiosa, pero no vale perder la corrida por ella.
        log(f"      aviso: no se pudo guardar evidencia ({type(exc).__name__})")


def reconciliar(
    sim_datos: Any,
    of_datos: Any,
    producto: Producto,
    nodo: Nodo,
) -> str:
    """
    Contrasta lo que dijo `simulation` contra lo que dijo `orderForm`.

    El modo por defecto confía en `simulation` porque cuesta 1 request en
    vez de 3. Esa confianza nunca fue verificada. Esta función mide una
    muestra por ambas vías y registra si difieren, para que si un día
    `simulation` empieza a mentir, lo sepamos por evidencia y no por
    intuición.

    No corrige nada ni reemplaza nada: solo deja constancia.
    """

    marcas: list[str] = []

    item_sim = extraer_item(sim_datos, producto)
    item_of = extraer_item(of_datos, producto)

    if item_sim.get("sellingPrice") != item_of.get("sellingPrice"):
        marcas.append(
            f"PRECIO_DIFIERE(sim={soles(item_sim.get('sellingPrice')) or '-'}"
            f",of={soles(item_of.get('sellingPrice')) or '-'})"
        )

    if item_sim.get("availability") != item_of.get("availability"):
        marcas.append(
            f"DISPONIBILIDAD_DIFIERE(sim={item_sim.get('availability') or '-'}"
            f",of={item_of.get('availability') or '-'})"
        )

    log_sim = extraer_logistica(sim_datos, nodo)
    log_of = extraer_logistica(of_datos, nodo)

    if log_sim.get("warehouseId") != log_of.get("warehouseId"):
        marcas.append(
            f"ALMACEN_DIFIERE(sim={log_sim.get('warehouseId') or '-'}"
            f",of={log_of.get('warehouseId') or '-'})"
        )

    if item_sim.get("sellerChain") != item_of.get("sellerChain"):
        marcas.append("SELLER_DIFIERE")

    return " | ".join(marcas) if marcas else "COINCIDEN"


async def refrescar_stock_cadena(
    cliente: Cliente,
    productos: list[Producto],
) -> dict[str, str]:
    """
    Trae el stock de CADENA fresco para todos los SKUs, en pocas requests.

    Por qué existe
    --------------
    Hasta v07 la columna de stock salía de `panel.json`, o sea de una foto
    tomada cuando se creó el panel. Nunca se refrescaba: en la corrida del
    13-ago, 0 de 100 SKUs cambiaron su valor entre corridas, y 0 de 100
    diferían entre sucursales. Parecía stock vivo y era una foto vieja de
    un agregado. En una serie de tiempo eso es una mina: alguien grafica
    "tendencia de stock" y le sale una línea plana que se cree.

    Por qué es barato
    -----------------
    El endpoint de catálogo acepta varios `fq=productId:` en una misma
    consulta, así que 100 SKUs se resuelven en 3 requests en vez de 100.
    El costo de tener el dato fresco es despreciable.

    Qué NO es
    ---------
    `AvailableQuantity` es del canal completo, no de una sucursal. Sirve
    para saber si la cadena todavía tiene lo que a esta tienda se le acabó
    — que es justamente la señal que interesa — pero no dice en qué tienda
    está ese stock.
    """

    stock: dict[str, str] = {}

    # El catálogo no pagina más de 50 por consulta; 40 deja margen.
    LOTE = 40

    lotes = [productos[i:i + LOTE] for i in range(0, len(productos), LOTE)]

    log(f"Refrescando stock de cadena: {len(productos)} SKUs en {len(lotes)} requests...")

    for numero, lote in enumerate(lotes, 1):

        filtros = "&".join(
            f"fq=productId:{p.product_id}" for p in lote if p.product_id
        )

        if not filtros:
            continue

        url = (
            f"{BASE_URL}/api/catalog_system/pub/products/search"
            f"?{filtros}&_from=0&_to=49&sc={SALES_CHANNEL}"
        )

        try:
            status, datos, _ = await cliente.pedir(url)
        except TopeAgotadoError:
            # v13: igual que en medir() — el presupuesto agotado no es "este
            # lote falló", es que no queda margen para NADA más. Se propaga
            # para que main() lo trate como fallo de fase (stock_cadena),
            # no como un lote más que hay que loguear y seguir de largo.
            raise
        except Exception as exc:
            log(f"  lote {numero}/{len(lotes)}: falló ({str(exc)[:70]})")
            continue

        if status >= 400 or not isinstance(datos, list):
            log(f"  lote {numero}/{len(lotes)}: HTTP {status}")
            continue

        for crudo in datos:
            if not isinstance(crudo, dict):
                continue

            for item in crudo.get("items") or []:
                if not isinstance(item, dict):
                    continue

                vendedores = item.get("sellers") or []

                if not vendedores:
                    continue

                oferta = vendedores[0].get("commertialOffer") or {}
                cantidad = oferta.get("AvailableQuantity")

                if cantidad is not None:
                    stock[s(item.get("itemId"))] = s(cantidad)

        log(f"  lote {numero}/{len(lotes)}: {len(stock)} SKUs con stock leído")

    faltantes = [p.sku_id for p in productos if p.sku_id not in stock]

    if faltantes:
        # Un SKU que ya no aparece en el catálogo es información: puede
        # haber sido dado de baja. Se deja vacío, no en cero.
        log(f"  {len(faltantes)} SKUs sin stock de cadena (no volvieron del catálogo)")

    return stock


def calcular_stock_signal(availability: str, chain_stock: str) -> str:
    """
    Cruza los dos niveles de stock en una sola señal accionable.

        DISPONIBLE       la sucursal lo tiene
        QUIEBRE_LOCAL    la sucursal NO lo tiene pero la cadena SÍ   <- oportunidad
        QUIEBRE_CADENA   no queda en ninguna parte
        QUIEBRE_LOCAL_?  la sucursal no lo tiene y no sabemos de la cadena

    `QUIEBRE_LOCAL` es la fila que vale dinero: el producto existe, hay
    unidades en la cadena, y esta tienda no las tiene.
    """

    if not availability:
        return ""

    if availability == "available":
        return "DISPONIBLE"

    try:
        cadena = int(chain_stock)
    except (TypeError, ValueError):
        return "QUIEBRE_LOCAL_CADENA_DESCONOCIDA"

    return "QUIEBRE_LOCAL" if cadena > 0 else "QUIEBRE_CADENA"


# ===========================================================================
# EXTRACCIÓN DE LA RESPUESTA VTEX
# ===========================================================================


def extraer_logistica(datos: Any, nodo: Nodo) -> dict[str, str]:
    """
    Devuelve el bloque logístico resuelto, con su contexto de selección.

    Prioriza el SLA del nodo esperado; si no está, el primer `delivery`.
    Nunca inventa: si no hay deliveryIds, devuelve vacío.

    NOVEDAD v09 — se conserva lo que antes se tiraba:

        slaSeleccionado  el SLA que VTEX marcó como elegido (`selectedSla`)
        slaCount         cuántos SLAs había en total para ese ítem
        esSeleccionado   si el SLA que usamos ES el seleccionado

    Sin esto, elegir "el SLA cuyo almacén coincide con el esperado" es un
    sesgo de confirmación con forma de código: buscamos lo que queríamos
    encontrar y reportamos que lo encontramos.
    """

    candidatos: list[dict[str, str]] = []
    total_slas = 0
    seleccionado_global = ""

    for bloque in buscar_todo(datos, "logisticsInfo"):
        if not isinstance(bloque, list):
            continue

        for info in bloque:
            if not isinstance(info, dict):
                continue

            seleccionado = s(info.get("selectedSla"))

            if seleccionado and not seleccionado_global:
                seleccionado_global = seleccionado

            for sla in info.get("slas") or []:
                if not isinstance(sla, dict):
                    continue

                total_slas += 1

                identidad = s(sla.get("name")) or s(sla.get("id"))

                for entrega in sla.get("deliveryIds") or []:
                    if not isinstance(entrega, dict):
                        continue

                    if not entrega.get("warehouseId"):
                        continue

                    candidatos.append(
                        {
                            "warehouseId": s(entrega.get("warehouseId")),
                            "dockId": s(entrega.get("dockId")),
                            "courierId": s(entrega.get("courierId")),
                            "courierName": s(entrega.get("courierName")),
                            "polygonName": s(sla.get("polygonName")),
                            "slaName": identidad,
                            "deliveryChannel": (
                                s(sla.get("deliveryChannel"))
                                or s(info.get("selectedDeliveryChannel"))
                            ),
                            "shippingCost": soles(sla.get("price")),
                            "shippingEstimate": s(sla.get("shippingEstimate")),
                            # Se compara contra el selectedSla del MISMO
                            # bloque, no contra uno global: cada ítem tiene
                            # su propia selección.
                            "esSeleccionado": (
                                "SI"
                                if seleccionado and identidad == seleccionado
                                else "NO"
                            ),
                        }
                    )

    if not candidatos:
        return {}

    def con_contexto(elegido: dict[str, str]) -> dict[str, str]:
        salida = dict(elegido)
        salida["slaSeleccionado"] = seleccionado_global
        salida["slaCount"] = str(total_slas)
        return salida

    for candidato in candidatos:
        if candidato["warehouseId"] == nodo.warehouse_id:
            return con_contexto(candidato)

    for candidato in candidatos:
        if candidato["deliveryChannel"] == "delivery":
            return con_contexto(candidato)

    return con_contexto(candidatos[0])


def evaluar_calidad(fila: "Fila", item: dict[str, str], producto: Producto) -> str:
    """
    Reglas de calidad sobre la fila ya construida.

    Solo tres, y las tres detectan mentiras del dato, no incumplimientos
    de estilo. Un motor de calidad con veinte reglas ceremoniales sobre un
    dataset de 200 filas es burocracia; estas tres han fallado de verdad.

        DQ_SKU_DISTINTO   VTEX devolvió un SKU que no pedimos. Si pasa,
                          el precio pertenece a OTRO producto. Ya se
                          verificaba en orderForm; en simulation no.
        DQ_PRECIO_MAYOR   sellingPrice > listPrice. Rompe la aritmética
                          del descuento y suele indicar mala lectura.
        DQ_PRECIO_CERO    precio 0 con el ítem disponible. Casi siempre
                          es un placeholder, no una ganga.

    Devuelve las que fallaron separadas por "|", o vacío si la fila está
    limpia.
    """

    fallos: list[str] = []

    if item and item.get("id") and s(item.get("id")) != producto.sku_id:
        fallos.append("DQ_SKU_DISTINTO")

    try:
        lista = float(item.get("listPrice") or 0)
        venta = float(item.get("sellingPrice") or 0)
        multiplicador = float(item.get("unitMultiplier") or 1)

        # OJO: en productos por peso, sellingPrice > listPrice es lo
        # NORMAL (pieza vs kilo), no un error. Marcarlo como falla de
        # calidad fue un falso positivo en 7 de 100 SKUs: la regla
        # detectó una diferencia de unidad, no un dato mentiroso.
        if multiplicador == 1 and lista > 0 and venta > lista:
            fallos.append("DQ_PRECIO_MAYOR")

        if venta == 0 and fila.availability == "available":
            fallos.append("DQ_PRECIO_CERO")

    except (TypeError, ValueError):
        pass

    return "|".join(fallos)


def extraer_item(datos: Any, producto: Producto) -> dict[str, str]:
    items = datos.get("items") if isinstance(datos, dict) else None

    if not isinstance(items, list):
        return {}

    for item in items:
        if not isinstance(item, dict):
            continue

        if s(item.get("id")) != producto.sku_id:
            continue

        cadena = item.get("sellerChain")

        if isinstance(cadena, list):
            cadena_texto = " > ".join(s(v) for v in cadena if v is not None)
        else:
            cadena_texto = s(cadena)

        return {
            "price": s(item.get("price")),
            "sellingPrice": s(item.get("sellingPrice")),
            "listPrice": s(item.get("listPrice")),
            "availability": s(item.get("availability")),
            "sellerChain": cadena_texto,
            "name": s(item.get("name")),
            # VTEX los expone directamente: no hay que inferir el peso.
            "measurementUnit": s(item.get("measurementUnit")),
            "unitMultiplier": s(item.get("unitMultiplier")),
        }

    return {}


def extraer_direccion(datos: Any) -> dict[str, str]:
    """
    Lee la dirección de shippingData, NO la primera que aparezca.

    Una búsqueda recursiva ciega puede devolver la dirección de un
    pickup point o de clientProfileData, que no es lo que se envió.
    """

    if isinstance(datos, dict):
        envio = datos.get("shippingData")

        if isinstance(envio, dict):
            direccion = envio.get("address")

            if isinstance(direccion, dict):
                return {
                    "postalCode": s(direccion.get("postalCode")),
                    "neighborhood": s(direccion.get("neighborhood")),
                }

        direccion = datos.get("address")

        if isinstance(direccion, dict) and direccion.get("postalCode"):
            return {
                "postalCode": s(direccion.get("postalCode")),
                "neighborhood": s(direccion.get("neighborhood")),
            }

    return {}


def clasificar_fulfillment(
    logistica: dict[str, str],
    node_id: str,
    seller_confirmado: bool,
) -> str:
    """
    Quién despacha realmente el producto.

    Un mismo sitio web vende cosas que salen de lugares muy distintos, y
    mezclarlas en una sola métrica de "catálogo Makro" es engañoso:

      tienda            almacén de la sucursal + seller de la sucursal
      tienda_raiz       almacén de la sucursal, pero seller principal
      proveedor         stock del proveedor (dropshipping)
      generico_pv       operación genérica de PlazaVea, no de Makro
      desconocido       firma que todavía no sabemos leer

    Ejemplo real capturado el 13-ago (Crepera VENTUS):
        warehouseId  STKPRVVNT      -> STocK PRoVeedor VeNTus
        dockId       DCKGNRCPV-MK   -> DoCK GeNéRiCo PlazaVea
        courierId    DD-PV-GNRC     -> Despacho Domicilio PlazaVea GeNéRiCo
    Ese producto se compra en el sitio de Makro pero no sale de Makro.
    """

    if not logistica:
        return ""

    if node_id in NODOS:
        return "tienda" if seller_confirmado else "tienda_raiz"

    warehouse = logistica.get("warehouseId", "").upper()
    courier = logistica.get("courierId", "").upper()
    dock = logistica.get("dockId", "").upper()

    if warehouse.startswith("STKPRV") or "PRV" in warehouse:
        return "proveedor"

    if "-PV-" in courier or "PV" in dock or "GNRC" in courier:
        return "generico_pv"

    return "desconocido"


def identificar_nodo(
    logistica: dict[str, str],
    seller_chain: str,
) -> tuple[str, str, bool]:
    """
    Identifica QUÉ sucursal devolvió VTEX, buscando en el catálogo de firmas.

    Esto es una BÚSQUEDA, no una afirmación: en vez de preguntar "¿es el
    nodo que yo esperaba?" pregunta "¿de quién es esta firma?".

    CAMBIO EN v07 — el sellerChain ya no vota para descartar.

    Hasta v06, un producto con firma logística perfecta de Santa Anita
    (SW-359-MKO + DC-359-MKO + DD-359-MKO + polígono correcto) pero con
    `sellerChain: ["1"]` se descartaba como nodo desconocido. Era un error:
    ese producto SÍ sale del almacén de Santa Anita, lo dicen los cuatro
    identificadores. Lo único distinto es que pertenece al catálogo del
    seller principal y no al inventario del seller-sucursal, así que su
    precio es el del canal y no el de la tienda.

    Es un caso real y merece etiqueta propia, no ser tratado como ajeno.

    Devuelve (node_id, polygon_drift, seller_confirmado).
    """

    if not logistica:
        return "NONE", "", False

    # De "1 > plazaveamko360" nos quedamos con "plazaveamko360".
    cadena_final = ""

    if seller_chain:
        cadena_final = seller_chain.split(">")[-1].strip()

    observado = {
        "warehouseId": logistica.get("warehouseId", ""),
        "dockId": logistica.get("dockId", ""),
        "courierId": logistica.get("courierId", ""),
        "courierName": logistica.get("courierName", ""),
    }

    for node_id, nodo in NODOS.items():
        esperado = nodo.firma_core()

        coincide = all(
            observado.get(campo, "") == valor
            for campo, valor in esperado.items()
            if campo != "sellerChain"
        )

        if not coincide:
            continue

        # El sellerChain ahora INFORMA, no descarta.
        seller_confirmado = cadena_final == nodo.seller_chain

        deriva = ""

        if logistica.get("polygonName", "") != nodo.polygon_name:
            deriva = (
                f"esperado={nodo.polygon_name} "
                f"observado={logistica.get('polygonName', '')}"
            )

        return node_id, deriva, seller_confirmado

    return "OTHER", "", False


# ===========================================================================
# MEDICIÓN
# ===========================================================================


async def consultar_simulation(
    cliente: Cliente,
    producto: Producto,
    nodo: Nodo,
) -> tuple[int, Any]:
    """
    Una sola request devuelve precio + logística.

    La simulación NO guarda estado en el contexto: la geolocalización va
    en el body de cada llamada. Por eso el contexto del navegador se
    puede reutilizar sin riesgo de contaminación entre mediciones.
    """

    cuerpo = {
        "items": [
            {
                "id": int(producto.sku_id),
                "quantity": 1,
                "seller": producto.seller_id or "1",
            }
        ],
        "country": nodo.country,
        "postalCode": nodo.postal_code,
        "geoCoordinates": [nodo.longitude, nodo.latitude],
    }

    status, datos, _ = await cliente.pedir(
        f"{BASE_URL}/api/checkout/pub/orderforms/simulation?sc={SALES_CHANNEL}",
        metodo="POST",
        body=cuerpo,
    )

    return status, datos


async def consultar_orderform(
    cliente: Cliente,
    producto: Producto,
    nodo: Nodo,
) -> tuple[int, Any]:
    """
    Flujo completo: orderForm -> items -> shippingData.

    Más lento (3 requests) pero es el único que devuelve la dirección
    resuelta por el backend.

    ATENCIÓN — EL ORDERFORM ES UN CARRITO, O SEA ESTADO DE SESIÓN
    ------------------------------------------------------------
    `simulation` es sin estado: la geolocalización viaja en el body y se
    puede reutilizar el mismo contexto de navegador para 200 mediciones.
    `orderForm` NO: VTEX lo ata a la cookie de sesión y devuelve SIEMPRE
    el mismo carrito. Los productos se acumulan.

    Consecuencia medida el 13-ago con la auditoría al 5% y al 10%:

        corrida 1  → solo el 1er SKU auditado devolvió datos
        corrida 2  → solo el 1er SKU auditado devolvió datos
        corrida 3  → solo el 1er SKU auditado devolvió datos

    Determinista. A partir del segundo, el carrito ya tenía el producto
    anterior, `items[0].id` no coincidía con el SKU pedido, y la
    verificación de identidad abortaba con carrito vacío. La
    reconciliación reportaba `of=-` como si simulation y orderForm
    difirieran, cuando en realidad era nuestro propio carrito sucio.

    El bug entró en v05, cuando se pasó a reutilizar el contexto para
    ahorrar cargas de página — correcto para `simulation`, silenciosamente
    roto para `orderForm`. Estuvo oculto porque el fallback casi nunca se
    dispara; la auditoría lo destapó en su primera corrida, que es
    exactamente para lo que sirve auditar.

    Por eso cada llamada usa un contexto propio: carrito limpio garantizado.
    Cuesta ~300ms y cero requests extra.
    """

    if NAVEGADOR is None:
        # Sin navegador (tests unitarios): se usa el cliente tal cual.
        return await _orderform_con_cliente(cliente, producto, nodo)

    contexto = await NAVEGADOR.new_context(
        locale="es-PE",
        timezone_id="America/Lima",
        viewport={"width": 1440, "height": 900},
    )

    api_previa = cliente.api

    try:
        cliente.api = contexto.request
        return await _orderform_con_cliente(cliente, producto, nodo)
    finally:
        cliente.api = api_previa
        await contexto.close()


async def _orderform_con_cliente(
    cliente: Cliente,
    producto: Producto,
    nodo: Nodo,
) -> tuple[int, Any]:
    """Cuerpo del flujo orderForm. Asume que el carrito está limpio."""

    status, datos, _ = await cliente.pedir(f"{BASE_URL}/api/checkout/pub/orderForm")

    if status >= 400 or not isinstance(datos, dict):
        return status, datos

    order_form_id = datos.get("orderFormId")

    if not order_form_id:
        return status, {"__error": "orderForm sin orderFormId"}

    status, datos, _ = await cliente.pedir(
        f"{BASE_URL}/api/checkout/pub/orderForm/{order_form_id}"
        f"/items?sc={SALES_CHANNEL}",
        metodo="POST",
        body={
            "orderItems": [
                {
                    "id": int(producto.sku_id),
                    "quantity": 1,
                    "seller": producto.seller_id or "1",
                }
            ],
            "expectedOrderFormSections": ORDERFORM_SECTIONS,
        },
    )

    if status >= 400:
        return status, datos

    # Verificación de identidad: VTEX debe devolver el SKU que pedimos.
    # Si devuelve otro, todo lo que sigue mediría el producto equivocado.
    items = datos.get("items") if isinstance(datos, dict) else None

    if isinstance(items, list) and items:
        devuelto = s(items[0].get("id"))

        if devuelto and devuelto != producto.sku_id:
            return status, {
                "__error": (
                    f"SKU inesperado: se pidió {producto.sku_id}, "
                    f"VTEX devolvió {devuelto}"
                )
            }

    status, datos, _ = await cliente.pedir(
        f"{BASE_URL}/api/checkout/pub/orderForm/{order_form_id}"
        f"/attachments/shippingData",
        metodo="POST",
        body={
            "address": nodo.direccion(),
            "clearAddressIfPostalCodeNotFound": False,
            "expectedOrderFormSections": ORDERFORM_SECTIONS,
        },
    )

    return status, datos


def construir_fila(
    producto: Producto,
    nodo: Nodo,
    status: int,
    datos: Any,
    metodo: str,
    momento: datetime,
) -> Fila:

    fila = Fila(
        timestamp=momento.isoformat(timespec="seconds"),
        fecha=momento.strftime("%Y-%m-%d"),
        branch=nodo.branch,
        node_id=nodo.node_id,
        product_id=producto.product_id,
        sku_id=producto.sku_id,
        sku_ref=producto.sku_ref,
        ean=producto.ean,
        product_name=producto.product_name,
        brand=producto.brand,
        category=producto.category,
        seller_id=producto.seller_id,
        url=producto.url,
        chain_stock=STOCK_CADENA.get(producto.sku_id, ""),
        postal_sent=nodo.postal_code,
        method=metodo,
        http_status=str(status),
        run_id=RUN_ID,
    )

    item = extraer_item(datos, producto)
    logistica = extraer_logistica(datos, nodo)
    direccion = extraer_direccion(datos)

    # ---- PRECIO: se guarda SIEMPRE que VTEX lo haya devuelto ------------
    # La validación logística decide si es atribuible a la sucursal,
    # no si merece existir en el dataset.
    fila.price = soles(item.get("sellingPrice"))
    fila.list_price = soles(item.get("listPrice"))
    fila.base_price = soles(item.get("price"))
    fila.price_cents = s(item.get("sellingPrice"))

    fila.measurement_unit = item.get("measurementUnit", "")
    fila.unit_multiplier = item.get("unitMultiplier", "")

    try:
        lista = float(item.get("listPrice") or 0)
        venta = float(item.get("sellingPrice") or 0)
        multiplicador = float(item.get("unitMultiplier") or 1)

        # Precio por unidad de medida: es el único comparable entre
        # tiendas cuando el producto se vende por peso.
        if multiplicador > 0 and venta > 0:
            fila.price_per_unit = f"{venta / 100 / multiplicador:.4f}"

        # El descuento solo tiene sentido cuando ambos precios están en
        # la MISMA base. En un producto por peso, listPrice es por kilo y
        # sellingPrice es por pieza: restarlos daría un descuento inventado.
        if multiplicador == 1 and lista > 0 and venta > 0 and lista >= venta:
            fila.discount_pct = f"{(lista - venta) / lista * 100:.2f}"

    except (TypeError, ValueError):
        pass

    fila.availability = item.get("availability", "")
    fila.stock_signal = calcular_stock_signal(fila.availability, fila.chain_stock)
    fila.seller_chain = item.get("sellerChain", "")

    if not fila.product_name and item.get("name"):
        fila.product_name = item["name"]

    # ---- LOGÍSTICA ------------------------------------------------------
    fila.warehouse_id = logistica.get("warehouseId", "")
    fila.dock_id = logistica.get("dockId", "")
    fila.courier_id = logistica.get("courierId", "")
    fila.courier_name = logistica.get("courierName", "")
    fila.polygon_name = logistica.get("polygonName", "")
    fila.sla_name = logistica.get("slaName", "")
    fila.delivery_channel = logistica.get("deliveryChannel", "")
    fila.shipping_cost = logistica.get("shippingCost", "")
    fila.shipping_estimate = logistica.get("shippingEstimate", "")

    fila.postal_resolved = direccion.get("postalCode", "")
    fila.neighborhood_resolved = direccion.get("neighborhood", "")

    # ---- CONTEXTO DE SELECCIÓN DEL SLA ----------------------------------
    fila.sla_selected = logistica.get("slaSeleccionado", "")
    fila.sla_count = logistica.get("slaCount", "")

    if logistica:
        # OJO: `simulation` NO elige SLA — solo lista opciones. `selectedSla`
        # viene vacío ahí por diseño del endpoint, no porque haya ambigüedad.
        # Verificado contra la captura real: orderForm trae
        # selectedSla="Despacho a Domicilio Makro Lima Moderna", simulation
        # lo trae nulo con el mismo SLA en la lista.
        #
        # Por eso la ausencia de selección solo es ambigua cuando hay MÁS DE
        # UNA opción. Con una sola, no estamos eligiendo nada: es la única.
        try:
            cuantos = int(fila.sla_count or 0)
        except (TypeError, ValueError):
            cuantos = 0

        elegido_explicito = logistica.get("esSeleccionado") == "SI"
        unico_sin_ambiguedad = not fila.sla_selected and cuantos == 1

        confirmado = elegido_explicito or unico_sin_ambiguedad

        fila.fulfillment_confirmed = "SI" if confirmado else "NO"

        if elegido_explicito:
            fila.sla_status = "SLA_SELECTED_MATCH"
        elif unico_sin_ambiguedad:
            # Única opción: no hay decisión que confirmar.
            fila.sla_status = "SLA_UNICO"
        elif not fila.sla_selected:
            # Varias opciones y VTEX no eligió: el que elige somos nosotros,
            # y eso hay que decirlo en vez de disfrazarlo de confirmación.
            fila.sla_status = f"SLA_SELECTION_UNKNOWN_ENTRE_{cuantos}"
        else:
            # Hay un SLA elegido y NO es el del almacén que buscábamos.
            fila.sla_status = "SLA_AVAILABLE_NOT_SELECTED"

    # Los errores lógicos de VTEX viajan con HTTP 200 y un __error en el
    # cuerpo. Si solo miráramos el status, ese mensaje se perdería y la
    # fila quedaría vacía sin explicación.
    if isinstance(datos, dict) and datos.get("__error"):
        fila.error = str(datos["__error"])[:400]
        fila.error_class = "ITEM_NOT_RETURNED"

    # ---- VEREDICTO ------------------------------------------------------
    if status >= 400:
        fila.logistics_status = "HTTP_ERROR"
        fila.node_resolved = "NONE"
        fila.price_status = "UNVERIFIED" if fila.price else "NO_PRICE"
        fila.error = str(datos)[:400]
        fila.error_class = "RATE_LIMITED" if status == 429 else "HTTP_ERROR"
        fila.dq_flags = evaluar_calidad(fila, item, producto)
        return fila

    node_resolved, deriva, seller_confirmado = identificar_nodo(
        logistica, fila.seller_chain
    )

    fila.node_resolved = node_resolved
    fila.polygon_drift = deriva
    fila.fulfillment_type = clasificar_fulfillment(
        logistica, node_resolved, seller_confirmado
    )

    if not logistica:
        if not item:
            # VTEX ni siquiera devolvió el SKU: no se pudo cotizar.
            fila.logistics_status = "ITEM_NOT_RETURNED"

        elif fila.availability == "withoutStock":
            # Sin stock AHORA. Es transitorio y puede volver mañana.
            # No confundir con falta de cobertura: el producto sí se
            # vende en esa sucursal, hoy no hay unidades.
            fila.logistics_status = "SIN_STOCK"

        elif fila.availability == "cannotBeDelivered":
            # No se despacha a esa zona. Es estructural, no transitorio.
            fila.logistics_status = "NO_COVERAGE"

        elif fila.availability and fila.availability != "available":
            # Cualquier otro estado que VTEX invente en el futuro.
            # Se registra tal cual en vez de forzarlo a una categoría.
            fila.logistics_status = f"NO_DISPONIBLE_{fila.availability}"

        else:
            # Disponible pero sin SLA: anomalía real, vale la pena mirarla.
            fila.logistics_status = "NO_LOGISTICS"

    elif node_resolved == nodo.node_id:
        if fila.fulfillment_confirmed != "SI":
            # El almacén de la sucursal aparece entre las opciones, pero
            # VTEX no seleccionó ese despacho. Encontramos el almacén; no
            # confirmamos que sea lo que el cliente recibiría.
            fila.logistics_status = "MATCH_SIN_CONFIRMAR"

        else:
            # Firma logística confirmada. La única diferencia es de quién
            # viene el precio: del seller de la tienda o del principal.
            fila.logistics_status = (
                "MATCH" if seller_confirmado else "MATCH_SELLER_RAIZ"
            )

    elif node_resolved == "OTHER":
        # No es ninguna sucursal conocida. Antes decía "nodo desconocido";
        # ahora `fulfillment_type` dice de qué operador se trata.
        fila.logistics_status = "OPERADOR_EXTERNO"

    else:
        fila.logistics_status = f"MISMATCH_RESOLVED_{node_resolved}"

    if not fila.price:
        fila.price_status = "NO_PRICE"
    elif fila.logistics_status == "MATCH":
        fila.price_status = "VERIFIED"
    elif fila.logistics_status == "MATCH_SELLER_RAIZ":
        # Atribuible a la sucursal (despacha desde su almacén) pero el
        # precio lo pone el seller principal. Etiqueta propia para que un
        # filtro `price_status = 'VERIFIED'` no los mezcle sin querer.
        fila.price_status = "VERIFIED_SELLER_RAIZ"
    elif fila.logistics_status == "MATCH_SIN_CONFIRMAR":
        # Ni VERIFIED ni UNVERIFIED: la evidencia apunta a la sucursal
        # pero no alcanza para afirmarlo. Un caso ambiguo debe seguir
        # siendo ambiguo, no convertirse en MATCH por conveniencia.
        fila.price_status = "QUALIFIED"
    else:
        fila.price_status = "UNVERIFIED"

    if not fila.error_class and fila.logistics_status in (
        "SIN_STOCK",
        "NO_COVERAGE",
        "ITEM_NOT_RETURNED",
    ):
        # No es un fallo nuestro: es el estado comercial del retailer.
        fila.error_class = "BUSINESS_UNAVAILABLE"

    fila.dq_flags = evaluar_calidad(fila, item, producto)

    return fila


async def medir(
    cliente: Cliente,
    producto: Producto,
    nodo: Nodo,
    modo: str,
    auditar: bool = False,
) -> Fila:
    """
    Mide un SKU en una sucursal. Nunca lanza: siempre devuelve una fila.

    Si `auditar` es True, mide por AMBAS vías y registra si difieren
    (`recon_status`). No cambia el resultado: solo lo contrasta.
    """

    momento = ahora()

    try:
        if modo == "orderform":
            status, datos = await consultar_orderform(cliente, producto, nodo)
            usado = "orderform"

            guardar_evidencia(producto, nodo, usado, status, datos)

            return construir_fila(producto, nodo, status, datos, usado, momento)

        status, datos = await consultar_simulation(cliente, producto, nodo)
        usado = "simulation"

        guardar_evidencia(producto, nodo, usado, status, datos)

        if status < 400:
            CONTRATO["respuestas_ok"] += 1

            if extraer_logistica(datos, nodo):
                CONTRATO["con_logistica"] += 1

        # ---- AUDITORÍA MUESTREADA ----
        if auditar:
            fase_previa = cliente.fase
            cliente.fase = "auditoria"

            try:
                au_status, au_datos = await consultar_orderform(
                    cliente, producto, nodo
                )

                guardar_evidencia(producto, nodo, "orderform_auditoria",
                                  au_status, au_datos)

                veredicto = reconciliar(datos, au_datos, producto, nodo)

                fila = construir_fila(
                    producto, nodo, status, datos, usado, momento
                )
                fila.recon_status = veredicto

                AUDITORIA["mediciones_realizadas"] += 1
                por_nodo = AUDITORIA["por_nodo"].setdefault(
                    nodo.node_id,
                    {"branch": nodo.branch, "realizadas": 0, "coinciden": 0,
                     "discrepan": 0, "fallidas": 0},
                )
                por_nodo["realizadas"] += 1

                if veredicto == "COINCIDEN":
                    AUDITORIA["coinciden"] += 1
                    por_nodo["coinciden"] += 1
                else:
                    AUDITORIA["discrepan"] += 1
                    por_nodo["discrepan"] += 1
                    AUDITORIA["detalle"].append(
                        {
                            "sku_id": producto.sku_id,
                            "producto": producto.product_name[:70],
                            "node_id": nodo.node_id,
                            "veredicto": veredicto,
                        }
                    )
                    log(f"      RECONCILIACIÓN: {veredicto}")

                return fila

            except TopeAgotadoError:
                # v13: el presupuesto agotado durante la auditoría NO es
                # una auditoría fallida — es la corrida entera quedándose
                # sin margen. Contarla en AUDITORIA["fallidas"] la
                # disfrazaría de discrepancia técnica puntual. Se propaga
                # tal cual para que main() la trate como el fallo global
                # que es (ver MEDICION más abajo).
                raise

            except Exception as exc:
                AUDITORIA["fallidas"] += 1
                por_nodo = AUDITORIA["por_nodo"].setdefault(
                    nodo.node_id,
                    {"branch": nodo.branch, "realizadas": 0, "coinciden": 0,
                     "discrepan": 0, "fallidas": 0},
                )
                por_nodo["fallidas"] += 1
                AUDITORIA["detalle"].append(
                    {
                        "sku_id": producto.sku_id,
                        "producto": producto.product_name[:70],
                        "node_id": nodo.node_id,
                        "veredicto": f"AUDITORIA_FALLIDA: {type(exc).__name__}",
                    }
                )
                log(f"      auditoría falló ({type(exc).__name__}), sigo con simulation")

            finally:
                cliente.fase = fase_previa

        item_sim = extraer_item(datos, producto)
        disponibilidad = item_sim.get("availability", "")

        # ¿Vale la pena el fallback?
        #
        # Si la simulación ya dijo `cannotBeDelivered` / `withoutStock`,
        # esa YA es la respuesta definitiva: esa sucursal no despacha ese
        # producto. Reintentar con orderForm no la va a cambiar y son 3
        # requests de regalo al servidor.
        respuesta_definitiva = disponibilidad in ("cannotBeDelivered", "withoutStock")

        necesita_fallback = (
            status < 400
            and not extraer_logistica(datos, nodo)
            and not respuesta_definitiva
        )

        if necesita_fallback:
            fase_previa = cliente.fase
            cliente.fase = "fallback"

            try:
                of_status, of_datos = await consultar_orderform(
                    cliente, producto, nodo
                )
            finally:
                cliente.fase = fase_previa

            guardar_evidencia(producto, nodo, "orderform_fallback",
                              of_status, of_datos)

            # REGLA: jamás cambiar una respuesta que TIENE el SKU por una
            # que no lo tiene.
            #
            # Si el orderForm no logra agregar el producto, VTEX devuelve
            # un carrito vacío: sin precio, sin availability, sin dirección.
            # Sobrescribir con eso destruía el precio que la simulación ya
            # había entregado, que es exactamente lo que NO podemos hacer.
            item_of = extraer_item(of_datos, producto)

            if item_of.get("sellingPrice") or not item_sim.get("sellingPrice"):
                status, datos, usado = of_status, of_datos, "orderform_fallback"
            else:
                usado = "simulation_conservada"

        return construir_fila(producto, nodo, status, datos, usado, momento)

    except TopeAgotadoError:
        # v13, Prioridad 1 — el fix central de esta versión.
        #
        # Bug confirmado en ejecución real (aporte_codex.txt §2.8, smoke de
        # 10 SKU en Kali el 13-ago): sin este `except` específico,
        # `TopeAgotadoError` caía en el `except Exception` genérico de
        # abajo y se convertía en una Fila con logistics_status=EXCEPTION,
        # price_status=NO_PRICE — indistinguible de "VTEX no tiene precio
        # para este SKU". No es eso: es que la corrida se quedó sin
        # presupuesto y ESTE SKU (ni los que faltan) nunca se llegaron a
        # pedir. Es un estado de la CORRIDA, no del SKU — "medir() nunca
        # lanza" es una garantía sobre fallos LOCALES (HTTP, parseo,
        # timeout), no sobre el presupuesto global agotándose a mitad de
        # camino. Se re-lanza para que el bucle de medición en main() la
        # trate como lo que es: detener la fase, conservar lo ya medido, y
        # dejar constancia en manifiesto.medicion de qué quedó sin
        # intentar (no de qué "falló").
        raise

    except Exception as exc:
        # Una excepción tampoco puede costarnos la fila: se registra
        # el intento con el error, para que quede rastro en el dataset.
        fila = Fila(
            timestamp=momento.isoformat(timespec="seconds"),
            fecha=momento.strftime("%Y-%m-%d"),
            branch=nodo.branch,
            node_id=nodo.node_id,
            product_id=producto.product_id,
            sku_id=producto.sku_id,
            sku_ref=producto.sku_ref,
            ean=producto.ean,
            product_name=producto.product_name,
            brand=producto.brand,
            category=producto.category,
            seller_id=producto.seller_id,
            url=producto.url,
            chain_stock=STOCK_CADENA.get(producto.sku_id, ""),
            postal_sent=nodo.postal_code,
            method=modo,
            run_id=RUN_ID,
            logistics_status="EXCEPTION",
            node_resolved="NONE",
            price_status="NO_PRICE",
            error_class="NETWORK_ERROR" if "Timeout" in type(exc).__name__ else "UNKNOWN",
            error=f"{type(exc).__name__}: {exc}"[:400],
        )
        return fila


# ===========================================================================
# ESCRITURA
# ===========================================================================


def archivar_si_cambio_el_esquema(momento: datetime) -> list[str]:
    """
    Protege el histórico cuando cambian las columnas.

    El esquema del CSV se deriva de los campos de `Fila`
    (`COLUMNAS = list(Fila().__dict__.keys())`), así que agregar un campo
    agrega una columna. Eso es cómodo, pero en modo append es peligroso:
    el archivo viejo ya tiene su cabecera escrita, y `DictWriter` seguiría
    apendeando filas con MÁS columnas debajo de una cabecera con MENOS.
    El CSV queda desalineado en silencio y el histórico se corrompe.

    En vez de fallar a mitad de corrida, se detecta ANTES de medir: el
    archivo viejo se renombra con la fecha de corte y la corrida empieza
    uno nuevo. No se pierde nada y los dos quedan legibles por separado.
    """

    archivados: list[str] = []

    for nodo in NODOS.values():
        destino = SALIDA / nodo.archivo

        if not destino.exists():
            continue

        with destino.open(encoding="utf-8-sig") as archivo:
            cabecera = archivo.readline().strip()

        if not cabecera:
            continue

        if cabecera.split(",") == COLUMNAS:
            continue

        sufijo = momento.strftime("%Y%m%d_%H%M%S")
        viejo = destino.with_name(f"{destino.stem}__esquema_anterior_{sufijo}.csv")

        destino.rename(viejo)
        archivados.append(viejo.name)

    return archivados


def apendear_csv(nodo: Nodo, filas: list[Fila], reiniciar: bool) -> Path:
    """
    Apendea al CSV de la sucursal.

    APPEND, no overwrite: cada corrida suma una observación fechada.
    Eso es lo que convierte el archivo en una serie de tiempo en vez de
    una foto que se pisa a sí misma.
    """

    SALIDA.mkdir(parents=True, exist_ok=True)

    destino = SALIDA / nodo.archivo

    if reiniciar and destino.exists():
        destino.unlink()

    nuevo = not destino.exists()

    with destino.open("a", encoding="utf-8-sig", newline="") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=COLUMNAS)

        if nuevo:
            escritor.writeheader()

        for fila in filas:
            escritor.writerow(asdict(fila))

    return destino


def evaluar_corrida(resumen: dict[str, Any]) -> tuple[bool, list[str], int]:
    """
    Decide si la corrida se presenta como completa y qué exit code le
    corresponde (v13, Prioridad 4).

    Nunca ocultar un falso éxito, pero tampoco perder datos válidos ya
    obtenidos solo para forzar un exit != 0: el motor sigue escribiendo
    CSV y manifiesto igual, esta función solo decide el veredicto final.

    Regla central: un límite DELIBERADO de descubrimiento (--catalogo,
    --por-categoria, --presupuesto-descubrimiento) NO es un fallo — el
    motor midió exactamente lo que prometía medir con la muestra
    solicitada, así que sigue siendo éxito (exit 0) aunque el catálogo sea
    parcial a propósito (`descubrimiento.clasificacion` lo deja explícito
    para quien lea el manifiesto). Lo que SÍ es un fallo operacional es que
    la MEDICIÓN o el STOCK DE CADENA se hayan cortado por agotar
    presupuesto: ahí la corrida entregó menos de lo que la selección ya
    elegida prometía, sin que el usuario lo haya pedido así — es
    exactamente el defecto B confirmado por Codex en Kali.

    Auditoría incompleta por causas ajenas al presupuesto (fallidas > 0 por
    error de red puntual, por ejemplo) queda como DECISIÓN ABIERTA — sigue
    sin forzar exit 1, igual que en v12. Si la auditoría se corta por
    agotar presupuesto, ya viene cubierto: esas mediciones son parte de
    `medicion` (la auditoría mide via `medir()`, mismo mecanismo).
    """

    motivos: list[str] = []

    medicion = resumen.get("medicion", {})
    stock = resumen.get("stock_cadena", {})

    if not medicion.get("completa", True):
        motivos.append("MEDICION_INCOMPLETA_POR_PRESUPUESTO")

    if not stock.get("completo", True):
        motivos.append("STOCK_CADENA_INCOMPLETO_POR_PRESUPUESTO")

    corrida_completa = not motivos
    codigo_salida = 1 if motivos else 0

    return corrida_completa, motivos, codigo_salida


def escribir_manifiesto(
    filas: list[Fila],
    cliente: Cliente,
    modo: str,
    inicio: datetime,
) -> int:

    resumen: dict[str, Any] = {
        "run_id": RUN_ID,
        "schema_version": SCHEMA_VERSION,
        "version_script": VERSION,
        "motor": MOTOR,
        "retailer": RETAILER,
        "contrato_vtex": dict(CONTRATO),
        "archivo_script": Path(__file__).name,
        "carpeta_salida": str(SALIDA),
        "corrida": inicio.isoformat(timespec="seconds"),
        "duracion_segundos": round(
            (ahora() - inicio).total_seconds(), 1
        ),
        "modo": modo,
        "requests_totales": cliente.contador,
        # Desglose por fase: sin esto no se puede explicar a dónde se fue
        # el gasto en un servidor que no es nuestro.
        "requests_por_fase": dict(sorted(cliente.por_fase.items())),
        "reintentos": cliente.reintentos_usados,
        "intervalo_segundos": cliente.intervalo,
        # Sin esto, una categoría que se pierde por un HTTP 500 desaparece
        # del registro y el catálogo queda cojo sin que nadie se entere.
        "descubrimiento": dict(ESTADISTICAS_CATALOGO),
        "seleccion": dict(SELECCION),
        "auditoria": {k: v for k, v in AUDITORIA.items()},
        # v13: estado explícito de fases que hasta v12 solo se podían
        # inferir leyendo filas EXCEPTION/NO_PRICE en el CSV. Ver
        # TopeAgotadoError, MEDICION y STOCK_CADENA_ESTADO.
        "medicion": dict(MEDICION),
        "stock_cadena": dict(STOCK_CADENA_ESTADO),
        # Avisos que antes solo vivían en la consola de una corrida que
        # nadie miraba en vivo (bug confirmado en v11, revisión de Codex).
        "avisos": list(AVISOS),
        "por_sucursal": {},
    }

    for node_id, nodo in NODOS.items():
        propias = [f for f in filas if f.node_id == node_id]
        con_match = sum(f.logistics_status in MATCHES_VALIDOS for f in propias)

        resumen["por_sucursal"][node_id] = {
            "branch": nodo.branch,
            "archivo": nodo.archivo,
            "mediciones": len(propias),
            "verified": sum(f.price_status == "VERIFIED" for f in propias),
            "unverified": sum(f.price_status == "UNVERIFIED" for f in propias),
            "sin_precio": sum(f.price_status == "NO_PRICE" for f in propias),
            "con_precio": sum(bool(f.price) for f in propias),
            "deriva_poligono": sum(bool(f.polygon_drift) for f in propias),
            "errores": sum(bool(f.error) for f in propias),
            # v13, Prioridad 5: visibilidad directa de si la firma de este
            # nodo está resolviendo algo real, sin tener que abrir el CSV.
            "match": con_match,
            "alarma_firma": bool(ALARMA_FIRMA_DISPARADA.get(node_id)),
        }

    corrida_completa, motivos_fallo_global, codigo_salida = evaluar_corrida(resumen)

    resumen["corrida_completa"] = corrida_completa
    resumen["motivos_fallo_global"] = motivos_fallo_global
    resumen["exit_code"] = codigo_salida

    SALIDA.mkdir(parents=True, exist_ok=True)

    MANIFEST_FILE.write_text(
        json.dumps(resumen, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return codigo_salida


# ===========================================================================
# CLI
# ===========================================================================


def parsear_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extractor de precios por sucursal Makro. Sin archivos de "
            "entrada: descubre el catálogo de la API en cada corrida. "
            "Un CSV por nodo, sin comparación embebida."
        )
    )

    parser.add_argument("--catalogo", type=int, default=0,
                        help=(
                            "Corta el descubrimiento al llegar a N SKUs. "
                            "0 = catálogo completo. Default: 0"
                        ))

    parser.add_argument("--muestra", type=int, default=0,
                        help=(
                            "Cuántos de los SKUs descubiertos se miden. La "
                            "selección es determinística (hash de --semilla + "
                            "sku_id): misma semilla, misma muestra, sin "
                            "guardar nada. 0 = medir todo. Default: 0"
                        ))

    parser.add_argument("--salida", default="",
                        help=(
                            "Carpeta de salida. Default: "
                            "<script>/salida/" + MOTOR + "/"
                        ))

    parser.add_argument("--auditoria", type=float, default=5.0,
                        help=(
                            "%% de mediciones que se contrastan simulation vs "
                            "orderForm para detectar discrepancias. 0 = "
                            "desactivada. Default: 5"
                        ))

    parser.add_argument("--sin-evidencia", action="store_true",
                        help="No guarda el JSON crudo en salida/raw/.")

    parser.add_argument("--por-categoria", type=int, default=0,
                        dest="por_categoria",
                        help=(
                            "Máximo de SKUs por subcategoría durante el "
                            "descubrimiento. 0 = sin límite (catálogo real). "
                            "Un valor bajo junto a --catalogo da una muestra "
                            "ANCHA en vez de las dos primeras categorías del "
                            "árbol. Default: 0"
                        ))

    parser.add_argument("--presupuesto-descubrimiento", type=int, default=0,
                        dest="presupuesto_descubrimiento",
                        help=(
                            "Tope de requests reservado SOLO para la fase de "
                            "descubrimiento, subordinado siempre al tope "
                            "global (nunca lo supera). 0 = automático: si "
                            "--muestra > 0, se reserva tope_efectivo menos el "
                            "margen que la medición y la auditoría van a "
                            "necesitar (mismo cálculo ×4 que ya se usa para "
                            "el tope automático), así el árbol de categorías "
                            "(~78%% vacío) no se come el presupuesto que la "
                            "medición prometida necesita. Con --muestra 0 "
                            "(medir todo lo descubierto) no hay forma de "
                            "estimar la reserva de antemano, así que queda "
                            "sin freno propio, igual que en v12. Default: 0"
                        ))

    parser.add_argument("--reiniciar", action="store_true",
                        help="Borra los CSV y empieza el histórico de cero.")

    parser.add_argument("--modo", choices=["simulation", "orderform"],
                        default="simulation",
                        help="simulation = 1 request. orderform = 3 requests.")

    parser.add_argument("--intervalo", type=float, default=1.5,
                        help="Segundos mínimos entre requests. Default: 1.5")

    parser.add_argument("--tope", type=int, default=0,
                        help=(
                            "Máximo de requests por corrida. 0 = automático: "
                            "el descubrimiento arranca con un tope prudente y "
                            "se recalcula al saber cuántos SKUs se van a "
                            "medir. El catálogo completo necesita subirlo a "
                            "mano, a propósito. Default: 0"
                        ))

    parser.add_argument("--reintentos", type=int, default=3,
                        help="Reintentos ante 429/5xx. Default: 3")

    parser.add_argument("--semilla", type=int, default=0,
                        help=(
                            "Sal del hash de selección. Cambiarla da otra "
                            "muestra, igual de reproducible. Default: 0"
                        ))

    parser.add_argument("--version", action="store_true",
                        help="Muestra la versión del script y termina.")

    parser.add_argument("--headed", action="store_true",
                        help="Muestra el navegador.")

    parser.add_argument("--canal", default="chrome",
                        help="Canal Playwright: chrome | msedge | chromium.")

    return parser.parse_args()


async def abrir_navegador(playwright, canal: str, headless: bool):
    if canal and canal != "chromium":
        try:
            return await playwright.chromium.launch(channel=canal, headless=headless)
        except Exception:
            log(f"Canal '{canal}' no disponible. Usando Chromium empaquetado.")

    return await playwright.chromium.launch(headless=headless)


async def main() -> int:
    argumentos = parsear_argumentos()

    if argumentos.version:
        log(f"{Path(__file__).name}  v{VERSION}")
        log("")
        log("Historial:")

        for linea in CAMBIOS:
            log(f"  {linea}")

        return 0

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log("Falta Playwright:")
        log("  python -m pip install playwright")
        log("  playwright install chromium")
        return 2

    inicio = ahora()

    global RUN_ID, GUARDAR_EVIDENCIA, SALIDA, MANIFEST_FILE
    RUN_ID = inicio.strftime("run_%Y%m%d_%H%M%S")
    GUARDAR_EVIDENCIA = not argumentos.sin_evidencia

    # Estado por-corrida (v13). Estos diccionarios son globales de módulo
    # para que escribir_manifiesto() los lea sin pasarlos por parámetro,
    # igual que AUDITORIA/SELECCION/AVISOS ya hacían en v11/v12. Si
    # `main()` se llama más de una vez en el mismo proceso (tests, uso
    # programático), tienen que arrancar limpios en cada corrida.
    MEDICION.clear()
    MEDICION.update(
        {
            "esperadas": 0,
            "realizadas": 0,
            "completa": True,
            "motivo": "",
            "pendientes_total": 0,
            "pendientes_muestra": [],
        }
    )
    STOCK_CADENA_ESTADO.clear()
    STOCK_CADENA_ESTADO.update({"completo": True, "motivo": ""})
    ALARMA_FIRMA_DISPARADA.clear()

    if argumentos.salida:
        SALIDA = Path(argumentos.salida).expanduser().resolve()
        MANIFEST_FILE = SALIDA / "ultima_corrida.json"

    SALIDA.mkdir(parents=True, exist_ok=True)

    # Aviso de reubicación.
    #
    # Hasta v10 todo caía en `salida/` a secas. Si esos archivos siguen
    # ahí, el motor NO los toca: mover el histórico de alguien por
    # iniciativa propia no es tarea de un extractor. Pero callarlo sería
    # peor — quedarían dos CSV con el mismo nombre en dos carpetas y la
    # próxima duda sería cuál de los dos es el bueno.
    if SALIDA.parent == RAIZ_SALIDA:
        viejos = [
            n.archivo for n in NODOS.values() if (RAIZ_SALIDA / n.archivo).exists()
        ]

        if viejos:
            log("AVISO: hay salida de versiones anteriores en la carpeta raíz:")

            for nombre in viejos:
                log(f"  {RAIZ_SALIDA / nombre}")

            log(f"v{VERSION.rsplit('-', 1)[-1]} escribe en {SALIDA} y no toca esos archivos.")
            log("")

            AVISOS.append(
                "CSV de versiones anteriores detectados en "
                f"{RAIZ_SALIDA}: {', '.join(viejos)}. No se tocaron. "
                f"Esta corrida escribió en {SALIDA}."
            )

    log("=" * 110)
    log(f"EXTRACTOR DE PRECIOS POR SUCURSAL — MAKRO PERÚ    v{VERSION}")
    log("=" * 110)
    log(f"Script     : {Path(__file__).name}")
    log(f"Run ID     : {RUN_ID}   (esquema {SCHEMA_VERSION})")
    log(f"Motor      : {MOTOR}  ({RETAILER})")
    log(f"Salida     : {SALIDA}")
    log(f"Modo       : {argumentos.modo}")
    log(
        "Catálogo   : "
        + (
            f"descubrimiento cortado en {argumentos.catalogo} SKUs"
            if argumentos.catalogo
            else "completo (se recorre todo el árbol)"
        )
        + (
            f" · máx {argumentos.por_categoria} por subcategoría"
            if argumentos.por_categoria
            else ""
        )
    )
    log(
        "Muestra    : "
        + (
            f"{argumentos.muestra} SKUs elegidos por hash (semilla "
            f"{argumentos.semilla}) — reproducible sin archivo"
            if argumentos.muestra
            else "se mide todo lo descubierto"
        )
    )
    log(
        f"Auditoría  : {argumentos.auditoria:.0f}% de mediciones "
        "contrastadas simulation vs orderForm"
        if argumentos.auditoria > 0
        else "Auditoría  : desactivada"
    )
    log(
        f"Evidencia  : {'no se guarda' if argumentos.sin_evidencia else 'raw/ (JSONL comprimido)'}"
    )
    log(f"Intervalo  : {argumentos.intervalo}s entre requests (secuencial)")
    log(
        "Tope       : "
        + (
            f"{argumentos.tope} requests (fijo)"
            if argumentos.tope
            else "automático (se recalcula al terminar el descubrimiento)"
        )
    )
    log("")
    log("Sin archivos de entrada: el catálogo se descubre de la API.")
    log("El precio se guarda SIEMPRE que VTEX lo devuelva.")
    log("price_status indica si es atribuible a la sucursal.")
    log("=" * 110)
    log("")

    filas: list[Fila] = []

    async with async_playwright() as playwright:

        navegador = await abrir_navegador(
            playwright, argumentos.canal, headless=not argumentos.headed
        )

        global NAVEGADOR
        NAVEGADOR = navegador

        try:
            contexto = await navegador.new_context(
                locale="es-PE",
                timezone_id="America/Lima",
                viewport={"width": 1440, "height": 900},
            )

            # Una sola carga de página para sembrar cookies públicas.
            # La simulación no guarda estado, así que no hace falta
            # recargar por cada medición.
            try:
                pagina = await contexto.new_page()
                await pagina.goto(BASE_URL, wait_until="domcontentloaded", timeout=45000)
                await pagina.wait_for_timeout(1200)
                await pagina.close()
            except Exception as exc:
                log(f"Aviso: no se pudo precargar la home ({type(exc).__name__}).")

            # Tope durante el descubrimiento.
            #
            # Falla CERRADO a propósito: el árbol tiene ~3354 nodos, así
            # que recorrerlo entero no cabe en este tope y la corrida se
            # corta con un error claro. Es lo que queremos — el catálogo
            # completo es una decisión consciente (`--tope 60000`), no
            # algo en lo que uno cae sin querer un martes a la tarde.
            cliente = Cliente(
                contexto.request,
                intervalo=argumentos.intervalo,
                reintentos=argumentos.reintentos,
                tope=argumentos.tope or 2000,
            )

            # ---------------- DESCUBRIMIENTO ----------------
            cliente.fase = "descubrimiento"

            # Presupuesto de fase (v13, Prioridad 2).
            #
            # El problema confirmado en Kali: con --muestra 10 y --tope 300,
            # el descubrimiento se gastó 286/300 requests (el árbol es ~78%
            # categorías vacías) y a la medición casi no le quedó nada. El
            # tope global se compartía entre las 4 fases sin que ninguna
            # supiera cuánto le tocaba.
            #
            # Si se pidió --muestra > 0, se puede estimar cuánto va a
            # necesitar medición ANTES de descubrir nada (muestra × nodos,
            # con el mismo margen ×4 que ya usa el recálculo automático del
            # tope para el fallback a orderForm + reintentos) y reservarle
            # ese margen al descubrimiento como techo. Con --muestra 0
            # (medir todo lo descubierto) el tamaño de la medición depende
            # de lo que aparezca, así que no hay nada que reservar de
            # antemano: mismo comportamiento que v12.
            presupuesto_descubrimiento = argumentos.presupuesto_descubrimiento

            if not presupuesto_descubrimiento and argumentos.muestra > 0:
                tope_efectivo = argumentos.tope or cliente.tope
                reserva_medicion = argumentos.muestra * len(NODOS) * 4
                reserva_stock = 10
                margen = 20
                minimo_descubrimiento = 20

                presupuesto_descubrimiento = max(
                    tope_efectivo - reserva_medicion - reserva_stock - margen,
                    minimo_descubrimiento,
                )

                if presupuesto_descubrimiento == minimo_descubrimiento:
                    log(
                        f"AVISO: --tope {tope_efectivo} es apenas suficiente "
                        f"para medir --muestra {argumentos.muestra} "
                        f"({reserva_medicion} requests estimados) — el "
                        f"descubrimiento queda con solo "
                        f"{minimo_descubrimiento} requests de margen. Subí "
                        "--tope si esto corta el descubrimiento antes de "
                        "encontrar suficientes SKUs."
                    )

                AVISOS.append(
                    "Presupuesto de descubrimiento reservado automáticamente"
                    f" en {presupuesto_descubrimiento} requests (de "
                    f"{tope_efectivo} totales), para dejarle "
                    f"~{reserva_medicion + reserva_stock} a medición/stock. "
                    "Ajustable con --presupuesto-descubrimiento."
                )

            log("DESCUBRIMIENTO DEL CATÁLOGO")
            log("-" * 110)

            if presupuesto_descubrimiento:
                log(
                    f"Presupuesto reservado para esta fase: "
                    f"{presupuesto_descubrimiento} de {cliente.tope} "
                    "requests totales."
                )

            catalogo = await descubrir_catalogo(
                cliente,
                limite=argumentos.catalogo,
                por_categoria=argumentos.por_categoria,
                presupuesto_fase=presupuesto_descubrimiento,
            )

            # ---------------- SELECCIÓN ----------------
            seleccion = seleccionar(
                catalogo, argumentos.muestra, argumentos.semilla
            )

            SELECCION.clear()
            SELECCION.update(
                {
                    "metodo": (
                        "hash_md5(semilla:sku_id)"
                        if argumentos.muestra
                        else "todo lo descubierto"
                    ),
                    "semilla": argumentos.semilla,
                    "muestra_pedida": argumentos.muestra,
                    "descubiertos": len(catalogo),
                    "seleccionados": len(seleccion),
                }
            )

            log("")
            log(
                f"SELECCIÓN: {len(seleccion)} de {len(catalogo)} SKUs descubiertos"
                + (
                    f"  (hash con semilla {argumentos.semilla} — "
                    "misma semilla, misma muestra)"
                    if argumentos.muestra
                    else ""
                )
            )
            log("-" * 110)

            for i, producto in enumerate(seleccion, 1):
                log(f"{i:03d}. SKU {producto.sku_id:<10} {producto.product_name[:60]}")

            total = len(seleccion) * len(NODOS)

            # Tope automático.
            #
            # En la corrida del 13-ago el tope fijo de 400 se alcanzó
            # EXACTO: 400 de 400. Un SKU más y la corrida habría reventado
            # a mitad de camino, perdiendo 10 minutos de trabajo.
            #
            # El costo real es: lo ya gastado en descubrimiento + una
            # medición por SKU y nodo, ×4 por si todas caen en el fallback
            # de orderForm (3 requests) más reintentos, + margen.
            if not argumentos.tope:
                # +10 por los lotes del refresco de stock de cadena.
                cliente.tope = cliente.contador + total * 4 + 60

                log("")
                log(
                    f"Tope recalculado: {cliente.tope} requests "
                    f"({cliente.contador} ya usados en descubrimiento + "
                    f"{total} mediciones con margen)"
                )

            # ---------------- STOCK DE CADENA ----------------
            log("")

            STOCK_CADENA.clear()
            cliente.fase = "stock_cadena"

            # v13: un solo interruptor para "no queda presupuesto, no
            # intentes medir nada" — lo puede encender stock_cadena (acá
            # abajo) o la propia medición (más abajo). Una vez encendido,
            # el resto de la corrida se limita a conservar lo que ya hay y
            # dejar constancia de lo que no se pudo hacer.
            presupuesto_agotado_global = False

            try:
                STOCK_CADENA.update(
                    await refrescar_stock_cadena(cliente, seleccion)
                )
            except TopeAgotadoError as exc:
                # v13: esto ya NO es "stock de cadena falló, seguimos con
                # chain_stock vacío" (ese sigue siendo el trato para
                # cualquier OTRO error, ver el except de abajo). Es que no
                # queda presupuesto para nada más, ni para medir. Entrar
                # igual al bucle de medición solo repetiría el mismo
                # TopeAgotadoError en el primer SKU.
                STOCK_CADENA_ESTADO["completo"] = False
                STOCK_CADENA_ESTADO["motivo"] = "REQUEST_BUDGET_EXHAUSTED"
                presupuesto_agotado_global = True

                log(
                    f"AVISO GRAVE: presupuesto agotado durante el refresco "
                    f"de stock de cadena ({exc}). No queda margen para "
                    "medir en esta corrida."
                )
                AVISOS.append(
                    "Presupuesto agotado durante stock_cadena: la corrida "
                    "no llegó a medir ningún SKU. Ver manifiesto.stock_cadena "
                    "y manifiesto.medicion."
                )
            except Exception as exc:
                # Si el catálogo falla por CUALQUIER otra razón, las
                # mediciones siguen: chain_stock queda vacío y stock_signal
                # lo dice. No vale la pena perder la corrida entera por una
                # columna de contexto.
                log(f"Aviso: no se pudo refrescar el stock de cadena ({exc}).")

            # ---------------- GUARDIA DE ESQUEMA ----------------
            archivados = archivar_si_cambio_el_esquema(inicio)

            if archivados:
                log("")
                log("AVISO: las columnas cambiaron respecto al CSV existente.")
                log("El histórico anterior se archivó para no corromperlo:")

                for nombre in archivados:
                    log(f"  -> {nombre}")

                AVISOS.append(
                    f"Esquema cambió respecto al CSV existente. Archivado: "
                    f"{', '.join(archivados)}."
                )

            log("")
            log("MEDICIONES")
            log("-" * 110)

            cliente.fase = "medicion"

            # Qué SKUs se auditan: mismo criterio determinístico que la
            # selección, con otra sal. Sin `random`, sin estado: una
            # corrida con la misma semilla audita exactamente los mismos
            # SKUs, que es lo que hace comparables dos auditorías.
            cupo_auditoria = int(len(seleccion) * argumentos.auditoria / 100)

            a_auditar = {
                p.sku_id
                for p in seleccionar(
                    seleccion, cupo_auditoria, f"auditoria:{argumentos.semilla}"
                )
            } if cupo_auditoria > 0 else set()

            # Unidades separadas a propósito (bug confirmado en v11): se
            # ELIGEN SKUs, pero se MIDEN SKU × nodo. Con 5 SKUs y 2 nodos
            # se esperan 10 mediciones auditadas, no 5.
            AUDITORIA["skus_solicitados"] = len(a_auditar)
            AUDITORIA["mediciones_esperadas"] = len(a_auditar) * len(NODOS)

            if a_auditar:
                log(
                    f"Auditando {len(a_auditar)} SKUs por ambas vías "
                    f"(simulation vs orderForm) — "
                    f"{AUDITORIA['mediciones_esperadas']} mediciones "
                    f"esperadas ({len(a_auditar)} SKUs × {len(NODOS)} nodos)."
                )
                log("")

            pares = [
                (producto, nodo) for producto in seleccion for nodo in NODOS.values()
            ]

            MEDICION["esperadas"] = len(pares)

            pendientes: list[tuple[str, str]] = []

            # Conteo corriente por nodo para la alarma de firma (v13,
            # Prioridad 5) — ver UMBRAL_ALARMA_FIRMA más arriba.
            conteo_por_nodo: dict[str, dict[str, int]] = {
                node_id: {"total": 0, "match": 0} for node_id in NODOS
            }

            if presupuesto_agotado_global:
                log(
                    "Medición SALTADA por completo: no queda presupuesto "
                    "(ver AVISO GRAVE de stock_cadena arriba)."
                )
                pendientes = [(p.sku_id, n.node_id) for p, n in pares]

            else:
                for idx, (producto, nodo) in enumerate(pares, 1):

                    try:
                        fila = await medir(
                            cliente,
                            producto,
                            nodo,
                            argumentos.modo,
                            auditar=producto.sku_id in a_auditar,
                        )
                    except TopeAgotadoError as exc:
                        # v13, Prioridad 1 — el corazón del fix de esta
                        # versión. Bug confirmado en Kali
                        # (aporte_codex.txt §2.8): hasta v12 cada par
                        # SKU×nodo que quedaba generaba su propia fila
                        # EXCEPTION/NO_PRICE, indistinguible de un fallo
                        # real de ese SKU. Acá se detiene UNA vez: lo ya
                        # medido se conserva, lo que falta queda listado en
                        # manifiesto.medicion.pendientes_muestra — no se le
                        # inventa una fila a algo que nunca se intentó.
                        presupuesto_agotado_global = True
                        pendientes = [
                            (p.sku_id, n.node_id) for p, n in pares[idx - 1:]
                        ]
                        log(
                            f"AVISO GRAVE: presupuesto agotado durante la "
                            f"medición ({exc}). Se detiene aquí — "
                            f"{len(filas)} filas ya medidas se conservan, "
                            f"{len(pendientes)} pares SKU×nodo quedan sin "
                            "intentar."
                        )
                        break

                    filas.append(fila)

                    cn = conteo_por_nodo[nodo.node_id]
                    cn["total"] += 1

                    if fila.logistics_status in MATCHES_VALIDOS:
                        cn["match"] += 1

                    if (
                        cn["total"] >= UMBRAL_ALARMA_FIRMA
                        and cn["match"] == 0
                        and not ALARMA_FIRMA_DISPARADA.get(nodo.node_id)
                    ):
                        # Prioridad 5 / lección Atlassian: un nodo con firma
                        # mal cargada en NODOS no produce un error — VTEX
                        # simplemente nunca devuelve esa combinación de
                        # warehouseId/dockId/courierId/courierName, y cada
                        # fila sale OPERADOR_EXTERNO/UNVERIFIED en silencio.
                        # Esto grita ANTES de que la corrida termine, sin
                        # abortar (el otro nodo puede estar perfectamente
                        # bien y sus datos no deben perderse).
                        ALARMA_FIRMA_DISPARADA[nodo.node_id] = True
                        log("!" * 110)
                        log(
                            f"ALARMA DE FIRMA: nodo {nodo.node_id} "
                            f"({nodo.branch}) acumula {cn['total']} "
                            "mediciones SIN un solo MATCH / "
                            "MATCH_SELLER_RAIZ / MATCH_SIN_CONFIRMAR. "
                            "Revisa si la firma logística en NODOS sigue "
                            "vigente (VTEX pudo cambiar warehouseId / "
                            "dockId / courierId / courierName) antes de "
                            "confiar en el dataset de esta sucursal."
                        )
                        log("!" * 110)
                        AVISOS.append(
                            f"Nodo {nodo.node_id} ({nodo.branch}): 0 MATCH "
                            f"en {cn['total']}+ mediciones — posible firma "
                            "logística obsoleta o mal cargada en NODOS."
                        )

                    log(
                        f"[{idx:03d}/{total}] {nodo.node_id} {nodo.branch:<12} "
                        f"{producto.product_name[:32]:<34} "
                        f"{fila.price or '-':>9} "
                        f"{fila.price_status:<11} "
                        f"{fila.logistics_status}"
                    )

                    if fila.polygon_drift:
                        log(f"          deriva de polígono: {fila.polygon_drift}")

                    if fila.error:
                        log(f"          ERROR: {fila.error[:120]}")

            MEDICION["realizadas"] = len(filas)
            MEDICION["completa"] = not pendientes
            MEDICION["pendientes_total"] = len(pendientes)
            MEDICION["pendientes_muestra"] = [
                {"sku_id": s, "node_id": n} for s, n in pendientes[:50]
            ]

            if pendientes:
                MEDICION["motivo"] = "REQUEST_BUDGET_EXHAUSTED"

            await contexto.close()

        finally:
            await navegador.close()

    # ---------------- SALIDA ----------------
    rutas = []

    for node_id, nodo in NODOS.items():
        propias = [f for f in filas if f.node_id == node_id]

        if propias:
            rutas.append(apendear_csv(nodo, propias, argumentos.reiniciar))

    codigo_salida = escribir_manifiesto(filas, cliente, argumentos.modo, inicio)

    log("")
    log("=" * 110)
    log("RESUMEN")
    log("=" * 110)

    for node_id, nodo in NODOS.items():
        propias = [f for f in filas if f.node_id == node_id]

        verificados = sum(f.price_status == "VERIFIED" for f in propias)
        con_precio = sum(bool(f.price) for f in propias)

        log(
            f"Nodo {node_id} {nodo.branch:<14} "
            f"mediciones={len(propias):<4} "
            f"con precio={con_precio:<4} "
            f"verificados={verificados:<4} "
            f"-> {nodo.archivo}"
        )

    # ---- AUDITORÍA ----
    #
    # v10 auditaba y no lo decía. Si la vía barata (simulation) empieza a
    # divergir de la cara (orderForm), el dataset entero queda en duda: no
    # puede enterarse quien abra el CSV por casualidad.
    #
    # Unidades separadas (bug confirmado en v11, revisión de Codex,
    # 13-ago): se eligen SKUs, se miden SKU × nodo. Mezclar ambas cifras
    # en el mismo "N/M" daba lecturas de más de 100% sin sentido.
    if AUDITORIA["skus_solicitados"]:
        log("")
        log(
            f"Auditoría        : {AUDITORIA['skus_solicitados']} SKUs "
            f"elegidos × {len(NODOS)} nodos = "
            f"{AUDITORIA['mediciones_esperadas']} mediciones esperadas"
        )
        log(
            f"                   {AUDITORIA['mediciones_realizadas']}/"
            f"{AUDITORIA['mediciones_esperadas']} mediciones contrastadas"
        )

        if AUDITORIA["discrepan"] == 0 and AUDITORIA["mediciones_realizadas"]:
            log(
                f"                   COINCIDEN — simulation y orderForm "
                f"dieron lo mismo en {AUDITORIA['coinciden']}/"
                f"{AUDITORIA['mediciones_realizadas']}"
            )
        elif AUDITORIA["discrepan"]:
            log("")
            log("!" * 110)
            log(
                f"DISCREPANCIAS: {AUDITORIA['discrepan']} de "
                f"{AUDITORIA['mediciones_realizadas']} mediciones auditadas "
                "difieren entre simulation y orderForm."
            )
            log("El modo por defecto confía en simulation. Si esto persiste,")
            log("esa confianza dejó de estar justificada.")

            for node_id, datos_nodo in sorted(AUDITORIA["por_nodo"].items()):
                if datos_nodo["discrepan"]:
                    log(
                        f"  {node_id} {datos_nodo['branch']:<14} "
                        f"{datos_nodo['discrepan']}/{datos_nodo['realizadas']} "
                        "discrepan"
                    )

            for caso in AUDITORIA["detalle"][:10]:
                if str(caso["veredicto"]).startswith("AUDITORIA_FALLIDA"):
                    continue

                log(
                    f"  {caso['node_id']} SKU {caso['sku_id']:<10} "
                    f"{caso['producto'][:40]:<42} {caso['veredicto']}"
                )

            log("!" * 110)

        if AUDITORIA["fallidas"]:
            log(
                f"                   {AUDITORIA['fallidas']} auditorías no se "
                "pudieron completar (la medición principal sí)"
            )

    log("")
    # ---- ALARMA DE CONTRATO ----
    #
    # Si VTEX cambia la forma del JSON, dejaríamos de parsear logística y
    # el dataset diría "ninguna sucursal despacha nada" — una conclusión
    # de negocio falsa nacida de un cambio técnico. Un corte tan brusco
    # nunca es real: es nuestro parser que quedó obsoleto.
    if CONTRATO["respuestas_ok"] >= 20:
        tasa = CONTRATO["con_logistica"] / CONTRATO["respuestas_ok"] * 100

        if tasa < 20:
            log("")
            log("!" * 110)
            log(
                f"ALARMA DE CONTRATO: solo el {tasa:.0f}% de las respuestas "
                f"OK trajo logística parseable "
                f"({CONTRATO['con_logistica']}/{CONTRATO['respuestas_ok']})."
            )
            log("Antes de creerle a este dataset, revisa si VTEX cambió el")
            log("formato de logisticsInfo. La evidencia cruda está en salida/raw/.")
            log("!" * 110)

    log("")
    log(f"Requests totales : {cliente.contador}")

    # Desglose: 233 requests donde la cuenta a mano daba 218 es un 7% sin
    # trazar. Sobre el catálogo completo ese 7% son ~45 minutos de carga
    # sobre un servidor ajeno que nadie sabría explicar.
    for fase, cantidad in sorted(
        cliente.por_fase.items(), key=lambda par: -par[1]
    ):
        log(f"  {fase:<16} {cantidad:>6}")

    log(f"Reintentos       : {cliente.reintentos_usados}")
    log(f"Duración         : {(ahora() - inicio).total_seconds():.0f}s")
    log("")

    for ruta in rutas:
        log(f"  {ruta}")

    log(f"  {MANIFEST_FILE}")
    log("=" * 110)

    # ---- VEREDICTO FINAL (v13, Prioridad 4) ----
    #
    # Hasta v12 esto era `return 0` fijo: el motor jamás fallaba cerrado,
    # sin importar si la medición se había cortado por presupuesto a mitad
    # de camino. `evaluar_corrida()` (adentro de escribir_manifiesto, con
    # el mismo criterio que queda grabado en manifiesto.motivos_fallo_global)
    # ya decidió: un límite deliberado de descubrimiento sigue siendo
    # exit 0 — el motor midió lo que prometía. Solo la medición o el stock
    # de cadena cortados por presupuesto agotado devuelven 1.
    if codigo_salida != 0:
        motivos_txt = []

        if not MEDICION.get("completa", True):
            motivos_txt.append(
                f"medición incompleta: {MEDICION.get('pendientes_total', 0)} "
                "pares SKU×nodo sin intentar por presupuesto agotado"
            )

        if not STOCK_CADENA_ESTADO.get("completo", True):
            motivos_txt.append("stock de cadena incompleto por presupuesto agotado")

        log("")
        log("!" * 110)
        log(f"CORRIDA INCOMPLETA (exit {codigo_salida}): {' · '.join(motivos_txt)}")
        log("Los datos ya medidos SÍ se conservaron en CSV/manifiesto — esto")
        log("no es pérdida de datos, es que la corrida no cumplió lo que")
        log("la selección prometía medir. Revisa manifiesto.medicion y")
        log("manifiesto.stock_cadena antes de tratar este dataset como una")
        log("corrida exitosa.")
        log("!" * 110)

    return codigo_salida


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.")
        sys.exit(130)
