#!/usr/bin/env python3

import unittest

import changedetectionio.processors.restock_diff.processor as restock_diff
from changedetectionio.processors.restock_diff.pure_python_extractor import (
    extract_metadata_pure_python,
    query_price_availability,
)


def extract(html, url):
    return restock_diff.extract_itemprop_availability_safe(html, url=url)


def pure(html, url):
    return query_price_availability(extract_metadata_pure_python(html), url=url)


def extruct(html, url):
    return restock_diff.get_itemprop_availability(html, url=url)


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
        <div itemscope itemtype="http://schema.org/Product"
             itemid="BO1294C_color_9887">
          <span itemprop="name">Carousel Top</span>
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="price" content="99.9" />
            <link itemprop="availability" href="http://schema.org/InStock" />
          </div>
        </div>
        </body></html>"""

        url = 'https://shop.example/product/main-blouse-CL1294P.html?dwvar_CL1294P_Z_COL_INTD=9887'
        value = extract(html, url)
        self.assertIsNone(value.get('price'))
        self.assertEqual(value.get('availability'), 'outofstock')
        self.assertEqual(pure(html, url), {})
        self.assertEqual(extruct(html, url).get('availability'), 'outofstock')

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

        url = 'https://shop.example/product/main-blouse-CL1294P.html'
        value = extract(html, url)
        self.assertEqual(value.get('price'), 189.9)
        self.assertEqual(value.get('currency'), 'PLN')
        self.assertEqual(value.get('availability'), 'instock')
        self.assertEqual(pure(html, url).get('price'), 189.9)
        self.assertEqual(extruct(html, url).get('price'), 189.9)

    def test_duplicate_product_blocks_use_first(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Same Name",
         "url": "https://shop.example/products/more-collagen",
         "offers": {"@type": "Offer", "price": "99.99", "priceCurrency": "PLN",
                    "availability": "http://schema.org/InStock"}}
        </script>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "ProductGroup", "name": "Same Name",
         "url": "https://shop.example/products/more-collagen",
         "hasVariant": [{"@type": "Product", "sku": "5906639913603",
          "offers": {"@type": "Offer", "price": "84.99", "priceCurrency": "PLN",
                     "availability": "http://schema.org/InStock"}}]}
        </script>
        </body></html>"""

        url = 'https://shop.example/products/more-collagen'
        value = extract(html, url)
        self.assertEqual(value.get('price'), 99.99)
        self.assertEqual(value.get('availability'), 'instock')
        self.assertEqual(pure(html, url).get('price'), 99.99)
        self.assertEqual(extruct(html, url).get('price'), 99.99)

    def test_id_fragment_does_not_block_url_fallback(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "ProductGroup", "name": "Main",
         "url": "https://shop.example/products/foo-30",
         "@id": "https://shop.example/products/foo-30#product",
         "offers": {"@type": "Offer", "price": "10", "priceCurrency": "PLN",
                    "availability": "http://schema.org/InStock"}}
        </script>
        </body></html>"""

        url = 'https://shop.example/products/foo-30?variant=58522834239823'
        value = extract(html, url)
        self.assertEqual(value.get('price'), 10.0)
        self.assertEqual(value.get('availability'), 'instock')
        self.assertEqual(pure(html, url).get('price'), 10.0)
        self.assertEqual(extruct(html, url).get('price'), 10.0)

    def test_duplicate_blocks_still_match_url_sku(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Same",
         "url": "https://shop.example/products/more-collagen", "sku": "AAA111",
         "offers": {"@type": "Offer", "price": "10"}}
        </script>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Same",
         "url": "https://shop.example/products/more-collagen", "sku": "BBB222",
         "offers": {"@type": "Offer", "price": "20"}}
        </script>
        </body></html>"""

        url = 'https://shop.example/products/more-collagen?sku=BBB222'
        self.assertEqual(extract(html, url).get('price'), 20.0)
        self.assertEqual(pure(html, url).get('price'), 20.0)
        self.assertEqual(extruct(html, url).get('price'), 20.0)

    def test_multiple_unmatched_jsonld_products_raise(self):
        html = """<html><head>
        <meta property="og:price:amount" content="15" />
        </head><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "A",
         "sku": "AAA111", "offers": {"@type": "Offer", "price": "10"}}
        </script>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "B",
         "sku": "BBB222", "offers": {"@type": "Offer", "price": "20"}}
        </script>
        </body></html>"""

        url = 'https://shop.example/product/foo'
        self.assertEqual(pure(html, url), {})
        with self.assertRaises(restock_diff.MoreThanOnePriceFound):
            extract(html, url)
        with self.assertRaises(restock_diff.MoreThanOnePriceFound):
            extruct(html, url)

    def test_multiple_unmatched_jsonld_products_with_same_price_are_used(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "A",
         "sku": "AAA111", "offers": {"@type": "Offer", "price": "10",
          "priceCurrency": "PLN", "availability": "http://schema.org/InStock"}}
        </script>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "B",
         "sku": "BBB222", "offers": {"@type": "Offer", "price": "10",
          "priceCurrency": "PLN", "availability": "http://schema.org/InStock"}}
        </script>
        </body></html>"""

        url = 'https://shop.example/product/foo'
        self.assertEqual(extract(html, url).get('price'), 10.0)
        self.assertEqual(extract(html, url).get('availability'), 'instock')
        self.assertEqual(pure(html, url).get('price'), 10.0)
        self.assertEqual(extruct(html, url).get('price'), 10.0)

    def test_itemlist_with_only_product_is_used(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "ItemList",
         "itemListElement": [{"@type": "Product", "name": "Only",
          "sku": "MAIN123", "offers": {"@type": "Offer", "price": "10",
           "priceCurrency": "PLN", "availability": "http://schema.org/InStock"}}]}
        </script>
        </body></html>"""

        url = 'https://shop.example/product/black-dress'
        self.assertEqual(extract(html, url).get('price'), 10.0)
        self.assertEqual(extract(html, url).get('availability'), 'instock')
        self.assertEqual(pure(html, url).get('price'), 10.0)
        self.assertEqual(extruct(html, url).get('price'), 10.0)

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

        with_sku_url = 'https://shop.example/product/main-MAIN123.html'
        with_sku = extract(html, with_sku_url)
        self.assertEqual(with_sku.get('price'), 10.0)
        self.assertEqual(pure(html, with_sku_url).get('price'), 10.0)
        self.assertEqual(extruct(html, with_sku_url).get('price'), 10.0)

        without_token_url = 'https://shop.example/product/foo'
        without_token = extract(html, without_token_url)
        self.assertEqual(without_token.get('price'), 10.0)
        self.assertEqual(pure(html, without_token_url).get('price'), 10.0)
        self.assertEqual(extruct(html, without_token_url).get('price'), 10.0)

    def test_null_main_with_priced_recommendation_does_not_guess(self):
        html = """<html><head>
        <meta property="og:price:amount" content="15" />
        </head><body>
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

        url = 'https://shop.example/product/foo'
        value = extract(html, url)
        self.assertIsNone(value.get('price'))
        self.assertIsNone(pure(html, url).get('price'))
        self.assertIsNone(extruct(html, url).get('price'))

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

        url = 'https://shop.example/product/main-MAIN123.html'
        value = extract(html, url)
        self.assertIsNone(value.get('price'))
        self.assertEqual(value.get('availability'), 'outofstock')
        self.assertIsNone(pure(html, url).get('price'))
        self.assertEqual(extruct(html, url).get('availability'), 'outofstock')

    def test_same_name_microdata_without_url_token_does_not_fill_main(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Same Name",
         "sku": "MAIN123", "offers": {"@type": "Offer", "price": "null",
         "availability": "http://schema.org/OutOfStock"}}
        </script>
        <div itemscope itemtype="http://schema.org/Product">
          <span itemprop="name">Same Name</span>
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="price" content="99.9" />
            <link itemprop="availability" href="http://schema.org/InStock" />
          </div>
        </div>
        <div itemscope itemtype="http://schema.org/Product">
          <span itemprop="name">Same Name</span>
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="price" content="88.8" />
            <link itemprop="availability" href="http://schema.org/InStock" />
          </div>
        </div>
        </body></html>"""

        url = 'https://shop.example/collection/same-name.html'
        value = extract(html, url)
        self.assertIsNone(value.get('price'))
        self.assertEqual(value.get('availability'), 'outofstock')
        self.assertIsNone(pure(html, url).get('price'))
        self.assertIsNone(extruct(html, url).get('price'))

    def test_single_different_name_microdata_does_not_fill_main_availability(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Main Product",
         "sku": "MAIN123", "offers": {"@type": "Offer", "price": "null"}}
        </script>
        <div itemscope itemtype="http://schema.org/Product">
          <span itemprop="name">Carousel Product</span>
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="price" content="99.9" />
            <link itemprop="availability" href="http://schema.org/InStock" />
          </div>
        </div>
        </body></html>"""

        url = 'https://shop.example/collection/spring.html'
        value = extract(html, url)
        self.assertIsNone(value.get('price'))
        self.assertIsNone(value.get('availability'))
        self.assertIsNone(pure(html, url).get('price'))
        self.assertIsNone(extruct(html, url).get('availability'))

    def test_watch_url_product_keys(self):
        from changedetectionio.processors.restock_diff.pure_python_extractor import watch_url_product_keys

        keys = watch_url_product_keys(
            'https://shop.example/product/foo?collection=999999&sized=888888&color=123456&pid=MAIN123')
        self.assertIn('main123', keys)
        self.assertNotIn('123456', keys)
        self.assertNotIn('999999', keys)
        self.assertNotIn('888888', keys)

        keys = watch_url_product_keys(
            'https://shop.example/product/foo-BO1294C.HTML?dwvar_BO1294C_Z_COL_INT=9887')
        self.assertIn('bo1294c', keys)
        self.assertNotIn('9887', keys)

        keys = watch_url_product_keys(
            'https://yourkaya.pl/products/more-collagen?variant=58522834239823')
        self.assertIn('58522834239823', keys)

        keys = watch_url_product_keys(
            'https://shop.example/product/main-MAIN123.html?variant=58522834239823')
        self.assertIn('main123', keys)
        self.assertNotIn('58522834239823', keys)

        self.assertIn('abc123', watch_url_product_keys('https://shop.example/product/abc123/'))
        self.assertIn('abc123', watch_url_product_keys('https://shop.example/product/abc123/reviews'))

    def test_variant_id_selects_product_when_path_has_no_code(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Other",
         "productID": "999999", "offers": {"@type": "Offer", "price": "99",
         "priceCurrency": "PLN", "availability": "http://schema.org/InStock"}}
        </script>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Main",
         "productID": "58522834239823", "offers": {"@type": "Offer", "price": "10",
         "priceCurrency": "PLN", "availability": "http://schema.org/InStock"}}
        </script>
        </body></html>"""

        url = 'https://yourkaya.pl/products/more-collagen?variant=58522834239823'
        self.assertEqual(extract(html, url).get('price'), 10.0)
        self.assertEqual(pure(html, url).get('price'), 10.0)
        self.assertEqual(extruct(html, url).get('price'), 10.0)

    def test_color_value_does_not_select_another_product(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Other",
         "sku": "9887", "offers": {"@type": "Offer", "price": "99",
         "priceCurrency": "PLN", "availability": "http://schema.org/InStock"}}
        </script>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Main",
         "sku": "MAIN123", "offers": {"@type": "Offer", "price": "10",
         "priceCurrency": "PLN", "availability": "http://schema.org/InStock"}}
        </script>
        </body></html>"""

        url = 'https://shop.example/product/main-MAIN123.html?color=9887'
        self.assertEqual(extract(html, url).get('price'), 10.0)
        self.assertEqual(pure(html, url).get('price'), 10.0)
        self.assertEqual(extruct(html, url).get('price'), 10.0)

    def test_short_numeric_sku_matches_full_token_only(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Decoy",
         "sku": "13230", "offers": {"@type": "Offer", "price": "99",
         "priceCurrency": "PLN", "availability": "http://schema.org/InStock"}}
        </script>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Main Bag",
         "sku": 3230, "offers": {"@type": "Offer", "price": "10",
         "priceCurrency": "PLN", "availability": "http://schema.org/InStock"}}
        </script>
        </body></html>"""

        url = 'https://shop.example/pl/torby/3230-sicily-bag.html'
        value = extract(html, url)
        self.assertEqual(value.get('price'), 10.0)
        self.assertEqual(pure(html, url).get('price'), 10.0)
        self.assertEqual(extruct(html, url).get('price'), 10.0)

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

        url = 'https://shop.example/product/main-blouse-CL1294P.html'
        value = extract(html, url)
        self.assertEqual(value.get('price'), 189.9)
        self.assertEqual(pure(html, url).get('price'), 189.9)
        self.assertEqual(extruct(html, url).get('price'), 189.9)

    def test_product_code_matching_uses_last_url_path_segment(self):
        main = """<html><body><script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Main",
         "@id": "https://shop.example/12345/product/main-MAIN123.html?color=9887",
         "offers": {"@type": "Offer", "price": "10", "priceCurrency": "PLN",
                    "availability": "http://schema.org/InStock"}}
        </script>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Recommendation",
         "offers": {"@type": "Offer", "price": "99", "priceCurrency": "PLN",
                    "availability": "http://schema.org/InStock"}}
        </script></body></html>"""

        url = 'https://shop.example/product/main-MAIN123.html'
        self.assertEqual(extract(main, url).get('price'), 10.0)
        self.assertEqual(pure(main, url).get('price'), 10.0)
        self.assertEqual(extruct(main, url).get('price'), 10.0)

        # A different product under the same numeric category prefix must not match.
        other = main.replace('"name": "Main",', '"name": "Other",').replace(
            'main-MAIN123.html', 'other-OTHER999.html')
        self.assertIsNone(pure(other, url).get('price'))
        with self.assertRaises(restock_diff.MoreThanOnePriceFound):
            extract(other, url)
        with self.assertRaises(restock_diff.MoreThanOnePriceFound):
            extruct(other, url)

        # A single top-level product is still the only candidate.
        sku_only = """<html><body><script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Other",
         "sku": "OTHER999", "offers": {"@type": "Offer", "price": "10"}}
        </script></body></html>"""
        self.assertEqual(extract(sku_only, url).get('price'), 10.0)

        # GTIN/MPN never appear in URLs, but they do not contradict one either.
        gtin_only = sku_only.replace('"sku": "OTHER999"', '"gtin13": "1234567890123"')
        self.assertEqual(extract(gtin_only, url).get('price'), 10.0)

    def test_tied_url_match_is_refused(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Red",
         "sku": "MAIN123-RED", "offers": {"@type": "Offer", "price": "10"}}
        </script>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Blue",
         "sku": "MAIN123-BLUE", "offers": {"@type": "Offer", "price": "20"}}
        </script>
        </body></html>"""

        url = 'https://shop.example/product/main-MAIN123.html'
        self.assertEqual(pure(html, url), {})
        with self.assertRaises(restock_diff.MoreThanOnePriceFound):
            extract(html, url)
        with self.assertRaises(restock_diff.MoreThanOnePriceFound):
            extruct(html, url)

    def test_explicit_sku_wins_over_url_only_tie(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Dress",
         "sku": "44132-99X", "offers": {"@type": "Offer", "price": "74.9",
          "priceCurrency": "PLN", "availability": "http://schema.org/OutOfStock"}}
        </script>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Dress",
         "@id": "https://shop.example/44132-99X",
         "gtin13": "5903972700778", "offers": {"@type": "Offer", "price": "249.9",
          "priceCurrency": "PLN", "availability": "http://schema.org/InStock"}}
        </script>
        </body></html>"""

        url = 'https://shop.example/sukienka-welly-44132-99X'
        value = extract(html, url)
        self.assertEqual(value.get('price'), 74.9)
        self.assertEqual(value.get('availability'), 'outofstock')

    def test_microdata_tie_is_refused(self):
        html = """<html><body>
        <div itemscope itemtype="http://schema.org/Product">
          <span itemprop="name">Carousel</span>
          <meta itemprop="sku" content="ABC123-X" />
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="price" content="99" />
          </div>
        </div>
        <div itemscope itemtype="http://schema.org/Product">
          <span itemprop="name">Main</span>
          <meta itemprop="sku" content="ABC123" />
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="price" content="10" />
          </div>
        </div>
        </body></html>"""

        url = 'https://shop.example/product/ABC123'
        self.assertEqual(pure(html, url), {})
        with self.assertRaises(restock_diff.MoreThanOnePriceFound):
            extract(html, url)
        with self.assertRaises(restock_diff.MoreThanOnePriceFound):
            extruct(html, url)

    def test_single_microdata_main_accepts_longer_name(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Main Product",
         "sku": "MAIN123", "offers": {"@type": "Offer", "priceCurrency": "PLN"}}
        </script>
        <div itemscope itemtype="http://schema.org/Product">
          <span itemprop="name">Main Product - Red, Size M</span>
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="price" content="10" />
          </div>
        </div>
        </body></html>"""

        url = 'https://shop.example/product/main-MAIN123.html'
        self.assertEqual(extract(html, url).get('price'), 10.0)
        self.assertEqual(pure(html, url).get('price'), 10.0)

    def test_alphabetic_skus_are_refused_not_guessed(self):
        # shortcut: digit-bearing codes only; this documents the current limit.
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Black",
         "sku": "BLACK-DRESS", "offers": {"@type": "Offer", "price": "10",
         "priceCurrency": "PLN", "availability": "http://schema.org/InStock"}}
        </script>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Red",
         "sku": "RED-DRESS", "offers": {"@type": "Offer", "price": "20",
         "priceCurrency": "PLN", "availability": "http://schema.org/InStock"}}
        </script>
        </body></html>"""

        url = 'https://shop.example/product/black-dress'
        self.assertEqual(pure(html, url), {})
        with self.assertRaises(restock_diff.MoreThanOnePriceFound):
            extract(html, url)
        with self.assertRaises(restock_diff.MoreThanOnePriceFound):
            extruct(html, url)

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
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Broken Price",
         "sku": "BROKEN123", "offers": {"@type": "Offer", "price": NaN,
         "priceCurrency": "PLN", "availability": "http://schema.org/InStock"}}
        </script>
        </body></html>"""

        url = 'https://shop.example/product/broken-BROKEN123.html'
        value = extract(html, url)
        self.assertIsNone(value.get('price'))
        self.assertIsNone(pure(html, url).get('price'))

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

        url = 'https://kazar.com/leather-boots-93220-01-00.html'
        value = extract(html, url)
        self.assertEqual(value.get('price'), 1299.0)
        self.assertEqual(value.get('currency'), 'PLN')
        self.assertIsNone(value.get('availability'))
        self.assertEqual(pure(html, url).get('price'), 1299.0)
        self.assertEqual(extruct(html, url).get('price'), 1299.0)

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

        url = 'https://esotiq.com/pl/pl/dress-black-44132-99X'
        value = extract(html, url)
        self.assertEqual(value.get('price'), 74.9)
        self.assertEqual(value.get('availability'), 'outofstock')
        self.assertEqual(pure(html, url).get('price'), 74.9)
        self.assertEqual(extruct(html, url).get('price'), 74.9)

    def test_graph_product_selection(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@graph": [
          {"@type": "Product", "name": "Main Product", "sku": "MAIN123",
           "offers": {"@type": "Offer", "price": "10", "priceCurrency": "PLN",
                      "availability": "http://schema.org/InStock"}},
          {"@type": "ItemList", "itemListElement": [
            {"@type": "Product", "name": "Recommendation",
             "offers": {"@type": "Offer", "price": "99", "priceCurrency": "PLN",
                        "availability": "http://schema.org/InStock"}}
          ]}
        ]}
        </script>
        </body></html>"""

        url = 'https://shop.example/product/main-MAIN123.html'
        self.assertEqual(extract(html, url).get('price'), 10.0)
        self.assertEqual(pure(html, url).get('price'), 10.0)
        self.assertEqual(extruct(html, url).get('price'), 10.0)

    def test_safe_float_and_as_price_edges(self):
        from changedetectionio.processors.restock_diff.processor import _safe_float
        from changedetectionio.processors.restock_diff.pure_python_extractor import _as_price

        self.assertEqual(_safe_float('12.5'), 12.5)
        self.assertIsNone(_safe_float('nan'))
        self.assertIsNone(_safe_float('inf'))
        self.assertIsNone(_safe_float(True))
        self.assertEqual(_as_price('12.5'), 12.5)
        self.assertIsNone(_as_price(float('nan')))
        self.assertIsNone(_as_price(float('inf')))
        self.assertIsNone(_as_price(True))
        self.assertIsNone(_as_price(['10', '20']))
        self.assertIsNone(_as_price({'price': '10'}))
        self.assertIsNone(_as_price(('10',)))
        self.assertIsNone(_as_price({10}))

    def test_restock_normalizes_missing_currency_and_availability(self):
        from changedetectionio.processors.restock_diff import Restock

        missing = Restock({'currency': 'null', 'availability': 'n/a'})
        self.assertIsNone(missing.get('currency'))
        self.assertIsNone(missing.get('availability'))

        present = Restock({'currency': 'PLN', 'availability': 'InStock'})
        self.assertEqual(present.get('currency'), 'PLN')
        self.assertEqual(present.get('availability'), 'InStock')

        self.assertIsNone(Restock({'price': True, 'last_price': False}).get('price'))
        self.assertIsNone(Restock({'last_price': False}).get('last_price'))
        for bad in ('--', '.', '-', '1.2.3'):
            self.assertIsNone(Restock({'price': bad}).get('price'))
        self.assertIsNone(Restock({'price': '9' * 400}).get('price'))
        for bad in (float('nan'), float('inf'), [10], {'price': 10}):
            self.assertIsNone(Restock({'price': bad}).get('price'))
        for bad in ('--', '1.2.3'):
            self.assertIsNone(Restock().parse_currency(bad))
        self.assertIsNone(Restock().parse_currency('9' * 400))
        bad_fields = Restock({'currency': ['PLN'], 'availability': ['http://schema.org/InStock']})
        self.assertIsNone(bad_fields.get('currency'))
        self.assertIsNone(bad_fields.get('availability'))

    def test_product_level_fields_without_offers(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Direct Fields",
         "sku": "DIRECT123", "price": "12.5", "priceCurrency": "USD",
         "availability": "http://schema.org/InStock"}
        </script>
        </body></html>"""

        url = 'https://shop.example/product/direct-DIRECT123.html'
        self.assertEqual(extract(html, url).get('price'), 12.5)
        self.assertEqual(pure(html, url).get('currency'), 'USD')
        self.assertEqual(extruct(html, url).get('availability'), 'instock')

    def test_aggregate_offer_low_price(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Aggregate",
         "sku": "AGG123", "offers": {"@type": "AggregateOffer", "lowPrice": "10",
         "offerCount": 1, "priceCurrency": "PLN",
         "availability": "http://schema.org/InStock"}}
        </script>
        </body></html>"""

        url = 'https://shop.example/product/aggregate-AGG123.html'
        self.assertEqual(extract(html, url).get('price'), 10.0)
        self.assertEqual(pure(html, url).get('price'), 10.0)
        self.assertEqual(extruct(html, url).get('price'), 10.0)

        multi = html.replace('"offerCount": 1', '"offerCount": 2')
        self.assertIsNone(pure(multi, url).get('price'))
        self.assertIsNone(extruct(multi, url).get('price'))

    def test_og_currency_fallbacks(self):
        from changedetectionio.processors.restock_diff.pure_python_extractor import og_currency

        self.assertEqual(og_currency({'product:sale_price:currency': 'EUR'}), 'EUR')
        self.assertEqual(og_currency({'product:price:currency': 'PLN'}), 'PLN')
        self.assertEqual(og_currency({'og:price:currency': 'USD'}), 'USD')
        self.assertIsNone(og_currency({'product:price:currency': 'null'}))

    def test_no_product_currency_conflict_is_not_overridden_by_opengraph(self):
        html = """<html><head>
        <meta property="product:availability" content="in stock" />
        <meta property="product:price:currency" content="PLN" />
        </head><body>
        <div itemscope itemtype="http://schema.org/Offer">
          <meta itemprop="priceCurrency" content="EUR" />
        </div>
        <div itemscope itemtype="http://schema.org/Offer">
          <meta itemprop="priceCurrency" content="USD" />
        </div>
        </body></html>"""

        url = 'https://shop.example/product/main-MAIN123.html'
        self.assertIsNone(extract(html, url).get('currency'))
        self.assertIsNone(pure(html, url).get('currency'))
        self.assertIsNone(extruct(html, url).get('currency'))

    def test_no_product_microdata_price_beats_opengraph(self):
        html = """<html><head>
        <meta property="og:price:amount" content="20" />
        <meta property="og:price:currency" content="PLN" />
        </head><body>
        <div itemscope itemtype="http://schema.org/Offer">
          <meta itemprop="price" content="10" />
          <meta itemprop="priceCurrency" content="PLN" />
        </div>
        </body></html>"""

        url = 'https://shop.example/product/main-MAIN123.html'
        self.assertEqual(extract(html, url).get('price'), 10.0)
        self.assertEqual(pure(html, url).get('price'), 10.0)
        self.assertEqual(extruct(html, url).get('price'), 10.0)

    def test_currency_comparison_is_case_insensitive(self):
        html = """<html><head>
        <meta property="product:price:currency" content="pln" />
        </head><body>
        <div itemscope itemtype="http://schema.org/Offer">
          <meta itemprop="price" content="10" />
          <meta itemprop="priceCurrency" content="PLN" />
        </div>
        </body></html>"""

        url = 'https://shop.example/product/main-MAIN123.html'
        self.assertEqual(extract(html, url).get('currency'), 'PLN')
        self.assertEqual(pure(html, url).get('currency'), 'PLN')
        self.assertEqual(extruct(html, url).get('currency'), 'PLN')

        list_currency = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Main",
         "sku": "MAIN123", "offers": {"@type": "Offer", "price": "10",
          "priceCurrency": ["PLN", "pln"]}}
        </script>
        </body></html>"""
        self.assertEqual(extract(list_currency, url).get('currency'), 'PLN')

    def test_extruct_og_flat_handles_dict_and_list_properties(self):
        from changedetectionio.processors.restock_diff.processor import _extruct_og_flat

        list_form = _extruct_og_flat({'opengraph': [{'properties': [
            ['bad'], ['product:price:amount', '10'], ['product:price:currency', 'PLN']]}]})
        self.assertEqual(list_form['product:price:amount'], '10')
        self.assertEqual(list_form['product:price:currency'], 'PLN')

        dict_form = _extruct_og_flat({'opengraph': [{'properties': {
            'og:price:amount': '20', 'og:price:currency': 'EUR'}}]})
        self.assertEqual(dict_form['og:price:amount'], '20')
        self.assertEqual(dict_form['og:price:currency'], 'EUR')

    def test_product_jsonld_og_fills_missing_currency_and_availability(self):
        html = """<html><head>
        <meta property="product:price:currency" content="PLN" />
        <meta property="product:availability" content="in stock" />
        </head><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "OG Fill",
         "sku": "OGFILL123", "offers": {"@type": "Offer", "price": "10"}}
        </script>
        </body></html>"""

        url = 'https://shop.example/product/og-fill-OGFILL123.html'
        value = extract(html, url)
        self.assertEqual(value.get('price'), 10.0)
        self.assertEqual(value.get('currency'), 'PLN')
        self.assertEqual(value.get('availability'), 'instock')
        self.assertEqual(pure(html, url).get('currency'), 'PLN')
        self.assertEqual(extruct(html, url).get('availability'), 'instock')

    def test_product_microdata_beats_opengraph_for_currency_and_stock(self):
        html = """<html><head>
        <meta property="product:availability" content="in stock" />
        <meta property="product:price:currency" content="EUR" />
        </head><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Main Product",
         "sku": "MAIN123", "offers": {"@type": "Offer", "price": "10"}}
        </script>
        <div itemscope itemtype="http://schema.org/Product"
             itemid="https://shop.example/product/main-MAIN123.html">
          <span itemprop="name">Main Product</span>
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="price" content="10" />
            <meta itemprop="priceCurrency" content="PLN" />
            <link itemprop="availability" href="http://schema.org/OutOfStock" />
          </div>
        </div>
        </body></html>"""

        url = 'https://shop.example/product/main-MAIN123.html'
        value = extract(html, url)
        self.assertEqual(value.get('price'), 10.0)
        self.assertEqual(value.get('currency'), 'PLN')
        self.assertEqual(value.get('availability'), 'outofstock')
        self.assertEqual(pure(html, url).get('availability'), 'outofstock')
        self.assertEqual(extruct(html, url).get('availability'), 'outofstock')

    def test_ambiguous_microdata_does_not_get_opengraph_stock(self):
        html = """<html><head>
        <meta property="product:availability" content="in stock" />
        </head><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Same Name",
         "sku": "MAIN123", "offers": {"@type": "Offer", "price": "10"}}
        </script>
        <div itemscope itemtype="http://schema.org/Product">
          <span itemprop="name">Same Name</span>
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="price" content="99" />
            <link itemprop="availability" href="http://schema.org/InStock" />
          </div>
        </div>
        <div itemscope itemtype="http://schema.org/Product">
          <span itemprop="name">Same Name</span>
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="price" content="88" />
            <link itemprop="availability" href="http://schema.org/InStock" />
          </div>
        </div>
        </body></html>"""

        url = 'https://shop.example/collection/same-name.html'
        value = extract(html, url)
        self.assertEqual(value.get('price'), 10.0)
        self.assertIsNone(value.get('availability'))
        self.assertIsNone(pure(html, url).get('availability'))
        self.assertIsNone(extruct(html, url).get('availability'))

    def test_url_matched_main_still_uses_opengraph_with_stray_microdata(self):
        html = """<html><head>
        <meta property="product:availability" content="in stock" />
        <meta property="product:price:currency" content="USD" />
        </head><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Main",
         "sku": "AAA111", "offers": {"@type": "Offer", "price": "100"}}
        </script>
        <div itemscope itemtype="http://schema.org/Product">
          <span itemprop="name">Stray</span>
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="price" content="999" />
          </div>
        </div>
        </body></html>"""

        url = 'https://shop.example/item-AAA111'
        value = extract(html, url)
        self.assertEqual(value.get('price'), 100.0)
        self.assertEqual(value.get('currency'), 'USD')
        self.assertEqual(value.get('availability'), 'instock')
        self.assertEqual(pure(html, url).get('currency'), 'USD')
        self.assertEqual(extruct(html, url).get('availability'), 'instock')

    def test_product_id_microdata_confirms_product(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Main Product",
         "sku": "MAIN123", "offers": {"@type": "Offer", "price": "10"}}
        </script>
        <div itemscope itemtype="http://schema.org/Product">
          <span itemprop="name">Main Product</span>
          <meta itemprop="productID" content="MAIN123" />
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="price" content="10" />
            <link itemprop="availability" href="http://schema.org/OutOfStock" />
          </div>
        </div>
        </body></html>"""

        url = 'https://shop.example/product/main-MAIN123.html'
        value = extract(html, url)
        self.assertEqual(value.get('price'), 10.0)
        self.assertEqual(value.get('availability'), 'outofstock')
        self.assertEqual(pure(html, url).get('availability'), 'outofstock')
        self.assertEqual(extruct(html, url).get('availability'), 'outofstock')

    def test_explicit_null_in_price_specification_or_variant_blocks_microdata_price(self):
        microdata = """
        <div itemscope itemtype="http://schema.org/Product"
             itemid="https://shop.example/product/main-MAIN123.html">
          <span itemprop="name">Main Product</span>
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="price" content="10" />
          </div>
        </div>"""
        specification = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Main Product",
         "sku": "MAIN123", "offers": {"@type": "Offer",
          "priceSpecification": {"@type": "PriceSpecification", "price": "null"}}}
        </script>""" + microdata + "</body></html>"
        variant = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "ProductGroup", "name": "Main Product",
         "productGroupID": "MAIN123",
         "hasVariant": [{"@type": "Product", "sku": "MAIN123-1", "price": "null"}]}
        </script>""" + microdata + "</body></html>"
        variant_specification = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "ProductGroup", "name": "Main Product",
         "productGroupID": "MAIN123",
         "hasVariant": [{"@type": "Product", "sku": "MAIN123-1",
          "priceSpecification": {"@type": "PriceSpecification", "price": "null"}}]}
        </script>""" + microdata + "</body></html>"

        url = 'https://shop.example/product/main-MAIN123.html'
        self.assertIsNone(extract(specification, url).get('price'))
        self.assertIsNone(extract(variant, url).get('price'))
        self.assertIsNone(extract(variant_specification, url).get('price'))

    def test_listed_microdata_price_uses_unique_value(self):
        html = """<html><body>
        <div itemscope itemtype="http://schema.org/Product">
          <meta itemprop="price" content="10" />
          <span itemprop="price">$10</span>
        </div>
        </body></html>"""

        url = 'https://shop.example/product/main-MAIN123.html'
        self.assertEqual(extract(html, url).get('price'), 10.0)
        self.assertEqual(pure(html, url).get('price'), 10.0)
        self.assertEqual(extruct(html, url).get('price'), 10.0)

    def test_microdata_price_fills_missing_jsonld_price_but_not_explicit_null(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Main Product",
         "sku": "MAIN123", "offers": {"@type": "Offer", "priceCurrency": "PLN"}}
        </script>
        <div itemscope itemtype="http://schema.org/Product"
             itemid="https://shop.example/product/main-MAIN123.html">
          <span itemprop="name">Main Product</span>
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="price" content="10" />
            <meta itemprop="priceCurrency" content="PLN" />
            <link itemprop="availability" href="http://schema.org/InStock" />
          </div>
        </div>
        </body></html>"""

        url = 'https://shop.example/product/main-MAIN123.html'
        self.assertEqual(extract(html, url).get('price'), 10.0)
        self.assertEqual(extruct(html, url).get('price'), 10.0)
        self.assertEqual(pure(html, url).get('price'), 10.0)

        explicit_null = html.replace(
            '"priceCurrency": "PLN"', '"price": "null", "priceCurrency": "PLN"', 1)
        self.assertIsNone(extract(explicit_null, url).get('price'))

    def test_opengraph_price_fills_missing_jsonld_price(self):
        html = """<html><head>
        <meta property="og:price:amount" content="49.99" />
        <meta property="og:price:currency" content="PLN" />
        </head><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Main Product",
         "sku": "MAIN123", "offers": {"@type": "Offer", "priceCurrency": "PLN",
          "availability": "http://schema.org/InStock"}}
        </script>
        </body></html>"""

        url = 'https://shop.example/product/main-MAIN123.html'
        self.assertEqual(extract(html, url).get('price'), 49.99)
        self.assertEqual(pure(html, url).get('price'), 49.99)
        self.assertEqual(extruct(html, url).get('price'), 49.99)

        explicit_null = html.replace(
            '"priceCurrency": "PLN"', '"price": "null", "priceCurrency": "PLN"', 1)
        self.assertIsNone(extract(explicit_null, url).get('price'))
        self.assertIsNone(pure(explicit_null, url).get('price'))

    def test_bare_offer_is_ignored_when_product_microdata_exists(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Main",
         "sku": "MAIN123", "offers": {"@type": "Offer", "priceCurrency": "PLN"}}
        </script>
        <div itemscope itemtype="http://schema.org/Product">
          <span itemprop="name">Stray</span>
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="price" content="999" />
          </div>
        </div>
        <div itemscope itemtype="http://schema.org/Offer">
          <meta itemprop="price" content="42" />
        </div>
        </body></html>"""

        url = 'https://shop.example/product/main-MAIN123.html'
        self.assertIsNone(extract(html, url).get('price'))

    def test_bare_offer_jsonld_is_used(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Offer", "price": "77",
         "priceCurrency": "PLN", "availability": "http://schema.org/InStock"}
        </script>
        </body></html>"""

        url = 'https://shop.example/product/anything'
        self.assertEqual(extract(html, url).get('price'), 77.0)
        self.assertEqual(pure(html, url).get('price'), 77.0)
        self.assertEqual(extruct(html, url).get('price'), 77.0)

    def test_conflicting_microdata_prices_raise(self):
        html = """<html><body>
        <div itemscope itemtype="http://schema.org/Product">
          <meta itemprop="price" content="10" />
          <meta itemprop="price" content="20" />
        </div>
        </body></html>"""

        url = 'https://shop.example/product/main-MAIN123.html'
        self.assertIsNone(pure(html, url).get('price'))
        with self.assertRaises(restock_diff.MoreThanOnePriceFound):
            extract(html, url)
        with self.assertRaises(restock_diff.MoreThanOnePriceFound):
            extruct(html, url)

    def test_bare_offer_microdata_fills_missing_jsonld_price(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Main Product",
         "sku": "MAIN123", "offers": {"@type": "Offer", "priceCurrency": "PLN",
          "availability": "http://schema.org/InStock"}}
        </script>
        <div itemscope itemtype="http://schema.org/Offer">
          <meta itemprop="price" content="42" />
        </div>
        </body></html>"""

        url = 'https://shop.example/product/main-MAIN123.html'
        self.assertEqual(extract(html, url).get('price'), 42.0)
        self.assertEqual(pure(html, url).get('price'), 42.0)
        self.assertEqual(extruct(html, url).get('price'), 42.0)

    def test_combine_fills_currency_and_availability(self):
        from changedetectionio.processors.restock_diff import Restock
        from changedetectionio.processors.restock_diff.processor import _combine_with_pure_partial

        merged = _combine_with_pure_partial(
            {'price': 10.0},
            Restock({'currency': 'PLN', 'availability': 'instock'}),
            scoped=True, pure_had_main=True, fetched_had_main=True)
        self.assertEqual(merged.get('price'), 10.0)
        self.assertEqual(merged.get('currency'), 'PLN')
        self.assertEqual(merged.get('availability'), 'instock')

        # Both engines selected a main product, so the fetched price is
        # main-derived and may fill a gap in the pure result.
        merged = _combine_with_pure_partial(
            {'availability': 'outofstock'},
            Restock({'price': 99.9, 'availability': 'outofstock'}),
            scoped=True, pure_had_main=True, fetched_had_main=True)
        self.assertEqual(merged.get('price'), 99.9)
        self.assertEqual(merged.get('availability'), 'outofstock')

    def test_og_sale_price_precedence_without_jsonld_or_microdata(self):
        html = """<html><head>
        <meta property="product:price:amount" content="74.90" />
        <meta property="product:sale_price:amount" content="59.90" />
        <meta property="product:original_price:amount" content="249.90" />
        <meta property="product:min_30d_price:amount" content="124.90" />
        <meta property="product:price:currency" content="PLN" />
        </head><body></body></html>"""

        url = 'https://shop.example/product/og-only-OG123.html'
        self.assertEqual(extract(html, url).get('price'), 59.9)
        self.assertEqual(pure(html, url).get('price'), 59.9)
        self.assertEqual(extruct(html, url).get('price'), 59.9)

    def test_worker_passes_url_and_returns_scoped_result(self):
        import json
        from multiprocessing import Pipe

        from changedetectionio.processors.restock_diff.processor import (
            _extract_itemprop_availability_worker,
        )

        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Main Product",
         "sku": "MAIN123", "offers": {"@type": "Offer", "price": "10",
         "priceCurrency": "PLN", "availability": "http://schema.org/InStock"}}
        </script>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Recommendation",
         "offers": {"@type": "Offer", "price": "99", "priceCurrency": "PLN",
         "availability": "http://schema.org/InStock"}}
        </script>
        </body></html>"""

        parent, child = Pipe()
        parent.send_bytes(html.encode('utf-8'))
        _extract_itemprop_availability_worker(
            child, 'https://shop.example/product/main-MAIN123.html')
        result = json.loads(parent.recv_bytes().decode('utf-8'))

        self.assertTrue(result['success'])
        self.assertEqual(result['data']['price'], 10.0)
        self.assertTrue(result['had_main'])

    def test_product_group_variant_disagreement_uses_first(self):
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

        url = 'https://shop.example/product/main-SKU123.html'
        value = extract(html, url)
        self.assertEqual(value.get('price'), 10.0)
        self.assertEqual(value.get('availability'), 'instock')
        self.assertEqual(pure(html, url).get('price'), 10.0)
        self.assertEqual(extruct(html, url).get('price'), 10.0)

    def test_zero_price_is_valid(self):
        html = """<html><body>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@type": "Product", "name": "Free Sample",
         "sku": "ZERO123", "offers": {"@type": "Offer", "price": 0,
         "priceCurrency": "USD", "availability": "http://schema.org/InStock"}}
        </script>
        </body></html>"""

        url = 'https://shop.example/product/free-ZERO123.html'
        value = extract(html, url)
        self.assertEqual(value.get('price'), 0.0)
        self.assertEqual(value.get('availability'), 'instock')
        self.assertEqual(pure(html, url).get('price'), 0.0)
        self.assertEqual(extruct(html, url).get('price'), 0.0)

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

        url = 'https://shop.example/product/widget-WIDGET123.html'
        value = extract(html, url)
        self.assertEqual(value.get('price'), 155.55)
        self.assertEqual(value.get('currency'), 'USD')
        self.assertEqual(pure(html, url).get('currency'), 'USD')
        self.assertEqual(extruct(html, url).get('currency'), 'USD')

    def test_no_product_jsonld_availability_only_is_kept(self):
        html = """<html><body>
        <div itemscope itemtype="http://schema.org/Product">
          <span itemprop="name">Main Product</span>
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="availability" content="OutOfStock" />
          </div>
        </div>
        </body></html>"""

        url = 'https://shop.example/product/main-93220-01-00.html'
        value = extract(html, url)
        self.assertIsNone(value.get('price'))
        self.assertEqual(value.get('availability'), 'outofstock')
        self.assertEqual(pure(html, url).get('availability'), 'outofstock')
        self.assertEqual(extruct(html, url).get('availability'), 'outofstock')

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

        url = 'https://shop.example/product/main-93220-01-00.html'
        self.assertEqual(pure(html, url), {})
        with self.assertRaises(restock_diff.MoreThanOnePriceFound):
            extract(html, url)

    def test_single_mismatched_microdata_product_is_refused(self):
        html = """<html><body>
        <div itemscope itemtype="http://schema.org/Product"
             itemid="https://shop.example/product/other-OTHER999.html">
          <span itemprop="name">Other Product</span>
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="price" content="99" />
            <link itemprop="availability" href="http://schema.org/InStock" />
          </div>
        </div>
        </body></html>"""

        url = 'https://shop.example/product/main-MAIN123.html'
        self.assertIsNone(extract(html, url).get('price'))
        self.assertIsNone(pure(html, url).get('price'))
        self.assertIsNone(extruct(html, url).get('price'))

    def test_multiple_microdata_products_use_url_match(self):
        html = """<html><body>
        <div itemscope itemtype="http://schema.org/Product"
             itemid="https://shop.example/product/other-OTHER999.html">
          <span itemprop="name">Other Product</span>
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="price" content="99" />
            <link itemprop="availability" href="http://schema.org/InStock" />
          </div>
        </div>
        <div itemscope itemtype="http://schema.org/Product"
             itemid="https://shop.example/product/main-MAIN123.html">
          <span itemprop="name">Main Product</span>
          <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
            <meta itemprop="price" content="10" />
            <link itemprop="availability" href="http://schema.org/InStock" />
          </div>
        </div>
        </body></html>"""

        url = 'https://shop.example/product/main-MAIN123.html'
        self.assertEqual(extract(html, url).get('price'), 10.0)
        self.assertEqual(extruct(html, url).get('price'), 10.0)
        self.assertEqual(pure(html, url), {})


if __name__ == '__main__':
    unittest.main()
