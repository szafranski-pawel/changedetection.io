#!/usr/bin/env python3

import unittest

import changedetectionio.processors.restock_diff.processor as restock_diff


def extract(html, url):
    return restock_diff.extract_itemprop_availability_safe(html, url=url)


class TestRestockProductScope(unittest.TestCase):

    def test_main_product_null_price_ignores_carousel(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Main Blouse",
         "sku": "CL1294P", "offers": {"@type": "Offer", "price": "null", "priceCurrency": ""}}
        </script>
        <div itemscope itemtype="http://schema.org/Product">
          <span itemprop="name">Main Blouse</span>
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="price" content="null" />
            <meta itemprop="availability" content="OutOfStock" />
          </div>
        </div>
        <div itemscope itemtype="http://schema.org/Product">
          <span itemprop="name">Carousel Top</span>
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="price" content="99.9" />
            <link itemprop="availability" href="http://schema.org/InStock" />
          </div>
        </div>
        </body></html>"""

        value = extract(html, 'https://shop.example/product/main-blouse-CL1294P.html')
        self.assertIsNone(value.get('price'))
        self.assertEqual(value.get('availability'), 'outofstock')

    def test_main_product_price_beats_carousel(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Main Blouse",
         "sku": "CL1294P", "offers": {"@type": "Offer", "price": "189.90",
         "priceCurrency": "PLN", "availability": "http://schema.org/InStock"}}
        </script>
        <div itemscope itemtype="http://schema.org/Product">
          <span itemprop="name">Main Blouse</span>
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="price" content="189.90" />
            <link itemprop="availability" href="http://schema.org/InStock" />
          </div>
        </div>
        <div itemscope itemtype="http://schema.org/Product">
          <span itemprop="name">Carousel Top</span>
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="price" content="99.9" />
            <link itemprop="availability" href="http://schema.org/InStock" />
          </div>
        </div>
        </body></html>"""

        value = extract(html, 'https://shop.example/product/main-blouse-CL1294P.html')
        self.assertEqual(value.get('price'), 189.9)
        self.assertEqual(value.get('currency'), 'PLN')
        self.assertEqual(value.get('availability'), 'instock')

    def test_itemlist_recommendations_are_not_treated_as_top_level(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Main Product",
         "sku": "MAIN123", "offers": {"@type": "Offer", "price": "10",
         "priceCurrency": "PLN", "availability": "http://schema.org/InStock"}}
        </script>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "ItemList", "itemListElement": [
          {"@type": "Product", "name": "Carousel A", "sku": "REC111",
           "offers": {"@type": "Offer", "price": "99", "priceCurrency": "PLN",
                      "availability": "http://schema.org/InStock"}},
          {"@type": "Product", "name": "Carousel B", "sku": "REC222",
           "offers": {"@type": "Offer", "price": "88", "priceCurrency": "PLN",
                      "availability": "http://schema.org/InStock"}}
        ]}
        </script>
        </body></html>"""

        with_sku = extract(html, 'https://shop.example/product/main-MAIN123.html')
        self.assertEqual(with_sku.get('price'), 10.0)

        without_token = extract(html, 'https://shop.example/product/foo')
        self.assertEqual(without_token.get('price'), 10.0)

    def test_null_main_with_priced_recommendation_does_not_guess(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Main Product",
         "sku": "MAIN123", "offers": {"@type": "Offer", "price": "null"}}
        </script>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Priced Recommendation",
         "offers": {"@type": "Offer", "price": "99", "priceCurrency": "PLN",
         "availability": "http://schema.org/InStock"}}
        </script>
        </body></html>"""

        value = extract(html, 'https://shop.example/product/foo')
        self.assertIsNone(value.get('price'))

    def test_microdata_url_match_beats_duplicate_name(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Same Name",
         "sku": "MAIN123", "offers": {"@type": "Offer", "price": "null"}}
        </script>
        <div itemscope itemtype="http://schema.org/Product"
             itemid="https://shop.example/product/other-OTHER999.html">
          <span itemprop="name">Same Name</span>
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="price" content="99.9" />
            <link itemprop="availability" href="http://schema.org/InStock" />
          </div>
        </div>
        <div itemscope itemtype="http://schema.org/Product"
             itemid="https://shop.example/product/main-MAIN123.html">
          <span itemprop="name">Same Name</span>
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="price" content="null" />
            <meta itemprop="availability" content="OutOfStock" />
          </div>
        </div>
        </body></html>"""

        value = extract(html, 'https://shop.example/product/main-MAIN123.html')
        self.assertIsNone(value.get('price'))
        self.assertEqual(value.get('availability'), 'outofstock')

    def test_query_param_color_size_matching_is_not_substring(self):
        from changedetectionio.processors.restock_diff.pure_python_extractor import watch_url_product_keys

        keys = watch_url_product_keys(
            'https://shop.example/product/foo?collection=999999&sized=888888&color=123456&pid=MAIN123')
        self.assertIn('main123', keys)
        self.assertIn('123456', keys)
        self.assertNotIn('999999', keys)
        self.assertNotIn('888888', keys)

    def test_short_numeric_sku_matches_full_token_only(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Decoy",
         "sku": "13230", "offers": {"@type": "Offer", "price": "99",
         "priceCurrency": "PLN", "availability": "http://schema.org/InStock"}}
        </script>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Main Bag",
         "sku": "3230", "offers": {"@type": "Offer", "price": "10",
         "priceCurrency": "PLN", "availability": "http://schema.org/InStock"}}
        </script>
        </body></html>"""

        value = extract(html, 'https://shop.example/pl/torby/3230-sicily-bag.html')
        self.assertEqual(value.get('price'), 10.0)

    def test_recommendation_jsonld_before_main_is_ignored(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Recommendation",
         "offers": {"@type": "Offer", "price": "229.9", "priceCurrency": "PLN",
         "availability": "http://schema.org/InStock"}}
        </script>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Main Blouse",
         "sku": "CL1294P", "offers": {"@type": "Offer", "price": "189.9",
         "priceCurrency": "PLN", "availability": "http://schema.org/InStock"}}
        </script>
        </body></html>"""

        value = extract(html, 'https://shop.example/product/main-blouse-CL1294P.html')
        self.assertEqual(value.get('price'), 189.9)

    def test_pure_partial_survives_extruct_failure(self):
        # Kazar's rendered page can make extruct fail even though pure Python
        # already found the single product price. Never discard that price.
        from changedetectionio.processors.restock_diff import Restock
        from changedetectionio.processors.restock_diff.processor import _combine_with_pure_partial

        merged = _combine_with_pure_partial(
            {'price': 1299.0, 'currency': 'PLN'}, Restock(), scoped=True)
        self.assertEqual(merged.get('price'), 1299.0)
        self.assertEqual(merged.get('currency'), 'PLN')

        merged = _combine_with_pure_partial(
            {'price': 205.9, 'currency': 'PLN'},
            Restock({'availability': 'outofstock'}), scoped=True)
        self.assertEqual(merged.get('price'), 205.9)
        self.assertEqual(merged.get('availability'), 'outofstock')

        # Different provenance: never attach a fallback price to a selected main.
        merged = _combine_with_pure_partial(
            {'price': 99.0},
            Restock({'availability': 'outofstock'}), scoped=True,
            pure_had_main=False, fetched_had_main=True)
        self.assertIsNone(merged.get('price'))
        self.assertEqual(merged.get('availability'), 'outofstock')

        # Different provenance: keep the selected-main price.
        merged = _combine_with_pure_partial(
            {'price': 205.9},
            Restock({'price': 99.0, 'availability': 'outofstock'}), scoped=True,
            pure_had_main=True, fetched_had_main=False)
        self.assertEqual(merged.get('price'), 205.9)

    def test_nan_and_infinity_prices_are_ignored(self):
        from changedetectionio.processors.restock_diff.processor import _safe_float

        self.assertIsNone(_safe_float('nan'))
        self.assertIsNone(_safe_float('inf'))

        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Broken Price",
         "sku": "BROKEN123", "offers": {"@type": "Offer", "price": NaN,
         "priceCurrency": "PLN", "availability": "http://schema.org/InStock"}}
        </script>
        </body></html>"""

        value = extract(html, 'https://shop.example/product/broken-BROKEN123.html')
        self.assertIsNone(value.get('price'))

    def test_no_product_jsonld_uses_single_microdata_price(self):
        # Kazar-style page: no Product JSON-LD, one microdata Product + product:* OG.
        html = """<html><head>
        <meta property="product:price:amount" content="1299" />
        <meta property="product:price:currency" content="PLN" />
        </head><body>
        <div itemscope itemtype="http://schema.org/Product">
          <span itemprop="name">Leather Boots</span>
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="price" content="1299" />
            <meta itemprop="priceCurrency" content="PLN" />
          </div>
        </div>
        </body></html>"""

        value = extract(html, 'https://kazar.com/leather-boots-93220-01-00.html')
        self.assertEqual(value.get('price'), 1299.0)
        self.assertEqual(value.get('currency'), 'PLN')
        self.assertIsNone(value.get('availability'))

    def test_sale_price_wins_over_original_and_min_30d(self):
        html = """<html><head>
        <meta property="product:price:amount" content="74.90" />
        <meta property="product:sale_price:amount" content="74.90" />
        <meta property="product:original_price:amount" content="249.90" />
        <meta property="product:min_30d_price:amount" content="124.90" />
        </head><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Dress",
         "sku": "44132-99X", "offers": {"@type": "Offer", "price": "74.90",
         "priceCurrency": "PLN", "availability": "https://schema.org/OutOfStock"}}
        </script>
        </body></html>"""

        value = extract(html, 'https://esotiq.com/pl/pl/dress-black-44132-99X')
        self.assertEqual(value.get('price'), 74.9)
        self.assertEqual(value.get('availability'), 'outofstock')

    def test_product_group_variant_disagreement_is_partial(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "ProductGroup",
         "name": "Main Product", "productGroupID": "SKU123_color_1",
         "hasVariant": [
           {"@type": "Product", "sku": "SKU123 1S",
            "offers": {"@type": "Offer", "price": "10", "priceCurrency": "PLN",
                       "availability": "http://schema.org/InStock"}},
           {"@type": "Product", "sku": "SKU123 1M",
            "offers": {"@type": "Offer", "price": "20", "priceCurrency": "PLN",
                       "availability": "http://schema.org/OutOfStock"}}
         ]}
        </script>
        </body></html>"""

        value = extract(html, 'https://shop.example/product/main-SKU123.html')
        self.assertIsNone(value.get('price'))
        self.assertIsNone(value.get('availability'))

    def test_zero_price_is_valid(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Free Sample",
         "sku": "ZERO123", "offers": {"@type": "Offer", "price": 0,
         "priceCurrency": "USD", "availability": "http://schema.org/InStock"}}
        </script>
        </body></html>"""

        value = extract(html, 'https://shop.example/product/free-ZERO123.html')
        self.assertEqual(value.get('price'), 0.0)
        self.assertEqual(value.get('availability'), 'instock')

    def test_price_specification_currency(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Widget",
         "sku": "WIDGET123", "offers": {"@type": "Offer",
         "priceSpecification": {"@type": "PriceSpecification", "price": "155.55",
                                "priceCurrency": "USD"},
         "availability": "http://schema.org/InStock"}}
        </script>
        </body></html>"""

        value = extract(html, 'https://shop.example/product/widget-WIDGET123.html')
        self.assertEqual(value.get('price'), 155.55)
        self.assertEqual(value.get('currency'), 'USD')

    def test_no_product_jsonld_availability_only_is_kept(self):
        html = """<html><body>
        <div itemscope itemtype="http://schema.org/Product">
          <span itemprop="name">Main Product</span>
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="availability" content="OutOfStock" />
          </div>
        </div>
        </body></html>"""

        value = extract(html, 'https://shop.example/product/main-93220-01-00.html')
        self.assertIsNone(value.get('price'))
        self.assertEqual(value.get('availability'), 'outofstock')

    def test_no_product_jsonld_multiple_microdata_products_refuses(self):
        html = """<html><body>
        <div itemscope itemtype="http://schema.org/Product">
          <span itemprop="name">Main Product</span>
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="price" content="1299" />
          </div>
        </div>
        <div itemscope itemtype="http://schema.org/Product">
          <span itemprop="name">Carousel Product</span>
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="price" content="99.9" />
          </div>
        </div>
        </body></html>"""

        with self.assertRaises(restock_diff.MoreThanOnePriceFound):
            extract(html, 'https://shop.example/product/main-93220-01-00.html')


if __name__ == '__main__':
    unittest.main()
