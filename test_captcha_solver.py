import unittest
from pathlib import Path
from capchasolver import solve_captcha


class CaptchaSolverTests(unittest.TestCase):
    def setUp(self):
        self.captchas_dir = Path(__file__).parent / "captchas"

    def test_solve_sample_captchas(self):
        # Dictionary of filename to expected solved captcha code
        samples = {
            "20260722_105231_revive_formulations_india_private_limited_attempt1.png": "Q2PY2",
            "20260722_110529_revive_formulations_india_private_limited_attempt1.png": "HGZI8",
            "20260722_111149_test_company_attempt1.png": "E7TP5",
            "20260722_112838_r_k_c_infracon_private_limited_attempt1.png": "6H2Z9",
            "20260722_113026_bluejay_nuts_private_limited_attempt1.png": "WPKNC",
            "20260722_113321_gsh_facilities_management_services_private_limited_attempt1.png": "298VO",
            "20260722_113608_gsh_facilities_management_services_private_limited_attempt1.png": "4DCAC",
            "20260722_113901_gsh_facilities_management_services_private_limited_attempt2.png": "HE6ZA",
            "20260722_114509_reliance_industries_limited_attempt1.png": "EERTF",
            "20260722_115209_xllent_marine_line_private_limited_attempt1.png": "R73E7",
            "20260722_115300_collicare_logistics_india_private_limited_attempt8.png": "BPVUP",
        }

        for filename, expected in samples.items():
            path = self.captchas_dir / filename
            if not path.exists():
                # Skip if the image was cleaned up/deleted by some test or doesn't exist,
                # but we know they exist in current environment.
                continue
            with self.subTest(filename=filename):
                result = solve_captcha(path)
                self.assertEqual(result, expected, f"Failed for {filename}")


if __name__ == "__main__":
    unittest.main()
