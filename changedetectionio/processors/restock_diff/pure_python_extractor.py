"""
Pure Python metadata extractor - no lxml, no memory leaks.

This module provides a fast, memory-efficient alternative to extruct for common
e-commerce metadata extraction. It handles:
- JSON-LD (covers 80%+ of modern sites)
- OpenGraph meta tags
- Basic microdata attributes

Uses Python's built-in html.parser instead of lxml/libxml2, avoiding C-level
memory allocation issues. For edge cases, the main processor can fall back to
extruct (with subprocess isolation on Linux).
"""

from html.parser import HTMLParser
import html as html_module
from urllib.parse import unquote, parse_qs, urlparse
import json
import re
from loguru import logger
from . import MISSING_STRINGS, Restock

_UNSET = object()
_VOID_TAGS = {
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
    'link', 'meta', 'param', 'source', 'track', 'wbr',
}
_PRICE_SKIP_TAGS = {'del', 's', 'strike', 'script', 'style'}


def _is_missing(value):
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in MISSING_STRINGS
    return False


def _as_price(value):
    import math
    if _is_missing(value) or isinstance(value, (bool, list, dict, tuple, set, frozenset)):
        return None
    if isinstance(value, (int, float)):
        try:
            value = float(value)
        except (OverflowError, ValueError):
            return None
        return value if math.isfinite(value) else None
    price = Restock().parse_currency(str(value))
    return price if price is not None and math.isfinite(price) else None


def _single_value(value, parser):
    values = value if isinstance(value, list) else [value]
    parsed = {parser(item) for item in values}
    parsed.discard(None)
    return parsed.pop() if len(parsed) == 1 else None


def _single_price(value):
    return _single_value(value, _as_price)


def _single_currency(value):
    return _single_value(
        value, lambda item: None if _is_missing(item) else str(item).strip().casefold())


def _single_availability(value):
    return _single_value(value, normalize_availability)


def _as_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


def _ci_get(obj, name, default=None):
    """Case-insensitive dict lookup (JSON-LD keys are not always consistently cased)."""
    if not isinstance(obj, dict):
        return default
    wanted = name.casefold()
    for key, value in obj.items():
        if isinstance(key, str) and key.casefold() == wanted:
            return value
    return default


def _type_name(value):
    return str(value).strip().casefold().rsplit('/', 1)[-1]


def _type_names(value):
    if isinstance(value, str):
        return [_type_name(value)]
    if isinstance(value, list):
        return [_type_name(item) for item in value]
    return []


def _jsonld_types(obj):
    return _type_names(_ci_get(obj, '@type'))


def _has_variants(product):
    return bool(_as_list(_ci_get(product, 'hasVariant')))


def _normalize_name(value):
    if _is_missing(value):
        return ''
    value = html_module.unescape(str(value)).casefold()
    value = re.sub(r'[^\w]+', ' ', value)
    return re.sub(r'\s+', ' ', value).strip()


def normalize_availability(value):
    if _is_missing(value):
        return None
    value = str(value).strip().strip('"\'')
    value = re.sub(r'(?i)^https?://schema\.org/', '', value)
    value = re.sub(r'\s+', '', value)
    return value.casefold() or None


def _key_tokens(value):
    if _is_missing(value):
        return []
    value = unquote(str(value)).casefold()
    return [token for token in re.split(r'[-_.\s/:]+', value) if token]


def _significant_tokens(value):
    # Product identifiers in URLs virtually always contain a digit. This avoids
    # matching generic words such as "product", "body" or "black".
    # shortcut: alphabetic-only SKUs are not matched; widen this with a
    # false-positive test when such a shop is supported.
    return [token for token in _key_tokens(value)
            if len(token) >= 3 and any(char.isdigit() for char in token)]


def _has_significant_tokens(value):
    return bool(_significant_tokens(str(value).split('#', 1)[0].split('?', 1)[0]))


_TRAILING_ROUTE = re.compile(
    r'^(?:reviews?|ratings?|comments?|page(?:[-_]?\d+)?|details?|specs?|'
    r'description|questions?|gallery)$', re.IGNORECASE)


def watch_url_product_keys(url, include_variant=False):
    """Return product-code-like tokens from the watch URL.

    Covers `pid`/`sku` query parameters, demandware-style `dwvar_<PID>_...`
    parameter names, Shopify-style `variant` params, and codes embedded in the
    last path segment. Ancestor path segments are deliberately ignored: a
    numeric category id there can otherwise select a recommendation. Variant
    ids are only a fallback when the URL has no stronger product code, because
    they can collide with unrelated SKUs.
    """
    if not url:
        return set()

    parsed = urlparse(url)
    tokens = set()
    fallback_tokens = set()

    def add(value):
        tokens.update(_significant_tokens(value))

    for key, values in parse_qs(parsed.query).items():
        key_lower = key.casefold()
        if key_lower in ('pid', 'productid', 'product_id', 'sku'):
            for value in values:
                add(value)
        elif key_lower.startswith('dwvar_'):
            add(key[len('dwvar_'):].split('_')[0])
        elif key_lower in ('variant', 'variantid', 'variant_id', 'id'):
            for value in values:
                fallback_tokens.update(_significant_tokens(value))

    path = parsed.path.rstrip('/')
    segments = [unquote(segment) for segment in path.split('/') if segment]
    if segments:
        index = len(segments) - 1
        while index >= 0:
            segment = re.sub(r'\.(?:html?|php|aspx?)$', '', segments[index],
                             flags=re.IGNORECASE)
            if _TRAILING_ROUTE.match(segment):
                index -= 1
                continue
            if segment.isdigit() and index > 0:
                previous = re.sub(r'\.(?:html?|php|aspx?)$', '', segments[index - 1],
                                  flags=re.IGNORECASE)
                if _TRAILING_ROUTE.match(previous):
                    index -= 1
                    continue
            break
        if index >= 0:
            add(segments[index])
            if index > 0:
                previous = re.sub(r'\.(?:html?|php|aspx?)$', '', segments[index - 1],
                                  flags=re.IGNORECASE)
                if not previous.isdigit():
                    add(previous)
                elif (index > 1
                      and not segments[index].isdigit()
                      and segments[index - 2].casefold() in (
                          'p', 'product', 'products', 'dp')):
                    add(previous)

    if not tokens or include_variant:
        tokens.update(fallback_tokens)
    return tokens


def _product_keys(product, strong=False, include_variants=True):
    names = ('sku', 'productID', 'productGroupID') if strong else (
        'sku', 'productID', 'productGroupID', 'mpn', 'gtin13', '@id', 'url')
    variant_names = ('sku', 'productID') if strong else (
        'sku', 'productID', 'mpn', 'gtin13')
    keys = []
    for name in names:
        keys.extend(_string_values(_ci_get(product, name)))
    if include_variants:
        for variant in _as_list(_ci_get(product, 'hasVariant')):
            if isinstance(variant, dict):
                for name in variant_names:
                    keys.extend(_string_values(_ci_get(variant, name)))
    return keys


def _product_strong_keys(product):
    return _product_keys(product, strong=True)


def _variant_keys(variant):
    keys = []
    for name in ('sku', 'productID', 'mpn', 'gtin13', '@id', 'url'):
        keys.extend(_string_values(_ci_get(variant, name)))
    for offer in _as_list(_ci_get(variant, 'offers')):
        if isinstance(offer, dict):
            for name in ('url', '@id'):
                keys.extend(_string_values(_ci_get(offer, name)))
    return keys


def _string_values(value):
    values = value if isinstance(value, list) else [value]
    return [str(entry) for entry in values
            if isinstance(entry, (str, int, float)) and not isinstance(entry, bool)]


