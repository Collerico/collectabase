# Security update: CodeQL alerts 2–9

## Scope

This update addresses the Python CodeQL findings for import error reporting,
PriceCharting product URLs, and frontend file serving. It does not suppress
queries, dismiss findings, or change the CodeQL configuration.

The administrator key in browser localStorage (alert 1) is explicitly out of
scope. No authentication, database schema, dependency, or deployment configuration
changes are included.

## Compatibility and security boundaries

- CSV and CLZ imports keep their response fields, partial-import behavior and
  counters. Expected input validation remains actionable. Unexpected internal
  exception messages are replaced by controlled messages; technical diagnostics
  stay in server logs rather than HTTP responses.
- PriceCharting product links are parsed and checked against trusted origins
  instead of searching for a domain substring. Legitimate relative and absolute
  product links continue to work. Foreign origins and malformed links are rejected.
  Price matching, price tiers, currency handling, and provider fallback order are
  unchanged. Tests use HTTP fixtures, not live requests.
- Frontend file requests are delegated to Starlette StaticFiles with its default
  canonical-path containment and external-symlink protections. Missing paths,
  deep SPA routes, traversal attempts, and invalid null/overlong paths fall back
  to index.html. Unknown `/api/` endpoints remain JSON 404 responses. Existing
  static files benefit from the framework's standard conditional caching.

## Verification

The CI backend command includes all three security regression modules after the
existing smoke/provider suites. Regression tests cover safe and failing imports,
valid and hostile PriceCharting links, ordinary frontend files, SPA deep links,
encoded traversal, sibling-directory prefixes, and external symlinks.

Run from the repository root:

```sh
npm --prefix frontend ci
npm --prefix frontend run build
.venv/bin/python -m unittest backend.tests.test_api_smoke backend.tests.test_ebay_price_provider backend.tests.test_market_price_sources backend.tests.test_frontend_security backend.tests.test_import_security backend.tests.test_pricecharting_urls
.venv/bin/python -m unittest discover -s backend/tests -p 'test_*.py'
(cd frontend && npx vue-tsc --noEmit)
node --test frontend/tests/*.test.mjs
```

GitHub Actions and a fresh CodeQL Python analysis must also pass before merge;
local tests alone do not establish that the GitHub alerts are fixed.
