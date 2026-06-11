import importlib
import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "api"))


class AccuracyContractTests(unittest.TestCase):
    def setUp(self):
        self.check = importlib.import_module("check")
        self._orig_get_sources = self.check.get_rdap_sources
        self._orig_query_single_source = self.check.query_single_source

    def tearDown(self):
        self.check.get_rdap_sources = self._orig_get_sources
        self.check.query_single_source = self._orig_query_single_source

    def test_available_requires_completed_confirmation_when_multiple_sources_exist(self):
        self.check.get_rdap_sources = lambda suffix: ["https://primary.test/domain/", "https://fallback.test/domain/"]

        def fake_query(domain, source):
            if "primary" in source:
                return 404
            return -1

        self.check.query_single_source = fake_query

        result = self.check.check_domain("brandcheckexample.com")

        self.assertEqual(result.status, "error")
        self.assertEqual(result.sources_ok, 1)
        self.assertEqual(result.sources_total, 2)
        self.assertGreaterEqual(result.verification_rounds, 2)
        self.assertIn("可注册未完成复核", result.detail)

    def test_taken_wins_over_available_conflicts(self):
        self.check.get_rdap_sources = lambda suffix: ["https://primary.test/domain/", "https://fallback.test/domain/"]

        def fake_query(domain, source):
            if "primary" in source:
                return 404
            return 200

        self.check.query_single_source = fake_query

        result = self.check.check_domain("conflictcheckexample.com")

        self.assertEqual(result.status, "taken")
        self.assertEqual(result.sources_ok, 1)
        self.assertEqual(result.sources_total, 2)
        self.assertIn("冲突", result.detail)

    def test_premium_is_available_tag_not_separate_status_in_frontend(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")

        self.assertNotIn("displayStatus = 'premium'", html)
        self.assertNotIn("data-filter=\"premium\"", html)
        self.assertIn("premium", html)


if __name__ == "__main__":
    unittest.main()
