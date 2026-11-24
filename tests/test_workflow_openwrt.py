#!/usr/bin/env python3
"""四入口工作流、持久记录和真实 LAN 连接观测，仅用于可丢弃 OpenWrt 容器。"""
import json
import shlex
import subprocess
import sys
import time
import unittest
import uuid
from workflow_fixture import WorkflowFixture, GROUPS

CONTAINER = sys.argv.pop(1) if len(sys.argv) > 1 else 'xa-test'
IMAGE = 'openwrt/rootfs:armsr-armv8-24.10.4'


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = WorkflowFixture(CONTAINER)
        cls.work = cls.fixture.setup()
        cls.fixture.run('uci set xbox-accel.main.extra_ips_ps=8.8.4.4; uci commit xbox-accel')

    @classmethod
    def tearDownClass(cls):
        cls.fixture.restore()

    def action(self, command, **arguments):
        payload = shlex.quote(json.dumps({'command': command, **arguments}))
        return json.loads(self.fixture.run('ubus call xbox-accel action ' + payload).stdout)

    def wait_done(self, admitted, timeout=50):
        self.assertTrue(admitted['accepted'], admitted)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.fixture.status()
            task = status.get('last_task', {})
            if task.get('id') == admitted['task_id'] and not status['testing'] and task.get('status') not in ('queued', 'running'):
                return status
            time.sleep(0.3)
        self.fail('后台任务等待超时')

    def runtime(self):
        return self.fixture.run("""sha256sum /etc/config/xbox-accel /etc/config/dhcp
for group in cn com store ps xbox_old switch; do
    for kind in best domains resources; do
        file="/etc/xbox-accel/${kind}_$group"
        [ ! -f "$file" ] || sha256sum "$file"
    done
done
""").stdout

    def measure(self, groups):
        status = self.wait_done(self.action('test', groups=groups))
        self.assertIn(status['last_task']['status'], ('success', 'partial'))
        return status

    def test_01_disabled_single_measurement_preserves_runtime(self):
        before = self.runtime()
        status = self.measure('ps')
        self.assertEqual(self.runtime(), before)
        self.assertFalse(status['enabled'])
        self.assertFalse(status['platforms']['ps']['enabled'])
        self.assertEqual(status['measurements']['ps']['ip'], '9.9.9.9')
        self.assertEqual(status['last_task']['kind'], 'measure')
        self.assertEqual(status['last_task']['application']['code'], 'MEASUREMENT_ONLY')
        self.assertEqual(status['platforms']['ps']['ip'], '')

    def test_02_multi_selection_and_store_signature(self):
        before = self.runtime()
        status = self.measure('cn,store,switch')
        self.assertEqual(self.runtime(), before)
        for group in ('cn', 'store', 'switch'):
            self.assertEqual(status['measurements'][group]['ip'], '9.9.9.9')
            self.assertTrue(status['measurements'][group]['compatible'])
        self.assertEqual(status['last_task']['groups']['com']['code'], 'NOT_REQUESTED')

    def test_03_failure_and_cancel_keep_previous_measurement(self):
        previous = self.fixture.status()['measurements']['ps']
        before = self.runtime()
        self.fixture.run('touch ' + self.work + '/fail')
        try:
            status = self.wait_done(self.action('test_ps'))
            self.assertEqual(status['last_task']['status'], 'failed')
            self.assertEqual(status['measurements']['ps'], previous)
            self.assertEqual(self.runtime(), before)
        finally:
            self.fixture.run('rm -f ' + self.work + '/fail')
        self.fixture.run('touch ' + self.work + '/slow')
        try:
            admitted = self.action('test_ps')
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                status = self.fixture.status()
                if status['last_task'].get('stage') == 'quick_ps':
                    break
                time.sleep(0.2)
            self.assertFalse(self.action('test_cn')['ok'])
            self.assertTrue(self.action('cancel')['ok'])
            status = self.wait_done(admitted)
            self.assertEqual(status['last_task']['status'], 'cancelled')
            self.assertEqual(status['measurements']['ps'], previous)
            self.assertEqual(self.runtime(), before)
        finally:
            self.fixture.run('rm -f ' + self.work + '/slow')

    def test_04_runtime_validation_is_separate_from_sampling(self):
        self.fixture.run('uci set xbox-accel.main.resolver_port=invalid; uci commit xbox-accel')
        try:
            status = self.measure('ps')
            before = self.runtime()
            rejected = self.wait_done(self.action('adopt_ps', result_id=status['measurements']['ps']['id']))
            self.assertEqual(rejected['last_task']['code'], 'CONFIG_INVALID')
            self.assertEqual(self.runtime(), before)
            self.assertFalse(rejected['enabled'])
        finally:
            self.fixture.run('uci set xbox-accel.main.resolver_port=5335; uci commit xbox-accel')

    def test_05_changed_resources_and_failed_commit_preserve_runtime(self):
        result = self.fixture.status()['measurements']['ps']
        self.fixture.run("uci set xbox-accel.main.test_url_ps='http://gst.prod.dl.playstation.net/changed-resource.pkg'; uci commit xbox-accel")
        try:
            self.assertFalse(self.fixture.status()['measurements']['ps']['compatible'])
            before = self.runtime()
            rejected = self.wait_done(self.action('adopt_ps', result_id=result['id']))
            self.assertEqual(rejected['last_task']['code'], 'MEASUREMENT_CHANGED')
            self.assertEqual(self.runtime(), before)
        finally:
            self.fixture.run('uci -q delete xbox-accel.main.test_url_ps; uci commit xbox-accel')
        self.fixture.run('touch ' + self.work + '/commit-fail')
        try:
            before = self.runtime()
            rejected = self.wait_done(self.action('adopt_ps', result_id=result['id']))
            self.assertEqual(rejected['last_task']['code'], 'CONFIG_COMMIT_FAILED')
            self.assertEqual(self.runtime(), before)
            self.assertEqual(self.fixture.run('find /etc/xbox-accel -maxdepth 1 -name "adopt-*"').stdout, '')
        finally:
            self.fixture.run('rm -f ' + self.work + '/commit-fail')

    def test_06_adoption_enables_platform_and_exposes_group_acceptance(self):
        result = self.fixture.status()['measurements']['ps']
        status = self.wait_done(self.action('adopt_ps', result_id=result['id']))
        self.assertTrue(status['enabled'])
        self.assertTrue(status['platforms']['ps']['enabled'])
        self.assertEqual(status['platforms']['ps']['ip'], result['ip'])
        self.assertEqual(status['last_task']['status'], 'success')
        self.assertEqual(status['last_application']['groups']['ps']['status'], 'verified')
        self.assertEqual(status['measurements']['ps']['id'], result['id'])
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            status = self.fixture.status()
            if status['observation']['fresh'] and status['observation']['flows']:
                break
            time.sleep(0.5)
        self.assertEqual(status['observation']['flows'][0]['status'], 'matched')
        verified = self.wait_done(self.action('verify'))
        report = json.loads(self.fixture.run('cat /tmp/xbox-accel/last-application.json').stdout)
        self.assertEqual(report['summary']['clients'], 'verified')
        self.assertEqual(verified['last_application']['clients'], 'verified')
        self.assertTrue(any(row['code'] == 'CLIENT_NODE_MATCHED' for row in report['environment']))
        self.assertFalse(any(event['code'] in ('TRAFFIC_NOT_OBSERVED', 'CLIENTS_NOT_OBSERVED', 'PERFORMANCE_COMPARISON_PENDING')
                             for event in json.loads(self.fixture.run('xbox-accel json_records').stdout)['errors']))

    def test_07_raw_flow_sampling_tracks_age_changes_ipv6_and_paths(self):
        work = self.work + '/observation-test'
        self.fixture.run('mkdir -p ' + work + '/state ' + work + '/memory ' + work + '/run')
        self.fixture.write(work + '/clock', '2000000000\n')
        self.fixture.write(work + '/signature', 'configuration-a\n')
        self.fixture.write(work + '/active', 'ps switch\n')
        self.fixture.write(work + '/state/best_ps', '9.9.9.9\n')
        self.fixture.write(work + '/state/best_switch', '9.9.9.9\n')
        self.fixture.write(work + '/devices', '192.168.1.20\tTEST_DEVICE_NAME\nfd00::20\tIPv6_DEVICE\n')
        script = r'''
set -- _fixture
. /usr/bin/xbox-accel >/dev/null
TMP_DIR="$1/memory"; STATE_DIR="$1/state"; RUN_DIR="$1/run"; fixture_work="$1"
date() { [ "$1" != +%s ] || { cat "$fixture_work/clock"; return; }; /bin/date "$@"; }
observation_signature() { cat "$fixture_work/signature"; }
observation_devices() { cat "$fixture_work/devices"; }
observation_connections() { cat "$fixture_work/connections"; }
groups_active() { cat "$fixture_work/active"; }
com_mode() { echo off; }
observation_firewall_capability() { echo 0 > "$TMP_DIR/observation/interception-possible"; }
record_event() { printf '%s %s\n' "$1" "$2" >> "$fixture_work/events"; }
records_flush_due() { :; }
ip() { echo '9.9.9.9 via 10.0.0.1 dev wan'; }
is_enabled() { return 0; }
observation_step
cat "$TMP_DIR/observation.json"
'''
        # Source dispatch consumes $1; retain the dedicated test path separately.
        script = script.replace('set -- _fixture\n', 'fixture_path=$1\nset -- _fixture\n')
        script = script.replace('TMP_DIR="$1/memory"; STATE_DIR="$1/state"; RUN_DIR="$1/run"; fixture_work="$1"',
                                'TMP_DIR="$fixture_path/memory"; STATE_DIR="$fixture_path/state"; RUN_DIR="$fixture_path/run"; fixture_work="$fixture_path"')

        def raw(sport=52001, target='9.9.9.9', bytes_received=100, source='192.168.1.20', reply=None, reply_port=443, mark=0):
            return (f'tcp 6 400 ESTABLISHED src={source} dst={target} sport={sport} dport=443 packets=2 bytes=200 '
                    f'src={reply or target} dst={source} sport={reply_port} dport={sport} packets=10 bytes={bytes_received} mark={mark}\n')

        def step(text):
            self.fixture.write(work + '/connections', text)
            return json.loads(self.fixture.run('sh -c ' + shlex.quote(script) + ' observation-test ' + work).stdout)

        self.assertTrue(step(raw())['flows'][0]['shared_node'])
        self.fixture.write(work + '/clock', '2000000005\n')
        sampled = step(raw(bytes_received=500100))
        self.assertEqual(sampled['flows'][0]['speed_bps'], 100000)
        self.assertEqual(sampled['flows'][0]['path'], 'direct')
        self.fixture.write(work + '/clock', '2000090005\n')
        self.assertEqual(step(raw())['flows'][0]['status'], 'matched')
        self.fixture.write(work + '/signature', 'configuration-b\n')
        self.fixture.write(work + '/active', 'ps\n')
        self.fixture.write(work + '/state/best_ps', '8.8.4.4\n')
        self.fixture.write(work + '/memory/dns-observation.log', 'dnsmasq: reply gst.prod.dl.playstation.net is 2001:db8::8\n')
        flows = step(raw() + raw(sport=52002, reply='127.0.0.1', reply_port=1041) +
                     raw(sport=52003, target='2001:db8::8', source='fd00::20', mark=32))['flows']
        self.assertEqual([flow['status'] for flow in flows], ['previous', 'different', 'different'])
        self.assertEqual([flow['path'] for flow in flows], ['direct', 'intercepted', 'unknown'])
        events = self.fixture.run('cat ' + work + '/events').stdout
        self.assertIn('INFO DOWNLOAD_PREVIOUS_NODE', events)
        self.assertIn('WARN DOWNLOAD_TARGET_DIFFERENT', events)

    def test_08_persisted_records_survive_memory_loss_and_directory_swap(self):
        self.fixture.run('xbox-accel disable >/dev/null')
        original = json.loads(self.fixture.run('xbox-accel json_records').stdout)
        self.assertGreater(len(original['events']), 0)
        self.assertGreater(len(original['tasks']), 0)
        self.fixture.run('rm -rf /tmp/xbox-accel/records')
        persisted = json.loads(self.fixture.run('xbox-accel json_records').stdout)
        self.assertEqual(persisted['source'], 'persisted')
        self.assertEqual(persisted['events'], original['events'])
        self.assertEqual(persisted['tasks'], original['tasks'])
        self.fixture.run('mv /etc/xbox-accel/records /etc/xbox-accel/records.previous')
        fallback = json.loads(self.fixture.run('xbox-accel json_records').stdout)
        self.assertEqual(fallback['tasks'], original['tasks'])
        self.fixture.run("set -- _fixture; . /usr/bin/xbox-accel >/dev/null; record_event ERROR RECORD_RECOVERY_TEST '已恢复旧快照' service")
        recovered = json.loads(self.fixture.run('xbox-accel json_records').stdout)
        self.assertEqual(recovered['tasks'], original['tasks'])
        self.assertEqual(recovered['errors'][-1]['code'], 'RECORD_RECOVERY_TEST')
        self.assertEqual(self.fixture.run('find /etc/xbox-accel -maxdepth 1 -name "records.*"').stdout, '')

    def test_09_real_lan_download_matches_node_and_retains_nat_evidence(self):
        self.fixture.run('xbox-accel enable >/dev/null')
        work, client = self.work, 'xa-codex-download-client-' + uuid.uuid4().hex[:10]
        router = subprocess.check_output(['docker', 'inspect', CONTAINER, '--format',
                  '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'], text=True).strip()
        lan = self.fixture.run('uci -q get network.lan.ipaddr').stdout.strip().split('/')[0]
        self.fixture.run('mkdir -p ' + work + '/www/cgi-bin')
        self.fixture.write(work + '/www/cgi-bin/stream', """#!/bin/sh
printf 'Content-Type: application/octet-stream\\r\\n\\r\\n'
for index in $(seq 1 25); do dd if=/dev/zero bs=65536 count=1 2>/dev/null; sleep 1; done
""", True)
        self.fixture.run(f'/usr/sbin/uhttpd -f -p {lan}:18445 -h {work}/www -x /cgi-bin -t 40 -T 40 >{work}/http.log 2>&1 & echo $! >{work}/http.pid')
        self.fixture.run(f"""nft -f - <<'NFT'
table ip xa_workflow_download {{
 chain prerouting {{
  type nat hook prerouting priority -101; policy accept;
  iifname "br-lan" ip daddr 9.9.9.9 tcp dport 443 dnat to {lan}:18445
 }}
}}
NFT
touch {work}/real-conntrack
""")
        process = None
        try:
            command = ('ip route add 9.9.9.9/32 via "$1"; sleep 2; '
                       '{ printf "GET /cgi-bin/stream HTTP/1.0\\r\\nHost: test\\r\\n\\r\\n"; sleep 28; } | nc 9.9.9.9 443 >/dev/null')
            process = subprocess.Popen(['docker', 'run', '--rm', '--name', client, '--platform', 'linux/aarch64_generic',
                      '--cap-add', 'NET_ADMIN', '--network', 'bridge', '--entrypoint', '/bin/sh', IMAGE,
                      '-c', command, 'client', router], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            deadline = time.monotonic() + 12
            client_ip = ''
            while time.monotonic() < deadline:
                inspected = subprocess.run(['docker', 'inspect', client, '--format',
                      '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'], text=True, capture_output=True)
                client_ip = inspected.stdout.strip()
                if inspected.returncode == 0 and client_ip:
                    break
                time.sleep(0.3)
            self.assertTrue(client_ip)
            self.fixture.run(f"printf '%s aa:bb:cc:dd:ee:01 {client_ip} REAL_LAN_DEVICE *\\n' '2147483647' >> /tmp/dhcp.leases")
            deadline = time.monotonic() + 20
            observed = None
            while time.monotonic() < deadline:
                status = self.fixture.status()
                observed = next((flow for flow in status['observation'].get('flows', [])
                           if flow['device_ip'] == client_ip and flow['speed_bps'] > 0), None)
                if observed:
                    break
                time.sleep(0.5)
            self.assertIsNotNone(observed, self.fixture.run('cat ' + work + '/http.log').stdout)
            self.assertEqual(observed['status'], 'matched')
            self.assertEqual(observed['path'], 'intercepted')
            self.assertGreater(observed['bytes_received'], 0)
            self.assertEqual(observed['target_ip'], '9.9.9.9')
        finally:
            subprocess.run(['docker', 'rm', '-f', client], capture_output=True, timeout=10)
            if process:
                process.communicate(timeout=10)
            self.fixture.run(f'nft delete table ip xa_workflow_download; rm -f {work}/real-conntrack; '
                             f'set -- _fixture; . /usr/bin/xbox-accel >/dev/null; fixture_pid=$(cat {work}/http.pid); '
                             f'tr "\\0" "\\n" < /proc/$fixture_pid/cmdline | grep -qxF {work}/www && kill_tree "$fixture_pid"', check=False)


if __name__ == '__main__':
    unittest.main()

