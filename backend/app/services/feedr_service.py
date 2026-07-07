"""
Feedr.co catering vendor fetcher and indexer.

Feedr.co is powered by CaterDesk (caterdesk.com).  Their GraphQL API is
public and returns rich vendor data — no Playwright required.

API: https://gql.r53.prod.caterdesk.com/graphql
Feedr tenant ID: 64c934f210528b243cba6142

Scraped vendors are stored in ChromaDB as 2 chunks per vendor:
  1. Rich text  — for semantic search
  2. Raw JSON   — for exact data retrieval (chunk_type="raw_json")
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_GEOAPIFY_KEY = os.getenv("GEOAPIFY_API_KEY", "")

_GQL_URL = "https://gql.r53.prod.caterdesk.com/graphql"
_FEEDR_TENANT_ID = "64c934f210528b243cba6142"

_GQL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Content-Type": "application/json",
    "Origin": "https://feedr.co",
    "Referer": "https://feedr.co/en-gb/office-catering/vendors",
    "x-tenant-id": _FEEDR_TENANT_ID,
}

# GraphQL query — serviceType enum must be inline (not a variable)
_VENDORS_QUERY = """
query FeedrVendors($lat: Float!, $lng: Float!, $tenantId: String, $limit: Int, $offset: Int) {
  vendors(
    limit: $limit
    offset: $offset
    filters: {
      isGMDiscoverable: true
      tenantId: $tenantId
      serviceType: gm
      location: { latitude: $lat, longitude: $lng }
    }
  ) {
    rows {
      id
      companyName
      permalink
      descriptionShort
      guidePrice
      priceLevel
      rating
      totalRatings
      logo
      keywords
      tags { name label type groupName }
      locations { city postcode country line1 lat lng }
    }
    pageInfo { count limit offset }
  }
}
"""


# ── Haversine distance ────────────────────────────────────────────────────────

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two (lat, lon) points."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return round(R * 2 * math.asin(math.sqrt(a)), 2)


# ── Geocoding ─────────────────────────────────────────────────────────────────

def geocode_city(city: str) -> Optional[Tuple[float, float]]:
    """Geocode a city name to (lat, lon) via Geoapify or Nominatim fallback."""
    import requests as _req

    if _GEOAPIFY_KEY:
        try:
            r = _req.get(
                "https://api.geoapify.com/v1/geocode/search",
                params={"text": city, "lang": "en", "limit": 1, "apiKey": _GEOAPIFY_KEY},
                timeout=10,
            )
            feats = r.json().get("features", [])
            if feats:
                lon, lat = feats[0]["geometry"]["coordinates"]
                return float(lat), float(lon)
        except Exception as exc:
            logger.debug("Geoapify geocode failed for '%s': %s", city, exc)

    # Nominatim fallback
    try:
        time.sleep(1.1)
        r = _req.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": city, "format": "json", "limit": 1, "countrycodes": "gb"},
            timeout=10,
            headers={"User-Agent": "EventManagerApp/1.0 (events@company.com)"},
        )
        results = r.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception as exc:
        logger.debug("Nominatim geocode failed for '%s': %s", city, exc)

    return None


# ── GraphQL fetching ──────────────────────────────────────────────────────────

def _fetch_page(lat: float, lon: float, limit: int, offset: int) -> Dict[str, Any]:
    """Fetch one page of vendors from the CaterDesk GraphQL API."""
    import requests as _req
    payload = {
        "operationName": "FeedrVendors",
        "query": _VENDORS_QUERY,
        "variables": {
            "lat": float(lat),
            "lng": float(lon),
            "tenantId": _FEEDR_TENANT_ID,
            "limit": limit,
            "offset": offset,
        },
    }
    r = _req.post(_GQL_URL, json=payload, headers=_GQL_HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_feedr_vendors(
    lat: float,
    lon: float,
    city: str,
    max_vendors: int = 300,
) -> List[Dict[str, Any]]:
    """
    Fetch catering vendors near (lat, lon) from the Feedr/CaterDesk GraphQL API.
    Paginates automatically.  Returns normalised vendor dicts.
    """
    page_size = 50
    offset = 0
    all_vendors: List[Dict[str, Any]] = []
    total: Optional[int] = None

    while True:
        try:
            result = _fetch_page(lat, lon, page_size, offset)
        except Exception as exc:
            logger.warning("Feedr GraphQL fetch failed at offset %d: %s", offset, exc)
            break

        errors = result.get("errors")
        if errors:
            logger.warning("Feedr GraphQL errors: %s", errors[:3])
            break

        data = (result.get("data") or {}).get("vendors") or {}
        rows = data.get("rows") or []
        page_info = data.get("pageInfo") or {}

        if total is None:
            total = page_info.get("count", 0)
            logger.info("Feedr.co: %d total vendors available near (%.4f, %.4f)", total, lat, lon)

        for row in rows:
            vendor = _normalise_vendor(row, city)
            all_vendors.append(vendor)

        offset += len(rows)
        if not rows or offset >= min(total or 0, max_vendors):
            break

    logger.info("Feedr.co: fetched %d vendors", len(all_vendors))
    return all_vendors


def _normalise_vendor(row: Dict[str, Any], city: str) -> Dict[str, Any]:
    """Convert a CaterDesk GraphQL vendor row to our standard vendor dict."""
    tags = row.get("tags") or []
    tag_names = [str(t.get("name")) for t in tags if isinstance(t, dict) and t.get("name")]

    # Extract cuisine hint from keywords and tags
    cuisine_keywords = [
        "mexican", "italian", "indian", "chinese", "japanese", "thai",
        "greek", "turkish", "lebanese", "mediterranean", "british", "american",
        "korean", "vietnamese", "caribbean", "middle eastern", "egyptian",
        "german", "sushi", "pizza", "burger", "halal", "vegan", "vegetarian",
    ]
    cuisine = ""
    search_text = " ".join([
        row.get("keywords") or "",
        row.get("descriptionShort") or "",
    ] + tag_names).lower()
    for kw in cuisine_keywords:
        if kw in search_text:
            cuisine = kw
            break

    # Extract dietary from tags
    dietary_keywords = ["vegan", "vegetarian", "halal", "kosher", "gluten-free", "dairy-free", "nut-free"]
    specializations = [kw for kw in dietary_keywords if kw.replace("-", " ") in search_text or kw in search_text]

    # Primary location (first one)
    locations = row.get("locations") or []
    primary_loc = locations[0] if locations else {}

    # Price level → rough price-per-head estimate (£ per meal)
    price_level = row.get("priceLevel") or 0
    price_map = {1: "£", 2: "££", 3: "£££", 4: "££££"}
    price_range = price_map.get(price_level, "")

    guide_price = row.get("guidePrice")
    price_per_head = float(guide_price) if guide_price else None

    return {
        "name": (row.get("companyName") or "").strip()[:120],
        "type": "Catering",
        "vendor_type": "catering",
        "source": "feedr.co",
        "city": primary_loc.get("city") or city,
        "address": ", ".join(x for x in [
            str(primary_loc.get("line1") or ""),
            str(primary_loc.get("city") or ""),
            str(primary_loc.get("postcode") or ""),
            str(primary_loc.get("country") or ""),
        ] if x).strip(),
        "postcode": (primary_loc.get("postcode") or "").strip(),
        "lat": primary_loc.get("lat") or None,
        "lon": primary_loc.get("lng") or None,
        "website": f"https://feedr.co/en-gb/office-catering/vendors/{row.get('permalink', '')}",
        "logo": row.get("logo") or "",
        "cuisine": cuisine,
        "description": (row.get("descriptionShort") or "")[:400],
        "keywords": row.get("keywords") or "",
        "tags": tag_names,
        "specializations": specializations,
        "price_per_head": price_per_head,
        "price_range": price_range,
        "price_level": price_level,
        "rating": row.get("rating"),
        "total_ratings": row.get("totalRatings"),
        "delivery_available": True,
        "all_locations": [
            {
                "city": loc.get("city", ""),
                "postcode": loc.get("postcode", ""),
                "line1": loc.get("line1", ""),
                "lat": loc.get("lat"),
                "lng": loc.get("lng"),
            }
            for loc in locations
        ],
        "feedr_id": str(row.get("id", "")),
        "permalink": row.get("permalink") or "",
    }


# ── Vendor detail (full menu) ─────────────────────────────────────────────────

_VENDOR_DETAIL_QUERY = """
query VendorDetail($permalink: String!, $tenantId: String) {
  vendorByPermalink(permalink: $permalink, tenantId: $tenantId) {
    id companyName descriptionShort guidePrice priceLevel
    rating totalRatings logo quote tips lunchCutOff breakfastCutOff dinnerCutOff
    images { id secureUrl alt type }
    tags { name label type groupName }
    locations { city postcode country line1 lat lng }
    liveMenuItems(limit: 200) {
      pageInfo { count hasMore }
      items {
        _id name description price priceCustomerFacing image
        dietary_vegan dietary_vegetarian dietary_halal dietary_kosher
        dietary_lowcarb dietary_highprotein dietary_pescatarian dietary_non_hfss
        allergen_milk allergen_nuts allergen_peanuts allergen_cereals allergen_eggs
        allergen_fish allergen_crustaceans allergen_sesame allergen_sulphur
        macros_kcal macros_protein macros_carbs macros_fat
        isHot isCombo mealTag
      }
    }
  }
  vendorMenuCategories(vendorId: $vendorId, limit: 50) {
    rows { id name description itemCount }
  }
}
"""

# Simpler query without vendorMenuCategories (which needs a separate vendorId var)
_VENDOR_DETAIL_QUERY_SIMPLE = """
query VendorDetail($permalink: String!, $tenantId: String) {
  vendorByPermalink(permalink: $permalink, tenantId: $tenantId) {
    id companyName descriptionShort guidePrice priceLevel
    rating totalRatings logo quote tips lunchCutOff
    images { id secureUrl alt type }
    tags { name label type groupName }
    locations { city postcode country line1 lat lng }
    liveMenuItems(limit: 200) {
      pageInfo { count hasMore }
      items {
        _id name description price priceCustomerFacing image
        dietary_vegan dietary_vegetarian dietary_halal dietary_kosher
        dietary_lowcarb dietary_highprotein dietary_pescatarian
        allergen_milk allergen_nuts allergen_peanuts allergen_cereals allergen_eggs allergen_fish
        macros_kcal macros_protein macros_carbs macros_fat
        isHot isCombo mealTag
      }
    }
  }
}
"""


def fetch_vendor_detail(permalink: str) -> Dict[str, Any]:
    """
    Fetch full vendor info + all live menu items from the CaterDesk GraphQL API.
    Returns a normalized dict with vendor info and grouped menu.
    """
    import requests as _req

    payload = {
        "operationName": "VendorDetail",
        "query": _VENDOR_DETAIL_QUERY_SIMPLE,
        "variables": {"permalink": permalink, "tenantId": _FEEDR_TENANT_ID},
    }
    r = _req.post(_GQL_URL, json=payload, headers=_GQL_HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()

    if data.get("errors"):
        raise ValueError(f"GraphQL errors: {data['errors'][:2]}")

    v = (data.get("data") or {}).get("vendorByPermalink")
    if not v:
        raise ValueError(f"Vendor not found: {permalink}")

    # Fetch categories separately for proper category names
    cat_payload = {
        "operationName": "VMC",
        "query": "query VMC($vendorId: ID!) { vendorMenuCategories(vendorId: $vendorId, limit: 50) { rows { id name description itemCount } } }",
        "variables": {"vendorId": str(v["id"])},
    }
    categories: List[Dict[str, Any]] = []
    try:
        cr = _req.post(_GQL_URL, json=cat_payload, headers=_GQL_HEADERS, timeout=15)
        cdata = cr.json()
        if not cdata.get("errors"):
            categories = ((cdata.get("data") or {}).get("vendorMenuCategories") or {}).get("rows") or []
    except Exception:
        pass

    # Normalise images
    images = [
        img["secureUrl"]
        for img in (v.get("images") or [])
        if img.get("secureUrl")
    ]

    # Normalise menu items
    mi_page = v.get("liveMenuItems") or {}
    raw_items = mi_page.get("items") or []
    items = []
    for it in raw_items:
        price_p = it.get("priceCustomerFacing") or it.get("price") or 0
        items.append({
            "id": it.get("_id") or "",
            "name": (it.get("name") or "").strip(),
            "description": (it.get("description") or "").strip(),
            "price_pence": int(price_p) if price_p else 0,
            "price_gbp": round(int(price_p) / 100, 2) if price_p else 0.0,
            "image": it.get("image") or "",
            "is_vegan": bool(it.get("dietary_vegan")),
            "is_vegetarian": bool(it.get("dietary_vegetarian")),
            "is_halal": bool(it.get("dietary_halal")),
            "is_kosher": bool(it.get("dietary_kosher")),
            "is_low_carb": bool(it.get("dietary_lowcarb")),
            "is_high_protein": bool(it.get("dietary_highprotein")),
            "is_pescatarian": bool(it.get("dietary_pescatarian")),
            "allergen_milk": bool(it.get("allergen_milk")),
            "allergen_nuts": bool(it.get("allergen_nuts")),
            "allergen_peanuts": bool(it.get("allergen_peanuts")),
            "allergen_cereals": bool(it.get("allergen_cereals")),
            "allergen_eggs": bool(it.get("allergen_eggs")),
            "allergen_fish": bool(it.get("allergen_fish")),
            "kcal": it.get("macros_kcal") or None,
            "protein_g": it.get("macros_protein") or None,
            "carbs_g": it.get("macros_carbs") or None,
            "fat_g": it.get("macros_fat") or None,
            "is_hot": bool(it.get("isHot")),
            "is_combo": bool(it.get("isCombo")),
            "meal_tag": it.get("mealTag") or "",
        })

    # Group items by meal_tag (fallback grouping when no category link)
    _MEAL_TAG_LABELS = {
        "breakfast": "Breakfast",
        "brunch": "Brunch",
        "lunch": "Lunch",
        "dinner": "Dinner",
        "snack": "Snacks",
        "snacks": "Snacks",
        "dessert": "Desserts",
        "drink": "Drinks",
        "drinks": "Drinks",
        "fruit": "Fresh Fruit",
        "salad": "Salads",
        "protein": "Protein",
        "sandwich": "Sandwiches",
        "wrap": "Wraps",
        "bowl": "Bowls",
    }
    grouped: Dict[str, List] = {}
    for it in items:
        key = _MEAL_TAG_LABELS.get(it["meal_tag"], it["meal_tag"].title() if it["meal_tag"] else "Other")
        grouped.setdefault(key, []).append(it)

    # Normalise tags
    tags = v.get("tags") or []
    tag_names = [str(t.get("name")) for t in tags if isinstance(t, dict) and t.get("name")]

    # Locations
    locations = v.get("locations") or []
    primary_loc = locations[0] if locations else {}
    address = ", ".join(x for x in [
        str(primary_loc.get("line1") or ""),
        str(primary_loc.get("city") or ""),
        str(primary_loc.get("postcode") or ""),
    ] if x).strip()

    # Price level
    price_level = v.get("priceLevel") or 0
    price_map = {1: "£", 2: "££", 3: "£££", 4: "££££"}

    return {
        "id": str(v.get("id") or ""),
        "name": (v.get("companyName") or "").strip(),
        "permalink": permalink,
        "website": f"https://feedr.co/en-gb/office-catering/vendors/{permalink}",
        "description": (v.get("descriptionShort") or "").strip(),
        "logo": v.get("logo") or "",
        "images": images,
        "quote": (v.get("quote") or "").strip(),
        "tips": (v.get("tips") or "").strip(),
        "lunch_cutoff": v.get("lunchCutOff"),
        "guide_price": v.get("guidePrice"),
        "price_level": price_level,
        "price_range": price_map.get(price_level, ""),
        "rating": v.get("rating"),
        "total_ratings": v.get("totalRatings"),
        "address": address,
        "tags": tag_names,
        "categories": categories,
        "menu_item_count": mi_page.get("pageInfo", {}).get("count") or len(items),
        "menu_has_more": bool((mi_page.get("pageInfo") or {}).get("hasMore")),
        "menu_items": items,
        "menu_grouped": grouped,
    }


# ── Nearby vendor search ─────────────────────────────────────────────────────

def find_nearby_vendors(
    lat: float,
    lon: float,
    max_results: int = 20,
) -> List[Dict[str, Any]]:
    """
    Fetch catering vendors near (lat, lon) from Feedr.co via CaterDesk GraphQL,
    calculate real distance for each vendor, and return sorted closest-first.
    """
    vendors = fetch_feedr_vendors(lat, lon, city="", max_vendors=max_results)
    for v in vendors:
        v_lat = v.get("lat")
        v_lon = v.get("lon")
        if v_lat is not None and v_lon is not None:
            v["distance_km"] = haversine_km(lat, lon, float(v_lat), float(v_lon))
        else:
            v["distance_km"] = None

    vendors.sort(key=lambda v: v.get("distance_km") if v.get("distance_km") is not None else 999)
    return vendors


# ── Catering group matching ───────────────────────────────────────────────────

_NON_VEG_ALIASES = {"non-veg", "non_veg", "non veg", "regular", "standard", ""}


def match_vendors_for_groups(
    lat: float,
    lon: float,
    groups: List[Dict[str, Any]],
    max_vendors: int = 50,
) -> Dict[str, Any]:
    """
    Fetch nearby vendors and match each dietary group to suitable vendors.
    Returns per-group matches with estimated costs based on headcount × price_per_head.
    """
    all_vendors = find_nearby_vendors(lat, lon, max_results=max_vendors)

    result_groups = []
    for grp in groups:
        label = grp.get("label") or "Group"
        count = int(grp.get("count") or 0)
        dtype = (grp.get("dietary_type") or "").lower().strip()

        if dtype in _NON_VEG_ALIASES:
            # Non-veg (no restriction) — any vendor qualifies
            matched = list(all_vendors)
        else:
            matched = []
            for v in all_vendors:
                specs = [s.lower() for s in (v.get("specializations") or [])]
                tags = [t.lower() for t in (v.get("tags") or [])]
                dtype_norm = dtype.replace("-", " ")
                if (dtype in specs
                        or dtype_norm in specs
                        or dtype in tags
                        or dtype_norm in tags):
                    matched.append(v)

        # Add estimated cost per vendor
        enriched = []
        for v in matched:
            pph = v.get("price_per_head")
            if pph and count > 0:
                est = round(float(pph) * count, 2)
                est_str = f"£{est:,.0f}"
            else:
                est = None
                est_str = None
            enriched.append({**v, "estimated_cost": est, "estimated_cost_str": est_str})

        result_groups.append({
            "label": label,
            "count": count,
            "dietary_type": dtype,
            "matched_vendors": enriched,
            "match_count": len(enriched),
        })

    return {
        "groups": result_groups,
        "all_vendors": all_vendors,
        "total_vendors_fetched": len(all_vendors),
    }


# ── Text conversion ───────────────────────────────────────────────────────────

def vendor_to_text(v: Dict[str, Any], city: str) -> str:
    """Convert a Feedr vendor dict to a rich text chunk for ChromaDB."""
    parts = [f"Catering Vendor: {v.get('name', 'Unknown')}"]
    parts.append("Platform: Feedr.co (office catering marketplace)")
    c = v.get("city") or city
    if c:
        parts.append(f"City: {c}")
    if v.get("address"):
        parts.append(f"Address: {v['address']}")
    if v.get("cuisine"):
        parts.append(f"Cuisine: {v['cuisine'].title()}")
    if v.get("keywords"):
        parts.append(f"Keywords: {v['keywords']}")
    if v.get("tags"):
        parts.append(f"Tags: {', '.join(v['tags'][:10])}")
    if v.get("price_per_head"):
        parts.append(f"Price per head: £{v['price_per_head']:.2f}")
    elif v.get("price_range"):
        parts.append(f"Price level: {v['price_range']}")
    if v.get("specializations"):
        parts.append(f"Dietary options: {', '.join(v['specializations'])}")
    if v.get("rating"):
        parts.append(f"Rating: {v['rating']:.1f} ({v.get('total_ratings', 0)} reviews)")
    parts.append("Delivery: available (office catering delivery platform)")
    if v.get("description"):
        parts.append(f"Description: {v['description']}")
    if v.get("website"):
        parts.append(f"Website: {v['website']}")
    # List all locations if vendor has multiple
    locs = v.get("all_locations") or []
    if len(locs) > 1:
        loc_strs = [f"{loc.get('city','')} {loc.get('postcode','')}".strip() for loc in locs[:5]]
        parts.append(f"Locations: {', '.join(loc_strs)}")
    return "\n".join(parts)


# ── Indexing ──────────────────────────────────────────────────────────────────

def index_feedr_vendors(
    db: Session,
    user_id: int,
    vendors: List[Dict[str, Any]],
    city: str,
    replace_existing: bool = True,
) -> Tuple[Optional[int], Optional[str]]:
    """
    Index Feedr.co vendors into a dedicated ChromaDB collection.
    Collection name: feedr_u{user_id}_{city_slug}
    Each vendor → 2 chunks: rich text + raw JSON.
    """
    from app.models.indexed_source import IndexedSource
    from app.services import rag_service

    if not vendors:
        return None, "No vendors to index."

    city_slug = re.sub(r"[^a-z0-9]", "_", city.lower().strip())
    city_slug = re.sub(r"_+", "_", city_slug).strip("_")[:30]
    collection_name = f"feedr_u{user_id}_{city_slug}"
    source_name = f"Feedr.co Catering Vendors — {city.title()}"

    source_id: Optional[int] = None
    try:
        if replace_existing:
            existing = (
                db.query(IndexedSource)
                .filter(
                    IndexedSource.user_id == user_id,
                    IndexedSource.collection_name == collection_name,
                )
                .first()
            )
            if existing:
                rag_service.delete_collection(existing.collection_name)
                db.delete(existing)
                db.commit()

        chunks: List[str] = []
        metadatas: List[dict] = []

        for i, v in enumerate(vendors):
            # Chunk 1: rich semantic text
            chunks.append(vendor_to_text(v, city))
            metadatas.append({
                "source": source_name,
                "city": city,
                "chunk_type": "vendor",
                "chunk_index": i,
                "vendor_name": v.get("name", ""),
                "cuisine": v.get("cuisine", ""),
                "api_source": "feedr.co",
                "delivery_available": "True",
                "price_per_head": str(v.get("price_per_head") or ""),
                "rating": str(v.get("rating") or ""),
                "lat": str(v.get("lat") or ""),
                "lon": str(v.get("lon") or ""),
                "website": str(v.get("website") or ""),
            })

            # Chunk 2: raw JSON
            chunks.append(
                f"RAW JSON for {v.get('name', 'vendor')} ({city}):\n"
                + json.dumps(v, ensure_ascii=False)
            )
            metadatas.append({
                "source": source_name,
                "city": city,
                "chunk_type": "raw_json",
                "chunk_index": i,
                "vendor_name": v.get("name", ""),
                "api_source": "feedr.co",
            })

        ids = [f"{collection_name}_{i}" for i in range(len(chunks))]

        source = IndexedSource(
            user_id=user_id,
            source_name=source_name,
            source_type="feedr",
            chunk_count=0,
            status="pending",
            collection_name=collection_name,
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        source_id = source.id

        count = rag_service.add_to_collection(collection_name, chunks, metadatas, ids)

        source.chunk_count = count
        source.status = "indexed"
        source.indexed_at = datetime.utcnow()
        db.commit()
        return source_id, None

    except Exception as exc:
        db.rollback()
        if source_id is not None:
            try:
                src = db.query(IndexedSource).filter(IndexedSource.id == source_id).first()
                if src:
                    src.status = "failed"
                    db.commit()
            except Exception:
                pass
        return None, str(exc)
