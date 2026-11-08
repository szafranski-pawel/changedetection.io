"""Phase 0 characterization snapshots for the restock extractor.

This file is intentionally test-only: it freezes the current pure/extruct/safe
behaviour, including known divergences, before the identity refactor starts.
It does not change production data structures or the Restock API.
"""

import json
import os
from pathlib import Path

import extruct
from changedetectionio.processors.restock_diff.processor import (
    extract_itemprop_availability_safe,
    get_itemprop_availability_with_provenance,
)
from changedetectionio.processors.restock_diff.pure_python_extractor import (
    _ci_get,
    _normalize_name,
    _product_identity,
    _product_strong_keys,
    extract_metadata_pure_python,
    query_price_availability,
    select_main_product,
)

SNAPSHOT_PATH = Path(__file__).with_name('restock_snapshots.json')
POSITIVE_STOCK = (
    'instock', 'instoreonly', 'limitedavailability', 'onlineonly', 'presale')

# Pinned parser versions used to generate the fixtures. Full live pages stay
# out of the repo; these are reduced synthetic fragments.
FIXTURE_MANIFEST = {
    'extruct': '0.18.0',
    'lxml': '6.x',
    'python': '3.11+',
    'fixture_policy': 'reduced synthetic fragments only; full pages stay private',
}

TARGETS = {
    'anonymous_single_top_level': {
        'price': 10.0, 'currency': 'PLN', 'availability': 'instock',
        'in_stock': True},
    'bare_offers_same_price_diff_stock': {
        'price': 10.0, 'currency': None, 'availability': None,
        'in_stock': None},
    'conflicting_offer_stock': {
        'price': 10.0, 'currency': 'PLN', 'availability': None,
        'in_stock': None},
    'duplicate_identity_prices': {
        'price': 10.0, 'currency': None, 'availability': None,
        'in_stock': None},
    'explicit_null_main': {
        'price': None, 'currency': None, 'availability': 'outofstock',
        'in_stock': False},
    'microdata_scope_name_mismatch': {
        'price': 10.0, 'currency': 'PLN', 'availability': None,
        'in_stock': None},
    'microdata_sibling_size': {
        'price': None, 'currency': None, 'availability': None,
        'in_stock': None},
    'nested_price_unit': {
        'price': 10.0, 'currency': None, 'availability': 'instock',
        'in_stock': True},
    'og_sale_currency': {
        'price': 80.0, 'currency': 'EUR', 'availability': None,
        'in_stock': None},
    'product_group_no_variant': {
        'price': 219.0, 'currency': None, 'availability': None,
        'in_stock': None},
    'same_price_recommendations_vs_main_microdata': {
        'price': 19.99, 'currency': None, 'availability': 'outofstock',
        'in_stock': False},
    'single_recommendation_with_url': {
        'price': None, 'currency': None, 'availability': None,
        'in_stock': None},
    'variant_group_offer_url': {
        'price': 84.99, 'currency': None, 'availability': None,
        'in_stock': None},
    'variant_value_different_path': {
        'price': None, 'currency': None, 'availability': None,
        'in_stock': None},
}


def stock_state(availability):
    if not availability:
        return None
    value = str(availability).lower()
    return any(token in value for token in POSITIVE_STOCK)


def identity(product):
    if not isinstance(product, dict):
        return None
    name, path, query = _product_identity(product)
    return {
        'name': name,
        'path': path,
        'query': [list(entry) for entry in query],
        'strong': sorted(_product_strong_keys(product)),
    }


def result_data(value):
    return {
        'price': value.get('price'),
        'currency': value.get('currency'),
        'availability': value.get('availability'),
        'in_stock': stock_state(value.get('availability')),
    }


