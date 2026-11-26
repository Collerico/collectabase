import asyncio
import base64
import logging
import math
import os
import re
import time
from collections import Counter
from typing import Optional

import httpx

from ....database import get_app_meta_many
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

_MAX_DEEP_SEARCH_TEXT_QUERIES = 8
_DEEP_SEARCH_DURATION_SECONDS = 35
_DEEP_SEARCH_REQUEST_TIMEOUT_SECONDS = 8


def _ebay_credentials():
    client_id = _env_any("EBAY_CLIENT_ID", "EBAY_APP_ID", "EBAY_APPID", "EBAY_CLIENTID")
    client_secret = _env_any("EBAY_CLIENT_SECRET", "EBAY_SECRET", "EBAY_CLIENTSECRET", "EBAY_SECRET_KEY")
    if not client_id or not client_secret:
        return None, None
    return client_id, client_secret


def _normalise(text: str) -> str:
    """Lowercase and collapse whitespace for fuzzy matching."""
    return re.sub(r"\s+", " ", (text or "").lower().strip())


def _search_clock() -> float:
    """Monotonic clock wrapper so search deadlines can be tested deterministically."""
    return time.monotonic()


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


def _ebay_shipping_enabled() -> bool:
    meta = get_app_meta_many(["ebay_include_shipping"])
    value = str(meta.get("ebay_include_shipping", "0")).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _shipping_price(item) -> tuple[Optional[float], str]:
    """Return the cheapest valid EUR shipping amount and a diagnostic status."""
    options = item.get("shippingOptions")
    if not isinstance(options, list) or not options:
        return None, "shipping_missing"

    valid_prices = []
    option_reasons = Counter()
    for option in options:
        shipping = option.get("shippingCost") if isinstance(option, dict) else None
        value, reason = _amount_eur(shipping)
        if reason != "ok":
            option_reasons[reason] += 1
            continue
        if value < 0:
            option_reasons["invalid"] += 1
            continue
        valid_prices.append(value)

    if valid_prices:
        price = min(valid_prices)
        return price, "shipping_free" if price == 0 else "shipping_included"
    if option_reasons["currency_unconverted"]:
        return None, "shipping_currency_unconverted"
    if option_reasons["currency_unknown"]:
        return None, "shipping_currency_unknown"
    if option_reasons["invalid"]:
        return None, "shipping_cost_invalid"
    return None, "shipping_cost_missing"


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


def _normalise_gtin(value) -> Optional[str]:
    """Return a checksum-valid UPC/EAN/ISBN GTIN suitable for Browse search."""
    raw = str(value or "").strip().upper().replace("-", "").replace(" ", "")
    if len(raw) == 10 and re.fullmatch(r"[0-9]{9}[0-9X]", raw, flags=re.ASCII):
        isbn_check = sum(int(digit) * (10 - index) for index, digit in enumerate(raw[:9]))
        isbn_check += 10 if raw[9] == "X" else int(raw[9])
        if isbn_check % 11 != 0:
            return None
        isbn13_body = "978" + raw[:9]
        check = (10 - sum(
            int(digit) * (3 if (len(isbn13_body) - index) % 2 else 1)
            for index, digit in enumerate(isbn13_body)
        ) % 10) % 10
        return isbn13_body + str(check)

    if not re.fullmatch(r"[0-9]+", raw, flags=re.ASCII):
        return None
    digits = raw
    if len(digits) not in {8, 12, 13}:
        return None
    body = digits[:-1]
    expected = (10 - sum(
        int(digit) * (3 if (len(body) - index) % 2 else 1)
        for index, digit in enumerate(body)
    ) % 10) % 10
    return digits if expected == int(digits[-1]) else None


def _region_search_terms(region: str) -> list[str]:
    """Map common collection region labels to conservative listing keywords."""
    value = _normalise(region)
    if not value:
        return []
    if value in {"jp", "japan", "japanese", "ntsc-j", "ntsc j"} or "japan" in value or "ntsc-j" in value:
        return ["Japan", "NTSC-J"]
    if value in {"us", "usa", "united states", "north america", "ntsc-u", "ntsc u"} or any(
        token in value for token in ("united states", "north america", "ntsc-u", "ntsc u")
    ):
        return ["USA", "NTSC-U"]
    if value in {"uk", "united kingdom", "europe", "eu", "italy", "pal"} or any(
        country in value for country in ("europe", "italy", "germany", "france", "spain", "uk", "pal")
    ):
        return [str(region).strip(), "PAL"]
    if value in {"australia", "australian", "au"}:
        return ["Australia", "PAL"]
    return [str(region).strip()]


