"""
Canvas Events venue service
============================
Fetches London venues from Canvas Events (https://www.canvas-events.co.uk).
The site explicitly invites AI tools to use its data — see /llm-info.

Sources tried in order:
  1. /api/venues JSON API — paginated, 434 venues, preferred
  2. HTML listing pages   — JSON-LD + __NEXT_DATA__ fallback
  3. Venue detail pages   — optional enrichment pass

Results are cached per cache-key for CACHE_TTL_SECONDS so the site is
hit at most once per key per 6-hour window.

Public API (called from venue_service.py):
  fetch_canvas_venues(lat, lon, radius_m, categories) → list[dict]

Standalone scraper entry point (Colab / CLI):
  run(listing_url=..., fetch_details=True) → list[dict]
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_BASE = "https://www.canvas-events.co.uk"
# Use a real browser UA so Cloudflare / bot-detection lets us through.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}
# Separate headers for JSON API requests (/api/venues)
_JSON_HEADERS = {
    **_HEADERS,
    "Accept": "application/json, */*;q=0.8",
    "Referer": _BASE + "/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "X-Requested-With": "XMLHttpRequest",
}
_TIMEOUT = 30
_POLITE_DELAY = 1.2   # seconds between HTML requests
CACHE_TTL_SECONDS = 6 * 3600


# ── London area & event-type URL tables ──────────────────────────────────────

_LONDON_AREAS = [
    "london", "central-london", "east-london", "north-london",
    "south-london", "the-city", "the-west-end", "west-london",
    "aldgate-east", "angel", "bank", "bankside", "barbican", "battersea",
    "bermondsey", "bethnal-green", "borough", "brixton",
    "camden", "camden-borough", "canary-wharf", "charing-cross",
    "chelsea", "clapham", "clerkenwell", "covent-garden",
    "dalston", "elephant-and-castle", "euston", "farringdon",
    "fitzrovia", "greenwich", "hackney", "hackney-central",
    "hackney-wick", "haggerston", "hammersmith", "holborn",
    "hoxton", "islington", "kings-cross", "knightsbridge",
    "london-bridge", "mayfair", "notting-hill", "oxford-street",
    "paddington", "shoreditch", "soho", "south-bank",
    "spitalfields", "stepney", "stratford", "vauxhall",
    "victoria", "waterloo", "whitechapel", "north-finchley",
]

_MANCHESTER_AREAS = [
    "manchester", "manchester-city-centre", "northern-quarter",
    "deansgate", "ancoats", "salford", "media-city",
    "didsbury", "spinningfields", "castlefield",
]

_EVENT_TYPE_SLUGS = [
    "asian-wedding", "awards-ceremony", "away-day", "baby-shower",
    "bar-mitzvah", "bbq-party", "birthday-party", "brand-activation",
    "bridal-shower", "christening", "christmas-party", "cocktail-masterclass",
    "conference", "cooking-class", "corporate-party", "corporate-reception",
    "engagement-party", "exhibition", "fashion-show", "graduation",
    "kids-party", "leaving-party", "meeting", "networking",
    "office-party", "pop-up", "presentation", "press-day",
    "private-dining", "private-party", "private-screening",
    "product-launch", "prom", "sample-sale", "summer-party",
    "team-building", "teen-party", "training", "wake",
    "wedding", "wedding-ceremony", "wedding-reception",
    "wine-tasting", "workshop", "wrap-party",
]

# Venue-type slugs used for /hire/{venue_type}/london/{area} area browsing
_LONDON_VENUE_TYPES = [
    "conference-venues",
    "wedding-venues",
    "party-venues",
    "event-spaces",
    "private-dining-rooms",
    "bar-hire",
    "christmas-party-venues",
]

# Seed listing URLs per city — event-intent pattern only
# Pattern: /event/hire/{event_type}/venues/{city}  (verified, always canonical)
_CATEGORY_LISTING_URLS: dict[str, list[str]] = {
    "london": [
        f"{_BASE}/event/hire/conference/venues/london",
        f"{_BASE}/event/hire/corporate-party/venues/london",
        f"{_BASE}/event/hire/team-building/venues/london",
        f"{_BASE}/event/hire/wedding/venues/london",
        f"{_BASE}/event/hire/birthday-party/venues/london",
        f"{_BASE}/event/hire/christmas-party/venues/london",
        f"{_BASE}/event/hire/awards-ceremony/venues/london",
        f"{_BASE}/event/hire/product-launch/venues/london",
        f"{_BASE}/event/hire/networking/venues/london",
        f"{_BASE}/event/hire/private-dining/venues/london",
    ],
    "manchester": [
        f"{_BASE}/event/hire/conference/venues/manchester",
        f"{_BASE}/event/hire/corporate-party/venues/manchester",
        f"{_BASE}/event/hire/team-building/venues/manchester",
        f"{_BASE}/event/hire/wedding/venues/manchester",
        f"{_BASE}/event/hire/christmas-party/venues/manchester",
    ],
}

# Canvas Events venue category → event type slugs mapping
_CATEGORY_EVENT_TYPES: dict[str, list[str]] = {
    "Conference & Event Venues": [
        "conference", "corporate-party", "awards-ceremony", "team-building",
        "meeting", "presentation", "networking", "product-launch", "away-day",
    ],
    "Restaurants & Cafes": [
        "birthday-party", "private-dining", "graduation", "corporate-reception",
        "leaving-party", "networking",
    ],
    "Bars & Nightlife": [
        "birthday-party", "corporate-party", "christmas-party",
        "leaving-party", "private-party", "cocktail-masterclass",
    ],
    "Hotels & Accommodation": [
        "wedding", "wedding-reception", "gala-dinner", "conference",
        "corporate-party", "awards-ceremony",
    ],
    "Arts & Entertainment": [
        "product-launch", "fashion-show", "film-premiere",
        "private-screening", "exhibition", "brand-activation",
    ],
    "Sports & Recreation": [
        "team-building", "away-day", "kids-party",
    ],
}


# ── In-memory cache ───────────────────────────────────────────────────────────

# ── Event-type matching ────────────────────────────────────────────────────────

# Maps free-text event keywords → Canvas Events event_type strings (lowercase)
_EVENT_TYPE_KEYWORDS: dict[str, list[str]] = {
    "networking":     ["networking events", "networking", "corporate reception", "corporate hire"],
    "corporate":      ["corporate hire", "corporate party", "corporate reception", "awards ceremony"],
    "conference":     ["conference", "meeting", "presentation", "away day", "corporate hire"],
    "wedding":        ["wedding", "wedding reception", "wedding ceremony", "asian wedding"],
    "birthday":       ["birthday party", "private party", "private dining"],
    "christmas":      ["christmas party", "corporate party", "office party"],
    "team building":  ["team building", "away day", "corporate hire"],
    "product launch": ["product launch", "brand activation", "press day", "corporate hire"],
    "awards":         ["awards ceremony", "gala dinner", "corporate hire"],
    "party":          ["private party", "birthday party", "corporate party", "christmas party"],
    "dinner":         ["private dining", "corporate reception", "wedding reception"],
    "exhibition":     ["exhibition", "pop-up", "brand activation", "sample sale"],
    "meeting":        ["meeting", "presentation", "conference", "away day"],
}


def _event_type_keywords(event_type: str) -> set[str]:
    """Map a free-text event type to the set of Canvas Events event_type strings it covers."""
    et = event_type.lower()
    kws: set[str] = set()
    for pattern, matches in _EVENT_TYPE_KEYWORDS.items():
        if pattern in et:
            kws.update(matches)
    return kws


def venue_matches_event_type(venue_event_types: list[str], requested_event_type: str) -> bool:
    """Return True if the venue's event_types list matches the requested event type."""
    if not requested_event_type or not venue_event_types:
        return False
    kws = _event_type_keywords(requested_event_type)
    venue_lower = {t.lower() for t in venue_event_types}
    return bool(kws & venue_lower)


