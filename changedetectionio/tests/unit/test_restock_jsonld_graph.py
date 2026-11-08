import copy

from changedetectionio.processors.restock_diff.pure_python_extractor import (
    _GRAPH_NODE_CAP,
    _collect_product_candidates,
    _resolve_jsonld_graph,
    select_main_product,
    select_main_product_v2,
)


def test_graph_reference_variants_are_resolved_and_attached():
    blocks = [{'@graph': [
        {'@type': 'ProductGroup', '@id': '#group', 'productGroupID': 'G1',
         'hasVariant': [{'@id': '#v1'}, {'@id': '#v2'}]},
        {'@type': 'Product', '@id': '#v1', 'sku': 'V1',
         'offers': {'price': '10'}},
        {'@type': 'Product', '@id': '#v2', 'sku': 'V2',
         'offers': {'price': '20'}},
    ]}]

    resolved, conflict = _resolve_jsonld_graph(
        blocks, base_url='https://shop.example/products/main')

    assert conflict is False
    group = resolved[0]['@graph'][0]
    assert group['hasVariant'][0]['sku'] == 'V1'
    assert group['hasVariant'][1]['sku'] == 'V2'

    candidates = _collect_product_candidates(resolved)
    group_candidate = [entry for entry in candidates
                       if entry[0].get('productGroupID') == 'G1']
    assert len(group_candidate) == 1
    assert group_candidate[0][1] is True
    assert [entry[0]['sku'] for entry in candidates if not entry[1]] == ['V1', 'V2']


def test_graph_resolution_selects_matching_variant():
    blocks = [{'@graph': [
        {'@type': 'ProductGroup', '@id': '#group', 'name': 'Group',
         'url': 'https://shop.example/products/main',
         'hasVariant': [{'@id': '#v1'}, {'@id': '#v2'}]},
        {'@type': 'Product', '@id': '#v1', 'sku': 'V1',
         'offers': {'price': '10', 'url':
                    'https://shop.example/products/main?variant=111'}},
        {'@type': 'Product', '@id': '#v2', 'sku': 'V2',
         'offers': {'price': '20', 'url':
                    'https://shop.example/products/main?variant=222'}},
    ]}]

    selected = select_main_product(
        blocks, url='https://shop.example/products/main?variant=222',
        resolve_graph=True)

    assert selected['sku'] == 'V2'


def test_graph_main_entity_reference_is_resolved():
    blocks = [
        {'@type': 'WebPage', 'mainEntity': {'@id': '#product'}},
        {'@type': 'Product', '@id': '#product', 'sku': 'MAIN123',
         'offers': {'price': '10'}},
    ]

    resolved, conflict = _resolve_jsonld_graph(
        blocks, base_url='https://shop.example/product/main-MAIN123.html')

    assert conflict is False
    assert resolved[0]['mainEntity']['sku'] == 'MAIN123'
    products = [entry for entry in _collect_product_candidates(resolved)
                if entry[0].get('sku') == 'MAIN123']
    assert len(products) == 1 and products[0][1] is True


def test_graph_duplicate_id_is_not_merged():
    blocks = [
        {'@type': 'ProductGroup', 'hasVariant': [{'@id': '#v'}]},
        {'@type': 'Product', '@id': '#v', 'sku': 'ONE',
         'offers': {'price': '10'}},
        {'@type': 'Product', '@id': '#v', 'sku': 'TWO',
         'offers': {'price': '20'}},
    ]

    resolved, conflict = _resolve_jsonld_graph(
        blocks, base_url='https://shop.example/product/main')

    assert conflict is True
    assert resolved[0]['hasVariant'][0] == {'@id': '#v'}


def test_unreferenced_duplicate_id_is_not_a_graph_conflict():
    blocks = [
        {'@type': 'Product', '@id': '#product', 'name': 'Main', 'sku': '8012',
         'url': 'https://shop.example/products/main',
         'offers': {'price': '219', 'priceCurrency': 'PLN'}},
        {'@type': 'Product', '@id': '#product', 'name': 'Main',
         'aggregateRating': {'@type': 'AggregateRating', 'ratingValue': 4.9}},
    ]

    _resolved, conflict = _resolve_jsonld_graph(
        blocks, base_url='https://shop.example/products/main')
    selected = select_main_product_v2(
        blocks, url='https://shop.example/products/main')

    assert conflict is False
    assert selected['sku'] == '8012'


def test_graph_cycle_does_not_recurse_forever():
    blocks = [
        {'@type': 'ProductGroup', '@id': '#group',
         'hasVariant': [{'@id': '#variant'}]},
        {'@type': 'Product', '@id': '#variant',
         'isVariantOf': {'@id': '#group'}, 'offers': {'price': '10'}},
    ]

    resolved, conflict = _resolve_jsonld_graph(
        blocks, base_url='https://shop.example/product/main')

    assert conflict is False
    assert resolved[0]['hasVariant'][0]['offers']['price'] == '10'


def test_graph_resolution_does_not_mutate_input():
    blocks = [{'@type': 'ProductGroup', '@id': '#group',
               'hasVariant': [{'@id': '#variant'}]},
              {'@type': 'Product', '@id': '#variant', 'offers': {'price': '10'}}]
    original = copy.deepcopy(blocks)

    _resolve_jsonld_graph(blocks, base_url='https://shop.example/product/main')

    assert blocks == original


def test_graph_node_cap_returns_conflict_and_original():
    blocks = [{'@type': 'Product', '@id': f'#p{i}', 'sku': str(i)}
              for i in range(_GRAPH_NODE_CAP + 1)]

    resolved, conflict = _resolve_jsonld_graph(
        blocks, base_url='https://shop.example/product/main')

    assert conflict is True
    assert resolved is blocks
