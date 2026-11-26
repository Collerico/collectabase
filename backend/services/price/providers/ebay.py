import base64
import logging
import math
import os
import re
import time
from collections import Counter
from typing import Optional

import httpx

from ..utils import _env_any, _trim_outliers_and_median

_EBAY_TOKEN_CACHE = {"token": None, "expires_at": 0.0}
logger = logging.getLogger(__name__)

# ── Base bundle / defect keywords (always filtered) ──
_BUNDLE_KEYWORDS_ALWAYS = {
    "bundle", "lot", "sammlung", "konvolut", "paket", "set of",
    "wholesale", "bulk", "joblot", "job lot",
    "defekt", "defective", "broken", "not working", "für bastler",
    "ersatzteile", "parts only", "as is",
}

# ── Additional keywords filtered only for *games* ──
_BUNDLE_KEYWORDS_GAME_ONLY = {
    "console", "konsole", "system", "hardware",
    "controller", "zubehör", "accessory", "accessories",
}

# ── Item types that are collectibles (not platform-bound games) ──
_COLLECTIBLE_TYPES = {"funko", "figure", "comic", "vinyl", "manga", "art"}

# ── Item types where platform name should NOT be appended to query ──
_NO_PLATFORM_TYPES = {"funko", "figure", "comic", "vinyl", "manga", "art"}


def _ebay_credentials():
    client_id = _env_any("EBAY_CLIENT_ID", "EBAY_APP_ID", "EBAY_APPID", "EBAY_CLIENTID")
    client_secret = _env_any("EBAY_CLIENT_SECRET", "EBAY_SECRET", "EBAY_CLIENTSECRET", "EBAY_SECRET_KEY")
    if not client_id or not client_secret:
        return None, None
    return client_id, client_secret


def _normalise(text: str) -> str:
    """Lowercase and collapse whitespace for fuzzy matching."""
    return re.sub(r"\s+", " ", (text or "").lower().strip())


def _build_bundle_keywords(item_type: str) -> set:
    """Return the set of bundle keywords appropriate for the given item type."""
    keywords = set(_BUNDLE_KEYWORDS_ALWAYS)
    item_type_lower = (item_type or "game").lower()
    # Only add game-specific keywords (console, controller, etc.) for actual games
    if item_type_lower in ("game", ""):
        keywords |= _BUNDLE_KEYWORDS_GAME_ONLY
    # For collectibles, also filter "collection" to avoid "lot/collection" listings
    if item_type_lower not in _COLLECTIBLE_TYPES:
        keywords.add("collection")
    return keywords


def _title_exclusion_reason(item_title: str, game_title: str, item_type: str = "game") -> Optional[str]:
    """Explain why a title is not a single, relevant listing for the requested item."""
    norm_item = _normalise(item_title)
    norm_game = _normalise(game_title)

    for keyword in _build_bundle_keywords(item_type):
        if keyword in norm_item:
            return "keyword_bundle_or_defect"

    if re.search(r"\d+\s*(spiele|games|titles|stück|stk)", norm_item):
        return "keyword_multiple_items"

    game_words = [word for word in norm_game.split() if len(word) >= 3]
    if game_words:
        matches = sum(1 for word in game_words if word in norm_item)
        if matches < max(1, len(game_words) * 0.5):
            return "title_not_relevant"
    return None


def _title_is_relevant(item_title: str, game_title: str, platform_name: str, item_type: str = "game") -> bool:
    """Keep the existing boolean helper interface for callers and tests."""
    return _title_exclusion_reason(item_title, game_title, item_type) is None


def _condition_ids_for_item_type(item_type: str) -> set[str]:
    """Return condition IDs relevant to this Collectabase item type."""
    kind = (item_type or "game").strip().lower()
    if kind in {"game", ""}:
        # eBay's video-game categories use New, Like New, Very Good, Good,
        # and Acceptable condition IDs; retain legacy IDs used by game listings.
        return {"1000", "1500", "2750", "3000", "4000", "5000", "6000"}
    if kind in _COLLECTIBLE_TYPES:
        return {"1000", "1500", "2750", "3000"}
    if kind in {"console", "controller", "accessory"}:
        return {"1000", "1500", "2000", "2010", "2020", "2030", "2500", "2750", "3000"}
    return {"2750", "3000"}