def _microdata_item_keys(item, strong=False):
    properties = item.get('properties') or {}
    if strong:
        values = (properties.get('sku'), properties.get('productID'))
    else:
        values = (item.get('id'), properties.get('sku'),
                  properties.get('productID'), properties.get('url'))
    keys = []
    for value in values:
        keys.extend(_string_values(value))
    return keys


def _microdata_item_strong_keys(item):
    return _microdata_item_keys(item, strong=True)


def _microdata_name_matches(main_name, names):
    main_tokens = _normalize_name(main_name).split()
    if not main_tokens:
        return False
    for name in names:
        candidate = _normalize_name(name).split()
        for index in range(len(candidate) - len(main_tokens) + 1):
            if candidate[index:index + len(main_tokens)] == main_tokens:
                return True
    return False


def _url_identity(url):
    """Return (last-path-segment code, variant values, product-id values)."""
    parsed = urlparse(str(url))
    last = re.sub(r'[^a-z0-9]+', '', _last_segment(url).casefold())
    variant_values = set()
    product_values = set()
    for key, entries in parse_qs(parsed.query).items():
        key_lower = key.casefold()
        if key_lower in ('variant', 'variantid', 'variant_id'):
            target = variant_values
        elif key_lower in ('id', 'sku', 'pid', 'productid', 'product_id'):
            target = product_values
        else:
            continue
        for entry in entries:
            normalized = re.sub(r'[^a-z0-9]+', '', entry.casefold())
            if normalized:
                target.add(normalized)
    return last, variant_values, product_values


def _identities_match(watch, other):
    watch_last, watch_variants, watch_products = watch
    other_last, other_variants, other_products = other
    if watch_variants or other_variants:
        return bool(watch_variants and other_variants
                    and watch_variants & other_variants)
    if watch_products or other_products:
        return bool(watch_products and other_products
                    and watch_products & other_products)
    return bool(watch_last and watch_last == other_last)


def _last_segment(url):
    parsed = urlparse(str(url))
    segments = [unquote(segment) for segment in parsed.path.rstrip('/').split('/') if segment]
    if not segments:
        return ''
    return re.sub(r'\.(?:html?|php|aspx?)$', '', segments[-1], flags=re.IGNORECASE)


def _same_path_host(left, right):
    left_parsed = urlparse(str(left))
    right_parsed = urlparse(str(right))
    left_host = left_parsed.netloc.casefold().removeprefix('www.')
    right_host = right_parsed.netloc.casefold().removeprefix('www.')
    if left_host and right_host and left_host != right_host:
        return False
    return _last_segment(left).casefold() == _last_segment(right).casefold()


def _variant_match_score(variant, url):
    """Return how specifically a variant matches the watched URL (0 = no match)."""
    if not url or not isinstance(variant, dict):
        return 0
    watch_identity = _url_identity(url)
    _last, variant_values, product_values = watch_identity
    last_tokens = set(_key_tokens(_last_segment(url)))
    best = 0
    for key in _variant_keys(variant):
        normalized = re.sub(r'[^a-z0-9]+', '', key.casefold())
        if '/' in key:
            if (_same_path_host(url, key)
                    and _identities_match(watch_identity, _url_identity(key))):
                best = max(best, len(normalized))
            continue
        if len(normalized) < 3:
            continue
        key_tokens = set(_key_tokens(key))
        has_digit = any(char.isdigit() for char in normalized)
        if normalized in variant_values or normalized in product_values:
            best = max(best, len(normalized))
        elif has_digit and key_tokens <= last_tokens:
            best = max(best, len(normalized))
        elif has_digit and len(key_tokens) > 1 and normalized in _last:
            best = max(best, len(normalized))
    return best


def _set_offers(product, offers):
    result = {key: value for key, value in product.items()
              if not (isinstance(key, str) and key.casefold() == 'offers')}
    result['offers'] = offers
    return result


def _variant_with_parent(variant, parent, url=None):
    result = _select_offer_by_url(variant, url) if url else {**variant}
    for field in ('priceCurrency', 'availability'):
        if _ci_get(result, field) is None and _ci_get(parent, field) is not None:
            result[field] = _ci_get(parent, field)
    offers = [offer for offer in _as_list(_ci_get(result, 'offers'))
              if isinstance(offer, dict)]
    if (not offers and _ci_get(result, 'price') is None
            and not has_explicit_missing_price(variant)):
        parent_offers = [offer for offer in _as_list(_ci_get(parent, 'offers'))
                         if isinstance(offer, dict)]
        if len(parent_offers) == 1:
            offers = parent_offers
        elif parent_offers:
            matched = [offer for offer in parent_offers
                       if _offer_matches_watch(offer, url)]
            if len(matched) == 1:
                offers = matched
        if offers:
            result = _set_offers(result, offers)
    if len(offers) > 1:
        priced = [offer for offer in offers if _offer_price(offer) is not None]
        if priced:
            return _set_offers(result, [priced[0]])
    return result


def _offer_matches_watch(offer, url):
    if not url:
        return False
    watch_identity = _url_identity(url)
    for key in (_ci_get(offer, 'url'), _ci_get(offer, '@id')):
        if (key and _same_path_host(url, key)
                and _identities_match(watch_identity, _url_identity(key))):
            return True
    return False


def _product_has_watch_offer(product, url):
    watch_identity = _url_identity(url)
    for key in (_ci_get(product, 'url'), _ci_get(product, '@id')):
        if (key and _same_path_host(url, key)
                and _identities_match(watch_identity, _url_identity(key))):
            return True
    for offer in _as_list(_ci_get(product, 'offers')):
        if isinstance(offer, dict) and _offer_matches_watch(offer, url):
            return True
    for variant in _as_list(_ci_get(product, 'hasVariant')):
        if isinstance(variant, dict) and _product_has_watch_offer(variant, url):
            return True
    return False


def _select_offer_by_url(product, url):
    """Reduce a multi-offer Product to the offer whose URL matches the watch URL."""
    offers = [offer for offer in _as_list(_ci_get(product, 'offers'))
              if isinstance(offer, dict)]
    if len(offers) < 2 or not url:
        return product
    matches = [offer for offer in offers if _offer_matches_watch(offer, url)]
    if len(matches) == 1:
        return _set_offers(product, [matches[0]])
    if matches:
        return product
    if any(has_explicit_missing_price(offer) for offer in offers):
        return product
    _last, watch_variants, watch_products = _url_identity(url)
    offer_urls = [_ci_get(offer, 'url') or _ci_get(offer, '@id') for offer in offers]
    offer_has_identity = any(
        _url_identity(offer_url)[1] or _url_identity(offer_url)[2]
        for offer_url in offer_urls if offer_url)
    watch_query_keys = set(parse_qs(urlparse(str(url)).query))
    offer_query_keys = set()
    for offer_url in offer_urls:
        if offer_url:
            offer_query_keys.update(parse_qs(urlparse(str(offer_url)).query))
    query_conflict = bool(watch_query_keys & offer_query_keys)
    if (not query_conflict
            and not watch_variants and not watch_products
            and not _has_variants(product)
            and offer_has_identity
            and all(offer_urls)):
        priced = [offer for offer in offers if _offer_price(offer) is not None]
        if priced:
            return _set_offers(product, [priced[0]])
    return product


def has_conflicting_microdata_prices(item):
    properties = item.get('properties') or {}
    offer_prices = set()
    for offer in _as_list(properties.get('offers')):
        if not isinstance(offer, dict):
            continue
        offer_properties = _unwrap_properties(offer)
        raw_price = _ci_get(offer_properties, 'price')
        for entry in (raw_price if isinstance(raw_price, list) else [raw_price]):
            price = _as_price(entry)
            if price is not None:
                offer_prices.add(price)
    if offer_prices:
        return len(offer_prices) > 1
    raw_price = _ci_get(properties, 'price')
    prices = {_as_price(entry)
              for entry in (raw_price if isinstance(raw_price, list) else [raw_price])}
    prices.discard(None)
    return len(prices) > 1