def _safe_query(parts: list[str]) -> str:
    """Build a bounded Browse keyword query without adding private collection fields."""
    query = " ".join(dict.fromkeys(str(part).strip() for part in parts if str(part or "").strip()))
    if len(query) <= 100:
        return query
    clipped = query[:100]
    return clipped.rsplit(" ", 1)[0] if " " in clipped else clipped


def _build_search_requests(
    title: str,
    platform_name: str,
    item_type: str,
    *,
    deep_search: bool = False,
    search_details: Optional[dict] = None,
) -> tuple[list[tuple[dict, bool]], list[str]]:
    """Return Browse query params plus the title variants used for local matching.

    Deep mode unions multiple bounded keyword searches and one separate GTIN search.
    It deliberately reads only product identifiers/attributes, never notes, location,
    purchase data, or free-form descriptions from the collection record.
    """
    details = search_details if isinstance(search_details, dict) else {}
    kind = (item_type or "game").strip().lower()
    platform = "" if kind in _NO_PLATFORM_TYPES else (platform_name or "").strip()
    base_query = _safe_query([title, platform])
    query_values = [base_query] if base_query else []
    title_variants = [str(title or "").strip()] if str(title or "").strip() else []

    if deep_search:
        alt_titles = details.get("alt_titles") or []
        if isinstance(alt_titles, str):
            alt_titles = [alt_titles]
        for alt_title in alt_titles[:3]:
            alt_title = str(alt_title or "").strip()
            if alt_title and _normalise(alt_title) not in {_normalise(v) for v in title_variants}:
                title_variants.append(alt_title)
                query = _safe_query([alt_title, platform])
                if query and _normalise(query) not in {_normalise(v) for v in query_values}:
                    query_values.append(query)

        region = str(details.get("region") or "").strip()
        if region:
            title_variants.append(region)
            region_terms = (
                _region_search_terms(region)
                if kind in {"game", "console", ""}
                else [region]
            )
            for term in region_terms:
                query = _safe_query([title, platform, term])
                if query and _normalise(query) not in {_normalise(v) for v in query_values}:
                    query_values.append(query)

        # Query one relevant catalog attribute at a time: Browse treats space-separated
        # keywords as AND, so combining every field would over-constrain the search.
        product_fields = (
            "publisher", "developer", "genre", "series_name", "character_name",
            "scale", "funko_number", "vinyl_format", "completeness", "condition",
        )
        attribute_values = [str(details.get(field) or "").strip() for field in product_fields]
        release_date = str(details.get("release_date") or "")
        year_match = re.search(r"\b(?:19|20)\d{2}\b", release_date)
        if year_match:
            attribute_values.insert(0, year_match.group(0))
        for value in attribute_values:
            if not value:
                continue
            query = _safe_query([title, platform, value])
            if query and _normalise(query) not in {_normalise(v) for v in query_values}:
                query_values.append(query)

        query_values = query_values[:_MAX_DEEP_SEARCH_TEXT_QUERIES]

    requests = [({"q": query, "limit": "200"}, False) for query in query_values]
    if deep_search:
        gtin = _normalise_gtin(details.get("barcode"))
        if gtin:
            requests.append(({"gtin": gtin, "limit": "200"}, True))

    # A GTIN result is product-identified even when the seller's title is abbreviated.
    match_titles = [title_value for title_value in title_variants if title_value != region] if deep_search else title_variants
    return requests, match_titles


