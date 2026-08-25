#!/usr/bin/env python3
"""
capturar_mk_shipping.py

Captura las requests/responses relacionadas con el checkout/logística de Makro
después de que el usuario establezca manualmente una dirección válida.

OBJETIVO:
    Capturar la request real que genera el navegador cuando Makro ya tiene
    seleccionado el código postal 15007 y aparece SW-359-MKO.

USO:
    python3 capturar_mk_shipping.py

El navegador se abre con una sesión persistente local.
No se guardan cookies ni Authorization headers en el archivo de captura.
El POST body sí se conserva porque es precisamente lo que queremos estudiar.

SALIDA:
    captura_mk_shipping.json

NOTA:
    Si el body contiene tokens embebidos por la propia aplicación, revísalos
    antes de compartir el archivo.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from datetime import datetime

from playwright.async_api import async_playwright


BASE_DIR = Path(__file__).resolve().parent
OUTPUT = BASE_DIR / "captura_mk_shipping.json"
PROFILE = BASE_DIR / ".mk_capture_profile"

URL = "https://www.makro.plazavea.com.pe/arroz-extra-anejo-faraon-saco-50kg/p"

# Palabras que nos interesan en URL, request body o response.
KEYWORDS = (
    "shippingData",
    "orderForm",
    "simulation",
    "logisticsInfo",
    "shipping",
    "delivery",
)

# Cabeceras que NO debemos persistir.
SECRET_HEADERS = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-vtex-session",
    "x-vtex-io-session",
    "x-vtex-session-token",
}


def redact_headers(headers: dict) -> dict:
    clean = {}

    for key, value in headers.items():
        if key.lower() in SECRET_HEADERS:
            clean[key] = "<REDACTED>"
        else:
            clean[key] = value

    return clean


def relevant(text: str) -> bool:
    low = text.lower()
    return any(k.lower() in low for k in KEYWORDS)


async def main():
    captures = []
    seen_urls = set()

    async with async_playwright() as p:

        # channel=chrome usa Chrome instalado en el sistema.
        context = await p.chromium.launch_persistent_context(
            str(PROFILE),
            channel="chrome",
            headless=False,
            locale="es-PE",
            timezone_id="America/Lima",
            viewport={"width": 1440, "height": 900},
        )

        page = context.pages[0] if context.pages else await context.new_page()

        async def on_request(request):
            url = request.url
            body = request.post_data or ""

            # No guardamos todo el tráfico. Solo candidatos de checkout/logística.
            if not relevant(url + " " + body):
                return

            key = f"{request.method}|{url}|{body}"

            if key in seen_urls:
                return

            seen_urls.add(key)

            captures.append({
                "captured_at": datetime.now().astimezone().isoformat(),
                "type": "request",
                "method": request.method,
                "url": url,
                "resource_type": request.resource_type,
                "headers": redact_headers(await request.all_headers()),
                "post_data": body,
            })

            print("\n[REQUEST RELEVANTE]")
            print(f"{request.method} {url}")

            if body:
                print(f"POST body: {body[:500]}")

        async def on_response(response):
            request = response.request
            url = response.url

            # Primero filtramos por URL.
            if not relevant(url):
                return

            try:
                body = await response.text()
            except Exception as exc:
                body = f"<NO SE PUDO LEER RESPONSE: {exc}>"

            # Solo guardamos respuestas potencialmente útiles.
            if not relevant(url + " " + body):
                return

            captures.append({
                "captured_at": datetime.now().astimezone().isoformat(),
                "type": "response",
                "status": response.status,
                "url": url,
                "headers": redact_headers(await response.all_headers()),
                "response_body": body,
            })

            print("\n[RESPONSE RELEVANTE]")
            print(f"{response.status} {url}")

            # Detectar inmediatamente el nodo que nos interesa.
            if "SW-359-MKO" in body:
                print("\n>>> ENCONTRADO SW-359-MKO <<<")
                print("Esta response es especialmente importante.")

        page.on("request", on_request)
        page.on("response", on_response)

        print("=" * 90)
        print("CAPTURADOR MK / SHIPPING DATA")
        print("=" * 90)
        print()
        print("1. El navegador se abrirá con una sesión persistente.")
        print("2. Configura manualmente la dirección:")
        print()
        print("      Dirección: Santa Anita, Santa Anita")
        print("      Número:    15007")
        print("      Distrito:  Santa Anita")
        print()
        print("3. Pulsa 'Enviar a esta dirección'.")
        print("4. Espera hasta que la página termine de actualizar.")
        print()
        print("5. El script buscará requests relacionadas con:")
        print("      orderForm / shippingData / simulation / logisticsInfo")
        print()
        print("6. Cuando aparezca SW-359-MKO, vuelve a la terminal.")
        print()
        print("Pulsa ENTER para guardar la captura.")
        print("=" * 90)

        await page.goto(
            URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        await asyncio.to_thread(input)

        # Damos tiempo a que termine cualquier request pendiente.
        await page.wait_for_timeout(3000)

        result = {
            "metadata": {
                "captured_at": datetime.now().astimezone().isoformat(),
                "target": URL,
                "purpose": (
                    "Capturar request/response real de logística "
                    "para validar Makro Santa Anita 359."
                ),
                "postal_code_tested": "15007",
                "expected_warehouse": "SW-359-MKO",
                "expected_dock": "DC-359-MKO",
                "expected_courier": "DD-359-MKO",
                "expected_courier_name": "DD-Santa-Anita-Makro",
            },
            "captures": captures,
        }

        OUTPUT.write_text(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        print()
        print("=" * 90)
        print(f"Capturas guardadas: {len(captures)}")
        print(f"Archivo: {OUTPUT}")
        print("=" * 90)

        await context.close()


if __name__ == "__main__":
    asyncio.run(main())