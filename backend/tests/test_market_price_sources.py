import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

price_tracker = None


def setUpModule():
    global price_tracker
    from backend import price_tracker as tracker_module
    price_tracker = tracker_module


class MarketPriceSourceTests(unittest.TestCase):
    def _run_lookup(self, *, source=None, item_type="game", catalog=None, scrape=None, ebay=None):
        game = {
            "title": "The Legend of Zelda Breath of the Wild",
            "platform_name": "Nintendo Switch",
            "item_type": item_type,
        }
        scrape_mock = AsyncMock(return_value=scrape)
        ebay_mock = AsyncMock(return_value=ebay)
        fake_db = MagicMock()
        db_context = MagicMock()
        db_context.__enter__.return_value = fake_db
        db_context.__exit__.return_value = False
        with (
            patch.object(price_tracker, "_get_game_for_price_lookup", return_value=game),
            patch.object(price_tracker, "_ebay_credentials", return_value=("id", "secret")),
            patch.object(price_tracker, "_rawg_key", return_value=None),
            patch.object(price_tracker, "_lookup_local_catalog_price", return_value=catalog) as catalog_lookup,
            patch.object(price_tracker, "_fetch_pricecharting_scrape", new=scrape_mock),
            patch.object(price_tracker, "fetch_ebay_market_price", new=ebay_mock),
            patch.object(price_tracker, "get_eur_rate", new=AsyncMock(return_value=1.0)),
            patch.object(price_tracker, "get_db", return_value=db_context),
        ):
            result = asyncio.run(price_tracker.fetch_market_price(17, source=source))
        return result, catalog_lookup, scrape_mock, ebay_mock

    def test_catalog_hit_identifies_local_catalog_without_scrape_or_ebay(self):
        catalog = {
            "loose_eur": 21.0,
            "cib_eur": 30.0,
            "new_eur": 45.0,
            "pricecharting_id": "pc-1",
            "product_name": "The Legend of Zelda Breath of the Wild",
            "platform": "Nintendo Switch",
            "match_score": 0.99,
        }
        result, catalog_lookup, scrape, ebay = self._run_lookup(catalog=catalog)

        self.assertEqual(result["source"], "pricecharting")
        self.assertEqual(result["source_detail"], "local_catalog")
        catalog_lookup.assert_called_once()
        scrape.assert_not_awaited()
        ebay.assert_not_awaited()

    def test_scrape_hit_identifies_live_pricecharting_source_without_ebay(self):
        scrape_result = {
            "loose_usd": 10.0,
            "cib_usd": 20.0,
            "new_usd": 30.0,
            "pricecharting_id": "pc-2",
        }
        result, catalog_lookup, scrape, ebay = self._run_lookup(scrape=scrape_result)

        self.assertEqual(result["source"], "pricecharting")
        self.assertEqual(result["source_detail"], "live_scrape")
        self.assertEqual(catalog_lookup.call_count, 2)
        scrape.assert_awaited_once()
        ebay.assert_not_awaited()

    def test_pricecharting_path_labels_existing_ebay_fallback(self):
        ebay_result = {
            "market_price": 39.4,
            "sample_size": 2,
            "price_min": 22.0,
            "price_max": 56.8,
        }
        result, _catalog_lookup, scrape, ebay = self._run_lookup(ebay=ebay_result)

        self.assertEqual(result["source"], "ebay")
        self.assertEqual(result["source_detail"], "automatic_ebay_fallback")
        scrape.assert_awaited_once()
        ebay.assert_awaited_once()

    def test_unsupported_pricecharting_type_is_labeled_as_automatic_ebay_fallback(self):
        ebay_result = {
            "market_price": 15.0,
            "sample_size": 1,
            "price_min": 15.0,
            "price_max": 15.0,
        }
        result, catalog_lookup, scrape, ebay = self._run_lookup(
            item_type="manga", ebay=ebay_result
        )

        self.assertEqual(result["source"], "ebay")
        self.assertEqual(result["source_detail"], "automatic_ebay_fallback")
        catalog_lookup.assert_not_called()
        scrape.assert_not_awaited()
        ebay.assert_awaited_once()

    def test_direct_ebay_path_is_distinguished_from_fallback(self):
        ebay_result = {
            "market_price": 39.4,
            "sample_size": 2,
            "price_min": 22.0,
            "price_max": 56.8,
        }
        result, catalog_lookup, scrape, ebay = self._run_lookup(
            source="ebay", catalog={"loose_eur": 10.0}, ebay=ebay_result
        )

        self.assertEqual(result["source"], "ebay")
        self.assertEqual(result["source_detail"], "direct_ebay")
        catalog_lookup.assert_not_called()
        scrape.assert_not_awaited()
        ebay.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
