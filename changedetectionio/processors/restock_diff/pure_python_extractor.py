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


def _is_group(product):
    return ('productgroup' in _jsonld_types(product)
            or bool(_as_list(_ci_get(product, 'hasVariant'))))


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
                if _TRAILING_ROUTE.match(previous) or _significant_tokens(previous):
                    index -= 1
                    continue
            break
        if index >= 0:
            add(segments[index])
            if not tokens and index > 0:
                previous = re.sub(r'\.(?:html?|php|aspx?)$', '', segments[index - 1],
                                  flags=re.IGNORECASE)
                if not previous.isdigit():
                    add(previous)

    if not tokens or include_variant:
        tokens.update(fallback_tokens)
    return tokens


def _product_keys(product, strong=False):
    names = ('sku', 'productID', 'productGroupID') if strong else (
        'sku', 'productID', 'productGroupID', 'mpn', 'gtin13', '@id', 'url')
    variant_names = ('sku', 'productID') if strong else (
        'sku', 'productID', 'mpn', 'gtin13')
    keys = []
    for name in names:
        keys.extend(_string_values(_ci_get(product, name)))
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


def _microdata_identity(item):
    properties = item.get('properties') or {}
    name = _normalize_name(properties.get('name'))
    url = item.get('id') or properties.get('url')
    path = urlparse(str(url)).path.rstrip('/') if isinstance(url, str) else ''
    return name, path


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
    segments = [unquote(segment) for segment in parsed.path.rstrip('/').split('/') if segment]
    last = re.sub(r'[^a-z0-9]+', '', segments[-1].casefold()) if segments else ''
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


def _variant_matches_url(variant, url):
    """Return True when a variant's own code appears in the watched product URL."""
    if not url or not isinstance(variant, dict):
        return False
    last, variant_values, product_values = _url_identity(url)
    for key in _variant_keys(variant):
        if '/' in key:
            key_last, key_variants, key_products = _url_identity(key)
            if key_variants and variant_values:
                return bool(key_variants & variant_values)
            if key_variants or variant_values:
                return key_last == last
            if key_products and product_values:
                return bool(key_products & product_values)
            if key_products or product_values:
                return key_last == last
            return bool(key_last) and key_last == last
        normalized = re.sub(r'[^a-z0-9]+', '', key.casefold())
        if len(normalized) < 3:
            continue
        if (normalized in variant_values or normalized in product_values
                or (last and normalized in last)):
            return True
    return False


def _set_offers(product, offers):
    result = {key: value for key, value in product.items()
              if not (isinstance(key, str) and key.casefold() == 'offers')}
    result['offers'] = offers
    return result


def _variant_with_parent(variant, parent):
    result = {**variant}
    for field in ('priceCurrency', 'availability'):
        if _ci_get(result, field) is None and _ci_get(parent, field) is not None:
            result[field] = _ci_get(parent, field)
    offers = [offer for offer in _as_list(_ci_get(result, 'offers'))
              if isinstance(offer, dict)]
    if len(offers) > 1:
        priced = [offer for offer in offers if _offer_price(offer) is not None]
        if priced:
            return _set_offers(result, [priced[0]])
    return result


def _offer_matches_watch(offer, url):
    offer_url = _ci_get(offer, 'url') or _ci_get(offer, '@id')
    if not offer_url or not url:
        return False
    watch_last, watch_variants, watch_products = _url_identity(url)
    offer_last, offer_variants, offer_products = _url_identity(offer_url)
    if watch_variants or offer_variants:
        if watch_variants and offer_variants:
            return bool(watch_variants & offer_variants)
        return watch_last == offer_last
    if watch_products or offer_products:
        if watch_products and offer_products:
            return bool(watch_products & offer_products)
        return watch_last == offer_last
    return bool(watch_last) and watch_last == offer_last


def _product_has_watch_offer(product, url):
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


