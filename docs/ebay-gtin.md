# eBay GTIN barcode lookup

Collectabase first looks up a barcode with UPCitemdb. If UPCitemdb returns no
product, Collectabase tries the eBay Browse API using the barcode as a GTIN.

eBay matches are suggestions, not guaranteed product identifications. Check
the title, platform, edition, and region before adding a result to your
collection.

## Configure the eBay marketplace

Set the `EBAY_MARKETPLACE_ID` environment variable to select the eBay
marketplace used for GTIN barcode searches and eBay price estimates. If the
variable is not set, Collectabase defaults to `EBAY_IT`.

Common examples:

- `EBAY_IT` — Italy
- `EBAY_GB` — United Kingdom
- `EBAY_US` — United States
- `EBAY_CA` — Canada
- `EBAY_AU` — Australia
- `EBAY_DE` — Germany
- `EBAY_FR` — France
- `EBAY_ES` — Spain

The marketplace setting is separate from the eBay API credentials. Configure
the eBay Client ID and Client Secret in Collectabase Settings. Price estimates
use only the item's EUR price by default. A separate, off-by-default option in
Settings can include the lowest valid EUR shipping option; if eBay does not
provide one, the item's price remains usable and the unknown shipping status is
logged. The destination country follows the selected marketplace, so no CAP is
requested. Without a postcode, eBay may omit a calculated shipping amount; the
estimate then falls back to the item price. Item prices without a valid EUR
currency are excluded and foreign currencies are not converted.

A standard price lookup reads one Browse API search page, with up to 200
results. Optional deep search can issue up to eight keyword queries plus one
separate GTIN query, but reads only the first page of each. The diagnostics log
counts returned and truncated searches; deep estimates are marked partial when
the Browse API `next` link indicates an unfetched page.

## Price estimates by condition

PriceCharting presents each tier it actually returns from the local catalog and live scrape: Loose, CIB, and New. Missing tiers are not inferred from another value.

The eBay estimate keeps a median for all accepted listings, labelled **Market median (mixed conditions)**. It is not a Loose-only estimate. Additional selectable estimates are created only when the listing evidence supports them:

- **Loose** requires explicit game-title wording such as `loose`, `disc only`, `cartridge only`, or `game only`.
- **CIB** requires explicit title wording such as `CIB`, `complete in box`, or `complete with box/case`. A used-condition code, or a title that only mentions a box/manual without saying the game is complete, does not establish CIB. Box/case/manual-only and replacement packaging listings are excluded from game estimates.
- **New** uses the Browse `conditionId` value `1000`; eBay defines this as brand-new and unopened in original retail packaging. `1500` (“New other” / open box or packaging missing) is not treated as sealed New.

These eBay condition-specific buckets are conservative: unclassified listings still contribute to the overall market median, but are not assigned to Loose/CIB/New. The GUI shows the sample count for each tier, and a tier with no usable listings is omitted. Existing price filters, EUR validation, optional shipping behavior, and outlier trimming apply before each median is calculated. CIB/Loose title cues are used only for game listings; New condition ID `1000` can be used for other supported item types as well. Seller titles can be incomplete or wrong, so estimates are suggestions and should be checked against the listings.

See eBay's [condition ID definitions](https://developer.ebay.com/api-docs/sell/static/metadata/condition-id-values.html) and the Browse API [ItemSummary type](https://developer.ebay.com/api-docs/buy/browse/types/gct:ItemSummary) for the documented condition fields.

Never publish API credentials, tokens, database files, backups, or personal
deployment settings.

### Docker Compose example

The repository's `docker-compose.yml` passes this setting through by default.
In Portainer, set `EBAY_MARKETPLACE_ID` in the Stack environment. For local
Compose, put it in `.env` or run `docker compose --env-file stack.env up -d`;
Compose does not automatically load a file named `stack.env`.

For another marketplace, set `EBAY_MARKETPLACE_ID` to its corresponding ID and
recreate the container so it receives the new value.

## Optional deep eBay price search

The eBay button has a per-search **Deep eBay search** option. It is off by
default, so the existing single title/platform request remains unchanged. When
enabled, Collectabase can issue up to eight keyword queries using the title,
saved alternate titles, region/platform-format hints, and selected product
identifiers/attributes. A valid UPC/EAN or ISBN barcode (UPC-A, EAN-8/EAN-13, or ISBN-10/13) is sent as a separate
`gtin` request because the Browse API does not allow `gtin` and keyword `q` in
the same request. Results are merged and deduplicated by eBay item ID before
filtering and price calculation. Deep searches stop issuing requests after an
eBay rate-limit or authorization failure. They have a 35-second total budget and
an 8-second per-request timeout. Collectabase reads one Browse response page per
query (up to 200 listings); the API's documented `next` link is used to detect
additional pages, which are not fetched in this opt-in path. If queries fail,
are skipped due to the budget, or have more pages, the GUI labels the estimate
as partial rather than implying that all queries completed. See the [Browse
search pagination reference](https://developer.ebay.com/api-docs/buy/browse/resources/item_summary/methods/search.html)
for the documented `next` field.


For games, common saved regions are expanded into hints such as Japan/NTSC-J,
USA/NTSC-U, and Europe/PAL. These are additional searches, not hard region
filters; listings with no region in their title can still match the ordinary
query. Saved product fields can include condition/completeness, publisher, developer,
genre, series, character, scale, format, and release year; these are search hints,
not hard condition filters. Notes, location, description,
purchase price/date, and current value are deliberately not sent to eBay.

Deep mode can take longer, still return few usable listings, and include false
positives because sellers often omit or inconsistently describe edition and region. Check
listing titles before relying on the estimate. The standard lookup remains the
recommended default when broad coverage is more important than narrowing the
edition.