def _match_keys_score(keys, url_tokens):
    if not url_tokens:
        return 0

    best = 0
    for key in keys:
        clean_key = key.split('#', 1)[0].split('?', 1)[0]
        if '/' in clean_key:
            # Ancestor path segments are often category ids, so only the last
            # segment of a URL/id is a reliable product code.
            segment = urlparse(clean_key).path.rstrip('/').rsplit('/', 1)[-1]
            tokens = _significant_tokens(segment)
        else:
            tokens = _significant_tokens(clean_key)
        if not tokens:
            continue
        # Plain SKUs/ids usually start with the product code and may end with a
        # colour or size code.
        candidates = tokens if '/' in clean_key else tokens[:1]
        if not any(token in url_tokens for token in candidates):
            continue
        score = 1
        if all(token in url_tokens for token in tokens):
            score += 1
        if len(tokens) == 1:
            score += 1
        best = max(best, score)
    return best


def _match_product_score(product, url_tokens):
    return _match_keys_score(_product_keys(product), url_tokens)


def _product_url_path(product):
    value = _ci_get(product, 'url') or _ci_get(product, '@id')
    return urlparse(str(value)).path.rstrip('/').casefold() if isinstance(value, str) else ''


def _exact_url_match(product, url):
    if not url:
        return False
    watch_path = urlparse(str(url)).path.rstrip('/').casefold()
    return bool(watch_path and _product_url_path(product) == watch_path)


def _product_identity_confirmed(product, url):
    if not url:
        return True
    watch_tokens = watch_url_product_keys(url, include_variant=True)
    if not watch_tokens or not _product_keys(product):
        return True
    return (_exact_url_match(product, url)
            or _match_product_score(product, watch_tokens) > 0
            or _product_has_watch_offer(product, url))


def _product_identity(product):
    name = _normalize_name(_ci_get(product, 'name'))
    url = _ci_get(product, 'url') or _ci_get(product, '@id')
    parsed = urlparse(str(url)) if isinstance(url, str) else None
    path = parsed.path.rstrip('/') if parsed else ''
    query = tuple(sorted(
        (key.casefold(), value)
        for key, values in parse_qs(parsed.query).items() for value in values
    )) if parsed else ()
    return name, path, query


def _collect_product_candidates(jsonld_blocks):
    """Return [(product, is_top_level), ...] from JSON-LD blocks."""
    candidates = []
    nested_keys = {
        'hasvariant', 'issimilarto', 'isaccessoryorsparepartfor', 'isrelatedto',
        'itemlistelement', 'offers'
    }

    def walk(value, nested=False):
        if isinstance(value, list):
            for item in value:
                walk(item, nested)
            return
        if not isinstance(value, dict):
            return

        types = _jsonld_types(value)
        if 'product' in types or 'productgroup' in types:
            candidates.append((value, not nested))
            for key, child in value.items():
                if isinstance(key, str) and key.casefold() in nested_keys:
                    walk(child, True)
            return

        for key, child in value.items():
            if not isinstance(key, str) or not isinstance(child, (dict, list)):
                continue
            key_lower = key.casefold()
            if key_lower == '@graph':
                walk(child, nested)
            elif key_lower in nested_keys:
                # Recommendations are often wrapped in ItemList/offers/isSimilarTo.
                # Products inside those containers are not the main product.
                walk(child, True)
            else:
                walk(child, nested)

    for block in jsonld_blocks:
        walk(block, False)
    return candidates


def _flatten_offer(offer):
    if not isinstance(offer, dict):
        return
    yield offer
    for nested in _as_list(_ci_get(offer, 'offers')):
        yield from _flatten_offer(nested)


def _iter_offers(product):
    for offer in _as_list(_ci_get(product, 'offers')):
        yield from _flatten_offer(offer)

    for variant in _as_list(_ci_get(product, 'hasVariant')):
        if not isinstance(variant, dict):
            continue
        for offer in _as_list(_ci_get(variant, 'offers')):
            yield from _flatten_offer(offer)


def _unwrap_properties(value):
    """Extruct microdata sub-items wrap their values in a `properties` dict."""
    if isinstance(value, dict) and isinstance(value.get('properties'), dict):
        return value['properties']
    return value


def _offer_price(offer):
    price = _ci_get(offer, 'price')
    if _is_missing(price):
        for specification in _as_list(_ci_get(offer, 'priceSpecification')):
            specification = _unwrap_properties(specification)
            price = _ci_get(specification, 'price')
            if not _is_missing(price):
                break

    if _is_missing(price) and 'aggregateoffer' in _jsonld_types(offer):
        offer_count = _ci_get(offer, 'offerCount')
        if offer_count in (None, 1, '1'):
            price = _ci_get(offer, 'lowPrice')

    return _single_price(price)


def _offer_currency(offer):
    value = _ci_get(offer, 'priceCurrency')
    if _is_missing(value):
        value = _ci_get(offer, 'currency')
    if _is_missing(value):
        for specification in _as_list(_ci_get(offer, 'priceSpecification')):
            specification = _unwrap_properties(specification)
            value = _ci_get(specification, 'priceCurrency')
            if not _is_missing(value):
                break
    return _single_currency(value)


def _offer_availability(offer):
    return _single_availability(_ci_get(offer, 'availability'))


def _consensus(values, first_wins=False):
    unique = []
    for value in values:
        if value is not None and value not in unique:
            unique.append(value)
    if len(unique) == 1:
        return unique[0]
    if first_wins:
        return next((value for value in values if value is not None), None)
    return None


def product_offer_data(product):
    """Return price/currency/availability for one selected product only.

    ProductGroup/hasVariant pages intentionally use the first priced variant.
    Other products return a partial result when their offers disagree, so the
    caller can refuse instead of silently choosing one.
    """
    if not isinstance(product, dict):
        return {}

    offers = [(_offer_price(offer), _offer_currency(offer), _offer_availability(offer))
              for offer in _iter_offers(product)]
    first_wins = _has_variants(product)

    if first_wins:
        row = next((entry for entry in offers if entry[0] is not None), None)
        if row is None:
            row = next((entry for entry in offers if any(entry)), None)
        price, currency, availability = row or (None, None, None)
    else:
        prices = [entry[0] for entry in offers if entry[0] is not None]
        currencies = [entry[1] for entry in offers if entry[1]]
        availabilities = [entry[2] for entry in offers if entry[2]]
        price = _consensus(prices)
        currency = _consensus(currencies)
        availability = _consensus(availabilities)

    if price is None:
        price = _single_price(_ci_get(product, 'price'))
    if currency is None:
        currency = _single_currency(_ci_get(product, 'priceCurrency'))
    if availability is None:
        availability = _single_availability(_ci_get(product, 'availability'))

    result = {}
    if price is not None:
        result['price'] = price
    if currency is not None:
        result['currency'] = currency.upper()
    if availability is not None:
        result['availability'] = availability
    return result


def has_conflicting_product_prices(product):
    if not isinstance(product, dict):
        return False
    if _has_variants(product):
        return False
    prices = set()
    for offer in _iter_offers(product):
        price = _offer_price(offer)
        if price is not None:
            prices.add(price)
    return len(prices) > 1


