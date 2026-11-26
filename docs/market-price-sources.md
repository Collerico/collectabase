# Market Price sources

The Market Prices panel has three actions with different purposes:

- **Price Browser** opens the local PriceCharting catalog filtered by the current title/platform and links the selected catalog entry to the collection item. The browser also has a separate manual scrape action; opening it does not call eBay.
- **PriceCharting** runs the single-item price lookup. Today it checks the local PriceCharting catalog first, then scrapes PriceCharting's public product pages if there is no local match, and finally falls back to eBay if neither PriceCharting path returns a price. The single-item route does not require a PriceCharting API token.
- **eBay** calls eBay Browse directly and does not fall back to PriceCharting.

The API response keeps its stable `source` field (`pricecharting` or `ebay`) and adds `source_detail` for the path that produced the current suggestion:

| `source` | `source_detail` | Meaning |
|---|---|---|
| `pricecharting` | `local_catalog` | Match from Collectabase's local PriceCharting catalog |
| `pricecharting` | `live_scrape` | Price read from a live PriceCharting web scrape |
| `ebay` | `automatic_ebay_fallback` | The automatic PriceCharting-first flow used eBay (including unsupported PriceCharting item types) |
| `ebay` | `direct_ebay` | The eBay action queried Browse directly |

The GUI labels these paths in the suggestion itself. This audit only adds provenance; it intentionally leaves the existing PriceCharting-first-to-eBay fallback behavior unchanged.
