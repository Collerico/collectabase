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

Each price lookup currently reads one Browse API search page, with up to 200
results. The diagnostics log includes the returned count and page limit; matches
beyond that page are not considered.

Never publish API credentials, tokens, database files, backups, or personal
deployment settings.

### Docker Compose example

The repository's `docker-compose.yml` passes this setting through by default.
In Portainer, set `EBAY_MARKETPLACE_ID` in the Stack environment. For local
Compose, put it in `.env` or run `docker compose --env-file stack.env up -d`;
Compose does not automatically load a file named `stack.env`.

For another marketplace, set `EBAY_MARKETPLACE_ID` to its corresponding ID and
recreate the container so it receives the new value.
