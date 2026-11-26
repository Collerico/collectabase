# eBay GTIN barcode lookup

Collectabase first looks up a barcode with UPCitemdb. If UPCitemdb returns no
product, Collectabase tries the eBay Browse API using the barcode as a GTIN.

eBay matches are suggestions, not guaranteed product identifications. Check
the title, platform, edition, and region before adding a result to your
collection.

## Configure the eBay marketplace

Set the `EBAY_MARKETPLACE_ID` environment variable to select the eBay
marketplace used for GTIN barcode searches. If the variable is not set,
Collectabase defaults to `EBAY_IT`.

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
the eBay Client ID and Client Secret in Collectabase Settings. Never publish
API credentials, tokens, database files, backups, or personal deployment
settings.

### Docker Compose example

Add the following environment entry to the Collectabase service:

    - EBAY_MARKETPLACE_ID=${EBAY_MARKETPLACE_ID:-EBAY_IT}

For a different marketplace, set `EBAY_MARKETPLACE_ID` to the corresponding
marketplace ID in the stack environment. Recreate the container after
changing the variable so that it receives the new value.
