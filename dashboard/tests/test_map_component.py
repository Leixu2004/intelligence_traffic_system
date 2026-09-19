import unittest

import pandas as pd

from dashboard.map_component import render_amap_html


class AMapComponentTests(unittest.TestCase):
    def test_amap_key_is_embedded_as_a_url_value(self):
        html = render_amap_html(pd.DataFrame(), "TESTKEY", "TESTSEC")

        self.assertIn("&key=TESTKEY&", html)
        self.assertNotIn('&key="TESTKEY"', html)
        self.assertIn('securityJsCode: "TESTSEC"', html)


if __name__ == "__main__":
    unittest.main()
