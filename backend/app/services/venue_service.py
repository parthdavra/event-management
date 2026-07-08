"""
Venue service — thin wrapper around the api_fetcher logic adapted for the backend.
All external HTTP calls (Geoapify, Overpass, Foursquare) happen here.
"""

import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

import requests

from app.core.config import get_settings
from app.schemas.venue import VenueSearchRequest, VenueSearchResponse
from app.services import canvas_service

settings = get_settings()

PHOTON_URL = "https://photon.komoot.io/api/"
OVERPASS_MIRRORS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

CATEGORY_TAGS: Dict[str, Dict] = {
    "Restaurants & Cafes": {
        "amenity": ["restaurant", "cafe", "fast_food", "food_court", "ice_cream", "bistro"],
        "tourism": [],
        "leisure": [],
        "foursquare": "13000",
        "geoapify": "catering.restaurant,catering.cafe,catering.fast_food,catering.food_court",
    },
    "Bars & Nightlife": {
        "amenity": ["bar", "pub", "nightclub", "biergarten", "cocktail_bar"],
        "tourism": [],
        "leisure": [],
        "foursquare": "10032",
        "geoapify": "catering.bar,catering.pub,adult.nightclub",
    },
    "Hotels & Accommodation": {
        "amenity": [],
        "tourism": ["hotel", "hostel", "motel", "guest_house", "apartment"],
        "leisure": [],
        "foursquare": "19014",
        "geoapify": "accommodation.hotel,accommodation.hostel,accommodation.motel,accommodation.guest_house,accommodation.apartment",
    },
    "Conference & Event Venues": {
        "amenity": ["conference_centre", "events_venue", "exhibition_centre",
                    "community_centre", "hall", "public_hall", "social_facility"],
        "tourism": ["conference_centre"],
        "leisure": ["sports_hall"],
        "foursquare": "10000",
        "geoapify": "tourism.sights.conference_centre,activity.events_venue,accommodation.hotel",
    },
    "Arts & Entertainment": {
        "amenity": ["theatre", "cinema", "arts_centre", "music_venue", "casino",
                    "concert_hall", "opera", "nightclub"],
        "tourism": ["museum", "gallery", "artwork", "attraction"],
        "leisure": ["arts_centre"],
        "foursquare": "10000",
        "geoapify": "entertainment.culture,entertainment.culture.theatre,entertainment.culture.arts_centre,entertainment.museum,entertainment.cinema",
    },
    "Sports & Recreation": {
        "amenity": ["fitness_centre", "gym", "swimming_pool", "dojo", "leisure_centre"],
        "tourism": [],
        "leisure": ["stadium", "sports_centre", "arena", "pitch", "golf_course",
                    "sports_hall", "fitness_centre"],
        "foursquare": "18000",
        "geoapify": "sport.stadium,sport.sports_hall,sport.sports_centre,leisure.fitness_centre,sport.pitch",
    },
    "Attractions & Tourism": {
        "amenity": ["place_of_worship", "fountain", "marketplace"],
        "tourism": ["attraction", "viewpoint", "monument", "theme_park", "zoo",
                    "aquarium", "castle", "ruins"],
        "leisure": ["park", "garden", "nature_reserve", "marina"],
        "foursquare": "16000",
        "geoapify": "tourism.sights,leisure.park,natural,entertainment.zoo,entertainment.aquarium",
    },
}

ALL_CATEGORIES = list(CATEGORY_TAGS.keys())

