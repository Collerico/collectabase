import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from .api.routes.games import _parse_alt_titles
from .api.security import require_admin_access
from .database import dict_from_row, get_db, set_app_meta
from . import jobs

from .services.price.utils import (
    PLATFORM_SLUGS, _to_eur, get_eur_rate, _normalize_text
)
from .services.price.catalog import (
    _lookup_local_catalog_price, scrape_platform_catalog,
    _upsert_catalog_entries, _derive_platform_label
)
from .services.price.platforms import canonicalize_platform, get_known_platforms
from .services.price.providers.ebay import fetch_ebay_market_price, _ebay_credentials
from .services.price.providers.rawg import fetch_rawg_reference, _rawg_key
from .services.price.providers.pricecharting import (
    _fetch_pricecharting_scrape, fetch_pricecharting
)

router = APIRouter()

logger = logging.getLogger("collectabase.price_tracker")

def _get_game_for_price_lookup(game_id: int):
    with get_db() as db:
        row = db.execute(
            """
            SELECT g.*, p.name as platform_name
            FROM games g LEFT JOIN platforms p ON g.platform_id = p.id
            WHERE g.id = ?
            """,
            (game_id,),
        ).fetchone()
    return dict_from_row(row) if row else None


def _price_options_from_values(loose, complete, new):
    """Return selectable condition-tier estimates without inventing missing values."""
    options = []
    for condition, label, value in (
        ("loose", "Loose", loose),
        ("complete", "CIB", complete),
        ("new", "New", new),
    ):
        if value is not None:
            options.append({"condition": condition, "label": label, "market_price": value})
    return options


def _ebay_search_details(game: dict) -> dict:
    """Return product-only attributes safe to send to the eBay search provider."""
    return {
        "alt_titles": _parse_alt_titles(game.get("alt_titles")),
        "region": game.get("region"),
        "barcode": game.get("barcode"),
        "release_date": game.get("release_date"),
        "condition": game.get("condition"),
        "completeness": game.get("completeness"),
        "publisher": game.get("publisher"),
        "developer": game.get("developer"),
        "genre": game.get("genre"),
        "series_name": game.get("series_name"),
        "character_name": game.get("character_name"),
        "scale": game.get("scale"),
        "funko_number": game.get("funko_number"),
        "vinyl_format": game.get("vinyl_format"),
    }