def outcome(html, url):
    pure_data = extract_metadata_pure_python(html)
    pure_main = select_main_product(pure_data.get('json-ld', []), url=url)
    pure_result = query_price_availability(
        pure_data, url=url, main_product=pure_main)

    extruct_data = extruct.extract(
        html, syntaxes=['json-ld', 'microdata', 'opengraph'])
    extruct_main = select_main_product(
        extruct_data.get('json-ld', []), url=url)

    extruct_error = None
    extruct_had_main = False
    try:
        extruct_result, extruct_had_main = (
            get_itemprop_availability_with_provenance(html, url=url))
    except Exception as exc:
        extruct_error = type(exc).__name__
        extruct_result = {}

    safe_error = None
    try:
        safe_result = extract_itemprop_availability_safe(html, url=url)
    except Exception as exc:
        safe_error = type(exc).__name__
        safe_result = {}

    pure = {
        **result_data(pure_result),
        'had_main': bool(pure_main),
        'identity': identity(pure_main),
        'error': None,
    }
    ext = {
        **result_data(extruct_result),
        'had_main': bool(extruct_had_main),
        'identity': identity(extruct_main),
        'error': extruct_error,
    }
    fields = ('price', 'currency', 'availability', 'in_stock', 'had_main', 'error')
    return {
        'pure': pure,
        'extruct': ext,
        'safe': {
            **result_data(safe_result),
            'error': safe_error,
            'reason': ('ambiguous' if safe_error == 'MoreThanOnePriceFound'
                       else safe_error or ('price' if safe_result.get('price') is not None
                                           else 'no_data')),
        },
        'divergence': {field: [pure[field], ext[field]] for field in fields
                       if pure[field] != ext[field]},
    }


def legacy_outcome(html):
    data = extract_metadata_pure_python(html)
    pure_result = query_price_availability(data)
    safe_result = extract_itemprop_availability_safe(html)
    return {
        'pure': result_data(pure_result),
        'safe': result_data(safe_result),
    }


