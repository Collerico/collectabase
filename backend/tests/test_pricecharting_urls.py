import asyncio
import unittest
from unittest.mock import patch

import httpx


class PriceChartingScrapeURLTests(unittest.TestCase):
    def test_catalog_excludes_spoofed_hosts_and_preserves_trusted_prices(self):
        # Import lazily: catalog imports database configuration at import time.
        from backend.services.price import catalog

        html = """
        <table id="games_table"><tbody>
          <tr><td class="title"><a href="https://pricecharting.com.evil.test/game/nes/fake">Fake</a></td>
              <td class="used_price">$999.00</td></tr>
          <tr><td class="title"><a href="https://evil.test/pricecharting.com/game/nes/fake">Fake</a></td>
              <td class="used_price">$999.00</td></tr>
          <tr><td class="title"><a href="https://user@pricecharting.com/game/nes/fake">Fake</a></td>
              <td class="used_price">$999.00</td></tr>
          <tr><td class="title"><a href="javascript:/game/nes/fake">Fake</a></td>
              <td class="used_price">$999.00</td></tr>
          <tr><td class="title"><a href="https://www.pricecharting.com/game/nes/mario">Mario</a></td>
              <td class="used_price">$12.50</td><td class="cib_price">$20.00</td>
              <td class="new_price">$30.00</td></tr>
        </tbody></table>
        """
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, text=html)

        client_class = httpx.AsyncClient
        transport = httpx.MockTransport(respond)
        with patch.object(catalog.httpx, "AsyncClient", side_effect=lambda **kw: client_class(transport=transport, **kw)):
            result = asyncio.run(catalog.scrape_platform_catalog("nes", "NES"))

        self.assertEqual(len(requests), 1)
        self.assertEqual(result, [{
            "pricecharting_id": "mario",
            "title": "Mario",
            "platform": "NES",
            "loose_usd": 12.5,
            "cib_usd": 20.0,
            "new_usd": 30.0,
            "page_url": "https://www.pricecharting.com/game/nes/mario",
        }])

    def test_catalog_preserves_query_without_including_it_in_product_identifier(self):
        from backend.services.price import catalog

        html = """
        <table id="games_table"><tbody><tr>
          <td class="title"><a href="game/nes/mario?variant=pal&amp;type=prices">Mario</a></td>
          <td class="used_price">$12.50</td>
        </tr></tbody></table>
        """
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html))
        client_class = httpx.AsyncClient
        with patch.object(catalog.httpx, "AsyncClient", side_effect=lambda **kw: client_class(transport=transport, **kw)):
            result = asyncio.run(catalog.scrape_platform_catalog("nes", "NES"))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["pricecharting_id"], "mario")
        self.assertEqual(result[0]["page_url"], "https://www.pricecharting.com/game/nes/mario?variant=pal&type=prices")
        self.assertEqual(result[0]["loose_usd"], 12.5)

    def test_live_scrape_skips_untrusted_link_before_selecting_valid_price(self):
        from backend.services.price.providers import pricecharting

        search_html = """
        <a href="https://evil.test/pricecharting.com/game/nes/fake">Mario NES</a>
        <a href="https://pricecharting.com.evil.test/game/nes/fake">Mario NES</a>
        <a href="https://user@pricecharting.com/game/nes/fake">Mario NES</a>
        <a href="javascript:/game/nes/fake">Mario NES</a>
        <a href="//pricecharting.com/game/nes/mario?variant=pal&amp;type=prices">Mario NES</a>
        """
        product_html = """
        <h1 id="product_name">Mario</h1>
        <div id="used_price"><span class="price">$12.50</span></div>
        <div id="complete_price">$20.00</div><div id="new_price">$30.00</div>
        """
        requested_urls = []

        def respond(request):
            requested_urls.append(str(request.url))
            if request.url.path == "/search-products":
                return httpx.Response(200, text=search_html)
            return httpx.Response(200, text=product_html)

        client_class = httpx.AsyncClient
        transport = httpx.MockTransport(respond)
        with patch.object(pricecharting.httpx, "AsyncClient", side_effect=lambda **kw: client_class(transport=transport, **kw)):
            result = asyncio.run(pricecharting._fetch_pricecharting_scrape("Mario", "NES"))

        self.assertEqual(requested_urls, [
            "https://www.pricecharting.com/search-products?q=Mario%20NES&type=prices",
            "https://www.pricecharting.com/game/nes/mario?variant=pal&type=prices",
        ])
        self.assertEqual(result, {
            "pricecharting_id": "mario",
            "product_name": "Mario",
            "loose_usd": 12.5,
            "cib_usd": 20.0,
            "new_usd": 30.0,
            "page_url": "https://www.pricecharting.com/game/nes/mario?variant=pal&type=prices",
        })

    def test_live_scrape_accepts_rootless_relative_product_link(self):
        from backend.services.price.providers import pricecharting

        requested_paths = []

        def respond(request):
            requested_paths.append(request.url.path)
            if request.url.path == "/search-products":
                return httpx.Response(200, text='<a href="game/nes/mario">Mario NES</a>')
            return httpx.Response(200, text='<div id="used_price">$12.50</div>')

        transport = httpx.MockTransport(respond)
        client_class = httpx.AsyncClient
        with patch.object(pricecharting.httpx, "AsyncClient", side_effect=lambda **kw: client_class(transport=transport, **kw)):
            result = asyncio.run(pricecharting._fetch_pricecharting_scrape("Mario", "NES"))
        self.assertIsNotNone(result)
        self.assertEqual(result["page_url"], "https://www.pricecharting.com/game/nes/mario")
        self.assertEqual(result["loose_usd"], 12.5)
        self.assertEqual(requested_paths, ["/search-products", "/game/nes/mario"])


