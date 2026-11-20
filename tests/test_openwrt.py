#!/usr/bin/env python3
"""Destructive integration tests ONLY for a disposable OpenWrt Docker container.
Usage: python3 tests/test_openwrt.py xa-test
Never point this at a production router. Requires Docker and bind-dig in container.
"""
import json
import subprocess
import sys
import time
import unittest

CONTAINER = sys.argv.pop(1) if len(sys.argv) > 1 else 'xa-test'

def shell(command, check=True):
    p = subprocess.run(['docker', 'exec', CONTAINER, 'sh', '-c', command], text=True, capture_output=True, timeout=90)
    if check and p.returncode:
        raise AssertionError(f'command failed {p.returncode}: {p.stdout}\n{p.stderr}')
    return p

MOCK_CURL = '''#!/bin/sh
case "$*" in
*xbox-accel/selftest*) exec /usr/bin/curl "$@" ;;
*119.29.29.29/d*assets1.xboxlive.cn*) echo 9.9.9.9; exit 0 ;;
*119.29.29.29/d*) echo 8.8.4.4; exit 0 ;;
esac
[ ! -f /tmp/xa-mock/fail ] || { printf '404 10 0'; exit 0; }
[ ! -f /tmp/xa-mock/slow ] || sleep 20
speed=10000000
case "$*" in *:9.9.9.9*) speed=30000000 ;; *:8.8.4.4*) speed=20000000 ;; esac
printf '206 1048576 %s' "$speed"
'''
MOCK_NSLOOKUP = '''#!/bin/sh
case "$*" in *127.0.0.1*) exec /usr/bin/nslookup "$@" ;; *.cn*) echo 'Address: 9.9.9.9' ;; *) echo 'Address: 8.8.4.4' ;; esac
'''

class OpenWrtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        shell('test -f /etc/openwrt_release && test -x /usr/bin/xbox-accel; xbox-accel disable >/dev/null; cp /etc/config/xbox-accel /tmp/xa-config-backup; mkdir -p /tmp/xa-mock')
        for name, contents in [('curl',MOCK_CURL),('nslookup',MOCK_NSLOOKUP)]:
            p = subprocess.run(['docker','exec','-i',CONTAINER,'sh','-c',f'cat > /tmp/xa-mock/{name}; chmod 755 /tmp/xa-mock/{name}'], input=contents,text=True,capture_output=True)
            assert p.returncode == 0, p.stderr
        shell("uci set xbox-accel.main.enabled=0; uci set xbox-accel.main.redirect_cn=0; uci set xbox-accel.main.use_seeds=0; uci set xbox-accel.main.use_isp_dns=0; uci set xbox-accel.main.passwall_direct=0; uci set xbox-accel.main.max_candidates=3; uci set xbox-accel.main.quick_size_mb=1; uci set xbox-accel.main.full_size_mb=1; uci set xbox-accel.main.top_n=2; uci set xbox-accel.main.cron='15 5 * * *'; uci set xbox-accel.main.health_interval=10; uci set xbox-accel.main.dnsmasq_instance='@dnsmasq[0]'; uci commit xbox-accel; xbox-accel reset >/dev/null")
        shell('uci set xbox-accel.main.schedule_type=legacy; uci commit xbox-accel')

    @classmethod
    def tearDownClass(cls):
        shell('xbox-accel disable >/dev/null; cp /tmp/xa-config-backup /etc/config/xbox-accel')

    def test_01_mock_selection_and_failure_retention(self):
        p = shell('PATH=/tmp/xa-mock:$PATH xbox-accel test')
        self.assertIn('测速结束',p.stdout)
        self.assertEqual(shell('cat /etc/xbox-accel/best_cn').stdout.strip(),'9.9.9.9')
        self.assertEqual(shell('cat /etc/xbox-accel/best_com').stdout.strip(),'8.8.4.4')
        shell('touch /tmp/xa-mock/fail')
        p = shell('PATH=/tmp/xa-mock:$PATH xbox-accel test',check=False)
        self.assertNotEqual(p.returncode,0)
        self.assertEqual(shell('cat /etc/xbox-accel/best_cn').stdout.strip(),'9.9.9.9')
        shell('rm -f /tmp/xa-mock/fail')

    def test_02_dns_srv_aaaa_and_rollback(self):
        before = shell('uci show dhcp; uci show uhttpd').stdout
        shell('uci set xbox-accel.main.enabled=1; uci set xbox-accel.main.redirect_cn=1; uci commit xbox-accel; /etc/init.d/xbox-accel reload')
        # service startup can take a few seconds in this Docker rootfs
        shell('for i in 1 2 3 4 5 6 7 8; do dig @127.0.0.1 assets1.xboxlive.com A +short +time=1 +tries=1 | grep -qx 192.168.1.1 && break; sleep 1; done')
        self.assertEqual(shell('dig @127.0.0.1 assets1.xboxlive.com A +short').stdout.strip(),'192.168.1.1')
        self.assertEqual(shell('dig @127.0.0.1 assets1.xboxlive.com AAAA +short').stdout.strip(),'')
        self.assertIn('assets1.xboxlive.com.',shell('dig @127.0.0.1 assets-srvs.xboxlive.com SRV +short').stdout)
        shell('xbox-accel disable >/dev/null')
        self.assertEqual(shell('uci show dhcp; uci show uhttpd').stdout,before)

    def test_03_rpc_allowlist_and_environment(self):
        result=json.loads(shell("ubus call xbox-accel action '{\"command\":\"arbitrary_exec\"}'").stdout)
        self.assertFalse(result['ok'])
        applied=json.loads(shell("ubus call xbox-accel action '{\"command\":\"apply\"}'").stdout)
        self.assertTrue(applied['ok'], 'Apply must admit a tracked task, not claim execution success')
        self.assertTrue(applied['accepted'])
        self.wait_task(False)
        status=json.loads(shell('ubus call xbox-accel status').stdout)
        environment=json.loads(shell('ubus call xbox-accel environment').stdout)
        self.assertIsInstance(environment['environment'],list)
        self.assertIn('log',status)

    def wait_task(self, running, timeout=25):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            status = json.loads(shell('ubus call xbox-accel status').stdout)
            services = json.loads(shell("ubus call service list '{\"name\":\"xbox-accel-task\"}'").stdout)
            instances = services.get('xbox-accel-task', {}).get('instances', {})
            procd_running = any(i.get('running', False) for i in instances.values())
            if running and status['testing']:
                return status
            if not running and not status['testing'] and not procd_running:
                return status
            time.sleep(0.3)
        self.fail('background task state did not become ' + str(running))

    def test_04_background_restart_and_cancel(self):
        shell('touch /tmp/xa-mock/slow; PATH=/tmp/xa-mock:$PATH xbox-accel start_test cn')
        self.wait_task(True)
        duplicate = shell('PATH=/tmp/xa-mock:$PATH xbox-accel start_test cn', check=False)
        self.assertNotEqual(duplicate.returncode, 0)
        shell('sleep 120 >/dev/null 2>&1 & echo $! > /tmp/xa-unrelated.pid')
        shell('xbox-accel cancel')
        self.wait_task(False)
        self.assertEqual(shell('kill -0 "$(cat /tmp/xa-unrelated.pid)"', check=False).returncode, 0)
        shell('kill "$(cat /tmp/xa-unrelated.pid)"; rm -f /tmp/xa-mock/slow')
        # Identical procd task specification must restart after previous completion.
        for _ in range(2):
            shell('PATH=/tmp/xa-mock:$PATH xbox-accel start_test cn')
            self.wait_task(False)
            time.sleep(0.5)
        self.assertEqual(shell('flock -n /var/run/xbox-accel/task.lock -c true', check=False).returncode, 0)

    def test_05_enabled_update_releases_locks_and_health_repairs_rules(self):
        shell('uci set xbox-accel.main.redirect_cn=0; uci set xbox-accel.main.enabled=1; uci commit xbox-accel; /etc/init.d/xbox-accel reload')
        shell('PATH=/tmp/xa-mock:$PATH xbox-accel test cn')
        shell('PATH=/tmp/xa-mock:$PATH xbox-accel test cn')
        self.assertEqual(shell('flock -n /var/run/xbox-accel/task.lock -c true', check=False).returncode, 0)
        shell("uci del_list 'dhcp.@dnsmasq[0].server=/assets1.xboxlive.cn/127.0.0.1#5335'; uci commit dhcp; PATH=/tmp/xa-mock:$PATH xbox-accel health")
        self.assertIn('/assets1.xboxlive.cn/127.0.0.1#5335', shell('uci get dhcp.@dnsmasq[0].server').stdout)
        env=json.loads(shell('ubus call xbox-accel environment').stdout)['environment']
        schedule=next(row for row in env if row['name']=='定时完整测速')['detail']
        self.assertIn('每天 05:15', schedule)
        self.assertIn('每 10 分钟', schedule)
        self.assertNotIn('*', schedule, 'Environment view should describe schedules, not expose expressions')
        shell('xbox-accel disable >/dev/null')

    def test_06_log_rotation_and_cron_validation(self):
        shell('for i in $(seq 1 650); do echo "test line $i"; done > /tmp/xbox-accel/activity.log; PATH=/tmp/xa-mock:$PATH xbox-accel test cn')
        self.assertLessEqual(int(shell('wc -l < /tmp/xbox-accel/activity.log').stdout), 500)
        shell("uci set xbox-accel.main.cron='99 99 * * *'")
        result=shell('xbox-accel _validate',check=False)
        self.assertNotEqual(result.returncode,0)
        shell('uci revert xbox-accel')
        shell('xbox-accel clear_log')
        self.assertEqual(shell('wc -c < /tmp/xbox-accel/activity.log').stdout.strip(),'0')

    def test_07_unknown_dns_redirect_is_reported(self):
        shell('nft add table inet xa_integration; nft "add chain inet xa_integration preroute { type nat hook prerouting priority -2; policy accept; }"; nft add rule inet xa_integration preroute udp dport 53 redirect to :5999')
        try:
            result=json.loads(shell('ubus call xbox-accel environment').stdout)
            entries=[r for r in result['environment'] if r['name']=='DNS入口']
            self.assertTrue(any(r['level']=='unknown' and '5999' in r['detail'] for r in entries))
        finally:
            shell('nft delete table inet xa_integration')

    def test_08_updated_ip_flushes_front_dns_cache(self):
        shell('uci set xbox-accel.main.enabled=1; uci set xbox-accel.main.redirect_cn=0; uci commit xbox-accel; /etc/init.d/xbox-accel reload')
        shell('for i in 1 2 3 4 5 6 7 8; do dig @127.0.0.1 assets1.xboxlive.com A +short +time=1 +tries=1 | grep -qx 8.8.4.4 && break; sleep 1; done')
        self.assertEqual(shell('dig @127.0.0.1 assets1.xboxlive.com A +short').stdout.strip(), '8.8.4.4')
        shell('echo 1.1.1.1 > /etc/xbox-accel/best_com; /etc/init.d/xbox-accel reload')
        # Must change before the previous 30s DNS TTL expires.
        shell('for i in 1 2 3 4 5; do dig @127.0.0.1 assets1.xboxlive.com A +short +time=1 +tries=1 | grep -qx 1.1.1.1 && break; sleep 1; done')
        self.assertEqual(shell('dig @127.0.0.1 assets1.xboxlive.com A +short').stdout.strip(), '1.1.1.1')
        shell('xbox-accel disable >/dev/null')

    def test_09_calendar_schedules_generate_exact_cron_entries(self):
        for kind, clock, expected in [
                ('daily', '23:59', '59 23 * * *'),
                ('weekly', '08:35', '35 8 * * 3'),
                ('monthly', '04:09', '9 4 31 * *')]:
            shell(f"uci set xbox-accel.main.enabled=1; uci set xbox-accel.main.schedule_type={kind}; uci set xbox-accel.main.schedule_time={clock}; uci set xbox-accel.main.schedule_weekday=3; uci set xbox-accel.main.schedule_monthday=31; uci commit xbox-accel; /etc/init.d/xbox-accel reload")
            self.assertIn(expected + ' /usr/bin/xbox-accel start_test all scheduled',
                          shell('cat /etc/crontabs/root').stdout)
        shell("uci set xbox-accel.main.schedule_time='24:00'")
        self.assertNotEqual(shell('xbox-accel _validate', check=False).returncode, 0)
        shell('uci revert xbox-accel; xbox-accel disable >/dev/null')

    def test_10_boot_hook_restores_enabled_and_cleans_disabled_rules(self):
        shell('uci set xbox-accel.main.autostart=1; uci set xbox-accel.main.enabled=1; uci commit xbox-accel; /etc/init.d/xbox-accel boot')
        self.assertIn('/assets1.xboxlive.com/127.0.0.1#5335', shell('uci get dhcp.@dnsmasq[0].server').stdout)
        self.assertTrue(json.loads(shell('ubus call xbox-accel status').stdout)['running'])
        shell('/etc/init.d/xbox-accel stop; uci set xbox-accel.main.autostart=0; uci commit xbox-accel')
        shell("uci add_list 'dhcp.@dnsmasq[0].server=/assets1.xboxlive.com/127.0.0.1#5335'; uci commit dhcp; /etc/init.d/xbox-accel boot")
        self.assertNotIn('127.0.0.1#5335', shell('uci -q get dhcp.@dnsmasq[0].server', check=False).stdout)
        status = json.loads(shell('ubus call xbox-accel status').stdout)
        self.assertTrue(status['enabled'])
        self.assertFalse(status['running'])
        self.assertNotIn('# xbox-accel', shell('cat /etc/crontabs/root').stdout)
        shell("uci set xbox-accel.main.enabled=0; uci set xbox-accel.main.autostart=1; uci commit xbox-accel; uci add_list 'dhcp.@dnsmasq[0].server=/assets1.xboxlive.com/127.0.0.1#5335'; uci commit dhcp; /etc/init.d/xbox-accel boot")
        self.assertNotIn('127.0.0.1#5335', shell('uci -q get dhcp.@dnsmasq[0].server', check=False).stdout)

    def test_11_disable_cleans_live_dns_copy_and_cache_with_restart_option_off(self):
        shell('test ! -e /etc/init.d/passwall; mkdir -p /tmp/xa-passwall; cp /usr/sbin/dnsmasq /tmp/xa-passwall/opaque-dns-worker')
        restart = '''#!/bin/sh
test "$1" = restart || exit 1
if test -s /tmp/xa-passwall/copy.pid; then
    kill "$(cat /tmp/xa-passwall/copy.pid)" 2>/dev/null
    sleep 1
fi
cat > /tmp/xa-passwall/copy.conf <<'CONF'
port=5454
listen-address=127.0.0.1
bind-interfaces
no-resolv
no-hosts
cache-size=100
pid-file=/tmp/xa-passwall/copy.pid
server=127.0.0.1#5453
server=/user.example/127.0.0.1#5335
CONF
for rule in $(uci -q get 'dhcp.@dnsmasq[0].server'); do
    case "$rule" in /*) echo "server=$rule" >> /tmp/xa-passwall/copy.conf ;; esac
done
/tmp/xa-passwall/opaque-dns-worker --conf-file=/tmp/xa-passwall/copy.conf
'''
        subprocess.run(['docker', 'exec', '-i', CONTAINER, 'sh', '-c',
                        'cat > /etc/init.d/passwall; chmod 755 /etc/init.d/passwall'],
                       input=restart, text=True, check=True)
        try:
            shell("mkdir -p /etc/xbox-accel; echo 1.1.1.1 > /etc/xbox-accel/best_com; echo assets1.xboxlive.com > /etc/xbox-accel/domains_com; uci set xbox-accel.main.optimize_cn=0; uci commit xbox-accel")
            shell("printf 'port=5453\\nlisten-address=127.0.0.1\\nbind-interfaces\\nno-resolv\\nno-hosts\\npid-file=/tmp/xa-upstream.pid\\nhost-record=assets1.xboxlive.com,4.3.2.1\\nlocal=/assets1.xboxlive.com/\\n' > /tmp/xa-upstream.conf; dnsmasq -C /tmp/xa-upstream.conf")
            shell('uci set xbox-accel.main.enabled=1; uci set xbox-accel.main.optimize_com=1; uci set xbox-accel.main.passwall_restart=0; uci commit xbox-accel; /etc/init.d/xbox-accel reload; /etc/init.d/passwall restart')
            self.assertEqual(shell('dig @127.0.0.1 -p 5454 assets1.xboxlive.com A +short').stdout.strip(), '1.1.1.1')
            shell('uci set xbox-accel.main.optimize_com=0; uci commit xbox-accel; /etc/init.d/xbox-accel reload')
            self.assertEqual(shell('dig @127.0.0.1 -p 5454 assets1.xboxlive.com A +short').stdout.strip(), '4.3.2.1')
            shell('uci set xbox-accel.main.optimize_com=1; uci commit xbox-accel; /etc/init.d/xbox-accel reload; /etc/init.d/passwall restart')
            self.assertEqual(shell('dig @127.0.0.1 -p 5454 assets1.xboxlive.com A +short').stdout.strip(), '1.1.1.1')
            shell('xbox-accel disable >/dev/null')
            self.assertEqual(shell('dig @127.0.0.1 -p 5454 assets1.xboxlive.com A +short').stdout.strip(), '4.3.2.1')
            copied = shell('cat /tmp/xa-passwall/copy.conf').stdout
            self.assertNotIn('server=/assets1.xboxlive.com/127.0.0.1#5335', copied)
            self.assertIn('server=/user.example/127.0.0.1#5335', copied)
            shell('xbox-accel disable >/dev/null')
        finally:
            shell('for pid in /tmp/xa-passwall/*.pid /tmp/xa-upstream.pid; do kill "$(cat "$pid")" 2>/dev/null || true; done; rm -f /etc/init.d/passwall /tmp/xa-upstream.pid /tmp/xa-upstream.conf; rm -rf /tmp/xa-passwall; xbox-accel disable >/dev/null')

    def test_12_restart_action_replaces_resolver_preserves_settings_and_reports_failure(self):
        shell("mkdir -p /etc/xbox-accel; echo 9.9.9.9 > /etc/xbox-accel/best_cn; echo 8.8.4.4 > /etc/xbox-accel/best_com; echo assets1.xboxlive.cn > /etc/xbox-accel/domains_cn; echo assets1.xboxlive.com > /etc/xbox-accel/domains_com; uci set xbox-accel.main.enabled=1; uci set xbox-accel.main.optimize_cn=1; uci set xbox-accel.main.optimize_com=1; uci commit xbox-accel; /etc/init.d/xbox-accel reload")
        before_config = shell('uci show xbox-accel').stdout
        before_pid = shell('cat /var/run/xbox-accel/dnsmasq.pid').stdout.strip()
        reply = json.loads(shell("printf '%s\\n' '{\"command\":\"restart\"}' | PATH=/tmp/xa-mock:$PATH /usr/libexec/rpcd/xbox-accel call action").stdout)
        self.assertTrue(reply['ok'] and reply['accepted'])
        result = self.wait_task(False, timeout=60)
        self.assertEqual(result['last_task']['id'], reply['task_id'])
        self.assertEqual(result['last_task']['kind'], 'restart')
        self.assertIn(result['last_task']['status'], ['success', 'partial'])
        self.assertNotEqual(shell('cat /var/run/xbox-accel/dnsmasq.pid').stdout.strip(), before_pid)
        self.assertEqual(shell('uci show xbox-accel').stdout, before_config)
        self.assertEqual(shell('cat /etc/xbox-accel/best_com').stdout.strip(), '8.8.4.4')
        self.assertEqual(shell('dig @127.0.0.1 assets1.xboxlive.com A +short').stdout.strip(), '8.8.4.4')

        before_pid = shell('cat /var/run/xbox-accel/dnsmasq.pid').stdout.strip()
        shell('uci set xbox-accel.main.resolver_port=53; uci commit xbox-accel')
        reply = json.loads(shell("ubus call xbox-accel action '{\"command\":\"restart\"}'").stdout)
        self.assertTrue(reply['accepted'])
        result = self.wait_task(False)
        self.assertEqual(result['last_task']['status'], 'failed')
        self.assertEqual(result['last_task']['code'], 'CONFIG_INVALID')
        self.assertEqual(shell('cat /var/run/xbox-accel/dnsmasq.pid').stdout.strip(), before_pid)
        shell('uci set xbox-accel.main.resolver_port=5335; uci commit xbox-accel; xbox-accel disable >/dev/null')

        reply = json.loads(shell("ubus call xbox-accel action '{\"command\":\"restart\"}'").stdout)
        self.assertTrue(reply['accepted'])
        result = self.wait_task(False)
        self.assertEqual(result['last_task']['id'], reply['task_id'])
        self.assertEqual(result['last_task']['kind'], 'restart')
        self.assertEqual(result['last_task']['code'], 'DISABLE_VERIFIED')
        self.assertFalse(result['enabled'] or result['running'])

if __name__ == '__main__':
    unittest.main()
