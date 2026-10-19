import unittest

from app.ai.model_circuit_breaker import ModelCircuitBreaker


class ModelCircuitBreakerTests(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        self.breaker = ModelCircuitBreaker(failure_threshold=2, cooldown_seconds=60, clock=lambda: self.now[0])

    def test_opens_after_consecutive_failures(self):
        self.breaker.record_failure("m", "http_500")
        self.assertFalse(self.breaker.is_open("m"))
        self.breaker.record_failure("m", "first_output_timeout")
        self.assertTrue(self.breaker.is_open("m"))
        self.assertFalse(self.breaker.is_open("other"))

    def test_success_between_failures_resets_the_count(self):
        self.breaker.record_failure("m", "http_500")
        self.breaker.record_success("m")
        self.breaker.record_failure("m", "http_500")
        self.assertFalse(self.breaker.is_open("m"))

    def test_half_open_after_cooldown_and_failed_probe_reopens(self):
        self.breaker.record_failure("m", "http_500")
        self.breaker.record_failure("m", "http_500")
        self.now[0] += 60
        self.assertFalse(self.breaker.is_open("m"))

        self.breaker.record_failure("m", "http_503")
        self.assertTrue(self.breaker.is_open("m"))

    def test_successful_probe_closes(self):
        self.breaker.record_failure("m", "http_500")
        self.breaker.record_failure("m", "http_500")
        self.now[0] += 60
        self.breaker.record_success("m")
        self.breaker.record_failure("m", "http_500")
        self.assertFalse(self.breaker.is_open("m"))

    def test_rejects_invalid_config(self):
        with self.assertRaises(ValueError):
            ModelCircuitBreaker(failure_threshold=0)
