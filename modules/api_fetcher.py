import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

FOURSQUARE_API_KEY = os.getenv("FOURSQUARE_API_KEY", "VVEUCPRX3VRWXOPS0R2P0XP4TKJYS0K45GGJACDA03KKWFTB")
GEOAPIFY_API_KEY = os.getenv("GEOAPIFY_API_KEY", "b61619a029c244c09638484ad809bbf6")

PHOTON_URL = "https://photon.komoot.io/api/"
OVERPASS_MIRRORS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

# ── Category mappings ──────────────────────────────────────────────────────────
# geoapify keys are VERIFIED against the official category list at:
#   https://apidocs.geoapify.com/docs/places/#categories

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
        # FIXED: verified Geoapify category strings from official docs
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

# ── Event-type catering profiles ───────────────────────────────────────────────
# Maps event type keywords → what food style is expected and which OSM/Geoapify
# categories to search for external catering options.

EVENT_CATERING_PROFILES: Dict[str, Dict] = {
    "corporate": {
        "label": "Corporate / Business Event",
        "food_style": "professional buffet, working lunches, coffee & tea service",
        "geoapify_cats": "catering.restaurant,catering.cafe,catering.food_court",
        "osm_amenity":   ["restaurant", "cafe", "fast_food", "food_court"],
        "notes": "Cater for dietary diversity (vegan, halal, kosher). Coffee breaks essential for full-day events.",
    },
    "birthday": {
        "label": "Birthday Party",
        "food_style": "casual dining, cocktails, birthday cake, desserts",
        "geoapify_cats": "catering.restaurant,catering.bar,catering.pub,catering.cafe",
        "osm_amenity":   ["restaurant", "bar", "pub", "cafe", "bakery"],
        "notes": "Look for a bakery for a custom cake. Bars/pubs suit adult parties; restaurants for family events.",
    },
    "graduation": {
        "label": "Graduation / Academic Celebration",
        "food_style": "celebratory buffet or sit-down dinner, champagne, cake",
        "geoapify_cats": "catering.restaurant,catering.cafe,accommodation.hotel",
        "osm_amenity":   ["restaurant", "cafe", "hotel"],
        "notes": "Family-friendly options are important. Afternoon tea style works well.",
    },
    "wedding": {
        "label": "Wedding / Civil Ceremony",
        "food_style": "full multi-course catering, wedding cake, bar & champagne service",
        "geoapify_cats": "accommodation.hotel,catering.restaurant",
        "osm_amenity":   ["hotel", "restaurant"],
        "notes": "Essential: vegan, vegetarian, halal, gluten-free options. Dedicated bar staff required.",
    },
    "conference": {
        "label": "Conference / Seminar",
        "food_style": "working lunches, coffee breaks, light refreshments, networking drinks",
        "geoapify_cats": "catering.cafe,catering.restaurant,catering.fast_food",
        "osm_amenity":   ["cafe", "restaurant", "fast_food"],
        "notes": "Coffee/tea service critical for AM sessions. Finger food for networking breaks.",
    },
    "gala": {
        "label": "Gala Dinner / Awards Ceremony",
        "food_style": "formal plated dinner, cocktail reception, wine pairing, dessert station",
        "geoapify_cats": "accommodation.hotel,catering.restaurant",
        "osm_amenity":   ["hotel", "restaurant"],
        "notes": "White-glove service. Dietary cards at every place setting. Sommelier recommended.",
    },
    "networking": {
        "label": "Networking / Social Mixer",
        "food_style": "canapes, finger food, drinks reception, bowl food",
        "geoapify_cats": "catering.bar,catering.pub,catering.restaurant",
        "osm_amenity":   ["bar", "pub", "restaurant"],
        "notes": "Easy-to-eat food — guests need a free hand for business cards. Open bar recommended.",
    },
    "exhibition": {
        "label": "Exhibition / Trade Show",
        "food_style": "food stalls, quick bites, coffee, light snacks",
        "geoapify_cats": "catering.cafe,catering.fast_food,catering.food_court,catering.restaurant",
        "osm_amenity":   ["cafe", "fast_food", "food_court", "restaurant"],
        "notes": "High footfall — multiple vendors preferred. Fast service is key.",
    },
    "product_launch": {
        "label": "Product Launch / Brand Event",
        "food_style": "branded canapes, themed cocktails, food stations",
        "geoapify_cats": "catering.restaurant,catering.bar,catering.cafe",
        "osm_amenity":   ["restaurant", "bar", "cafe"],
        "notes": "Food can be themed to match the brand. Instagram-worthy presentation is a bonus.",
    },
    "party": {
        "label": "General Party",
        "food_style": "buffet, finger food, drinks, cake",
        "geoapify_cats": "catering.restaurant,catering.bar,catering.pub,catering.cafe",
        "osm_amenity":   ["restaurant", "bar", "pub", "cafe"],
        "notes": "Confirm headcount 48h before. Cater ~10% more than guest count for buffer.",
    },
}

