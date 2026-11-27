import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

ebay = None
settings_routes = None
price_tracker = None


def setUpModule():
    global ebay, settings_routes, price_tracker
    from backend.api.routes import settings as settings_module
    from backend.services.price.providers import ebay as ebay_module
    from backend import price_tracker as price_tracker_module

    ebay = ebay_module
    settings_routes = settings_module
    price_tracker = price_tracker_module


class _Response:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, response, captured):
        self.responses_by_key = response if isinstance(response, dict) else None
        self.responses = list(response) if isinstance(response, list) else [response]
        self.captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, url, *, params, headers, timeout=None):
        request = {"url": url, "params": dict(params), "headers": dict(headers), "timeout": timeout}
        self.captured.setdefault("requests", []).append(request)
        self.captured.update(request)
        if self.responses_by_key is not None:
            if "gtin" in params:
                return self.responses_by_key["gtin"]
            return self.responses_by_key.get(params.get("q"), self.responses_by_key["q"])
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


class EbayPriceProviderTests(unittest.TestCase):
    def _search(self, item, *, marketplace=None, item_type="game", include_shipping=False):
        return self._search_items(
            [item], marketplace=marketplace, item_type=item_type, include_shipping=include_shipping
        )

    def _search_items(
        self, items, *, marketplace=None, item_type="game", include_shipping=False,
        deep_search=False, search_details=None, responses=None,
    ):
        captured = {}
        if responses is None:
            responses = [_Response({"itemSummaries": items})]
        client = _Client(responses, captured)
        env = {"EBAY_MARKETPLACE_ID": marketplace or ""}
        with (
            patch.dict(os.environ, env),
            patch.object(ebay, "get_ebay_token", new=AsyncMock(return_value="test-token")),
            patch.object(ebay.httpx, "AsyncClient", return_value=client),
            patch.object(
                ebay,
                "get_app_meta_many",
                return_value={"ebay_include_shipping": "1" if include_shipping else "0"},
            ),
        ):
            result = asyncio.run(
                ebay.fetch_ebay_market_price(
                    "Zelda Breath of the Wild", "Nintendo Switch", item_type,
                    deep_search=deep_search, search_details=search_details,
                )
            )
        return result, captured

    def _log_text(self, log_mock):
        messages = []
        for call in log_mock.call_args_list:
            template, *arguments = call.args
            messages.append(template % tuple(arguments) if arguments else template)
        return " ".join(messages)

    def test_shipping_toggle_setting_is_saved_as_explicit_boolean(self):
        payload = settings_routes.SecretsUpdate(ebay_include_shipping=True)

        with patch.object(settings_routes, "set_app_meta") as set_app_meta:
            result = asyncio.run(settings_routes.update_secrets(payload, _admin=None))

        self.assertIn("ebay_include_shipping", result["updated"])
        set_app_meta.assert_called_once_with("ebay_include_shipping", "1")

    def test_shipping_toggle_adds_lowest_valid_eur_cost(self):
        item = {
            "title": "The Legend of Zelda Breath of the Wild Nintendo Switch",
            "conditionId": "2750",
            "price": {"value": "40.00", "currency": "EUR"},
            "shippingOptions": [
                {"shippingCost": {"value": "8.00", "currency": "EUR"}},
                {"shippingCost": {"value": "4.90", "currency": "EUR"}},
            ],
        }

        result, captured = self._search(item, marketplace="EBAY_GB", include_shipping=True)

        self.assertEqual(result["market_price"], 44.9)
        self.assertEqual(captured["headers"]["X-EBAY-C-MARKETPLACE-ID"], "EBAY_GB")
        self.assertNotIn("X-EBAY-C-ENDUSERCTX", captured["headers"])

    def test_shipping_toggle_falls_back_to_item_price_when_cost_is_not_eur(self):
        item = {
            "title": "The Legend of Zelda Breath of the Wild Nintendo Switch",
            "conditionId": "2750",
            "price": {"value": "40.00", "currency": "EUR"},
            "shippingOptions": [{"shippingCost": {"value": "5.00", "currency": "USD"}}],
        }

        with patch.object(ebay.logger, "info") as log_info:
            result, _captured = self._search(item, include_shipping=True)

        self.assertEqual(result["market_price"], 40.0)
        self.assertIn("shipping_currency_unconverted:1", self._log_text(log_info))

    def test_enabled_shipping_cost_of_zero_is_treated_as_free(self):
        item = {
            "title": "The Legend of Zelda Breath of the Wild Nintendo Switch",
            "conditionId": "2750",
            "price": {"value": "40.00", "currency": "EUR"},
            "shippingOptions": [{"shippingCost": {"value": "0.00", "currency": "EUR"}}],
        }

        with patch.object(ebay.logger, "info") as log_info:
            result, _captured = self._search(item, include_shipping=True)

        self.assertEqual(result["market_price"], 40.0)
        self.assertIn("shipping_free:1", self._log_text(log_info))

    def test_enabled_shipping_missing_falls_back_to_item_price(self):
        item = {
            "title": "The Legend of Zelda Breath of the Wild Nintendo Switch",
            "conditionId": "2750",
            "price": {"value": "40.00", "currency": "EUR"},
            "shippingOptions": [],
        }

        with patch.object(ebay.logger, "info") as log_info:
            result, _captured = self._search(item, include_shipping=True)

        self.assertEqual(result["market_price"], 40.0)
        self.assertIn("shipping_missing:1", self._log_text(log_info))

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


    def test_standard_mode_stays_one_title_platform_query_even_if_metadata_exists(self):
        item = {
            "itemId": "standard-1",
            "title": "The Legend of Zelda Breath of the Wild Nintendo Switch",
            "conditionId": "2750",
            "price": {"value": "40.00", "currency": "EUR"},
        }
        _result, captured = self._search_items(
            [item], deep_search=False,
            search_details={
                "alt_titles": ["Zelda BOTW"],
                "region": "Japan",
                "barcode": "036000291452",
            },
        )

        self.assertEqual(len(captured["requests"]), 1)
        self.assertEqual(captured["requests"][0]["params"], {
            "q": "Zelda Breath of the Wild Nintendo Switch", "limit": "200"
        })

    def test_deep_search_uses_region_aliases_alternative_titles_and_safe_metadata(self):
        item = {
            "itemId": "v1",
            "title": "The Legend of Zelda Breath of the Wild Nintendo Switch",
            "conditionId": "2750",
            "price": {"value": "40.00", "currency": "EUR"},
        }
        details = {
            "alt_titles": ["Zelda BOTW Japan"],
            "region": "Japan",
            "publisher": "Nintendo",
            "developer": "Nintendo",
            "release_date": "2017-03-03",
            "genre": "Action",
            "condition": "Very Good",
            "completeness": "Complete in Box",
            "description": "Do not include this long description in a query",
            "notes": "private note must never be sent",
            "location": "private shelf location",
            "purchase_price": 123.45,
        }

        result, captured = self._search_items(
            [item], deep_search=True, search_details=details
        )

        queries = [request["params"].get("q", "") for request in captured["requests"]]
        self.assertGreater(len(queries), 1)
        self.assertIn("Zelda BOTW Japan Nintendo Switch", queries)
        self.assertIn("Zelda Breath of the Wild Nintendo Switch Japan", queries)
        self.assertIn("Zelda Breath of the Wild Nintendo Switch NTSC-J", queries)
        self.assertTrue(any("Nintendo" in query for query in queries))
        self.assertTrue(any("2017" in query for query in queries))
        joined_queries = " ".join(queries)
        for private_value in ("private note", "private shelf", "123.45", "long description"):
            self.assertNotIn(private_value, joined_queries)
        self.assertEqual(result["sample_size"], 1)

    def test_deep_search_uses_gtin_separately_and_deduplicates_listings(self):
        title_match = {
            "itemId": "v1",
            "title": "The Legend of Zelda Breath of the Wild Nintendo Switch",
            "conditionId": "2750",
            "price": {"value": "40.00", "currency": "EUR"},
        }
        gtin_only_match = {
            "itemId": "v2",
            "title": "Generic product title",
            "conditionId": "2750",
            "price": {"value": "60.00", "currency": "EUR"},
        }
        response_map = {
            "q": _Response({"itemSummaries": [title_match]}),
            "gtin": _Response({"itemSummaries": [title_match, gtin_only_match]}),
        }

        result, captured = self._search_items(
            [], deep_search=True, search_details={"barcode": "036000291452"},
            responses=response_map,
        )

        gtin_requests = [r["params"] for r in captured["requests"] if "gtin" in r["params"]]
        self.assertEqual(len(gtin_requests), 1)
        self.assertEqual(gtin_requests[0]["gtin"], "036000291452")
        self.assertNotIn("q", gtin_requests[0])
        self.assertEqual(result["sample_size"], 2)
        self.assertEqual(result["market_price"], 50.0)

    def test_short_game_titles_match_exact_tokens_without_failing_open(self):
        self.assertTrue(
            ebay._title_is_relevant("F1 2020 PlayStation 4", "F1", "PlayStation 4", "game")
        )
        self.assertFalse(
            ebay._title_is_relevant("FIFA 20 PlayStation 4", "F1", "PlayStation 4", "game")
        )
        self.assertFalse(
            ebay._title_is_relevant("Unrelated game listing", "F1", "PlayStation 4", "game")
        )
        self.assertFalse(
            ebay._title_is_relevant("FIFA 20 PlayStation 4", "F1 2020", "PlayStation 4", "game")
        )

    def test_deep_search_accepts_alternative_title_and_keeps_bundle_filter(self):
        alternative_listing = {
            "itemId": "alt-1",
            "title": "Zelda BOTW Japan Edition",
            "conditionId": "2750",
            "price": {"value": "50.00", "currency": "EUR"},
        }
        bundle_listing = {
            "itemId": "gtin-bundle",
            "title": "Generic product title bundle",
            "conditionId": "2750",
            "price": {"value": "100.00", "currency": "EUR"},
        }
        responses = {
            "q": _Response({"itemSummaries": [alternative_listing]}),
            "gtin": _Response({"itemSummaries": [alternative_listing, bundle_listing]}),
        }

        result, _captured = self._search_items(
            [], deep_search=True,
            search_details={"alt_titles": ["Zelda BOTW"], "barcode": "036000291452"},
            responses=responses,
        )

        self.assertEqual(result["sample_size"], 1)
        self.assertEqual(result["market_price"], 50.0)

    def test_region_term_mapping_handles_mixed_format_labels(self):
        self.assertEqual(ebay._region_search_terms("JP / NTSC-J"), ["Japan", "NTSC-J"])
        self.assertEqual(ebay._region_search_terms("North America (NTSC-U)"), ["USA", "NTSC-U"])

    def test_deep_search_marks_partial_results_when_one_request_is_rate_limited(self):
        item = {
            "itemId": "v1",
            "title": "The Legend of Zelda Breath of the Wild Nintendo Switch",
            "conditionId": "2750",
            "price": {"value": "40.00", "currency": "EUR"},
        }
        responses = {
            "q": _Response({"itemSummaries": [item]}),
            "Zelda BOTW Nintendo Switch": _Response({}, status_code=429),
        }

        result, captured = self._search_items(
            [], deep_search=True,
            search_details={"alt_titles": ["Zelda BOTW"]},
            responses=responses,
        )

        self.assertEqual(result["sample_size"], 1)
        self.assertFalse(result["search_complete"])
        self.assertEqual(result["failed_searches"], 1)
        self.assertEqual(len(captured["requests"]), 2)

    def test_deep_search_does_not_apply_video_region_standards_to_comics(self):
        item = {
            "itemId": "comic-1",
            "title": "Batman Comic",
            "conditionId": "2750",
            "price": {"value": "10.00", "currency": "EUR"},
        }
        _result, captured = self._search_items(
            [item], item_type="comic", deep_search=True,
            search_details={"region": "Italy"},
        )
        queries = [request["params"].get("q", "") for request in captured["requests"]]
        self.assertIn("Zelda Breath of the Wild Italy", queries)
        self.assertFalse(any("PAL" in query or "NTSC" in query for query in queries))

    def test_deep_search_marks_unfetched_next_page_as_partial(self):
        item = {
            "itemId": "v1",
            "title": "The Legend of Zelda Breath of the Wild Nintendo Switch",
            "conditionId": "2750",
            "price": {"value": "40.00", "currency": "EUR"},
        }
        responses = {
            "q": _Response({
                "itemSummaries": [item],
                "next": "https://api.ebay.com/buy/browse/v1/item_summary/search?offset=200",
            })
        }

        result, captured = self._search_items(
            [], deep_search=True, responses=responses
        )

        self.assertEqual(len(captured["requests"]), 1)
        self.assertEqual(result["sample_size"], 1)
        self.assertFalse(result["search_complete"])
        self.assertEqual(result["truncated_searches"], 1)

    def test_deep_search_deadline_cancels_slow_token_acquisition(self):
        cancelled = []
        async def slow_token():
            try:
                await asyncio.sleep(0.2)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
            return "test-token"

        captured = {}
        client = _Client(_Response({"itemSummaries": []}), captured)
        with (
            patch.object(ebay, "_DEEP_SEARCH_DURATION_SECONDS", 0.05),
            patch.object(ebay, "get_ebay_token", new=AsyncMock(side_effect=slow_token)),
            patch.object(ebay.httpx, "AsyncClient", return_value=client),
            patch.object(ebay, "get_app_meta_many", return_value={"ebay_include_shipping": "0"}),
        ):
            result = asyncio.run(ebay.fetch_ebay_market_price(
                "Zelda Breath of the Wild", "Nintendo Switch", deep_search=True
            ))

        self.assertTrue(cancelled)
        self.assertFalse(result["search_complete"])
        self.assertIn("time limit", result["error"])
        self.assertEqual(captured.get("requests", []), [])

    def test_deep_search_deadline_cancels_slow_browse_request(self):
        cancelled = []
        captured = {}

        class SlowClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_args):
                return None
            async def get(self, url, *, params, headers, timeout=None):
                captured.setdefault("requests", []).append({"params": dict(params), "timeout": timeout})
                try:
                    await asyncio.sleep(0.2)
                except asyncio.CancelledError:
                    cancelled.append(True)
                    raise
                return _Response({"itemSummaries": []})

        with (
            patch.object(ebay, "_DEEP_SEARCH_DURATION_SECONDS", 0.05),
            patch.object(ebay, "get_ebay_token", new=AsyncMock(return_value="test-token")),
            patch.object(ebay.httpx, "AsyncClient", return_value=SlowClient()),
            patch.object(ebay, "get_app_meta_many", return_value={"ebay_include_shipping": "0"}),
        ):
            result = asyncio.run(ebay.fetch_ebay_market_price(
                "Zelda Breath of the Wild", "Nintendo Switch", deep_search=True
            ))

        self.assertTrue(cancelled)
        self.assertFalse(result["search_complete"])
        self.assertIn("time limit", result["error"])
        self.assertEqual(len(captured["requests"]), 1)
        self.assertLessEqual(captured["requests"][0]["timeout"], 0.05)

    def test_deep_search_obeys_total_deadline_and_reports_skipped_requests(self):
        item = {
            "itemId": "v1",
            "title": "The Legend of Zelda Breath of the Wild Nintendo Switch",
            "conditionId": "2750",
            "price": {"value": "40.00", "currency": "EUR"},
        }
        with patch.object(ebay, "_search_clock", create=True, side_effect=[100.0, 100.0, 100.0, 136.0]):
            result, captured = self._search_items(
                [item], deep_search=True,
                search_details={"alt_titles": ["Zelda BOTW"]},
            )

        self.assertEqual(len(captured["requests"]), 1)
        self.assertFalse(result["search_complete"])
        self.assertEqual(result["skipped_searches"], 1)
        self.assertLessEqual(captured["requests"][0]["timeout"], 8)

    def test_deep_search_stops_after_authorization_failure(self):
        responses = {"q": _Response({}, status_code=403)}

        result, captured = self._search_items(
            [], deep_search=True,
            search_details={"alt_titles": ["Zelda BOTW"]},
            responses=responses,
        )

        self.assertEqual(len(captured["requests"]), 1)
        self.assertFalse(result["search_complete"])
        self.assertEqual(result["failed_searches"], 1)
        self.assertIn("403", result["error"])

    def test_deep_search_returns_explicit_error_when_failures_leave_no_estimate(self):
        responses = {"q": _Response({}, status_code=503)}

        result, _captured = self._search_items(
            [], deep_search=True, search_details={"alt_titles": ["Zelda BOTW"]},
            responses=responses,
        )

        self.assertFalse(result["search_complete"])
        self.assertEqual(result["failed_searches"], 2)
        self.assertIn("Some eBay deep-search requests failed", result["error"])

    def test_deep_search_caps_keyword_requests(self):
        details = {
            "alt_titles": [f"Alternate title {index}" for index in range(20)],
            "region": "Japan",
            "publisher": "Nintendo",
            "developer": "Nintendo EPD",
            "genre": "Action",
            "series_name": "The Legend of Zelda",
            "character_name": "Link",
            "completeness": "Complete in Box",
            "condition": "Very Good",
            "barcode": "036000291452",
        }
        requests, _titles = ebay._build_search_requests(
            "Zelda Breath of the Wild", "Nintendo Switch", "game",
            deep_search=True, search_details=details,
        )
        self.assertLessEqual(sum("q" in params for params, _ in requests), 8)
        self.assertLessEqual(sum("gtin" in params for params, _ in requests), 1)

    def test_deep_search_skips_invalid_gtin(self):
        item = {
            "itemId": "v1",
            "title": "The Legend of Zelda Breath of the Wild Nintendo Switch",
            "conditionId": "2750",
            "price": {"value": "40.00", "currency": "EUR"},
        }

        _result, captured = self._search_items(
            [item], deep_search=True, search_details={"barcode": "not-a-valid-gtin"}
        )

        self.assertTrue(captured["requests"])
        self.assertFalse(any("gtin" in r["params"] for r in captured["requests"]))

    def test_gtin_normalization_supports_valid_ean_and_isbn10(self):
        self.assertEqual(ebay._normalise_gtin("036000291452"), "036000291452")
        self.assertEqual(ebay._normalise_gtin("0306406152"), "9780306406157")
        self.assertIsNone(ebay._normalise_gtin("036000291453"))
        self.assertIsNone(ebay._normalise_gtin("not-a-code"))
        self.assertIsNone(ebay._normalise_gtin("12345678901231"))
        self.assertIsNone(ebay._normalise_gtin("barcode: 036000291452"))
        self.assertIsNone(ebay._normalise_gtin("٠٣٦٠٠٠٢٩١٤٥٢"))

    def test_market_price_endpoint_passes_only_product_metadata_for_deep_ebay_search(self):
        game = {
            "title": "The Legend of Zelda Breath of the Wild",
            "platform_name": "Nintendo Switch",
            "item_type": "game",
            "alt_titles": '["Zelda BOTW"]',
            "region": "Japan",
            "barcode": "036000291452",
            "publisher": "Nintendo",
            "developer": "Nintendo EPD",
            "genre": "Action",
            "release_date": "2017-03-03",
            "description": "long provider text",
            "notes": "private note",
            "location": "private location",
            "purchase_price": 123.45,
        }
        search = AsyncMock(return_value=None)
        with (
            patch.object(price_tracker, "_get_game_for_price_lookup", return_value=game),
            patch.object(price_tracker, "_ebay_credentials", return_value=("id", "secret")),
            patch.object(price_tracker, "fetch_ebay_market_price", new=search),
        ):
            result = asyncio.run(
                price_tracker.fetch_market_price(7, source="ebay", deep_search=True)
            )

        self.assertEqual(result["error"], "No eBay listings found for this game.")
        search.assert_awaited_once()
        kwargs = search.await_args.kwargs
        self.assertTrue(kwargs["deep_search"])
        details = kwargs["search_details"]
        self.assertEqual(details["alt_titles"], ["Zelda BOTW"])
        self.assertEqual(details["region"], "Japan")
        self.assertEqual(details["barcode"], "036000291452")
        for private_key in ("description", "notes", "location", "purchase_price"):
            self.assertNotIn(private_key, details)

    def test_fetch_market_price_http_route_parses_deep_search_query_parameter(self):
        app = FastAPI()
        app.include_router(price_tracker.router)
        game = {
            "title": "Zelda Breath of the Wild",
            "platform_name": "Nintendo Switch",
            "item_type": "game",
            "alt_titles": "[]",
            "region": "Japan",
        }
        search = AsyncMock(return_value=None)
        with (
            patch.object(price_tracker, "_get_game_for_price_lookup", return_value=game),
            patch.object(price_tracker, "_ebay_credentials", return_value=("id", "secret")),
            patch.object(price_tracker, "fetch_ebay_market_price", new=search),
            TestClient(app) as client,
        ):
            response = client.post(
                "/api/games/7/fetch-market-price?source=ebay&deep_search=true"
            )
            alias_response = client.post("/api/games/7/price-check?deep_search=true")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(alias_response.status_code, 200)
        self.assertEqual(search.await_count, 2)
        self.assertTrue(all(call.kwargs["deep_search"] for call in search.await_args_list))
        self.assertEqual(search.await_args_list[0].kwargs["search_details"]["region"], "Japan")

    def test_market_price_endpoint_exposes_partial_estimate_status(self):
        game = {
            "title": "Zelda Breath of the Wild",
            "platform_name": "Nintendo Switch",
            "item_type": "game",
        }
        partial = {
            "market_price": 40.0,
            "sample_size": 1,
            "price_min": 40.0,
            "price_max": 40.0,
            "search_complete": False,
            "failed_searches": 1,
            "skipped_searches": 2,
            "truncated_searches": 3,
        }
        search = AsyncMock(return_value=partial)
        fake_db = MagicMock()
        db_context = MagicMock()
        db_context.__enter__.return_value = fake_db
        db_context.__exit__.return_value = False
        with (
            patch.object(price_tracker, "_get_game_for_price_lookup", return_value=game),
            patch.object(price_tracker, "_ebay_credentials", return_value=("id", "secret")),
            patch.object(price_tracker, "fetch_ebay_market_price", new=search),
            patch.object(price_tracker, "get_db", return_value=db_context),
        ):
            result = asyncio.run(
                price_tracker.fetch_market_price(7, source="ebay", deep_search=True)
            )

        self.assertEqual(result["market_price"], 40.0)
        self.assertFalse(result["search_complete"])
        self.assertEqual(result["failed_searches"], 1)
        self.assertEqual(result["skipped_searches"], 2)
        self.assertEqual(result["truncated_searches"], 3)

    def test_market_price_endpoint_keeps_deep_search_opt_in(self):
        game = {
            "title": "The Legend of Zelda Breath of the Wild",
            "platform_name": "Nintendo Switch",
            "item_type": "game",
            "alt_titles": '["Zelda BOTW"]',
            "region": "Japan",
            "barcode": "036000291452",
        }
        search = AsyncMock(return_value=None)
        with (
            patch.object(price_tracker, "_get_game_for_price_lookup", return_value=game),
            patch.object(price_tracker, "_ebay_credentials", return_value=("id", "secret")),
            patch.object(price_tracker, "fetch_ebay_market_price", new=search),
        ):
            asyncio.run(price_tracker.fetch_market_price(7, source="ebay"))

        kwargs = search.await_args.kwargs
        self.assertFalse(kwargs["deep_search"])
        self.assertIsNone(kwargs["search_details"])

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



    def test_price_options_split_mixed_loose_cib_and_new_using_explicit_evidence(self):
        def listing(title, condition_id, price):
            return {
                "title": title,
                "conditionId": condition_id,
                "price": {"value": str(price), "currency": "EUR"},
            }

        items = [
            listing("The Legend of Zelda Breath of the Wild Nintendo Switch", "3000", 40),
            listing("The Legend of Zelda Breath of the Wild Nintendo Switch CIB complete in box", "3000", 60),
            listing("The Legend of Zelda Breath of the Wild Nintendo Switch", "1000", 100),
            listing("The Legend of Zelda Breath of the Wild Nintendo Switch new open box", "1500", 80),
            listing("The Legend of Zelda Breath of the Wild Nintendo Switch loose cartridge only", "3000", 20),
            listing("The Legend of Zelda Breath of the Wild Nintendo Switch box and manual", "3000", 70),
            listing("The Legend of Zelda Breath of the Wild Nintendo Switch box only", "3000", 10),
        ]

        result, _captured = self._search_items(items)

        options = {option["condition"]: option for option in result["price_options"]}
        self.assertEqual(result["market_price"], 65.0)
        self.assertEqual(result["sample_size"], 6)
        self.assertEqual(options["market"]["sample_size"], 6)
        self.assertEqual(options["loose"]["market_price"], 20.0)
        self.assertEqual(options["loose"]["sample_size"], 1)
        self.assertEqual(options["complete"]["market_price"], 60.0)
        self.assertEqual(options["complete"]["sample_size"], 1)
        self.assertEqual(options["new"]["market_price"], 100.0)
        self.assertEqual(options["new"]["sample_size"], 1)

    def test_box_only_listing_is_excluded_from_game_market_estimate(self):
        items = [
            {
                "title": "The Legend of Zelda Breath of the Wild Nintendo Switch",
                "conditionId": "3000",
                "price": {"value": "50.00", "currency": "EUR"},
            },
            {
                "title": "The Legend of Zelda Breath of the Wild Nintendo Switch box only",
                "conditionId": "3000",
                "price": {"value": "5.00", "currency": "EUR"},
            },
        ]

        result, _captured = self._search_items(items)

        self.assertEqual(result["market_price"], 50.0)
        self.assertEqual(result["sample_size"], 1)



if __name__ == "__main__":
    unittest.main()
