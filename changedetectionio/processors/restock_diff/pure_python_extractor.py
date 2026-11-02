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
        value = float(value)
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


def _ci_get(obj, name):
    """Case-insensitive dict lookup (JSON-LD keys are not always consistently cased)."""
    if not isinstance(obj, dict):
        return None
    wanted = name.casefold()
    for key, value in obj.items():
        if isinstance(key, str) and key.casefold() == wanted:
            return value
    return None


def _ci_has(obj, name):
    if not isinstance(obj, dict):
        return False
    wanted = name.casefold()
    return any(isinstance(key, str) and key.casefold() == wanted for key in obj)


def _type_name(value):
    return str(value).strip().casefold().rsplit('/', 1)[-1]


def _jsonld_types(obj):
    value = _ci_get(obj, '@type')
    if isinstance(value, str):
        return [_type_name(value)]
    if isinstance(value, list):
        return [_type_name(item) for item in value]
    return []


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


def watch_url_product_keys(url):
    """Return product-code-like tokens from the watch URL.

    Covers `pid`/`sku` query parameters, demandware-style `dwvar_<PID>_...`
    parameter names, Shopify-style `variant` params, and codes embedded in the
    path slug. Full-token matching is intentional: a SKU such as "3230" must not
    match "13230". Variant ids are only a fallback when the URL has no stronger
    product code, because they can collide with unrelated SKUs.
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
    for segment in segments:
        path_slug = re.sub(r'\.(?:html?|php|aspx?)$', '', segment, flags=re.IGNORECASE)
        add(path_slug)
        for part in re.split(r'-', path_slug):
            add(part)

    if not tokens:
        tokens.update(fallback_tokens)
    return tokens


def _product_keys(product):
    keys = []
    for name in ('sku', 'productID', 'productGroupID', 'mpn', 'gtin13', '@id', 'url'):
        keys.extend(_string_values(_ci_get(product, name)))

    for variant in _as_list(_ci_get(product, 'hasVariant')):
        if isinstance(variant, dict):
            for name in ('sku', 'productID', 'mpn', 'gtin13'):
                keys.extend(_string_values(_ci_get(variant, name)))
    return keys


def _product_strong_keys(product):
    keys = []
    for name in ('sku', 'productID', 'productGroupID'):
        keys.extend(_string_values(_ci_get(product, name)))
    for variant in _as_list(_ci_get(product, 'hasVariant')):
        if isinstance(variant, dict):
            for name in ('sku', 'productID'):
                keys.extend(_string_values(_ci_get(variant, name)))
    return keys


def _string_values(value):
    values = value if isinstance(value, list) else [value]
    return [str(entry) for entry in values
            if isinstance(entry, (str, int, float)) and not isinstance(entry, bool)]


def _microdata_item_keys(item):
    properties = item.get('properties') or {}
    keys = []
    for value in (item.get('id'), properties.get('sku'),
                  properties.get('productID'), properties.get('url')):
        keys.extend(_string_values(value))
    return keys


def _microdata_item_strong_keys(item):
    properties = item.get('properties') or {}
    keys = []
    for value in (properties.get('sku'), properties.get('productID')):
        keys.extend(_string_values(value))
    return keys


def _microdata_identity(item):
    properties = item.get('properties') or {}
    name = _normalize_name(properties.get('name'))
    url = item.get('id') or properties.get('url')
    path = urlparse(str(url)).path.rstrip('/') if isinstance(url, str) else ''
    return name, path


def _microdata_name_matches(main_name, names):
    if not main_name or not names:
        return True
    return any(main_name in name for name in names)


def has_conflicting_microdata_prices(item):
    properties = item.get('properties') or {}
    prices = set()

    def add(value):
        for entry in (value if isinstance(value, list) else [value]):
            price = _as_price(entry)
            if price is not None:
                prices.add(price)

    add(properties.get('price'))
    for offer in _as_list(properties.get('offers')):
        if not isinstance(offer, dict):
            continue
        offer_properties = offer.get('properties') if isinstance(offer.get('properties'), dict) else offer
        add(offer_properties.get('price'))
    return len(prices) > 1


def _match_keys_score(keys, url_tokens):
    if not url_tokens:
        return 0

    best = 0
    for key in keys:
        tokens = _significant_tokens(key.split('#', 1)[0].split('?', 1)[0])
        if not tokens:
            continue
        # URL/id keys can start with a shared category id, so only the last
        # path segment is a reliable product code there. Plain SKUs/ids usually
        # start with the product code and may end with a colour or size code.
        candidates = tokens[-1:] if '/' in key else tokens[:1]
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

    Variants/offers that disagree produce a partial result instead of silently
    choosing the first one. Each field is decided independently.
    """
    if not isinstance(product, dict):
        return {}

    prices = []
    currencies = []
    availabilities = []

    for offer in _iter_offers(product):
        price = _offer_price(offer)
        if price is not None:
            prices.append(price)
        currency = _offer_currency(offer)
        if currency:
            currencies.append(currency)
        availability = _offer_availability(offer)
        if availability:
            availabilities.append(availability)

    if not prices:
        price = _single_price(_ci_get(product, 'price'))
        if price is not None:
            prices.append(price)
    if not currencies:
        currency = _single_currency(_ci_get(product, 'priceCurrency'))
        if currency is not None:
            currencies.append(currency)
    if not availabilities:
        availability = _single_availability(_ci_get(product, 'availability'))
        if availability:
            availabilities.append(availability)

    result = {}
    first_wins = ('productgroup' in _jsonld_types(product)
                  or bool(_as_list(_ci_get(product, 'hasVariant'))))
    price = _consensus(prices, first_wins=first_wins)
    if price is not None:
        result['price'] = price
    currency = _consensus([value.casefold() for value in currencies], first_wins=first_wins)
    if currency is not None:
        result['currency'] = currency.upper()
    availability = _consensus(availabilities, first_wins=first_wins)
    if availability is not None:
        result['availability'] = availability
    return result


