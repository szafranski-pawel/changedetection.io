from unittest.mock import patch

import changedetectionio.processors.restock_diff.pure_python_extractor as pure
from changedetectionio.processors.restock_diff.pure_python_extractor import (
    product_offer_data,
    select_main_product_for_processing,
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


def _capture_shadow_logs():
    messages = []
    sink_id = pure.logger.add(
        lambda message: messages.append(str(message)), level='WARNING')
    return messages, sink_id


def test_shadow_mode_returns_old_and_logs_difference():
    old = {'@type': 'Product', 'name': 'Old', 'offers': {'price': '10'}}
    new = {'@type': 'Product', 'name': 'New', 'offers': {'price': '20'}}
    messages, sink_id = _capture_shadow_logs()

    try:
        with patch.object(pure, 'IDENTITY_V2', False), \
                patch.object(pure, 'IDENTITY_SHADOW', True), \
                patch.object(pure, 'select_main_product', return_value=old), \
                patch.object(pure, 'select_main_product_v2', return_value=new):
            selected = select_main_product_for_processing(
                [], 'https://shop.example/product/main')
    finally:
        pure.logger.remove(sink_id)

    assert selected is old
    assert any('Identity shadow difference' in message for message in messages)


def test_shadow_mode_does_not_log_when_equal():
    product = {'@type': 'Product', 'name': 'Same',
               'offers': {'price': '10'}}
    messages, sink_id = _capture_shadow_logs()

    try:
        with patch.object(pure, 'IDENTITY_V2', False), \
                patch.object(pure, 'IDENTITY_SHADOW', True), \
                patch.object(pure, 'select_main_product', return_value=product), \
                patch.object(pure, 'select_main_product_v2', return_value=product):
            selected = select_main_product_for_processing(
                [], 'https://shop.example/product/main')
    finally:
        pure.logger.remove(sink_id)

    assert selected is product
    assert not any('Identity shadow difference' in message
                   for message in messages)


def test_v2_authoritative_when_enabled():
    old = {'@type': 'Product', 'name': 'Old'}
    new = {'@type': 'Product', 'name': 'New'}

    with patch.object(pure, 'IDENTITY_V2', True), \
            patch.object(pure, 'IDENTITY_SHADOW', False), \
            patch.object(pure, 'select_main_product', return_value=old), \
            patch.object(pure, 'select_main_product_v2', return_value=new):
        selected = select_main_product_for_processing(
            [], 'https://shop.example/product/main')

    assert selected is new


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
