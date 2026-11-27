import unittest

from core.failover import GENERIC_PROVIDER_ERROR, is_retryable_provider_error


class _StatusError(Exception):
    def __init__(self, status_code):
        super().__init__("upstream failure")
        self.status_code = status_code


class _WrappedError(Exception):
    def __init__(self, status_code):
        super().__init__("wrapped")
        self.response = _StatusError(status_code)


class _TimeoutError(TimeoutError):
    pass


class FailoverHelperTests(unittest.TestCase):
    def test_payment_and_rate_limit_statuses_are_retryable(self):
        for status in (401, 402, 403, 410, 429, 500, 503):
            with self.subTest(status=status):
                self.assertTrue(is_retryable_provider_error(_StatusError(status)))

    def test_wrapped_response_status_is_retryable(self):
        self.assertTrue(is_retryable_provider_error(_WrappedError(429)))

    def test_timeout_and_quota_text_are_retryable(self):
        self.assertTrue(is_retryable_provider_error(_TimeoutError()))
        self.assertTrue(is_retryable_provider_error(RuntimeError("quota exhausted")))
        self.assertTrue(is_retryable_provider_error(RuntimeError("insufficient credits")))

    def test_bad_request_is_not_retryable(self):
        self.assertFalse(is_retryable_provider_error(_StatusError(400)))
        self.assertFalse(is_retryable_provider_error(ValueError("invalid prompt")))

    def test_generic_error_does_not_name_a_provider(self):
        lowered = GENERIC_PROVIDER_ERROR.lower()
        for name in ("google", "gemini", "hugging face", "huggingface", "pollinations"):
            self.assertNotIn(name, lowered)


if __name__ == "__main__":
    unittest.main()