def _collapse_duplicate_products(top_level_products, url=None):
    """Collapse identical product blocks after URL matching has had its say."""
    if len(top_level_products) < 2:
        return top_level_products

    if url:
        matched = [product for product in top_level_products
                   if _product_has_watch_offer(product, url)]
        if len(matched) == 1:
            return matched

    identities = [_product_identity(product) for product in top_level_products]
    if any(identity != identities[0] for identity in identities):
        return top_level_products

    offer_data = [product_offer_data(product) for product in top_level_products]
    if all(data == offer_data[0] for data in offer_data):
        return [top_level_products[0]]

    # Same name/URL but different offer data: only resolve when an offer URL
    # points at the watched variant, or when exactly one block has a price.
    if identities[0][0] and identities[0][1]:
        priced = [(product, data) for product, data in zip(top_level_products, offer_data)
                  if data.get('price') is not None]
        if len(priced) == 1:
            return [priced[0][0]]
        counts = {}
        for _product, data in priced:
            counts[data['price']] = counts.get(data['price'], 0) + 1
        if counts:
            best_price, best_count = max(counts.items(), key=lambda item: item[1])
            if best_count * 2 > len(top_level_products):
                winning = [data for _product, data in priced
                           if data['price'] == best_price]
                base = {key: value for key, value in winning[0].items() if key != 'price'}
                if all({key: value for key, value in data.items() if key != 'price'} == base
                       for data in winning):
                    return [next(product for product, data in priced
                                 if data['price'] == best_price)]
                return top_level_products
        return [top_level_products[0]]

    return top_level_products


def top_level_offers(jsonld_blocks):
    return [block for block in (jsonld_blocks or [])
            if isinstance(block, dict) and 'offer' in _jsonld_types(block)]


def top_level_product_consensus(products, offers_mode=False, url=None):
    """Return (data, has_conflict) for unmatched top-level products/offers."""
    if any(has_conflicting_product_prices(product) for product in products):
        return {}, True
    values = [product_offer_data(product) for product in products]
    all_values = list(values)
    if not offers_mode:
        if any(has_explicit_missing_price(product) for product in products):
            return {}, False
        unpriced = [(product, data) for product, data in zip(products, values)
                    if data.get('price') is None]
        if unpriced:
            priced = [(product, data) for product, data in zip(products, values)
                      if data.get('price') is not None]
            if not priced:
                return {}, False
            url_tokens = watch_url_product_keys(url, include_variant=True) if url else set()
            priced_names = {_normalize_name(_ci_get(product, 'name'))
                            for product, _data in priced}
            priced_paths = {_product_identity(product)[1] for product, _data in priced
                            if _product_identity(product)[1]}
            for product, _data in unpriced:
                if url and _match_product_score(product, url_tokens) > 0:
                    return {}, False
                if (_as_list(_ci_get(product, 'offers'))
                        or _ci_get(product, 'availability')
                        or _ci_get(product, 'priceCurrency')):
                    return {}, False
                name = _normalize_name(_ci_get(product, 'name'))
                path = _product_identity(product)[1]
                if not name or name not in priced_names:
                    return {}, False
                if not path:
                    continue
                if path in priced_paths:
                    continue
                return {}, False
            products = [product for product, data in zip(products, values)
                        if data.get('price') is not None]
            values = [data for data in values if data.get('price') is not None]
        identities = []
        for product in products:
            identity = _product_identity(product)
            if identity[1]:
                identities.append(identity)
                continue
            strong = tuple(sorted(_product_strong_keys(product)))
            if not strong:
                return {}, False
            identities.append((identity[0], strong))
        if len(set(identities)) > 1:
            identity_prices = {data.get('price') for data in values
                               if data.get('price') is not None}
            return ({}, True) if len(identity_prices) > 1 else ({}, False)
    prices = {data.get('price') for data in values if data.get('price') is not None}
    if len(prices) > 1:
        return {}, True
    if len(prices) == 1:
        priced_values = [data for data in values if data.get('price') is not None]
        result = dict(priced_values[0])
        field_values = priced_values if offers_mode else all_values
        currencies = {data.get('currency') for data in field_values if data.get('currency')}
        availabilities = {data.get('availability') for data in field_values if data.get('availability')}
        if len(currencies) == 1:
            result['currency'] = currencies.pop()
        else:
            result.pop('currency', None)
        if len(availabilities) == 1:
            result['availability'] = availabilities.pop()
        else:
            result.pop('availability', None)
        return result, False
    if offers_mode and values:
        result = dict(values[0])
        for other in values[1:]:
            _fill_missing(result, other, ('currency', 'availability'))
        return result, False
    return {}, False


def _fill_missing(target, source, fields):
    for field in fields:
        if target.get(field) is None and source.get(field) is not None:
            target[field] = source[field]
    return target


def fill_missing_from_og(result, og_data, fill_price=True):
    price_filled = False
    if fill_price and result.get('price') is None:
        price = og_price(og_data)
        if price is not None:
            result['price'] = price
            price_filled = True
    if result.get('currency') is None:
        if price_filled:
            currency = og_currency_for_price(og_data)
        elif result.get('price') is not None:
            currency = og_currency_for_amount(og_data, result.get('price'))
        else:
            currency = og_currency(og_data)
        if currency:
            result['currency'] = currency
    if result.get('availability') is None:
        availability = og_availability(og_data)
        if availability:
            result['availability'] = availability
    return result


def select_main_product(jsonld_blocks, url=None):
    """Select the Product/ProductGroup that belongs to the watched URL."""
    candidates = _collect_product_candidates(jsonld_blocks or [])
    if not candidates:
        return None

    if url:
        candidates = [(_select_offer_by_url(product, url), top_level)
                      for product, top_level in candidates]
        top_level_count = sum(1 for _product, top_level in candidates if top_level)
        watch_tokens = watch_url_product_keys(url, include_variant=True)

        def product_level_match(product):
            return (_exact_url_match(product, url)
                    or _match_keys_score(_product_keys(product, include_variants=False),
                                         watch_tokens) > 0)

        identified_main = any(product_level_match(product)
                              for product, top_level in candidates if top_level)
        matches = []
        for product, top_level in candidates:
            if not top_level and top_level_count:
                continue
            skip_non_url_variant = (identified_main
                                    and not product_level_match(product))
            for variant in _as_list(_ci_get(product, 'hasVariant')):
                if not isinstance(variant, dict):
                    continue
                if (skip_non_url_variant
                        and not any('/' in key for key in _variant_keys(variant))):
                    continue
                score = _variant_match_score(variant, url)
                if score:
                    matches.append((score, variant, product))
        if matches:
            best_score = max(entry[0] for entry in matches)
            best = [entry for entry in matches if entry[0] == best_score]
            if len(best) == 1:
                return _variant_with_parent(best[0][1], best[0][2], url=url)

    def score_candidates(url_tokens):
        scored = []
        for index, (product, top_level) in enumerate(candidates):
            score = _match_product_score(product, url_tokens)
            if _exact_url_match(product, url):
                score = max(score, 100)
            if score:
                strong = bool(_match_keys_score(_product_strong_keys(product), url_tokens))
                scored.append((score, top_level, strong, -index, product))
        return scored

    url_tokens = watch_url_product_keys(url)
    scored = score_candidates(url_tokens)
    if not scored and url:
        expanded_tokens = watch_url_product_keys(url, include_variant=True)
        if expanded_tokens != url_tokens:
            scored = score_candidates(expanded_tokens)
            if scored:
                url_tokens = expanded_tokens

    if scored:
        # A top-level product always beats a nested recommendation/variant.
        # Exact SKU/ProductID matches beat URL/GTIN-only matches. Genuine ties
        # between different products are ambiguous and must not be guessed.
        scored.sort(key=lambda entry: (entry[1], entry[2], entry[0], entry[3]), reverse=True)
        best = scored[0]
        tied = [entry for entry in scored
                if entry[0] == best[0] and entry[1] == best[1] and entry[2] == best[2]]
        if len(tied) > 1:
            products = _collapse_duplicate_products([entry[4] for entry in tied], url=url)
            if len(products) > 1:
                return None
            return products[0]
        return best[4]

    top_level_products = [product for product, top_level in candidates if top_level]
    if not top_level_products and len(candidates) == 1:
        product = candidates[0][0]
        if (not _product_keys(product)
                or not watch_url_product_keys(url, include_variant=True)):
            return None
        return product if _product_identity_confirmed(product, url) else None
    top_level_products = _collapse_duplicate_products(top_level_products, url=url)
    if len(top_level_products) == 1:
        product = top_level_products[0]
        return product if _product_identity_confirmed(product, url) else None
    return None


