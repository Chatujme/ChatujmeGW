import unittest

from chatujmegw import config, state


class TestRateLimit(unittest.TestCase):
    IP = '198.51.100.7'

    def setUp(self):
        state.connection_counts.pop(self.IP, None)

    def test_limit_blocks_after_max(self):
        for i in range(config.MAX_CONNECTIONS_PER_IP):
            self.assertTrue(state.check_rate_limit(self.IP), f"connection {i} should pass")
        self.assertFalse(state.check_rate_limit(self.IP))

    def test_uncount_frees_slot(self):
        for _ in range(config.MAX_CONNECTIONS_PER_IP):
            state.check_rate_limit(self.IP)
        self.assertFalse(state.check_rate_limit(self.IP))
        state.uncount_rate_limit(self.IP)  # probe disconnected quickly
        self.assertTrue(state.check_rate_limit(self.IP))

    def test_uncount_unknown_ip_is_noop(self):
        state.uncount_rate_limit('203.0.113.99')  # must not raise