EVENT_CATERING_PROFILES: Dict[str, Dict] = {
    "corporate": {
        "label": "Corporate / Business Event",
        "food_style": "professional buffet, working lunches, coffee & tea service",
        "geoapify_cats": "catering.restaurant,catering.cafe,catering.food_court",
        "osm_amenity": ["restaurant", "cafe", "fast_food", "food_court"],
        "notes": "Cater for dietary diversity (vegan, halal, kosher). Coffee breaks essential for full-day events.",
    },
    "birthday": {
        "label": "Birthday Party",
        "food_style": "casual dining, cocktails, birthday cake, desserts",
        "geoapify_cats": "catering.restaurant,catering.bar,catering.pub,catering.cafe",
        "osm_amenity": ["restaurant", "bar", "pub", "cafe", "bakery"],
        "notes": "Look for a bakery for a custom cake. Bars/pubs suit adult parties; restaurants for family events.",
    },
    "graduation": {
        "label": "Graduation / Academic Celebration",
        "food_style": "celebratory buffet or sit-down dinner, champagne, cake",
        "geoapify_cats": "catering.restaurant,catering.cafe,accommodation.hotel",
        "osm_amenity": ["restaurant", "cafe", "hotel"],
        "notes": "Family-friendly options are important. Afternoon tea style works well.",
    },
    "wedding": {
        "label": "Wedding / Civil Ceremony",
        "food_style": "full multi-course catering, wedding cake, bar & champagne service",
        "geoapify_cats": "accommodation.hotel,catering.restaurant",
        "osm_amenity": ["hotel", "restaurant"],
        "notes": "Essential: vegan, vegetarian, halal, gluten-free options. Dedicated bar staff required.",
    },
    "conference": {
        "label": "Conference / Seminar",
        "food_style": "working lunches, coffee breaks, light refreshments, networking drinks",
        "geoapify_cats": "catering.cafe,catering.restaurant,catering.fast_food",
        "osm_amenity": ["cafe", "restaurant", "fast_food"],
        "notes": "Coffee/tea service critical for AM sessions. Finger food for networking breaks.",
    },
    "gala": {
        "label": "Gala Dinner / Awards Ceremony",
        "food_style": "formal plated dinner, cocktail reception, wine pairing, dessert station",
        "geoapify_cats": "accommodation.hotel,catering.restaurant",
        "osm_amenity": ["hotel", "restaurant"],
        "notes": "White-glove service. Dietary cards at every place setting. Sommelier recommended.",
    },
    "networking": {
        "label": "Networking / Social Mixer",
        "food_style": "canapes, finger food, drinks reception, bowl food",
        "geoapify_cats": "catering.bar,catering.pub,catering.restaurant",
        "osm_amenity": ["bar", "pub", "restaurant"],
        "notes": "Easy-to-eat food — guests need a free hand for business cards. Open bar recommended.",
    },
    "party": {
        "label": "General Party",
        "food_style": "buffet, finger food, drinks, cake",
        "geoapify_cats": "catering.restaurant,catering.bar,catering.pub,catering.cafe",
        "osm_amenity": ["restaurant", "bar", "pub", "cafe"],
        "notes": "Confirm headcount 48h before. Cater ~10% more than guest count for buffer.",
    },
    "exhibition": {
        "label": "Exhibition / Trade Show",
        "food_style": "food stalls, quick bites, coffee, light snacks",
        "geoapify_cats": "catering.food_court,catering.fast_food,catering.cafe",
        "osm_amenity": ["food_court", "fast_food", "cafe"],
        "notes": "High footfall — multiple vendors preferred. Fast service is key.",
    },
    "product_launch": {
        "label": "Product Launch / Brand Event",
        "food_style": "branded canapes, themed cocktails, food stations",
        "geoapify_cats": "catering.restaurant,catering.bar,catering.cafe",
        "osm_amenity": ["restaurant", "bar", "cafe"],
        "notes": "Food can be themed to match the brand. Instagram-worthy presentation is a bonus.",
    },
}

_DEFAULT_CATERING_PROFILE: Dict = {
    "label": "General Event",
    "food_style": "buffet or sit-down dinner",
    "geoapify_cats": "catering.restaurant,catering.cafe",
    "osm_amenity": ["restaurant", "cafe"],
    "notes": "Confirm dietary requirements with all guests in advance.",
}

_IN_HOUSE_CATERING_TYPES = {
    "restaurant", "cafe", "hotel", "pub", "bar", "fast food", "food court",
    "bistro", "catering", "nightclub", "guest house", "motel", "hostel",
}

_DAY_NAMES = {
    "mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday",
    "fri": "Friday", "sat": "Saturday", "sun": "Sunday",
}


def _expand_day(d: str) -> str:
    """'Fri' -> 'Friday' etc. so a question like 'price for Friday' matches the
    indexed text directly instead of relying on the LLM to infer the abbreviation."""
    return _DAY_NAMES.get(d.strip().lower()[:3], d)