def has_conflicting_product_prices(product):
    if not isinstance(product, dict):
        return False
    if ('productgroup' in _jsonld_types(product)
            or _as_list(_ci_get(product, 'hasVariant'))):
        return False
    prices = set()
    for offer in _iter_offers(product):
        price = _offer_price(offer)
        if price is not None:
            prices.add(price)
    price = _as_price(_ci_get(product, 'price'))
    if price is not None:
        prices.add(price)
    return len(prices) > 1


def _collapse_duplicate_products(top_level_products):
    """Collapse identical product blocks after URL matching has had its say."""
    if len(top_level_products) < 2:
        return top_level_products

    identities = [_product_identity(product) for product in top_level_products]
    if any(identity != identities[0] for identity in identities):
        return top_level_products
    if all(identities[0]):
        offer_data = [product_offer_data(product) for product in top_level_products]
        has_group = any('productgroup' in _jsonld_types(product)
                        for product in top_level_products)
        if all(data == offer_data[0] for data in offer_data) or has_group:
            return [top_level_products[0]]
        return top_level_products

    offer_data = [product_offer_data(product) for product in top_level_products]
    if all(data == offer_data[0] for data in offer_data):
        return [top_level_products[0]]
    return top_level_products


def top_level_offers(jsonld_blocks):
    return [block for block in (jsonld_blocks or [])
            if isinstance(block, dict) and 'offer' in _jsonld_types(block)]


def top_level_product_consensus(products):
    """Return (data, has_conflict) for unmatched top-level products."""
    values = [product_offer_data(product) for product in products]
    if any(has_explicit_missing_price(product) for product in products):
        return {}, False
    prices = {data.get('price') for data in values if data.get('price') is not None}
    if len(prices) > 1:
        return {}, True
    if len(prices) == 1:
        for data in values:
            if data.get('price') is not None:
                result = dict(data)
                for other in values:
                    for field in ('currency', 'availability'):
                        if result.get(field) is None and other.get(field) is not None:
                            result[field] = other[field]
                return result, False
    return {}, False


def fill_missing_from_og(result, og_data):
    if result.get('price') is None:
        price = og_price(og_data)
        if price is not None:
            result['price'] = price
    if result.get('currency') is None:
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

    url_tokens = watch_url_product_keys(url)
    scored = []
    for index, (product, top_level) in enumerate(candidates):
        score = _match_product_score(product, url_tokens)
        if score:
            strong = bool(_match_keys_score(_product_strong_keys(product), url_tokens))
            scored.append((score, top_level, strong, -index, product))

    if scored:
        # A top-level product always beats a nested recommendation/variant.
        # Exact SKU/ProductID matches beat URL/GTIN-only matches. Genuine ties
        # between different products are ambiguous and must not be guessed.
        scored.sort(key=lambda entry: (entry[1], entry[2], entry[0], entry[3]), reverse=True)
        best = scored[0]
        tied = [entry for entry in scored
                if entry[0] == best[0] and entry[1] == best[1] and entry[2] == best[2]]
        if len(tied) > 1:
            products = _collapse_duplicate_products([entry[4] for entry in tied])
            if len(products) > 1:
                return None
            return products[0]
        return best[4]

    top_level_products = [product for product, top_level in candidates if top_level]
    if not top_level_products and len(candidates) == 1:
        return candidates[0][0]
    top_level_products = _collapse_duplicate_products(top_level_products)
    if len(top_level_products) == 1:
        return top_level_products[0]
    return None