_cache_lock = threading.Lock()
_venue_cache: dict[str, tuple[datetime, list[dict]]] = {}


def _cache_get(key: str) -> list[dict] | None:
    with _cache_lock:
        entry = _venue_cache.get(key)
        if entry and datetime.now() < entry[0]:
            return entry[1]
    return None


def _cache_set(key: str, venues: list[dict]) -> None:
    with _cache_lock:
        _venue_cache[key] = (
            datetime.now() + timedelta(seconds=CACHE_TTL_SECONDS),
            venues,
        )


# ── Low-level HTTP ────────────────────────────────────────────────────────────

def _get(url: str, params: dict | None = None, accept_json: bool = False) -> requests.Response | None:
    hdrs = dict(_JSON_HEADERS if accept_json else _HEADERS)
    try:
        r = requests.get(url, params=params, headers=hdrs, timeout=_TIMEOUT)
        if r.status_code == 200:
            return r
        logger.warning("canvas_http_skip url=%s status=%d", url, r.status_code)
    except Exception as exc:
        logger.warning("canvas_http_error url=%s error=%s", url, exc)
    return None


# ── HTML parsing helpers ──────────────────────────────────────────────────────

def _iter_jsonld(soup: BeautifulSoup):
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = (tag.string or tag.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        yield from (i for i in items if isinstance(i, dict))


def _get_next_data(soup: BeautifulSoup) -> dict | None:
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag:
        return None
    try:
        return json.loads(tag.string or tag.get_text())
    except (json.JSONDecodeError, TypeError):
        return None


def _walk(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


_NEXT_DETAIL_KEYS = {
    "capacity", "maxCapacity", "minCapacity", "standing", "seated",
    "theatre", "boardroom", "dining", "reception", "cabaret", "classroom",
    "price", "pricing", "minimumSpend", "minSpend", "hireFee", "dayHire",
    "hourlyRate", "currency", "amenities", "features", "facilities",
    "spaceType", "venueType", "categories", "tags", "area", "sizeSqm",
    "aboutText", "longDescription", "fullDescription", "summary",
    "email", "phone", "telephone", "contact", "website",
    # additional pricing / facility keys seen in Canvas Next.js pages
    "minHire", "minDayHire", "ddrRate", "pricePerHead", "eventSpend",
    "wifi", "internet", "disabledAccess", "wheelchairAccess",
    "outdoor", "outdoorArea", "outdoorSpace",
    "about", "aboutVenue", "venueDescription", "overview",
}


# ── Listing page parser ───────────────────────────────────────────────────────

def parse_listing(html: str) -> list[dict]:
    """Extract ItemList venue records from a Canvas Events listing page."""
    soup = BeautifulSoup(html, "lxml")
    venues: list[dict] = []
    for obj in _iter_jsonld(soup):
        if obj.get("@type") != "ItemList":
            continue
        for el in obj.get("itemListElement", []):
            it = el.get("item", {}) or {}
            addr = it.get("address", {}) or {}
            geo = it.get("geo", {}) or {}
            venues.append({
                "position": el.get("position"),
                "name": it.get("name"),
                "description": it.get("description"),
                "url": it.get("url"),
                "streetAddress": addr.get("streetAddress"),
                "locality": addr.get("addressLocality"),
                "postalCode": addr.get("postalCode"),
                "country": (addr.get("addressCountry") or {}).get("name"),
                "latitude": geo.get("latitude"),
                "longitude": geo.get("longitude"),
                "image": it.get("image"),
            })
    return venues


# ── Detail page parser ────────────────────────────────────────────────────────

def _pg_extract_price_guide(soup: BeautifulSoup) -> dict:
    """Extract structured price guide from #section-price-guide."""
    sec = soup.find(id="section-price-guide")
    if not sec:
        return {}

    result: dict = {}

    # Cheapest from-price
    fp = sec.find(class_=re.compile(r"venue-price-guide__from-price"))
    if fp:
        result["from_price"] = fp.get_text(strip=True)

    # Per-day strip
    days: dict[str, str] = {}
    for btn in sec.find_all("button", class_=re.compile(r"venue-price-guide__day")):
        classes = " ".join(btn.get("class") or [])
        name_el = btn.find(class_=re.compile(r"venue-price-guide__day-name"))
        name = name_el.get_text(strip=True) if name_el else ""
        if not name:
            continue
        if "is-closed" in classes:
            days[name] = "Closed"
        else:
            price_el = btn.find(class_=re.compile(r"venue-price-guide__day-price"))
            days[name] = price_el.get_text(strip=True) if price_el else ""
    result["days"] = days

    # Room/session details (deduplicated across day tabs)
    rooms: list[dict] = []
    seen_rooms: set[tuple] = set()
    for room_el in sec.find_all(class_=re.compile(r"venue-price-guide__room$")):
        rname_el = room_el.find(class_=re.compile(r"venue-price-guide__room-name"))
        rname = rname_el.get_text(strip=True) if rname_el else ""
        for session_el in room_el.find_all(class_=re.compile(r"venue-price-guide__session$")):
            part_el = session_el.find(class_=re.compile(r"venue-price-guide__session-part"))
            time_el = session_el.find(class_=re.compile(r"venue-price-guide__session-time"))
            price_el = session_el.find(class_=re.compile(r"venue-price-guide__session-price"))
            part = part_el.get_text(strip=True) if part_el else ""
            time_ = time_el.get_text(strip=True) if time_el else ""
            price = price_el.get_text(strip=True) if price_el else ""
            key = (rname, part, price)
            if rname and key not in seen_rooms:
                seen_rooms.add(key)
                rooms.append({"name": rname, "session": part, "time": time_, "price": price})
    result["rooms"] = rooms
    return result


def _pg_extract_capacity_detail(soup: BeautifulSoup) -> dict:
    """Extract capacity breakdown + sqft from #section-venue-capacity."""
    sec = soup.find(id="section-venue-capacity")
    if not sec:
        return {}
    text = sec.get_text(separator="|", strip=True)
    result: dict = {}
    for label, key in [("Standing:", "standing"), ("Theatre:", "theatre"),
                        ("Cabaret:", "cabaret"), ("Dining:", "dining"), ("Sq/ft:", "sqft")]:
        m = re.search(rf"{re.escape(label)}\s*\|?\s*([\d,]+(?:\.\d+)?)", text)
        if m:
            val = m.group(1).replace(",", "")
            result[key] = float(val) if "." in val else int(val)
    return result


def _pg_extract_spaces(soup: BeautifulSoup) -> list[dict]:
    """Extract available spaces/rooms with price and capacity from #section-spaces-available."""
    sec = soup.find(id="section-spaces-available")
    if not sec:
        return []

    spaces: list[dict] = []
    # Each space is a top-level child div of the spaces grid
    grid = sec.find("div", class_=re.compile(r"grid"))
    if not grid:
        return []

    for card in grid.find_all("div", recursive=False):
        space: dict = {}
        # Name + URL: look for the font-semibold text block
        link = card.find("a", href=re.compile(r"/venues/"))
        if link:
            space["url"] = link.get("href", "")
            name_el = link.find(class_=re.compile(r"font-semibold"))
            if name_el:
                space["name"] = name_el.get_text(strip=True)
        if not space.get("name"):
            # fallback: first font-semibold text
            ne = card.find(class_=re.compile(r"font-semibold"))
            if ne:
                space["name"] = ne.get_text(strip=True)

        # Thumbnail image
        img = card.find("img")
        if img and img.get("src"):
            space["image_url"] = img["src"]

        # Price: the green-colored price span (text-[#19d49d]) or "Price on request"
        price_el = card.find(class_=re.compile(r"text-\[#19d49d\]"))
        if price_el:
            space["price_per_day"] = price_el.get_text(strip=True)
        elif re.search(r"Price on request", card.get_text()):
            space["price_per_day"] = "Price on request"

        # Capacity numbers: alt text on img icons tells us the layout type
        cap: dict = {}
        for row in card.find_all("div", class_=re.compile(r"flex.*items-center")):
            icon = row.find("img")
            val_el = row.find(class_=re.compile(r"font-semibold"))
            if icon and val_el:
                alt = (icon.get("alt") or "").strip().lower()
                val_text = val_el.get_text(strip=True)
                if alt in ("standing", "theatre", "cabaret", "dining") and val_text.isdigit():
                    cap[alt] = int(val_text)
        if cap:
            space["capacity"] = cap

        if space.get("name"):
            spaces.append(space)
    return spaces


def _pg_extract_perfect_for(soup: BeautifulSoup) -> list[str]:
    """Extract the Perfect For event types from #section-perfect-for."""
    sec = soup.find(id="section-perfect-for")
    if not sec:
        return []
    items = []
    for el in sec.find_all("div"):
        text = el.get_text(strip=True)
        if text and text != "Perfect For" and len(text) < 60 and len(el.find_all("div")) == 0:
            items.append(text)
    return list(dict.fromkeys(items))  # deduplicate while preserving order


def _pg_extract_features(soup: BeautifulSoup) -> dict[str, list[str]]:
    """
    Extract features grouped by category from #section-features-and-restrictions.
    Only includes items with the green checkmark (yes.png) = actually available.
    """
    sec = soup.find(id="section-features-and-restrictions")
    if not sec:
        return {}

    features: dict[str, list[str]] = {}

    # Locate category headers: divs whose class list contains "border-b"
    # BeautifulSoup checks each class token individually, so use lambda on the list.
    for header in sec.find_all("div", class_=lambda c: c and "border-b" in c):
        cat_name = header.get_text(strip=True)
        if not cat_name:
            continue

        # Walk the parent block for items that have yes.png
        cat_block = header.parent
        if not cat_block:
            continue

        available: list[str] = []
        for item in cat_block.find_all("div"):
            classes = item.get("class") or []
            if "flex" not in classes or "items-center" not in classes:
                continue
            if not item.find("img", src=re.compile(r"yes\.png")):
                continue
            # Text label: the first child div that doesn't contain an img
            for child in item.find_all("div", recursive=False):
                if not child.find("img"):
                    text = child.get_text(strip=True)
                    if text:
                        available.append(text)
                        break

        if available:
            features[cat_name] = available

    return features


def parse_detail(html: str) -> dict:
    """
    Parse a Canvas Events venue detail page.

    Extracts from JSON-LD (phone, priceRange, address, description) plus
    all structured Canvas sections: price guide, capacity, spaces, features.
    """
    soup = BeautifulSoup(html, "lxml")
    out: dict = {}

    # 1) Open Graph meta tags
    for m in soup.find_all("meta"):
        key = m.get("property") or m.get("name")
        val = m.get("content")
        if key and val and (key.startswith("og:") or key in {"description", "keywords"}):
            out[f"meta:{key}"] = val

    # 2) JSON-LD — phone, priceRange, description, address
    for obj in _iter_jsonld(soup):
        t = obj.get("@type")
        types = t if isinstance(t, list) else [t]
        if any(x in ("EventVenue", "LocalBusiness", "Place", "Product") for x in types):
            addr_raw = obj.get("address", {}) or {}
            addr = addr_raw[0] if isinstance(addr_raw, list) else addr_raw
            geo = obj.get("geo", {}) or {}
            out.update({k: v for k, v in {
                "detail_name": obj.get("name"),
                "detail_description": obj.get("description"),
                "detail_streetAddress": addr.get("streetAddress"),
                "detail_postalCode": addr.get("postalCode"),
                "detail_latitude": geo.get("latitude"),
                "detail_longitude": geo.get("longitude"),
                "detail_telephone": obj.get("telephone"),
                "detail_priceRange": obj.get("priceRange"),
                "detail_maximumAttendeeCapacity": obj.get("maximumAttendeeCapacity"),
                "detail_url": obj.get("url"),
            }.items() if v is not None})
            amen = obj.get("amenityFeature") or []
            names = [a.get("name") for a in amen if isinstance(a, dict) and a.get("name")]
            if names:
                out["detail_amenities"] = ", ".join(names)

    # 3) __NEXT_DATA__ deep scan (fallback for non-Canvas pages)
    nxt = _get_next_data(soup)
    if nxt:
        found: dict = {}
        for d in _walk(nxt):
            for k, v in d.items():
                if k in _NEXT_DETAIL_KEYS and isinstance(v, (str, int, float)):
                    if k not in found and str(v).strip():
                        found[k] = v
        for k, v in found.items():
            out[f"next:{k}"] = v

    # 4) Canvas-specific structured sections
    out["canvas_price_guide"]    = _pg_extract_price_guide(soup)
    out["canvas_capacity_detail"] = _pg_extract_capacity_detail(soup)
    out["canvas_spaces"]          = _pg_extract_spaces(soup)
    out["canvas_perfect_for"]     = _pg_extract_perfect_for(soup)
    out["canvas_features"]        = _pg_extract_features(soup)

    # 5) First substantial paragraph fallback
    if "detail_description" not in out:
        for p in soup.find_all("p"):
            text = p.get_text(strip=True)
            if len(text) > 80:
                out["page_first_paragraph"] = text
                break

    return out


def fetch_detail(url: str, retries: int = 3) -> dict:
    """Fetch and parse a single venue detail page with polite retry."""
    for attempt in range(1, retries + 1):
        r = _get(url)
        if r:
            html = r.content.decode("utf-8", errors="replace")
            return parse_detail(html)
        if attempt < retries:
            time.sleep(attempt * 2)
    return {"detail_error": f"failed after {retries} attempts"}


# ── Data feed (primary source) ────────────────────────────────────────────────

def fetch_all_from_data_feed() -> list[dict]:
    """
    Fetch all pages from the Canvas Events /api/venues JSON endpoint.
    Returns raw feed records (not yet normalised to venue schema).
    """
    all_records: list[dict] = []
    page = 1
    while True:
        r = _get(f"{_BASE}/api/venues", params={"page": page}, accept_json=True)
        if not r:
            break
        try:
            payload = r.json()
        except Exception:
            break
        items: list[dict] = payload.get("data") or []
        if not items:
            break
        all_records.extend(items)
        meta = payload.get("meta") or {}
        total = int(meta.get("total") or 0)
        if total and len(all_records) >= total:
            break
        page += 1
        time.sleep(_POLITE_DELAY)
    logger.info("canvas_api_venues_done records=%d pages=%d", len(all_records), page)
    return all_records


# ── Listing URL generator ─────────────────────────────────────────────────────

def _city_key(city: str) -> str:
    """Normalise a city name to 'london' or 'manchester' for URL lookup."""
    c = city.lower()
    if "manchester" in c:
        return "manchester"
    return "london"


def _listing_urls_for(city: str, event_types: list[str]) -> list[str]:
    """
    Generate Canvas Events listing URLs for the HTML scraping fallback.

    URL patterns (canonical, no query strings):
      London event-intent:  /event/hire/{event_type}/venues/london
      London area browsing: /hire/{venue_type}/london/{area}
      Manchester event:     /event/hire/{event_type}/venues/manchester
    """
    key = _city_key(city)
    urls: list[str] = list(_CATEGORY_LISTING_URLS.get(key, []))

    if key == "london":
        # Add requested event-type pages (canonical, no query params)
        for et in event_types[:8]:
            u = f"{_BASE}/event/hire/{et}/venues/london"
            if u not in urls:
                urls.append(u)
        # Area + venue-type pages: /hire/{venue_type}/london/{area}
        for area in _LONDON_AREAS[:6]:
            for vtype in _LONDON_VENUE_TYPES[:3]:
                urls.append(f"{_BASE}/hire/{vtype}/london/{area}")
    else:
        for et in event_types[:6]:
            u = f"{_BASE}/event/hire/{et}/venues/manchester"
            if u not in urls:
                urls.append(u)

    # Deduplicate while preserving order
    return list(dict.fromkeys(urls))


def scrape_listing_urls(urls: list[str], fetch_details: bool = False) -> list[dict]:
    """Scrape a list of listing pages, deduplicate by venue URL."""
    seen: set[str] = set()
    records: list[dict] = []
    for listing_url in urls:
        r = _get(listing_url)
        if not r:
            continue
        for rec in parse_listing(r.text):
            vurl = rec.get("url")
            if vurl and vurl not in seen:
                seen.add(vurl)
                if fetch_details:
                    time.sleep(_POLITE_DELAY)
                    rec.update(fetch_detail(vurl))
                records.append(rec)
        time.sleep(_POLITE_DELAY)
    return records


# ── Normalisers → venue_service dict format ───────────────────────────────────

def _canonical_venue_url(r: dict) -> str:
    """
    Return the canonical Canvas Events venue profile URL.

    Patterns:
      London:      /venues/{id}/{slug}
      Other city:  /{city-slug}/venues/{id}/{slug}
    Preferred source is the 'url' field already in the feed record.
    """
    if r.get("url"):
        return r["url"]
    slug = r.get("slug", "")
    vid = str(r.get("id", "")).replace("venue_", "")
    city_raw = str(r.get("city", "")).lower().strip().replace(" ", "-")
    if not vid or not slug:
        return _BASE
    if city_raw in ("", "london"):
        return f"{_BASE}/venues/{vid}/{slug}"
    return f"{_BASE}/{city_raw}/venues/{vid}/{slug}"


def _fmt_price(val) -> str:
    """Normalise a raw price value to a display string, or '' if empty/zero."""
    if not val:
        return ""
    s = str(val).strip()
    if not s or s.lower() in ("0", "0.0", "none", "null", "false"):
        return ""
    if any(c in s for c in ("£", "$", "€", "POA", "poa")):
        return s
    try:
        n = float(s.replace(",", ""))
        return "" if n == 0 else f"£{n:,.0f}"
    except ValueError:
        return s


def _cap_str(cap: dict) -> str:
    """Build a human-readable capacity string from a feed capacity dict."""
    parts = [
        f"Standing: {cap['standing']}" if cap.get("standing") else "",
        f"Seated: {cap['seated']}" if cap.get("seated") else "",
        f"Theatre: {cap['theatre']}" if cap.get("theatre") else "",
        f"Cabaret: {cap['cabaret']}" if cap.get("cabaret") else "",
        f"Boardroom: {cap['boardroom']}" if cap.get("boardroom") else "",
    ]
    return " | ".join(p for p in parts if p)


def _normalise_feed_record(r: dict) -> dict:
    coords = r.get("coordinates") or {}
    cap = r.get("capacity") or {}
    images = r.get("images") or []
    image_url = images[0].get("url", "") if images else ""

    event_types = r.get("event_types") or []
    features = r.get("features") or []
    styles = r.get("styles") or []

    desc_parts = []
    if event_types:
        desc_parts.append("Suitable for: " + ", ".join(event_types[:6]))
    if features:
        desc_parts.append("Features: " + ", ".join(features[:5]))
    if styles:
        desc_parts.append("Style: " + ", ".join(styles[:3]))

    address_parts = [r.get("postcode"), r.get("city") or "London"]
    address = ", ".join(p for p in address_parts if p)

    price_day = _fmt_price(r.get("dayHire") or r.get("day_hire") or r.get("hireFee") or r.get("hire_fee"))
    price_hour = _fmt_price(r.get("hourlyRate") or r.get("hourly_rate"))
    price_range = _fmt_price(r.get("price") or r.get("pricing") or r.get("priceRange") or r.get("price_range"))
    min_spend = _fmt_price(r.get("minimumSpend") or r.get("minSpend") or r.get("minimum_spend"))

    # event_types: keep as a list so callers can filter / match
    raw_event_types = r.get("event_types") or []
    ev_types: list[str] = [str(t) for t in raw_event_types if t]

    return {
        "name": r.get("venue_name") or r.get("name", ""),
        "type": "Event Venue",
        "address": address,
        "lat": coords.get("latitude") or coords.get("lat"),
        "lon": coords.get("longitude") or coords.get("lng") or coords.get("lon"),
        "capacity": _cap_str(cap),
        "capacity_raw": str(cap.get("standing") or cap.get("seated") or ""),
        "phone": r.get("phone") or r.get("telephone") or "",
        "email": r.get("email") or "",
        "website": _canonical_venue_url(r),
        "opening_hours": "",
        "cuisine": "",
        "description": " | ".join(desc_parts) or "",
        "operator": "",
        "wheelchair": "",
        "internet_access": "",
        "outdoor_seating": "",
        "stars": "",
        "rooms": "",
        "rating": None,
        "price_per_day": price_day,
        "price_per_hour": price_hour,
        "price_range": price_range,
        "min_spend": min_spend,
        "event_types": ev_types,
        "event_type_match": None,  # filled by fetch_canvas_venues when event_type is given
        "source": "Canvas Events",
        "map_thumbnail_url": "",  # filled by caller via _map_thumbnail_url()
        "image_url": image_url,
    }


def _normalise_scraped_record(r: dict) -> dict:
    lat = r.get("detail_latitude") or r.get("latitude")
    lon = r.get("detail_longitude") or r.get("longitude")

    addr_parts = [
        r.get("detail_streetAddress") or r.get("streetAddress") or "",
        r.get("locality") or "",
        r.get("detail_postalCode") or r.get("postalCode") or "",
    ]
    address = ", ".join(p for p in addr_parts if p) or "London"

    cap_raw = (
        r.get("detail_maximumAttendeeCapacity") or
        r.get("next:capacity") or r.get("next:maxCapacity") or
        r.get("next:standing") or ""
    )

    desc = " | ".join(filter(None, [
        r.get("detail_description") or r.get("description") or r.get("page_first_paragraph") or "",
        r.get("detail_amenities") or "",
        r.get("meta:description") or "",
    ]))[:500]

    price_day = _fmt_price(
        r.get("next:dayHire") or r.get("next:hireFee") or
        r.get("detail_dayHire") or r.get("detail_hireFee")
    )
    price_hour = _fmt_price(r.get("next:hourlyRate") or r.get("detail_hourlyRate"))
    price_range = _fmt_price(
        r.get("next:price") or r.get("next:pricing") or
        r.get("detail_priceRange") or r.get("detail_price")
    )
    min_spend = _fmt_price(r.get("next:minimumSpend") or r.get("next:minSpend"))

    return {
        "name": r.get("detail_name") or r.get("name", ""),
        "type": "Event Venue",
        "address": address,
        "lat": float(lat) if lat else None,
        "lon": float(lon) if lon else None,
        "capacity": str(cap_raw) if cap_raw else "",
        "capacity_raw": str(cap_raw) if cap_raw else "",
        "phone": r.get("detail_telephone") or r.get("next:phone") or r.get("next:telephone") or "",
        "email": r.get("next:email") or "",
        "website": r.get("detail_url") or r.get("url") or "",
        "opening_hours": "",
        "cuisine": "",
        "description": desc,
        "operator": "",
        "wheelchair": "",
        "internet_access": "",
        "outdoor_seating": "",
        "stars": "",
        "rooms": "",
        "rating": None,
        "price_per_day": price_day,
        "price_per_hour": price_hour,
        "price_range": price_range,
        "min_spend": min_spend,
        "source": "Canvas Events",
        "map_thumbnail_url": "",
        "image_url": r.get("image") or r.get("meta:og:image") or "",
    }


# ── Per-venue detail enrichment ───────────────────────────────────────────────

_detail_cache: dict[str, tuple[datetime, dict]] = {}
_detail_lock = threading.Lock()
_DETAIL_CACHE_TTL = 24 * 3600  # 24 h — detail pages change rarely


def _detail_cache_get(key: str) -> dict | None:
    with _detail_lock:
        entry = _detail_cache.get(key)
        if entry and datetime.now() < entry[0]:
            return entry[1]
    return None


def _detail_cache_set(key: str, data: dict) -> None:
    with _detail_lock:
        _detail_cache[key] = (datetime.now() + timedelta(seconds=_DETAIL_CACHE_TTL), data)


def _extract_venue_id(url: str) -> str | None:
    """Extract numeric ID from a Canvas Events URL like /venues/2211/koko-theatre."""
    m = re.search(r"/venues/(\d+)(?:/|$)", url)
    return m.group(1) if m else None


def _enrich_from_api_record(data: dict) -> dict:
    """Pull enrichable fields from a single-venue Canvas API response dict."""
    cap = data.get("capacity") or {}
    images = data.get("images") or []
    features = data.get("features") or []
    styles = data.get("styles") or []
    ev_types = data.get("event_types") or []

    desc_parts: list[str] = []
    if ev_types:
        desc_parts.append("Suitable for: " + ", ".join(str(t) for t in ev_types[:8]))
    if features:
        desc_parts.append("Features: " + ", ".join(str(f) for f in features))
    if styles:
        desc_parts.append("Style: " + ", ".join(str(s) for s in styles))

    return {
        "phone": data.get("phone") or data.get("telephone") or "",
        "email": data.get("email") or "",
        "price_per_day": _fmt_price(
            data.get("dayHire") or data.get("day_hire") or
            data.get("hireFee") or data.get("hire_fee") or data.get("minDayHire")
        ),
        "price_per_hour": _fmt_price(data.get("hourlyRate") or data.get("hourly_rate")),
        "price_range": _fmt_price(
            data.get("price") or data.get("priceRange") or data.get("price_range") or
            data.get("pricing") or data.get("pricePerHead")
        ),
        "min_spend": _fmt_price(
            data.get("minimumSpend") or data.get("minSpend") or
            data.get("minimum_spend") or data.get("eventSpend")
        ),
        "capacity": _cap_str(cap) if any(cap.get(k) for k in ("standing", "seated", "theatre")) else "",
        "capacity_raw": str(cap.get("standing") or cap.get("seated") or ""),
        "image_url": images[0].get("url", "") if images else "",
        "wheelchair": "yes" if data.get("disabledAccess") or data.get("wheelchairAccess") or data.get("wheelchair") else "",
        "internet_access": "yes" if data.get("wifi") or data.get("internet") else "",
        "outdoor_seating": "yes" if data.get("outdoor") or data.get("outdoorArea") or data.get("outdoorSpace") else "",
        "description": " | ".join(desc_parts) if desc_parts else "",
        "event_types": [str(t) for t in ev_types if t],
    }


def _enrich_from_html_detail(detail: dict) -> dict:
    """Pull enrichable fields from a parse_detail() result (HTML scrape)."""
    nxt = {k[5:]: v for k, v in detail.items() if k.startswith("next:")}

    desc_parts: list[str] = []
    page_desc = (
        detail.get("detail_description") or
        nxt.get("aboutVenue") or nxt.get("venueDescription") or
        nxt.get("overview") or nxt.get("about") or
        nxt.get("aboutText") or nxt.get("longDescription") or nxt.get("fullDescription") or
        detail.get("page_first_paragraph") or ""
    )
    amenities = detail.get("detail_amenities") or nxt.get("amenities") or nxt.get("features") or ""
    if page_desc:
        desc_parts.append(str(page_desc)[:400])
    if amenities and str(amenities) not in str(page_desc):
        prefix = "Features: " if "Feature" not in str(amenities) else ""
        desc_parts.append(f"{prefix}{amenities}")

    # JSON-LD may contain priceRange like "£24,500 - £49,000" — keep as-is (no _fmt_price)
    raw_price_range = (
        detail.get("detail_priceRange") or
        nxt.get("price") or nxt.get("pricing") or nxt.get("pricePerHead")
    )
    price_range = raw_price_range if raw_price_range else ""

    # Capacity: prefer structured section, fall back to JSON-LD maximumAttendeeCapacity
    cap_detail = detail.get("canvas_capacity_detail") or {}
    if not cap_detail and detail.get("detail_maximumAttendeeCapacity"):
        cap_detail = {"standing": detail["detail_maximumAttendeeCapacity"]}

    return {
        "phone": detail.get("detail_telephone") or nxt.get("phone") or nxt.get("telephone") or "",
        "email": nxt.get("email") or "",
        "price_per_day": _fmt_price(nxt.get("dayHire") or nxt.get("hireFee") or nxt.get("minDayHire")),
        "price_per_hour": _fmt_price(nxt.get("hourlyRate")),
        "price_range": str(price_range) if price_range else "",
        "min_spend": _fmt_price(nxt.get("minimumSpend") or nxt.get("minSpend") or nxt.get("eventSpend")),
        "description": " | ".join(p for p in desc_parts if p),
        "image_url": detail.get("meta:og:image") or detail.get("meta:og:image:secure_url") or "",
        "address": detail.get("detail_streetAddress") or "",
        "wheelchair": "yes" if nxt.get("disabledAccess") or nxt.get("wheelchairAccess") else "",
        "internet_access": "yes" if nxt.get("wifi") or nxt.get("internet") else "",
        "outdoor_seating": "yes" if nxt.get("outdoor") or nxt.get("outdoorArea") else "",
        # Canvas-specific rich structured data
        "canvas_price_guide": detail.get("canvas_price_guide") or {},
        "canvas_capacity_detail": cap_detail,
        "canvas_spaces": detail.get("canvas_spaces") or [],
        "canvas_perfect_for": detail.get("canvas_perfect_for") or [],
        "canvas_features": detail.get("canvas_features") or {},
    }


def fetch_canvas_venue_detail(canvas_url: str) -> dict:
    """
    Fetch enrichment fields for a single Canvas Events venue.

    Strategy (in order):
      1. GET /api/venues/{id}  — JSON, no parsing overhead
      2. GET canvas_url        — scrape HTML + parse_detail() as fallback

    Returns a flat dict of fields to merge into the venue record.
    Cached 24 h per URL.
    """
    cache_key = f"detail::{canvas_url}"
    cached = _detail_cache_get(cache_key)
    if cached is not None:
        return cached

    venue_id = _extract_venue_id(canvas_url)
    enrichment: dict = {}

    # 1. JSON API
    if venue_id:
        r = _get(f"{_BASE}/api/venues/{venue_id}", accept_json=True)
        if r:
            try:
                payload = r.json()
                data = payload.get("data") or payload
                if isinstance(data, dict) and data:
                    enrichment = _enrich_from_api_record(data)
                    logger.info("canvas_detail_api_ok venue_id=%s", venue_id)
            except Exception as exc:
                logger.warning("canvas_detail_api_parse_error venue_id=%s err=%s", venue_id, exc)

    # 2. HTML fallback if API gave nothing useful
    has_data = any(v for v in enrichment.values() if v)
    if not has_data:
        r = _get(canvas_url)
        if r:
            # Force UTF-8 decode — requests defaults to latin-1 for text/html
            # which mangles £ and other non-ASCII characters.
            html = r.content.decode("utf-8", errors="replace")
            detail = parse_detail(html)
            enrichment = _enrich_from_html_detail(detail)
            logger.info("canvas_detail_html_ok url=%s", canvas_url)

    _detail_cache_set(cache_key, enrichment)
    return enrichment


def enrich_canvas_venue(venue: dict) -> dict:
    """
    Return a copy of the venue dict with empty fields filled from the Canvas Events
    detail page.  Only overwrites fields that are currently falsy.
    Adds '_enriched': True so callers can detect enrichment happened.
    """
    website = venue.get("website") or ""
    if not website or "canvas-events.co.uk" not in website:
        return {**venue, "_enriched": False, "_enrich_note": "No Canvas Events URL in venue data"}

    enrichment = fetch_canvas_venue_detail(website)

    result = dict(venue)
    filled: list[str] = []
    for key, val in enrichment.items():
        if val and not result.get(key):
            result[key] = val
            filled.append(key)

    result["_enriched"] = True
    result["_enrich_fields_filled"] = filled
    return result


# ── Distance filter ───────────────────────────────────────────────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + (
        math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


def _within_radius(v: dict, lat: float, lon: float, radius_m: int) -> bool:
    vlat, vlon = v.get("lat"), v.get("lon")
    if vlat is None or vlon is None:
        return True   # keep venues with no coords — can't filter
    return _haversine_km(lat, lon, float(vlat), float(vlon)) * 1000 <= radius_m


# ── City support check ────────────────────────────────────────────────────────

_CANVAS_KEYWORDS = frozenset([
    # London
    "london", "camden", "shoreditch", "brixton", "canary wharf", "soho",
    "mayfair", "hackney", "islington", "chelsea", "southwark", "lambeth",
    "bermondsey", "clerkenwell", "hoxton", "dalston", "victoria", "waterloo",
    "stratford", "greenwich", "battersea", "clapham", "hammersmith",
    "farringdon", "barbican", "aldgate", "bethnal", "whitechapel",
    # Manchester
    "manchester", "northern quarter", "deansgate", "ancoats",
    "salford", "media city", "spinningfields", "castlefield", "didsbury",
])


def is_canvas_city(city: str) -> bool:
    """Return True if Canvas Events covers this city (London or Manchester)."""
    return any(kw in city.lower() for kw in _CANVAS_KEYWORDS)


# ── Public backend API ────────────────────────────────────────────────────────

def fetch_canvas_venues(
    lat: float,
    lon: float,
    radius_m: int,
    categories: list[str] | None = None,
    city: str = "london",
    force_refresh: bool = False,
    event_type: str = "",
) -> list[dict]:
    """
    Return Canvas Events venues near (lat, lon) within radius_m metres.

    Cache is keyed by city only (not radius) so that auto-expanding the search
    radius is free — no extra HTTP requests, just re-filtering cached data.
    event_type: free-text (e.g. "corporate networking evening").  Matching
    venues are sorted first; event_type_match flag is set on each venue.
    """
    city_key = _city_key(city)
    # City-level cache — radius filtering applied on every call
    cache_key = f"canvas::{city_key}"
    all_city_venues: list[dict] = []

    if not force_refresh:
        cached = _cache_get(cache_key)
        if cached is not None:
            logger.info("canvas_cache_hit city=%s count=%d", city_key, len(cached))
            all_city_venues = cached

    if not all_city_venues:
        # 1. Try the /api/venues JSON endpoint
        raw = fetch_all_from_data_feed()
        if raw:
            all_city_venues = [_normalise_feed_record(r) for r in raw if r.get("venue_name") or r.get("name")]
            logger.info("canvas_api_venues_used count=%d city=%s", len(all_city_venues), city_key)
        else:
            # 2. Fall back to HTML listing scraping
            logger.info("canvas_api_unavailable — scraping %s listing pages", city_key)
            cat_event_types: list[str] = []
            for cat in (categories or []):
                cat_event_types.extend(_CATEGORY_EVENT_TYPES.get(cat, []))
            urls = _listing_urls_for(city_key, cat_event_types)
            raw_scraped = scrape_listing_urls(urls, fetch_details=False)
            all_city_venues = [_normalise_scraped_record(r) for r in raw_scraped]
            logger.info("canvas_html_scrape_done count=%d city=%s", len(all_city_venues), city_key)

        if all_city_venues:
            _cache_set(cache_key, all_city_venues)

    # Radius filter (cheap — just list comprehension on cached data)
    filtered = [v for v in all_city_venues if v.get("name") and _within_radius(v, lat, lon, radius_m)]

    # Event-type matching: stamp each venue and sort matches first
    if event_type:
        for v in filtered:
            v["event_type_match"] = venue_matches_event_type(v.get("event_types") or [], event_type)
        filtered.sort(key=lambda v: (0 if v.get("event_type_match") else 1))

    logger.info("canvas_venues_ready count=%d radius_m=%d event_type=%r", len(filtered), radius_m, event_type)
    return filtered


# ── Standalone scraper entry point (Colab / CLI) ──────────────────────────────

def run(
    listing_html_path: str | None = None,
    listing_url: str | None = None,
    listing_urls: list[str] | None = None,
    fetch_details: bool = True,
    out_prefix: str = "venues_full",
) -> list[dict]:
    """
    Standalone scraper — mirrors the original API, extended.

    Priority:
      1. listing_html_path  — parse a saved HTML file
      2. listing_url        — scrape a single listing URL
      3. listing_urls       — scrape a list of URLs
      4. (no arg)           — try /api/venues first, then all built-in listing URLs

    Saves <out_prefix>.json and (if pandas available) <out_prefix>.csv.
    """
    import random

    all_venues: list[dict] = []

    if listing_html_path:
        html = Path(listing_html_path).read_text(encoding="utf-8", errors="ignore")
        records = parse_listing(html)
        if fetch_details:
            for r in records:
                if r.get("url"):
                    r.update(fetch_detail(r["url"]))
                    time.sleep(random.uniform(1.0, 2.5))
        all_venues = [_normalise_scraped_record(r) for r in records]

    elif listing_url or listing_urls:
        urls = ([listing_url] if listing_url else []) + (listing_urls or [])
        records = scrape_listing_urls(urls, fetch_details=fetch_details)
        all_venues = [_normalise_scraped_record(r) for r in records]

    else:
        # Comprehensive scrape: data feed → HTML fallback
        print("Trying Canvas Events /api/venues …")
        raw = fetch_all_from_data_feed()
        if raw:
            print(f"Data feed returned {len(raw)} venues.")
            all_venues = [_normalise_feed_record(r) for r in raw]
        else:
            print("Data feed unavailable — scraping listing pages …")
            urls: list[str] = list(_CATEGORY_LISTING_URLS)
            for area in _LONDON_AREAS:
                urls.append(f"{_BASE}/hire-venue-{area}")
            records = scrape_listing_urls(urls, fetch_details=fetch_details)
            all_venues = [_normalise_scraped_record(r) for r in records]

    # Save outputs
    out_json = Path(f"{out_prefix}.json")
    out_json.write_text(
        json.dumps(all_venues, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Saved {out_json}  ({len(all_venues)} venues)")
    try:
        import pandas as pd
        pd.DataFrame(all_venues).to_csv(f"{out_prefix}.csv", index=False, encoding="utf-8")
        print(f"Saved {out_prefix}.csv")
    except ImportError:
        pass

    return all_venues


if __name__ == "__main__":
    records = run(fetch_details=True)
    print(f"\nTotal venues: {len(records)}")
    if records:
        print(json.dumps(records[0], indent=2, ensure_ascii=False))