class PriceChartingURLNormalizationTests(unittest.TestCase):
    def test_normalizes_trusted_product_links_without_losing_query_strings(self):
        from backend.services.price.pricecharting_urls import normalize_pricecharting_product_url

        path = "/game/nes/mario?variant=pal&type=prices&name=Super%20Mario"
        for href in (
            path,
            path[1:],
            "https://www.pricecharting.com" + path,
            "http://pricecharting.com" + path,
            "https://PRICECHARTING.COM:443" + path,
            "http://www.pricecharting.com:80" + path,
            "//pricecharting.com" + path,
            "//www.pricecharting.com:443" + path,
            path + "#prices",
        ):
            with self.subTest(href=href):
                self.assertEqual(normalize_pricecharting_product_url(href), path)

    def test_rejects_untrusted_origins_and_unsafe_or_malformed_product_links(self):
        from backend.services.price.pricecharting_urls import normalize_pricecharting_product_url

        for href in (
            None, "", 123,
            "https://pricecharting.com.evil.test/game/nes/mario",
            "https://evilpricecharting.com/game/nes/mario",
            "https://evil.test/pricecharting.com/game/nes/mario",
            "https://evil.test/game/nes/mario?host=pricecharting.com",
            "https://api.pricecharting.com/game/nes/mario",
            "https://pricecharting.com@evil.test/game/nes/mario",
            "https://evil.test@pricecharting.com/game/nes/mario",
            "https://user:password@www.pricecharting.com/game/nes/mario",
            "https://@pricecharting.com/game/nes/mario",
            "//evil.test/game/nes/mario",
            "https://pricecharting.com:444/game/nes/mario",
            "http://pricecharting.com:443/game/nes/mario",
            "https://pricecharting.com:abc/game/nes/mario",
            "https://pricecharting.com:99999/game/nes/mario",
            "https://pricecharting.com:/game/nes/mario",
            "https://[pricecharting.com/game/nes/mario",
            "https://pricecharting.com./game/nes/mario",
            "https://pricecharting.com%2eevil.test/game/nes/mario",
            "javascript:/game/nes/mario",
            "data:/game/nes/mario",
            "ftp://pricecharting.com/game/nes/mario",
            "https:/game/nes/mario",
            "https:///game/nes/mario",
            "///game/nes/mario",
            "////pricecharting.com/game/nes/mario",
            " https://pricecharting.com/game/nes/mario",
            "https://pricecharting.com/ga\tme/nes/mario",
            "/game/nes/mar\nio",
            "/game/nes/mario\x00",
            "/game/nes/mario title",
            "/game/nes/mario\\evil",
            "/game/nes/mario%ZZ",
            "/game/nes/mario%",
            "/game/nes/%2e%2e",
            "/game/nes/..",
            "/game/./mario",
            "/game/nes/%2f%2fevil.test",
            "/game/nes/mario%5cevil",
            "/game/nes/mario%0d%0a",
            "/game/nes/%FF",
            "/game//mario",
            "/game/nes/",
            "/game/nes",
            "/console/nes",
            "/game/nes/mario/../../console/nes",
        ):
            with self.subTest(href=href):
                self.assertIsNone(normalize_pricecharting_product_url(href))
