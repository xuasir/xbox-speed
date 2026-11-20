#!/usr/bin/env python3
"""Host-side checks for diagnostic error taxonomy; no router changes."""
import pathlib
import json
import subprocess
import unittest

LIB = pathlib.Path(__file__).resolve().parents[1] / 'files/usr/share/xbox-accel/diagnostics.sh'
PRIVACY = LIB.with_name('privacy.sh')


def invoke(function, *args):
    return subprocess.check_output(['sh', '-c', '. "$1"; shift; "$@"', 'diagnostics-test', str(LIB), function, *map(str, args)], text=True).strip()


def redact(value):
    return subprocess.check_output(['sh', '-c', '. "$1"; redact_report', 'privacy-test', str(PRIVACY)], input=json.dumps(value), text=True)


class ErrorClassificationTests(unittest.TestCase):
    def test_success_and_time_limited_sample(self):
        self.assertEqual(invoke('probe_classify', 0, 206, 1048576, 131072), 'OK')
        self.assertEqual(invoke('probe_classify', 28, 206, 1048576, 131072), 'TIME_LIMIT_WITH_DATA')
        self.assertEqual(invoke('probe_classify', 28, 206, 100, 131072), 'TIMEOUT')

    def test_transport_failures(self):
        for rc, expected in [(5, 'PROXY_RESOLVE_FAILED'), (6, 'DNS_RESOLVE_FAILED'), (7, 'CONNECT_FAILED'),
                             (18, 'PARTIAL_TRANSFER'), (23, 'LOCAL_WRITE_FAILED'), (35, 'TLS_HANDSHAKE_FAILED'),
                             (52, 'EMPTY_REPLY'), (56, 'NETWORK_READ_FAILED'), (60, 'CERTIFICATE_FAILED'),
                             (63, 'SIZE_LIMIT_EXCEEDED'), (3, 'URL_INVALID'), (55, 'NETWORK_WRITE_FAILED'),
                             (92, 'HTTP2_STREAM_FAILED'), (97, 'PROXY_HANDSHAKE_FAILED')]:
            self.assertEqual(invoke('probe_classify', rc, '000', 0, 131072), expected)

    def test_http_failures_not_marked_success(self):
        for code, expected in [('200', 'RANGE_IGNORED'), ('302', 'UNEXPECTED_REDIRECT'), ('403', 'HTTP_FORBIDDEN'),
                               ('404', 'HTTP_NOT_FOUND'), ('416', 'RANGE_INVALID'), ('429', 'RATE_LIMITED'),
                               ('503', 'UPSTREAM_ERROR'), ('000', 'NO_HTTP_RESPONSE')]:
            self.assertEqual(invoke('probe_classify', 0, code, 1048576, 131072), expected)
        self.assertEqual(invoke('probe_classify', 0, '206', 10, 131072), 'INSUFFICIENT_DATA')

    def test_query_secrets_redacted(self):
        value = invoke('safe_text', 'http://assets1.xboxlive.com/path?token=private&sig=secret trailing')
        self.assertNotIn('private', value)
        self.assertNotIn('secret', value)
        self.assertIn('?<redacted>', value)

    def test_export_redacts_compressed_ipv6_mac_and_url_credentials(self):
        text = redact({'log': '::1 fe80::abcd%br-lan ::ffff:203.0.113.77 2001:db8:0:0:0:0:0:1 AA:BB:CC:DD:EE:FF aa:bb:cc:dd:ee:ff',
                       'url': 'https://user:PRIVATE_PASSWORD@cdn.example/private/path?token=PRIVATE_TOKEN',
                       'time': '2026-11-20 12:34:56', 'status': 'failed'})
        result = json.loads(text)
        for private in ['::1', 'fe80::abcd', '203.0.113.77', '2001:db8', 'AA:BB:CC:DD:EE:FF', 'aa:bb:cc:dd:ee:ff',
                        'PRIVATE_PASSWORD', 'PRIVATE_TOKEN', '/private/path']:
            self.assertNotIn(private, text)
        self.assertEqual(result['log'].split()[-2:], ['MAC_1', 'MAC_1'])
        self.assertEqual(result['time'], '2026-11-20 12:34:56')
        self.assertEqual(result['status'], 'failed')

    def test_export_preserves_consistent_address_aliases_and_json(self):
        result = json.loads(redact({'expected': '9.9.9.9', 'observed': '9.9.9.9', 'other': '8.8.4.4', 'log': 'error="failed"\n9.9.9.9'}))
        self.assertEqual(result['expected'], result['observed'])
        self.assertNotEqual(result['expected'], result['other'])
        self.assertIn(result['expected'], result['log'])


if __name__ == '__main__':
    unittest.main()