def microdata_item_types(item):
    return _type_names(item.get('type'))


def _microdata_item_offer_matches(item, url):
    properties = item.get('properties') or {}
    for offer in _as_list(properties.get('offers')):
        if not isinstance(offer, dict):
            continue
        offer_properties = _unwrap_properties(offer)
        offer_url = (_ci_get(offer_properties, 'url')
                     or _ci_get(offer_properties, '@id'))
        if offer_url and _offer_matches_watch({'url': offer_url}, url):
            return True
    return False


def _microdata_item_url_keys(item):
    properties = item.get('properties') or {}
    keys = [item.get('id'), properties.get('url')]
    for offer in _as_list(properties.get('offers')):
        if not isinstance(offer, dict):
            continue
        offer_properties = _unwrap_properties(offer)
        keys.append(_ci_get(offer_properties, 'url') or _ci_get(offer_properties, '@id'))
    return keys


def select_microdata_product(items, url=None, jsonld_product=None, allow_anonymous=False):
    """Select the microdata Product matching the watched URL or JSON-LD product."""
    candidates = [item for item in (items or [])
                  if 'product' in microdata_item_types(item)
                  or 'productgroup' in microdata_item_types(item)]
    if not candidates:
        return None

    url_tokens = watch_url_product_keys(url)
    if url_tokens:
        scored = []
        for index, item in enumerate(candidates):
            strong = _microdata_item_strong_keys(item)
            url_keys = _microdata_item_url_keys(item)
            if not _strong_keys_match(strong, url_keys, url):
                continue
            keys = _microdata_item_keys(item)
            if not any(_scope_key_matches(key, url) for key in keys):
                continue
            score = _match_keys_score(keys, url_tokens)
            if score:
                scored.append((score, bool(_match_keys_score(strong, url_tokens)),
                               -index, item))
        if scored:
            scored.sort(key=lambda entry: (entry[1], entry[0], entry[2]), reverse=True)
            best = scored[0]
            tied = [entry for entry in scored
                    if entry[0] == best[0] and entry[1] == best[1]]
            if len(tied) > 1:
                offer_matched = [entry for entry in tied
                                 if _microdata_item_offer_matches(entry[3], url)]
                if len(offer_matched) == 1:
                    return offer_matched[0][3]
                identities = []
                for entry in tied:
                    item = entry[3]
                    properties = item.get('properties') or {}
                    name = _normalize_name(properties.get('name'))
                    url = item.get('id') or properties.get('url')
                    path = urlparse(str(url)).path.rstrip('/') if isinstance(url, str) else ''
                    identities.append((name, path))
                if all(identities[0]) and all(identity == identities[0]
                                           for identity in identities):
                    return tied[0][3]
                offer_data = [microdata_product_data(entry[3]) for entry in tied]
                if all(data == offer_data[0] for data in offer_data):
                    return tied[0][3]
                return None
            return best[3]

        # A URL-matched JSON-LD main plus one microdata Product is that product
        # when its own keys agree with the URL or its name agrees.
        if isinstance(jsonld_product, dict) and len(candidates) == 1:
            if _match_product_score(jsonld_product, url_tokens) > 0:
                item = candidates[0]
                strong = _microdata_item_strong_keys(item)
                url_keys = _microdata_item_url_keys(item)
                if not _strong_keys_match(strong, url_keys, url):
                    return None
                keys = _microdata_item_keys(item)
                if any(_scope_key_matches(key, url) for key in keys):
                    return item
                main_name = _normalize_name(_ci_get(jsonld_product, 'name'))
                names = [_normalize_name(value) for value in
                         _string_values((item.get('properties') or {}).get('name'))]
                if _microdata_name_matches(main_name, names):
                    return item
                if not names and not any(_has_significant_tokens(key) for key in keys):
                    return item
                return None

        if not isinstance(jsonld_product, dict):
            if allow_anonymous and len(candidates) == 1:
                keys = _microdata_item_keys(candidates[0])
                if not any(_has_significant_tokens(key) for key in keys):
                    return candidates[0]
            return None

    # A unique name match can supply availability when the JSON-LD omits it.
    # Duplicate or missing name matches are carousel tiles and must stay
    # ambiguous, even when only one microdata item exists.
    if isinstance(jsonld_product, dict):
        target_name = _normalize_name(_ci_get(jsonld_product, 'name'))
        if target_name:
            named = []
            for item in candidates:
                properties = item.get('properties') or {}
                if not any(_normalize_name(value) == target_name
                           for value in _string_values(properties.get('name'))):
                    continue
                strong = _microdata_item_strong_keys(item)
                url_keys = _microdata_item_url_keys(item)
                if _strong_keys_match(strong, url_keys, url):
                    named.append(item)
            return named[0] if len(named) == 1 else None
        return None

    return candidates[0] if allow_anonymous and len(candidates) == 1 else None


def _scope_key_matches(key, url):
    if '/' in key:
        return (_same_path_host(url, key)
                and _identities_match(_url_identity(url), _url_identity(key)))
    watch_tokens = set(watch_url_product_keys(url, include_variant=True))
    query_tokens = set()
    for name, entries in parse_qs(urlparse(str(url)).query).items():
        if name.casefold() in ('variant', 'variantid', 'variant_id', 'id',
                               'sku', 'pid', 'productid', 'product_id'):
            for entry in entries:
                query_tokens.update(_key_tokens(entry))
    last_tokens = set(_key_tokens(_last_segment(url)))
    key_tokens = set(_key_tokens(key))
    return bool(key_tokens) and key_tokens <= (last_tokens | watch_tokens | query_tokens)


def _strong_keys_match(strong_keys, url_keys, url):
    watch_tokens = set(_key_tokens(_last_segment(url)))
    watch_tokens.update(watch_url_product_keys(url, include_variant=True))
    for name, entries in parse_qs(urlparse(str(url)).query).items():
        if name.casefold() in ('variant', 'variantid', 'variant_id', 'id',
                               'sku', 'pid', 'productid', 'product_id'):
            for entry in entries:
                watch_tokens.update(_key_tokens(entry))

    def tokens_for(key):
        if '/' in key:
            return (set(watch_url_product_keys(key, include_variant=True))
                    | set(_key_tokens(_last_segment(key))))
        return set(_key_tokens(key))

    strong_match = any(_scope_key_matches(key, url) for key in strong_keys)
    url_match = any(_scope_key_matches(key, url) for key in url_keys if key)
    if url_keys:
        if url_match:
            if strong_match:
                return True
            for key in strong_keys:
                key_tokens = tokens_for(key)
                if key_tokens & watch_tokens and not key_tokens <= watch_tokens:
                    return False
            return True
        if any(tokens_for(key) & watch_tokens for key in url_keys if key):
            return False
    if strong_match:
        return True
    return not any(_has_significant_tokens(key) for key in strong_keys)


def _select_microdata_scope(microdata, url, main_product, allow_anonymous=False):
    """Return the single pure-Python Product scope matching the main product."""
    scopes = microdata.get('product_scopes_data') or []
    if len(scopes) != 1:
        return None
    scope = scopes[0]
    keys = scope.get('product_keys', [])
    strong_keys = scope.get('product_strong_keys', [])
    url_keys = scope.get('product_url_keys', [])
    if not _strong_keys_match(strong_keys, url_keys, url):
        return None
    _last, watch_variants, watch_products = _url_identity(url)
    if ((watch_variants or watch_products)
            and url_keys
            and not any(_scope_key_matches(key, url) for key in url_keys)):
        return None
    if any(_scope_key_matches(key, url) for key in keys):
        return scope
    if not main_product and allow_anonymous:
        return scope
    names = [_normalize_name(value) for value in scope.get('product_names', [])
             if _normalize_name(value)]
    main_name = _normalize_name(_ci_get(main_product, 'name'))
    if _microdata_name_matches(main_name, names):
        return scope
    if not keys and not names:
        return scope
    return None


