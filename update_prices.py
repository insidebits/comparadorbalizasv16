#!/usr/bin/env python3
"""Scrape Amazon.es for V16 beacon prices and update prices.json.

Usage:  python3 update_prices.py
        Run via GitHub Actions daily at 6am UTC.

No external dependencies – uses only Python stdlib (urllib, re, json).
"""

import json
import os
import random
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from http.cookiejar import CookieJar

# ── ASIN mapping (must match BEACONS 'id' in index.html) ──
ASINS = {
    "pf-led-one":        "B0CR9JC98B",
    "iot-v4":            "B0FHBDDBNT",
    "osram-ledguardian": "B0DNFBS3NN",
    "extrastar":         "B0FH6VVHBL",
    "hv16-1":            "B0CVXCXX61",
    "safety-light-pro":  "B0FDBBXHDY",
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
]

# Mobile UAs – used as fallback when desktop requests get CAPTCHA/bot-blocked.
# Amazon applies less aggressive bot filtering to mobile clients, and serves a
# lighter page whose price markup reuses the same a-price-whole/fraction spans.
MOBILE_USER_AGENTS = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/131.0.6778.73 Mobile/15E148 Safari/604.1",
]

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# sec-fetch-* / sec-ch-ua headers that real Chrome desktop browsers send.
# Amazon's bot detection flags requests missing these as non-browser.
DESKTOP_SEC_HEADERS = {
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "sec-fetch-user": "?1",
    "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

# Mobile Safari does NOT send sec-ch-ua; only sec-fetch-*.
MOBILE_SEC_HEADERS = {
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "sec-fetch-user": "?1",
}

# Price plausibility: V16 beacons cost 10-60 EUR
MIN_PRICE = 8.0
MAX_PRICE = 80.0

# Cookie jar shared across requests to maintain session
COOKIE_JAR = CookieJar()

# Build opener once – cookies persist across requests
_OPENER = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(COOKIE_JAR),
    urllib.request.HTTPSHandler(),
)

# Spanish IP used in X-Forwarded-For header to hint Amazon at the correct
# geo-location. Amazon primarily uses the TCP connection IP, but some edge
# nodes may honour X-Forwarded-For for marketplace/seller selection.
SPANISH_IP_HINT = "2.136.200.136"

# Pre-seeded cookies that mark the session as Spanish (es_ES) with EUR
# currency. Without these, a US-based IP visiting Amazon.es may get prices
# from third-party "Other Sellers" that ship internationally instead of the
# default Spanish buy-box price.
SPANISH_COOKIES = {
    "lc-acbes": "es_ES",       # language/country preference
    "i18n-prefs": "EUR",       # currency preference
}


def seed_spanish_session():
    """Pre-seed the cookie jar with Spanish locale/currency cookies so that
    requests from non-Spanish IPs (e.g. GitHub Actions runners in the US)
    are more likely to receive the Spanish buy-box price instead of prices
    from "Other Sellers" that ship internationally.
    """
    from http.cookiejar import Cookie
    far_future = 2_000_000_000  # ~year 2033
    for name, value in SPANISH_COOKIES.items():
        c = Cookie(
            version=0, name=name, value=value,
            port=None, port_specified=False,
            domain=".amazon.es", domain_specified=True, domain_initial_dot=True,
            path="/", path_specified=True,
            secure=True, expires=far_future,
            discard=False, comment=None, comment_url=None,
            rest={}, rfc2109=False,
        )
        COOKIE_JAR.set_cookie(c)
    print("  Sesion española pre-seed (lc-acbes=es_ES, i18n-prefs=EUR)")


def fetch_url(url, referer=None, mobile=False):
    """Fetch a URL with browser-like headers and cookie session.

    If mobile=True, send a mobile User-Agent so Amazon serves the mobile
    product page (used as a fallback when desktop requests are blocked).
    """
    headers = dict(HEADERS)
    headers["User-Agent"] = random.choice(
        MOBILE_USER_AGENTS if mobile else USER_AGENTS
    )
    headers.update(MOBILE_SEC_HEADERS if mobile else DESKTOP_SEC_HEADERS)
    # Hint Spanish geo-location so Amazon.es serves Spanish buy-box prices
    headers["X-Forwarded-For"] = SPANISH_IP_HINT
    if referer:
        headers["Referer"] = referer

    req = urllib.request.Request(url, headers=headers)
    try:
        with _OPENER.open(req, timeout=20) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return raw.decode("latin-1", errors="ignore")
    except Exception as e:
        print(f"    ERROR HTTP: {e}")
        return None