CASES = [
    {
        'name': 'single_recommendation_with_url',
        'url': 'https://shop.example/product/main-123',
        'html': '<html><body><script type="application/ld+json">'
                '{"@context":"https://schema.org","@type":"Product",'
                '"name":"Recommendation","url":"https://shop.example/product/other-999",'
                '"offers":{"@type":"Offer","price":"9.99","priceCurrency":"USD",'
                '"availability":"https://schema.org/InStock"}}</script></body></html>',
    },
    {
        'name': 'same_price_recommendations_vs_main_microdata',
        'url': 'https://shop.example/product/main-123',
        'html': '<html><body>'
                '<script type="application/ld+json">{"@context":"https://schema.org",'
                '"@type":"Product","name":"Rec A","offers":{"@type":"Offer","price":"9.99",'
                '"availability":"https://schema.org/InStock"}}</script>'
                '<script type="application/ld+json">{"@context":"https://schema.org",'
                '"@type":"Product","name":"Rec B","offers":{"@type":"Offer","price":"9.99",'
                '"availability":"https://schema.org/InStock"}}</script>'
                '<div itemscope itemtype="http://schema.org/Product" '
                'itemid="https://shop.example/product/main-123"><div itemprop="offers" '
                'itemscope itemtype="http://schema.org/Offer"><meta itemprop="price" content="19.99">'
                '<link itemprop="availability" href="https://schema.org/OutOfStock"></div></div>'
                '</body></html>',
    },
    {
        'name': 'conflicting_offer_stock',
        'url': 'https://shop.example/product/main-MAIN123.html',
        'html': '<html><body><script type="application/ld+json">'
                '{"@context":"https://schema.org","@type":"Product","name":"Main",'
                '"sku":"MAIN123","offers":[{"@type":"Offer","price":"10",'
                '"priceCurrency":"PLN","availability":"https://schema.org/OutOfStock"},'
                '{"@type":"Offer","price":"10","priceCurrency":"PLN",'
                '"availability":"https://schema.org/InStock"}]}</script></body></html>',
    },
    {
        'name': 'variant_value_different_path',
        'url': 'https://shop.example/product/A?variant=red',
        'html': '<html><body><script type="application/ld+json">'
                '{"@context":"https://schema.org","@type":"Product","name":"Main",'
                '"offers":{"@type":"Offer","price":"100","availability":"https://schema.org/InStock"}}'
                '</script><script type="application/ld+json">'
                '{"@context":"https://schema.org","@type":"Product","name":"Rec",'
                '"url":"https://shop.example/product/B?variant=red",'
                '"offers":{"@type":"Offer","price":"10","availability":"https://schema.org/InStock"}}'
                '</script></body></html>',
    },
    {
        'name': 'explicit_null_main',
        'url': 'https://shop.example/collection/same-name.html',
        'html': '<html><body><script type="application/ld+json">'
                '{"@context":"https://schema.org","@type":"Product","name":"Same Name",'
                '"sku":"MAIN123","offers":{"@type":"Offer","price":"null",'
                '"availability":"https://schema.org/OutOfStock"}}</script>'
                '<div itemscope itemtype="http://schema.org/Product"><span itemprop="name">Same Name</span>'
                '<div itemprop="offers" itemscope itemtype="http://schema.org/Offer">'
                '<meta itemprop="price" content="99.9"><link itemprop="availability" '
                'href="https://schema.org/InStock"></div></div></body></html>',
    },
    {
        'name': 'microdata_scope_name_mismatch',
        'url': 'https://shop.example/collections/spring',
        'html': '<html><body><script type="application/ld+json">'
                '{"@context":"https://schema.org","@type":"Product","name":"Ring",'
                '"sku":"MAIN123","offers":{"@type":"Offer","price":"10","priceCurrency":"PLN"}}'
                '</script><div itemscope itemtype="http://schema.org/Product">'
                '<span itemprop="name">Earrings</span><div itemprop="offers" '
                'itemscope itemtype="http://schema.org/Offer"><meta itemprop="price" content="99">'
                '<link itemprop="availability" href="https://schema.org/InStock"></div></div>'
                '</body></html>',
    },
    {
        'name': 'duplicate_identity_prices',
        'url': 'https://shop.example/products/same',
        'html': '<html><body><script type="application/ld+json">'
                '{"@context":"https://schema.org","@type":"Product","name":"Same",'
                '"url":"https://shop.example/products/same","offers":{"@type":"Offer","price":"10"}}'
                '</script><script type="application/ld+json">'
                '{"@context":"https://schema.org","@type":"Product","name":"Same",'
                '"url":"https://shop.example/products/same","offers":{"@type":"Offer","price":"99"}}'
                '</script></body></html>',
    },
    {
        'name': 'nested_price_unit',
        'url': 'https://shop.example/product/main-1299.html',
        'html': '<html><body><div itemscope itemtype="http://schema.org/Product" '
                'itemid="https://shop.example/product/main-1299.html"><div itemprop="offers" '
                'itemscope itemtype="http://schema.org/Offer"><span itemprop="price">10'
                '<span class="unit">/100ml</span></span><link itemprop="availability" '
                'href="https://schema.org/InStock"></div></div></body></html>',
    },
    {
        'name': 'anonymous_single_top_level',
        'url': 'https://shop.example/product/main-123',
        'html': '<html><body><script type="application/ld+json">'
                '{"@context":"https://schema.org","@type":"Product","name":"Main",'
                '"offers":{"@type":"Offer","price":"10","priceCurrency":"PLN",'
                '"availability":"https://schema.org/InStock"}}</script></body></html>',
    },
    {
        'name': 'variant_group_offer_url',
        'url': 'https://shop.example/products/group?variant=222',
        'html': '<html><body><script type="application/ld+json">'
                '{"@context":"https://schema.org","@type":"ProductGroup","name":"Group",'
                '"url":"https://shop.example/products/group","hasVariant":['
                '{"@type":"Product","sku":"G-ONE","offers":{"@type":"Offer",'
                '"url":"https://shop.example/products/group?variant=111","price":"21.99"}},'
                '{"@type":"Product","sku":"G-TWO","offers":{"@type":"Offer",'
                '"url":"https://shop.example/products/group?variant=222","price":"84.99"}}]}'
                '</script></body></html>',
    },
    {
        'name': 'product_group_no_variant',
        'url': 'https://shop.example/products/huel',
        'html': '<html><body><script type="application/ld+json">'
                '{"@context":"https://schema.org","@type":"ProductGroup","name":"Huel",'
                '"url":"https://shop.example/products/huel","hasVariant":['
                '{"@type":"Product","sku":"HUE-219","offers":{"@type":"Offer","price":"219"}},'
                '{"@type":"Product","sku":"HUE-190","offers":{"@type":"Offer","price":"190"}}]}'
                '</script></body></html>',
    },
    {
        'name': 'og_sale_currency',
        'url': 'https://shop.example/product/main-MAIN123.html',
        'html': '<html><head><meta property="product:price:amount" content="100">'
                '<meta property="product:price:currency" content="PLN">'
                '<meta property="product:sale_price:amount" content="80">'
                '<meta property="product:sale_price:currency" content="EUR"></head><body>'
                '<script type="application/ld+json">{"@context":"https://schema.org",'
                '"@type":"Product","name":"Main","sku":"MAIN123","offers":{"@type":"Offer",'
                '"price":"80"}}</script></body></html>',
    },
    {
        'name': 'microdata_sibling_size',
        'url': 'https://shop.example/product/ABC123/128GB',
        'html': '<html><body><div itemscope itemtype="http://schema.org/Product" '
                'itemid="https://shop.example/product/ABC123/256GB"><span itemprop="name">Phone</span>'
                '<meta itemprop="sku" content="ABC123"><div itemprop="offers" '
                'itemscope itemtype="http://schema.org/Offer"><meta itemprop="price" content="20">'
                '<link itemprop="availability" href="https://schema.org/InStock"></div></div>'
                '</body></html>',
    },
    {
        'name': 'bare_offers_same_price_diff_stock',
        'url': 'https://shop.example/product/foo',
        'html': '<html><body><script type="application/ld+json">'
                '{"@context":"https://schema.org","@type":"Offer","price":"10",'
                '"priceCurrency":"PLN","availability":"https://schema.org/InStock"}'
                '</script><script type="application/ld+json">'
                '{"@context":"https://schema.org","@type":"Offer","price":"10",'
                '"priceCurrency":"EUR","availability":"https://schema.org/OutOfStock"}'
                '</script></body></html>',
    },
]

