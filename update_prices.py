#!/usr/bin/env python3
"""Scrape Amazon.es for V16 beacon prices and update prices.json.

Usage:  python3 update_prices.py
        Run via GitHub Actions daily at 6am UTC.

No external dependencies – uses only Python stdlib (urllib, re, json).
"""

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

# ── ASIN mapping (must match BEACONS 'id' in index.html) ──
ASINS = {
    "pf-led-one":        "B0CR9JC98B",
    "iot-v4":            "B0FHBDDBNT",
    "osram-ledguardian": "B0DNFBS3NN",
    "extrastar":         "B0FH6VVHBL",
    "hv16-1":            "B0CVXCXX61",
    "safety-light-pro":  "B0FDBBXHDY",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}


def fetch_html(asin):
    url = f"https://www.amazon.es/dp/{asin}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            # Try UTF-8 first, fall back to latin-1
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return raw.decode("latin-1", errors="ignore")
    except Exception as e:
        print(f"    ERROR HTTP: {e}")
        return None


def scrape_product(asin):
    html = fetch_html(asin)
    if not html:
        return None

    info = {}

    # ── Price (a-price-whole + a-price-fraction) ──
    whole = re.search(r'<span class="a-price-whole">([^<]+)', html)
    if whole:
        frac = re.search(r'<span class="a-price-fraction">([^<]+)', html)
        raw = whole.group(1).replace(",", ".").replace(".", "")
        try:
            # If the raw value still has a dot as thousands separator, handle it
            val = float(raw)
            if frac:
                val += float(frac.group(1)) / 100.0
            info["precio"] = round(val, 2)
        except ValueError:
            pass

    # ── Strikethrough price ──
    # Try several patterns since Amazon varies the markup
    patterns = [
        # "Precio de publicación: <span class="a-text-strike"> 57,94 </span>"
        r'class="a-text-strike">\s*([\d,.]+)',
        # <span class="a-offscreen">57,94</span> near basisprice-value
        r'class="a-price[^"]*basisprice-value[^"]*".*?<span class="a-offscreen">([\d,.]+)',
        # fallback: legacy basisPriceAmount in JSON
        r'"basisPriceAmount[^"]*"[^:]*:["\']?([\d.,]+)',
    ]
    for pattern in patterns:
        basis = re.search(pattern, html, re.DOTALL)
        if basis:
            try:
                val = float(basis.group(1).replace(",", "."))
                # Only set if it's actually higher than the current price (real discount)
                if "precio" in info and val > info["precio"]:
                    info["precioTachado"] = val
                elif "precio" not in info:
                    info["precioTachado"] = val
            except ValueError:
                continue
            break

    # ── Rating (a-icon-alt) ──
    rating = re.search(r'a-icon-alt[^>]*>([0-9,]+) de', html)
    if rating:
        try:
            info["rating"] = float(rating.group(1).replace(",", "."))
        except ValueError:
            pass

    # ── Reviews count ──
    reviews = re.search(r'([0-9,.]+)\s*calificaciones?\s*global', html)
    if reviews:
        try:
            info["reviews"] = int(
                reviews.group(1).replace(".", "").replace(",", "")
            )
        except ValueError:
            pass

    return info if info else None


def main():
    # Work relative to script location
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    print(f"[{datetime.now(timezone.utc).isoformat()}] Scraping Amazon.es ...")

    # Load existing prices.json (or start fresh)
    try:
        with open("prices.json", "r") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {"products": {}}

    if "products" not in data:
        data["products"] = {}

    changed = False

    for pid, asin in ASINS.items():
        print(f"\n  {pid}  ({asin})")
        info = scrape_product(asin)
        if not info:
            print("    -> sin datos (producto no disponible?)")
            continue

        old = data["products"].get(pid, {})
        merged = dict(old)

        # Merge: override old with new, but preserve precioTachado if not found
        for key in ["precio", "rating", "reviews"]:
            if key in info:
                merged[key] = info[key]
        merged["precioTachado"] = info.get("precioTachado", None)

        for key in ["precio", "precioTachado", "rating", "reviews"]:
            new_val = merged.get(key)
            old_val = old.get(key)
            arrow = "→" if new_val != old_val else "="
            print(f"    {key}: {old_val} {arrow} {new_val}")

        if merged != old:
            changed = True

        data["products"][pid] = merged

    if changed:
        data["updated"] = datetime.now(timezone.utc).isoformat()
        data["source"] = "Amazon.es scraping"
        with open("prices.json", "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"\n  OK prices.json actualizado ({data['updated']})")
        print("::notice::prices.json updated")
    else:
        print("\n  Sin cambios – no se modifica prices.json")

    sys.exit(0)


if __name__ == "__main__":
    main()