def microdata_item_types(item):
    value = item.get('type')
    if isinstance(value, str):
        return [_type_name(value)]
    if isinstance(value, list):
        return [_type_name(entry) for entry in value]
    return []


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

        # A URL-matched JSON-LD main plus one microdata Product is that product,
        # even when the page gives it a longer or missing name.
        if isinstance(jsonld_product, dict) and len(candidates) == 1:
            if _match_product_score(jsonld_product, url_tokens) > 0:
                main_name = _normalize_name(_ci_get(jsonld_product, 'name'))
                names = [_normalize_name(value) for value in
                         _string_values((candidates[0].get('properties') or {}).get('name'))]
                if _microdata_name_matches(main_name, names):
                    return candidates[0]

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


def _mapping_has_missing_price(mapping):
    if not isinstance(mapping, dict):
        return False
    if _ci_has(mapping, 'price') and _is_missing(_ci_get(mapping, 'price')):
        return True
    for specification in _as_list(_ci_get(mapping, 'priceSpecification')):
        specification = _unwrap_properties(specification)
        if (_ci_has(specification, 'price')
                and _is_missing(_ci_get(specification, 'price'))):
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

    offers = []
    for offer in _as_list(properties.get('offers')):
        if not isinstance(offer, dict):
            continue
        if isinstance(offer.get('properties'), dict):
            offers.append(offer['properties'])
        else:
            offers.append(offer)

    pseudo_product = {
        'offers': offers,
        'price': properties.get('price'),
        'priceCurrency': properties.get('priceCurrency'),
        'availability': properties.get('availability'),
    }
    return product_offer_data(pseudo_product)


def og_price(og_data):
    """Current/sale OpenGraph price only. Never original/min-30d/pretax values."""
    for key in ('product:sale_price:amount', 'og:sale_price:amount',
                'product:price:amount', 'og:price:amount'):
        if key in og_data:
            price = _as_price(og_data[key])
            if price is not None:
                return price
    return None