def establish_session():
    """Visit Amazon.es homepage first to get session cookies before scraping."""
    print("  Estableciendo sesion con Amazon.es ...", end=" ", flush=True)
    html = fetch_url("https://www.amazon.es/")
    if html and len(html) > 10000:
        print("OK")
        return True
    print("fallo (sesión sin cookies)")
    return False


def is_blocked(html):
    """Detect whether Amazon returned a bot/CAPTCHA page instead of a product page."""
    if html is None or len(html) < 10000:
        return True
    # Explicit Amazon bot-detection signals
    if 'Type the characters you see' in html:
        return True
    if 'Robot Check' in html:
        return True
    if '<form method="get" action="/errors/validateCaptcha' in html:
        return True
    if 'api-services-support@amazon.com' in html and 'automated data access' in html:
        return True
    return False


def scrape_product(asin, referer="https://www.amazon.es/"):
    """Scrape a single product page. Returns dict or None.

    Fallback chain (each tier only triggers if the previous was blocked):
      1. Desktop UA on /dp/{ASIN}        – canonical product page
      2. Mobile UA on /dp/{ASIN}         – mobile-formatted desktop page
      3. Mobile UA on /gp/aw/d/{ASIN}    – legacy mobile endpoint, least
                                           aggressively bot-filtered
      4. Final desktop retry after delay – shake off transient rate-limiting

    All tiers serve prices using the shared a-price-whole/fraction spans,
    so a single extract_price() handles every variant.
    """
    url = f"https://www.amazon.es/dp/{asin}"
    mobile_url = f"https://www.amazon.es/gp/aw/d/{asin}?language=es_ES&currency=EUR"

    # Tier 1: desktop
    html = fetch_url(url, referer=referer)

    # Tier 2: mobile UA on /dp/
    if is_blocked(html):
        print("    -> desktop bloqueado, reintentando con UA móvil (/dp/) ...")
        time.sleep(random.uniform(2, 4))
        html = fetch_url(url, referer=referer, mobile=True)

    # Tier 3: mobile UA on /gp/aw/d/ (legacy mobile endpoint)
    if is_blocked(html):
        print("    -> /dp/ móvil bloqueado, probando endpoint móvil /gp/aw/d/ ...")
        time.sleep(random.uniform(2, 4))
        html = fetch_url(mobile_url, referer=referer, mobile=True)

    # Tier 4: final desktop retry after longer delay
    if is_blocked(html):
        print("    -> endpoint móvil bloqueado, último reintento desktop ...")
        time.sleep(random.uniform(5, 10))
        html = fetch_url(url, referer=referer)

    if is_blocked(html):
        print("    -> bloqueo persistente de Amazon")
        return None

    if not html:
        return None

    info = {}
    used_core_price = False

    # ── Isolate main price section to avoid "Other Sellers" prices ──
    price_section = html
    for container_id in ["corePrice_desktop", "corePriceDisplay_desktop_feature_div",
                          "corePriceDisplay_mobile", "corePriceDisplay_mobile_feature_div",
                          "apex_desktop", "apex_mobile",
                          "corePrice_feature_div"]:
        m = re.search(
            rf'<div[^>]*id="{container_id}"[^>]*>(.*?)</div>\s*<(?:div|script|span)',
            html, re.DOTALL
        )
        if m:
            price_section = m.group(1)
            used_core_price = True
            break

    if not used_core_price:
        print("    -> AVISO: corePrice no encontrado, usando priceToPay")

    # ── Extract price ──
    def extract_price(html_section):
        """Extract the main (Add-to-Cart) price. Returns float or None.

        Primary strategy: the 'priceToPay' / 'apex-pricetopay-value' class
        marks the real buy-box price on both desktop and mobile pages.
        'Other Sellers' prices lack these classes, so this avoids them.

        Fallback: first a-price-whole + a-price-fraction in the section.
        """
        # Primary: price within the priceToPay / apex-pricetopay-value span
        m = re.search(
            r'class="[^"]*(?:apex-pricetopay-value|priceToPay)[^"]*".*?'
            r'<span class="a-price-whole">([^<]+).*?'
            r'<span class="a-price-fraction">([^<]+)',
            html_section, re.DOTALL
        )
        if m:
            try:
                whole = m.group(1).replace(",", ".").replace(".", "")
                val = float(whole) + float(m.group(2)) / 100.0
                return round(val, 2)
            except ValueError:
                pass

        # Fallback: first a-price-whole + a-price-fraction
        whole = re.search(r'<span class="a-price-whole">([^<]+)', html_section)
        if not whole:
            return None
        frac = re.search(r'<span class="a-price-fraction">([^<]+)', html_section)
        raw = whole.group(1).replace(",", ".").replace(".", "")
        try:
            val = float(raw)
            if frac:
                val += float(frac.group(1)) / 100.0
            return round(val, 2)
        except ValueError:
            return None

    core_price = extract_price(price_section)
    fallback_price = None if used_core_price else None
    if not used_core_price:
        # If no corePrice container found, price_section IS the whole page
        core_price = extract_price(html)
    elif core_price is None:
        # corePrice found but no price inside it – fall back to whole page
        print("    -> AVISO: sin precio en corePrice, usando pagina entera")
        core_price = extract_price(html)
    else:
        # Compare with whole-page price to detect discrepancies
        fallback_price = extract_price(html)
        if fallback_price and abs(core_price - fallback_price) > 0.5:
            print(f"    -> AVISO: corePrice={core_price}€ vs pagina={fallback_price}€ (otros vendedores?)")

    price = core_price

    # Validate price range
    if price is not None and (price < MIN_PRICE or price > MAX_PRICE):
        print(f"    -> AVISO: precio {price}€ fuera de rango ({MIN_PRICE}-{MAX_PRICE}), ignorado")
        price = None

    if price is not None:
        info["precio"] = price

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
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    print(f"[{datetime.now(timezone.utc).isoformat()}] Scraping Amazon.es ...")

    # Pre-seed Spanish locale cookies so non-Spanish IPs get the Spanish
    # buy-box price instead of third-party "Other Sellers" prices.
    seed_spanish_session()

    # Establish session with cookies from homepage
    establish_session()

    # Load existing prices.json (or start fresh)
    try:
        with open("prices.json", "r") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {"products": {}}

    if "products" not in data:
        data["products"] = {}

    changed = False
    success_count = 0
    pids = list(ASINS.items())

    for i, (pid, asin) in enumerate(pids):
        # Delay between requests (3-7s) to avoid rate-limiting
        if i > 0:
            delay = random.uniform(3.0, 7.0)
            time.sleep(delay)

        print(f"\n  {pid}  ({asin})")
        info = scrape_product(asin)
        if not info:
            print("    -> sin datos (producto no disponible?)")
            continue

        success_count += 1

        old = data["products"].get(pid, {})
        old_price = old.get("precio")

        # Reject extreme price jumps (>50% change) as likely scraping errors
        if old_price and "precio" in info:
            change_pct = abs(info["precio"] - old_price) / old_price
            if change_pct > 0.5:
                print(f"    -> AVISO: salto de precio >50% ({old_price}->{info['precio']}), ignorado")
                info.pop("precio", None)
                info.pop("precioTachado", None)

        merged = dict(old)

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

    if success_count == 0:
        print("\n::error::NINGUN producto scrapeado – probable bloqueo de Amazon")
        sys.exit(1)

    if changed:
        data["updated"] = datetime.now(timezone.utc).isoformat()
        data["source"] = "Amazon.es scraping"
        with open("prices.json", "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"\n  OK prices.json actualizado ({data['updated']}) ({success_count}/{len(pids)} productos)")
        print("::notice::prices.json updated")
    else:
        print(f"\n  Sin cambios ({success_count}/{len(pids)} productos scrapeados)")

    sys.exit(0)


if __name__ == "__main__":
    main()