CAPACITY_ESTIMATES: Dict[str, str] = {
    "theatre": "typically 100–2,000 seats",
    "cinema": "typically 50–500 seats",
    "arts centre": "typically 50–800 people",
    "music venue": "typically 100–5,000 people",
    "concert hall": "typically 500–5,000 seats",
    "conference centre": "typically 50–5,000 people",
    "events venue": "typically 50–3,000 people",
    "exhibition centre": "typically 500–50,000 people",
    "hotel": "conference rooms typically 20–1,000 people",
    "restaurant": "typically 20–300 covers",
    "pub": "typically 20–200 people",
    "bar": "typically 50–500 people",
    "stadium": "typically 5,000–90,000 people",
    "sports centre": "typically 200–10,000 people",
    "sports hall": "typically 200–5,000 people",
    "arena": "typically 2,000–20,000 people",
    "community centre": "typically 50–500 people",
    "public hall": "typically 50–1,000 people",
    "nightclub": "typically 100–2,000 people",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _map_thumbnail_url(lat, lon) -> str:
    key = settings.geoapify_api_key
    if not key or lat is None or lon is None:
        return ""
    return (
        f"https://maps.geoapify.com/v1/staticmap"
        f"?style=osm-bright-smooth&width=600&height=220"
        f"&center=lonlat:{lon},{lat}&zoom=16"
        f"&marker=lonlat:{lon},{lat};color:%23e74c3c;size:large"
        f"&apiKey={key}"
    )


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(d_lon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _parse_capacity(raw: str) -> str:
    if not raw:
        return ""
    raw = raw.strip()
    if raw.isdigit():
        n = int(raw)
        return f"{n:,} people"
    nums = re.findall(r"\d+", raw)
    if nums:
        if len(nums) == 1:
            return f"{int(nums[0]):,} people"
        return f"{int(nums[0]):,} (seated) / {int(nums[1]):,} (standing)"
    return raw


def _osm_address(tags: Dict) -> str:
    return ", ".join(filter(None, [
        tags.get("addr:housenumber"),
        tags.get("addr:street"),
        tags.get("addr:suburb"),
        tags.get("addr:city"),
        tags.get("addr:postcode"),
    ]))


def get_catering_profile(event_type: str) -> Dict:
    key = event_type.lower().strip()
    if key in EVENT_CATERING_PROFILES:
        return EVENT_CATERING_PROFILES[key]
    for k, profile in EVENT_CATERING_PROFILES.items():
        if k in key or key in k:
            return profile
    return _DEFAULT_CATERING_PROFILE


def venue_to_text(v: Dict, city: str) -> str:
    """Convert a venue dict to an indexable text chunk (includes Canvas rich data).

    Renders every field the venue dict may carry, not just a summarized subset —
    room-by-room pricing, per-space pricing, and list fields (perfect_for, event
    types, features) are included in full rather than truncated, so the indexed
    chunk actually reflects everything available after Canvas enrichment instead
    of dropping most of it before it's ever searchable.
    """
    parts = [f"Venue: {v.get('name', 'Unknown')}"]
    if v.get("type"):
        parts.append(f"Type: {v['type']}")
    if v.get("address"):
        parts.append(f"Address: {v['address']}")
    if city:
        parts.append(f"City: {city}")
    if v.get("capacity"):
        parts.append(f"Capacity: {v['capacity']}")
    else:
        vtype_lower = (v.get("type") or "").lower()
        est = CAPACITY_ESTIMATES.get(vtype_lower, "")
        if est:
            parts.append(f"Capacity: {est} (estimated for {vtype_lower})")
    if v.get("capacity_raw"):
        parts.append(f"Capacity (numeric): {v['capacity_raw']}")
    if v.get("lat") and v.get("lon"):
        parts.append(f"Coordinates: {v['lat']}, {v['lon']}")
    if v.get("phone"):
        parts.append(f"Phone: {v['phone']}")
    if v.get("email"):
        parts.append(f"Email: {v['email']}")
    if v.get("website"):
        parts.append(f"Website: {v['website']}")
    if v.get("opening_hours"):
        parts.append(f"Opening hours: {v['opening_hours']}")
    if v.get("operator"):
        parts.append(f"Operator: {v['operator']}")
    if v.get("rating"):
        parts.append(f"Rating: {v['rating']}")
    if v.get("cuisine"):
        parts.append(f"Cuisine: {v['cuisine']}")
    if v.get("description"):
        parts.append(f"Description: {v['description'][:300]}")
    if v.get("wheelchair"):
        parts.append(f"Wheelchair access: {v['wheelchair']}")
    if v.get("internet_access"):
        parts.append(f"Internet access: {v['internet_access']}")
    if v.get("outdoor_seating"):
        parts.append(f"Outdoor seating: {v['outdoor_seating']}")
    if v.get("stars"):
        parts.append(f"Stars: {v['stars']}")
    if v.get("rooms"):
        parts.append(f"Hotel rooms: {v['rooms']}")
    if v.get("price_range"):
        parts.append(f"Price range: {v['price_range']}")
    if v.get("price_per_day"):
        parts.append(f"Day hire: {v['price_per_day']}")
    if v.get("price_per_hour"):
        parts.append(f"Hourly hire: {v['price_per_hour']}")
    if v.get("min_spend"):
        parts.append(f"Minimum spend: {v['min_spend']}")
    parts.append(f"Source: {v.get('source', 'unknown')}")

    # ── Canvas Events rich data ───────────────────────────────────────────────
    cap_detail = v.get("canvas_capacity_detail") or {}
    if cap_detail:
        caps = ", ".join(
            f"{k.title()}: {int(val):,}" for k, val in cap_detail.items() if val
        )
        if caps:
            parts.append(f"Capacity breakdown: {caps}")

    price_guide = v.get("canvas_price_guide") or {}
    if price_guide.get("from_price"):
        parts.append(f"Starting from: {price_guide['from_price']}")
    days = price_guide.get("days") or {}
    if days:
        day_prices = "; ".join(f"{_expand_day(d)}: {p}" for d, p in days.items() if p != "Closed")
        if day_prices:
            parts.append(f"Price guide by day: {day_prices}")
    rooms_pricing = price_guide.get("rooms") or []
    if rooms_pricing:
        room_lines = "; ".join(
            f"{r.get('name', '')} ({r.get('session', '')}, {r.get('time', '')}): {r.get('price', '')}"
            for r in rooms_pricing if r.get("name")
        )
        if room_lines:
            parts.append(f"Room hire options: {room_lines}")

    perfect_for = v.get("canvas_perfect_for") or []
    if perfect_for:
        parts.append(f"Perfect for: {', '.join(perfect_for)}")

    spaces = v.get("canvas_spaces") or []
    if spaces:
        space_lines = ", ".join(
            f"{s['name']} ({s['price_per_day']})" if s.get("price_per_day") else s.get("name", "")
            for s in spaces if s.get("name")
        )
        if space_lines:
            parts.append(f"Available spaces: {space_lines}")

    features = v.get("canvas_features") or {}
    for cat, items in features.items():
        if items:
            parts.append(f"{cat}: {', '.join(items)}")

    event_types = v.get("event_types") or []
    if event_types:
        parts.append(f"Event types: {', '.join(event_types)}")

    return "\n".join(parts)


# ── Geocoding ─────────────────────────────────────────────────────────────────

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_NOMINATIM_HEADERS = {"User-Agent": "EventManagementPlatform/1.0 (pdavra1997@gmail.com)"}
_GEOCODE_CACHE: Dict[str, Optional[Tuple[float, float]]] = {}


def get_city_coords(city: str) -> Optional[Tuple[float, float]]:
    key = city.strip().lower()
    if key in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[key]

    result = _resolve_city_coords(city)
    _GEOCODE_CACHE[key] = result
    return result


def _resolve_city_coords(city: str) -> Optional[Tuple[float, float]]:
    geoapify_key = settings.geoapify_api_key
    if geoapify_key:
        try:
            resp = requests.get(
                "https://api.geoapify.com/v1/geocode/search",
                params={"text": city, "type": "city", "limit": 1, "apiKey": geoapify_key},
                timeout=10,
            )
            if resp.status_code == 200:
                features = resp.json().get("features", [])
                if features:
                    props = features[0]["properties"]
                    return float(props["lat"]), float(props["lon"])
        except Exception:
            pass

    try:
        resp = requests.get(
            PHOTON_URL,
            params={"q": city, "limit": 5, "layer": "city"},
            timeout=10,
        )
        if resp.status_code == 200:
            features = resp.json().get("features", [])
            for f in features:
                props = f.get("properties", {})
                if props.get("type") in ("city", "town", "village", "municipality"):
                    lon, lat = f["geometry"]["coordinates"]
                    return float(lat), float(lon)
            if features:
                lon, lat = features[0]["geometry"]["coordinates"]
                return float(lat), float(lon)
    except Exception:
        pass

    # Nominatim fallback (OSM official geocoder — 1 req/sec policy)
    try:
        time.sleep(1)
        resp = requests.get(
            _NOMINATIM_URL,
            params={"q": city, "format": "json", "limit": 1},
            headers=_NOMINATIM_HEADERS,
            timeout=10,
        )
        if resp.status_code == 200:
            results = resp.json()
            if results:
                return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception:
        pass

    return None


# ── OSM / Overpass ────────────────────────────────────────────────────────────

def fetch_overpass_venues(
    lat: float, lon: float, radius_m: int, categories: List[str]
) -> List[Dict]:
    amenity_vals, tourism_vals, leisure_vals = [], [], []
    for cat in categories:
        tags = CATEGORY_TAGS.get(cat, {})
        amenity_vals.extend(tags.get("amenity", []))
        tourism_vals.extend(tags.get("tourism", []))
        leisure_vals.extend(tags.get("leisure", []))

    amenity_vals = list(set(amenity_vals))
    tourism_vals = list(set(tourism_vals))
    leisure_vals = list(set(leisure_vals))

    if not amenity_vals and not tourism_vals and not leisure_vals:
        amenity_vals = ["restaurant", "cafe", "bar", "hotel", "theatre", "conference_centre"]
        tourism_vals = ["museum", "attraction", "gallery"]

    parts = []
    bbox = f"around:{radius_m},{lat},{lon}"
    if amenity_vals:
        pattern = "|".join(amenity_vals)
        parts += [
            f'node["amenity"~"{pattern}"]({bbox});',
            f'way["amenity"~"{pattern}"]({bbox});',
        ]
    if tourism_vals:
        pattern = "|".join(tourism_vals)
        parts += [
            f'node["tourism"~"{pattern}"]({bbox});',
            f'way["tourism"~"{pattern}"]({bbox});',
        ]
    if leisure_vals:
        pattern = "|".join(leisure_vals)
        parts += [
            f'node["leisure"~"{pattern}"]({bbox});',
            f'way["leisure"~"{pattern}"]({bbox});',
        ]
    parts += [
        f'node["capacity"]({bbox})["name"];',
        f'way["capacity"]({bbox})["name"];',
    ]

    query = "[out:json][timeout:90];\n(\n  " + "\n  ".join(parts) + "\n);\nout center tags;"

    _NON_VENUE_AMENITY = {
        "parking", "bicycle_parking", "fuel", "toilets", "charging_station",
        "recycling", "waste_disposal", "waste_basket", "post_box",
        "atm", "vending_machine", "bench", "shelter", "telephone",
    }

    for mirror in OVERPASS_MIRRORS:
        try:
            resp = requests.post(mirror, data={"data": query}, timeout=120)
            if resp.status_code != 200:
                continue
            venues = []
            for el in resp.json().get("elements", []):
                tags = el.get("tags", {})
                name = tags.get("name", "").strip()
                if not name:
                    continue
                amenity_val = tags.get("amenity", "")
                leisure_val = tags.get("leisure", "")
                if amenity_val in _NON_VENUE_AMENITY or leisure_val == "pitch":
                    continue
                if el["type"] == "node":
                    vlat, vlon = el.get("lat"), el.get("lon")
                else:
                    center = el.get("center", {})
                    vlat, vlon = center.get("lat"), center.get("lon")
                venue_type = (
                    tags.get("amenity") or tags.get("tourism") or
                    tags.get("leisure") or tags.get("building", "venue")
                ).replace("_", " ").title()
                capacity_raw = tags.get("capacity", "")
                capacity_str = _parse_capacity(capacity_raw)
                venues.append({
                    "name": name,
                    "type": venue_type,
                    "address": _osm_address(tags),
                    "phone": tags.get("phone") or tags.get("contact:phone", ""),
                    "email": tags.get("email") or tags.get("contact:email", ""),
                    "website": tags.get("website") or tags.get("contact:website", ""),
                    "opening_hours": tags.get("opening_hours", ""),
                    "cuisine": tags.get("cuisine", "").replace(";", ", "),
                    "capacity": capacity_str,
                    "capacity_raw": capacity_raw,
                    "wheelchair": tags.get("wheelchair", ""),
                    "internet_access": tags.get("internet_access", ""),
                    "outdoor_seating": tags.get("outdoor_seating", ""),
                    "stars": tags.get("stars") or tags.get("accommodation:stars", ""),
                    "rooms": tags.get("rooms", ""),
                    "description": tags.get("description", ""),
                    "operator": tags.get("operator", ""),
                    "lat": vlat,
                    "lon": vlon,
                    "rating": None,
                    "price_per_day": "",
                    "price_per_hour": "",
                    "price_range": tags.get("charge") or tags.get("price_range") or "",
                    "min_spend": "",
                    "image_url": "",
                    "source": "OpenStreetMap",
                    "map_thumbnail_url": _map_thumbnail_url(vlat, vlon),
                })
            return venues
        except Exception:
            continue
    return []


def fetch_geoapify_venues(
    lat: float, lon: float, radius_m: int, categories: List[str]
) -> List[Dict]:
    geoapify_key = settings.geoapify_api_key
    if not geoapify_key:
        return []

    geo_cats = []
    for cat in categories:
        geo_cats.extend(CATEGORY_TAGS.get(cat, {}).get("geoapify", "").split(","))
    geo_cats = [c.strip() for c in set(geo_cats) if c.strip()]

    params: Dict = {
        "filter": f"circle:{lon},{lat},{min(radius_m, 50000)}",
        "limit": 200,
        "apiKey": geoapify_key,
    }
    if geo_cats:
        params["categories"] = ",".join(geo_cats)

    try:
        resp = requests.get("https://api.geoapify.com/v2/places", params=params, timeout=20)
        resp.raise_for_status()
        venues = []
        for feature in resp.json().get("features", []):
            props = feature.get("properties", {})
            name = props.get("name", "").strip()
            if not name:
                continue
            raw = props.get("datasource", {}).get("raw", {})
            contact = props.get("contact", {})
            facilities = props.get("facilities", {})
            catering = props.get("catering", {})
            accommodation = props.get("accommodation", {})
            cats = props.get("categories", [])
            cat_str = cats[0] if cats else ""
            type_label = cat_str.split(".")[-1].replace("_", " ").title() if cat_str else "Venue"
            capacity_raw = (
                str(catering.get("capacity", "")) or
                raw.get("capacity", "") or
                raw.get("maxcapacity", "")
            )
            capacity_str = _parse_capacity(capacity_raw)
            phone = (
                contact.get("phone") or raw.get("phone") or
                raw.get("contact:phone") or props.get("phone", "")
            )
            email = (
                contact.get("email") or raw.get("email") or
                raw.get("contact:email", "")
            )
            website = (
                raw.get("website") or raw.get("contact:website") or
                contact.get("website") or props.get("website", "")
            )
            stars = str(accommodation.get("stars", "") or raw.get("stars", ""))
            rooms = str(accommodation.get("rooms", "") or raw.get("rooms", ""))
            venues.append({
                "name": name,
                "type": type_label,
                "address": props.get("formatted", ""),
                "phone": phone,
                "email": email,
                "website": website,
                "opening_hours": raw.get("opening_hours", ""),
                "cuisine": raw.get("cuisine", "").replace(";", ", "),
                "capacity": capacity_str,
                "capacity_raw": capacity_raw,
                "wheelchair": facilities.get("wheelchair", "") or raw.get("wheelchair", ""),
                "internet_access": facilities.get("internet_access", "") or raw.get("internet_access", ""),
                "outdoor_seating": raw.get("outdoor_seating", ""),
                "stars": stars,
                "rooms": rooms,
                "description": raw.get("description", ""),
                "operator": raw.get("operator", ""),
                "lat": props.get("lat"),
                "lon": props.get("lon"),
                "rating": None,
                "price_per_day": "",
                "price_per_hour": "",
                "price_range": raw.get("charge") or raw.get("price_range") or raw.get("price") or "",
                "min_spend": "",
                "image_url": "",
                "source": "Geoapify",
                "map_thumbnail_url": _map_thumbnail_url(props.get("lat"), props.get("lon")),
            })
        return venues
    except Exception:
        return []


def fetch_foursquare_venues(
    lat: float, lon: float, radius_m: int, categories: List[str]
) -> List[Dict]:
    fsq_key = settings.foursquare_api_key
    if not fsq_key:
        return []

    fsq_cats = list({CATEGORY_TAGS.get(cat, {}).get("foursquare", "") for cat in categories if CATEGORY_TAGS.get(cat, {}).get("foursquare")})

    try:
        resp = requests.get(
            "https://places-api.foursquare.com/places/search",
            headers={
                "Authorization": f"Bearer {fsq_key}",
                "X-Places-Api-Version": "2025-06-17",
                "Accept": "application/json",
            },
            params={
                "ll": f"{lat},{lon}",
                "radius": min(radius_m, 100000),
                "limit": 50,
                "categories": ",".join(fsq_cats) if fsq_cats else "10000",
                "fields": "name,categories,location,tel,website,rating,price,photos,latitude,longitude",
            },
            timeout=15,
        )
        if resp.status_code not in (200, 201):
            return []
        venues = []
        for place in resp.json().get("results", []):
            name = place.get("name", "").strip()
            if not name:
                continue
            # New Places API: lat/lon at top level, not in geocodes.main
            vlat = place.get("latitude")
            vlon = place.get("longitude")
            loc = place.get("location", {})
            cats = place.get("categories", [])
            type_label = cats[0].get("name", "Venue") if cats else "Venue"
            address = loc.get("formatted_address") or ", ".join(filter(None, [
                loc.get("address"), loc.get("locality"), loc.get("postcode"),
            ]))
            price_tier = place.get("price")
            price_range = "£" * price_tier if isinstance(price_tier, int) and 1 <= price_tier <= 4 else ""

            photos = place.get("photos") or []
            image_url = ""
            if photos:
                p = photos[0]
                prefix = p.get("prefix", "")
                suffix = p.get("suffix", "")
                if prefix and suffix:
                    image_url = f"{prefix}400x300{suffix}"

            venues.append({
                "name": name,
                "type": type_label,
                "address": address,
                "phone": place.get("tel", ""),
                "email": "",
                "website": place.get("website", ""),
                "opening_hours": "",
                "cuisine": "",
                "capacity": "",
                "capacity_raw": "",
                "wheelchair": "",
                "internet_access": "",
                "outdoor_seating": "",
                "stars": "",
                "rooms": "",
                "description": "",
                "operator": "",
                "lat": vlat,
                "lon": vlon,
                "rating": place.get("rating"),
                "price_per_day": "",
                "price_per_hour": "",
                "price_range": price_range,
                "min_spend": "",
                "image_url": image_url,
                "source": "Foursquare",
                "map_thumbnail_url": _map_thumbnail_url(vlat, vlon),
            })
        return venues
    except Exception:
        return []


def _parse_venue_price(v: Dict) -> Optional[float]:
    """Return the best numeric day-hire price estimate for a venue, or None if unknown."""
    def _first_number(s: str) -> Optional[float]:
        nums = re.findall(r"[\d,]+", s.replace(",", ""))
        return float(nums[0]) if nums else None

    # Priority 1: Canvas price guide "from_price" — most reliable
    pg = v.get("canvas_price_guide") or {}
    if pg.get("from_price"):
        n = _first_number(pg["from_price"])
        if n:
            return n

    # Priority 2: cheapest Canvas space price
    for space in (v.get("canvas_spaces") or []):
        n = _first_number(space.get("price_per_day") or "")
        if n:
            return n

    # Priority 3: price_per_day field
    if v.get("price_per_day"):
        n = _first_number(v["price_per_day"])
        if n:
            return n

    # Priority 4: min_spend (lower bound — venue may still be affordable)
    if v.get("min_spend"):
        n = _first_number(v["min_spend"])
        if n:
            return n

    return None


def _tag_budget(venues: List[Dict], venue_hire_budget: float) -> None:
    """Mutate each venue dict in-place to add within_hire_budget / parsed_price."""
    for v in venues:
        price = _parse_venue_price(v)
        v["parsed_price"] = price
        v["within_hire_budget"] = bool(price is not None and price <= venue_hire_budget)
        v["over_hire_budget"] = bool(price is not None and price > venue_hire_budget)


def _deduplicate(venues: List[Dict]) -> List[Dict]:
    seen: set = set()
    unique: List[Dict] = []
    for v in venues:
        key = re.sub(r"[^a-z0-9]", "", v["name"].lower())
        if key and key not in seen:
            seen.add(key)
            unique.append(v)
    return unique


def fetch_all_city_venues(
    city: str,
    categories: List[str],
    radius_km: int = 5,
    use_foursquare: bool = True,
    use_geoapify: bool = True,
    enrich_details: bool = True,
    max_venues: int = 300,
    coords: Optional[Tuple[float, float]] = None,
    event_type: str = "",
    max_radius_km: int = 25,
    venue_hire_budget: float = 0,
) -> Tuple[List[Dict], Dict[str, int], int]:
    """
    Returns (venues, source_counts, radius_km_used).

    Auto-expands search radius by 1 km per step (up to max_radius_km) until:
    - At least one venue is found, AND
    - If venue_hire_budget > 0: at least one venue is within that budget.
    """
    if coords is None:
        coords = get_city_coords(city)
    if not coords:
        return [], {}, radius_km
    lat, lon = coords

    is_canvas = canvas_service.is_canvas_city(city)
    current_radius = radius_km
    last_source_counts: Dict[str, int] = {}

    while current_radius <= max_radius_km:
        radius_m = current_radius * 1000
        source_counts: Dict[str, int] = {}
        all_venues: List[Dict] = []

        # Canvas Events is PRIMARY for London and Manchester.
        if is_canvas:
            try:
                canvas_venues = canvas_service.fetch_canvas_venues(
                    lat, lon, radius_m, categories, city=city, event_type=event_type
                )
                for v in canvas_venues:
                    if not v.get("map_thumbnail_url"):
                        v["map_thumbnail_url"] = _map_thumbnail_url(v.get("lat"), v.get("lon"))
            except Exception:
                canvas_venues = []

            if canvas_venues:
                source_counts["Canvas Events"] = len(canvas_venues)
                deduped = _deduplicate(canvas_venues)
                # Tag budget for informational display on frontend — never gate expansion on it
                # (Canvas basic listing rarely includes pricing; expansion should only happen
                # when there are literally zero venues at the requested radius)
                if venue_hire_budget > 0:
                    _tag_budget(deduped, venue_hire_budget)
                return deduped[:max_venues], source_counts, current_radius

            source_counts["Canvas Events"] = 0
        else:
            # Non-Canvas cities: OSM + Geoapify + Foursquare in parallel
            with ThreadPoolExecutor(max_workers=3) as ex:
                futures: Dict[str, Any] = {
                    "OpenStreetMap": ex.submit(fetch_overpass_venues, lat, lon, radius_m, categories)
                }
                if use_geoapify:
                    futures["Geoapify"] = ex.submit(fetch_geoapify_venues, lat, lon, radius_m, categories)
                if use_foursquare:
                    futures["Foursquare"] = ex.submit(fetch_foursquare_venues, lat, lon, radius_m, categories)

                for name, fut in futures.items():
                    try:
                        result = fut.result(timeout=130)
                        source_counts[name] = len(result)
                        all_venues.extend(result)
                    except Exception:
                        source_counts[name] = 0

            deduped = _deduplicate(all_venues)
            if deduped:
                if venue_hire_budget > 0:
                    _tag_budget(deduped, venue_hire_budget)
                return deduped[:max_venues], source_counts, current_radius

        last_source_counts = source_counts
        # Zero venues found at this radius — expand by 2 km and retry
        current_radius += 2

    # Exhausted max_radius — return empty with the last radius tried
    return [], last_source_counts, current_radius - 2


# ── Public service functions ──────────────────────────────────────────────────

def fetch_venues(body: VenueSearchRequest) -> VenueSearchResponse:
    coords = get_city_coords(body.city)
    if not coords:
        return VenueSearchResponse(city=body.city, venues=[], total=0, source_counts={})

    venues, source_counts, radius_km_used = fetch_all_city_venues(
        city=body.city,
        categories=body.categories,
        radius_km=body.radius_km,
        use_foursquare=body.use_foursquare,
        use_geoapify=body.use_geoapify,
        enrich_details=body.enrich_details,
        max_venues=body.max_venues,
        coords=coords,
        event_type=body.event_type,
        max_radius_km=body.max_radius_km,
        venue_hire_budget=body.venue_hire_budget,
    )
    return VenueSearchResponse(
        city=body.city,
        venues=venues,
        total=len(venues),
        source_counts=source_counts,
        radius_km_used=radius_km_used,
    )


def get_catering_guide(event_type: str) -> Dict:
    profile = get_catering_profile(event_type)
    return {"event_type": event_type, "profile": profile}
