#!/usr/bin/env python3
"""Service-stop regressions ONLY for an idle disposable OpenWrt Docker container."""
import json
import subprocess
import sys
import time
import unittest

CONTAINER = sys.argv.pop(1) if len(sys.argv) > 1 else 'xa-test'


def shell(script, check=True):
    result = subprocess.run(['docker', 'exec', CONTAINER, 'sh', '-c', script],
                            text=True, capture_output=True, timeout=15)
    if check and result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return result


class ServiceStopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        shell('test -f /etc/openwrt_release; test -x /usr/bin/xbox-accel; test "$(uci -q get xbox-accel.main.enabled)" = 0')
        if json.loads(shell('xbox-accel json_status').stdout)['running']:
            raise AssertionError('requires an idle disposable container')
        cls.work = shell('mktemp -d /tmp/xa-service-stop.XXXXXX').stdout.strip()
        shell(f'cp /etc/config/xbox-accel {cls.work}/config; '
              f'if [ -f /var/run/xbox-accel/dnsmasq.pid ]; then cp /var/run/xbox-accel/dnsmasq.pid {cls.work}/resolver.pid; fi; '
              'rm -f /var/run/xbox-accel/dnsmasq.pid; '
              f'if [ -f /var/etc/xbox-accel/dnsmasq.conf ]; then cp /var/etc/xbox-accel/dnsmasq.conf {cls.work}/resolver.conf; fi')

    @classmethod
    def tearDownClass(cls):
        shell(f'cp {cls.work}/config /etc/config/xbox-accel; '
              'rm -f /var/run/xbox-accel/dnsmasq.pid /var/etc/xbox-accel/dnsmasq.conf; '
              f'if [ -f {cls.work}/resolver.conf ]; then cp {cls.work}/resolver.conf /var/etc/xbox-accel/dnsmasq.conf; fi; '
              f'if [ -f {cls.work}/resolver.pid ]; then cp {cls.work}/resolver.pid /var/run/xbox-accel/dnsmasq.pid; fi; '
              f'rm -rf {cls.work}')

    def test_01_busy_procd_lock_has_deadline_and_cleans_waiters(self):
        # FD 53 demonstrates that ownership follows the lock, independently of its number.
        holder = shell(f"sh -c 'exec 53>/var/lock/procd_xbox-accel.lock; flock -n 53 || exit 1; "
                       f"echo $$ > {self.work}/holder.pid; exec sleep 60' >/dev/null 2>&1 & echo $!").stdout.strip()
        try:
            shell(f'for n in 1 2 3 4 5; do test -s {self.work}/holder.pid && exit 0; sleep 1; done; exit 1')
            started = time.monotonic()
            result = shell('XBOX_ACCEL_SERVICE_TIMEOUT=2 /etc/init.d/xbox-accel stop', check=False)
            self.assertEqual(result.returncode, 124, result.stdout + result.stderr)
            self.assertIn('SERVICE_TIMEOUT', result.stderr)
            self.assertLess(time.monotonic() - started, 8)
            self.assertNotIn('/etc/rc.common /etc/init.d/xbox-accel stop', shell('ps w').stdout)
            shell(f'kill -0 {holder}')
        finally:
            shell(f'kill -TERM {holder}', check=False)
        shell('/etc/init.d/xbox-accel stop')

    def test_02_stop_returns_teardown_failure(self):
        try:
            shell('uci set xbox-accel.main.dnsmasq_instance=missing_stop_fixture; uci commit xbox-accel')
            result = shell('/etc/init.d/xbox-accel stop', check=False)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn('missing_stop_fixture', result.stdout + result.stderr)
        finally:
            shell(f'cp {self.work}/config /etc/config/xbox-accel')

    def test_03_stop_finds_unregistered_resolver_with_missing_or_wrong_pid(self):
        system_pids = shell('for f in /var/run/dnsmasq/*.pid; do cat "$f"; done').stdout.split()
        for cache in ('missing', 'wrong'):
            with self.subTest(cache=cache):
                shell('mkdir -p /var/etc/xbox-accel /var/run/xbox-accel; '
                      "printf 'port=5547\nlisten-address=127.0.0.1\nbind-interfaces\nno-resolv\nno-hosts\n' > /var/etc/xbox-accel/dnsmasq.conf")
                pid = shell('/usr/sbin/dnsmasq -k -C /var/etc/xbox-accel/dnsmasq.conf >/dev/null 2>&1 & echo $!').stdout.strip()
                try:
                    shell(f'kill -0 {pid}')
                    shell("for n in 1 2 3 4 5; do netstat -ln | grep -q ':5547 ' && exit 0; sleep 1; done; exit 1")
                    if cache == 'wrong':
                        shell('echo 1 > /var/run/xbox-accel/dnsmasq.pid')
                    else:
                        shell('rm -f /var/run/xbox-accel/dnsmasq.pid')
                    shell('/etc/init.d/xbox-accel stop')
                    shell(f'for n in 1 2 3 4 5; do kill -0 {pid} 2>/dev/null || exit 0; sleep 1; done; exit 1')
                    shell('kill -0 1')
                    for system_pid in system_pids:
                        shell(f'kill -0 {system_pid}')
                finally:
                    shell(f'kill -TERM {pid}', check=False)
                    shell('rm -f /var/run/xbox-accel/dnsmasq.pid')


if __name__ == '__main__':
    unittest.main()