def _title_exclusion_reason(
    item_title: str,
    game_title: str,
    item_type: str = "game",
    alternative_titles: Optional[list[str]] = None,
    allow_title_mismatch: bool = False,
) -> Optional[str]:
    """Explain why a listing is not a single, relevant listing for the requested item."""
    norm_item = _normalise(item_title)

    for keyword in _build_bundle_keywords(item_type):
        if keyword in norm_item:
            return "keyword_bundle_or_defect"

    if re.search(r"\d+\s*(spiele|games|titles|stück|stk)", norm_item):
        return "keyword_multiple_items"

    if allow_title_mismatch:
        return None

    titles = [game_title, *(alternative_titles or [])]
    item_words = set(re.findall(r"[^\W_]+", norm_item, flags=re.UNICODE))
    has_comparable_words = False
    for candidate in titles:
        candidate_tokens = re.findall(r"[^\W_]+", str(candidate or ""), flags=re.UNICODE)
        game_words = [word.lower() for word in candidate_tokens if len(word) >= 3]
        short_identifiers = [
            word.lower()
            for word in candidate_tokens
            if len(word) == 2 and (word.isupper() or any(char.isdigit() for char in word))
        ]
        if not game_words and not short_identifiers:
            continue
        has_comparable_words = True
        if short_identifiers and not any(word in item_words for word in short_identifiers):
            continue
        if not game_words or sum(1 for word in game_words if word in item_words) >= max(1, len(game_words) * 0.5):
            return None
    return "title_not_relevant" if has_comparable_words else None


def _title_is_relevant(item_title: str, game_title: str, platform_name: str, item_type: str = "game") -> bool:
    """Keep the existing boolean helper interface for callers and tests."""
    return _title_exclusion_reason(item_title, game_title, item_type) is None


