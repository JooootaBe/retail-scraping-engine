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
import copy
import csv
import gzip
import hashlib
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
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
VERSION = "2026.08.24-19"

# Versión del esquema de salida. Se graba en CADA fila: cuando el CSV
# termine en Parquet/PostgreSQL, una fila vieja tiene que poder decir con
# qué reglas nació sin depender de un manifiesto aparte.
#
# v13 NO cambia ningún campo de `Fila` — solo corrige control de flujo y
# amplía el manifiesto — así que SCHEMA_VERSION se queda en "3". Un CSV
# de v12 y uno de v13 se apendean sin disparar `archivar_si_cambio_el_esquema`.
#
# v14 (bloque A de 1.1.0) TAMPOCO toca `Fila`: cambia dónde se escribe, cómo
# se selecciona y dos reglas de clasificación, ninguna columna. Se quedó en
# "3" a propósito, dejando el "4" para el bloque B.
#
# v15 (bloque B) SÍ agrega 22 campos a `Fila` — 56 -> 78 columnas — así que
# SCHEMA_VERSION sube a "4" (§6.7). Un CSV de v14 y uno de v15 NO son la
# misma tabla, y con una carpeta inmutable por corrida cada uno queda
# legible por separado; la capa de consolidación usa esta columna para
# decidir qué puede unir con qué.
#
# v18 sube a "5" SIN agregar ni renombrar una sola columna, y es el caso que
# esta constante existe para cubrir. `precio_mayorista` CAMBIA DE SIGNIFICADO:
# hasta v17 era `price − descuento` (mal) y desde v18 es `list_price −
# descuento`, y donde el escalón no le gana a la promoción unitaria ahora va
# vacío. Un mismo número en la misma columna quiere decir dos cosas distintas
# según la época. La serie histórica va a tener filas de las dos conviviendo:
# sin este bump no hay forma de separarlas, y un promedio que las mezcle es
# un promedio de dos definiciones. Vale también para `bi_umbral` (§6.2, ahora
# se conserva fuera de COMPLETO) y `descuento_mayorista_pct` (§6.5, base
# explícita).
SCHEMA_VERSION = "5"