def _price_is_missing(value):
    if _is_missing(value):
        return True
    return (isinstance(value, list) and bool(value)
            and all(_is_missing(entry) for entry in value))


def _mapping_has_missing_price(mapping):
    if not isinstance(mapping, dict):
        return False
    if _price_is_missing(_ci_get(mapping, 'price', _UNSET)):
        return True
    for specification in _as_list(_ci_get(mapping, 'priceSpecification')):
        specification = _unwrap_properties(specification)
        if _price_is_missing(_ci_get(specification, 'price', _UNSET)):
            return True
    return False


def has_explicit_missing_price(product):
    """True when the product explicitly says price is missing, e.g. "null".

    A missing price key is not the same: some shops omit offers.price but still
    publish the price in microdata.
    """
    if not isinstance(product, dict):
        return False
    if _mapping_has_missing_price(product):
        return True
    for offer in _iter_offers(product):
        if _mapping_has_missing_price(offer):
            return True
    for variant in _as_list(_ci_get(product, 'hasVariant')):
        if isinstance(variant, dict) and _mapping_has_missing_price(variant):
            return True
    return False


def microdata_product_data(item):
    """Extract price/currency/availability from one extruct microdata Product."""
    if not isinstance(item, dict):
        return {}
    properties = item.get('properties') or {}

    offers = [_unwrap_properties(offer)
              for offer in _as_list(properties.get('offers'))
              if isinstance(offer, dict)]

    pseudo_product = {
        'offers': offers,
        'price': properties.get('price'),
        'priceCurrency': properties.get('priceCurrency'),
        'availability': properties.get('availability'),
    }
    return product_offer_data(pseudo_product)


def og_price_key(og_data):
    """Return the OpenGraph key that supplied the current/sale price."""
    for key in ('product:sale_price:amount', 'og:sale_price:amount',
                'product:price:amount', 'og:price:amount'):
        if key in og_data and _as_price(og_data[key]) is not None:
            return key
    return None


def og_price(og_data):
    """Current/sale OpenGraph price only. Never original/min-30d/pretax values."""
    key = og_price_key(og_data)
    return _as_price(og_data[key]) if key else None


def _og_currency(og_data, keys):
    for key in keys:
        if key in og_data and not _is_missing(og_data[key]):
            return str(og_data[key]).strip().upper()
    return None


def og_currency(og_data):
    return _og_currency(og_data, (
        'product:sale_price:currency', 'og:sale_price:currency',
        'product:price:currency', 'og:price:currency'))


def og_currency_for_price(og_data):
    """Use the currency family that supplied the selected price."""
    key = og_price_key(og_data)
    if key and 'sale' in key:
        keys = ('product:sale_price:currency', 'og:sale_price:currency',
                'product:price:currency', 'og:price:currency')
    elif key:
        keys = ('product:price:currency', 'og:price:currency',
                'product:sale_price:currency', 'og:sale_price:currency')
    else:
        return og_currency(og_data)
    return _og_currency(og_data, keys)


def og_currency_for_amount(og_data, amount):
    """Use the currency family whose amount matches an already-known price."""
    amount_keys = ('product:sale_price:amount', 'og:sale_price:amount',
                   'product:price:amount', 'og:price:amount')
    if amount is None:
        return None
    if not any(key in og_data for key in amount_keys):
        return og_currency(og_data)
    for key in amount_keys:
        price = _as_price(og_data.get(key))
        if price is None or price != amount:
            continue
        if 'sale' in key:
            return _og_currency(og_data, (
                'product:sale_price:currency', 'og:sale_price:currency'))
        return _og_currency(og_data, (
            'product:price:currency', 'og:price:currency'))
    return og_currency(og_data)


def og_availability(og_data):
    for key in ('product:availability', 'og:availability'):
        if key in og_data:
            availability = normalize_availability(og_data[key])
            if availability:
                return availability
    return None


class JSONLDExtractor(HTMLParser):
    """
    Extract JSON-LD structured data from HTML.

    Finds all <script type="application/ld+json"> tags and parses their content.
    Handles multiple JSON-LD blocks on the same page.
    """

    def __init__(self):
        super().__init__()
        self.in_jsonld = False
        self.data = []  # List of all parsed JSON-LD objects
        self.current_script = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            # Check if this is a JSON-LD script tag
            for attr, value in attrs:
                if (attr == 'type' and value
                        and value.split(';', 1)[0].strip().casefold() == 'application/ld+json'):
                    self.in_jsonld = True
                    self.current_script = []
                    break

    def handle_data(self, data):
        if self.in_jsonld:
            self.current_script.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.in_jsonld:
            # Parse the accumulated script content
            script_content = ''.join(self.current_script)
            if script_content.strip():
                try:
                    # Parse JSON (handles both objects and arrays)
                    parsed = json.loads(script_content)
                    if isinstance(parsed, list):
                        self.data.extend(parsed)
                    else:
                        self.data.append(parsed)
                except (ValueError, RecursionError) as e:
                    logger.debug(f"Failed to parse JSON-LD: {e}")
                    pass

            self.in_jsonld = False
            self.current_script = []


class OpenGraphExtractor(HTMLParser):
    """
    Extract OpenGraph/product meta tags from HTML.

    Finds <meta property="og:*"> and <meta property="product:*"> tags. Some
    shops only use `product:price:amount`/`product:availability`, not `og:*`.
    """

    def __init__(self):
        super().__init__()
        self.og_data = {}

    def handle_starttag(self, tag, attrs):
        if tag == 'meta':
            attrs_dict = dict(attrs)
            prop = attrs_dict.get('property', '')

            if prop.startswith('og:') or prop.startswith('product:'):
                content = attrs_dict.get('content', '')
                if content:
                    self.og_data[prop] = content


