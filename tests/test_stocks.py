import unittest

from scripts.stocks import parse_float, parse_int


class StockTests(unittest.TestCase):
    def test_parse_missing_values(self):
        self.assertIsNone(parse_float("N/D"))
        self.assertIsNone(parse_int(""))

    def test_parse_numeric_values(self):
        self.assertEqual(parse_float("123.45678"), 123.4568)
        self.assertEqual(parse_int("12345"), 12345)


if __name__ == "__main__":
    unittest.main()
