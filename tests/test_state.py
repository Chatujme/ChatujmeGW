import unittest

from chatujmegw import config, state


class TestRateLimit(unittest.TestCase):
    IP = '198.51.100.7'

    def setUp(self):
        state.connection_log.pop(self.IP, None)

    def test_limit_blocks_after_max(self):
        for i in range(config.MAX_CONNECTIONS_PER_IP):
            self.assertTrue(state.allow_connection(self.IP), f"connection {i} should pass")
        self.assertFalse(state.allow_connection(self.IP))

    def test_refund_frees_slot(self):
        for _ in range(config.MAX_CONNECTIONS_PER_IP):
            state.allow_connection(self.IP)
        self.assertFalse(state.allow_connection(self.IP))
        state.refund_connection(self.IP)  # probe disconnected quickly
        self.assertTrue(state.allow_connection(self.IP))

    def test_refund_unknown_ip_is_noop(self):
        state.refund_connection('203.0.113.99')  # must not raise