def _product_identity(product):
    name = _normalize_name(_ci_get(product, 'name'))
    url = _ci_get(product, 'url') or _ci_get(product, '@id')
    path = urlparse(str(url)).path.rstrip('/') if isinstance(url, str) else ''
    return name, path


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
    first_wins = _is_group(product)

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
        currency = _consensus(currencies, first_wins=True)
        availability = _consensus(availabilities, first_wins=True)

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
    if _is_group(product):
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

    identities = [_product_identity(product) for product in top_level_products]
    if any(identity != identities[0] for identity in identities):
        return top_level_products

    offer_data = [product_offer_data(product) for product in top_level_products]
    if all(data == offer_data[0] for data in offer_data):
        return [top_level_products[0]]

    # Same name/URL but different offer data: only resolve when an offer URL
    # points at the watched variant, or when exactly one block has a price.
    if all(identities[0]):
        if url:
            matched = [product for product in top_level_products
                       if _product_has_watch_offer(product, url)]
            if len(matched) == 1:
                return matched
        priced = [product for product in top_level_products
                  if product_offer_data(product).get('price') is not None]
        if len(priced) == 1:
            return priced
        counts = {}
        for product in priced:
            price = product_offer_data(product)['price']
            counts[price] = counts.get(price, 0) + 1
        if counts:
            best_price, best_count = max(counts.items(), key=lambda item: item[1])
            if best_count * 2 > len(top_level_products):
                return [next(product for product in priced
                             if product_offer_data(product)['price'] == best_price)]
        return top_level_products

    return top_level_products


def top_level_offers(jsonld_blocks):
    return [block for block in (jsonld_blocks or [])
            if isinstance(block, dict) and 'offer' in _jsonld_types(block)]


def top_level_product_consensus(products, offers_mode=False):
    """Return (data, has_conflict) for unmatched top-level products/offers."""
    if any(has_conflicting_product_prices(product) for product in products):
        return {}, True
    values = [product_offer_data(product) for product in products]
    if not offers_mode:
        if any(has_explicit_missing_price(product) for product in products):
            return {}, False
        if any(value.get('price') is None for value in values):
            return {}, False
    prices = {data.get('price') for data in values if data.get('price') is not None}
    if len(prices) > 1:
        return {}, True
    if len(prices) == 1:
        result = dict(next(data for data in values if data.get('price') is not None))
        if offers_mode:
            for other in values:
                _fill_missing(result, other, ('currency', 'availability'))
        else:
            currencies = {data.get('currency') for data in values if data.get('currency')}
            availabilities = {data.get('availability') for data in values if data.get('availability')}
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
            currency = _og_currency(og_data, ('product:price:currency', 'og:price:currency'))
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
        watch_tokens = watch_url_product_keys(url, include_variant=True)
        top_level_count = sum(1 for _product, top_level in candidates if top_level)
        matches = []
        for product, top_level in candidates:
            if not top_level and top_level_count:
                continue
            if (top_level_count > 1
                    and _match_product_score(product, watch_tokens) <= 0):
                continue
            for variant in _as_list(_ci_get(product, 'hasVariant')):
                if isinstance(variant, dict) and _variant_matches_url(variant, url):
                    matches.append((variant, product))
        if len(matches) == 1:
            return _variant_with_parent(*matches[0])

    def score_candidates(url_tokens):
        scored = []
        for index, (product, top_level) in enumerate(candidates):
            score = _match_product_score(product, url_tokens)
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
        return candidates[0][0]
    top_level_products = _collapse_duplicate_products(top_level_products, url=url)
    if len(top_level_products) == 1:
        return top_level_products[0]
    return None


def microdata_item_types(item):
    return _type_names(item.get('type'))


def select_microdata_product(items, url=None, jsonld_product=None):
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
            score = _match_keys_score(_microdata_item_keys(item), url_tokens)
            if score:
                strong = bool(_match_keys_score(_microdata_item_strong_keys(item), url_tokens))
                scored.append((score, strong, -index, item))
        if scored:
            scored.sort(key=lambda entry: (entry[1], entry[0], entry[2]), reverse=True)
            best = scored[0]
            tied = [entry for entry in scored
                    if entry[0] == best[0] and entry[1] == best[1]]
            if len(tied) > 1:
                identities = [_microdata_identity(entry[3]) for entry in tied]
                if all(identities[0]) and all(identity == identities[0] for identity in identities):
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
                keys = _microdata_item_keys(item)
                if any(_has_significant_tokens(key) for key in keys):
                    return item if _match_keys_score(keys, url_tokens) > 0 else None
                main_name = _normalize_name(_ci_get(jsonld_product, 'name'))
                names = [_normalize_name(value) for value in
                         _string_values((item.get('properties') or {}).get('name'))]
                if _microdata_name_matches(main_name, names):
                    return item
                return item if not names else None

        if not isinstance(jsonld_product, dict):
            if len(candidates) == 1:
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
            named = [item for item in candidates
                     if any(_normalize_name(value) == target_name
                            for value in _string_values((item.get('properties') or {}).get('name')))]
            return named[0] if len(named) == 1 else None
        return None

    return candidates[0] if len(candidates) == 1 else None