CAMBIOS = [
    "19  la fase de DESCUBRIMIENTO ahora archiva su crudo en raw.jsonl.gz"
    " (TAREA B) · guardar_evidencia solo se llamaba sobre respuestas de"
    " medición, y el descubrimiento es la ÚNICA fuente de CantidadBiPrecioMK,"
    " del teaser del descuento y de los metadatos de campaña: la simulación"
    " no los devuelve. Medido en run_20260822_020027: 3174 simulation, 158"
    " orderform_auditoria, 3 simulation_qtyN y CERO respuestas de catálogo —"
    " la cadena CantidadBiPrecioMK aparecía 0 veces en el archivo ·"
    " CONSECUENCIA que esto repara: el CSV no podía reproducir su propio"
    " veredicto. biprecio_status=SIN_DESCUENTO afirma que hubo un umbral"
    " declarado, y la misma fila lo vacía por §6.2; el umbral de los SKUs"
    " 10907796, 1062 y 10907803 ya no existe en ninguna fuente local. El"
    " motor observó un dato, no lo archivó y después lo destruyó ·"
    " QUÉ SE ARCHIVA: el árbol de categorías (category_tree, 1 registro, es"
    " lo que permite reconstruir qué universo se recorrió), cada página de"
    " categoría (category_page, con ruta/nivel/ventana/resources en"
    " `contexto`) y cada lote de --skus (skus_lookup, con los sku_ids"
    " pedidos). Se escriben ANTES de juzgar el status: un HTTP 500 es"
    " justamente lo que uno quiere poder releer sin volver a pedirlo ·"
    " CÓMO SE FILTRA: campo `tipo` en cada registro, \"catalogo\" o"
    " \"medicion\". El nombre del método no alcanzaba para separarlos ·"
    " escribir_evidencia queda como único punto de escritura; las dos"
    " puertas comparten archivo, guardas (--sin-evidencia, --dry-run) y modo"
    " de fallo · NO cambia ninguna columna de Fila: SCHEMA_VERSION sigue en"
    " 5 · NO es recuperable hacia atrás — la corrida del 22 ya perdió ese"
    " crudo; aplica de la próxima en adelante, y por eso va antes de empezar"
    " a acumular la serie · Costo: ~123 requests de descubrimiento contra"
    " ~3174 de medición ·"
    " PENDIENTE: refrescar_stock_cadena también pega al catálogo y tampoco"
    " archiva, pero es la fase stock_cadena, no descubrimiento, y sus campos"
    " sí llegan enteros al CSV",

    "18  el precio mayorista se restaba sobre la BASE EQUIVOCADA. Corrige la"
    " fórmula, agrega un estado y sube SCHEMA_VERSION a 5 ·"
    " (1) LA FÓRMULA: precio_mayorista = list_price − descuento, NO"
    " price − descuento. Estuvo mal seis semanas porque donde no hay"
    " promoción unitaria price == list_price y las dos fórmulas dan lo"
    " mismo: 2021 de las 2328 filas COMPLETO de run_20260822_020027 no"
    " tenían promo, o sea que el bug era invisible en el 87% del dataset y"
    " la auditoría de 3 SKUs de §11 cayó en la parte invisible ·"
    " (2) ESTADO NUEVO BIPRECIO_SUPERADO_POR_PROMO: si"
    " list_price − descuento >= price, el escalón NO se publica ni se cobra"
    " — la promoción unitaria le gana. precio_mayorista,"
    " precio_mayorista_cents, descuento_mayorista_pct y"
    " precio_mayorista_por_unidad_base van vacíos; el umbral se conserva ·"
    " (3) §6.2 REESCRITA: bi_umbral se vacía SOLO si el catálogo no lo"
    " declara. Vaciarlo en todo estado != COMPLETO destruía evidencia real"
    " en SIN_DESCUENTO (236 filas) y en el estado nuevo (233): ahí el umbral"
    " se conoce, lo que falta es el precio ·"
    " (4) descuento_mayorista_pct con BASE EXPLÍCITA (§6.5): ahorro contra"
    " price, no descuento sobre list_price. Antes daba igual; con la base"
    " corregida difiere en 307 filas. Es además la fórmula que la auditoría"
    " ya aplicaba al precio medido, así que deja de haber dos maneras de"
    " calcular la misma columna ·"
    " (5) SCHEMA_VERSION 4 -> 5 sin tocar una columna: precio_mayorista"
    " cambia de SIGNIFICADO y la serie va a tener filas de las dos épocas"
    " conviviendo ·"
    " (6) verdad EXTERNA por primera vez: tests/test_precio_mayorista.py"
    " contra 23 fichas capturadas a mano del storefront. La fórmula vieja"
    " acierta 11/23, ésta 23/23. Todo otro fixture del proyecto sale de la"
    " misma API que se está midiendo ·"
    " (7) el crudo pagó el descubrimiento: raw.jsonl.gz tenía ListPrice en"
    " cada respuesta de simulation, así que reinterpretar la auditoría"
    " costó cero requests",
    "17  --categoria: el alcance de la corrida lo decide el usuario, no el"
    " orden del árbol. No agrega columnas: SCHEMA_VERSION sigue en 4 ·"
    " (1) el problema: el árbol tiene ~3.400 categorías y descubrir_catalogo"
    " las recorre en orden fijo cortando al llegar a --catalogo N. Ese orden"
    " es lo que hace reproducible el descubrimiento, pero significaba que una"
    " corrida acotada medía SIEMPRE las primeras del árbol (Packs Limpieza,"
    " Packs Desayunos, Packs Vinos) y abarrotes podía no entrar nunca:"
    " --catalogo 300 no era '300 SKUs del catálogo' sino 'los primeros 300"
    " que aparezcan', una muestra sesgada por cómo VTEX ordena su árbol ·"
    " (2) --categoria '/399/,/77/' acota el universo por match de prefijo"
    " sobre la ruta completa que aplanar_categorias ya construye. Pedir un"
    " padre incluye a sus hijos. El match es POR SEGMENTO, no startswith de"
    " string: pedir 39 no puede arrastrar la 399, que es otra rama ·"
    " (3) el filtro se aplica ANTES de recorrer, y eso no es una optimización"
    " sino la diferencia entre que el flag sirva o no: descartar después de"
    " paginar cuesta una request por categoría tirada, ~83 minutos para nada ·"
    " (4) acepta la ruta con o sin barras y normaliza. Una ruta que no existe"
    " en el árbol es CategoriaInexistenteError y exit 2, no un aviso: una"
    " corrida que mide cero categorías por un typo no puede terminar en 0 con"
    " un CSV vacío, porque se vería igual que una rama sin stock. Se reportan"
    " todas las rutas malas juntas, no la primera ·"
    " (5) --catalogo y --por-categoria siguen operando DENTRO del universo"
    " acotado; los tres frenos componen. --categoria es incompatible con"
    " --skus, que ya nombra exactamente qué medir ·"
    " (6) cambia lo que significa completo=True: pasa a ser 'completo"
    " respecto de lo pedido'. El manifiesto lo distingue con alcance"
    " (CATALOGO_COMPLETO / CATEGORIAS_SELECCIONADAS) y con la clasificación"
    " nueva COMPLETO_EN_CATEGORIAS — llamarlo COMPLETO a secas haría que una"
    " serie armada sobre una rama se lea después como cobertura total. Con"
    " --por-categoria el contrato viejo sigue mandando: nunca completo ·"
    " (7) el manifiesto registra categoria_filtro, categorias_seleccionadas y"
    " categorias_a_recorrer, sin lo cual no se puede reconstruir el alcance de"
    " una corrida vieja: dos corridas con el mismo número de SKUs pueden haber"
    " mirado ramas distintas ·"
    " (8) antes de recorrer se listan en consola las categorías seleccionadas"
    " con su ruta y su nombre, y después del descubrimiento se estima el costo"
    " de la medición — que es la parte cara — para poder abortar mientras"
    " abortar todavía ahorra algo",
    "16  AUDITORÍA DEL PRECIO MAYORISTA (decisiones_1.1.0 §1/§5/§11). No"
    " agrega columnas: SCHEMA_VERSION sigue en 4 ·"
    " (1) el motor mide a qty=1 SIEMPRE, y a qty=1 el descuento del"
    " bi-precio no se aplica, así que hasta v15 precio_mayorista era una"
    " RESTA de dos números del catálogo hecha con confianza y"
    " precio_mayorista_verificado no podía decir otra cosa que NO en todas"
    " las filas. Fase nueva --auditoria-mayorista N (default 3, 0 la apaga):"
    " remide N SKUs COMPLETO a qty=bi_umbral y contrasta medido contra"
    " reconstruido ·"
    " (2) las filas auditadas pasan a precio_mayorista_verificado=SI con el"
    " precio MEDIDO, y descuento_mayorista_pct y"
    " precio_mayorista_por_unidad_base se recalculan contra lo realmente"
    " cobrado para que precio y porcentaje no se contradigan dentro de la"
    " misma fila ·"
    " (3) si el medido NO coincide con el reconstruido gana el medido —es lo"
    " que el cliente paga— y la fila sale marcada DQ_MAYORISTA_DISCREPA."
    " descuento_monto NO se toca: sigue siendo lo que VTEX declaró, y que lo"
    " declarado y lo cobrado no cierren ES el hallazgo. Grita en consola y"
    " queda en el manifiesto: una discrepancia invalida la fórmula para ese"
    " umbral y pone en duda el resto de la columna, que sigue reconstruida ·"
    " (4) la selección NO usa el hash de seleccionar(): busca cubrir el RANGO"
    " DE UMBRALES, no una muestra representativa. Uno bajo (2-3), el más alto"
    " disponible (12-24), y el resto prefiriendo umbrales no cubiertos. Tres"
    " auditorías del mismo umbral prueban la mitad de lo que esta fase tiene"
    " que decir y dejan intacto el hueco de §11. El desempate es"
    " (bi_umbral, sku_id): determinista, así que dos corridas con las mismas"
    " filas COMPLETO auditan los mismos SKUs y son comparables entre días ·"
    " (5) consultar_simulation acepta `cantidad` (default 1): es el único"
    " lugar del motor que la sube ·"
    " (6) presupuesto agotado durante la auditoría corta la fase y conserva"
    " todo lo medido — las filas se quedan con el mayorista reconstruido,"
    " que es un dato honesto. Una corrida no puede morir por auditar ·"
    " (7) el manifiesto gana auditoria_mayorista{solicitadas, realizadas,"
    " coinciden, discrepan, fallidas, node_id, umbrales_auditados, detalle} y"
    " el RESUMEN avisa explícitamente cuando ningún umbral alto entró en la"
    " muestra: la fórmula sigue sin probarse donde están los descuentos"
    " grandes",
    "15  BLOQUE B de 1.1.0 (decisiones_1.1.0.md §1/§5/§6): las 22 columnas"
    " del precio mayorista. SCHEMA_VERSION sube 3 -> 4 — 56 columnas pasan a"
    " 78, el mismo header que golden_v5.csv ·"
    " (1) BI-PRECIO (9 columnas): precio_mayorista = price − descuento, un"
    " solo escalón. El UMBRAL (CantidadBiPrecioMK) solo existe en el"
    " catálogo y el DESCUENTO viene en catálogo y en simulation, así que"
    " Producto ahora ARRASTRA lo que solo el catálogo sabe (umbral bi/tri,"
    " descuento, category_id, teaser de respaldo) desde el descubrimiento"
    " hasta la medición: unir por sku_id sin releer el catálogo ni guardar"
    " un archivo intermedio, que es lo que v11 sacó del motor ·"
    " (2) CantidadTriPrecioMK se REGISTRA y NUNCA se aplica: está declarado"
    " y se midió que checkout no lo honra ·"
    " (3) el descuento se busca POR NOMBRE de parámetro a cualquier"
    " profundidad (PromotionalPriceTableItemsDiscount), nunca por índice de"
    " array — el orden lo decide VTEX y el día que agregue un parámetro"
    " adelante se leería el ID del producto como monto ·"
    " (4) RÉGIMEN (4 columnas): promo_regime_id sale del TEASER, no de"
    " rateAndBenefitsIdentifiers, que está vacío en qty=1 y el motor mide a"
    " qty=1 siempre. El catálogo trae el teaser sin id y simulation con id:"
    " medición preferida, catálogo respaldo ·"
    " (5) IDENTIDAD (4 columnas): ean_type con dígito verificador GS1 y"
    " prefijo 20-29 -> INTERNO_RESTRINGIDO (la mitad del catálogo no cruza"
    " contra otro retailer y sin esta columna eso son falsos negativos"
    " silenciosos) · category_id de la ruta más larga de categoriesIds,"
    " estable donde el texto no lo es · surtido_makro desde sellerChain ·"
    " (6) PRESENTACIÓN (5 columnas): regla de dos ramas — measurement_unit"
    " != 'un' usa el unit_multiplier de VTEX y es AUTORITATIVO; == 'un'"
    " parsea el nombre y es HEURÍSTICA, etiquetada como tal en"
    " presentacion_origen; si no se puede, DESCONOCIDO y derivadas vacías,"
    " nunca un 1 inventado. Incluye la corrección de multipack tras la"
    " medida (600ml Paquete 6un = 3.6 L) con el corte de relleno sin"
    " dígitos, para que 'Deli 1Lt D2 Sf Pp 25un' no ate el litro con las 25"
    " unidades ·"
    " (7) todo cálculo monetario en Decimal o centavos enteros (§6.1):"
    " ningún float toca un precio, y un descuento que no cae exacto en el"
    " centavo sale INCONSISTENTE en vez de redondeado en silencio ·"
    " (8) vacío != cero (§6.2): sin biprecio_status COMPLETO, bi_umbral y"
    " precio_mayorista* van vacíos — un mayorista igual al unitario es una"
    " mentira que se filtra sola en una hoja de cálculo ·"
    " (9) precio_mayorista_verificado sale siempre NO: el motor mide a qty=1"
    " y RECONSTRUYE el mayorista. El SI solo puede ponerlo algo que haya"
    " medido a qty>=umbral, y eso hoy no existe en el motor (la sonda v5 sí"
    " lo tiene, y por eso el golden trae 3 filas en SI) ·"
    " (10) las reglas viven en funciones PURAS (calcular_mayorista,"
    " clasificar_ean, resolver_presentacion, leer_regimen, leer_descuento) y"
    " enriquecer_fila() es solo pegamento: se prueban sin levantar"
    " Playwright y se mudan solas el día que el archivo se parta en módulos",
    "14  BLOQUE A de 1.1.0 (decisiones_1.1.0.md §4/§7/§8/§8.1/§9): salida,"
    " flags y bugs. NO toca el esquema — SCHEMA_VERSION sigue en 3 y ninguna"
    " columna se agrega ni se renombra; las 22 del precio mayorista son el"
    " bloque B ·"
    " (1) MOTOR pasa de makro a makro_plazavea: el colector se identifica por"
    " FUENTE, no por retailer — makro_pe es el mismo retailer con otra fuente"
    " y no puede compartir carpeta ·"
    " (2) la raíz de salida se deriva del repo buscando pyproject.toml hacia"
    " arriba, en vez de colgar de BASE_DIR: tras el movimiento a src/ eso"
    " escribía datos DENTRO del paquete instalable"
    " (src/retail_engine/collectors/salida/) ·"
    " (3) UNA CARPETA POR CORRIDA, inmutable:"
    " data/makro_plazavea/<run_id>/ con filas.csv + run.json + raw.jsonl.gz,"
    " más runs.jsonl y last_run.json en la raíz del colector."
    " carpeta_corrida() es la única función que construye rutas. La propiedad"
    " de serie de tiempo ya no la da apendear a un archivo mutable sino"
    " acumular carpetas: un glob sobre run_*/filas.csv es la historia entera ·"
    " (4) UN SOLO CSV LARGO en vez de uno por sucursal: el nodo es una"
    " columna, así que una sucursal nueva agrega valores, no archivos ·"
    " (5) --reiniciar ELIMINADO: con carpetas inmutables no hay archivo"
    " acumulado que reiniciar, y lo único que el flag podría borrar es"
    " historia ya cerrada. archivar_si_cambio_el_esquema() se va por la misma"
    " razón estructural (nadie apendea bajo una cabecera ajena); el"
    " razonamiento queda escrito donde vivía la función ·"
    " (6) --skus: lista explícita de hasta 20 sku_id, salta el descubrimiento,"
    " incompatible con --catalogo/--muestra. Resuelve el catálogo con"
    " fq=skuId: batcheado (20 SKUs en 2 requests, no 20), reordenando items"
    " por el SKU pedido y saltando el filtro de stock de cadena — los dos bugs"
    " de §9. Un SKU pedido que el catálogo no devuelve SE ESCRIBE igual como"
    " SKU_NO_ENCONTRADO, una fila por nodo: si desapareciera no habría cómo"
    " distinguir 'no existe' de 'no lo pedí'. El manifiesto marca"
    " modo_seleccion=skus_explicitos para poder filtrar estas corridas al"
    " armar la serie. NO es el panel.json que mató v11: la lista viaja en la"
    " línea de comandos y se resuelve contra el catálogo de esa corrida ·"
    " (7) --dry-run: mide, imprime y no escribe NADA — ni CSV, ni manifiesto,"
    " ni crudo, ni la carpeta ·"
    " (8) fulfillment_type gana operador_externo para la firma identificada"
    " que no es Makro ni PlazaVea (caso real STK917NF / DCK-NF-MK-917 /"
    " DD-NF-CD-917-URBANO, SLA único y despacho confirmado): desconocido queda"
    " reservado para cuando FALTA información, que es lo contrario ·"
    " (9) price_per_unit deja de calcularse por división cuando"
    " unit_multiplier != 1 y se LEE de list_price (Maracuyá: 1.23/0.2 = 6.15 y"
    " el kilo real es 6.19 — el redondeo de VTEX se amplifica al dividir por"
    " un multiplicador menor a 1). La división se conserva como control: si"
    " difiere de list_price en más de un céntimo entra la cuarta regla DQ,"
    " DQ_UNIDAD_INCONSISTENTE. discount_pct NO se toca: ya estaba protegido"
    " con multiplicador == 1 desde 1.0.0",
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


def raiz_repo() -> Path:
    """
    La raíz del repo, por MARCA (`pyproject.toml`), no contando niveles.

    Hasta 1.0.0 la salida colgaba de `BASE_DIR`, que antes del movimiento a
    `src/` era la carpeta del script y quedaba junto al repo. Después del
    movimiento el mismo código resuelve `src/retail_engine/collectors/salida/`
    — o sea, datos generados DENTRO del paquete instalable. Contar `parents[N]`
    lo arreglaría hasta el próximo movimiento de carpetas; buscar el marcador
    aguanta cualquier reorganización.

    Si no aparece el marcador (paquete instalado en site-packages, sin repo
    alrededor), se cae a `BASE_DIR`: peor lugar, pero nunca una excepción a
    mitad de corrida.
    """

    for padre in (BASE_DIR, *BASE_DIR.parents):
        if (padre / "pyproject.toml").exists():
            return padre

    return BASE_DIR


RAIZ_REPO = raiz_repo()

# Slug del COLECTOR, no del retailer. `makro_pe` es el mismo retailer con
# otra fuente, y las dos no pueden compartir carpeta: lo que identifica a un
# dataset es de dónde se sacó, no de quién habla.
MOTOR = "makro_plazavea"

RAIZ_SALIDA = RAIZ_REPO / "data"
SALIDA = RAIZ_SALIDA / MOTOR

# Dónde escribía 1.0.0 (dentro del paquete). Solo se usa para AVISAR si
# quedaron datos ahí: el motor nunca mueve el histórico de nadie.
RAIZ_SALIDA_LEGADO = BASE_DIR / "salida"


# ---------------------------------------------------------------------------
# RUTAS DE SALIDA — una carpeta por corrida (decisiones_1.1.0 §7)
# ---------------------------------------------------------------------------
#
# Hasta 1.0.0 la serie de tiempo era "apendear al CSV de la sucursal". Eso
# ataba tres cosas que no tienen por qué ir juntas: que el dato sea
# acumulativo, que el archivo sea mutable, y que haya un archivo por nodo.
# Ahora la serie la produce la ACUMULACIÓN DE CARPETAS: cada corrida escribe
# una carpeta nueva e inmutable, y leer la historia entera es un glob.
#
# `carpeta_corrida()` es la ÚNICA función que construye rutas de salida.
# Todo lo que escribe cuelga de acá — si aparece un `SALIDA / "algo"` suelto
# en el resto del archivo, es un bug.


def carpeta_corrida(run_id: str = "") -> Path:
    """
    La carpeta de una corrida. Se llama IGUAL que el `run_id`.

    `RUN_ID` ya nace con el prefijo `run_` (`run_20260821_191415`), así que
    la carpeta es `data/makro_plazavea/run_20260821_191415/`. No agregar otro
    `run_` acá: un solo identificador, imposible de desincronizar.
    """

    return SALIDA / (run_id or RUN_ID)


def archivo_filas(run_id: str = "") -> Path:
    """El CSV largo de la corrida: todos los nodos, `node_id` como columna."""

    return carpeta_corrida(run_id) / "filas.csv"


def archivo_manifiesto(run_id: str = "") -> Path:
    """El manifiesto de ESTA corrida, dentro de su propia carpeta."""

    return carpeta_corrida(run_id) / "run.json"


def archivo_evidencia(run_id: str = "") -> Path:
    """El crudo de la corrida. Nombre fijo: el run_id ya está en la carpeta."""

    return carpeta_corrida(run_id) / "raw.jsonl.gz"


def archivo_indice() -> Path:
    """Índice append-only del colector: una línea por corrida."""

    return SALIDA / "runs.jsonl"


def archivo_ultima() -> Path:
    """Copia del manifiesto de la última corrida, para no tener que globear."""

    return SALIDA / "last_run.json"

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
    # Nombre del CSV por sucursal de 1.0.0. Desde §8 hay UN solo CSV largo y
    # el motor ya no lo usa para nada, pero se conserva: es lo que permite
    # rastrear de qué nodo salió un `makro_359_santa_anita.csv` de la
    # historia vieja. Borrarlo no ahorra nada y pierde esa referencia.
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

    # --- LO QUE SOLO SABE EL CATÁLOGO (decisiones_1.1.0 §1) --------------
    #
    # El UMBRAL del bi-precio no existe en checkout: solo viene como
    # `specification` del producto. El DESCUENTO viene en las dos fuentes,
    # pero el catálogo trae el teaser SIN `id`, así que como respaldo sirve
    # para el nombre y no para el identificador.
    #
    # Viajan en `Producto` porque es el único objeto que cruza desde el
    # descubrimiento hasta la medición: sin esto habría que releer el
    # catálogo por SKU al medir, o —peor— guardar un archivo intermedio, que
    # es exactamente lo que v11 sacó del motor.
    #
    # Todo `str`: vacío = el catálogo no lo dijo. La conversión a número vive
    # en las funciones puras, no acá.
    bi_umbral: str = ""
    tri_umbral: str = ""
    category_id: str = ""
    descuento_catalogo: str = ""
    promo_regime_id_catalogo: str = ""
    promo_regime_name_catalogo: str = ""
    payment_method_id_catalogo: str = ""


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

    # =====================================================================
    # 1.1.0 — LAS 22 COLUMNAS NUEVAS (decisiones_1.1.0 §5)
    # =====================================================================
    #
    # El ORDEN importa: `COLUMNAS` se deriva de este dataclass, así que este
    # es el orden del CSV. Es el mismo de `golden_v5.csv`, que es el
    # baseline del criterio de aceptación (§10) — reordenarlas rompería el
    # diff sin cambiar un solo dato.

    # --- BI-PRECIO (9) ---------------------------------------------------
    # Regla dura §6.2: si `biprecio_status != COMPLETO`, `bi_umbral` y
    # `precio_mayorista*` van VACÍOS. Nunca cero, nunca el unitario repetido.
    bi_umbral: str = ""
    # Se registra SIEMPRE que exista y NUNCA se aplica: está declarado en el
    # catálogo y se midió que checkout no lo honra (§1).
    tri_umbral_declarado: str = ""
    # El descuento es un hecho de VTEX: se publica aunque el mayorista no se
    # pueda armar. Es lo que hace diagnosticable un SIN_UMBRAL (§6.4).
    descuento_monto: str = ""
    descuento_monto_cents: str = ""
    precio_mayorista: str = ""
    precio_mayorista_cents: str = ""
    descuento_mayorista_pct: str = ""
    biprecio_status: str = ""
    # OTRO EJE que `biprecio_status`: dice si el mayorista se MIDIÓ a
    # qty≥umbral o se reconstruyó con la fórmula de §1. Un COMPLETO con
    # `NO` es lo normal, no una carencia.
    precio_mayorista_verificado: str = ""

    # --- RÉGIMEN PROMOCIONAL (4) -----------------------------------------
    promo_regime_id: str = ""
    promo_regime_name: str = ""
    # Se registra, no se interpreta: `4` es informativo y no restringe nada.
    payment_method_id: str = ""
    # `3000-01-02` es el centinela de "sin vencimiento".
    price_valid_until: str = ""

    # --- IDENTIDAD Y CALIDAD (4) -----------------------------------------
    ean_type: str = ""
    category_id: str = ""
    sales_channel: str = ""
    # La ÚNICA prueba de surtido Makro de ESTA sucursal (§2).
    surtido_makro: str = ""

    # --- PRESENTACIÓN (5) ------------------------------------------------
    unidad_base: str = ""
    cantidad_base: str = ""
    presentacion_origen: str = ""
    precio_por_unidad_base: str = ""
    precio_mayorista_por_unidad_base: str = ""


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


# ---------------------------------------------------------------------------
# DINERO EN DECIMAL — regla dura §6.1
# ---------------------------------------------------------------------------
#
# `float` solo para lectura humana. Todo cálculo monetario va en centavos
# enteros o en `Decimal`, porque el bi-precio es una RESTA de dos números que
# vienen del servidor y un céntimo perdido en binario es un céntimo que el
# analista no puede explicar.

CERO = Decimal("0")
CENTAVO = Decimal("0.01")
CUATRO = Decimal("0.0001")


def dec(valor: Any) -> Decimal | None:
    """
    A `Decimal` pasando por `str`: NUNCA `Decimal(float)`.

    El JSON llega con los precios ya parseados como float, y `Decimal(13.3)`
    arrastra la basura binaria (13.300000000000000710542735760100185871124267578125).
    `Decimal("13.3")` es lo que el servidor dijo.
    """

    if valor is None or isinstance(valor, bool) or valor == "":
        return None

    try:
        return Decimal(str(valor).strip())
    except (InvalidOperation, ValueError, ArithmeticError):
        return None


def entero(valor: Any) -> int | None:
    """Solo si el número es entero exacto: 2.5 unidades no es un umbral."""

    numero = dec(valor)

    if numero is None:
        return None

    try:
        return int(numero) if numero == numero.to_integral_value() else None
    except (InvalidOperation, ArithmeticError):
        return None


def money(valor: Decimal | None) -> str:
    """Dos decimales, para leer. Vacío si no hay número."""

    return "" if valor is None else f"{valor.quantize(CENTAVO)}"


def money4(valor: Decimal | None) -> str:
    """Cuatro decimales: los precios por unidad base necesitan la precisión."""

    return "" if valor is None else f"{valor.quantize(CUATRO)}"


def a_centavos(valor: Decimal | None) -> int | None:
    """
    Soles -> centavos enteros. `None` si NO cae exacto en el centavo.

    Un descuento de 0.205 soles no existe. Si VTEX lo manda, es un dato que
    no entiendo, y prefiero la celda vacía —con su estado al lado
    explicándola— antes que redondear medio céntimo en silencio y que
    aparezca multiplicado por un umbral de 24.
    """

    if valor is None:
        return None

    escalado = valor * 100

    if escalado != escalado.to_integral_value():
        return None

    return int(escalado)


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

    # `categoriesIds` trae TODAS las rutas del producto; la más larga es la
    # más específica, y es la que identifica sin depender del texto (que VTEX
    # renombra sin avisar). Por eso `category_id` y `category` conviven: una
    # es estable, la otra es legible.
    rutas = [s(r) for r in (crudo.get("categoriesIds") or []) if s(r)]

    regimen = leer_regimen(crudo)

    descuento = leer_descuento(crudo)

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
        bi_umbral=s(especificacion(crudo, SPEC_BI)),
        tri_umbral=s(especificacion(crudo, SPEC_TRI)),
        category_id=max(rutas, key=len) if rutas else "",
        descuento_catalogo="" if descuento is None else str(descuento),
        promo_regime_id_catalogo=regimen["promo_regime_id"],
        promo_regime_name_catalogo=regimen["promo_regime_name"],
        payment_method_id_catalogo=regimen["payment_method_id"],
    )


