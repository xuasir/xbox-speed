#!/usr/bin/env python3
"""独立重定向集成测试，仅用于空闲、可丢弃的 OpenWrt Docker 容器。

用法：python3 tests/test_redirect_openwrt.py xa-test
使用真实 procd、DNS、HTTP、nftables/iptables 和独立 LAN 客户端。
"""
import json
import shlex
import subprocess
import sys
import time
import unittest
import uuid

CONTAINER = sys.argv.pop(1) if len(sys.argv) > 1 else 'xa-test'
IMAGE = 'openwrt/rootfs:armsr-armv8-24.10.4'
ENTRY = '192.0.2.1'
PORT = 18081


def shell(command, check=True, timeout=25):
    result = subprocess.run(['docker', 'exec', CONTAINER, 'sh', '-c', command],
                            text=True, capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise AssertionError(f'command failed: {command}\n{result.stdout}\n{result.stderr}')
    return result


def response(text):
    headers = text.replace('\r\n', '\n').split('\n\n', 1)[0].splitlines()
    status = int(headers[0].split()[1])
    fields = {line.split(':', 1)[0].lower(): line.split(':', 1)[1].strip()
              for line in headers[1:] if ':' in line}
    return status, fields


class RedirectServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        shell('test -f /etc/openwrt_release; test -r /usr/share/xbox-accel/redirect.sh; '
              'test "$(uci -q get xbox-accel.main.enabled)" = 0')
        status = json.loads(shell('xbox-accel json_status').stdout)
        if status['running'] or status['testing'] or status['redirect_running']:
            raise AssertionError('requires an idle disposable OpenWrt container')
        cls.work = shell('mktemp -d /tmp/xa-redirect-service.XXXXXX').stdout.strip()
        if not cls.work.startswith('/tmp/xa-redirect-service.'):
            raise AssertionError(cls.work)
        shell(f'cp /etc/config/xbox-accel {cls.work}/xbox; cp /etc/config/uhttpd {cls.work}/web; '
              f'cp /etc/config/firewall {cls.work}/firewall; '
              f'if [ -d /etc/xbox-accel ]; then tar -cf {cls.work}/state.tar -C /etc/xbox-accel .; fi')
        cls.web_before = shell('uci show uhttpd').stdout
        cls.firewall_before = shell('uci show firewall').stdout
        cls.router = subprocess.check_output(
            ['docker', 'inspect', CONTAINER, '--format', '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'],
            text=True).strip()
        cls.lan = shell('uci -q get network.lan.ipaddr').stdout.strip().split('/')[0]

    @classmethod
    def tearDownClass(cls):
        shell(f'/etc/init.d/xbox-accel stop; cp {cls.work}/xbox /etc/config/xbox-accel; '
              f'cp {cls.work}/web /etc/config/uhttpd; cp {cls.work}/firewall /etc/config/firewall; '
              f'if [ -f {cls.work}/state.tar ]; then tar -xf {cls.work}/state.tar -C /etc/xbox-accel; fi; '
              f'rm -rf {cls.work}')

    def setUp(self):
        shell(f'set -e\ncp {self.work}/xbox /etc/config/xbox-accel\n'
              'uci set xbox-accel.main.enabled=1\nuci set xbox-accel.main.redirect_cn=1\n'
              f'uci set xbox-accel.main.redirect_ip={ENTRY}\nuci set xbox-accel.main.redirect_port={PORT}\n'
              'uci set xbox-accel.main.health_interval=0\nuci set xbox-accel.main.passwall_direct=0\n'
              'for group in cn com store ps xbox_old switch; do uci set "xbox-accel.main.optimize_$group=0"; done\n'
              'uci commit xbox-accel')

    def tearDown(self):
        stopped = shell('/etc/init.d/xbox-accel stop', check=False)
        try:
            self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
            self.assertEqual(shell('uci show firewall').stdout, self.firewall_before)
            self.assertNotEqual(shell('nft list table ip xbox_accel_redirect', check=False).returncode, 0)
            status = json.loads(shell('xbox-accel json_status').stdout)
            self.assertFalse(status['running'])
            self.assertFalse(status['redirect_running'])
        finally:
            shell(f'cp {self.work}/xbox /etc/config/xbox-accel; '
                  f'if ! cmp -s {self.work}/web /etc/config/uhttpd; then '
                  f'cp {self.work}/web /etc/config/uhttpd; /etc/init.d/uhttpd restart; fi')

    def services(self, name='xbox-accel'):
        return json.loads(shell('ubus call service list ' + shlex.quote(json.dumps({'name': name}))).stdout)

    def main_pid(self):
        instances = self.services('uhttpd')['uhttpd']['instances']
        return next(value['pid'] for value in instances.values() if value['running'])

    def request(self, host='assets1.xboxlive.com', uri='/5/test?x=1&y=2', ip=ENTRY, port=80):
        return response(shell('curl -sS --noproxy "*" -m 3 -i -H ' + shlex.quote('Host: ' + host) +
                              ' ' + shlex.quote(f'http://{ip}:{port}{uri}')).stdout)

    def start(self):
        shell('/etc/init.d/xbox-accel reload')
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                self.assertEqual(self.request()[0], 302)
                return
            except (AssertionError, ValueError, IndexError):
                time.sleep(0.2)
        self.fail('redirect HTTP listener did not become ready')

    def client_request(self, host='assets1.xboxlive.com', uri='/5/lan-test?x=1&y=2', entry=ENTRY):
        name = 'xa-redirect-client-' + uuid.uuid4().hex[:12]
        command = ('set -e\nip route add "$2/32" via "$1"\n'
                   '{ printf "GET %s HTTP/1.0\\r\\nHost: %s\\r\\n\\r\\n" "$3" "$4"; sleep 1; } | nc "$2" 80')
        try:
            result = subprocess.run([
                'docker', 'run', '--rm', '--name', name, '--platform', 'linux/aarch64_generic',
                '--cap-add', 'NET_ADMIN', '--network', 'bridge', '--entrypoint', '/bin/sh', IMAGE,
                '-c', command, 'client', self.router, entry, uri, host,
            ], text=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(result.stdout, 'LAN client returned an empty HTTP response: ' + result.stderr)
            return response(result.stdout)
        finally:
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=10)

    def test_01_independent_instances_dns_and_local_http(self):
        main = self.main_pid()
        self.start()
        instances = self.services()['xbox-accel']['instances']
        self.assertEqual(set(instances), {'resolver', 'redirect', 'observation'})
        self.assertTrue(all(value['running'] for value in instances.values()))
        self.assertEqual(instances['redirect']['command'].count('-p'), 2)
        self.assertIn('127.0.0.1:18081', instances['redirect']['command'])
        self.assertIn(self.lan + ':18081', instances['redirect']['command'])
        self.assertEqual(shell('uci show uhttpd').stdout, self.web_before)
        self.assertEqual(self.main_pid(), main)
        for host in ('assets1.xboxlive.com', 'dlassets2.xboxlive.com', '5.xb.delivery.microsoft.com'):
            self.assertEqual(shell(f'dig @127.0.0.1 {host} A +short').stdout.strip(), ENTRY)
            self.assertEqual(shell(f'dig @127.0.0.1 {host} AAAA +short').stdout.strip(), '')
        self.assertIn('assets1.xboxlive.com.', shell('dig @127.0.0.1 assets-srvs.xboxlive.com SRV +short').stdout)
        self.assertEqual(self.request()[1]['location'], 'http://assets1.xboxlive.cn/5/test?x=1&y=2')
        self.assertEqual(self.request(host='router.lan')[0], 404)
        status = json.loads(shell('xbox-accel json_status').stdout)
        self.assertTrue(status['redirect_running'])
        self.assertTrue(status['redirect_ready'])
        self.assertEqual(status['redirect_ip'], ENTRY)
        self.assertEqual(status['lan_ip'], self.lan)

    def test_02_lan_client_hits_prerouting_and_main_http_stays_available(self):
        self.start()
        status, fields = self.client_request()
        self.assertEqual(status, 302)
        self.assertEqual(fields['location'], 'http://assets1.xboxlive.cn/5/lan-test?x=1&y=2')
        self.assertEqual(self.client_request(host='router.lan')[0], 404)
        self.assertEqual(self.request(host='router.lan', uri='/luci-static/resources/luci.js', ip=self.lan)[0], 200)
        self.assertRegex(shell('nft list chain ip xbox_accel_redirect prerouting').stdout, r'counter packets [1-9][0-9]*')

    def test_03_main_https_redirect_and_custom_http_port_are_independent(self):
        shell('set -e\nuci delete uhttpd.main.listen_http\n'
              'uci add_list uhttpd.main.listen_http=127.0.0.1:18083\n'
              'uci set uhttpd.main.redirect_https=1\nuci commit uhttpd\n/etc/init.d/uhttpd restart')
        configured = shell('uci show uhttpd').stdout
        self.start()
        self.assertEqual(shell('uci show uhttpd').stdout, configured)
        self.assertEqual(self.request()[0], 302)
        status, fields = self.request(host='router.lan', ip='127.0.0.1', port=18083)
        self.assertIn(status, (301, 302, 307, 308))
        self.assertTrue(fields['location'].startswith('https://'))

    def test_04_changing_port_and_entry_replaces_old_listener_and_dns(self):
        self.start()
        shell('uci set xbox-accel.main.redirect_ip=192.0.2.2; '
              'uci set xbox-accel.main.redirect_port=18084; uci commit xbox-accel; /etc/init.d/xbox-accel reload')
        self.assertEqual(self.request(ip='192.0.2.2')[0], 302)
        self.assertEqual(shell('dig @127.0.0.1 assets1.xboxlive.com A +short').stdout.strip(), '192.0.2.2')
        self.assertNotEqual(shell('curl -sS --noproxy "*" -m 1 http://127.0.0.1:18081/', check=False).returncode, 0)
        rules = shell('nft list table ip xbox_accel_redirect').stdout
        self.assertNotIn('ip daddr 192.0.2.1 ', rules)
        self.assertIn('dnat to 127.0.0.1:18084', rules)

    def test_05_procd_respawns_redirect_and_preserves_other_instances(self):
        self.start()
        before = self.services()['xbox-accel']['instances']
        main = self.main_pid()
        shell(f'kill -KILL {before["redirect"]["pid"]}')
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            current = self.services()['xbox-accel']['instances']
            if current['redirect']['running'] and current['redirect']['pid'] != before['redirect']['pid']:
                self.assertEqual(current['resolver']['pid'], before['resolver']['pid'])
                self.assertEqual(self.main_pid(), main)
                self.assertEqual(self.request()[0], 302)
                return
            time.sleep(0.25)
        self.fail('procd did not respawn the redirect process')

    def test_06_firewall_reload_restores_missing_rules(self):
        self.start()
        before = self.services()['xbox-accel']['instances']['redirect']['pid']
        shell('nft delete table ip xbox_accel_redirect; /etc/init.d/firewall reload')
        self.assertEqual(self.request()[0], 302)
        self.assertEqual(self.services()['xbox-accel']['instances']['redirect']['pid'], before)
        shell('/etc/init.d/firewall restart')
        self.assertEqual(self.client_request()[0], 302)

    def test_07_stop_discovers_unregistered_process_with_missing_or_wrong_cache(self):
        main = self.main_pid()
        for cache in ('missing', 'wrong'):
            with self.subTest(cache=cache):
                shell('mkdir -p /var/etc/xbox-accel/redirect /var/run/xbox-accel; '
                      'cp /usr/share/xbox-accel/redirect.cgi /var/etc/xbox-accel/redirect/redirect.cgi; '
                      'chmod 755 /var/etc/xbox-accel/redirect/redirect.cgi')
                pid = shell('/usr/sbin/uhttpd -f -h /var/etc/xbox-accel/redirect -x / -E /redirect.cgi '
                            '-p 127.0.0.1:18085 >/dev/null 2>&1 & echo $!').stdout.strip()
                try:
                    shell(f'kill -0 {pid}')
                    if cache == 'wrong':
                        shell(f'echo {main} > /var/run/xbox-accel/redirect.pid')
                    else:
                        shell('rm -f /var/run/xbox-accel/redirect.pid')
                    shell('/etc/init.d/xbox-accel stop')
                    self.assertNotEqual(shell(f'kill -0 {pid}', check=False).returncode, 0)
                    self.assertEqual(self.main_pid(), main)
                finally:
                    shell(f'kill -TERM {pid}', check=False)

    def test_08_invalid_settings_and_occupied_port_preserve_live_service(self):
        self.start()
        for settings in ('redirect_port=80', 'redirect_port=5335', 'redirect_port=018081',
                         'redirect_ip=192.168.1.1', 'redirect_ip=192.168.1.222', 'redirect_ip=127.0.0.1'):
            with self.subTest(settings=settings):
                shell(f'uci set xbox-accel.main.{settings}')
                rejected = shell('/etc/init.d/xbox-accel reload', check=False)
                self.assertEqual(rejected.returncode, 1)
                self.assertEqual(self.request()[0], 302)
                shell('uci revert xbox-accel')
        shell(f'mkdir -p {self.work}/foreign')
        pid = shell(f'/usr/sbin/uhttpd -f -h {self.work}/foreign -p 127.0.0.1:18086 '
                    '>/dev/null 2>&1 & echo $!').stdout.strip()
        try:
            time.sleep(0.2)
            shell('uci set xbox-accel.main.redirect_port=18086')
            rejected = shell('/etc/init.d/xbox-accel reload', check=False)
            self.assertEqual(rejected.returncode, 1)
            self.assertIn('被其他服务占用', rejected.stdout + rejected.stderr)
            self.assertEqual(self.request()[0], 302)
            shell(f'kill -0 {pid}')
        finally:
            shell('uci revert xbox-accel')
            shell(f'kill -TERM {pid}', check=False)

    def test_09_legacy_handler_and_router_ip_migrate_once(self):
        shell('set -e\nmkdir -p /etc/xbox-accel\n'
              'echo /previous-custom-404 > /etc/xbox-accel/uhttpd_error_page\n'
              'touch /etc/xbox-accel/uhttpd_pending\n'
              'uci set uhttpd.main.error_page=/cgi-bin/xbox-accel-redirect\nuci commit uhttpd\n'
              f'uci set xbox-accel.main.redirect_ip={self.lan}\nuci commit xbox-accel\n/etc/init.d/uhttpd restart')
        self.start()
        self.assertEqual(shell('uci -q get uhttpd.main.error_page').stdout.strip(), '/previous-custom-404')
        self.assertEqual(shell('uci -q get xbox-accel.main.redirect_ip').stdout.strip(), ENTRY)
        shell('test ! -e /etc/xbox-accel/uhttpd_pending; test ! -e /etc/xbox-accel/uhttpd_error_page')
        main = self.main_pid()
        shell('/etc/init.d/xbox-accel reload')
        self.assertEqual(self.main_pid(), main)
        self.assertEqual(self.request()[0], 302)

    def test_10_foreign_firewall_table_is_preserved(self):
        shell('nft add table ip xbox_accel_redirect; nft add chain ip xbox_accel_redirect user_chain')
        try:
            rejected = shell('/etc/init.d/xbox-accel reload', check=False)
            self.assertEqual(rejected.returncode, 1)
            self.assertIn('由其他配置持有', rejected.stdout + rejected.stderr)
            self.assertIn('user_chain', shell('nft list table ip xbox_accel_redirect').stdout)
            self.assertEqual(shell('uci show uhttpd').stdout, self.web_before)
        finally:
            shell('nft delete table ip xbox_accel_redirect')

    def test_10a_legacy_address_migrates_after_old_handler_was_already_cleared(self):
        shell(f'uci set xbox-accel.main.redirect_ip={self.lan}; '
              'uci -q delete xbox-accel.main.redirect_service_version; uci commit xbox-accel')
        self.start()
        self.assertEqual(shell('uci -q get xbox-accel.main.redirect_ip').stdout.strip(), ENTRY)
        self.assertEqual(shell('uci -q get xbox-accel.main.redirect_service_version').stdout.strip(), '1')
        self.assertEqual(shell('uci show uhttpd').stdout, self.web_before)

    def test_11_iptables_rules_deliver_http_and_cleanup_owned_chains(self):
        if shell('command -v iptables', check=False).returncode:
            self.skipTest('iptables backend is not installed in this disposable container')
        self.start()
        script = f'''
set -e
NAME=xbox-accel; SHARE_DIR=/usr/share/xbox-accel; STATE_DIR=/etc/xbox-accel; RUN_DIR=/var/run/xbox-accel
REDIRECT_CONF=/var/etc/xbox-accel/redirect.conf
cfg() {{ uci -q get "xbox-accel.main.$1" || echo "$2"; }}
lan_ip() {{ echo {self.lan}; }}
note() {{ echo "$*" >&2; }}
. /usr/share/xbox-accel/redirect.sh
redirect_backend() {{ echo iptables; }}
apply_redirect_firewall {ENTRY} {self.lan} {PORT} br-lan iptables
echo '{self.lan} {PORT} {ENTRY} br-lan iptables' > "$REDIRECT_CONF"
redirect_firewall_ready
'''
        shell(script)
        self.assertEqual(self.request()[0], 302)
        self.assertEqual(self.client_request()[0], 302)
        shell('/etc/init.d/xbox-accel stop')
        self.assertNotEqual(shell('iptables -w 2 -t nat -S XBOX_ACCEL_PRE', check=False).returncode, 0)
        self.assertNotEqual(shell('iptables -w 2 -t nat -S XBOX_ACCEL_OUT', check=False).returncode, 0)

    def test_12_loopback_only_process_is_reported_as_partial_listener(self):
        shell('mkdir -p /var/etc/xbox-accel/redirect; '
              'cp /usr/share/xbox-accel/redirect.cgi /var/etc/xbox-accel/redirect/redirect.cgi; '
              'chmod 755 /var/etc/xbox-accel/redirect/redirect.cgi')
        pid = shell('/usr/sbin/uhttpd -f -h /var/etc/xbox-accel/redirect -x / -E /redirect.cgi '
                    '-p 127.0.0.1:18081 >/dev/null 2>&1 & echo $!').stdout.strip()
        try:
            time.sleep(0.2)
            status = json.loads(shell('xbox-accel json_status').stdout)
            self.assertTrue(status['redirect_running'])
            self.assertFalse(status['redirect_ready'])
            report = json.loads(shell('xbox-accel json_preflight').stdout)
            self.assertTrue(any(row['code'] == 'REDIRECT_LISTENER_MISSING' and row['level'] == 'fail'
                                for row in report['environment']))
        finally:
            shell(f'kill -TERM {pid}', check=False)

    def test_13_disabling_redirect_and_plugin_removes_instance_and_rules(self):
        for option in ('redirect_cn', 'enabled'):
            with self.subTest(option=option):
                shell('uci set xbox-accel.main.enabled=1; uci set xbox-accel.main.redirect_cn=1; uci commit xbox-accel')
                self.start()
                shell(f'uci set xbox-accel.main.{option}=0; uci commit xbox-accel; /etc/init.d/xbox-accel reload')
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    status = json.loads(shell('xbox-accel json_status').stdout)
                    if not status['redirect_running']:
                        break
                    time.sleep(0.2)
                self.assertFalse(status['redirect_running'])
                self.assertEqual(status['running'], option == 'redirect_cn')
                self.assertNotEqual(shell('nft list table ip xbox_accel_redirect', check=False).returncode, 0)
                self.assertNotEqual(shell('uci -q get firewall.xbox_accel_redirect', check=False).returncode, 0)


if __name__ == '__main__':
    unittest.main()
