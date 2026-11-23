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

    def test_safe_text_never_writes_warnings_to_stderr(self):
        # gawk warnings from the redaction regexp would be counted as log lines.
        result = subprocess.run(['sh', '-c', '. "$1"; shift; "$@"', 'diagnostics-test', str(LIB),
                                 'safe_text', 'http://host/p?token=private&sig=secret trailing'],
                                text=True, capture_output=True, check=True)
        self.assertEqual(result.stderr, '')
        self.assertIn('?<redacted>', result.stdout)
        self.assertNotIn('private', result.stdout)

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


class LogModeTests(unittest.TestCase):
    def events(self, mode):
        return subprocess.run(['sh', '-c', '''
            . "$1"
            LOG_MODE=$2
            stamp() { echo fixed; }
            system_log() { :; }
            task_event DEBUG DNS_MATCH 'matched'
            task_event INFO OK 'sample'
            task_event INFO STAGE_SUMMARY 'samples=2 valid=2'
            task_event INFO NODE_SELECTED 'selected_ip=9.9.9.9'
            task_event WARN CONNECT_FAILED 'url=http://host/path?secret=value'
            task_event ERROR CONFIG_INVALID 'bad config'
            task_event INFO TASK_FINAL 'status=success'
        ''', 'logging-test', str(LIB), mode], text=True, capture_output=True, check=True).stderr

    def test_concise_keeps_results_and_errors(self):
        log = self.events('concise')
        self.assertEqual(len(log.splitlines()), 5)
        for code in ['STAGE_SUMMARY', 'NODE_SELECTED', 'CONNECT_FAILED', 'CONFIG_INVALID', 'TASK_FINAL']:
            self.assertIn(f'[{code}]', log)
        self.assertNotIn('[DEBUG]', log)
        self.assertNotIn('[OK]', log)
        self.assertNotIn('secret=value', log)

    def test_detail_keeps_individual_events(self):
        log = self.events('detail')
        self.assertEqual(len(log.splitlines()), 7)
        self.assertIn('[DEBUG][DNS_MATCH]', log)
        self.assertIn('[INFO][OK]', log)
        self.assertNotIn('secret=value', log)

    def test_suppressed_events_skip_formatting(self):
        result = subprocess.run(['sh', '-c', '''
            . "$1"
            safe_text() { echo unexpected-formatting >&2; }
            stamp() { echo unexpected-timestamp >&2; }
            task_event DEBUG DNS_MATCH 'matched'
            task_event INFO OK 'sample'
        ''', 'logging-test', str(LIB)], text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout + result.stderr, '')

    def test_display_filters_history_and_keeps_raw_log(self):
        result = subprocess.check_output(['sh', '-c', '''
            . "$1"
            diagnostic_log() {
                printf '%s\\n' '[id][stage][DEBUG][DNS_MATCH] matched' '[id][stage][INFO][OK] sample' \\
                    '[id][stage][INFO][NODE_SELECTED] ip' '[id][stage][WARN][TIMEOUT] timeout' 'legacy line'
            }
            display_log
            diagnostic_log
        ''', 'logging-test', str(LIB)], text=True).splitlines()
        self.assertEqual(result[:3], ['[id][stage][INFO][NODE_SELECTED] ip', '[id][stage][WARN][TIMEOUT] timeout', 'legacy line'])
        self.assertEqual(len(result), 8, '原始日志仍保留全部诊断行供导出')

    def test_legacy_cli_rows_keep_anomalies_and_expected_values(self):
        log = subprocess.check_output(['sh', '-c', '''
            . "$1"
            diagnostic_log() {
                printf '%s\\n' '11-20 10:00:00 [ 正常 ] [DNS_MATCH] matched' \\
                    '11-20 10:00:00   预期=9.9.9.9；实际=9.9.9.9' \\
                    '11-20 10:00:00 [ 异常 ] [DNS_MISMATCH] mismatch' \\
                    '11-20 10:00:00   预期=9.9.9.9；实际=1.2.3.4'
            }
            display_log
        ''', 'logging-test', str(LIB)], text=True)
        self.assertEqual(len(log.splitlines()), 2)
        self.assertIn('DNS_MISMATCH', log)
        self.assertIn('预期=9.9.9.9；实际=1.2.3.4', log)


if __name__ == '__main__':
    unittest.main()