LEGACY_CASES = [
    {
        'name': 'legacy_split_price',
        'html': '<html><body><div itemscope itemtype="http://schema.org/Product">'
                '<div itemprop="offers" itemscope itemtype="http://schema.org/Offer">'
                '<span itemprop="price"><span class="int">1.299</span>'
                '<span class="dec">,00</span></span></div></div></body></html>',
    },
]


def build_snapshot():
    return {
        'manifest': FIXTURE_MANIFEST,
        'cases': {
            case['name']: {
                'expected_current': outcome(case['html'], case['url']),
                'expected_target': TARGETS[case['name']],
            }
            for case in CASES
        },
        'legacy': {case['name']: legacy_outcome(case['html']) for case in LEGACY_CASES},
    }


def test_fixture_manifest_versions():
    from importlib.metadata import version

    assert version('extruct') == FIXTURE_MANIFEST['extruct']
    assert version('lxml').split('.')[0] == FIXTURE_MANIFEST['lxml'].split('.')[0]


def test_restock_snapshots():
    if os.environ.get('UPDATE_RESTOCK_SNAPSHOTS') == '1':
        SNAPSHOT_PATH.write_text(
            json.dumps(build_snapshot(), indent=2, sort_keys=True) + '\n',
            encoding='utf-8')
        return
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding='utf-8'))
    actual = build_snapshot()
    assert actual == expected


if __name__ == '__main__':
    SNAPSHOT_PATH.write_text(
        json.dumps(build_snapshot(), indent=2, sort_keys=True) + '\n',
        encoding='utf-8')
