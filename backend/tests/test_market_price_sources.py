import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

price_tracker = None


def setUpModule():
    global price_tracker
    from backend import price_tracker as tracker_module
    price_tracker = tracker_module


class MarketPriceSourceTests(unittest.TestCase):
    def _run_lookup(self, *, source="pricecharting", item_type="game", catalog=None, scrape=None, ebay=None):
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
        return result, catalog_lookup, scrape_mock, ebay_mock, fake_db

    def test_catalog_and_live_scrape_both_run_and_are_returned_separately(self):
        catalog = {
            "loose_eur": 21.0,
            "cib_eur": 30.0,
            "new_eur": 45.0,
            "pricecharting_id": "pc-1",
            "product_name": "The Legend of Zelda Breath of the Wild",
            "platform": "Nintendo Switch",
            "match_score": 0.99,
        }
        scrape_result = {
            "loose_usd": 25.0,
            "cib_usd": 35.0,
            "new_usd": 50.0,
            "pricecharting_id": "pc-1-live",
        }
        result, catalog_lookup, scrape, ebay, fake_db = self._run_lookup(
            catalog=catalog, scrape=scrape_result
        )

        self.assertEqual(result["source"], "pricecharting")
        self.assertEqual(result["source_detail"], "combined")
        self.assertEqual(
            [candidate["source_detail"] for candidate in result["suggestions"]],
            ["local_catalog", "live_scrape"],
        )
        self.assertEqual(result["suggestions"][0]["market_price"], 21.0)
        self.assertEqual(result["market_price"], result["suggestions"][1]["market_price"])
        for candidate, expected in zip(result["suggestions"], [
            {"loose": 21.0, "complete": 30.0, "new": 45.0},
            {"loose": 25.0, "complete": 35.0, "new": 50.0},
        ]):
            self.assertEqual(
                {option["condition"]: option["market_price"] for option in candidate["price_options"]},
                expected,
            )
        self.assertEqual(catalog_lookup.call_count, 1)
        scrape.assert_awaited_once()
        ebay.assert_not_awaited()
        history_writes = [
            call for call in fake_db.execute.call_args_list
            if "INSERT INTO price_history" in call.args[0]
        ]
        self.assertEqual(len(history_writes), 2)
        self.assertEqual(
            [call.args[1][1] for call in history_writes],
            ["pricecharting_local", "pricecharting_scrape"],
        )
        self.assertEqual(
            [call.args[1][2] for call in history_writes],
            [candidate["market_price"] for candidate in result["suggestions"]],
        )

    def test_scrape_hit_identifies_live_pricecharting_source_without_ebay(self):
        scrape_result = {
            "loose_usd": 10.0,
            "cib_usd": 20.0,
            "new_usd": 30.0,
            "pricecharting_id": "pc-2",
        }
        result, catalog_lookup, scrape, ebay, fake_db = self._run_lookup(scrape=scrape_result)

        self.assertEqual(result["source"], "pricecharting")
        self.assertEqual(result["source_detail"], "live_scrape")
        self.assertEqual(catalog_lookup.call_count, 2)
        scrape.assert_awaited_once()
        ebay.assert_not_awaited()

    def test_no_pricecharting_result_returns_info_suggesting_ebay_without_fallback(self):
        result, _catalog_lookup, scrape, ebay, _fake_db = self._run_lookup()

        self.assertEqual(result["suggested_action"], "ebay")
        self.assertIn("Try the eBay button", result["info"])
        scrape.assert_awaited_once()
        ebay.assert_not_awaited()

    def test_unsupported_pricecharting_type_suggests_direct_ebay_without_fallback(self):
        result, catalog_lookup, scrape, ebay, fake_db = self._run_lookup(item_type="manga")

        self.assertEqual(result["suggested_action"], "ebay")
        self.assertIn("Try the eBay button", result["info"])
        catalog_lookup.assert_not_called()
        scrape.assert_not_awaited()
        ebay.assert_not_awaited()

    def test_direct_ebay_path_is_distinguished_from_fallback(self):
        ebay_result = {
            "market_price": 39.4,
            "sample_size": 2,
            "price_min": 22.0,
            "price_max": 56.8,
        }
        result, catalog_lookup, scrape, ebay, fake_db = self._run_lookup(
            source="ebay", catalog={"loose_eur": 10.0}, ebay=ebay_result
        )

        self.assertEqual(result["source"], "ebay")
        self.assertEqual(result["source_detail"], "direct_ebay")
        catalog_lookup.assert_not_called()
        scrape.assert_not_awaited()
        ebay.assert_awaited_once()

    def test_direct_ebay_returns_and_saves_each_condition_tier(self):
        ebay_result = {
            "market_price": 50.0,
            "sample_size": 5,
            "price_min": 20.0,
            "price_max": 100.0,
            "complete_price": 60.0,
            "new_price": 100.0,
            "price_options": [
                {"condition": "market", "label": "Market median (mixed conditions)", "market_price": 50.0, "sample_size": 5},
                {"condition": "loose", "label": "Loose (explicit listing text)", "market_price": 20.0, "sample_size": 1},
                {"condition": "complete", "label": "CIB (explicit title text)", "market_price": 60.0, "sample_size": 1},
                {"condition": "new", "label": "New (eBay condition New)", "market_price": 100.0, "sample_size": 1},
            ],
        }

        result, _catalog_lookup, _scrape, _ebay, fake_db = self._run_lookup(
            source="ebay", ebay=ebay_result
        )

        self.assertEqual(
            [option["condition"] for option in result["suggestions"][0]["price_options"]],
            ["market", "loose", "complete", "new"],
        )
        history_writes = [
            call for call in fake_db.execute.call_args_list
            if "INSERT INTO price_history" in call.args[0]
        ]
        self.assertEqual(len(history_writes), 4)
        self.assertEqual(
            [call.args[1][1] for call in history_writes],
            ["ebay_market", "ebay_loose", "ebay_cib", "ebay_new"],
        )
        self.assertEqual(
            [call.args[1][2:] for call in history_writes],
            [
                (50.0, None, None),
                (20.0, None, None),
                (None, 60.0, None),
                (None, None, 100.0),
            ],
        )


if __name__ == "__main__":
    unittest.main()
