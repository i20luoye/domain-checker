import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parent
HTML = (ROOT / "public" / "index.html").read_text(encoding="utf-8")


class FrontendBatchingTests(unittest.TestCase):
    def test_api_requests_are_capped_independently_from_user_batch_size(self):
        self.assertIn("SAFE_API_BATCH_SIZE", HTML)
        self.assertIn("slice(queueIndex, queueIndex + workBatchSize)", HTML)
        self.assertIn("sendBatchWithSplitRetry(batch)", HTML)

    def test_gateway_timeout_retries_by_splitting_batch(self):
        self.assertIn("isGatewayTimeout", HTML)
        self.assertIn("Math.ceil(batch.length / 2)", HTML)
        self.assertIn("leftResults.concat(rightResults)", HTML)

    def test_export_recheck_uses_safe_api_batch_size(self):
        self.assertIn("i+=SAFE_API_BATCH_SIZE", HTML)


if __name__ == "__main__":
    unittest.main()