def _select_microdata_scope(microdata, url, main_product):
    """Return the single pure-Python Product scope matching the main product."""
    scopes = microdata.get('product_scopes_data') or []
    if len(scopes) != 1:
        return None
    scope = scopes[0]
    url_tokens = watch_url_product_keys(url, include_variant=True)
    keys = scope.get('product_keys', [])
    if url_tokens and _match_keys_score(keys, url_tokens):
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
            'currencies': [],
            'availabilities': [],
            'product_scopes': 0,
            'product_keys': [],
            'product_names': [],
            'product_scopes_data': [],
        }
        self.current_itemprop = None
        self.current_itemprop_text = []
        self.current_itemprop_depth = 0
        self.tag_stack = []

    def _product_scope(self):
        for _tag, scope in reversed(self.tag_stack):
            if scope and scope['is_product']:
                return scope
        return None

    def _record(self, field, value):
        self.microdata[field].append(value)
        scope = self._product_scope()
        if scope is not None:
            scope[field].append(value)

    def _start_itemprop(self, name, tag):
        if tag in _VOID_TAGS:
            return
        self.current_itemprop = name
        self.current_itemprop_text = []
        self.current_itemprop_depth = 1

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)

        if self.current_itemprop and tag not in _VOID_TAGS:
            self.current_itemprop_depth += 1

        scope = None
        if 'itemscope' in attrs_dict:
            itemtype = (attrs_dict.get('itemtype') or '').casefold()
            scope = {
                'is_product': 'product' in itemtype,
                'product_keys': [],
                'product_names': [],
                'prices': [],
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
        if name == 'price' and text:
            # Not [^\d.] + float(), that breaks "39,99" (#4493), see Restock.parse_currency()
            price = Restock().parse_currency(text)
            if price is not None:
                self.microdata['price'] = price
                self._record('prices', text)
            else:
                # Keep the legacy last-text-chunk behavior for url=None callers.
                for part in reversed(parts):
                    price = Restock().parse_currency(part.strip())
                    if price is not None:
                        self.microdata['price'] = price
                        break
        elif name == 'priceCurrency' and text:
            self.microdata['currency'] = text
            self._record('currencies', text)
        elif name in ('sku', 'productID', 'url') and text:
            self._record('product_keys', text)
        elif name == 'name' and text:
            self._record('product_names', text)
        elif name == 'availability' and text:
            self.microdata['availability'] = text
            self._record('availabilities', text)
        self.current_itemprop = None
        self.current_itemprop_text = []
        self.current_itemprop_depth = 0

    def handle_data(self, data):
        if self.current_itemprop:
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
    if not result.get('price') and 'price' in microdata and microdata['price'] is not None:
        result['price'] = microdata['price']
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
        prices = ({_as_price(value) for value in source.get('prices', [])}
                  if scopes == 0 or confirmed else set())
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
    if len(top_level_products) > 1:
        if any(has_explicit_missing_price(product) for product in top_level_products):
            return {}
        result, conflict = top_level_product_consensus(top_level_products)
        if conflict:
            return {}
        if result:
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
    if scopes == 1 and url:
        keys = list(microdata.get('product_keys', []))
        url_tokens = watch_url_product_keys(url)
        if (url_tokens
                and any(_has_significant_tokens(key) for key in keys)
                and not _match_keys_score(keys, url_tokens)):
            return {}

    prices = {_as_price(value) for value in microdata.get('prices', [])}
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
    og_currency_value = (og_currency_for_price(og_data) if price_from_og
                         else og_currency(og_data))
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