class MicrodataExtractor(HTMLParser):
    """
    Extract basic microdata attributes from HTML.

    Finds elements with itemprop attributes. This is a simplified extractor
    that doesn't handle nested itemscope/itemtype hierarchies - for complex
    cases, use extruct as fallback.
    """

    def __init__(self):
        super().__init__()
        self.microdata = {
            'price': None,
            'currency': None,
            'availability': None,
            'prices': [],
            'offer_prices': [],
            'legacy_price': None,
            'currencies': [],
            'availabilities': [],
            'product_scopes': 0,
            'product_keys': [],
            'product_strong_keys': [],
            'product_url_keys': [],
            'product_names': [],
            'product_scopes_data': [],
        }
        self.current_itemprop = None
        self.current_itemprop_text = []
        self.current_itemprop_all_text = []
        self.current_itemprop_depth = 0
        self.tag_stack = []

    def _product_scope(self):
        for _tag, scope in reversed(self.tag_stack):
            if scope and scope['is_product']:
                return scope
        return None

    def _offer_scope(self):
        for _tag, scope in reversed(self.tag_stack):
            if scope and scope['is_offer']:
                return scope
        return None

    def _record(self, field, value):
        self.microdata[field].append(value)
        scope = self._product_scope()
        if scope is not None:
            if field == 'prices' and self._offer_scope() is not None:
                scope['offer_prices'].append(value)
            else:
                scope[field].append(value)

    def _start_itemprop(self, name, tag):
        if tag in _VOID_TAGS:
            return
        self.current_itemprop = name
        self.current_itemprop_text = []
        self.current_itemprop_all_text = []
        self.current_itemprop_depth = 1

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)

        if self.current_itemprop and tag not in _VOID_TAGS:
            self.current_itemprop_depth += 1

        scope = None
        if 'itemscope' in attrs_dict:
            itemtype = ((attrs_dict.get('itemtype') or '').casefold()
                        .split('#', 1)[0].split('?', 1)[0])
            types = {part.rstrip('/').rsplit('/', 1)[-1]
                     for part in itemtype.split() if part}
            scope = {
                'is_product': bool(types & {'product', 'productgroup'}),
                'is_offer': 'offer' in types,
                'product_keys': [],
                'product_strong_keys': [],
                'product_url_keys': [],
                'product_names': [],
                'prices': [],
                'offer_prices': [],
                'currencies': [],
                'availabilities': [],
            }
            if scope['is_product']:
                self.microdata['product_scopes'] += 1
                self.microdata['product_scopes_data'].append(scope)
        if tag not in _VOID_TAGS:
            self.tag_stack.append((tag, scope))
        if scope and scope['is_product']:
            itemid = attrs_dict.get('itemid')
            if itemid:
                self._record('product_keys', itemid)
                self._record('product_url_keys', itemid)

        if 'itemprop' in attrs_dict:
            itemprop = attrs_dict['itemprop']

            # Price/currency/availability can be in content/href attributes
            if itemprop == 'price':
                if 'content' in attrs_dict:
                    self.microdata['price'] = attrs_dict['content']
                    self._record('prices', attrs_dict['content'])
                else:
                    self._start_itemprop('price', tag)

            elif itemprop in ('sku', 'productID', 'url'):
                value = attrs_dict.get('content') or attrs_dict.get('href')
                if value:
                    self._record('product_keys', value)
                    if itemprop in ('sku', 'productID'):
                        self._record('product_strong_keys', value)
                    else:
                        self._record('product_url_keys', value)
                else:
                    self._start_itemprop(itemprop, tag)

            elif itemprop == 'name':
                value = attrs_dict.get('content')
                if value:
                    self._record('product_names', value)
                else:
                    self._start_itemprop('name', tag)

            elif itemprop == 'priceCurrency':
                if 'content' in attrs_dict:
                    self.microdata['currency'] = attrs_dict['content']
                    self._record('currencies', attrs_dict['content'])
                else:
                    self._start_itemprop('priceCurrency', tag)

            elif itemprop == 'availability':
                # Can be in href (link) or content (meta)
                if 'href' in attrs_dict:
                    self.microdata['availability'] = attrs_dict['href']
                    self._record('availabilities', attrs_dict['href'])
                elif 'content' in attrs_dict:
                    self.microdata['availability'] = attrs_dict['content']
                    self._record('availabilities', attrs_dict['content'])
                else:
                    self._start_itemprop('availability', tag)

    def _finish_itemprop(self):
        name = self.current_itemprop
        parts = [part for part in self.current_itemprop_text if part.strip()]
        text = ''.join(parts).strip()
        if name == 'price':
            # Not [^\d.] + float(), that breaks "39,99" (#4493), see Restock.parse_currency()
            price = Restock().parse_currency(text) if text else None
            if price is not None:
                self.microdata['price'] = price
                self._record('prices', text)
            legacy_text = ''.join(self.current_itemprop_all_text).strip()
            legacy = Restock().parse_currency(legacy_text) if legacy_text else None
            if legacy is None:
                for part in reversed(self.current_itemprop_all_text):
                    legacy = Restock().parse_currency(part.strip())
                    if legacy is not None:
                        break
            if legacy is not None:
                self.microdata['legacy_price'] = legacy
                if text and price is None:
                    self.microdata['price'] = legacy
                    self._record('prices', legacy_text or str(legacy))
        elif name == 'priceCurrency' and text:
            self.microdata['currency'] = text
            self._record('currencies', text)
        elif name in ('sku', 'productID', 'url') and text:
            self._record('product_keys', text)
            if name in ('sku', 'productID'):
                self._record('product_strong_keys', text)
            else:
                self._record('product_url_keys', text)
        elif name == 'name' and text:
            self._record('product_names', ' '.join(parts))
        elif name == 'availability' and text:
            self.microdata['availability'] = text
            self._record('availabilities', text)
        self.current_itemprop = None
        self.current_itemprop_text = []
        self.current_itemprop_all_text = []
        self.current_itemprop_depth = 0

    def handle_data(self, data):
        if not self.current_itemprop:
            return
        if any(tag in _PRICE_SKIP_TAGS for tag, _scope in self.tag_stack):
            return
        self.current_itemprop_all_text.append(data)
        if (self.current_itemprop == 'price'
                and self.current_itemprop_depth > 1
                and re.search(r'\d', data)
                and not data.lstrip().startswith((',', '.'))):
            return
        self.current_itemprop_text.append(data)

    def handle_endtag(self, tag):
        if self.current_itemprop and tag not in _VOID_TAGS:
            self.current_itemprop_depth -= 1
            if self.current_itemprop_depth <= 0:
                self._finish_itemprop()
        for index in range(len(self.tag_stack) - 1, -1, -1):
            if self.tag_stack[index][0] == tag:
                del self.tag_stack[index:]
                break


def extract_metadata_pure_python(html_content):
    """
    Extract structured metadata from HTML using pure Python parsers.

    Returns a dict with three keys:
    - 'json-ld': List of parsed JSON-LD objects
    - 'opengraph': Dict of OpenGraph properties
    - 'microdata': Dict of microdata properties

    Args:
        html_content: HTML string to parse

    Returns:
        dict: Extracted metadata in three formats
    """
    result = {
        'json-ld': [],
        'opengraph': {},
        'microdata': {}
    }

    # Extract JSON-LD
    try:
        jsonld_extractor = JSONLDExtractor()
        jsonld_extractor.feed(html_content)
        result['json-ld'] = jsonld_extractor.data
        logger.trace(f"Pure Python: Found {len(jsonld_extractor.data)} JSON-LD blocks")
    except Exception as e:
        logger.debug(f"JSON-LD extraction failed: {e}")

    # Extract OpenGraph
    try:
        og_extractor = OpenGraphExtractor()
        og_extractor.feed(html_content)
        result['opengraph'] = og_extractor.og_data
        if result['opengraph']:
            logger.trace(f"Pure Python: Found {len(og_extractor.og_data)} OpenGraph tags")
    except Exception as e:
        logger.debug(f"OpenGraph extraction failed: {e}")

    # Extract Microdata
    try:
        microdata_extractor = MicrodataExtractor()
        microdata_extractor.feed(html_content)
        result['microdata'] = microdata_extractor.microdata
        if result['microdata'].get('prices') or result['microdata'].get('availabilities'):
            logger.trace(f"Pure Python: Found microdata: {result['microdata']}")
    except Exception as e:
        logger.debug(f"Microdata extraction failed: {e}")

    return result


