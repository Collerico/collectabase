# Market Price sources

The Market Prices panel has three actions with different purposes:

- **Price Browser** opens the local PriceCharting catalog filtered by the current title/platform and links the selected catalog entry to the collection item. The browser has a separate manual scrape action; opening it does not call eBay.
- **PriceCharting** searches both the local catalog and PriceCharting's public product pages on every click, even when the local catalog already has a match. The live scrape sends the item's title and platform, and may try saved alternate titles, to PriceCharting; it does not send collection notes, location, description, or purchase data. It does not require a PriceCharting API token on this single-item path. It does not fall back to eBay. If neither PriceCharting source returns a usable price, the panel shows an informational message with a **Try eBay** action.
- **eBay** calls eBay Browse directly and does not use PriceCharting or any other fallback.

When PriceCharting returns one or two candidates, the GUI shows each candidate with its source and a separate **Set as current value** action. Selecting one updates the collection item's current value; it does not erase the other quote from history.

## API provenance

The API keeps the stable top-level `source` field (`pricecharting` or `ebay`). For a single candidate, `source_detail` identifies the source; when both PriceCharting paths return values, the top level uses `source_detail: "combined"` and provides a `suggestions` array containing the individual candidates. For compatibility, the top-level `market_price` prefers the live scrape when it exists, otherwise it uses the local catalog result. The Market Prices GUI uses the individual suggestions rather than choosing that legacy primary automatically.

| `source` | `source_detail` | Meaning |
|---|---|---|
| `pricecharting` | `local_catalog` | Match from Collectabase's local PriceCharting catalog |
| `pricecharting` | `live_scrape` | Price read from a live PriceCharting web scrape |
| `pricecharting` | `combined` | Both sources returned candidates; see `suggestions` |
| `ebay` | `direct_ebay` | The eBay action queried Browse directly |

## Price history

Each available PriceCharting candidate is recorded separately in the existing `price_history.source` text field as `pricecharting_local` or `pricecharting_scrape`. The UI labels these entries clearly. Older rows and other existing catalog-update jobs may still use the generic `pricecharting` value. No schema migration is needed. When both candidates exist, the live scrape is inserted last and remains the top-level legacy `market_price`; the local candidate is still retained as a separate history entry and selectable current value.