def especificacion(crudo: dict, nombre: str) -> Any:
    """
    Una `specification` del producto: VTEX las devuelve como lista de un solo
    valor, y a veces como el valor pelado.
    """

    valor = crudo.get(nombre)

    if isinstance(valor, list):
        return valor[0] if valor else None

    return valor


# Diagnóstico del último descubrimiento. Se vuelca en ultima_corrida.json
# para que quede registro de cuántas categorías se tocaron y cuántas
# fallaron: una categoría perdida por un HTTP 500 no puede desaparecer en
# silencio del registro.
ESTADISTICAS_CATALOGO: dict[str, Any] = {}


# ===========================================================================
# FILTRO DE CATEGORÍAS — `--categoria` (1.2.0)
# ===========================================================================
#
# El problema que resuelve
# ------------------------
# El árbol de VTEX tiene ~3.400 categorías y `descubrir_catalogo` las recorre
# en ORDEN FIJO (por ruta), cortando al llegar a `--catalogo N`. Ese orden
# fijo es lo que hace reproducible el descubrimiento, pero tiene un efecto
# que nadie eligió: una corrida acotada mide SIEMPRE las primeras categorías
# del árbol — Packs Limpieza, Packs Desayunos, Packs Vinos — y abarrotes
# puede no entrar nunca.
#
# O sea que hasta 1.1.0 el ALCANCE de la serie lo decidía el orden del árbol,
# no el analista. `--catalogo 300` no significaba "300 SKUs del catálogo",
# significaba "los primeros 300 que aparezcan", que es una muestra sesgada
# por una propiedad accidental de cómo VTEX ordena su árbol.
#
# Por qué el filtro va ANTES de recorrer
# --------------------------------------
# No es una optimización, es la diferencia entre que el flag sirva o no.
# Filtrar después de paginar cuesta una request por categoría descartada:
# 3.300 requests a 1.5s son ~83 minutos para tirar el resultado a la basura.
# Filtrar antes las salta sin tocarlas.


class CategoriaInexistenteError(ValueError):
    """Una ruta pedida en `--categoria` no existe en el árbol vivo.

    Es un error de ARGUMENTOS, no un aviso. Una corrida que mide cero
    categorías porque alguien escribió `/3999/` en vez de `/399/` no puede
    terminar con exit 0 y un CSV vacío: se vería igual que una categoría que
    de verdad se quedó sin stock.
    """


def normalizar_ruta_categoria(texto: str) -> str:
    """
    `/399/`, `399`, ` 399 ` -> `399`. `/399/604/` -> `399/604`.

    `aplanar_categorias` construye las rutas sin barras en los extremos
    (`1/13/152`), pero el usuario copia el ID del sitio, donde aparece con
    barras. Aceptar las dos formas cuesta una línea; obligar a una sola
    convierte un typo en una corrida vacía.
    """

    return "/".join(p for p in str(texto or "").strip().split("/") if p)


def parsear_categorias_pedidas(texto: str) -> list[str]:
    """Rutas separadas por coma, normalizadas, sin duplicados y en orden."""

    vistas: set[str] = set()
    rutas: list[str] = []

    for crudo in str(texto or "").split(","):
        ruta = normalizar_ruta_categoria(crudo)

        if not ruta or ruta in vistas:
            continue

        vistas.add(ruta)
        rutas.append(ruta)

    return rutas


def ruta_bajo(ruta: str, pedida: str) -> bool:
    """
    ¿`ruta` es la categoría pedida o desciende de ella?

    Match de PREFIJO POR SEGMENTO, no de string: `startswith("39")` haría que
    pedir la categoría 39 arrastrase la 399, que es otra rama entera. Pedir un
    padre incluye a sus hijos, y eso es lo que se quiere: `--categoria /399/`
    trae la rama completa sin tener que enumerar cada subcategoría.
    """

    return ruta == pedida or ruta.startswith(pedida + "/")


def filtrar_categorias(
    categorias: list[dict],
    pedidas: list[str],
) -> tuple[list[dict], list[str]]:
    """
    Devuelve `(seleccionadas, no_encontradas)`.

    `seleccionadas` sale en el orden en que venía `categorias`, para no tocar
    el determinismo del recorrido. `no_encontradas` son las rutas pedidas que
    no matchean NADA del árbol — cada una es un error de argumentos, y se
    devuelven todas juntas en vez de morir en la primera para que alguien que
    pidió cuatro rutas y erró dos no tenga que descubrirlo de a una.
    """

    if not pedidas:
        return list(categorias), []

    seleccionadas = [
        c for c in categorias
        if any(ruta_bajo(c["ruta"], pedida) for pedida in pedidas)
    ]

    encontradas = {
        pedida for pedida in pedidas
        if any(ruta_bajo(c["ruta"], pedida) for c in categorias)
    }

    return seleccionadas, [p for p in pedidas if p not in encontradas]


