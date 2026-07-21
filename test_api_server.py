import unittest
import datetime as dt
import csv
import io

from api_server import TABLES, build_where, csv_bytes_from_payload, parse_bool, parse_int, public_row


class ApiServerTests(unittest.TestCase):
    def test_parse_bool(self):
        self.assertTrue(parse_bool("true"))
        self.assertTrue(parse_bool("1"))
        self.assertFalse(parse_bool("false"))
        self.assertFalse(parse_bool(None))

    def test_parse_int_clamps(self):
        self.assertEqual(parse_int("5000", default=100, minimum=1, maximum=1000), 1000)
        self.assertEqual(parse_int("-5", default=100, minimum=1, maximum=1000), 1)
        self.assertEqual(parse_int("bad", default=100, minimum=1, maximum=1000), 100)

    def test_public_row_hides_raw_by_default(self):
        row = {"id": 1, "raw_html": "<html>hello</html>", "row_json": '{"a": 1}'}
        result = public_row(row, include_raw=False)
        self.assertNotIn("raw_html", result)
        self.assertEqual(result["raw_html_length"], 18)
        self.assertEqual(result["row_json"], {"a": 1})

    def test_build_where_uses_allowed_filters(self):
        where_sql, values, applied = build_where(
            TABLES["payment-details"],
            {"est_id": ["ABC123"], "bad_column": ["x"], "q": ["JAN"]},
        )
        self.assertIn("est_id = %s", where_sql)
        self.assertNotIn("bad_column", where_sql)
        self.assertEqual(values[0], "ABC123")
        self.assertEqual(applied["q"], "JAN")

    def test_csv_bytes_from_payload_matches_epf_columns(self):
        payload = {
            "company_name": "Acme Private Limited",
            "establishments": [
                {
                    "establishment": {"office_name": "DELHI"},
                    "fields": {
                        "CIN Code": "U12345HR2026PTC000001",
                        "Working Status": "LIVE ESTABLISHMENT",
                        "State": "DELHI",
                        "Exemption Status": "UNEXEMPTED",
                    },
                    "payments": [
                        {
                            "est_id": "ABCXY1234567000",
                            "wage_month": "MAY-26",
                            "first_credit_date": dt.date(2026, 6, 12),
                            "amount": 1000,
                            "no_of_employee": 5,
                        }
                    ],
                }
            ],
        }

        filename, encoded, row_count, has_payment_rows = csv_bytes_from_payload(payload, "July_2026", {})
        rows = list(csv.reader(io.StringIO(encoded.decode("utf-8-sig"))))

        self.assertEqual(filename, "Acme Private Limited_EPFO_U12345HR2026PTC000001_July_2026.csv")
        self.assertEqual(row_count, 1)
        self.assertTrue(has_payment_rows)
        self.assertEqual(rows[0], ["ACME PRIVATE LIMITED"])
        self.assertEqual(rows[2][0], "ESTABLISHMENT CODE")
        self.assertEqual(rows[3], ["ABCXY1234567000", "NON PRIVATE TRUST", "LIVE ESTABLISHMENT", "DELHI", "MAY-26", "15/06/2026", "12/06/2026", "0 Day", "1000", "5"])


if __name__ == "__main__":
    unittest.main()
