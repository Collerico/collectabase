# Market Price sources

The Market Prices panel has three actions with different purposes:

- **Price Browser** opens the local PriceCharting catalog filtered by the current title/platform and links the selected catalog entry to the collection item. The browser has a separate manual scrape action; opening it does not call eBay.
- **PriceCharting** searches both the local catalog and PriceCharting's public product pages on every click, even when the local catalog already has a match. The live scrape sends the item's title and platform, and may try saved alternate titles, to PriceCharting; it does not send collection notes, location, description, or purchase data. It does not require a PriceCharting API token on this single-item path. It does not fall back to eBay. If neither PriceCharting source returns a usable price, the panel shows an informational message with a **Try eBay** action.
- **eBay** calls eBay Browse directly and does not use PriceCharting or any other fallback.

The GUI shows each available condition tier as a separate selectable estimate. PriceCharting can return Loose, CIB, and New values from either the local catalog or live scrape. eBay returns an overall market median plus any condition-specific estimates supported by listing evidence. Selecting a tier updates the collection item's current value; it does not erase other quotes from history.

## API provenance

The API keeps the stable top-level `source` field (`pricecharting` or `ebay`). For PriceCharting, each source candidate has `source_detail` (`local_catalog` or `live_scrape`) and a `price_options` list containing only tiers with values. When both PriceCharting paths return values, the top level uses `source_detail: "combined"` and provides a `suggestions` array containing both candidates. For compatibility, the top-level `market_price` prefers the live scrape when it exists, otherwise it uses the local catalog result. The eBay response likewise provides selectable `price_options` under its `suggestions`; its top-level `market_price` remains the overall median across accepted listings, not a Loose-only estimate. The GUI uses individual condition options rather than choosing that legacy primary automatically.

| `source` | `source_detail` | Meaning |
|---|---|---|
| `pricecharting` | `local_catalog` | Match from Collectabase's local PriceCharting catalog |
| `pricecharting` | `live_scrape` | Price read from a live PriceCharting web scrape |
| `pricecharting` | `combined` | Both sources returned candidates; see `suggestions` |
| `ebay` | `direct_ebay` | The eBay action queried Browse directly |

## Price history

Each PriceCharting source candidate is recorded separately in the existing `price_history.source` text field as `pricecharting_local` or `pricecharting_scrape`; each history row retains all available Loose/CIB/New values. eBay condition options are recorded with source labels `ebay_market`, `ebay_loose`, `ebay_cib`, and `ebay_new`. Older rows may still use the generic `ebay` or `pricecharting` source values. No schema migration is needed. The UI labels the overall eBay median as a mixed-condition market estimate, not Loose.

## Condition classification limits

- PriceCharting values use the provider's explicit Loose/CIB/New fields. A tier with no returned value is omitted rather than copied from another tier.
- eBay's **Market median** uses all accepted listings after the existing relevance, condition, currency, and price filters. It can contain mixed conditions and must not be interpreted as a Loose price.
- eBay **Loose** is emitted only when a game listing title explicitly says `loose`, `disc only`, `cartridge only`, `game only`, or equivalent supported wording. **CIB** requires an explicit title cue such as `CIB`, `complete in box`, or `complete with box/case`; eBay's generic used-condition IDs or a title that only mentions packaging are not enough to infer completeness. Box/case/manual-only and replacement packaging listings are excluded from the game estimate.
- eBay **New** is based only on Browse `conditionId` `1000`. eBay documents this ID as brand-new/unopened in original retail packaging. ID `1500` is “New other”/open-box or packaging-missing, so it is not mixed into the sealed-New estimate.
- Listings without sufficient evidence remain in the overall market median but are not assigned to Loose/CIB/New. Each condition estimate shows its own usable sample count; absent or sparse tiers are not guessed. Title text is seller-provided and may be incomplete or inaccurate.