async def descubrir_catalogo(
    cliente: Cliente,
    limite: int = 0,
    por_categoria: int = 0,
    presupuesto_fase: int = 0,
    categorias_pedidas: list[str] | None = None,
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
    categorias_pedidas
                      rutas de categoría (`399`, `399/604`) que acotan el
                      UNIVERSO a recorrer. Vacío = el árbol entero, como
                      hasta 1.1.0. `limite` y `por_categoria` siguen
                      operando DENTRO de ese universo: son tres frenos
                      independientes y componen.

                      Cambia lo que significa `completo=True`: pasa a ser
                      "completo respecto de las categorías pedidas", no
                      "el catálogo entero". El manifiesto lo distingue con
                      `alcance` y con la clasificación
                      COMPLETO_EN_CATEGORIAS — llamarlo COMPLETO a secas
                      afirmaría tener un catálogo que nadie pidió medir.
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

    # El árbol define QUÉ universo se recorrió: sin él archivado no se puede
    # reconstruir después la decisión de `filtrar_categorias` para esta
    # corrida. Es un solo registro.
    guardar_evidencia_catalogo("category_tree", status, arbol)

    categorias = aplanar_categorias(arbol) if status < 400 else []

    pedidas = list(categorias_pedidas or [])

    # EL FILTRO VA ACÁ: antes de decidir niveles y antes de recorrer nada.
    # Lo que se descarta acá no cuesta una sola request.
    universo, no_encontradas = filtrar_categorias(categorias, pedidas)

    if no_encontradas:
        raise CategoriaInexistenteError(
            "Estas rutas de --categoria no existen en el árbol vivo: "
            + ", ".join(f"/{r}/" for r in no_encontradas)
            + f". El árbol tiene {len(categorias)} categorías; revisá el ID "
            "en el sitio (la ruta va desde la raíz, p. ej. /399/604/)."
        )

    # Nivel 1 es demasiado amplio, nivel 4+ suele tener 2 o 3 productos.
    # El `or universo` es el mismo escape que ya existía: si la rama pedida
    # vive entera fuera de esa franja —una categoría profunda, por ejemplo—
    # se recorre igual, porque el usuario ya decidió el alcance a mano y la
    # heurística de niveles existe para cuando NO lo decidió nadie.
    utiles = [c for c in universo if 2 <= c["nivel"] <= 3] or universo

    # Orden fijo: el descubrimiento no puede depender del azar si el
    # motor tiene que ser reproducible sin guardar el resultado.
    utiles.sort(key=lambda c: c["ruta"])

    if pedidas:
        log("")
        log(f"  ALCANCE ACOTADO a {len(pedidas)} ruta(s): "
            + ", ".join(f"/{r}/" for r in pedidas))
        log(f"  {len(universo)} categorías del árbol caen bajo esas rutas; "
            f"se recorren {len(utiles)} (nivel 2-3). "
            f"Se saltan {len(categorias) - len(universo)} sin gastar requests.")
        log("")

        for c in utiles:
            log(f"    /{c['ruta']}/  {c['name'][:52]}")

        log("")

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
        # Alcance de la corrida (1.2.0). Sin esto no se puede reconstruir
        # después qué universo miró una corrida vieja: dos corridas con el
        # mismo número de SKUs pueden haber mirado ramas distintas del árbol.
        "categoria_filtro": list(pedidas),
        "alcance": "CATEGORIAS_SELECCIONADAS" if pedidas else "CATALOGO_COMPLETO",
        "categorias_en_arbol": len(categorias),
        "categorias_seleccionadas": len(universo),
        "categorias_a_recorrer": len(utiles),
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

            # Se archiva ANTES de juzgar la respuesta: un HTTP 500 o un
            # cuerpo inesperado es exactamente lo que uno quiere poder releer
            # sin volver a pedirlo. La página trae `CantidadBiPrecioMK` y el
            # teaser del descuento, que no vuelven a aparecer en ninguna otra
            # fase.
            guardar_evidencia_catalogo(
                "category_page",
                status,
                datos,
                {
                    "categoria_ruta": categoria["ruta"],
                    "categoria_nombre": categoria["name"],
                    "categoria_nivel": categoria["nivel"],
                    "desde": desde,
                    "hasta": desde + VENTANA - 1,
                    "resources": s(cabeceras.get("resources", "")),
                },
            )

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
    #   COMPLETO_EN_CATEGORIAS     se recorrió sin cortes TODO lo que
    #                              --categoria pedía, que no es el catálogo.
    #                              Vale como "no falta nada de lo pedido" y
    #                              NO vale como "este es el catálogo Makro":
    #                              confundirlos haría que una serie armada
    #                              sobre una rama se lea como cobertura total.
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
        stats["clasificacion"] = (
            "COMPLETO_EN_CATEGORIAS" if pedidas else "COMPLETO"
        )
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

        if pedidas:
            raise RuntimeError(
                "Las categorías pedidas existen en el árbol pero no "
                "devolvieron ningún producto bajo sc="
                f"{SALES_CHANNEL}: {', '.join('/' + r + '/' for r in pedidas)}. "
                "El árbol de VTEX tiene ~78% de categorías vacías, así que "
                "una rama entera sin stock es posible — pero revisá el ID "
                "antes de asumirlo."
            )

        raise RuntimeError(
            "No se obtuvo ningún producto del catálogo. "
            "Revisa conexión o si el sitio está bloqueando."
        )

    return list(encontrados.values())


# ===========================================================================
# SELECCIÓN EXPLÍCITA — `--skus` (decisiones_1.1.0 §8.1)
# ===========================================================================
#
# El descubrimiento elige contra el catálogo VIVO: dos corridas separadas por
# días no eligen lo mismo, así que una corrida vieja no se puede volver a
# medir. `--skus` invierte eso: los SKUs entran por parámetro y el catálogo
# solo se consulta para lo que únicamente él sabe.
#
# NO es el `panel.json` que v11 mató. Aquello era un archivo en disco que se
# LEÍA para decidir qué medir y envejecía en silencio. Acá la lista viaja en
# la línea de comandos, queda escrita en el manifiesto de la corrida, y se
# resuelve contra el catálogo de ESA corrida. Sigue sin haber archivos de
# entrada.

# Techo deliberado: `--skus` es para responder una pregunta puntual o
# remedir un baseline, no para extraer a lo grande. Lo grande se descubre.
MAX_SKUS_EXPLICITOS = 20

# `fq=skuId:` acepta varios filtros en la misma consulta, igual que el
# `fq=productId:` de refrescar_stock_cadena: 20 SKUs salen en 2 requests, no
# en 20.
LOTE_SKUS = 10


def parsear_lista_skus(texto: str) -> list[str]:
    """Comas, espacios o saltos de línea. Conserva el orden y deduplica."""

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
    Pone el SKU pedido en `items[0]`, que es donde `parsear_producto` mira.

    BUG de §9, encontrado al implementar esto en la sonda v5. `fq=skuId:`
    FILTRA por SKU pero devuelve el PRODUCTO entero, con todas sus variantes.
    `parsear_producto` lee `items[0]`: sin reordenar, pedir la variante de 1L
    de un producto que también viene en 4L devuelve la fila de la de 4L, con
    otro `sku_id` y otro precio. No falla, no avisa: mide otra cosa.

    Muta `crudo` a propósito y devuelve si encontró el SKU. Llamarla una vez
    por SKU y JUSTO ANTES de leerlo — reordenar para todos por adelantado deja
    en cabeza al último.
    """

    items = crudo.get("items") or []

    for posicion, item in enumerate(items):
        if s(item.get("itemId")) == sku_id:
            if posicion:
                items.insert(0, items.pop(posicion))

            return True

    return False


def producto_forzado(crudo: dict) -> Producto | None:
    """
    `parsear_producto` saltando el filtro de stock de cadena.

    BUG de §9, el segundo. `parsear_producto` descarta lo que la cadena no
    tiene disponible (`AvailableQuantity` vacío). Al DESCUBRIR está bien: no
    tiene sentido muestrear lo que nadie puede comprar. Al medir una LISTA
    EXPLÍCITA está al revés — un SKU que se quedó sin stock es justo el caso
    que se quería ver, y descartarlo lo hace desaparecer de la salida sin
    distinguirse de "no lo pedí".

    La disponibilidad se fuerza sobre una COPIA del crudo y después se borra
    `stock_catalog`, para no dejar grabado un número que el servidor no dijo.
    El estado real sigue saliendo de la medición (`availability`) y del stock
    de cadena refrescado (`chain_stock`).
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

    producto = parsear_producto(copia)

    if producto is not None:
        producto.stock_catalog = ""

    return producto


async def descubrir_por_skus(
    cliente: Cliente,
    sku_ids: list[str],
) -> tuple[list[Producto], list[str], list[str]]:
    """
    Resuelve una lista fija de `sku_id` contra el catálogo, en pocas requests.

    Devuelve `(productos, ausentes, sin_resolver)`, y la diferencia entre los
    dos últimos es el punto de toda la función:

        ausentes      se preguntó y el catálogo NO lo devolvió. Es un hecho
                      sobre el SKU: no existe, o salió del surtido. Se le
                      escribe fila igual, SKU_NO_ENCONTRADO (§8.1).
        sin_resolver  NUNCA se llegó a preguntar, porque se agotó el
                      presupuesto de requests. No es un hecho sobre el SKU,
                      es un hecho sobre la CORRIDA. NO se le escribe fila:
                      inventarle un SKU_NO_ENCONTRADO diría que el catálogo
                      lo negó cuando nadie se lo preguntó.

    Es la misma distinción que v13 introdujo en la medición (filas medidas vs
    `manifiesto.medicion.pendientes`), por la misma razón: un presupuesto
    agotado no puede disfrazarse de dato.
    """

    log(f"Resolviendo {len(sku_ids)} SKUs explícitos contra el catálogo...")

    por_sku: dict[str, Producto] = {}
    forzados: list[str] = []
    sin_resolver: list[str] = []

    lotes = [
        sku_ids[i:i + LOTE_SKUS] for i in range(0, len(sku_ids), LOTE_SKUS)
    ]

    for numero, lote in enumerate(lotes, 1):
        filtros = "&".join(f"fq=skuId:{sku}" for sku in lote)

        url = (
            f"{BASE_URL}/api/catalog_system/pub/products/search"
            f"?{filtros}&_from=0&_to={len(lote) * 2 - 1}&sc={SALES_CHANNEL}"
        )

        try:
            status, datos, _ = await cliente.pedir(url)
        except TopeAgotadoError as exc:
            # Condición de CORRIDA, no de SKU. Se detiene acá: lo ya
            # resuelto se conserva y lo que faltaba queda como
            # `sin_resolver`, que es distinto de "no existe".
            sin_resolver = [
                s for lote_pendiente in lotes[numero - 1:]
                for s in lote_pendiente
                if s not in por_sku
            ]

            log(
                f"  lote {numero}: presupuesto agotado ({exc}). "
                f"{len(sin_resolver)} SKUs quedan sin preguntar."
            )
            AVISOS.append(
                f"Presupuesto agotado resolviendo --skus: {len(sin_resolver)} "
                "SKUs nunca se preguntaron (no son 'no encontrados'). "
                "Ver manifiesto.seleccion.skus_sin_resolver."
            )
            ESTADISTICAS_CATALOGO.update(
                {
                    "clasificacion": "INCOMPLETO_NO_PLANEADO",
                    "completo": False,
                    "motivo": "REQUEST_BUDGET_EXHAUSTED",
                }
            )
            break
        except Exception as exc:
            log(f"  lote {numero}: {type(exc).__name__} al pedir el catálogo.")
            AVISOS.append(
                f"Lote {numero} de --skus falló ({type(exc).__name__}): "
                "esos SKUs quedan como no encontrados."
            )
            continue

        # Mismo motivo que en `descubrir_catalogo`: este lote es la única
        # vez que el umbral del bi-precio pasa por el proceso. Se archiva
        # antes de juzgar el status.
        guardar_evidencia_catalogo(
            "skus_lookup",
            status,
            datos,
            {"lote": numero, "de": len(lotes), "sku_ids": list(lote)},
        )

        if status >= 400 or not isinstance(datos, list):
            log(f"  lote {numero}: HTTP {status}.")
            AVISOS.append(
                f"Lote {numero} de --skus respondió HTTP {status}: "
                "esos SKUs quedan como no encontrados."
            )
            continue

        for crudo in datos:
            if not isinstance(crudo, dict):
                continue

            for sku in lote:
                if sku in por_sku or not ordenar_items(crudo, sku):
                    continue

                producto = producto_forzado(crudo)

                if producto is None or producto.sku_id != sku:
                    continue

                if not parsear_producto(crudo):
                    forzados.append(sku)

                por_sku[sku] = producto

        log(f"  lote {numero}/{len(lotes)}: {len(por_sku)}/{len(sku_ids)} "
            f"resueltos  ({cliente.contador} requests)")

    productos = [por_sku[sku] for sku in sku_ids if sku in por_sku]

    nunca_preguntados = set(sin_resolver)
    ausentes = [
        sku for sku in sku_ids
        if sku not in por_sku and sku not in nunca_preguntados
    ]

    if forzados:
        AVISOS.append(
            f"{len(forzados)} SKUs sin stock de cadena se midieron igual "
            f"(parsear_producto los habría descartado): {', '.join(forzados)}."
        )

    if ausentes:
        AVISOS.append(
            f"{len(ausentes)} SKUs pedidos no existen en el catálogo y se "
            f"escriben como SKU_NO_ENCONTRADO: {', '.join(ausentes)}."
        )

    return productos, ausentes, sin_resolver


def fila_sku_ausente(sku_id: str, nodo: Nodo, momento: datetime) -> Fila:
    """
    La fila de un SKU que se pidió y el catálogo no devolvió.

    §8.1: **se escribe igual**. Si desapareciera de la salida no habría forma
    de distinguir *no existe* de *no lo pedí* — la misma razón por la que
    ninguna fila se descarta en silencio en el resto del motor.

    No inventa nada: sin catálogo no hay nombre, ni marca, ni precio. Lo
    único que afirma es que se preguntó y no vino.
    """

    return Fila(
        timestamp=momento.isoformat(timespec="seconds"),
        fecha=momento.strftime("%Y-%m-%d"),
        branch=nodo.branch,
        node_id=nodo.node_id,
        sku_id=sku_id,
        postal_sent=nodo.postal_code,
        run_id=RUN_ID,
        logistics_status="SKU_NO_ENCONTRADO",
        node_resolved="NONE",
        price_status="NO_PRICE",
        error_class="NOT_FOUND",
        error="El SKU se pidió con --skus y el catálogo no lo devolvió.",
    )


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

# --dry-run: se mide y se imprime, no se escribe NADA (§8.1). Vive acá
# arriba, junto a GUARDAR_EVIDENCIA, porque lo consultan funciones de
# escritura repartidas por el archivo y pasarlo por parámetro obligaría
# a tocar firmas que este bloque no tiene por qué tocar.
DRY_RUN = False

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

# Resultado de la auditoría del PRECIO MAYORISTA (1.1.0).
#
# Es otra pregunta que la auditoría simulation-vs-orderForm, aunque el patrón
# sea el mismo (muestrear, contrastar, registrar). Aquella pregunta "¿la vía
# barata dice lo mismo que la cara?". Esta pregunta "¿la FÓRMULA de §1
# reconstruye el precio que el cliente pagaría de verdad?" — y solo se puede
# responder pidiendo `qty = bi_umbral`, porque a qty=1 el descuento no se
# aplica.
#
# Sin esta fase, `precio_mayorista_cents` es una resta de dos números del
# catálogo hecha con confianza, y `precio_mayorista_verificado` no puede
# decir otra cosa que `NO` en todas las filas.
AUDITORIA_MAYORISTA: dict[str, Any] = {
    "solicitadas": 0,
    "realizadas": 0,
    "coinciden": 0,
    "discrepan": 0,
    "fallidas": 0,
    "node_id": "",
    "umbrales_auditados": [],
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

    escribir_evidencia({
        "tipo": "medicion",
        "sku_id": producto.sku_id,
        "product_id": producto.product_id,
        "node_id": nodo.node_id,
        "method": metodo,
        "http_status": status,
        "response": datos,
    })


def guardar_evidencia_catalogo(
    metodo: str,
    status: int,
    datos: Any,
    contexto: dict[str, Any] | None = None,
) -> None:
    """
    Archiva una respuesta cruda de la fase de DESCUBRIMIENTO.

    Por qué existe (TAREA B del brief de tareas bloqueantes)
    -------------------------------------------------------
    `guardar_evidencia` solo se llamaba sobre respuestas de MEDICIÓN. Pero
    el descubrimiento es la ÚNICA fuente de `CantidadBiPrecioMK` (el umbral
    del bi-precio), del teaser del descuento y de los metadatos de campaña:
    la simulación no los devuelve. Sin este archivo, esos campos solo
    existían en `filas.csv`, donde la regla §6.2 los vacía en todo estado
    distinto de COMPLETO — o sea, el motor observaba un umbral, no lo
    archivaba, y después lo borraba. Verificado: `CantidadBiPrecioMK`
    aparecía 0 veces en el `raw.jsonl.gz` de run_20260822_020027, y el
    umbral de los SKUs 10907796, 1062 y 10907803 no existe ya en ninguna
    fuente local.

    Consecuencia estructural que esto repara: el CSV no podía reproducir su
    propio veredicto (`biprecio_status = SIN_DESCUENTO` afirma que hubo un
    umbral declarado, y la misma fila lo vació). Con el crudo del catálogo
    archivado, la salida del motor vuelve a ser reconstruible desde la
    evidencia — igual que ya lo era todo lo que toca la medición.

    NO es recuperable hacia atrás: aplica de esta corrida en adelante.

    Volumen: ~123 requests de descubrimiento contra ~3174 de medición en una
    corrida normal. El costo en disco es marginal.

    El registro lleva `tipo = "catalogo"` para poder separarlo de las
    respuestas de medición sin adivinar por el nombre del método.

    `contexto` guarda QUÉ se preguntó (la categoría y la ventana de
    paginado, o el lote de SKUs). Sin eso, dos páginas de la misma
    categoría son indistinguibles al releer el archivo. No lleva `sku_id`
    ni `node_id` porque una respuesta de catálogo no es de un SKU ni de un
    nodo: rellenar esos campos afirmaría algo que no se midió.
    """

    escribir_evidencia({
        "tipo": "catalogo",
        "method": metodo,
        "http_status": status,
        "contexto": contexto or {},
        "response": datos,
    })


def escribir_evidencia(registro: dict[str, Any]) -> None:
    """
    Escribe una línea en el JSONL comprimido de la corrida.

    Único punto de escritura de la evidencia: medición y catálogo comparten
    archivo, guardas y modo de fallo. Se separan por el campo `tipo`.
    """

    if not RUN_ID or not GUARDAR_EVIDENCIA or DRY_RUN:
        return

    linea = {
        "run_id": RUN_ID,
        "captured_at": ahora().isoformat(timespec="seconds"),
        **registro,
    }

    # §7: nombre fijo dentro de la carpeta de la corrida. El `raw/<fecha>/`
    # de 1.0.0 repetía el run_id en el nombre del archivo y separaba el crudo
    # de las filas que explicaba; ahora la evidencia viaja junto al CSV que
    # produjo, que es lo que hace barato replayear un fix de parser.
    archivo = archivo_evidencia()
    archivo.parent.mkdir(parents=True, exist_ok=True)

    try:
        with gzip.open(archivo, "at", encoding="utf-8") as salida:
            salida.write(json.dumps(linea, ensure_ascii=False) + "\n")
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


# ===========================================================================
# BI-PRECIO, EAN Y PRESENTACIÓN — reglas puras (decisiones_1.1.0 §1, §5)
# ===========================================================================
#
# Todo lo de esta sección es función pura: entra JSON o strings, sale un
# valor. Ninguna toca la red, ninguna toca `Fila`. Están juntas y sin
# dependencias del resto del motor para que el día que el archivo se parta en
# módulos se muevan solas, y para que se puedan probar sin levantar Playwright.
#
# El pegamento —qué función alimenta qué columna— vive en `enriquecer_fila()`,
# abajo, y es deliberadamente delgado.

# El nombre del parámetro que lleva el descuento del bi-precio, dentro del
# teaser. Se busca POR NOMBRE, nunca por índice: ver `parametros_por_nombre`.
PARAM_DESCUENTO = "PromotionalPriceTableItemsDiscount"
PARAM_PAGO = "PaymentMethodId"

# Specifications del catálogo. El umbral SOLO existe acá: checkout no lo
# devuelve nunca, y por eso catálogo y medición hay que unirlos por sku_id.
SPEC_BI = "CantidadBiPrecioMK"
SPEC_TRI = "CantidadTriPrecioMK"


def clave_normal(clave: Any) -> str:
    """
    `<Name>k__BackingField` -> `name`. `Name` -> `name`. `name` -> `name`.

    VTEX serializa el MISMO dato de tres formas distintas según el endpoint
    (catálogo viejo, catálogo nuevo, checkout). Normalizar la clave es lo que
    permite escribir cada regla una sola vez en vez de tres.
    """

    return str(clave).replace("k__BackingField", "").strip("<> ").strip().lower()


def parametros_por_nombre(objeto: Any, nombre: str) -> list[Any]:
    """
    Busca `{"Name": <nombre>, "Value": X}` a CUALQUIER profundidad.

    Nunca por índice. Anclar en `Parameters[1]` funciona hasta el día que
    VTEX agrega un parámetro adelante, y ese día se lee el ID del producto
    como si fuera un monto de descuento — sin error, sin aviso, con el
    precio mayorista saliendo mal en todas las filas.
    """

    encontrados: list[Any] = []

    def recorrer(nodo: Any) -> None:
        if isinstance(nodo, dict):
            claves = {clave_normal(k): k for k in nodo.keys()}

            if "name" in claves and "value" in claves:
                if s(nodo[claves["name"]]) == nombre:
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
            return s(nodo[clave])

    return ""


def teasers_con_descuento(objeto: Any) -> list[dict]:
    """
    Los teasers que llevan un `PromotionalPriceTableItemsDiscount`.

    Se recorren TODAS las listas de teasers de la respuesta (`teaser` en
    checkout, `PromotionTeasers` / `Teasers` en catálogo) y se filtra por
    CONTENIDO, no por dónde estaban: el régimen se identifica porque carga el
    descuento, no porque aparezca en la posición que uno esperaba.
    """

    candidatos: list[dict] = []

    for clave in ("teaser", "PromotionTeasers", "Teasers", "teasers"):
        for bloque in buscar_todo(objeto, clave):
            if isinstance(bloque, list):
                candidatos.extend(t for t in bloque if isinstance(t, dict))
            elif isinstance(bloque, dict):
                candidatos.append(bloque)

    return [t for t in candidatos if parametros_por_nombre(t, PARAM_DESCUENTO)]


def leer_descuento(objeto: Any) -> Decimal | None:
    """El monto de descuento por unidad, en soles. `None` si no está."""

    for valor in parametros_por_nombre(objeto, PARAM_DESCUENTO):
        numero = dec(valor)

        if numero is not None:
            return numero

    return None


def leer_regimen(objeto: Any) -> dict[str, str]:
    """
    Régimen promocional que gobierna el bi-precio: id, nombre, medio de pago.

    NO se lee de `rateAndBenefitsIdentifiers`: ese array está VACÍO en qty=1 y
    el motor mide a qty=1 siempre, así que como fuente principal es
    inutilizable (§1). Se lee del teaser.

    El catálogo trae el teaser SIN `id` (solo nombre) y `simulation` lo trae
    CON `id`. Por eso la medición es la fuente preferida y el catálogo el
    respaldo: entre las dos, la columna se llena.
    """

    salida = {"promo_regime_id": "", "promo_regime_name": "", "payment_method_id": ""}

    for teaser in teasers_con_descuento(objeto):
        salida["promo_regime_id"] = salida["promo_regime_id"] or campo(teaser, "id")
        salida["promo_regime_name"] = salida["promo_regime_name"] or campo(teaser, "name")

        pagos = parametros_por_nombre(teaser, PARAM_PAGO)

        if pagos and not salida["payment_method_id"]:
            salida["payment_method_id"] = s(pagos[0])

    # Respaldo del id: cuando la cantidad alcanza el umbral, la promoción
    # aplicada sí aparece en `rateAndBenefitsIdentifiers`. El motor mide a
    # qty=1 y no llega ahí, pero una respuesta auditada sí puede traerlo.
    if not salida["promo_regime_id"]:
        for bloque in buscar_todo(objeto, "rateAndBenefitsIdentifiers"):
            for regimen in bloque if isinstance(bloque, list) else []:
                nombre = campo(regimen, "name")

                if "PRECIO" in nombre.upper().replace(" ", ""):
                    salida["promo_regime_id"] = campo(regimen, "id")
                    salida["promo_regime_name"] = salida["promo_regime_name"] or nombre
                    break

    return salida


def leer_price_valid_until(datos: Any, sku_id: str) -> str:
    """
    `priceValidUntil` del ítem PEDIDO, no del primero que aparezca.

    `3000-01-02` es el centinela de VTEX para "sin vencimiento". Una fecha
    real sería vencimiento de campaña en la fuente, y ahí sí querríamos
    enterarnos antes de que el precio cambie solo.
    """

    items = datos.get("items") if isinstance(datos, dict) else None

    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and s(item.get("id")) == sku_id:
            return s(item.get("priceValidUntil"))

    return ""


def calcular_mayorista(
    price_cents: int | None,
    list_price_cents: int | None,
    umbral: int | None,
    descuento: Decimal | None,
) -> dict[str, Any]:
    """
    El ÚNICO lugar donde se decide si hay precio mayorista y por qué.

        precio_mayorista_cents = list_price_cents − descuento_monto_cents

    LA BASE ES `ListPrice`, NO `Price` (corregido en v18). Hasta v17 esta
    función restaba sobre `Price` y estuvo mal seis semanas sin que nada lo
    delatara: donde NO hay promoción unitaria `price == list_price` y las dos
    fórmulas colapsan en el mismo número. De las 2328 filas COMPLETO de
    `run_20260822_020027`, 2021 no tenían promoción unitaria — el bug era
    invisible en el 87% del dataset, y la auditoría de 3 SKUs de §11 cayó casi
    entera en la parte invisible: 2 sin promo pasaron, 1 con promo falló y se
    le echó la culpa al redondeo. La fórmula nunca estuvo probada donde podía
    fallar.

    Lo que la prueba hoy es verdad EXTERNA, no otra respuesta de la misma API:
    23 fichas capturadas a mano del storefront
    (`tests/fixtures/makro_plazavea/fichas_publicadas_20260822.csv`). La
    fórmula vieja acierta 11/23; ésta, 23/23.

    De ahí sale la segunda regla, que antes no existía:

        si list_price − descuento >= price:
            el escalón NO se publica ni se cobra
            → la promoción unitaria le gana al escalón mayorista
            → BIPRECIO_SUPERADO_POR_PROMO, sin precio mayorista

    Ese estado no es un hueco en el dato: el umbral se conoce y es real, lo
    que no existe es el precio. Por eso `bi_umbral` sobrevive (§6.2) y solo
    se vacían las columnas de precio.

    Un solo escalón (§1). `CantidadTriPrecioMK` se registra y NO se aplica:
    está declarado en el catálogo y se midió que checkout no lo honra.

    Devuelve el estado SIEMPRE, y los números SOLO cuando el estado es
    COMPLETO. Esa asimetría es deliberada: el estado explica la celda vacía
    de al lado, así que nunca puede faltar (§6.2 — vacío ≠ cero; un mayorista
    igual al unitario es una mentira que se filtra sola en una hoja de
    cálculo).
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
        # marca. Redondear acá sería inventar medio céntimo por unidad.
        estado = "INCONSISTENTE"

    elif list_price_cents is None:
        # Hay umbral y descuento declarados, pero falta la base sobre la que
        # se resta. NO se cae de vuelta a `price_cents`: ese atajo ES el bug
        # que v18 corrige, y lo peor que tenía era que no se notaba.
        estado = "INCONSISTENTE"

    else:
        mayorista_cents = list_price_cents - descuento_cents

        if mayorista_cents <= 0 or umbral < 2:
            # Un "bi-precio" que arranca en 1 unidad no es un bi-precio.
            estado = "INCONSISTENTE"

        elif mayorista_cents >= price_cents:
            # El escalón existe en el catálogo pero no le gana a la promoción
            # unitaria: comprando de a uno ya se paga igual o menos. Makro no
            # lo imprime en la ficha y checkout no lo cobra, así que
            # publicarlo acá sería ofrecer un precio que nadie puede pagar.
            #
            # Con `>=` y no `>`: empatar tampoco es un escalón. Un mayorista
            # idéntico al unitario es la mentira que el docstring de arriba
            # dice que se filtra sola en una hoja de cálculo.
            estado = "BIPRECIO_SUPERADO_POR_PROMO"

        else:
            estado = "COMPLETO"

    if estado != "COMPLETO":
        return {
            "estado": estado,
            "descuento": descuento,
            "descuento_cents": descuento_cents,
            "mayorista_cents": None,
        }

    return {
        "estado": estado,
        "descuento": descuento,
        "descuento_cents": descuento_cents,
        "mayorista_cents": list_price_cents - descuento_cents,
    }


def clasificar_ean(ean: str) -> str:
    """
    GS1_GLOBAL | INTERNO_RESTRINGIDO | FALTANTE | INVALIDO

    Por qué importa en un motor de precios: un EAN interno (prefijo 20-29) es
    un código que la propia tienda se inventó. Sirve dentro de Makro y NO
    sirve para cruzar el producto contra otro retailer. Tratar los dos como
    "el EAN" arruina cualquier match de catálogo cruzado — y medido, la mitad
    del catálogo no cruza. Sin esta columna ese 50% produce falsos negativos
    silenciosos: el producto "no existe en el competidor" cuando lo que pasa
    es que se lo está buscando por un código que solo existe acá.

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

    # Prefijo 20-29: rango que GS1 reserva para numeración interna del
    # comercio. No es global aunque el dígito verificador cierre.
    prefijo = limpio[:2] if len(limpio) >= 13 else limpio[:2].zfill(2)

    if len(limpio) >= 12 and prefijo.isdigit() and 20 <= int(prefijo) <= 29:
        return "INTERNO_RESTRINGIDO"

    return "GS1_GLOBAL"


# --- PRESENTACIÓN: regla de dos ramas (§5) ---------------------------------

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
ENVASES = (
    r"(?:un|und|unid|unidades|u|pack|packs|bolsas?|botellas?|latas?|sobres?"
    r"|paquetes?|cajas?|frascos?|barras?|piezas?)"
)

MEDIDA = (
    r"(\d+(?:[.,]\d+)?)\s*("
    + "|".join(sorted(UNIDADES, key=len, reverse=True))
    + r")\b"
)

# "26g x 12un"  /  "473ml x 6"
RE_MULTI_A = re.compile(MEDIDA + r"\s*(?:x|por)\s*(\d+)\s*" + ENVASES + r"?", re.I)
# "12 Bolsas 17g"  /  "6 botellas de 473ml"
RE_MULTI_B = re.compile(r"(?:x\s*)?(\d+)\s*" + ENVASES + r"\s*(?:de\s*)?" + MEDIDA, re.I)
# "600ml Paquete 6un" / "4L Paquete 4un" — el conteo va DESPUÉS de la medida y
# sin `x` de por medio. El relleno entre ambos NO puede contener dígitos: en
# "Deli 1Lt D2 Sf Pp 25un" ese "D2" es un código de producto, y sin el corte
# la regla ataría el litro con las 25 unidades cruzando basura.
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

    Es heurística y se etiqueta como tal (`presentacion_origen = NOMBRE`): un
    analista tiene que poder filtrar por `VTEX` y quedarse solo con lo que el
    servidor afirmó.

    El multipack se MULTIPLICA a propósito: el precio de la fila es el del
    empaque completo, así que "26g x 12un" son 312 g, no 26. Sin multiplicar,
    el precio por kilo de todo pack saldría 12 veces más caro de lo que es.
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

    # Sin peso ni volumen: si el nombre declara un conteo de piezas, la unidad
    # base es la pieza. Es menos informativo que un kilo, pero es verdad, y
    # hace comparable un pack de 12 contra uno de 6.
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
    Regla de dos ramas. Devuelve `(unidad_base, cantidad_base, origen, regla)`.

    Rama VTEX   — `measurement_unit != 'un'`: el producto se vende por peso o
                  volumen variable (carnes, frutas, verduras) y VTEX declara
                  el tamaño de la pieza en `unit_multiplier`. AUTORITATIVO:
                  nadie adivina mejor que el servidor cuánto pesa el trozo.
    Rama NOMBRE — `measurement_unit == 'un'`: el producto es envasado y su
                  tamaño solo vive en el texto del nombre. HEURÍSTICA.
    Ninguna     — `DESCONOCIDO` y las derivadas VACÍAS. Un `1` inventado en
                  `cantidad_base` convierte el precio del empaque en un
                  "precio por kilo" falso, que es peor que no tener el dato.

    `regla` no es columna del CSV: es trazabilidad. Separa lo que salió de una
    medida escrita tal cual ("500g") de lo que salió de multiplicar un
    multipack, que es donde el parser puede equivocarse sin que se note.
    """

    unidad = (measurement_unit or "").strip().lower()

    if unidad and unidad != "un":
        multiplicador = dec(unit_multiplier)
        base = UNIDADES.get(unidad)

        if multiplicador is not None and multiplicador > CERO and base:
            return base[0], f"{(multiplicador * base[1]).normalize():f}", "VTEX", "vtex"

        # `measurement_unit` raro y sin tabla: no se inventa una equivalencia.
        return "", "", "DESCONOCIDO", "unidad_no_reconocida"

    parseado = parsear_presentacion_nombre(nombre)

    if parseado:
        return parseado[0], f"{parseado[1].normalize():f}", "NOMBRE", parseado[2]

    return "", "", "DESCONOCIDO", "sin_medida_en_el_nombre"


# Tolerancia del control de peso variable. DOS condiciones, y hay que pasar
# las dos para que la fila se marque.
#
# §4 pide marcar cuando la reconstrucción difiere de `list_price` "en más de
# un céntimo". Medido contra las 8 filas de peso variable de golden_v5.csv,
# ese umbral solo se dispara en 4 de ellas — y las 4 son normales:
#
#     Huachalomo   28.9857 vs 28.99   dif 0.0043   0.01%
#     Guiso        28.4857 vs 28.49   dif 0.0043   0.02%
#     Granadilla    9.6667 vs  9.69   dif 0.0233   0.24%
#     Maracuyá      6.1500 vs  6.19   dif 0.0400   0.65%
#
# La causa está en el propio §4: el peso es un PROMEDIO DECLARADO ("0.2 kg
# aprox."), no el peso real de la pieza. O sea que la división y `list_price`
# tienen por qué diferir siempre, y el céntimo absoluto convierte el control
# en una alarma que suena en la mitad de los productos por peso. Esa es
# exactamente la falla que ya costó una regla DQ (ver DQ_PRECIO_MAYOR, falso
# positivo en 7 de 100 SKUs por confundir unidad con error).
#
# El céntimo se conserva como PISO —para que un producto barato no dispare
# por ruido de centavos— y se le suma un piso RELATIVO. El error que este
# control busca de verdad es un multiplicador en otra escala (gramos donde
# dicen kilos: 1000x, no 0.65%), y 5% lo separa del promedio declarado con
# casi dos órdenes de magnitud de margen sobre el peor caso medido.
TOLERANCIA_UNIDAD = 0.01
TOLERANCIA_UNIDAD_RELATIVA = 0.05


def calcular_precio_por_unidad(
    selling_price_cents: Any,
    list_price_cents: Any,
    unit_multiplier: Any,
) -> tuple[str, bool]:
    """
    Precio por unidad de medida, y si la reconstrucción lo contradice.

    Devuelve `(precio_por_unidad, sospechoso)`.

    Dos ramas, porque son dos preguntas distintas (§4):

    **`unit_multiplier == 1`** — producto por unidad. El precio por unidad ES
    el precio, y `sellingPrice / multiplicador` da exactamente eso.

    **`unit_multiplier != 1`** — peso variable. Acá `list_price` NO es precio
    tachado: es el precio de la unidad base, y VTEX lo dice él mismo. Hasta
    1.0.0 esto se calculaba dividiendo (`venta / 100 / multiplicador`), que
    arrastra el redondeo de VTEX y lo AMPLIFICA cuando el multiplicador es
    menor a 1:

        Maracuyá: 1.23 ÷ 0.2 = 6.15   y el kilo real es 6.19

    Cuatro céntimos por kilo no rompen una fila, pero sí una comparación
    entre retailers, que es justo para lo que existe esta columna. Leer el
    dato que el servidor ya dio es más barato y más cierto que derivarlo.

    La división se conserva como CONTROL: si difiere de `list_price` más de
    lo que el promedio declarado explica (ver TOLERANCIA_UNIDAD*), el
    multiplicador no corresponde al precio y la fila sale marcada
    (`DQ_UNIDAD_INCONSISTENTE`). No se corrige el número —no sabemos cuál de
    los dos está mal—, se avisa.
    """

    try:
        venta = float(selling_price_cents or 0)
        lista = float(list_price_cents or 0)
        multiplicador = float(unit_multiplier or 1)
    except (TypeError, ValueError):
        return "", False

    if multiplicador <= 0 or venta <= 0:
        return "", False

    division = venta / 100 / multiplicador

    if multiplicador == 1:
        return f"{division:.4f}", False

    # Peso variable sin `list_price` utilizable: queda la división, que es
    # lo único que hay. Vacío sería peor: el dato existe, solo es más flojo.
    if lista <= 0:
        return f"{division:.4f}", False

    por_unidad = lista / 100

    diferencia = abs(division - por_unidad)

    sospechoso = (
        diferencia > TOLERANCIA_UNIDAD
        and diferencia > por_unidad * TOLERANCIA_UNIDAD_RELATIVA
    )

    return f"{por_unidad:.4f}", sospechoso


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
        DQ_UNIDAD_INCONSISTENTE
                          peso variable donde `sellingPrice / multiplicador`
                          no reconstruye `listPrice`. Uno de los dos está
                          mal y no sabemos cuál: se avisa, no se corrige
                          (§4).

    La cuarta entra con la misma vara que las otras tres: tiene un caso real
    detrás y un consumidor concreto. `price_per_unit` es la columna con la
    que se compara contra otro retailer; si el multiplicador declarado no
    corresponde al precio, esa comparación sale mal sin que nada lo delate.

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

    _, unidad_sospechosa = calcular_precio_por_unidad(
        item.get("sellingPrice") if item else None,
        item.get("listPrice") if item else None,
        item.get("unitMultiplier") if item else None,
    )

    if unidad_sospechosa:
        fallos.append("DQ_UNIDAD_INCONSISTENTE")

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
      operador_externo  origen identificado, pero no es Makro ni PlazaVea
      desconocido       falta información logística para decidir

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

    # `operador_externo` vs `desconocido` (§9).
    #
    # Hasta 1.0.0 todo lo que no encajaba en los patrones de arriba caía en
    # `desconocido`, que mezclaba dos cosas opuestas: "VTEX no me dijo de
    # dónde sale" y "VTEX me lo dijo con todo detalle y resulta que no es
    # Makro". Caso real:
    #
    #     warehouseId  STK917NF
    #     dockId       DCK-NF-MK-917
    #     courierId    DD-NF-CD-917-URBANO
    #     SLA único, despacho confirmado
    #
    # El origen está perfectamente identificado. Llamarlo `desconocido`
    # invita a tratarlo como dato faltante —algo que revisar o reintentar—
    # cuando es un hecho firme sobre el surtido: ese SKU no sale de Makro.
    #
    # Se exige la firma COMPLETA (almacén + dock + courier) para no
    # reclasificar como "identificado" una respuesta a medio parsear.
    if warehouse and dock and courier:
        return "operador_externo"

    # Reservado para lo que de verdad falta información.
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
    cantidad: int = 1,
) -> tuple[int, Any]:
    """
    Una sola request devuelve precio + logística.

    La simulación NO guarda estado en el contexto: la geolocalización va
    en el body de cada llamada. Por eso el contexto del navegador se
    puede reutilizar sin riesgo de contaminación entre mediciones.

    `cantidad` es 1 en toda la medición normal, y es lo que hace que el
    motor RECONSTRUYA el precio mayorista en vez de observarlo (§1: a qty=1
    el descuento no se aplica y `priceTags` viene vacío). La auditoría del
    mayorista es el único lugar que la sube, a `qty = bi_umbral`, para ver
    el precio que el cliente pagaría de verdad.
    """

    cuerpo = {
        "items": [
            {
                "id": int(producto.sku_id),
                "quantity": max(1, int(cantidad)),
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


def enriquecer_fila(
    fila: Fila,
    producto: Producto,
    nodo: Nodo,
    datos: Any,
    item: dict[str, str] | None = None,
) -> None:
    """
    Llena las 22 columnas de 1.1.0 sobre una `Fila` ya construida (§5).

    Es PEGAMENTO, a propósito delgado: cada regla vive en su función pura
    (`calcular_mayorista`, `clasificar_ean`, `resolver_presentacion`,
    `leer_regimen`, `calcular_precio_por_unidad`) y acá solo se decide qué
    alimenta a qué. Si esta función empieza a decidir cosas, la regla se
    volvió imposible de probar sin una respuesta de VTEX al lado.

    Nunca lanza: una fila a medio medir sale con columnas vacías y su estado
    explicándolas, igual que el resto del motor.
    """

    # ---- BI-PRECIO ------------------------------------------------------
    #
    # El descuento se lee PRIMERO de la medición (viene por sucursal y está
    # vivo) y solo si no vino, del catálogo (sin contexto de sucursal). El
    # umbral SOLO existe en el catálogo. Unir las dos fuentes por sku_id es
    # el mecanismo entero (§1).
    descuento = leer_descuento(datos)

    if descuento is None:
        descuento = dec(producto.descuento_catalogo)

    price_cents = entero(fila.price_cents)
    # La base del escalón es `ListPrice`, no `Price` (v18). Se lee del item
    # en céntimos crudos, igual que `price_cents`, para no ir y volver por el
    # string en soles.
    list_price_cents = entero((item or {}).get("listPrice"))
    umbral = entero(producto.bi_umbral)

    veredicto = calcular_mayorista(price_cents, list_price_cents, umbral, descuento)
    completo = veredicto["estado"] == "COMPLETO"

    mayorista_cents = veredicto["mayorista_cents"]
    mayorista = Decimal(mayorista_cents) / 100 if mayorista_cents is not None else None

    # BASE EXPLÍCITA (§6.5): el porcentaje mide el AHORRO CONTRA `price` —
    # cuánto deja de pagar por unidad quien compra el escalón en vez de
    # comprar de a uno. NO es el descuento sobre `list_price`.
    #
    # Es la pregunta que un analista de pricing hace de verdad: `list_price`
    # es un precio que hoy nadie paga si hay promoción unitaria encima, así
    # que un porcentaje medido contra él exagera el beneficio del escalón.
    #
    # Hasta v17 daba igual —con la base vieja el ahorro ERA el descuento— y
    # por eso podía escribirse como `descuento / price`. Con la base
    # corregida son dos números distintos en toda fila con promoción
    # unitaria. Y esta es además la fórmula que la auditoría del mayorista ya
    # usaba sobre el precio medido: hasta ahora una fila auditada y una
    # reconstruida reportaban porcentajes calculados de dos maneras.
    pct = None

    if completo and mayorista_cents is not None and price_cents:
        pct = Decimal(price_cents - mayorista_cents) / Decimal(price_cents) * 100

    # §6.2 REESCRITA (v18): el umbral se vacía SOLO si el catálogo no lo
    # declara. Antes se vaciaba en todo estado != COMPLETO, y eso destruía
    # evidencia real: en SIN_DESCUENTO (236 filas de run_20260822_020027) y en
    # BIPRECIO_SUPERADO_POR_PROMO (233) el umbral se conoce y es un hecho del
    # catálogo — lo que no existe es el precio. Vaciar el precio no puede
    # obligar a vaciar el umbral.
    #
    # El riesgo que la regla vieja atacaba —alguien arma el mayorista a mano
    # y le sale distinto— sigue cubierto por `biprecio_status` en la columna
    # de al lado, que dice exactamente por qué no hay precio.
    fila.bi_umbral = str(umbral) if umbral is not None else ""
    tri = entero(producto.tri_umbral)
    fila.tri_umbral_declarado = str(tri) if tri is not None else ""
    fila.descuento_monto = money(veredicto["descuento"])
    fila.descuento_monto_cents = (
        str(veredicto["descuento_cents"])
        if veredicto["descuento_cents"] is not None
        else ""
    )
    fila.precio_mayorista = money(mayorista) if completo else ""
    fila.precio_mayorista_cents = str(mayorista_cents) if completo else ""
    fila.descuento_mayorista_pct = (
        f"{pct.quantize(CENTAVO)}" if pct is not None else ""
    )
    fila.biprecio_status = veredicto["estado"]
    # El motor mide a qty=1 SIEMPRE, así que nunca observa el mayorista
    # aplicado: lo reconstruye. `SI` solo puede ponerlo algo que haya medido
    # a qty≥umbral, y hoy eso no existe en el motor. Decir `SI` acá sería
    # inventar evidencia.
    fila.precio_mayorista_verificado = "NO" if completo else ""

    # ---- RÉGIMEN PROMOCIONAL -------------------------------------------
    regimen = leer_regimen(datos)

    fila.promo_regime_id = (
        regimen["promo_regime_id"] or producto.promo_regime_id_catalogo
    )
    fila.promo_regime_name = (
        regimen["promo_regime_name"] or producto.promo_regime_name_catalogo
    )
    fila.payment_method_id = (
        regimen["payment_method_id"] or producto.payment_method_id_catalogo
    )
    fila.price_valid_until = leer_price_valid_until(datos, producto.sku_id)

    # ---- IDENTIDAD Y CALIDAD -------------------------------------------
    fila.ean_type = clasificar_ean(producto.ean)
    fila.category_id = producto.category_id
    fila.sales_channel = SALES_CHANNEL
    fila.surtido_makro = (
        "SI" if f"plazaveamko{nodo.node_id}" in (fila.seller_chain or "") else "NO"
    )

    # ---- PRESENTACIÓN ---------------------------------------------------
    unidad_base, cantidad_base, origen, _regla = resolver_presentacion(
        fila.measurement_unit,
        fila.unit_multiplier,
        producto.product_name or fila.product_name,
    )

    fila.unidad_base = unidad_base
    fila.cantidad_base = cantidad_base
    fila.presentacion_origen = origen

    cantidad = dec(cantidad_base)

    if cantidad and cantidad > CERO:
        # Rama VTEX (peso variable): el precio por unidad base ya lo publica
        # el servidor en `list_price`, y dividir amplifica su redondeo (§4).
        # Es la MISMA regla que `price_per_unit`, así que se reusa la misma
        # función pura en vez de repetir el criterio y arriesgar que se
        # separen.
        if origen == "VTEX":
            fila.precio_por_unidad_base, _ = calcular_precio_por_unidad(
                fila.price_cents,
                (item or {}).get("listPrice"),
                fila.unit_multiplier,
            )
        elif price_cents:
            fila.precio_por_unidad_base = money4(
                Decimal(price_cents) / 100 / cantidad
            )

        # El mayorista por unidad base SIEMPRE se deriva: VTEX no publica un
        # `list_price` del precio mayorista, así que acá no hay dato del
        # servidor que leer. Queda dicho para que nadie lo lea como si
        # tuviera el mismo respaldo que la línea de arriba.
        if completo and mayorista_cents is not None:
            fila.precio_mayorista_por_unidad_base = money4(
                Decimal(mayorista_cents) / 100 / cantidad
            )


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

    # Precio por unidad de medida: el único comparable entre tiendas cuando
    # el producto se vende por peso. La regla vive en una función pura
    # (§4) — acá solo se aplica y se anota la sospecha.
    fila.price_per_unit, _ = calcular_precio_por_unidad(
        item.get("sellingPrice"),
        item.get("listPrice"),
        item.get("unitMultiplier"),
    )

    try:
        lista = float(item.get("listPrice") or 0)
        venta = float(item.get("sellingPrice") or 0)
        multiplicador = float(item.get("unitMultiplier") or 1)

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
        # También acá: un HTTP 500 no borra lo que el catálogo ya sabía del
        # producto (umbral, EAN, presentación). La fila sale sin precio pero
        # con su identidad completa, y `biprecio_status` dice SIN_MEDICION.
        enriquecer_fila(fila, producto, nodo, datos, item)
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

    enriquecer_fila(fila, producto, nodo, datos, item)

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

        # Sin respuesta no hay precio ni régimen, pero lo que el catálogo ya
        # había dicho del producto sigue siendo cierto y se escribe igual:
        # EAN, category_id, presentación, tri declarado. `biprecio_status`
        # sale SIN_MEDICION, que es exactamente lo que pasó (§5).
        enriquecer_fila(fila, producto, nodo, None)

        return fila


# ===========================================================================
# ESCRITURA
# ===========================================================================


# La guardia de esquema de 1.0.0 (`archivar_si_cambio_el_esquema`) se
# ELIMINA acá, y conviene dejar escrito por qué antes de que alguien la
# extrañe.
#
# Existía porque el CSV era APPEND: agregar un campo a `Fila` agregaba una
# columna, y `DictWriter` seguía escribiendo filas más anchas debajo de una
# cabecera más angosta — el histórico se desalineaba en silencio. La guardia
# renombraba el archivo viejo antes de que eso pasara.
#
# Con una carpeta inmutable por corrida (§7) el vector desapareció: cada
# corrida escribe su propio `filas.csv` con su propia cabecera y no vuelve a
# abrirse nunca. Dos corridas con esquemas distintos ya no comparten archivo,
# así que no hay nada que desalinear. La propiedad que la guardia protegía
# —poder saber bajo qué reglas nació una fila— la sigue dando
# `schema_version`, que va escrito en CADA fila (§6.7).
#
# Lo que SÍ hereda el lector: un glob sobre `run_*/filas.csv` puede juntar
# cabeceras distintas. Eso es trabajo de la capa de consolidación, que tiene
# `schema_version` para decidir, y no algo que el motor pueda resolver
# escribiendo.


def escribir_filas(filas: list[Fila]) -> Path | None:
    """
    Escribe el CSV largo de la corrida. Una fila por (SKU, nodo).

    Cambia respecto de 1.0.0 en dos cosas, y las dos son de §7/§8:

    1. **Un solo archivo**, no uno por sucursal. El nodo es una COLUMNA
       (`node_id`), no un nombre de archivo. Así una sucursal nueva no crea
       un archivo nuevo ni cambia el esquema: agrega valores a una columna.
    2. **Se escribe, no se apendea.** La carpeta es de esta corrida y no
       existía hace un segundo; abrir en modo append sería sugerir que
       alguien más podría estar escribiendo ahí.

    La serie de tiempo no se pierde: la producen las carpetas acumuladas.
    `read_csv('data/makro_plazavea/run_*/filas.csv')` es la historia entera.
    """

    if DRY_RUN:
        return None

    destino = archivo_filas()
    destino.parent.mkdir(parents=True, exist_ok=True)

    with destino.open("w", encoding="utf-8-sig", newline="") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=COLUMNAS)
        escritor.writeheader()

        for fila in filas:
            escritor.writerow(asdict(fila))

    return destino


def registrar_corrida(resumen: dict[str, Any]) -> None:
    """
    Deja el manifiesto en los tres lugares donde sirve para algo distinto.

        run.json        dentro de la carpeta — el dataset se explica solo
        last_run.json   en la raíz — la última corrida sin globear
        runs.jsonl      en la raíz, APPEND — el índice de la serie

    `runs.jsonl` es lo único append-only que queda en el layout, y a
    propósito: es un índice, no un dataset. Guarda una línea PLANA por
    corrida (no el manifiesto entero) para que siga siendo legible con
    `tail` cuando haya mil corridas.
    """

    if DRY_RUN:
        return

    carpeta = carpeta_corrida()
    carpeta.mkdir(parents=True, exist_ok=True)

    texto = json.dumps(resumen, ensure_ascii=False, indent=2)

    archivo_manifiesto().write_text(texto, encoding="utf-8")
    archivo_ultima().write_text(texto, encoding="utf-8")

    indice = {
        "run_id": resumen.get("run_id"),
        "corrida": resumen.get("corrida"),
        "version_script": resumen.get("version_script"),
        "schema_version": resumen.get("schema_version"),
        "modo": resumen.get("modo"),
        "modo_seleccion": resumen.get("modo_seleccion"),
        "filas": resumen.get("filas_escritas"),
        "requests_totales": resumen.get("requests_totales"),
        "duracion_segundos": resumen.get("duracion_segundos"),
        "corrida_completa": resumen.get("corrida_completa"),
        "exit_code": resumen.get("exit_code"),
    }

    with archivo_indice().open("a", encoding="utf-8") as archivo:
        archivo.write(json.dumps(indice, ensure_ascii=False) + "\n")


# ===========================================================================
# AUDITORÍA DEL PRECIO MAYORISTA (decisiones_1.1.0 §1, §5, §11)
# ===========================================================================
#
# La fórmula `precio_mayorista = price − descuento` está verificada al
# centavo en 5 SKUs (§1), pero SOLO en umbrales 2, 3 y 4. Los altos —12, 15,
# 24— son justo donde viven los descuentos grandes (hasta 25.5%), y son los
# que más caro salen si VTEX aplica ahí otra regla: otro escalón, otro
# redondeo, un tope. §11 lo deja anotado como verificación pendiente.
#
# Esta fase la cierra de la única forma posible: pidiendo `qty = bi_umbral` y
# mirando qué precio devuelve VTEX. Todo lo demás son dos números del
# catálogo restados con confianza.


def elegir_auditables_mayorista(
    filas: list[Fila],
    node_id: str,
    cuantos: int,
) -> list[Fila]:
    """
    Elige qué filas remedir a `qty = bi_umbral`. Determinista y sin `random`.

    El criterio NO es el hash que usa `seleccionar()` para la muestra, y la
    diferencia es deliberada: acá no se busca una muestra REPRESENTATIVA, se
    busca cubrir el RANGO DE UMBRALES. Tres auditorías del mismo umbral
    prueban que la fórmula funciona para ese umbral, que es la mitad de lo
    que esta fase tiene que decir — y deja intacto el hueco de §11.

    Orden de prioridad:

    1. Un umbral BAJO (2-3), que es el caso ya verificado: si este falla, el
       problema no está en la escala sino en la fórmula.
    2. Un umbral ALTO (12-24), el hueco de §11. Se toma el MÁS alto
       disponible: es donde más lejos está la extrapolación.
    3. El resto, prefiriendo umbrales todavía no cubiertos.

    Determinismo: el desempate es `(bi_umbral, sku_id)`, así que dos corridas
    con las mismas filas COMPLETO auditan exactamente los mismos SKUs. Eso es
    lo que hace comparables dos auditorías de días distintos — y lo que hace
    que este motor reproduzca la elección de la sonda que generó el golden.
    """

    disponibles = [
        f for f in filas
        if f.node_id == node_id
        and f.biprecio_status == "COMPLETO"
        and entero(f.bi_umbral)
    ]

    if not disponibles or cuantos <= 0:
        return []

    por_umbral = sorted(disponibles, key=lambda f: (entero(f.bi_umbral), f.sku_id))

    elegidos: list[Fila] = []

    bajos = [f for f in por_umbral if 2 <= entero(f.bi_umbral) <= 3]
    altos = [f for f in por_umbral if 12 <= entero(f.bi_umbral) <= 24]

    if bajos:
        elegidos.append(bajos[0])

    if altos and altos[-1] not in elegidos:
        elegidos.append(altos[-1])

    # Si el rango pedido no existe en la muestra, se completa con los
    # extremos disponibles, prefiriendo umbrales todavía no cubiertos.
    cubiertos = {entero(f.bi_umbral) for f in elegidos}

    for preferir_nuevo in (True, False):
        for candidato in [por_umbral[-1], por_umbral[0]] + por_umbral:
            if len(elegidos) >= cuantos:
                break

            if candidato in elegidos:
                continue

            umbral = entero(candidato.bi_umbral)

            if preferir_nuevo and umbral in cubiertos:
                continue

            elegidos.append(candidato)
            cubiertos.add(umbral)

    return elegidos[:cuantos]


async def auditar_mayorista(
    cliente: Cliente,
    fila: Fila,
    producto: Producto,
    nodo: Nodo,
) -> dict[str, Any]:
    """
    Remide el MISMO SKU a `qty = bi_umbral` y contrasta contra lo reconstruido.

    Si coinciden, la fila pasa a `precio_mayorista_verificado = SI` y el
    número deja de ser una resta para pasar a ser una observación.

    Si NO coinciden, gana el MEDIDO —es lo que el cliente pagaría— y la fila
    sale marcada `DQ_MAYORISTA_DISCREPA`. `descuento_monto` NO se toca: sigue
    siendo lo que VTEX declaró, y justamente que lo declarado y lo cobrado no
    cierren es el hallazgo. Una discrepancia acá invalida la fórmula de §1
    para ese umbral, que es de las cosas más caras que pueden pasarle a este
    dataset: se registra en la fila, en el manifiesto y a gritos en consola.

    Nunca lanza salvo `TopeAgotadoError`, que es condición de corrida.
    """

    umbral = entero(fila.bi_umbral)
    reconstruido = entero(fila.precio_mayorista_cents)

    resultado: dict[str, Any] = {
        "sku_id": fila.sku_id,
        "node_id": nodo.node_id,
        "producto": fila.product_name,
        "umbral": umbral,
        "unitario_cents": entero(fila.price_cents),
        "reconstruido_cents": reconstruido,
        "medido_cents": None,
        "veredicto": "",
        "detalle": "",
    }

    try:
        status, datos = await consultar_simulation(cliente, producto, nodo, umbral)
    except TopeAgotadoError:
        # Igual que en el resto del motor: el presupuesto agotado es un
        # estado de la CORRIDA. Se propaga para que main() lo trate como tal.
        raise
    except Exception as exc:
        resultado["veredicto"] = "AUDITORIA_FALLIDA"
        resultado["detalle"] = f"{type(exc).__name__}: {exc}"[:200]
        return resultado

    guardar_evidencia(producto, nodo, f"simulation_qty{umbral}", status, datos)

    if status >= 400:
        resultado["veredicto"] = "AUDITORIA_FALLIDA"
        resultado["detalle"] = f"HTTP {status}"
        return resultado

    item = extraer_item(datos, producto)
    medido = entero(item.get("sellingPrice"))

    resultado["medido_cents"] = medido

    if medido is None:
        resultado["veredicto"] = "AUDITORIA_FALLIDA"
        resultado["detalle"] = "simulation no devolvió sellingPrice"
        return resultado

    # El precio medido es el HECHO. Se escribe siempre, coincida o no.
    fila.precio_mayorista_verificado = "SI"
    fila.precio_mayorista_cents = str(medido)
    fila.precio_mayorista = money(Decimal(medido) / 100)

    unitario = entero(fila.price_cents)

    if unitario:
        # El porcentaje se recalcula contra el precio REALMENTE cobrado, para
        # que precio y porcentaje no se contradigan dentro de la misma fila.
        fila.descuento_mayorista_pct = (
            f"{(Decimal(unitario - medido) / Decimal(unitario) * 100).quantize(CENTAVO)}"
        )

    cantidad = dec(fila.cantidad_base)

    if cantidad and cantidad > CERO:
        fila.precio_mayorista_por_unidad_base = money4(
            Decimal(medido) / 100 / cantidad
        )

    if medido == reconstruido:
        resultado["veredicto"] = "COINCIDE"
        return resultado

    resultado["veredicto"] = "DISCREPA"
    resultado["detalle"] = (
        f"medido {medido} vs reconstruido {reconstruido} "
        f"({medido - (reconstruido or 0):+d} céntimos)"
    )

    fila.dq_flags = "|".join(
        [f for f in (fila.dq_flags, "DQ_MAYORISTA_DISCREPA") if f]
    )

    return resultado


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
        "carpeta_salida": str(carpeta_corrida()),
        "raiz_colector": str(SALIDA),
        # §8.1: qué eligió esta corrida. `descubrimiento` es una muestra del
        # catálogo; `skus_explicitos` es una remedición dirigida y NO debe
        # promediarse con las otras al armar la serie. Va también acá arriba,
        # además de en `seleccion`, para poder filtrar con un solo `jq`.
        "modo_seleccion": SELECCION.get("modo", "descubrimiento"),
        "dry_run": DRY_RUN,
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
        # Otra pregunta que `auditoria` (simulation vs orderForm): esta dice
        # si la FÓRMULA del mayorista reconstruye lo que el cliente paga.
        "auditoria_mayorista": {k: v for k, v in AUDITORIA_MAYORISTA.items()},
        # v13: estado explícito de fases que hasta v12 solo se podían
        # inferir leyendo filas EXCEPTION/NO_PRICE en el CSV. Ver
        # TopeAgotadoError, MEDICION y STOCK_CADENA_ESTADO.
        "medicion": dict(MEDICION),
        "stock_cadena": dict(STOCK_CADENA_ESTADO),
        # Avisos que antes solo vivían en la consola de una corrida que
        # nadie miraba en vivo (bug confirmado en v11, revisión de Codex).
        "avisos": list(AVISOS),
        "filas_escritas": len(filas),
        "por_sucursal": {},
    }

    for node_id, nodo in NODOS.items():
        propias = [f for f in filas if f.node_id == node_id]
        con_match = sum(f.logistics_status in MATCHES_VALIDOS for f in propias)

        resumen["por_sucursal"][node_id] = {
            "branch": nodo.branch,
            # Sin `archivo`: desde §8 hay UN solo CSV y el nodo es una
            # columna. Dejar acá el nombre del CSV por sucursal de 1.0.0
            # apuntaría a un archivo que esta corrida no escribió.
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

    registrar_corrida(resumen)

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
                            "Raíz del colector. Adentro se crea una carpeta "
                            "por corrida. Default: <repo>/data/" + MOTOR + "/"
                        ))

    parser.add_argument("--categoria", default="",
                        help=(
                            "Rutas de categoría separadas por coma que acotan "
                            "el universo a recorrer: '/399/,/77/' o '399,77'. "
                            "Pedir un padre incluye a sus hijos. El filtro se "
                            "aplica ANTES de recorrer, así que las categorías "
                            "descartadas no cuestan requests. --catalogo y "
                            "--por-categoria siguen operando dentro de este "
                            "universo. Una ruta que no exista en el árbol es "
                            "un error, no un aviso. Vacío = árbol completo."
                        ))

    parser.add_argument("--skus", default="",
                        help=(
                            "Lista explícita de sku_id (comas o espacios). "
                            "Salta el descubrimiento y mide SOLO esos, contra "
                            f"todos los nodos. Máximo {MAX_SKUS_EXPLICITOS}. "
                            "Incompatible con --catalogo y --muestra. Un SKU "
                            "que el catálogo no devuelva se escribe igual, "
                            "como SKU_NO_ENCONTRADO."
                        ))

    parser.add_argument("--dry-run", action="store_true", dest="dry_run",
                        help=(
                            "Mide e imprime, no escribe NADA: ni CSV, ni "
                            "manifiesto, ni evidencia cruda. Sirve para "
                            "consultar un SKU o probar una categoría sin "
                            "ensuciar la serie."
                        ))

    parser.add_argument("--auditoria", type=float, default=5.0,
                        help=(
                            "%% de mediciones que se contrastan simulation vs "
                            "orderForm para detectar discrepancias. 0 = "
                            "desactivada. Default: 5"
                        ))

    parser.add_argument("--auditoria-mayorista", type=int, default=3,
                        dest="auditoria_mayorista",
                        help=(
                            "Cuántos SKUs con bi-precio se remiden a "
                            "qty=bi_umbral para comprobar que la fórmula "
                            "price - descuento da el precio que el cliente "
                            "paga de verdad. Cuesta 1 request por SKU, fijo: "
                            "no crece con el catálogo. La selección prioriza "
                            "cubrir umbrales DISTINTOS (uno bajo, el más alto "
                            "disponible) antes que repetir el mismo. "
                            "0 = desactivada. Default: 3"
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

    # `--reiniciar` NO existe más, y conviene dejar escrito por qué.
    #
    # Borraba los CSV por sucursal para "empezar el histórico de cero", que
    # tenía sentido cuando la serie era un archivo mutable al que se
    # apendeaba. Con una carpeta inmutable por corrida (§7) no hay archivo
    # acumulado que reiniciar: lo único que el flag podría borrar es historia
    # ya cerrada, corridas que ya se explicaron a sí mismas. Un flag cuyo
    # único efecto posible es destruir datos pasados no se redefine, se saca.
    #
    # "Reiniciar la serie" hoy es empezar a leer desde otra fecha, que es una
    # decisión del que consulta y no del que extrae. Y si alguien de verdad
    # quiere el borrón, `rm -rf data/makro_plazavea/` es explícito, está
    # fuera del motor, y nadie lo escribe por accidente en un martes.

    argumentos = parser.parse_args()

    argumentos.skus = parsear_lista_skus(argumentos.skus)
    argumentos.categoria = parsear_categorias_pedidas(argumentos.categoria)

    # La existencia de cada ruta NO se puede validar acá: hace falta el árbol
    # vivo, y eso es una request. Se valida en `descubrir_catalogo`, que ya lo
    # tiene, y el error sale con el mismo exit 2 que usa argparse para un uso
    # incorrecto — es lo que es.
    if argumentos.categoria and argumentos.skus:
        parser.error(
            "--categoria y --skus son incompatibles: --skus ya nombra "
            "exactamente qué medir, así que acotar el universo no cambia "
            "nada y sugeriría un filtro que no se aplicó."
        )

    if argumentos.skus:
        # O lista explícita, o descubrimiento. Las dos juntas dejarían la
        # selección sin una única fuente y el manifiesto no podría decir
        # honestamente qué eligió esta corrida (§8.1).
        if argumentos.catalogo or argumentos.muestra:
            parser.error(
                "--skus es incompatible con --catalogo y --muestra: "
                "o se mide una lista explícita, o se descubre el catálogo."
            )

        if len(argumentos.skus) > MAX_SKUS_EXPLICITOS:
            parser.error(
                f"--skus admite hasta {MAX_SKUS_EXPLICITOS} SKUs y se "
                f"pasaron {len(argumentos.skus)}. Para medir más, descubrí "
                "el catálogo con --catalogo/--muestra: --skus es para una "
                "pregunta puntual o para remedir un baseline."
            )

    return argumentos


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

    global RUN_ID, GUARDAR_EVIDENCIA, SALIDA, DRY_RUN
    RUN_ID = inicio.strftime("run_%Y%m%d_%H%M%S")
    DRY_RUN = argumentos.dry_run
    # --dry-run apaga la evidencia aunque no se haya pedido --sin-evidencia:
    # "no escribe nada" no admite excepciones (§8.1).
    GUARDAR_EVIDENCIA = not argumentos.sin_evidencia and not DRY_RUN

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
    AUDITORIA_MAYORISTA.clear()
    AUDITORIA_MAYORISTA.update(
        {
            "solicitadas": 0,
            "realizadas": 0,
            "coinciden": 0,
            "discrepan": 0,
            "fallidas": 0,
            "node_id": "",
            "umbrales_auditados": [],
            "detalle": [],
        }
    )

    if argumentos.salida:
        SALIDA = Path(argumentos.salida).expanduser().resolve()

    # La carpeta de la corrida se crea recién al escribir. Con --dry-run no
    # se crea nunca: una corrida que no deja datos tampoco tiene por qué
    # dejar carpetas vacías por las que después alguien se pregunte.
    if not DRY_RUN:
        SALIDA.mkdir(parents=True, exist_ok=True)

    # Aviso de reubicación.
    #
    # Hasta v10 todo caía en `salida/` a secas. Si esos archivos siguen
    # ahí, el motor NO los toca: mover el histórico de alguien por
    # iniciativa propia no es tarea de un extractor. Pero callarlo sería
    # peor — quedarían dos CSV con el mismo nombre en dos carpetas y la
    # próxima duda sería cuál de los dos es el bueno.
    if RAIZ_SALIDA_LEGADO.exists():
        viejos = sorted(
            str(ruta.relative_to(RAIZ_SALIDA_LEGADO))
            for ruta in RAIZ_SALIDA_LEGADO.rglob("*.csv")
        )

        if viejos:
            log("AVISO: hay salida del layout 1.0.0 dentro del paquete:")

            for nombre in viejos[:10]:
                log(f"  {RAIZ_SALIDA_LEGADO / nombre}")

            if len(viejos) > 10:
                log(f"  ... y {len(viejos) - 10} archivos más")

            log(f"1.1.0 escribe en {SALIDA} y NO toca esos archivos.")
            log("")

            AVISOS.append(
                f"CSV del layout 1.0.0 detectados en {RAIZ_SALIDA_LEGADO} "
                f"({len(viejos)} archivos). No se tocaron. Esta corrida "
                f"escribió en {carpeta_corrida()}."
            )

    log("=" * 110)
    log(f"EXTRACTOR DE PRECIOS POR SUCURSAL — MAKRO PERÚ    v{VERSION}")
    log("=" * 110)
    log(f"Script     : {Path(__file__).name}")
    log(f"Run ID     : {RUN_ID}   (esquema {SCHEMA_VERSION})")
    log(f"Colector   : {MOTOR}  ({RETAILER})")
    log(
        "Salida     : "
        + ("nada (--dry-run: se mide y se imprime)" if DRY_RUN
           else str(carpeta_corrida()))
    )
    log(f"Modo       : {argumentos.modo}")
    log(
        "Catálogo   : "
        + (
            f"lista explícita de {len(argumentos.skus)} SKUs "
            "(sin descubrimiento)"
            if argumentos.skus
            else f"descubrimiento cortado en {argumentos.catalogo} SKUs"
            if argumentos.catalogo
            else "completo (se recorre todo el árbol)"
        )
        + (
            "  ·  ACOTADO a "
            + ", ".join(f"/{r}/" for r in argumentos.categoria)
            if argumentos.categoria
            else ""
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
        "Mayorista  : "
        + (
            f"{argumentos.auditoria_mayorista} SKUs remedidos a qty=bi_umbral"
            if argumentos.auditoria_mayorista > 0
            else "auditoría desactivada (el mayorista queda reconstruido)"
        )
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

            # SKUs pedidos por --skus que el catálogo no devolvió. Se
            # arrastran hasta la salida: cada uno se escribe igual, una fila
            # por nodo (§8.1). Vacío en modo descubrimiento.
            ausentes: list[str] = []

            SELECCION.clear()

            if argumentos.skus:
                # ------------ SELECCIÓN EXPLÍCITA (§8.1) ------------
                log("SELECCIÓN EXPLÍCITA — sin descubrimiento")
                log("-" * 110)

                catalogo, ausentes, sin_resolver = await descubrir_por_skus(
                    cliente, argumentos.skus
                )
                seleccion = catalogo

                SELECCION.update(
                    {
                        "modo": "skus_explicitos",
                        "metodo": "lista explícita en la línea de comandos",
                        "skus_pedidos": list(argumentos.skus),
                        # Se preguntó y el catálogo dijo que no: es un dato
                        # del SKU y lleva fila propia.
                        "skus_no_encontrados": ausentes,
                        # Nunca se llegó a preguntar (presupuesto agotado):
                        # es un dato de la CORRIDA y NO lleva fila.
                        "skus_sin_resolver": sin_resolver,
                        "descubiertos": len(catalogo),
                        "seleccionados": len(seleccion),
                    }
                )
            else:
                log("DESCUBRIMIENTO DEL CATÁLOGO")
                log("-" * 110)

                if presupuesto_descubrimiento:
                    log(
                        f"Presupuesto reservado para esta fase: "
                        f"{presupuesto_descubrimiento} de {cliente.tope} "
                        "requests totales."
                    )

                try:
                    catalogo = await descubrir_catalogo(
                        cliente,
                        limite=argumentos.catalogo,
                        por_categoria=argumentos.por_categoria,
                        presupuesto_fase=presupuesto_descubrimiento,
                        categorias_pedidas=argumentos.categoria,
                    )
                except CategoriaInexistenteError as exc:
                    # Error de ARGUMENTOS, detectable solo con el árbol vivo
                    # en la mano. Mismo exit 2 que argparse usa para un uso
                    # incorrecto: la corrida no empezó, y no debe parecer que
                    # midió cero por falta de stock.
                    log("")
                    log("!" * 110)
                    log(f"ARGUMENTO INVÁLIDO: {exc}")
                    log("!" * 110)
                    return 2

                # ---------------- SELECCIÓN ----------------
                seleccion = seleccionar(
                    catalogo, argumentos.muestra, argumentos.semilla
                )

                SELECCION.update(
                    {
                        "modo": "descubrimiento",
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
                f"SELECCIÓN: {len(seleccion)} SKUs"
                + (
                    f" de los {len(argumentos.skus)} pedidos"
                    + (f", {len(ausentes)} no encontrados" if ausentes else "")
                    if argumentos.skus
                    else f" de {len(catalogo)} descubiertos"
                )
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

            # Estimación de costo, antes de gastar la parte cara. Descubrir
            # cuesta ~1 request por categoría; MEDIR cuesta SKUs × nodos (y
            # hasta ×4 con fallback y reintentos). Este es el último punto
            # donde abortar todavía ahorra algo, así que el número va acá y
            # no al final, donde ya sería una autopsia.
            segundos = total * argumentos.intervalo

            log("")
            log(
                f"COSTO ESTIMADO DE LA MEDICIÓN: {len(seleccion)} SKUs × "
                f"{len(NODOS)} nodos = {total} mediciones "
                f"(~{total}-{total * 4} requests, ~{segundos / 60:.0f}-"
                f"{segundos * 4 / 60:.0f} min a {argumentos.intervalo}s)"
            )

            if argumentos.categoria:
                log(
                    "Alcance: solo "
                    + ", ".join(f"/{r}/" for r in argumentos.categoria)
                    + " — NO es el catálogo completo."
                )

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

            # La GUARDIA DE ESQUEMA de 1.0.0 iba acá. Ya no hace falta:
            # cada corrida escribe su propio CSV en su propia carpeta, así
            # que no hay cabecera vieja que un esquema nuevo pueda
            # desalinear. El razonamiento completo está donde vivía la
            # función, arriba de escribir_filas().

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

            # ---------------- AUDITORÍA DEL MAYORISTA ----------------
            #
            # Va DESPUÉS de la medición porque necesita las filas ya
            # construidas: se auditan las que salieron COMPLETO, y eso no se
            # sabe hasta haberlas medido. Y va ANTES de la salida porque
            # MUTA esas filas — el precio medido reemplaza al reconstruido.
            #
            # Se salta entera si no queda presupuesto: una fila con el
            # mayorista reconstruido y `verificado = NO` es un dato honesto;
            # una corrida que muere por auditar no lo es.
            if argumentos.auditoria_mayorista > 0 and not presupuesto_agotado_global:
                cliente.fase = "auditoria_mayorista"

                nodo_auditoria = next(iter(NODOS.values()))

                auditables = elegir_auditables_mayorista(
                    filas, nodo_auditoria.node_id, argumentos.auditoria_mayorista
                )

                AUDITORIA_MAYORISTA["node_id"] = nodo_auditoria.node_id
                AUDITORIA_MAYORISTA["solicitadas"] = len(auditables)

                if not auditables:
                    log("")
                    log(
                        "Auditoría del mayorista: ninguna fila salió "
                        f"biprecio_status=COMPLETO en el nodo "
                        f"{nodo_auditoria.node_id} — no hay nada que auditar."
                    )
                else:
                    log("")
                    log("AUDITORÍA DEL PRECIO MAYORISTA")
                    log("-" * 110)
                    log(
                        f"{len(auditables)} SKUs remedidos a qty=bi_umbral "
                        f"contra el nodo {nodo_auditoria.node_id} "
                        f"({nodo_auditoria.branch}) — es la única prueba de "
                        "que la fórmula de §1 reconstruye lo que el cliente "
                        "pagaría."
                    )

                # `medir()` recibe el Producto; acá las filas ya no lo
                # tienen, así que se busca por sku_id sobre la selección de
                # esta misma corrida.
                por_sku = {p.sku_id: p for p in seleccion}

                for fila_auditada in auditables:
                    producto = por_sku.get(fila_auditada.sku_id)

                    if producto is None:
                        continue

                    try:
                        resultado = await auditar_mayorista(
                            cliente, fila_auditada, producto, nodo_auditoria
                        )
                    except TopeAgotadoError as exc:
                        # Mismo trato que las otras fases: es condición de
                        # CORRIDA. Se corta la auditoría, se conserva todo lo
                        # medido, y queda constancia. No degrada la medición:
                        # las filas ya tienen su mayorista reconstruido.
                        log(
                            f"AVISO: presupuesto agotado durante la auditoría "
                            f"del mayorista ({exc}). Se detiene aquí; las "
                            "filas conservan el mayorista reconstruido."
                        )
                        AVISOS.append(
                            "Presupuesto agotado durante auditoria_mayorista: "
                            f"{AUDITORIA_MAYORISTA['realizadas']} de "
                            f"{len(auditables)} auditorías completadas."
                        )
                        break

                    AUDITORIA_MAYORISTA["detalle"].append(resultado)

                    if resultado["veredicto"] == "AUDITORIA_FALLIDA":
                        AUDITORIA_MAYORISTA["fallidas"] += 1
                    else:
                        AUDITORIA_MAYORISTA["realizadas"] += 1
                        AUDITORIA_MAYORISTA["umbrales_auditados"].append(
                            resultado["umbral"]
                        )

                        if resultado["veredicto"] == "COINCIDE":
                            AUDITORIA_MAYORISTA["coinciden"] += 1
                        else:
                            AUDITORIA_MAYORISTA["discrepan"] += 1

                    log(
                        f"  {resultado['sku_id']:>9} qty={resultado['umbral']:<3} "
                        f"{(resultado['producto'] or '')[:34]:<36} "
                        f"reconstruido={resultado['reconstruido_cents']} "
                        f"medido={resultado['medido_cents']} "
                        f"{resultado['veredicto']} {resultado['detalle']}"
                    )

                AUDITORIA_MAYORISTA["umbrales_auditados"] = sorted(
                    set(AUDITORIA_MAYORISTA["umbrales_auditados"])
                )

            await contexto.close()

        finally:
            await navegador.close()

    # ---------------- SALIDA ----------------
    #
    # Las filas de los SKUs que se pidieron y el catálogo no devolvió se
    # agregan ACÁ, después de medir y antes de escribir (§8.1). No pasan por
    # `medir()` porque no hay nada que medir: no existe el producto. Pero se
    # escriben igual, una por nodo, porque su ausencia en el CSV sería
    # indistinguible de no haberlos pedido nunca.
    if ausentes:
        momento_ausentes = ahora()

        for sku_id in ausentes:
            for nodo in NODOS.values():
                filas.append(fila_sku_ausente(sku_id, nodo, momento_ausentes))

        log("")
        log(
            f"{len(ausentes)} SKUs pedidos no existen en el catálogo: se "
            f"escriben {len(ausentes) * len(NODOS)} filas SKU_NO_ENCONTRADO "
            f"({', '.join(ausentes)})."
        )

    # Orden estable (sku_id, node_id): el CSV largo se lee de a pares y dos
    # corridas se diffean sin ordenar antes.
    filas.sort(key=lambda f: (f.sku_id, f.node_id))

    ruta_csv = escribir_filas(filas)

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
            f"verificados={verificados:<4}"
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

    # ---- AUDITORÍA DEL MAYORISTA ----
    #
    # Mismo principio que el bloque de arriba: una auditoría que no reporta no
    # cumple su función. Si la fórmula de §1 empieza a fallar, el dataset
    # entero de precios mayoristas queda en duda, y eso no puede enterarse
    # solo quien abra el CSV a mirar una columna que no sabe que existe.
    if AUDITORIA_MAYORISTA["solicitadas"]:
        umbrales = AUDITORIA_MAYORISTA["umbrales_auditados"]

        log("")
        log(
            f"Mayorista        : {AUDITORIA_MAYORISTA['realizadas']}/"
            f"{AUDITORIA_MAYORISTA['solicitadas']} SKUs remedidos a "
            f"qty=bi_umbral contra el nodo {AUDITORIA_MAYORISTA['node_id']}"
        )

        if umbrales:
            log(
                f"                   umbrales auditados: "
                + ", ".join(str(u) for u in umbrales)
                + (
                    "  (ninguno alto: la fórmula sigue sin probarse en 12/15/24)"
                    if umbrales and max(umbrales) < 12
                    else ""
                )
            )

        if AUDITORIA_MAYORISTA["discrepan"] == 0 and AUDITORIA_MAYORISTA["realizadas"]:
            log(
                f"                   COINCIDEN — el precio medido a qty=umbral "
                f"es exacto al reconstruido en "
                f"{AUDITORIA_MAYORISTA['coinciden']}/"
                f"{AUDITORIA_MAYORISTA['realizadas']}"
            )
        elif AUDITORIA_MAYORISTA["discrepan"]:
            log("")
            log("!" * 110)
            log(
                f"LA FÓRMULA DEL MAYORISTA FALLÓ EN "
                f"{AUDITORIA_MAYORISTA['discrepan']} DE "
                f"{AUDITORIA_MAYORISTA['realizadas']} AUDITORÍAS."
            )
            log("precio_mayorista = price - descuento NO reprodujo lo que VTEX")
            log("cobra a qty=umbral. Las filas afectadas llevan el precio MEDIDO")
            log("y la marca DQ_MAYORISTA_DISCREPA, pero el resto del dataset")
            log("sigue reconstruido con una fórmula que acaba de fallar: revisa")
            log("manifiesto.auditoria_mayorista antes de usar esa columna.")

            for caso in AUDITORIA_MAYORISTA["detalle"]:
                if caso["veredicto"] == "DISCREPA":
                    log(
                        f"  SKU {caso['sku_id']:<10} qty={caso['umbral']:<3} "
                        f"{caso['detalle']}"
                    )

            log("!" * 110)

        if AUDITORIA_MAYORISTA["fallidas"]:
            log(
                f"                   {AUDITORIA_MAYORISTA['fallidas']} no se "
                "pudieron completar (la fila conserva el reconstruido)"
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
            log(f"formato de logisticsInfo. La evidencia cruda está en {archivo_evidencia()}.")
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

    if DRY_RUN:
        log("--dry-run: no se escribió nada (ni CSV, ni manifiesto, ni crudo).")
        log(f"           habrían sido {len(filas)} filas en {carpeta_corrida()}")
    else:
        log(f"  {ruta_csv}")
        log(f"  {archivo_manifiesto()}")

        if GUARDAR_EVIDENCIA and archivo_evidencia().exists():
            log(f"  {archivo_evidencia()}")

        log(f"  {archivo_indice()}")
        log(f"  {archivo_ultima()}")

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
