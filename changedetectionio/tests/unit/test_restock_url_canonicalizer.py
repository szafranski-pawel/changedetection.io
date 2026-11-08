from changedetectionio.processors.restock_diff.pure_python_extractor import (
    _canonical_url_parts,
    _product_identity,
    _same_path_host,
    _url_identity,
    watch_url_product_keys,
)


def test_canonical_parts_normalizes_host_path_and_query():
    parts = _canonical_url_parts(
        'https://WWW.Shop.Example/Products/Main-123.HTML/'
        '?utm_source=x&variant=222&size=M#frag')

    assert parts['host'] == 'shop.example'
    assert parts['path'] == '/products/main-123'
    assert parts['last_segment'] == 'main-123'
    assert parts['query']['tracking'] == {'utm_source': ['x']}
    assert parts['query']['identity'] == {'variant': ['222']}
    assert parts['query']['unknown'] == {'size': ['M']}
    assert parts['variant_values'] == {'222'}
    assert parts['product_values'] == set()


def test_canonical_parts_treats_dwvar_as_identity():
    parts = _canonical_url_parts(
        'https://shop.example/product/g-ABC123.html'
        '?dwvar_ABC123_COLOR=BLUE')

    assert parts['query']['identity'] == {'dwvar_abc123_color': ['BLUE']}
    assert parts['product_values'] == {'abc123'}


def test_product_identity_drops_tracking_but_keeps_unknown_params():
    product = {
        'name': 'Same',
        'url': 'https://shop.example/products/same?utm_source=x&size=M',
    }

    assert _product_identity(product) == (
        'same', '/products/same', (('unknown', 'size', 'M'),))


def test_same_path_host_is_www_case_and_extension_insensitive():
    assert _same_path_host(
        'https://www.shop.example/products/main.html',
        'https://shop.example/Products/Main/',
    )


def test_url_identity_extracts_variant_and_product_values():
    assert _url_identity(
        'https://shop.example/p/main?variant=ABC-222&sku=MAIN123') == (
        'main', {'abc222'}, {'main123'})


def test_watch_keys_ignore_tracking_and_unknown_params():
    assert watch_url_product_keys(
        'https://shop.example/p/main?utm_source=x&size=M&variant=123') == {'123'}


def test_canonical_parts_handles_relative_and_empty_urls():
    relative = _canonical_url_parts('/products/main.html?variant=2')
    assert relative['host'] == ''
    assert relative['path'] == '/products/main'
    assert relative['variant_values'] == {'2'}

    empty = _canonical_url_parts('')
    assert empty['path'] == ''
    assert empty['last_segment'] == ''
