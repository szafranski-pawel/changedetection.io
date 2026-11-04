from changedetectionio.processors.restock_diff.pure_python_extractor import (
    product_offer_data,
    select_main_product_v2,
)


def test_v2_recommendation_variant_does_not_hijack_main():
    blocks = [
        {'@type': 'Product', 'name': 'Main', 'sku': 'MAIN123',
         'offers': {'price': '100'}},
        {'@type': 'ProductGroup', 'name': 'Rec', 'hasVariant': [
            {'@type': 'Product', 'sku': 'RED',
             'offers': {'price': '10'}}]},
    ]

    selected = select_main_product_v2(
        blocks, 'https://shop.example/products/black-dress-MAIN123-red')

    assert selected['sku'] == 'MAIN123'


def test_v2_group_variant_url_selects_matching_variant():
    blocks = [
        {'@type': 'ProductGroup', 'name': 'Group',
         'url': 'https://shop.example/products/group', 'hasVariant': [
             {'@type': 'Product', 'sku': 'G-ONE', 'offers': {
                 'price': '10',
                 'url': 'https://shop.example/products/group?variant=111'}},
             {'@type': 'Product', 'sku': 'G-TWO', 'offers': {
                 'price': '20',
                 'url': 'https://shop.example/products/group?variant=222'}},
         ]},
    ]

    selected = select_main_product_v2(
        blocks, 'https://shop.example/products/group?variant=222')

    assert selected['sku'] == 'G-TWO'
    assert product_offer_data(selected)['price'] == 20.0


def test_v2_group_identity_beats_reused_product_sku():
    blocks = [
        {'@type': 'ProductGroup', 'productGroupID': 'MAIN123',
         'name': 'Group', 'hasVariant': [
             {'@type': 'Product', 'sku': 'V1',
              'offers': {'price': '10'}}]},
        {'@type': 'Product', 'name': 'Rec', 'sku': 'MAIN123',
         'offers': {'price': '99'}},
    ]

    selected = select_main_product_v2(
        blocks, 'https://shop.example/product/main-MAIN123.html')

    assert selected['productGroupID'] == 'MAIN123'


def test_v2_exact_digitless_url_wins():
    blocks = [
        {'@type': 'Product', 'name': 'Black Dress',
         'url': 'https://shop.example/products/black-dress',
         'offers': {'price': '100'}},
        {'@type': 'Product', 'name': 'Red Dress',
         'url': 'https://shop.example/products/red-dress',
         'offers': {'price': '50'}},
    ]

    selected = select_main_product_v2(
        blocks, 'https://shop.example/products/black-dress')

    assert selected['url'] == 'https://shop.example/products/black-dress'


def test_v2_anonymous_single_product_is_accepted():
    blocks = [{'@type': 'Product', 'name': 'Main',
               'offers': {'price': '10'}}]

    selected = select_main_product_v2(
        blocks, 'https://shop.example/product/main-123')

    assert product_offer_data(selected)['price'] == 10.0


def test_v2_graph_reference_variant_is_selected():
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

    selected = select_main_product_v2(
        blocks, 'https://shop.example/products/main?variant=222')

    assert selected['sku'] == 'V2'


def test_v2_duplicate_graph_id_refuses():
    blocks = [
        {'@type': 'ProductGroup', 'hasVariant': [{'@id': '#v'}]},
        {'@type': 'Product', '@id': '#v', 'sku': 'ONE',
         'offers': {'price': '10'}},
        {'@type': 'Product', '@id': '#v', 'sku': 'TWO',
         'offers': {'price': '20'}},
    ]

    assert select_main_product_v2(
        blocks, 'https://shop.example/product/main') is None