def _amount_eur(amount) -> tuple[Optional[float], str]:
    """Parse a Browse API ConvertedAmount without assuming its currency."""
    if not isinstance(amount, dict) or amount.get("value") is None:
        return None, "missing"
    currency = str(amount.get("currency") or "").strip().upper()
    if currency != "EUR":
        return None, "currency_unknown" if not currency else "currency_unconverted"
    try:
        value = float(amount["value"])
    except (TypeError, ValueError):
        return None, "invalid"
    if not math.isfinite(value):
        return None, "invalid"
    return value, "ok"



async def get_ebay_token() -> Optional[str]:
    client_id, client_secret = _ebay_credentials()
    if not client_id or not client_secret:
        return None

    now = time.time()
    cached = _EBAY_TOKEN_CACHE.get("token")
    if cached and _EBAY_TOKEN_CACHE.get("expires_at", 0) > now:
        return cached

    basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    headers = {
        "Authorization": f"Basic {basic}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    body = "grant_type=client_credentials&scope=https://api.ebay.com/oauth/api_scope"

    try:
        async with httpx.AsyncClient(timeout=12) as client:
            res = await client.post("https://api.ebay.com/identity/v1/oauth2/token", headers=headers, content=body)
        if res.status_code >= 400:
            logger.warning("eBay OAuth request failed with HTTP %s", res.status_code)
            return None

        data = res.json()
        token = data.get("access_token")
        expires_in = int(data.get("expires_in", 0))
        if not token:
            return None

        _EBAY_TOKEN_CACHE["token"] = token
        _EBAY_TOKEN_CACHE["expires_at"] = now + max(expires_in - 60, 60)
        return token
    except Exception as exc:
        logger.warning("eBay OAuth request failed (%s)", type(exc).__name__)
        return None


async def fetch_ebay_market_price(title: str, platform_name: str, item_type: str = "game"):
    token = await get_ebay_token()
    if not token:
        return None

    item_type_lower = (item_type or "game").strip().lower()
    if item_type_lower in _NO_PLATFORM_TYPES:
        query = title.strip()
    else:
        query = " ".join(part for part in [title, platform_name] if part).strip()

    # Filter conditions locally so diagnostics can count each excluded result.
    # Use Browse's documented maximum page size to reduce losses before local filtering.
    params = {"q": query, "limit": "200"}
    marketplace_id = os.getenv("EBAY_MARKETPLACE_ID", "EBAY_IT").strip().upper() or "EBAY_IT"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": marketplace_id,
    }
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            res = await client.get(
                "https://api.ebay.com/buy/browse/v1/item_summary/search",
                params=params,
                headers=headers,
            )

        if res.status_code >= 400:
            logger.warning("eBay Browse search failed with HTTP %s", res.status_code)
            return None

        payload = res.json()
        items = payload.get("itemSummaries", []) or []
        if not isinstance(items, list):
            items = []

        exclusions = Counter()
        prices: list[float] = []
        accepted_conditions = _condition_ids_for_item_type(item_type_lower)

        for item in items:
            if not isinstance(item, dict):
                exclusions["invalid_item"] += 1
                continue

            condition_id = str(item.get("conditionId") or "").strip()
            if not condition_id:
                exclusions["condition_missing"] += 1
                continue
            if condition_id not in accepted_conditions:
                exclusions["condition_excluded"] += 1
                continue

            title_reason = _title_exclusion_reason(item.get("title", ""), title, item_type_lower)
            if title_reason:
                exclusions[title_reason] += 1
                continue

            item_price, price_reason = _amount_eur(item.get("price"))
            if price_reason != "ok":
                exclusions[f"price_{price_reason}"] += 1
                continue
            if item_price <= 0:
                exclusions["price_invalid"] += 1
                continue

            prices.append(item_price)

        median_price, trimmed, min_price, max_price = _trim_outliers_and_median(prices)
        used_count = len(trimmed)
        logger.info(
            "eBay price search diagnostics marketplace=%s api_returned=%d api_page_limit=%s eligible=%d used=%d trimmed_outliers=%d exclusions=%s",
            marketplace_id,
            len(items),
            params["limit"],
            len(prices),
            used_count,
            max(len(prices) - used_count, 0),
            ",".join(f"{reason}:{count}" for reason, count in sorted(exclusions.items())) or "none",
        )
        if median_price is None:
            return None

        return {
            "market_price": round(median_price, 2),
            "sample_size": used_count,
            "price_min": round(min_price, 2),
            "price_max": round(max_price, 2),
        }
    except Exception as exc:
        logger.warning("eBay Browse search failed (%s)", type(exc).__name__)
        return None
