from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from common.currencies import currency_for_country, decimals_for, quantize
from common import fx


class CurrencyDataTests(TestCase):
    def test_country_to_currency(self):
        self.assertEqual(currency_for_country("Nigeria"), "NGN")
        self.assertEqual(currency_for_country("Ghana"), "GHS")
        self.assertEqual(currency_for_country("Togo"), "XOF")
        self.assertEqual(currency_for_country("Cameroon"), "XAF")
        self.assertEqual(currency_for_country("Uganda"), "UGX")
        self.assertEqual(currency_for_country("KE"), "KES")
        self.assertEqual(currency_for_country("Mars"), "NGN")  # fallback

    def test_decimals(self):
        self.assertEqual(decimals_for("NGN"), 2)
        self.assertEqual(decimals_for("XOF"), 0)
        self.assertEqual(decimals_for("UGX"), 0)

    def test_quantize_rounds_to_currency_precision(self):
        # 2-dp currency
        self.assertEqual(quantize("100.555", "NGN"), Decimal("100.56"))
        # whole-unit currencies must NOT keep cents (HALF_UP)
        self.assertEqual(quantize("100.40", "XOF"), Decimal("100"))
        self.assertEqual(quantize("100.55", "XOF"), Decimal("101"))
        self.assertEqual(quantize("100.5", "XOF"), Decimal("101"))
        self.assertEqual(quantize("2500.49", "UGX"), Decimal("2500"))


class FxTests(TestCase):
    def test_same_currency_is_identity(self):
        self.assertEqual(fx.get_rate("NGN", "NGN"), Decimal("1"))
        self.assertEqual(fx.convert("1000.00", "NGN", "NGN"), Decimal("1000.00"))

    @patch.object(fx, "get_rate", return_value=Decimal("0.045"))
    def test_convert_rounds_to_target(self, _rate):
        # 10000 NGN * 0.045 = 450 GHS (2dp)
        self.assertEqual(fx.convert("10000", "NGN", "GHS"), Decimal("450.00"))

    @patch.object(fx, "get_rate", return_value=Decimal("2.6"))
    def test_convert_whole_unit_target_has_no_cents(self, _rate):
        # 1000 NGN * 2.6 = 2600 XOF (0dp, stays whole)
        self.assertEqual(fx.convert("1000", "NGN", "XOF"), Decimal("2600"))

    def test_missing_rate_raises(self):
        # No EXCHANGE_RATE_API_KEY / cache in tests -> must raise, never guess.
        with patch("common.fx.os.getenv", return_value=None):
            with self.assertRaises(fx.FxError):
                fx.get_rate("NGN", "GHS")
