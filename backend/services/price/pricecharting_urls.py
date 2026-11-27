"""Normalize trusted PriceCharting product links to origin-relative paths."""

import re
from typing import Optional
from urllib.parse import unquote, urlsplit, urlunsplit


def normalize_pricecharting_product_url(href: str) -> Optional[str]:
    """Return a validated /game/platform/product path, preserving its query.

    Only the bare and www PriceCharting hosts with HTTP(S) default ports
    are trusted. Callers attach the fixed HTTPS origin to the returned path;
    fragments are not needed for product requests.
    """
    if not isinstance(href, str) or not href:
        return None
    # urlsplit silently strips some control characters; reject them first.
    if "\\" in href or any(
        c.isspace() or ord(c) < 32 or ord(c) == 127 for c in href
    ):
        return None
    if re.search(r"%(?![0-9a-fA-F]{2})", href):
        return None
    try:
        parsed = urlsplit(href)
        if parsed.scheme and parsed.scheme not in ("http", "https"):
            return None
        if parsed.netloc:
            if parsed.hostname not in ("pricecharting.com", "www.pricecharting.com"):
                return None
            if parsed.username is not None or parsed.password is not None:
                return None
            default_port = 80 if parsed.scheme == "http" else 443
            if parsed.netloc.endswith(":") or parsed.port not in (None, default_port):
                return None
        elif parsed.scheme or href.startswith("//"):
            return None

        path = "/" + parsed.path.lstrip("/")
        if not re.fullmatch(r"/game/[^/]+/[^/]+/?", path):
            return None
        # Reject traversal and encoded separators without rewriting valid slugs.
        for segment in path.strip("/").split("/")[1:]:
            decoded = unquote(segment, errors="strict")
            if decoded in (".", "..") or any(
                c in "/\\?#" or ord(c) < 32 or ord(c) == 127 for c in decoded
            ):
                return None
        return urlunsplit(("", "", path, parsed.query, ""))
    except (ValueError, UnicodeError):
        return None
