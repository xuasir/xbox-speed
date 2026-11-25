#!/usr/bin/env python3
"""连接原始/回复元组、IPv6、QUIC 和直连证据的边界检查。"""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

LIB = Path(__file__).resolve().parents[1] / 'files/usr/share/xbox-accel/observation.sh'
RECORDS = LIB.with_name('records.sh')


class ObservationTests(unittest.TestCase):
    def test_saved_device_filter_preserves_observation_freshness(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config'
            base = "package xbox-accel\n\nconfig main 'main'\n\toption enabled '1'\n"
            body = r'''
. "$1"
RESOLVER_CONF=$PWD/resolver.conf; REDIRECT_CONF=$PWD/redirect.conf
STATE_DIR=$PWD/state; ALL_GROUPS=; OBSERVATION_PYTHON=$2
uci() { cat "$PWD/config"; }
sha256sum() { "$OBSERVATION_PYTHON" -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'; }
observation_signature
'''
            def signature(content):
                config.write_text(content)
                return subprocess.check_output(['sh', '-c', body, 'signature-test', str(LIB), sys.executable],
                                               cwd=directory, text=True).strip()
            original = signature(base)
            self.assertEqual(original, signature(base + "\toption download_devices ''\n"))
            self.assertEqual(original, signature(base + "\tlist download_devices '192.168.1.20'\n\tlist download_devices '192.168.1.21'\n"))
            self.assertNotEqual(original, signature(base.replace("enabled '1'", "enabled '0'")))

    def parse(self, text):
        result = subprocess.run(['sh', '-c', '. "$1"; observation_parse_connections',
                                 'observation-test', str(LIB)], input=text, capture_output=True, text=True, check=True)
        return [line.split('\t') for line in result.stdout.splitlines()]

    def test_downstream_uses_reply_bytes_and_retains_original_destination(self):
        values = self.parse('ipv4 2 tcp 6 430 ESTABLISHED src=192.168.1.20 dst=9.9.9.9 sport=50001 dport=443 packets=2 bytes=300 src=9.9.9.9 dst=192.168.1.1 sport=443 dport=50001 packets=3 bytes=900000 [ASSURED] mark=0 use=1\n')
        self.assertEqual(values[0][:5], ['192.168.1.20','9.9.9.9','50001','443','tcp'])
        self.assertEqual(values[0][8], '900000')

    def test_nat_interception_is_visible_in_the_reply_tuple(self):
        values = self.parse('tcp 6 400 ESTABLISHED src=192.168.1.20 dst=9.9.9.9 sport=50002 dport=443 src=127.0.0.1 dst=192.168.1.20 sport=1041 dport=50002 mark=32\n')
        self.assertEqual(values[0][6:10], ['127.0.0.1','1041','-','32'])

    def test_ipv6_quic_and_missing_counters(self):
        values = self.parse('ipv6 10 udp 17 180 src=fd00::20 dst=2001:db8::8 sport=55001 dport=443 src=2001:db8::8 dst=fd00::20 sport=443 dport=55001 [ASSURED] mark=0\n')
        self.assertEqual(values[0][0:5], ['fd00::20','2001:db8::8','55001','443','udp'])
        self.assertEqual(values[0][8], '-')

    def test_dns_and_incomplete_handshakes_are_filtered(self):
        values = self.parse('udp 17 120 src=192.168.1.20 dst=9.9.9.9 sport=50001 dport=53 src=9.9.9.9 dst=192.168.1.20 sport=53 dport=50001\n'
                            'tcp 6 10 SYN_SENT src=192.168.1.20 dst=9.9.9.9 sport=50002 dport=443 src=9.9.9.9 dst=192.168.1.20 sport=443 dport=50002\n')
        self.assertEqual(values, [])

    def test_route_evidence_requires_readable_firewall_and_zero_mark(self):
        with tempfile.TemporaryDirectory() as directory:
            body = r'''
. "$1"
TMP_DIR=$PWD; mkdir -p observation
redirect_ip() { echo 192.0.2.1; }
redirect_port() { echo 18081; }
redirect_device() { echo br-lan; }
lan_ip() { echo 192.168.1.1; }
ip() { echo '9.9.9.9 via 10.0.0.1 dev wan'; }
printf '1\n' > observation/interception-possible
observation_path 192.168.1.20 9.9.9.9 9.9.9.9 443 443 0
printf '0\n' > observation/interception-possible
observation_path 192.168.1.20 9.9.9.9 9.9.9.9 443 443 32
observation_path 192.168.1.20 9.9.9.9 9.9.9.9 443 443 0
observation_path 192.168.1.20 9.9.9.9 127.0.0.1 443 1041 0
observation_path 192.168.1.20 192.0.2.1 192.168.1.1 80 18081 0
ip() { echo '9.9.9.9 dev tun0'; }
observation_path 192.168.1.20 9.9.9.9 9.9.9.9 443 443 0
'''
            values = subprocess.check_output(['sh','-c',body,'path-test',str(LIB)],cwd=directory,text=True).splitlines()
            self.assertEqual(values,['unknown','unknown','direct','intercepted','redirect_entry','unknown'])

    def test_capacity_rotation_preserves_complete_json_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'events.jsonl'
            file.write_text(''.join('{"message":"' + 'x' * 100 + '"}\n' for _ in range(500)))
            subprocess.run(['sh','-c','. "$1"; records_bound "$2" 300 10000','records-test',str(RECORDS),str(file)],check=True)
            import json
            self.assertLessEqual(file.stat().st_size,10000)
            self.assertGreater(len(file.read_text().splitlines()),0)
            for line in file.read_text().splitlines(): json.loads(line)


if __name__ == '__main__':
    unittest.main()