def og_currency(og_data):
    for key in ('product:sale_price:currency', 'og:sale_price:currency',
                'product:price:currency', 'og:price:currency'):
        if key in og_data and not _is_missing(og_data[key]):
            return str(og_data[key]).strip().upper()
    return None


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
                if attr == 'type' and value == 'application/ld+json':
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
                except json.JSONDecodeError as e:
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
        }
        self.current_itemprop = None

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)

        if 'itemscope' in attrs_dict:
            itemtype = (attrs_dict.get('itemtype') or '').casefold()
            if 'product' in itemtype:
                self.microdata['product_scopes'] += 1
                itemid = attrs_dict.get('itemid')
                if itemid:
                    self.microdata['product_keys'].append(itemid)

        if 'itemprop' in attrs_dict:
            itemprop = attrs_dict['itemprop']

            # Price/currency/availability can be in content/href attributes
            if itemprop == 'price':
                if 'content' in attrs_dict:
                    self.microdata['price'] = attrs_dict['content']
                    self.microdata['prices'].append(attrs_dict['content'])
                else:
                    self.current_itemprop = 'price'

            elif itemprop in ('sku', 'productID', 'url'):
                value = attrs_dict.get('content') or attrs_dict.get('href')
                if value:
                    self.microdata['product_keys'].append(value)
                else:
                    self.current_itemprop = itemprop

            elif itemprop == 'name':
                value = attrs_dict.get('content')
                if value:
                    self.microdata['product_names'].append(value)
                else:
                    self.current_itemprop = 'name'

            elif itemprop == 'priceCurrency':
                if 'content' in attrs_dict:
                    self.microdata['currency'] = attrs_dict['content']
                    self.microdata['currencies'].append(attrs_dict['content'])
                else:
                    self.current_itemprop = 'priceCurrency'

            elif itemprop == 'availability':
                # Can be in href (link) or content (meta)
                if 'href' in attrs_dict:
                    self.microdata['availability'] = attrs_dict['href']
                    self.microdata['availabilities'].append(attrs_dict['href'])
                elif 'content' in attrs_dict:
                    self.microdata['availability'] = attrs_dict['content']
                    self.microdata['availabilities'].append(attrs_dict['content'])
                else:
                    self.current_itemprop = 'availability'

    def handle_data(self, data):
        # Capture text content for itemprop elements
        if self.current_itemprop == 'price':
            # Try to extract numeric price from text
            try:
                # Not [^\d.] + float(), that breaks "39,99" (#4493), see Restock.parse_currency() for every caller
                price = Restock().parse_currency(data.strip())
                if price is not None:
                    self.microdata['price'] = price
                    self.microdata['prices'].append(data.strip())
            except ValueError:
                pass
        elif self.current_itemprop == 'priceCurrency':
            currency = data.strip()
            if currency:
                self.microdata['currency'] = currency
                self.microdata['currencies'].append(currency)
        elif self.current_itemprop in ('sku', 'productID', 'url'):
            value = data.strip()
            if value:
                self.microdata['product_keys'].append(value)
        elif self.current_itemprop == 'name':
            value = data.strip()
            if value:
                self.microdata['product_names'].append(value)
        elif self.current_itemprop == 'availability':
            availability = data.strip()
            if availability:
                self.microdata['availability'] = availability
                self.microdata['availabilities'].append(availability)

    def handle_endtag(self, tag):
        # Reset current itemprop after closing tag
        self.current_itemprop = None


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
        result = product_offer_data(main_product)
        microdata = extracted_data.get('microdata', {}) or {}
        scopes = microdata.get('product_scopes', 0)
        url_tokens = watch_url_product_keys(url)
        main_url_matched = bool(url_tokens) and _match_product_score(main_product, url_tokens) > 0
        confirmed = False
        if scopes == 1:
            keys = list(microdata.get('product_keys', []))
            main_name = _normalize_name(_ci_get(main_product, 'name'))
            names = [_normalize_name(value)
                     for value in microdata.get('product_names', [])
                     if _normalize_name(value)]
            confirmed = bool(url_tokens) and (
                _match_keys_score(keys, url_tokens) > 0
                or (main_url_matched and _microdata_name_matches(main_name, names)))
            if confirmed:
                currencies = {str(value).strip().upper()
                              for value in microdata.get('currencies', [])
                              if not _is_missing(value)}
                if result.get('currency') is None and len(currencies) == 1:
                    result['currency'] = currencies.pop()
                availabilities = {normalize_availability(value)
                                  for value in microdata.get('availabilities', [])}
                availabilities.discard(None)
                if result.get('availability') is None and len(availabilities) == 1:
                    result['availability'] = availabilities.pop()

        if result.get('price') is None and not has_explicit_missing_price(main_product):
            prices = {_as_price(value) for value in microdata.get('prices', [])}
            prices.discard(None)
            if len(prices) == 1 and (scopes == 0 or confirmed):
                result['price'] = prices.pop()

        # OpenGraph is page-level. Only use it when there is no ambiguous
        # microdata product; otherwise let the extruct path select by URL/name.
        if scopes == 0 or confirmed or main_url_matched:
            if result.get('price') is None and not has_explicit_missing_price(main_product):
                price = og_price(og_data)
                if price is not None:
                    result['price'] = price
            if result.get('currency') is None:
                currency = og_currency(og_data)
                if currency:
                    result['currency'] = currency
            if result.get('availability') is None:
                availability = og_availability(og_data)
                if availability:
                    result['availability'] = availability
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
            values = [product_offer_data(offer) for offer in offers]
            prices = {data.get('price') for data in values if data.get('price') is not None}
            if len(prices) > 1:
                return {}
            if len(prices) == 1:
                return fill_missing_from_og(values[0], og_data)
        if len(offers) == 1:
            return fill_missing_from_og(product_offer_data(offers[0]), og_data)

    if scopes > 1:
        return {}
    if scopes == 1 and url:
        keys = list(microdata.get('product_keys', []))
        url_tokens = watch_url_product_keys(url)
        if (url_tokens
                and any(_has_significant_tokens(key) for key in keys)
                and not _match_keys_score(keys, url_tokens)):
            return {}

    prices = []
    for raw_price in microdata.get('prices', []):
        price = _as_price(raw_price)
        if price is not None and price not in prices:
            prices.append(price)
    if not prices:
        og_price_value = og_price(og_data)
        if og_price_value is not None:
            prices.append(og_price_value)

    availabilities = []
    for raw_availability in microdata.get('availabilities', []):
        availability = normalize_availability(raw_availability)
        if availability and availability not in availabilities:
            availabilities.append(availability)
    og_availability_value = og_availability(og_data)
    if og_availability_value and og_availability_value not in availabilities:
        availabilities.append(og_availability_value)

    currencies = []
    for raw_currency in microdata.get('currencies', []):
        currency = str(raw_currency).strip().upper()
        if not _is_missing(raw_currency) and currency not in currencies:
            currencies.append(currency)
    og_currency_value = og_currency(og_data)
    if og_currency_value and og_currency_value not in currencies:
        currencies.append(og_currency_value)
    if len(currencies) > 1:
        currencies = []

    result = {}
    if len(prices) == 1:
        result['price'] = prices[0]
    if len(currencies) == 1:
        result['currency'] = currencies[0]
    if len(availabilities) == 1:
        result['availability'] = availabilities[0]
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
