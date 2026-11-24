#!/usr/bin/env python3
"""平台集成回归，仅运行于可丢弃的 OpenWrt Docker 容器。
python3 tests/test_platforms_openwrt.py xa-platform-test
需要插件、bind-dig、openssl-util、python3-light 和 python3-openssl。
"""
import copy
import json
import shlex
import subprocess
import sys
import time
import unittest
from test_platforms import function, helpers, SWITCH_TEST_HOSTS, SWITCH_GAME_HOSTS

CONTAINER = sys.argv.pop(1) if len(sys.argv) > 1 else 'xa-platform-test'
if not CONTAINER.startswith('xa-'):
    raise SystemExit('仅允许 xa- 前缀的测试容器')
MOCK = '/tmp/xa-platform-mock'
PREFIX = 'PATH=' + MOCK + ':$PATH '
GROUPS = ('store', 'ps', 'xbox_old', 'switch')
IPS = {'store': '9.9.9.9', 'ps': '8.8.4.4', 'xbox_old': '1.1.1.1', 'switch': '208.67.222.222'}
GAME_HOST = 'atum.hac.lp1.d4c.nintendo.net'
GAME_URL = 'https://' + GAME_HOST + '/private-game-path?token=PRIVATE_GAME_TOKEN'


def run(command, check=True, timeout=90):
    result = subprocess.run(['docker', 'exec', CONTAINER, 'sh', '-c', command], text=True,
                            capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise AssertionError(f'{command}\nexit={result.returncode}\n{result.stdout}\n{result.stderr}')
    return result


def write(path, content, executable=False):
    command = 'cat > ' + shlex.quote(path)
    if executable:
        command += '; chmod 755 ' + shlex.quote(path)
    result = subprocess.run(['docker', 'exec', '-i', CONTAINER, 'sh', '-c', command],
                            input=content, text=True, capture_output=True, timeout=20)
    if result.returncode:
        raise AssertionError(result.stderr)


def response():
    return {'code': '200', 'data': [{'name': 'Microsoft.WindowsStore.msixbundle', 'size': 157286400,
            'url': 'https://dl.delivery.mp.microsoft.com/store/private-resource.pkg?P1=' +
                   str(int(time.time()) + 86400) + '&P4=PRIVATE_STORE_SIGNATURE'}]}


MOCK_CURL = r'''#!/bin/sh
mode=$(cat /tmp/xa-platform-mock/mode 2>/dev/null)
output=; previous=; range=0-1023; url=
for argument in "$@"; do
    [ "$previous" != -o ] || output=$argument
    [ "$previous" != -r ] || range=$argument
    previous=$argument; url=$argument
done
case "$url" in
*Game/GetAppPackage*)
    printf '%s\n' "$*" >> /tmp/xa-platform-mock/api.log
    [ "$mode" != api_fail ] || { echo 'fixture API unavailable' >&2; exit 22; }
    cp /tmp/xa-platform-mock/response.json "$output"; exit 0 ;;
*119.29.29.29/d*)
    case "$url" in
    *microsoft.com*) echo 9.9.9.9 ;; *playstation.net*) echo 8.8.4.4 ;;
    *dlassets*) echo 1.1.1.1 ;; *nintendo.net*) echo 208.67.222.222 ;; *) echo 9.9.9.9 ;;
    esac
    exit 0 ;;
*xbox-accel/selftest*) exec /usr/bin/curl "$@" ;;
esac
printf '%s\n' "$*" >> /tmp/xa-platform-mock/probes.log
[ "$mode" != slow ] || sleep 30
case "$url" in
*atum.hac.lp1.d4c.nintendo.net*) [ "$mode" != game_fail ] || { printf '403 0 0 0.1 0.2 208.67.222.222'; exit 0; } ;;
*ctest-dl-lp1.cdn.nintendo.net*) [ "$mode" != public_fail ] || { printf '403 0 0 0.1 0.2 208.67.222.222'; exit 0; } ;;
esac
case "$url" in
*microsoft.com*) ip=9.9.9.9 ;; *playstation.net*) ip=8.8.4.4 ;;
*dlassets*) ip=1.1.1.1 ;; *nintendo.net*) ip=208.67.222.222 ;; *) ip=9.9.9.9 ;;
esac
start=${range%-*}; end=${range#*-}; bytes=$((end-start+1))
printf '206 %s 12000000 0.1 0.2 %s' "$bytes" "$ip"
'''
MOCK_NSLOOKUP = r'''#!/bin/sh
case "$*" in *127.0.0.1*) exec /usr/bin/nslookup "$@" ;; esac
case "$*" in
*microsoft.com*) host=dl.delivery.mp.microsoft.com; ip=9.9.9.9 ;;
*playstation.net*) host=gst.prod.dl.playstation.net; ip=8.8.4.4 ;;
*dlassets*) host=dlassets.xboxlive.cn; ip=1.1.1.1 ;;
*nintendo.net*) host=ctest-dl-lp1.cdn.nintendo.net; ip=208.67.222.222 ;;
*) host=assets1.xboxlive.cn; ip=9.9.9.9 ;;
esac
printf 'Name: %s\nAddress: %s\n' "$host" "$ip"
'''


class PlatformIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run('test -f /etc/openwrt_release && test -f /usr/share/xbox-accel/platforms.sh')
        run('xbox-accel disable >/dev/null; cp /etc/config/xbox-accel /tmp/xa-platform-config-backup; mkdir -p ' + MOCK)
        write(MOCK + '/curl', MOCK_CURL, True)
        write(MOCK + '/nslookup', MOCK_NSLOOKUP, True)

    @classmethod
    def tearDownClass(cls):
        run('xbox-accel disable >/dev/null; cp /tmp/xa-platform-config-backup /etc/config/xbox-accel')

    def setUp(self):
        run('xbox-accel cancel >/dev/null; xbox-accel disable >/dev/null; cp /tmp/xa-platform-config-backup /etc/config/xbox-accel')
        options = {'enabled': 0, 'optimize_cn': 0, 'optimize_com': 0, 'redirect_cn': 0,
                   'use_seeds': 0, 'use_isp_dns': 0, 'passwall_direct': 0, 'max_candidates': 2,
                   'quick_size_mb': 1, 'full_size_mb': 1, 'top_n': 1, 'store_test_app': 'store',
                   'probe_dns': '223.5.5.5', 'health_interval': 0, **{'optimize_' + g: 1 for g in GROUPS}}
        command = '; '.join('uci set xbox-accel.main.' + key + '=' + shlex.quote(str(value)) for key, value in options.items())
        run(command + '; uci -q delete xbox-accel.main.verification_urls_switch; uci commit xbox-accel; xbox-accel reset >/dev/null; rm -f ' + MOCK + '/api.log ' + MOCK + '/probes.log')
        write(MOCK + '/mode', 'good\n')
        write(MOCK + '/response.json', json.dumps(response()))

    def tearDown(self):
        run('xbox-accel cancel >/dev/null; xbox-accel disable >/dev/null')

    def select(self, group='all', check=True):
        return run(PREFIX + 'xbox-accel test ' + group, check=check)

    def record(self):
        return json.loads(run('cat /tmp/xbox-accel/live-task.json').stdout)

    def status(self):
        result = run('xbox-accel json_status')
        self.assertTrue(result.stdout.strip(), '状态 JSON 为空：' + result.stderr[:500])
        return json.loads(result.stdout)

    def action(self, command):
        request = shlex.quote(json.dumps({'command': command}))
        return json.loads(run("printf '%s\\n' " + request + ' | ' + PREFIX + '/usr/libexec/rpcd/xbox-accel call action').stdout)

    def assert_game_dns(self, hosts=SWITCH_GAME_HOSTS):
        for host in hosts:
            for port in (53, 5335):
                query = 'dig @127.0.0.1 -p ' + str(port) + ' ' + host
                self.assertEqual(run(query + ' A +short').stdout.strip(), IPS['switch'])
                aaaa = run(query + ' AAAA +noall +comments +answer').stdout
                self.assertIn('status: NOERROR', aaaa)
                self.assertIn('ANSWER: 0', aaaa)

    def wait(self, task_id, stage=None, timeout=40):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self.record()
            if current.get('id') == task_id:
                if stage and current.get('stage') == stage:
                    return current
                if not stage and current.get('status') not in ('queued', 'running'):
                    return current
            time.sleep(0.15)
        self.fail('后台任务未在期限内完成目标状态')

    def test_01_four_groups_select_and_keep_independent_results(self):
        self.select()
        record = self.record()
        self.assertEqual(record['status'], 'success')
        self.assertEqual(set(record['groups']), {'cn', 'com', *GROUPS})
        for group in GROUPS:
            self.assertEqual(record['groups'][group]['status'], 'success')
            self.assertEqual(run('cat /etc/xbox-accel/best_' + group).stdout.strip(), IPS[group])
        self.assertEqual(record['groups']['cn']['code'], 'NOT_REQUESTED')
        status = self.status()
        switch = status['platforms']['switch']
        self.assertEqual(switch['game_status'], 'default')
        self.assertEqual((switch['game_mapped'], switch['game_default_mapped'], switch['game_total']), (3, 3, 3))
        self.assertEqual((switch['game_configured'], switch['game_verified']), (0, 0))
        for host in SWITCH_GAME_HOSTS:
            self.assertIn(host, switch['domains'].split())
        self.assertEqual(run('cat /etc/xbox-accel/resources_switch').stdout, '')
        self.assertNotIn('ares.dl.playstation.net', status['platforms']['ps']['domains'])
        probes = run('cat ' + MOCK + '/probes.log').stdout
        for host in SWITCH_GAME_HOSTS:
            self.assertNotIn(host, probes)
        self.assertIn('dl.delivery.mp.microsoft.com:443:9.9.9.9', probes)
        self.assertIn('-A XboxDownload/Nintendo NX', probes)
        for host in ('gs2.ww.prod.dl.playstation.net', 'zeus.dl.playstation.net'):
            self.assertNotIn('/networktest/get_192m', next(line for line in probes.splitlines() if host in line))
        self.assertTrue(run('ls -l /tmp/xbox-accel/store-resource.json').stdout.startswith('-rw-------'))

    def test_02_store_cache_refreshes_and_fetch_failure_preserves_state(self):
        self.select('store')
        before = run('sha256sum /etc/xbox-accel/best_store /etc/xbox-accel/domains_store').stdout
        write('/tmp/xbox-accel/store-resource.json', json.dumps({'app': 'store', 'size': 157286400,
              'url': 'https://dl.delivery.mp.microsoft.com/store/file?P1=1&P4=expired', 'expires_at': 1}))
        self.select('store')
        self.assertGreaterEqual(len(run('cat ' + MOCK + '/api.log').stdout.splitlines()), 2)
        run('rm -f /tmp/xbox-accel/store-resource.json')
        write(MOCK + '/mode', 'api_fail\n')
        self.assertNotEqual(self.select('store', check=False).returncode, 0)
        self.assertEqual(self.record()['groups']['store']['code'], 'STORE_LINK_FETCH_FAILED')
        self.assertEqual(run('sha256sum /etc/xbox-accel/best_store /etc/xbox-accel/domains_store').stdout, before)

    def test_03_store_rejects_bad_domain_expiry_size_and_data_shape(self):
        self.select('store')
        before = run('sha256sum /etc/xbox-accel/best_store /etc/xbox-accel/domains_store').stdout
        base = response()
        host = copy.deepcopy(base); host['data'][0]['url'] = 'https://evil.example/file?P1=1999999999&P4=value'
        expiry = copy.deepcopy(base); expiry['data'][0]['url'] = 'https://dl.delivery.mp.microsoft.com/file?P1=1&P4=expired'
        size = copy.deepcopy(base); size['data'][0]['size'] = 1024
        shape = {'code': 200, 'data': {'first': base['data'][0]}}
        for invalid in (host, expiry, size, shape):
            run('rm -f /tmp/xbox-accel/store-resource.json')
            write(MOCK + '/response.json', json.dumps(invalid))
            self.assertNotEqual(self.select('store', check=False).returncode, 0)
            self.assertEqual(self.record()['groups']['store']['code'], 'STORE_LINK_INVALID')
            self.assertEqual(run('sha256sum /etc/xbox-accel/best_store /etc/xbox-accel/domains_store').stdout, before)

    def test_04_switch_apply_validates_game_link_and_changes_require_validation(self):
        self.select()
        run('uci set xbox-accel.main.enabled=1; uci add_list xbox-accel.main.verification_urls_switch=' + shlex.quote(GAME_URL) + '; uci commit xbox-accel')
        admitted = self.action('apply')
        self.assertTrue(admitted['accepted'])
        ended = self.wait(admitted['task_id'])
        self.assertEqual(ended['kind'], 'test')
        self.assertIn(ended['status'], ('success', 'partial'))
        status = self.status()['platforms']['switch']
        self.assertEqual(status['game_status'], 'verified')
        self.assertEqual((status['game_mapped'], status['game_default_mapped'], status['game_configured'], status['game_verified']), (3, 2, 1, 1))
        self.assertIn(GAME_HOST, status['domains'])
        self.assertNotIn('PRIVATE_GAME_TOKEN', run('cat /etc/xbox-accel/resources_switch').stdout)
        self.assertEqual(run('dig @127.0.0.1 ' + GAME_HOST + ' A +short').stdout.strip(), IPS['switch'])
        run('uci delete xbox-accel.main.verification_urls_switch; uci add_list xbox-accel.main.verification_urls_switch=' + shlex.quote(GAME_URL + '&new=1') + '; uci commit xbox-accel')
        status = self.status()['platforms']['switch']
        self.assertEqual(status['game_status'], 'pending')
        self.assertNotIn(GAME_HOST, status['domains'])
        self.assertEqual((status['game_mapped'], status['game_default_mapped'], status['game_verified']), (2, 2, 0))
        self.assertTrue(self.status()['last_application']['stale'])
        admitted = self.action('apply')
        self.assertTrue(admitted['accepted'])
        self.assertEqual(self.wait(admitted['task_id'])['kind'], 'test')
        self.assertEqual(self.status()['platforms']['switch']['game_verified'], 1)
        self.assert_game_dns()

    def test_05_switch_game_failure_is_partial_and_public_probe_stays_available(self):
        run('uci add_list xbox-accel.main.verification_urls_switch=' + shlex.quote(GAME_URL) + '; uci commit xbox-accel')
        self.select('switch')
        write(MOCK + '/mode', 'game_fail\n')
        self.assertEqual(self.select('switch', check=False).returncode, 2)
        self.assertEqual(self.record()['status'], 'partial')
        self.assertEqual(self.record()['groups']['switch']['code'], 'PARTIAL_COVERAGE')
        state = self.status()['platforms']['switch']
        self.assertIn('ctest-dl-lp1.cdn.nintendo.net', state['domains'])
        self.assertNotIn(GAME_HOST, state['domains'])
        self.assertEqual(state['game_status'], 'pending')
        self.assertEqual(state['ip'], IPS['switch'])
        self.assertEqual((state['game_mapped'], state['game_default_mapped'], state['game_verified']), (2, 2, 0))
        run('uci set xbox-accel.main.enabled=1; uci commit xbox-accel; ' + PREFIX + '/etc/init.d/xbox-accel reload')
        automatic = self.record()
        self.assertEqual(automatic['trigger'], 'first_run')
        self.wait(automatic['id'])
        self.assertNotIn(GAME_HOST, run('cat /etc/xbox-accel/main_rules /var/etc/xbox-accel/dnsmasq.conf').stdout)
        self.assert_game_dns(SWITCH_GAME_HOSTS[1:])

    def test_06_rpc_group_actions_and_cancellation_finish_group_records(self):
        for group in GROUPS:
            admitted = self.action('test_' + group)
            self.assertTrue(admitted['accepted'])
            ended = self.wait(admitted['task_id'])
            self.assertEqual(ended['groups'][group]['status'], 'success')
            for other in GROUPS:
                if other != group:
                    self.assertEqual(ended['groups'][other]['code'], 'NOT_REQUESTED')
        write(MOCK + '/mode', 'slow\n')
        admitted = self.action('test_switch')
        self.wait(admitted['task_id'], stage='quick_switch')
        self.assertTrue(self.action('cancel')['ok'])
        self.assertEqual(self.wait(admitted['task_id'])['groups']['switch']['status'], 'cancelled')
        for command in ('test_ea', 'test_battle', 'test_ps; reboot'):
            self.assertFalse(self.action(command)['ok'])

    def test_07_main_dns_aaaa_group_disable_and_full_cleanup(self):
        self.select()
        run('uci set xbox-accel.main.enabled=1; uci commit xbox-accel; /etc/init.d/xbox-accel reload')
        for host, group in [('dl.delivery.mp.microsoft.com', 'store'), ('tlu.dl.delivery.mp.microsoft.com', 'store'),
                            ('gst.prod.dl.playstation.net', 'ps'), ('dlassets2.xboxlive.cn', 'xbox_old'),
                            ('ctest-dl-lp1.cdn.nintendo.net', 'switch')]:
            self.assertEqual(run('dig @127.0.0.1 ' + host + ' A +short').stdout.strip(), IPS[group])
            aaaa = run('dig @127.0.0.1 ' + host + ' AAAA +noall +comments +answer').stdout
            self.assertIn('status: NOERROR', aaaa)
            self.assertIn('ANSWER: 0', aaaa)
        self.assert_game_dns()
        run('uci set xbox-accel.main.optimize_store=0; uci commit xbox-accel; /etc/init.d/xbox-accel reload')
        self.assertNotIn('delivery.mp.microsoft.com', run('cat /etc/xbox-accel/main_rules').stdout)
        self.assertNotIn('delivery.mp.microsoft.com', run('cat /var/etc/xbox-accel/dnsmasq.conf').stdout)
        self.assertIn('playstation.net', run('cat /etc/xbox-accel/main_rules').stdout)
        run('uci set xbox-accel.main.optimize_switch=0; uci commit xbox-accel; /etc/init.d/xbox-accel reload')
        self.assertNotIn('nintendo.net', run('cat /etc/xbox-accel/main_rules /var/etc/xbox-accel/dnsmasq.conf').stdout)
        self.assertIn('playstation.net', run('cat /etc/xbox-accel/main_rules').stdout)
        run('xbox-accel disable >/dev/null')
        self.assertNotIn('127.0.0.1#5335', run("uci -q get 'dhcp.@dnsmasq[0].server'", check=False).stdout)

    def test_08_generated_parent_rules_forward_other_children_and_preserve_ipv6(self):
        self.select('store')
        work = '/tmp/xa-platform-dns'
        run('mkdir -p ' + work)
        body = 'cd ' + work + '\nPLATFORM_LIB=/usr/share/xbox-accel/platforms.sh\n' + helpers() + '\n' + function('gen_resolver_conf') + '''
cp /etc/xbox-accel/best_store state/best_store
cp /etc/xbox-accel/domains_store state/domains_store
echo 0 > config/optimize_cn; echo 0 > config/optimize_com; echo 1 > config/optimize_store
echo 5458 > config/resolver_port
gen_resolver_conf > resolver.conf
sed -i 's/^server=223.5.5.5$/server=127.0.0.1#5459/;s/^server=119.29.29.29$/server=127.0.0.1#5459/' resolver.conf
'''
        write(work + '/generate.sh', body)
        run('sh ' + work + '/generate.sh')
        upstream = ('port=5459\nlisten-address=127.0.0.1\nbind-interfaces\nno-resolv\nno-hosts\npid-file=' + work + '/upstream.pid\n'
                    'host-record=other.dl.delivery.mp.microsoft.com,4.3.2.2,2001:db8::2\nlocal=/other.dl.delivery.mp.microsoft.com/\n')
        write(work + '/upstream.conf', upstream)
        try:
            run('dnsmasq -C ' + work + '/upstream.conf; dnsmasq -C ' + work + '/resolver.conf')
            self.assertEqual(run('dig @127.0.0.1 -p 5458 dl.delivery.mp.microsoft.com A +short').stdout.strip(), IPS['store'])
            aaaa = run('dig @127.0.0.1 -p 5458 dl.delivery.mp.microsoft.com AAAA +noall +comments').stdout
            self.assertIn('status: NOERROR', aaaa)
            self.assertIn('ANSWER: 0', aaaa)
            self.assertEqual(run('dig @127.0.0.1 -p 5458 other.dl.delivery.mp.microsoft.com A +short').stdout.strip(), '4.3.2.2')
            self.assertEqual(run('dig @127.0.0.1 -p 5458 other.dl.delivery.mp.microsoft.com AAAA +short').stdout.strip(), '2001:db8::2')
        finally:
            run('kill "$(cat ' + work + '/upstream.pid)" "$(cat ' + work + '/run/dnsmasq.pid)"', check=False)

    def test_09_https_probe_uses_real_tls_and_range_with_certificate_validation(self):
        work = '/tmp/xa-platform-tls'
        run('mkdir -p ' + work + '/www; dd if=/dev/zero of=' + work + '/www/resource bs=1024 count=4 2>/dev/null')
        run('openssl req -x509 -newkey rsa:2048 -nodes -keyout ' + work + '/key.pem -out ' + work + '/cert.pem -days 1 '
            "-subj '/CN=gst.prod.dl.playstation.net' -addext 'subjectAltName=DNS:gst.prod.dl.playstation.net' >" + work + '/openssl.log 2>&1')
        write(work + '/probe.sh', 'TMP_DIR=' + work + '\n' + function('probe') + '\nprobe "$@"\n')
        write(work + '/server.py', '''import socket, ssl, sys
root=sys.argv[1]
context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(root+'/cert.pem',root+'/key.pem')
def sni(conn,name,ctx):
    with open(root+'/sni.log','a') as out: out.write(str(name)+'\\n')
context.sni_callback=sni
listener=socket.socket(); listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
listener.bind(('127.0.0.1',443)); listener.listen(5)
while True:
    conn,_=listener.accept()
    try:
        with context.wrap_socket(conn,server_side=True) as secured:
            request=secured.recv(8192)
            secured.sendall(b'HTTP/1.1 206 Partial Content\\r\\nContent-Length: 1024\\r\\nContent-Range: bytes 0-1023/4096\\r\\nConnection: close\\r\\n\\r\\n'+b'x'*1024)
    except (ssl.SSLError, OSError): conn.close()
''')
        run('/etc/init.d/uhttpd stop')
        try:
            run('python3 ' + work + '/server.py ' + work + ' >' + work + '/server.log 2>&1 & echo "$!" > ' + work + '/server.pid')
            time.sleep(0.2)
            args = '127.0.0.1 gst.prod.dl.playstation.net /resource 0 1024 5 https'
            self.assertEqual(run('sh ' + work + '/probe.sh ' + args).stdout.split()[3], '60')
            success = run('CURL_CA_BUNDLE=' + work + '/cert.pem sh ' + work + '/probe.sh ' + args).stdout.split()
            self.assertEqual(success[0:2], ['206', '1024'])
            self.assertEqual(success[3], '0')
            self.assertEqual(success[6], '127.0.0.1')
            self.assertIn('gst.prod.dl.playstation.net', run('cat ' + work + '/sni.log').stdout)
            wrong = run('CURL_CA_BUNDLE=' + work + '/cert.pem sh ' + work + '/probe.sh ' + args.replace('gst.prod.dl.playstation.net', 'gs2.ww.prod.dl.playstation.net')).stdout.split()
            self.assertEqual(wrong[3], '60')
        finally:
            run('kill "$(cat ' + work + '/server.pid)"', check=False)
            run('/etc/init.d/uhttpd start')

    def test_10_export_preserves_platform_status_and_redacts_links(self):
        self.select()
        run('uci add_list xbox-accel.main.verification_urls_switch=' + shlex.quote(GAME_URL) + '; uci commit xbox-accel')
        self.select('switch')
        admitted = self.action('export')
        self.assertEqual(self.wait(admitted['task_id'])['code'], 'REPORT_READY')
        content = run('cat /tmp/xbox-accel/diagnostic-report.json').stdout
        report = json.loads(content)
        self.assertTrue(report['redacted'])
        for group in GROUPS:
            self.assertIn('optimize_' + group, report['configuration'])
        for value in ('PRIVATE_GAME_TOKEN', 'PRIVATE_STORE_SIGNATURE', '/private-game-path', '/store/private-resource.pkg'):
            self.assertNotIn(value, content)
        self.assertEqual(report['current_checks']['summary']['clients'], 'unknown')

    def test_11_blocked_syslog_keeps_partial_results_and_bounded_cancellation(self):
        write(MOCK + '/logger', '#!/bin/sh\nexec sleep 30\n', True)
        try:
            run('uci add_list xbox-accel.main.verification_urls_switch=' + shlex.quote(GAME_URL) + '; uci commit xbox-accel')
            self.select('switch')
            write(MOCK + '/mode', 'game_fail\n')
            started = time.monotonic()
            self.assertEqual(self.select('switch', check=False).returncode, 2)
            self.assertLess(time.monotonic() - started, 12)
            self.assertEqual(self.record()['groups']['switch']['status'], 'partial')
            write(MOCK + '/mode', 'slow\n')
            admitted = self.action('test_switch')
            self.assertTrue(admitted['accepted'])
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if self.record()['groups']['switch']['status'] == 'running':
                    break
                time.sleep(0.1)
            started = time.monotonic()
            run(PREFIX + 'xbox-accel cancel')
            self.assertLess(time.monotonic() - started, 12)
            ended = self.wait(admitted['task_id'])
            self.assertEqual(ended['status'], 'cancelled')
            self.assertNotIn(ended['groups']['switch']['status'], ('queued', 'running'))
        finally:
            run('rm -f ' + MOCK + '/logger')

    def test_12_legacy_public_result_applies_default_game_dns_without_retesting(self):
        run('uci set xbox-accel.main.enabled=1; uci set xbox-accel.main.optimize_store=0; '
            'uci set xbox-accel.main.optimize_ps=0; uci set xbox-accel.main.optimize_xbox_old=0; uci commit xbox-accel')
        write('/etc/xbox-accel/best_switch', IPS['switch'] + '\n')
        write('/etc/xbox-accel/domains_switch', SWITCH_TEST_HOSTS[0] + '\n')
        admitted = self.action('apply')
        self.assertTrue(admitted['accepted'])
        ended = self.wait(admitted['task_id'])
        self.assertEqual(ended['kind'], 'apply')
        self.assertIn(ended['status'], ('success', 'partial'))
        state = self.status()['platforms']['switch']
        self.assertEqual((state['game_mapped'], state['game_default_mapped'], state['game_verified']), (3, 3, 0))
        self.assertEqual(run('cat /etc/xbox-accel/domains_switch').stdout.strip(), SWITCH_TEST_HOSTS[0])
        self.assert_game_dns()
        checks = json.loads(run('cat /etc/xbox-accel/last-application.json').stdout)
        optional = [row for row in checks['environment'] if row['code'] == 'OPTIONAL_GAME_RESOURCE']
        self.assertEqual(len(optional), 3)
        self.assertTrue(all(row['level'] == 'skipped' and row['scope'] == 'measurement' for row in optional))
        self.assertEqual(checks['summary']['measurement'], 'verified')
        for host in SWITCH_GAME_HOSTS:
            dns_rows = [row for row in checks['environment'] if row['name'].startswith(('A ' + host + ' @', 'AAAA ' + host + ' @'))]
            self.assertEqual(len(dns_rows), 4)
            self.assertTrue(all(row['level'] == 'ok' for row in dns_rows))
            self.assertNotIn(host, run('cat ' + MOCK + '/probes.log').stdout)

    def test_13_removing_failed_game_link_restores_default_mapping_and_health(self):
        run('uci set xbox-accel.main.optimize_store=0; uci set xbox-accel.main.optimize_ps=0; '
            'uci set xbox-accel.main.optimize_xbox_old=0; uci add_list xbox-accel.main.verification_urls_switch=' +
            shlex.quote(GAME_URL) + '; uci commit xbox-accel')
        self.select('switch')
        write(MOCK + '/mode', 'game_fail\n')
        self.assertEqual(self.select('switch', check=False).returncode, 2)
        run('uci set xbox-accel.main.enabled=1; uci commit xbox-accel; ' + PREFIX + '/etc/init.d/xbox-accel reload')
        automatic = self.record()
        self.assertEqual(automatic['trigger'], 'first_run')
        self.wait(automatic['id'])
        self.assertNotIn(GAME_HOST, self.status()['platforms']['switch']['domains'].split())
        run('uci delete xbox-accel.main.verification_urls_switch; uci commit xbox-accel; rm -f ' + MOCK + '/probes.log')
        admitted = self.action('apply')
        self.assertTrue(admitted['accepted'], admitted)
        ended = self.wait(admitted['task_id'])
        self.assertEqual((ended['kind'], ended['status']), ('apply', 'success'))
        state = self.status()['platforms']['switch']
        self.assertEqual((state['game_mapped'], state['game_default_mapped'], state['game_configured']), (3, 3, 0))
        self.assert_game_dns()
        run(PREFIX + 'xbox-accel health')
        self.assertEqual(self.record()['groups']['switch']['code'], 'HEALTH_OK')
        probes = run('cat ' + MOCK + '/probes.log').stdout
        for host in SWITCH_GAME_HOSTS:
            self.assertNotIn(host, probes)

    def test_14_public_resource_failure_preserves_default_game_result(self):
        self.select('switch')
        before = run('sha256sum /etc/xbox-accel/best_switch /etc/xbox-accel/domains_switch /etc/xbox-accel/resources_switch').stdout
        write(MOCK + '/mode', 'public_fail\n')
        self.assertNotEqual(self.select('switch', check=False).returncode, 0)
        self.assertEqual(self.record()['groups']['switch']['code'], 'QUICK_TEST_FAILED')
        self.assertEqual(run('sha256sum /etc/xbox-accel/best_switch /etc/xbox-accel/domains_switch /etc/xbox-accel/resources_switch').stdout, before)
        state = self.status()['platforms']['switch']
        self.assertEqual((state['game_mapped'], state['game_default_mapped']), (3, 3))


if __name__ == '__main__':
    unittest.main(verbosity=2)