# Fallback for unknown event types
_DEFAULT_CATERING_PROFILE: Dict = {
    "label": "General Event",
    "food_style": "buffet or sit-down dinner",
    "geoapify_cats": "catering.restaurant,catering.cafe",
    "osm_amenity":   ["restaurant", "cafe"],
    "notes": "Confirm dietary requirements with all guests in advance.",
}

# Venue types that typically include in-house catering
_IN_HOUSE_CATERING_TYPES = {
    "restaurant", "cafe", "hotel", "pub", "bar", "fast food", "food court",
    "bistro", "catering", "nightclub", "guest house", "motel", "hostel",
}


def get_catering_profile(event_type: str) -> Dict:
    """Return the catering profile for a given event type string."""
    key = event_type.lower().strip()
    # Try direct match first
    if key in EVENT_CATERING_PROFILES:
        return EVENT_CATERING_PROFILES[key]
    # Fuzzy match against label words
    for k, profile in EVENT_CATERING_PROFILES.items():
        if k in key or key in k:
            return profile
    return _DEFAULT_CATERING_PROFILE


# ── Distance helpers ───────────────────────────────────────────────────────────

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in kilometres between two (lat, lon) points."""
    import math
    R = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (math.sin(d_lat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(d_lon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _walking_time(km: float) -> str:
    """Rough walking-time estimate (5 km/h average walking speed)."""
    mins = km / 5.0 * 60
    if mins < 2:
        return "< 2 min walk"
    return f"~{int(round(mins))} min walk"


# ── Catering search near a venue ──────────────────────────────────────────────

def fetch_catering_near_venue(
    venue_lat: float,
    venue_lon: float,
    event_type: str = "corporate",
    initial_radius_m: int = 1000,
    max_radius_m: int = 5000,
) -> Tuple[List[Dict], int]:
    """
    Search for food / catering options near a venue.
    Auto-expands radius by 1 km if fewer than 3 results found, up to max_radius_m.
    Returns (venues_with_distance, final_radius_m_used).
    """
    profile = get_catering_profile(event_type)
    geo_cats = profile["geoapify_cats"]
    osm_amenity = profile["osm_amenity"]

    radius_m = initial_radius_m
    results: List[Dict] = []

    while radius_m <= max_radius_m:
        candidates: List[Dict] = []

        # Try Geoapify first (reliable from Docker)
        if GEOAPIFY_API_KEY:
            try:
                resp = requests.get(
                    "https://api.geoapify.com/v2/places",
                    params={
                        "filter": f"circle:{venue_lon},{venue_lat},{radius_m}",
                        "categories": geo_cats,
                        "limit": 50,
                        "apiKey": GEOAPIFY_API_KEY,
                    },
                    timeout=15,
                )
                if resp.status_code == 200:
                    for feat in resp.json().get("features", []):
                        p = feat.get("properties", {})
                        name = p.get("name", "").strip()
                        if not name:
                            continue
                        raw = p.get("datasource", {}).get("raw", {})
                        contact = p.get("contact", {})
                        vlat, vlon = p.get("lat"), p.get("lon")
                        dist_km = haversine_km(venue_lat, venue_lon, vlat, vlon) if vlat and vlon else None
                        candidates.append({
                            "name": name,
                            "type": (p.get("categories", [""])[0].split(".")[-1]
                                     .replace("_", " ").title()),
                            "address": p.get("formatted", ""),
                            "phone": contact.get("phone") or raw.get("phone", ""),
                            "email": contact.get("email") or raw.get("email", ""),
                            "website": raw.get("website", ""),
                            "cuisine": raw.get("cuisine", "").replace(";", ", "),
                            "opening_hours": raw.get("opening_hours", ""),
                            "wheelchair": p.get("facilities", {}).get("wheelchair", ""),
                            "lat": vlat,
                            "lon": vlon,
                            "distance_km": round(dist_km, 3) if dist_km is not None else None,
                            "distance_label": _walking_time(dist_km) if dist_km is not None else "unknown",
                            "source": "Geoapify",
                        })
            except Exception:
                pass

        # Overpass fallback / supplement
        if len(candidates) < 3:
            pattern = "|".join(osm_amenity)
            bbox = f"around:{radius_m},{venue_lat},{venue_lon}"
            query = (
                f'[out:json][timeout:30];\n(\n'
                f'  node["amenity"~"{pattern}"]["name"]({bbox});\n'
                f'  way["amenity"~"{pattern}"]["name"]({bbox});\n'
                f');\nout center tags;'
            )
            for mirror in OVERPASS_MIRRORS:
                try:
                    resp = requests.post(mirror, data={"data": query}, timeout=45)
                    if resp.status_code == 200:
                        for el in resp.json().get("elements", []):
                            tags = el.get("tags", {})
                            name = tags.get("name", "").strip()
                            if not name:
                                continue
                            vlat = el.get("lat") or el.get("center", {}).get("lat")
                            vlon = el.get("lon") or el.get("center", {}).get("lon")
                            dist_km = haversine_km(venue_lat, venue_lon, vlat, vlon) if vlat and vlon else None
                            candidates.append({
                                "name": name,
                                "type": tags.get("amenity", "food").replace("_", " ").title(),
                                "address": _osm_address(tags),
                                "phone": tags.get("phone") or tags.get("contact:phone", ""),
                                "email": tags.get("email") or tags.get("contact:email", ""),
                                "website": tags.get("website") or tags.get("contact:website", ""),
                                "cuisine": tags.get("cuisine", "").replace(";", ", "),
                                "opening_hours": tags.get("opening_hours", ""),
                                "wheelchair": tags.get("wheelchair", ""),
                                "lat": vlat,
                                "lon": vlon,
                                "distance_km": round(dist_km, 3) if dist_km is not None else None,
                                "distance_label": _walking_time(dist_km) if dist_km is not None else "unknown",
                                "source": "OpenStreetMap",
                            })
                        break
                except Exception:
                    continue

        # Deduplicate by name
        seen: set = set()
        unique: List[Dict] = []
        for c in candidates:
            key = re.sub(r"[^a-z0-9]", "", c["name"].lower())
            if key and key not in seen:
                seen.add(key)
                unique.append(c)

        # Sort by distance
        unique.sort(key=lambda x: x["distance_km"] if x["distance_km"] is not None else 99)
        results = unique

        if len(results) >= 3:
            break
        radius_m += 1000  # expand by 1 km and retry

    return results, radius_m


# ── Typical seated capacity ranges by OSM/Geoapify venue type.
# Used when no exact capacity tag is present — gives the LLM enough context
# to answer questions like "find a venue for 300 people".
CAPACITY_ESTIMATES: Dict[str, str] = {
    "theatre":            "typically 100–2,000 seats",
    "cinema":             "typically 50–500 seats",
    "arts centre":        "typically 50–800 people",
    "music venue":        "typically 100–5,000 people",
    "concert hall":       "typically 500–5,000 seats",
    "opera":              "typically 500–3,000 seats",
    "conference centre":  "typically 50–5,000 people",
    "events venue":       "typically 50–3,000 people",
    "conference_centre":  "typically 50–5,000 people",
    "exhibition centre":  "typically 500–50,000 people",
    "stadium":            "typically 5,000–90,000 people",
    "sports centre":      "typically 200–10,000 people",
    "sports hall":        "typically 200–5,000 people",
    "arena":              "typically 2,000–20,000 people",
    "community centre":   "typically 50–500 people",
    "social facility":    "typically 20–300 people",
    "public hall":        "typically 50–1,000 people",
    "hotel":              "conference rooms typically 20–1,000 people",
    "restaurant":         "typically 20–300 covers",
    "pub":                "typically 20–200 people",
    "bar":                "typically 50–500 people",
    "nightclub":          "typically 100–2,000 people",
    "museum":             "no fixed seated capacity — visit by arrangement",
    "gallery":            "no fixed seated capacity — visit by arrangement",
    "zoo":                "outdoor space, unlimited capacity",
    "aquarium":           "typically 100–500 per session",
    "park":               "outdoor space, flexible capacity",
}

# Venue types worth enriching with Place Details API (priority for capacity data)
_ENRICH_PRIORITY_TYPES = {
    "conference_centre", "conference centre", "events venue", "event venue",
    "sports hall", "sports centre", "stadium", "arena", "hotel", "theatre",
    "arts centre", "music venue", "concert hall", "exhibition centre", "hall",
    "public hall", "community centre",
}


# ── Geocoding ──────────────────────────────────────────────────────────────────

def get_city_coords(city: str) -> Optional[Tuple[float, float]]:
    """Return (lat, lon) for a city. Geoapify primary, Photon fallback."""
    if GEOAPIFY_API_KEY:
        try:
            resp = requests.get(
                "https://api.geoapify.com/v1/geocode/search",
                params={"text": city, "type": "city", "limit": 1, "apiKey": GEOAPIFY_API_KEY},
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

    return None


# ── Overpass (OpenStreetMap) ───────────────────────────────────────────────────

def fetch_overpass_venues(
    lat: float, lon: float, radius_m: int, categories: List[str]
) -> List[Dict]:
    """Fetch venues from Overpass API. Extracts full OSM tag set including capacity."""
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

    # Extra: named venues with explicit capacity tag
    parts += [
        f'node["capacity"]({bbox})["name"];',
        f'way["capacity"]({bbox})["name"];',
    ]

    query = "[out:json][timeout:90];\n(\n  " + "\n  ".join(parts) + "\n);\nout center tags;"

    resp = None
    for mirror in OVERPASS_MIRRORS:
        try:
            resp = requests.post(mirror, data={"data": query}, timeout=120)
            if resp.status_code == 200:
                break
        except Exception:
            continue

    if resp is None or resp.status_code != 200:
        return []

    # OSM amenity types that are never event venues
    _NON_VENUE_AMENITY = {
        "parking", "bicycle_parking", "fuel", "toilets", "charging_station",
        "recycling", "waste_disposal", "waste_basket", "post_box",
        "atm", "vending_machine", "bench", "shelter", "telephone",
    }

    try:
        venues = []
        for el in resp.json().get("elements", []):
            tags = el.get("tags", {})
            name = tags.get("name", "").strip()
            if not name:
                continue

            # Skip transport / infrastructure nodes picked up by the capacity query
            amenity_val = tags.get("amenity", "")
            leisure_val = tags.get("leisure", "")
            if amenity_val in _NON_VENUE_AMENITY:
                continue
            if leisure_val == "pitch":
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
                "diet_vegan": tags.get("diet:vegan", ""),
                "description": tags.get("description", ""),
                "operator": tags.get("operator", ""),
                "fee": tags.get("fee", ""),
                "lat": vlat,
                "lon": vlon,
                "place_id": None,
                "rating": None,
                "price": "",
                "source": "OpenStreetMap",
            })
        return venues
    except Exception:
        return []


def _parse_capacity(raw: str) -> str:
    """Convert raw capacity string to a clean human-readable label."""
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


# ── Geoapify Places API ───────────────────────────────────────────────────────

def fetch_geoapify_venues(
    lat: float, lon: float, radius_m: int, categories: List[str]
) -> List[Dict]:
    """
    Fetch venues from Geoapify Places API.
    Reads the full datasource.raw block so capacity, email, wheelchair etc. are captured.
    """
    if not GEOAPIFY_API_KEY:
        return []

    geo_cats = []
    for cat in categories:
        geo_cats.extend(CATEGORY_TAGS.get(cat, {}).get("geoapify", "").split(","))
    geo_cats = [c.strip() for c in set(geo_cats) if c.strip()]

    params: Dict = {
        "filter": f"circle:{lon},{lat},{min(radius_m, 50000)}",
        "limit": 200,
        "apiKey": GEOAPIFY_API_KEY,
    }
    if geo_cats:
        params["categories"] = ",".join(geo_cats)

    try:
        resp = requests.get(
            "https://api.geoapify.com/v2/places",
            params=params,
            timeout=20,
        )
        resp.raise_for_status()
        venues = []
        for feature in resp.json().get("features", []):
            props = feature.get("properties", {})
            name = props.get("name", "").strip()
            if not name:
                continue

            # Raw OSM tags — richest source for capacity / contact data
            raw = props.get("datasource", {}).get("raw", {})
            contact = props.get("contact", {})
            facilities = props.get("facilities", {})
            catering = props.get("catering", {})
            accommodation = props.get("accommodation", {})

            cats = props.get("categories", [])
            cat_str = cats[0] if cats else ""
            type_label = cat_str.split(".")[-1].replace("_", " ").title() if cat_str else "Venue"

            capacity_raw = (
                str(catering.get("capacity", ""))
                or raw.get("capacity", "")
                or raw.get("maxcapacity", "")
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
            wheelchair = facilities.get("wheelchair") or raw.get("wheelchair", "")
            internet = raw.get("internet_access", "") or raw.get("wifi", "")
            outdoor = raw.get("outdoor_seating", "")
            stars = (
                str(accommodation.get("stars", "")) or
                raw.get("stars", "") or
                raw.get("accommodation:stars", "")
            )
            rooms = str(accommodation.get("rooms", "")) or raw.get("rooms", "")
            cuisine = (
                raw.get("cuisine", "").replace(";", ", ") or
                str(catering.get("cuisine", ""))
            )

            venues.append({
                "name": name,
                "type": type_label,
                "address": props.get("formatted", ""),
                "phone": phone,
                "email": email,
                "website": website,
                "opening_hours": raw.get("opening_hours", "") or props.get("opening_hours", ""),
                "cuisine": cuisine,
                "capacity": capacity_str,
                "capacity_raw": capacity_raw,
                "wheelchair": wheelchair,
                "internet_access": internet,
                "outdoor_seating": outdoor,
                "stars": stars,
                "rooms": rooms,
                "description": raw.get("description", ""),
                "operator": raw.get("operator", ""),
                "fee": raw.get("fee", ""),
                "diet_vegan": raw.get("diet:vegan", ""),
                "lat": props.get("lat"),
                "lon": props.get("lon"),
                "place_id": props.get("place_id", ""),
                "rating": None,
                "price": "",
                "source": "Geoapify",
            })
        return venues
    except Exception:
        return []


# ── Geoapify Place Details (enrichment) ───────────────────────────────────────

def _fetch_one_place_details(place_id: str) -> Optional[Dict]:
    """Fetch enriched details for a single Geoapify place_id. Returns a patch-dict."""
    if not GEOAPIFY_API_KEY or not place_id:
        return None
    try:
        resp = requests.get(
            "https://api.geoapify.com/v2/place-details",
            params={
                "id": place_id,
                "features": "details",
                "apiKey": GEOAPIFY_API_KEY,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return None

        features = resp.json().get("features", [])
        if not features:
            return None

        props = features[0].get("properties", {})
        raw = props.get("datasource", {}).get("raw", {})
        contact = props.get("contact", {})
        facilities = props.get("facilities", {})
        catering = props.get("catering", {})
        accommodation = props.get("accommodation", {})

        patch: Dict = {}

        capacity_raw = (
            str(catering.get("capacity", "")) or
            raw.get("capacity", "") or
            raw.get("maxcapacity", "")
        )
        if capacity_raw:
            patch["capacity"] = _parse_capacity(capacity_raw)
            patch["capacity_raw"] = capacity_raw

        for key, sources in [
            ("phone",         [contact.get("phone"), raw.get("phone"), raw.get("contact:phone")]),
            ("email",         [contact.get("email"), raw.get("email"), raw.get("contact:email")]),
            ("website",       [raw.get("website"), raw.get("contact:website"), contact.get("website")]),
            ("wheelchair",    [facilities.get("wheelchair"), raw.get("wheelchair")]),
            ("internet_access", [raw.get("internet_access"), raw.get("wifi")]),
            ("outdoor_seating", [raw.get("outdoor_seating")]),
            ("stars",         [str(accommodation.get("stars", "")), raw.get("stars", "")]),
            ("rooms",         [str(accommodation.get("rooms", "")), raw.get("rooms", "")]),
            ("opening_hours", [raw.get("opening_hours")]),
            ("description",   [raw.get("description")]),
        ]:
            val = next((v for v in sources if v and str(v).strip()), None)
            if val:
                patch[key] = str(val)

        return patch if patch else None
    except Exception:
        return None


def enrich_venues_with_details(venues: List[Dict], max_enrich: int = 60) -> List[Dict]:
    """
    Call the Geoapify Place Details API for priority venue types to fill
    missing capacity / contact / facilities. Uses a thread pool for speed.
    """
    if not GEOAPIFY_API_KEY:
        return venues

    indices_to_enrich = []
    for i, v in enumerate(venues):
        if len(indices_to_enrich) >= max_enrich:
            break
        vtype = v.get("type", "").lower()
        needs_capacity = not v.get("capacity")
        is_priority = any(t in vtype for t in _ENRICH_PRIORITY_TYPES)
        has_place_id = bool(v.get("place_id"))
        if (needs_capacity or is_priority) and has_place_id:
            indices_to_enrich.append(i)

    if not indices_to_enrich:
        return venues

    def _enrich(idx: int):
        patch = _fetch_one_place_details(venues[idx]["place_id"])
        return idx, patch

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_enrich, idx): idx for idx in indices_to_enrich}
        for future in as_completed(futures):
            try:
                idx, patch = future.result()
                if patch:
                    # Only fill fields that are currently empty
                    for k, val in patch.items():
                        if val and not venues[idx].get(k):
                            venues[idx][k] = val
            except Exception:
                pass

    return venues


# ── Foursquare ────────────────────────────────────────────────────────────────

def fetch_foursquare_venues(
    lat: float, lon: float, radius_m: int, categories: List[str]
) -> List[Dict]:
    """Fetch from Foursquare Places API (new endpoint: places-api.foursquare.com)."""
    if not FOURSQUARE_API_KEY:
        return []

    fsq_cats = list({CATEGORY_TAGS[c]["foursquare"] for c in categories if c in CATEGORY_TAGS})
    params: Dict = {"ll": f"{lat},{lon}", "radius": min(radius_m, 100000), "limit": 50}
    if fsq_cats:
        params["categories"] = ",".join(fsq_cats)

    try:
        resp = requests.get(
            "https://places-api.foursquare.com/places/search",
            headers={
                "Authorization": f"Bearer {FOURSQUARE_API_KEY}",
                "X-Places-Api-Version": "2025-06-17",
                "Accept": "application/json",
            },
            params=params,
            timeout=15,
        )
        # Non-2xx auth/gone errors are non-fatal — OSM + Geoapify cover primary needs
        if resp.status_code in (401, 403, 410):
            return []
        resp.raise_for_status()
        venues = []
        for place in resp.json().get("results", []):
            location = place.get("location", {})
            cats = place.get("categories", [])
            # New API: lat/lon are top-level fields (not nested in geocodes)
            p_lat = place.get("latitude") or place.get("geocodes", {}).get("main", {}).get("latitude")
            p_lon = place.get("longitude") or place.get("geocodes", {}).get("main", {}).get("longitude")
            addr_parts = [
                location.get("address", ""),
                location.get("locality", ""),
                location.get("region", ""),
                location.get("postcode", ""),
                location.get("country", ""),
            ]
            address = ", ".join(p for p in addr_parts if p)
            venues.append({
                "name": place.get("name", ""),
                "type": cats[0].get("name", "Venue") if cats else "Venue",
                "address": address,
                "phone": place.get("tel", ""),
                "email": place.get("email", ""),
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
                "description": place.get("description", ""),
                "operator": "",
                "fee": "",
                "diet_vegan": "",
                "lat": p_lat,
                "lon": p_lon,
                "place_id": place.get("fsq_place_id", place.get("fsq_id", "")),
                "rating": place.get("rating"),
                "price": str(place.get("price", "")) if place.get("price") else "",
                "source": "Foursquare",
            })
        return venues
    except Exception:
        return []


# ── Venue → rich text chunk ───────────────────────────────────────────────────

def venue_to_text(venue: Dict, city: str) -> str:
    """
    Convert a venue dict to a rich, searchable text chunk for embedding.
    Capacity is placed prominently and repeated in multiple phrasings so
    queries like "300 person venue" or "can accommodate 500 guests" match.
    """
    v = venue
    lines = [
        f"Venue: {v['name']}",
        f"City: {city}",
        f"Category / Type: {v.get('type', 'Venue')}",
    ]

    # ── Capacity (most critical for event-planning queries) ───────────────────
    cap = str(v.get("capacity", "")).strip()
    vtype_lower = v.get("type", "").lower()
    estimated = CAPACITY_ESTIMATES.get(vtype_lower, "")

    if cap:
        lines.append(f"Capacity: {cap} (confirmed)")
        lines.append(f"Maximum Occupancy: {cap}")
        lines.append(f"This venue can accommodate {cap}.")
        lines.append(f"Suitable for events with up to {cap}.")
        if estimated:
            lines.append(f"Venue type typical range: {estimated}.")
    else:
        if estimated:
            lines.append(f"Capacity: Not confirmed — contact venue. Typical for a {vtype_lower}: {estimated}.")
            lines.append(f"Estimated range for this type of venue: {estimated}.")
        else:
            lines.append("Capacity: Not specified — please contact the venue for exact occupancy limits.")

    # ── Accommodation specifics ───────────────────────────────────────────────
    if v.get("stars"):
        lines.append(f"Star Rating: {v['stars']} stars")
    if v.get("rooms"):
        lines.append(f"Number of Rooms / Spaces: {v['rooms']}")

    # ── Contact & location ────────────────────────────────────────────────────
    for field, label in [
        ("address",       "Address"),
        ("phone",         "Phone"),
        ("email",         "Email"),
        ("website",       "Website"),
        ("opening_hours", "Opening Hours"),
        ("operator",      "Operated by"),
        ("description",   "Description"),
    ]:
        val = str(v.get(field, "")).strip()
        if val:
            lines.append(f"{label}: {val}")

    # ── Food & dietary ────────────────────────────────────────────────────────
    if v.get("cuisine"):
        lines.append(f"Cuisine: {v['cuisine']}")
    if v.get("diet_vegan"):
        lines.append(f"Vegan Options: {v['diet_vegan']}")

    # ── Facilities ────────────────────────────────────────────────────────────
    if v.get("wheelchair") in ("yes", "limited", "designated"):
        lines.append(f"Wheelchair Accessible: {v['wheelchair']}")
    if v.get("internet_access") in ("yes", "wlan", "wifi", "free"):
        lines.append("Internet / Wi-Fi: Available")
    if v.get("outdoor_seating") == "yes":
        lines.append("Outdoor Seating: Yes")
    if v.get("fee") in ("yes", "no"):
        lines.append(f"Entry Fee: {v['fee']}")

    if v.get("rating"):
        lines.append(f"Rating: {v['rating']}")
    if v.get("price"):
        lines.append(f"Price Level: {v['price']}")

    if v.get("lat") and v.get("lon"):
        lines.append(f"Coordinates: {float(v['lat']):.5f}, {float(v['lon']):.5f}")
    lines.append(f"Data Source: {v.get('source', 'API')}")

    return "\n".join(lines)


# ── Aggregator ────────────────────────────────────────────────────────────────

def fetch_all_city_venues(
    city: str,
    categories: List[str],
    radius_km: int = 5,
    use_foursquare: bool = True,
    use_geoapify: bool = True,
    enrich_details: bool = True,
    max_venues: int = 500,
    coords: Optional[Tuple[float, float]] = None,
) -> Tuple[List[Dict], Dict[str, int]]:
    """
    Fetch and deduplicate venues for a city from all configured sources.
    Returns (venues, source_counts).

    enrich_details=True calls Geoapify Place Details for priority venue types
    to fill missing capacity / contact / facilities via a thread pool.
    """
    if coords is None:
        coords = get_city_coords(city)
    if not coords:
        return [], {}

    lat, lon = coords
    radius_m = radius_km * 1000
    all_venues: List[Dict] = []
    source_counts: Dict[str, int] = {}

    # 1. OpenStreetMap via Overpass (richest tag data, always first)
    osm = fetch_overpass_venues(lat, lon, radius_m, categories)
    if osm:
        all_venues.extend(osm)
        source_counts["OpenStreetMap"] = len(osm)

    # 2. Geoapify Places (fixed categories, full datasource.raw extraction)
    if use_geoapify and GEOAPIFY_API_KEY:
        time.sleep(0.3)
        geo = fetch_geoapify_venues(lat, lon, radius_m, categories)
        if geo:
            all_venues.extend(geo)
            source_counts["Geoapify"] = len(geo)

    # 3. Foursquare
    if use_foursquare and FOURSQUARE_API_KEY:
        time.sleep(0.3)
        fsq = fetch_foursquare_venues(lat, lon, radius_m, categories)
        if fsq:
            all_venues.extend(fsq)
            source_counts["Foursquare"] = len(fsq)

    # Deduplicate by normalised name
    seen: set = set()
    unique: List[Dict] = []
    for v in all_venues:
        key = re.sub(r"[^a-z0-9]", "", v["name"].lower())
        if key and key not in seen:
            seen.add(key)
            unique.append(v)
        if len(unique) >= max_venues:
            break

    # 4. Enrich priority venue types via Place Details API
    if enrich_details and use_geoapify and unique:
        unique = enrich_venues_with_details(unique, max_enrich=60)
        with_cap = sum(1 for v in unique if v.get("capacity"))
        source_counts["with_capacity"] = with_cap

    return unique, source_counts
