import unittest

from epfo_scraper import (
    captcha_error_message,
    company_search_variants,
    normalize_payment_header,
    parse_detail_section,
    parse_payment_rows,
    parse_payment_url,
    parse_search_results,
)


class EpfoParserTests(unittest.TestCase):
    def test_normalize_payment_header_aliases(self):
        self.assertEqual(normalize_payment_header("Credit Date"), "date_of_credit")
        self.assertEqual(normalize_payment_header("Payment Date"), "date_of_credit")
        self.assertEqual(normalize_payment_header("Month of Wage"), "wage_month")
        self.assertEqual(normalize_payment_header("Wage Period"), "wage_month")
        self.assertEqual(normalize_payment_header("Number of Employees"), "no_of_employee")
        self.assertEqual(normalize_payment_header("Emp Count"), "no_of_employee")

    def test_company_search_variants(self):
        variants = company_search_variants("Bsc- C And C- Kurali Toll Road Limited.", max_variants=4)
        self.assertEqual(variants[0], "Bsc C And C Kurali Toll Road Limited")
        self.assertIn("Bsc C And C Kurali Toll Road", variants)

    def test_captcha_error_message_from_text(self):
        message = captcha_error_message("<html><body>Please enter valid captcha</body></html>")
        self.assertEqual(message, "Please enter valid captcha")

    def test_captcha_error_message_from_alert(self):
        message = captcha_error_message("<script>alert('Invalid Captcha, Please try again');</script>")
        self.assertEqual(message, "Invalid Captcha, Please try again")

    def test_parse_search_results(self):
        html = """
        <table>
          <thead><tr>
            <th>Establishment ID</th><th>Establishment Name</th><th>Address</th><th>Office Name</th><th>Action</th>
          </tr></thead>
          <tbody><tr>
            <td>GJNRD0004147000</td>
            <td>RELIANCE INDUSTRIES LIMITED</td>
            <td>103/106 NARODA INDUSTRIAL ESTATE</td>
            <td>NARODA</td>
            <td><a name="GJNRD0004147000" onclick="fnViewDetails(this.name,'/publicPortal/no-auth/estSearch/viewA','/publicPortal/no-auth/estSearch/viewB')">View Details</a></td>
          </tr></tbody>
        </table>
        """
        rows = parse_search_results(html)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].est_id, "GJNRD0004147000")
        self.assertEqual(rows[0].office_name, "NARODA")
        self.assertEqual(rows[0].detail_urls, ["/publicPortal/no-auth/estSearch/viewA", "/publicPortal/no-auth/estSearch/viewB"])

    def test_parse_search_results_with_quoted_est_id(self):
        html = """
        <table>
          <thead><tr>
            <th>Establishment ID</th><th>Establishment Name</th><th>Address</th><th>Office Name</th><th>Action</th>
          </tr></thead>
          <tbody><tr>
            <td>GJNRD0004147000</td>
            <td>RELIANCE INDUSTRIES LIMITED</td>
            <td>103/106 NARODA INDUSTRIAL ESTATE</td>
            <td>NARODA</td>
            <td><a onclick="fnViewDetails('GJNRD0004147000','/publicPortal/no-auth/estSearch/viewA','/publicPortal/no-auth/estSearch/viewB')">View Details</a></td>
          </tr></tbody>
        </table>
        """
        rows = parse_search_results(html)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].est_id, "GJNRD0004147000")
        self.assertEqual(rows[0].detail_urls, ["/publicPortal/no-auth/estSearch/viewA", "/publicPortal/no-auth/estSearch/viewB"])

    def test_parse_search_results_with_combined_long_cell(self):
        html = """
        <table>
          <tr>
            <th>Establishment ID</th><th>Establishment Name</th><th>Address</th><th>Office Name</th><th>Action</th>
          </tr>
          <tr>
            <td>Some copied text before DLBRA1234567000 and more copied text after the id</td>
            <td>Bird Delhi General Aviation Services Private Limited</td>
            <td>DELHI</td>
            <td>DELHI</td>
            <td><a onclick='fnViewDetails("DLBRA1234567000","/publicPortal/no-auth/estSearch/viewA")'>View Details</a></td>
          </tr>
        </table>
        """
        rows = parse_search_results(html)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].est_id, "DLBRA1234567000")

    def test_parse_detail_fields_and_payment_link(self):
        html = """
        <a onclick="loadPayment('/publicPortal/no-auth/estSearch/viewPaymentDetails?_HDIV_STATE_=x')">View Payment Details</a>
        <table>
          <tr><td>A.</td><td>Establishment Code</td><td>GJNRD0004147000</td></tr>
          <tr><td>B.</td><td>Establishment Name</td><td>RELIANCE INDUSTRIES LIMITED</td></tr>
        </table>
        """
        records = parse_detail_section(html)
        self.assertEqual(records[0].field_name, "Establishment Code")
        self.assertEqual(records[0].field_value, "GJNRD0004147000")
        self.assertEqual(parse_payment_url(html), "/publicPortal/no-auth/estSearch/viewPaymentDetails?_HDIV_STATE_=x")

    def test_parse_payment_rows(self):
        html = """
        <table>
          <thead><tr><th>TRRN</th><th>Date Of Credit</th><th>Amount</th><th>Wage Month</th><th>No. of Employee</th><th>ECR</th></tr></thead>
          <tbody>
            <tr><td>1851701000805</td><td>16-JAN-2017</td><td>17,05,252</td><td>DEC-16</td><td>2878</td><td>YES</td></tr>
            <tr><td>1851702001577</td><td>14-FEB-2017</td><td>20,68,014</td><td>JAN-17</td><td>2860</td><td>YES</td></tr>
          </tbody>
        </table>
        """
        rows = parse_payment_rows(html)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["trrn"], "1851701000805")
        self.assertEqual(rows[0]["no_of_employee"], "2878")

    def test_parse_payment_rows_with_aliases(self):
        html = """
        <table>
          <thead><tr><th>TRRN</th><th>Payment Date</th><th>Amount</th><th>Wage Period</th><th>Emp Count</th><th>ECR</th></tr></thead>
          <tbody>
            <tr><td>1851701000805</td><td>16-JAN-2017</td><td>17,05,252</td><td>DEC-16</td><td>2878</td><td>YES</td></tr>
          </tbody>
        </table>
        """
        rows = parse_payment_rows(html)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date_of_credit"], "16-JAN-2017")
        self.assertEqual(rows[0]["wage_month"], "DEC-16")
        self.assertEqual(rows[0]["no_of_employee"], "2878")


if __name__ == "__main__":
    unittest.main()
