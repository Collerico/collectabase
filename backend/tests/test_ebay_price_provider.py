import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

ebay = None


def setUpModule():
    global ebay
    from backend.services.price.providers import ebay as ebay_module

    ebay = ebay_module


class _Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, response, captured):
        self.response = response
        self.captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, *, params, headers):
        self.captured.update(url=url, params=params, headers=headers)
        return self.response


class EbayPriceProviderTests(unittest.TestCase):
    def _search(self, item, *, marketplace=None, item_type="game"):
        return self._search_items([item], marketplace=marketplace, item_type=item_type)

    def _search_items(self, items, *, marketplace=None, item_type="game"):
        captured = {}
        response = _Response({"itemSummaries": items})
        client = _Client(response, captured)
        env = {"EBAY_MARKETPLACE_ID": marketplace or ""}
        with (
            patch.dict(os.environ, env),
            patch.object(ebay, "get_ebay_token", new=AsyncMock(return_value="test-token")),
            patch.object(ebay.httpx, "AsyncClient", return_value=client),
        ):
            result = asyncio.run(
                ebay.fetch_ebay_market_price(
                    "Zelda Breath of the Wild", "Nintendo Switch", item_type
                )
            )
        return result, captured

    def _log_text(self, log_mock):
        messages = []
        for call in log_mock.call_args_list:
            template, *arguments = call.args
            messages.append(template % tuple(arguments) if arguments else template)
        return " ".join(messages)

    def test_price_estimate_ignores_shipping_by_default(self):
        item = {
            "title": "The Legend of Zelda Breath of the Wild Nintendo Switch",
            "conditionId": "2750",
            "price": {"value": "40.00", "currency": "EUR"},
            "shippingOptions": [{"shippingCost": {"value": "4.90", "currency": "EUR"}}],
        }

        result, _captured = self._search(item)

        self.assertEqual(result["market_price"], 40.0)


    def test_game_search_accepts_additional_game_conditions_and_excludes_others(self):
        conditions = ["1000", "1500", "2750", "3000", "4000", "5000", "6000", "7000"]
        items = [
            {
                "title": "The Legend of Zelda Breath of the Wild Nintendo Switch",
                "conditionId": condition,
                "price": {"value": str(20 + index), "currency": "EUR"},
                "shippingOptions": [],
            }
            for index, condition in enumerate(conditions)
        ]
        captured = {}
        client = _Client(_Response({"itemSummaries": items}), captured)
        with (
            patch.dict(os.environ, {"EBAY_MARKETPLACE_ID": ""}),
            patch.object(ebay, "get_ebay_token", new=AsyncMock(return_value="test-token")),
            patch.object(ebay.httpx, "AsyncClient", return_value=client),
        ):
            result = asyncio.run(ebay.fetch_ebay_market_price("Zelda Breath of the Wild", "Nintendo Switch"))

        self.assertEqual(result["sample_size"], 7)
        self.assertNotIn("filter", captured["params"])
        self.assertEqual(captured["params"]["limit"], "200")





    def test_console_search_keeps_its_own_condition_policy(self):
        item = {
            "title": "Zelda Breath of the Wild Nintendo Switch",
            "conditionId": "5000",
            "price": {"value": "40.00", "currency": "EUR"},
        }

        with patch.object(ebay.logger, "info") as log_info:
            result, _captured = self._search(item, item_type="console")

        self.assertIsNone(result)
        self.assertIn("condition_excluded:1", self._log_text(log_info))



    def test_non_eur_item_price_is_excluded_instead_of_assumed_eur(self):
        item = {
            "title": "The Legend of Zelda Breath of the Wild Nintendo Switch",
            "conditionId": "2750",
            "price": {"value": "40.00", "currency": "USD"},
        }

        with patch.object(ebay.logger, "info") as log_info:
            result, _captured = self._search(item)

        self.assertIsNone(result)
        self.assertIn("price_currency_unconverted:1", self._log_text(log_info))

    def test_exclusions_are_logged_by_reason_without_titles_or_credentials(self):
        items = [
            {
                "title": "The Legend of Zelda Breath of the Wild Bundle",
                "conditionId": "2750",
                "price": {"value": "20", "currency": "EUR"},
            },
            {
                "title": "The Legend of Zelda Breath of the Wild defective",
                "conditionId": "2750",
                "price": {"value": "30", "currency": "EUR"},
            },
            {
                "title": "Mario Kart unrelated title",
                "conditionId": "2750",
                "price": {"value": "30", "currency": "EUR"},
            },
            {
                "title": "The Legend of Zelda Breath of the Wild 10 Spiele",
                "conditionId": "2750",
                "price": {"value": "30", "currency": "EUR"},
            },
            {
                "title": "The Legend of Zelda Breath of the Wild",
                "conditionId": "2750",
                "shippingOptions": [],
            },
        ]

        with patch.object(ebay.logger, "info") as log_info:
            self._search_items(items)

        text = self._log_text(log_info)
        for reason in ("keyword_bundle_or_defect:2", "keyword_multiple_items:1", "title_not_relevant:1", "price_missing:1"):
            self.assertIn(reason, text)
        self.assertIn("api_returned=5", text)
        self.assertNotIn("Zelda Breath", text)
        self.assertNotIn("20100", text)
        self.assertNotIn("test-token", text)


    def test_one_listing_still_produces_an_estimate(self):
        item = {
            "title": "The Legend of Zelda Breath of the Wild Nintendo Switch",
            "conditionId": "2750",
            "price": {"value": "40.00", "currency": "EUR"},
            "shippingOptions": [{"shippingCost": {"value": "4.00", "currency": "EUR"}}],
        }

        result, _captured = self._search(item)

        self.assertEqual(result["market_price"], 40.0)
        self.assertEqual(result["sample_size"], 1)


    def test_non_numeric_item_price_is_excluded_and_diagnosed(self):
        item = {
            "title": "The Legend of Zelda Breath of the Wild Nintendo Switch",
            "conditionId": "2750",
            "price": {"value": "not-a-number", "currency": "EUR"},
        }

        with patch.object(ebay.logger, "info") as log_info:
            result, _captured = self._search(item)

        self.assertIsNone(result)
        self.assertIn("price_invalid:1", self._log_text(log_info))

    def test_outlier_trim_count_is_logged(self):
        items = [
            {
                "title": "The Legend of Zelda Breath of the Wild Nintendo Switch",
                "conditionId": "2750",
                "price": {"value": str(price), "currency": "EUR"},
                "shippingOptions": [],
            }
            for price in [10, 10, 10, 10, 10, 10, 10, 1000]
        ]

        with patch.object(ebay.logger, "info") as log_info:
            result, _captured = self._search_items(items)

        self.assertEqual(result["sample_size"], 7)
        self.assertIn("trimmed_outliers=1", self._log_text(log_info))

    def test_marketplace_defaults_to_italy(self):
        item = {
            "title": "The Legend of Zelda Breath of the Wild Nintendo Switch",
            "conditionId": "2750",
            "price": {"value": "40.00", "currency": "EUR"},
        }

        result, captured = self._search(item)

        self.assertEqual(result["market_price"], 40.0)
        self.assertEqual(captured["headers"]["X-EBAY-C-MARKETPLACE-ID"], "EBAY_IT")
        self.assertNotIn("X-EBAY-C-ENDUSERCTX", captured["headers"])

    def test_configured_marketplace_overrides_italian_default(self):
        item = {
            "title": "The Legend of Zelda Breath of the Wild Nintendo Switch",
            "conditionId": "2750",
            "price": {"value": "40.00", "currency": "EUR"},
        }

        _result, captured = self._search(item, marketplace="EBAY_FR")

        self.assertEqual(captured["headers"]["X-EBAY-C-MARKETPLACE-ID"], "EBAY_FR")

    def test_missing_condition_id_is_reported_as_exclusion(self):
        item = {
            "title": "The Legend of Zelda Breath of the Wild Nintendo Switch",
            "price": {"value": "40.00", "currency": "EUR"},
        }

        with patch.object(ebay.logger, "info") as log_info:
            result, _captured = self._search(item)

        self.assertIsNone(result)
        self.assertIn("condition_missing:1", self._log_text(log_info))




if __name__ == "__main__":
    unittest.main()