@router.post("/api/games/{game_id}/fetch-market-price")
async def fetch_market_price(
    game_id: int,
    source: Optional[str] = None,
    deep_search: bool = False,
):
    """Price lookup. PriceCharting and eBay are explicit, isolated sources."""
    game = _get_game_for_price_lookup(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    item_type = game.get("item_type") or "game"
    if source == "ebay":
        if not all(_ebay_credentials()):
            return {"error": "eBay is not configured in Settings."}
        ebay = await fetch_ebay_market_price(
            game["title"],
            game.get("platform_name") or "",
            item_type,
            deep_search=deep_search,
            search_details=_ebay_search_details(game) if deep_search else None,
        )
        if ebay and ebay.get("error"):
            return {
                "error": ebay["error"],
                "search_complete": ebay.get("search_complete", False),
                "failed_searches": ebay.get("failed_searches", 0),
                "skipped_searches": ebay.get("skipped_searches", 0),
                "truncated_searches": ebay.get("truncated_searches", 0),
            }
        if ebay:
            price_options = ebay.get("price_options") or [{
                "condition": "market",
                "label": "Market median (mixed conditions)",
                "market_price": ebay["market_price"],
                "sample_size": ebay["sample_size"],
                "price_min": ebay["price_min"],
                "price_max": ebay["price_max"],
            }]
            history_sources = {
                "market": ("ebay_market", "loose_price"),
                "loose": ("ebay_loose", "loose_price"),
                "complete": ("ebay_cib", "complete_price"),
                "new": ("ebay_new", "new_price"),
            }
            suggestions = []
            with get_db() as db:
                for option in price_options:
                    condition = option.get("condition")
                    history_source = history_sources.get(condition)
                    price = option.get("market_price")
                    if history_source and price is not None:
                        source_name, price_column = history_source
                        loose_price = price if price_column == "loose_price" else None
                        complete_price = price if price_column == "complete_price" else None
                        new_price = price if price_column == "new_price" else None
                        db.execute(
                            """
                            INSERT INTO price_history
                                (game_id, source, loose_price, complete_price, new_price, eur_rate)
                            VALUES (?, ?, ?, ?, ?, 1.0)
                            """,
                            (game_id, source_name, loose_price, complete_price, new_price),
                        )
                    suggestions.append({
                        **option,
                        "source": "ebay",
                        "source_detail": "direct_ebay",
                    })
                db.commit()
            result = {
                "market_price": ebay["market_price"],
                "source": "ebay",
                "source_detail": "direct_ebay",
                "sample_size": ebay["sample_size"],
                "price_min": ebay["price_min"],
                "price_max": ebay["price_max"],
                "suggestions": [{
                    "source": "ebay",
                    "source_detail": "direct_ebay",
                    "price_options": suggestions,
                }],
            }
            if "search_complete" in ebay:
                result["search_complete"] = ebay["search_complete"]
                result["failed_searches"] = ebay.get("failed_searches", 0)
                result["skipped_searches"] = ebay.get("skipped_searches", 0)
                result["truncated_searches"] = ebay.get("truncated_searches", 0)
            return result
        return {"error": "No eBay listings found for this game."}

    if source not in (None, "pricecharting"):
        return {"error": "Unknown price source."}

    supported_pricecharting_types = {"game", "console", "controller", "accessory", "funko", "comic"}
    search_titles = []
    title = str(game.get("title") or "").strip()
    if title:
        search_titles.append(title)
    for alt_title in _parse_alt_titles(game.get("alt_titles")):
        if alt_title and alt_title.strip().lower() not in {value.lower() for value in search_titles}:
            search_titles.append(alt_title.strip())

    catalog = None
    scraped = None
    eur_rate = None
    platform_name = game.get("platform_name") or ""
    if item_type in supported_pricecharting_types:
        for search_title in search_titles:
            for platform_hint in dict.fromkeys((platform_name, "")):
                catalog = _lookup_local_catalog_price(search_title, platform_hint)
                if catalog:
                    break
            if catalog:
                break

        # Always attempt a live scrape, even when the local catalog matched.
        for search_title in search_titles:
            scraped = await _fetch_pricecharting_scrape(search_title, platform_name)
            if scraped:
                break
        if scraped:
            eur_rate = await get_eur_rate()

    candidates = []
    if catalog:
        candidates.append({
            "source": "pricecharting",
            "source_detail": "local_catalog",
            "_history_source": "pricecharting_local",
            "market_price": catalog["loose_eur"],
            "condition": "loose",
            "matched_title": catalog["product_name"],
            "matched_platform": catalog["platform"],
            "match_score": catalog["match_score"],
            "_history": {
                "loose": catalog["loose_eur"],
                "complete": catalog["cib_eur"],
                "new": catalog["new_eur"],
                "eur_rate": 1.0,
                "pricecharting_id": catalog["pricecharting_id"] or None,
            },
        })
    if scraped:
        scraped_loose = _to_eur(scraped["loose_usd"], eur_rate)
        scraped_cib = _to_eur(scraped.get("cib_usd"), eur_rate)
        scraped_new = _to_eur(scraped.get("new_usd"), eur_rate)
        candidates.append({
            "source": "pricecharting",
            "source_detail": "live_scrape",
            "_history_source": "pricecharting_scrape",
            "market_price": scraped_loose,
            "condition": "loose",
            "matched_title": scraped.get("product_name"),
            "_history": {
                "loose": scraped_loose,
                "complete": scraped_cib,
                "new": scraped_new,
                "eur_rate": eur_rate,
                "pricecharting_id": scraped.get("pricecharting_id"),
            },
        })

    for candidate in candidates:
        history = candidate["_history"]
        candidate["price_options"] = _price_options_from_values(
            history["loose"], history["complete"], history["new"]
        )

    if candidates:
        # Store each provider observation separately so either selectable quote
        # remains visible in history. Prefer the live scrape for the legacy top-level
        # market_price field when both paths found a value.
        primary = next(
            (candidate for candidate in candidates if candidate["source_detail"] == "live_scrape"),
            candidates[0],
        )
        with get_db() as db:
            for candidate in candidates:
                history = candidate["_history"]
                db.execute(
                    """
                    INSERT INTO price_history
                        (game_id, source, loose_price, complete_price, new_price, eur_rate, pricecharting_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (game_id, candidate["_history_source"], history["loose"],
                     history["complete"], history["new"], history["eur_rate"],
                     history["pricecharting_id"]),
                )
            db.commit()

        suggestions = [
            {key: value for key, value in candidate.items()
             if key not in {"_history", "_history_source"}}
            for candidate in candidates
        ]
        result = {
            "market_price": primary["market_price"],
            "source": "pricecharting",
            "source_detail": "combined" if len(suggestions) > 1 else primary["source_detail"],
            "condition": "loose",
            "suggestions": suggestions,
        }
        for field in ("matched_title", "matched_platform", "match_score"):
            if field in primary:
                result[field] = primary[field]
        return result

    return {
        "source": "pricecharting",
        "info": "PriceCharting found no price in the local catalog or live scrape. Try the eBay button.",
        "suggested_action": "ebay",
    }


@router.post("/api/games/{game_id}/price-check")
async def check_price(game_id: int, deep_search: bool = False):
    """Backward-compatible alias; deep search explicitly selects eBay."""
    source = "ebay" if deep_search else None
    return await fetch_market_price(game_id, source=source, deep_search=deep_search)


@router.get("/api/games/{game_id}/price-history")
async def get_price_history(game_id: int):
    """Return the last 20 price snapshots for a game."""
    with get_db() as db:
        rows = db.execute(
            """
            SELECT * FROM price_history
            WHERE game_id = ?
            ORDER BY fetched_at DESC, id DESC
            LIMIT 20
            """,
            (game_id,),
        ).fetchall()
        return [dict_from_row(r) for r in rows]


@router.delete("/api/games/{game_id}/price-history/{entry_id}")
async def delete_price_history_entry(game_id: int, entry_id: int):
    """Delete any price history entry (manual or provider)."""
    with get_db() as db:
        row = db.execute(
            "SELECT id, game_id, source FROM price_history WHERE id = ?",
            (entry_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Price history entry not found")
        item = dict_from_row(row)
        if int(item["game_id"]) != int(game_id):
            raise HTTPException(status_code=404, detail="Price history entry not found for this game")
        db.execute("DELETE FROM price_history WHERE id = ?", (entry_id,))
        db.commit()
    return {"ok": True}


@router.post("/api/prices/update-all")
async def bulk_price_update(
    background_tasks: BackgroundTasks,
    limit: int = 100,
    _admin: None = Depends(require_admin_access),
):
    """Kick off a background job to fetch prices for up to `limit` games.
    Returns immediately with a job_id. Poll /api/jobs/{job_id} for progress.
    """
    with get_db() as db:
        try:
            rows = db.execute(
                """
                SELECT g.id, g.title, g.item_type, p.name as platform_name
                FROM games g
                LEFT JOIN platforms p ON g.platform_id = p.id
                WHERE g.is_wishlist = 0
                ORDER BY g.id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        except Exception as e:
            logger.warning(f"Bulk price update query with is_wishlist filter failed, falling back: {e}")
            rows = db.execute(
                """
                SELECT g.id, g.title, g.item_type, p.name as platform_name
                FROM games g
                LEFT JOIN platforms p ON g.platform_id = p.id
                ORDER BY g.id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        game_list = [dict_from_row(r) for r in rows]

    job_id = jobs.start("bulk_price_update", total=len(game_list))
    background_tasks.add_task(_run_bulk_price_update, job_id, game_list)
    return {"job_id": job_id, "total": len(game_list), "state": "running"}


async def _run_bulk_price_update(job_id: str, game_list: list) -> None:
    eur_rate = await get_eur_rate()
    success = 0
    failed = 0

    for i, game in enumerate(game_list):
        jobs.update(job_id, progress=i + 1)

        item_type = game.get("item_type") or "game"
        is_pc_supported = item_type in ("game", "console", "controller", "accessory", "funko", "comic")
        
        if not is_pc_supported:
            failed += 1
            await asyncio.sleep(0.01)
            continue

        catalog = _lookup_local_catalog_price(game["title"], game.get("platform_name") or "")
        if not catalog:
            catalog = _lookup_local_catalog_price(game["title"], "")

        if catalog:
            with get_db() as db:
                db.execute(
                    """
                    INSERT INTO price_history
                        (game_id, source, loose_price, complete_price, new_price, eur_rate, pricecharting_id)
                    VALUES (?, 'pricecharting', ?, ?, ?, 1.0, ?)
                    """,
                    (
                        game["id"],
                        catalog["loose_eur"],
                        catalog["cib_eur"],
                        catalog["new_eur"],
                        catalog["pricecharting_id"] or None,
                    ),
                )
                db.commit()
            success += 1
            await asyncio.sleep(0.1)
            continue

        pc = await fetch_pricecharting(game["title"], game["platform_name"] or "")
        if pc:
            with get_db() as db:
                db.execute(
                    """
                    INSERT INTO price_history
                        (game_id, source, loose_price, complete_price, new_price, eur_rate, pricecharting_id)
                    VALUES (?, 'pricecharting', ?, ?, ?, ?, ?)
                    """,
                    (
                        game["id"],
                        _to_eur(pc["loose_usd"], eur_rate),
                        _to_eur(pc["cib_usd"], eur_rate),
                        _to_eur(pc["new_usd"], eur_rate),
                        eur_rate,
                        pc["pricecharting_id"],
                    ),
                )
                db.commit()
            success += 1
        else:
            failed += 1

        await asyncio.sleep(0.4)  # stay polite to PriceCharting.

    finished_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    set_app_meta("last_bulk_price_update_at", finished_at)
    set_app_meta("last_bulk_price_update_success", str(success))
    set_app_meta("last_bulk_price_update_failed", str(failed))
    set_app_meta("last_bulk_price_update_total", str(len(game_list)))
    set_app_meta("last_bulk_price_update_error", "")
    jobs.finish(job_id, success=success, failed=failed)


@router.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str):
    """Poll the status of a background job."""
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/api/jobs")
async def list_active_jobs():
    """Return all currently running background jobs."""
    return jobs.list_active()


class ManualPriceEntry(BaseModel):
    loose_price: Optional[float] = None
    complete_price: Optional[float] = None
    new_price: Optional[float] = None


class CatalogPriceApply(BaseModel):
    catalog_id: int


@router.post("/api/games/{game_id}/price-manual")
async def add_manual_price(game_id: int, entry: ManualPriceEntry):
    """Save a manually entered price snapshot (source='manual')."""
    with get_db() as db:
        row = db.execute("SELECT id FROM games WHERE id = ?", (game_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Game not found")

    with get_db() as db:
        db.execute(
            """
            INSERT INTO price_history
                (game_id, source, loose_price, complete_price, new_price)
            VALUES (?, 'manual', ?, ?, ?)
            """,
            (game_id, entry.loose_price, entry.complete_price, entry.new_price),
        )
        db.commit()
    return {"ok": True}


@router.post("/api/games/{game_id}/price-from-catalog")
async def apply_catalog_price(game_id: int, payload: CatalogPriceApply):
    with get_db() as db:
        game = db.execute("SELECT id FROM games WHERE id = ?", (game_id,)).fetchone()
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")

        row = db.execute("SELECT * FROM price_catalog WHERE id = ?", (payload.catalog_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Catalog entry not found")
        item = dict_from_row(row)

        loose = item.get("loose_eur")
        cib = item.get("cib_eur")
        new = item.get("new_eur")
        if loose is None and cib is None and new is None:
            raise HTTPException(status_code=400, detail="Catalog entry has no usable prices")

        db.execute(
            """
            INSERT INTO price_history
                (game_id, source, loose_price, complete_price, new_price, eur_rate, pricecharting_id)
            VALUES (?, 'pricecharting', ?, ?, ?, 1.0, ?)
            """,
            (game_id, loose, cib, new, item.get("pricecharting_id") or None),
        )

        if loose is not None:
            db.execute(
                "UPDATE games SET current_value = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (loose, game_id),
            )

        db.commit()

    return {
        "ok": True,
        "market_price": loose,
        "source": "pricecharting",
        "matched_title": item.get("title"),
        "matched_platform": item.get("platform"),
    }


def _resolve_scraper_target(platform: str) -> tuple[str, str] | None:
    if not platform or platform == "all":
        return None
    for lbl, slug in PLATFORM_SLUGS.items():
        if slug == platform or lbl == platform.lower():
            return (lbl, slug)
    identity = canonicalize_platform(platform)
    if identity.scraper_slug:
        return (identity.label, identity.scraper_slug)
    return None


@router.post("/api/price-catalog/scrape")
async def scrape_catalog(
    platform: str = "all",
    q: Optional[str] = None,
    _admin: None = Depends(require_admin_access),
):
    """Scrape PriceCharting catalog for one or all platforms into price_catalog table."""
    query = (q or "").strip()
    if query:
        platform_hint = None
        if platform and platform != "all":
            resolved = _resolve_scraper_target(platform)
            if not resolved:
                raise HTTPException(status_code=400, detail=f"Unknown platform: {platform}")
            platform_hint = resolved[0]

        scraped = await _fetch_pricecharting_scrape(query, platform_hint or "")
        if not scraped:
            return {
                "scraped": 0,
                "inserted": 0,
                "updated": 0,
                "unchanged": 0,
                "deduped_in_batch": 0,
                "duplicates_removed": 0,
                "platforms": [platform_hint] if platform_hint else [],
                "query": query,
                "targeted": True,
                "error": "No PriceCharting result found for query",
            }

        eur_rate = await get_eur_rate()
        resolved_platform = platform_hint or _derive_platform_label(scraped.get("page_url")) or "Unknown"
        entry = {
            "pricecharting_id": scraped.get("pricecharting_id") or "",
            "title": scraped.get("product_name") or query,
            "platform": resolved_platform,
            "loose_usd": scraped.get("loose_usd"),
            "cib_usd": scraped.get("cib_usd"),
            "new_usd": scraped.get("new_usd"),
            "page_url": scraped.get("page_url") or "",
        }
        stats = _upsert_catalog_entries([entry], eur_rate)
        result = {
            "scraped": stats["processed"],
            "inserted": stats["inserted"],
            "updated": stats["updated"],
            "unchanged": stats["unchanged"],
            "deduped_in_batch": stats["deduped_in_batch"],
            "duplicates_removed": stats["duplicates_removed"],
            "platforms": [resolved_platform],
            "query": query,
            "targeted": True,
        }
        finished_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        set_app_meta("last_catalog_scrape_at", finished_at)
        set_app_meta("last_catalog_scrape_platforms", ", ".join(result["platforms"]))
        set_app_meta("last_catalog_scrape_total", result["scraped"])
        return result

    if platform == "all":
        targets = list(PLATFORM_SLUGS.items())
    else:
        resolved = _resolve_scraper_target(platform)
        if not resolved:
            raise HTTPException(status_code=400, detail=f"Unknown platform: {platform}")
        targets = [resolved]

    eur_rate = await get_eur_rate()
    total_scraped = 0
    total_inserted = 0
    total_updated = 0
    total_unchanged = 0
    total_deduped_in_batch = 0
    total_duplicates_removed = 0

    for label, slug in targets:
        print(f"Starting catalog scrape for {label} ({slug})")
        entries = await scrape_platform_catalog(slug, label)
        stats = _upsert_catalog_entries(entries, eur_rate)
        total_scraped += stats["processed"]
        total_inserted += stats["inserted"]
        total_updated += stats["updated"]
        total_unchanged += stats["unchanged"]
        total_deduped_in_batch += stats["deduped_in_batch"]
        total_duplicates_removed += stats["duplicates_removed"]
        print(
            f"Finished {label}: processed={stats['processed']} inserted={stats['inserted']} "
            f"updated={stats['updated']} unchanged={stats['unchanged']}"
        )
        if len(targets) > 1:
            await asyncio.sleep(2.0)  # pause between platforms when scraping all

    result = {
        "scraped": total_scraped,
        "inserted": total_inserted,
        "updated": total_updated,
        "unchanged": total_unchanged,
        "deduped_in_batch": total_deduped_in_batch,
        "duplicates_removed": total_duplicates_removed,
        "platforms": [lbl for lbl, _ in targets],
    }
    finished_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    set_app_meta("last_catalog_scrape_at", finished_at)
    set_app_meta("last_catalog_scrape_platforms", ", ".join(result["platforms"]))
    set_app_meta("last_catalog_scrape_total", result["scraped"])
    return result


@router.post("/api/price-catalog/enrich-library")
async def enrich_catalog_from_library(limit: int = 120, _admin: None = Depends(require_admin_access)):
    """
    Fill price_catalog incrementally by scraping titles already present in the local library.
    This helps grow coverage beyond the paginated console-catalog scrape.
    """
    with get_db() as db:
        try:
            rows = db.execute(
                """
                SELECT g.id, g.title, p.name as platform_name
                FROM games g
                LEFT JOIN platforms p ON g.platform_id = p.id
                WHERE g.is_wishlist = 0
                ORDER BY g.updated_at DESC, g.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        except Exception:
            rows = db.execute(
                """
                SELECT g.id, g.title, p.name as platform_name
                FROM games g
                LEFT JOIN platforms p ON g.platform_id = p.id
                ORDER BY g.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    games = [dict_from_row(r) for r in rows]
    scanned = len(games)
    skipped_existing = 0
    failed = 0
    fetched_entries = []

    for game in games:
        title = (game.get("title") or "").strip()
        platform_name = (game.get("platform_name") or "").strip()
        if not title:
            continue

        existing = _lookup_local_catalog_price(title, platform_name)
        if not existing:
            existing = _lookup_local_catalog_price(title, "")
        if existing and float(existing.get("match_score") or 0) >= 0.9:
            skipped_existing += 1
            continue

        scraped = await _fetch_pricecharting_scrape(title, platform_name)
        if not scraped:
            failed += 1
            await asyncio.sleep(0.35)
            continue

        resolved_platform = (
            _derive_platform_label(scraped.get("page_url"))
            or _normalize_text(platform_name)
            or "unknown"
        )
        fetched_entries.append(
            {
                "pricecharting_id": scraped.get("pricecharting_id") or "",
                "title": scraped.get("product_name") or title,
                "platform": resolved_platform,
                "loose_usd": scraped.get("loose_usd"),
                "cib_usd": scraped.get("cib_usd"),
                "new_usd": scraped.get("new_usd"),
                "page_url": scraped.get("page_url") or "",
            }
        )
        await asyncio.sleep(0.5)

    eur_rate = await get_eur_rate()
    stats = _upsert_catalog_entries(fetched_entries, eur_rate)

    finished_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    set_app_meta("last_catalog_scrape_at", finished_at)
    set_app_meta("last_catalog_scrape_platforms", "library-enrich")
    set_app_meta("last_catalog_scrape_total", stats["processed"])
    set_app_meta("last_catalog_enrich_scanned", scanned)
    set_app_meta("last_catalog_enrich_skipped_existing", skipped_existing)
    set_app_meta("last_catalog_enrich_failed", failed)

    return {
        "library": True,
        "scanned": scanned,
        "skipped_existing": skipped_existing,
        "failed": failed,
        "fetched": len(fetched_entries),
        "scraped": stats["processed"],
        "inserted": stats["inserted"],
        "updated": stats["updated"],
        "unchanged": stats["unchanged"],
        "deduped_in_batch": stats["deduped_in_batch"],
        "duplicates_removed": stats["duplicates_removed"],
    }


@router.get("/api/price-catalog")
async def search_catalog(
    search: Optional[str] = None,
    platform: Optional[str] = None,
    sort: str = "title",
    order: str = "asc",
    page: int = 1,
    limit: int = 50,
):
    """Search and paginate the local price catalog."""
    allowed_sorts = {"title", "platform", "loose_eur", "cib_eur", "new_eur"}
    if sort not in allowed_sorts:
        sort = "title"
    order_dir = "DESC" if order.lower() == "desc" else "ASC"

    conditions = []
    params = []

    if search:
        conditions.append("title LIKE ?")
        params.append(f"%{search}%")
    if platform:
        identity = canonicalize_platform(platform)
        conditions.append("(platform_key = ? OR LOWER(platform) = LOWER(?))")
        params.extend([identity.key, identity.label])

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    offset = (page - 1) * limit

    with get_db() as db:
        count_row = db.execute(
            f"SELECT COUNT(*) as count FROM price_catalog {where}", tuple(params)
        ).fetchone()
        total = count_row["count"] if count_row else 0

        rows = db.execute(
            f"""
            SELECT * FROM price_catalog
            {where}
            ORDER BY {sort} {order_dir}
            LIMIT ? OFFSET ?
            """,
            tuple(params + [limit, offset]),
        ).fetchall()

    return {
        "items": [dict_from_row(r) for r in rows],
        "total": total,
        "page": page,
        "limit": limit,
    }


@router.get("/api/price-catalog/platforms")
async def catalog_platforms():
    """Return distinct platforms present in the price catalog with canonical keys and labels."""
    with get_db() as db:
        rows = db.execute(
            """
            SELECT platform, platform_key, COUNT(*) as count
            FROM price_catalog
            WHERE platform IS NOT NULL AND platform != ''
            GROUP BY platform, platform_key
            ORDER BY platform
            """
        ).fetchall()

    catalog_counts = {}
    catalog_platforms_dict = {}
    for r in rows:
        identity = canonicalize_platform(r["platform_key"] or r["platform"])
        catalog_counts[identity.key] = catalog_counts.get(identity.key, 0) + (r["count"] or 0)
        catalog_platforms_dict[identity.key] = identity

    results = []
    known = get_known_platforms()
    seen_keys = set()
    for kp in known:
        seen_keys.add(kp.key)
        results.append({
            "key": kp.key,
            "label": kp.label,
            "scraper_slug": kp.scraper_slug,
            "count": catalog_counts.get(kp.key, 0),
        })

    for key, identity in catalog_platforms_dict.items():
        if key not in seen_keys:
            seen_keys.add(key)
            results.append({
                "key": identity.key,
                "label": identity.label,
                "scraper_slug": identity.scraper_slug,
                "count": catalog_counts.get(key, 0),
            })

    results.sort(key=lambda x: x["label"].lower())
    return results


@router.delete("/api/price-catalog")
async def clear_catalog(platform: Optional[str] = None, _admin: None = Depends(require_admin_access)):
    """Delete all (or one platform's) entries from the price catalog."""
    with get_db() as db:
        if platform:
            identity = canonicalize_platform(platform)
            db.execute("DELETE FROM price_catalog WHERE platform_key = ? OR LOWER(platform) = LOWER(?)", (identity.key, identity.label))
        else:
            db.execute("DELETE FROM price_catalog")
        db.commit()
    return {"ok": True}