def _legacy_query_price_availability(extracted_data):
    """Frozen whole-document behaviour for url=None/direct callers.

    New extraction/selection rules belong in the scoped path; this function is
    intentionally kept small so existing integrations keep their old semantics.
    """
    from jsonpath_ng import parse

    result = {}

    # 1. Try JSON-LD first (most reliable and common)
    for data in extracted_data.get('json-ld', []):
        try:
            # Use jsonpath to find price/availability anywhere in the structure
            price_parse = parse('$..(price|Price)')
            availability_parse = parse('$..(availability|Availability)')
            currency_parse = parse('$..(priceCurrency|pricecurrency|currency)')

            price_results = [m.value for m in price_parse.find(data)]
            if price_results and not result.get('price'):
                # Handle various price formats
                price_val = price_results[0]
                if isinstance(price_val, (int, float)):
                    result['price'] = float(price_val)
                elif isinstance(price_val, str):
                    # Extract numeric value from string, see Restock.parse_currency() for every caller
                    try:
                        result['price'] = Restock().parse_currency(price_val)
                    except ValueError:
                        pass

            avail_results = [m.value for m in availability_parse.find(data)]
            if avail_results and not result.get('availability'):
                result['availability'] = str(avail_results[0])

            curr_results = [m.value for m in currency_parse.find(data)]
            if curr_results and not result.get('currency'):
                result['currency'] = str(curr_results[0])

            # If we found price, this JSON-LD block is good
            if result.get('price'):
                logger.debug(f"Pure Python: Found price data in JSON-LD: {result}")
                break

        except Exception as e:
            logger.debug(f"Error querying JSON-LD: {e}")
            continue

    # 2. Try OpenGraph if JSON-LD didn't provide everything
    og_data = extracted_data.get('opengraph', {})
    if not result.get('price') and 'og:price:amount' in og_data:
        try:
            result['price'] = float(og_data['og:price:amount'])
        except ValueError:
            pass
    if not result.get('currency') and 'og:price:currency' in og_data:
        result['currency'] = og_data['og:price:currency']
    if not result.get('availability') and 'og:availability' in og_data:
        result['availability'] = og_data['og:availability']

    # 3. Use microdata as last resort
    microdata = extracted_data.get('microdata', {})
    legacy_price = microdata.get('legacy_price')
    if not result.get('price') and 'price' in microdata and microdata['price'] is not None:
        result['price'] = microdata['price']
    if not result.get('price') and legacy_price is not None:
        result['price'] = legacy_price
    if not result.get('currency') and 'currency' in microdata and microdata['currency'] is not None:
        result['currency'] = microdata['currency']
    if not result.get('availability') and 'availability' in microdata and microdata['availability'] is not None:
        result['availability'] = microdata['availability']

    return result


def _query_price_availability_scoped(extracted_data, url, main_product=_UNSET):
    """Product-scoped extraction: select one product, then use only its values."""
    if main_product is _UNSET:
        main_product = select_main_product(extracted_data.get('json-ld', []), url=url)
    og_data = extracted_data.get('opengraph', {}) or {}

    if main_product:
        if has_conflicting_product_prices(main_product):
            return {}
        result = product_offer_data(main_product)
        explicit_missing = has_explicit_missing_price(main_product)
        microdata = extracted_data.get('microdata', {}) or {}
        scopes = microdata.get('product_scopes', 0)
        url_tokens = watch_url_product_keys(url, include_variant=True)
        main_url_matched = bool(url_tokens) and _match_product_score(main_product, url_tokens) > 0
        confirmed = False
        source = microdata
        if scopes == 1:
            scope_data = microdata.get('product_scopes_data')
            if scope_data:
                scope = _select_microdata_scope(microdata, url, main_product)
                confirmed = scope is not None
                source = scope or {}
            else:
                keys = list(microdata.get('product_keys', []))
                main_name = _normalize_name(_ci_get(main_product, 'name'))
                names = [_normalize_name(value)
                         for value in microdata.get('product_names', [])
                         if _normalize_name(value)]
                confirmed = bool(url_tokens) and (
                    _match_keys_score(keys, url_tokens) > 0
                    or (main_url_matched and _microdata_name_matches(main_name, names)))
                source = microdata if confirmed else {}
        if scopes == 0:
            raw_prices = source.get('prices', [])
        elif scopes == 1 and confirmed:
            raw_prices = source.get('offer_prices') or source.get('prices', [])
        else:
            raw_prices = []
        prices = {_as_price(value) for value in raw_prices}
        prices.discard(None)
        scope_conflict = scopes == 1 and confirmed and len(prices) > 1
        if scope_conflict and result.get('price') is None:
            return {}
        if scopes == 1 and confirmed and not scope_conflict:
            currencies = {str(value).strip().upper()
                          for value in source.get('currencies', [])
                          if not _is_missing(value)}
            if result.get('currency') is None and len(currencies) == 1:
                result['currency'] = currencies.pop()
            availabilities = {normalize_availability(value)
                              for value in source.get('availabilities', [])}
            availabilities.discard(None)
            if result.get('availability') is None and len(availabilities) == 1:
                result['availability'] = availabilities.pop()

        if (result.get('price') is None and not explicit_missing
                and len(prices) == 1):
            result['price'] = prices.pop()

        # OpenGraph is page-level. Currency/availability still need the
        # ambiguity gate, but a price may fill when no microdata price competes.
        if scopes == 0 or confirmed or main_url_matched:
            fill_missing_from_og(result, og_data, fill_price=not explicit_missing)
        elif result.get('price') is None and not explicit_missing and not prices:
            price = og_price(og_data)
            if price is not None:
                result['price'] = price
        return result

    # No Product/ProductGroup JSON-LD (Kazar-style page). Keep the legacy
    # single-unique-price fallback, but refuse when the page contains more than
    # one Product itemscope, so a carousel cannot masquerade as the main product.
    microdata = extracted_data.get('microdata', {}) or {}
    scopes = microdata.get('product_scopes', 0)
    top_level_products = [product for product, top_level
                          in _collect_product_candidates(extracted_data.get('json-ld', []))
                          if top_level]
    if any(has_conflicting_product_prices(product) for product in top_level_products):
        return {}
    if len(top_level_products) > 1:
        result, conflict = top_level_product_consensus(top_level_products, url=url)
        if conflict:
            return {}
        if result or scopes == 0:
            return result

    if scopes == 0:
        offers = top_level_offers(extracted_data.get('json-ld', []))
        if len(offers) > 1:
            consensus, conflict = top_level_product_consensus(offers, offers_mode=True)
            if conflict:
                return {}
            if consensus:
                explicit = any(has_explicit_missing_price(offer) for offer in offers)
                return fill_missing_from_og(consensus, og_data, fill_price=not explicit)
        if len(offers) == 1:
            return fill_missing_from_og(
                product_offer_data(offers[0]), og_data,
                fill_price=not has_explicit_missing_price(offers[0]))

    if scopes > 1:
        return {}
    if scopes == 1:
        scope = _select_microdata_scope(
            microdata, url, None, allow_anonymous=not top_level_products)
        microdata = scope or {}
    if scopes == 1 and url:
        keys = list(microdata.get('product_keys', []))
        url_tokens = watch_url_product_keys(url)
        if (url_tokens
                and any(_has_significant_tokens(key) for key in keys)
                and not _match_keys_score(keys, url_tokens)):
            return {}

    raw_prices = microdata.get('offer_prices') or microdata.get('prices', [])
    prices = {_as_price(value) for value in raw_prices}
    prices.discard(None)
    price_from_og = False
    if not prices:
        og_price_value = og_price(og_data)
        if og_price_value is not None:
            prices.add(og_price_value)
            price_from_og = True

    availabilities = {normalize_availability(value)
                      for value in microdata.get('availabilities', [])}
    availabilities.discard(None)
    og_availability_value = og_availability(og_data)
    if og_availability_value:
        availabilities.add(og_availability_value)

    currencies = {str(value).strip().upper()
                  for value in microdata.get('currencies', [])
                  if not _is_missing(value)}
    if price_from_og:
        og_currency_value = og_currency_for_price(og_data)
    elif len(prices) == 1:
        og_currency_value = og_currency_for_amount(og_data, next(iter(prices)))
    else:
        og_currency_value = og_currency(og_data)
    if og_currency_value:
        currencies.add(og_currency_value)

    result = {}
    if len(prices) == 1:
        result['price'] = prices.pop()
    if len(currencies) == 1:
        result['currency'] = currencies.pop()
    if len(availabilities) == 1:
        result['availability'] = availabilities.pop()
    return result


def query_price_availability(extracted_data, url=None, main_product=_UNSET):
    """
    Query extracted metadata for price and availability information.

    `url=None` keeps the original whole-document behaviour for direct callers.
    When the restock processor passes the watch URL, extraction is scoped to the
    Product/ProductGroup that belongs to that URL.
    """
    if url:
        return _query_price_availability_scoped(extracted_data, url, main_product=main_product)
    return _legacy_query_price_availability(extracted_data)