async def fetch_ebay_market_price(
    title: str,
    platform_name: str,
    item_type: str = "game",
    *,
    deep_search: bool = False,
    search_details: Optional[dict] = None,
):
    deadline = (
        _search_clock() + _DEEP_SEARCH_DURATION_SECONDS
        if deep_search else None
    )
    requests, match_titles = _build_search_requests(
        title, platform_name, item_type,
        deep_search=deep_search,
        search_details=search_details,
    )
    if deadline is not None:
        remaining = deadline - _search_clock()
        if remaining <= 0:
            return {
                "error": "eBay deep search reached its time limit before starting.",
                "search_complete": False,
                "failed_searches": 0,
                "skipped_searches": len(requests),
            }
        try:
            token = await asyncio.wait_for(get_ebay_token(), timeout=remaining)
        except asyncio.TimeoutError:
            logger.warning("eBay OAuth request exceeded the deep-search time limit")
            return {
                "error": "eBay deep search reached its time limit while acquiring credentials.",
                "search_complete": False,
                "failed_searches": 1,
                "skipped_searches": len(requests),
            }
    else:
        token = await get_ebay_token()
    if not token:
        return None
    marketplace_id = os.getenv("EBAY_MARKETPLACE_ID", "EBAY_IT").strip().upper() or "EBAY_IT"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": marketplace_id,
    }

    exclusions = Counter()
    shipping_status = Counter()
    prices: list[float] = []
    returned_count = 0
    truncated_searches = 0
    unique_items = {}
    gtin_item_keys = set()
    requests_made = 0
    failed_requests = 0
    rate_limited = False
    authorization_rejected = False
    authorization_status = None
    deadline_exhausted = False

    try:
        async with httpx.AsyncClient(timeout=12) as client:
            for request_index, (params, is_gtin_query) in enumerate(requests):
                request_timeout = 12
                if deadline is not None:
                    remaining = deadline - _search_clock()
                    if remaining <= 0:
                        deadline_exhausted = True
                        break
                    request_timeout = min(_DEEP_SEARCH_REQUEST_TIMEOUT_SECONDS, remaining)
                requests_made += 1
                try:
                    request = client.get(
                        "https://api.ebay.com/buy/browse/v1/item_summary/search",
                        params=params,
                        headers=headers,
                        timeout=request_timeout,
                    )
                    if deadline is not None:
                        res = await asyncio.wait_for(request, timeout=remaining)
                    else:
                        res = await request
                except asyncio.TimeoutError:
                    failed_requests += 1
                    deadline_exhausted = True
                    logger.warning("eBay Browse search exceeded the deep-search time limit")
                    break
                except Exception as exc:
                    failed_requests += 1
                    logger.warning("eBay Browse search failed (%s)", type(exc).__name__)
                    continue

                if res.status_code >= 400:
                    failed_requests += 1
                    logger.warning(
                        "eBay Browse search failed with HTTP %s (query_type=%s)",
                        res.status_code,
                        "gtin" if is_gtin_query else "keywords",
                    )
                    if res.status_code == 429 and deep_search:
                        rate_limited = True
                        break
                    if res.status_code in {401, 403} and deep_search:
                        authorization_rejected = True
                        authorization_status = res.status_code
                        break
                    continue

                try:
                    payload = res.json()
                except Exception as exc:
                    failed_requests += 1
                    logger.warning("eBay Browse response could not be parsed (%s)", type(exc).__name__)
                    continue
                if not isinstance(payload, dict):
                    failed_requests += 1
                    continue
                items = payload.get("itemSummaries", []) or []
                if not isinstance(items, list):
                    failed_requests += 1
                    continue
                returned_count += len(items)
                next_page = payload.get("next")
                if isinstance(next_page, str) and next_page.strip():
                    truncated_searches += 1

                for item_index, item in enumerate(items):
                    if not isinstance(item, dict):
                        exclusions["invalid_item"] += 1
                        continue
                    item_key = str(item.get("itemId") or item.get("itemWebUrl") or "").strip()
                    if not item_key:
                        item_key = f"unkeyed:{request_index}:{item_index}"
                    if item_key not in unique_items:
                        unique_items[item_key] = item
                    if is_gtin_query:
                        gtin_item_keys.add(item_key)

    except Exception as exc:
        logger.warning("eBay Browse search failed (%s)", type(exc).__name__)
        return None

    skipped_requests = max(len(requests) - requests_made, 0)
    accepted_conditions = _condition_ids_for_item_type((item_type or "game").strip().lower())
    include_shipping = _ebay_shipping_enabled()
    for item_key, item in unique_items.items():
        condition_id = str(item.get("conditionId") or "").strip()
        if not condition_id:
            exclusions["condition_missing"] += 1
            continue
        if condition_id not in accepted_conditions:
            exclusions["condition_excluded"] += 1
            continue

        title_reason = _title_exclusion_reason(
            item.get("title", ""), title, item_type,
            alternative_titles=match_titles[1:],
            allow_title_mismatch=item_key in gtin_item_keys,
        )
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

        if include_shipping:
            shipping_cost, shipping_reason = _shipping_price(item)
            shipping_status[shipping_reason] += 1
            prices.append(item_price + shipping_cost if shipping_cost is not None else item_price)
        else:
            shipping_status["disabled"] += 1
            prices.append(item_price)

    median_price, trimmed, min_price, max_price = _trim_outliers_and_median(prices)
    used_count = len(trimmed)
    logger.info(
        "eBay price search diagnostics marketplace=%s search_mode=%s api_requests=%d api_failed=%d api_skipped=%d api_truncated=%d deadline_reached=%s rate_limited=%s authorization_rejected=%s api_returned=%d unique_results=%d api_page_limit=200 eligible=%d used=%d trimmed_outliers=%d exclusions=%s shipping_enabled=%s shipping=%s",
        marketplace_id,
        "deep" if deep_search else "standard",
        requests_made,
        failed_requests,
        skipped_requests,
        truncated_searches,
        deadline_exhausted,
        rate_limited,
        authorization_rejected,
        returned_count,
        len(unique_items),
        len(prices),
        used_count,
        max(len(prices) - used_count, 0),
        ",".join(f"{reason}:{count}" for reason, count in sorted(exclusions.items())) or "none",
        include_shipping,
        ",".join(f"{reason}:{count}" for reason, count in sorted(shipping_status.items())) or "none",
    )
    if median_price is None:
        if deep_search and (failed_requests or deadline_exhausted or truncated_searches):
            if rate_limited:
                reason = "eBay deep search was rate-limited."
            elif authorization_rejected:
                reason = f"eBay rejected the deep-search request with HTTP {authorization_status}."
            elif deadline_exhausted:
                reason = "eBay deep search reached its time limit."
            elif truncated_searches:
                reason = "eBay deep search reached its one-page-per-query limit."
            else:
                reason = "Some eBay deep-search requests failed."
            return {
                "error": f"{reason} No usable listings were returned; try again or use the standard eBay search.",
                "search_complete": False,
                "failed_searches": failed_requests,
                "skipped_searches": skipped_requests,
                "truncated_searches": truncated_searches,
            }
        return None

    result = {
        "market_price": round(median_price, 2),
        "sample_size": used_count,
        "price_min": round(min_price, 2),
        "price_max": round(max_price, 2),
    }
    if deep_search:
        result["search_complete"] = (
            failed_requests == 0 and skipped_requests == 0
            and truncated_searches == 0 and not deadline_exhausted
        )
        result["failed_searches"] = failed_requests
        result["skipped_searches"] = skipped_requests
        result["truncated_searches"] = truncated_searches
    return result
