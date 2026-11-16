import unittest
from urllib.request import HTTPSHandler

from redux import net


class NetTests(unittest.TestCase):
    def test_downloads_verify_through_the_windows_certificate_store(self):
        import truststore
        self.assertIsInstance(net.ssl_context(), truststore.SSLContext)
        self.assertIs(net.ssl_context(), net.ssl_context())
        handlers = [h for h in net.opener().handlers if isinstance(h, HTTPSHandler)]
        self.assertEqual(len(handlers), 1)
        self.assertIs(handlers[0]._context, net.ssl_context())

    def test_the_bundled_mozilla_roots_back_up_the_windows_store(self):
        # truststore asks Windows first and falls back to roots loaded into the context.
        import certifi, ssl
        roots = net.ssl_context()._ctx.get_ca_certs()
        self.assertGreater(len(roots), 50)
        bundle = ssl.create_default_context(cafile=certifi.where()).get_ca_certs()
        self.assertEqual(len(roots), len(bundle))

    def test_a_certificate_failure_says_what_to_check(self):
        raw = "<urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:1082)>"
        message = net.friendly_error(raw)
        self.assertIn("date and time", message)
        self.assertTrue(message.endswith(raw), "the original error stays for diagnosis")
        self.assertEqual(net.friendly_error("disk full"), "disk full")


if __name__ == "__main__":
    unittest.main()
