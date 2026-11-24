#!/usr/bin/env python3
"""平台域名、资源隔离、私有链接与状态提交的主机回归检查。"""
import os
import pathlib
import re
import subprocess
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CORE = ROOT / 'files/usr/bin/xbox-accel'
PLATFORMS = ROOT / 'files/usr/share/xbox-accel/platforms.sh'
DIAGNOSTICS = PLATFORMS.with_name('diagnostics.sh')
SWITCH_TEST_HOSTS = ('ctest-dl-lp1.cdn.nintendo.net', 'ctest-dl.p01.lp1.ctest.srv.nintendo.net')
SWITCH_GAME_HOSTS = ('atum.hac.lp1.d4c.nintendo.net', 'nemof.p01.lp1.nemo.srv.nintendo.net',
                     'nemof.hac.lp1.nemo.srv.nintendo.net')


def function(name):
    source = CORE.read_text()
    match = re.search(r'^' + re.escape(name) + r'\(\) \{[^\n]*\}', source, re.M)
    if not match:
        match = re.search(r'^' + re.escape(name) + r'\(\) \{\n.*?^\}', source, re.M | re.S)
    if not match:
        raise ValueError(name)
    return match.group(0)


def helpers():
    constants = '\n'.join(line for line in CORE.read_text().splitlines()
                          if re.match(r'^(CN_DOMAINS|COM_DOMAINS|CDN_HOSTS|SRV_NAMES|DEF_URL_CN|DEF_URL_COM)=', line))
    names = ('url_scheme', 'url_host', 'url_path', 'ipv4s', 'public_only', 'uniq_keep_order',
             'to_int', 'cfg_bool', 'group_url', 'group_offset', 'best_ip', 'best_domains',
             'groups_enabled', 'groups_active', 'pin', 'pin_exact', 'probe')
    return constants + '\n' + '\n'.join(function(name) for name in names) + '''
STATE_DIR=$PWD/state; TMP_DIR=$PWD/tmp; RUN_DIR=$PWD/run
mkdir -p "$STATE_DIR" "$TMP_DIR" "$RUN_DIR" config
cfg() { if [ -s "config/$1" ]; then cat "config/$1"; else printf '%s\\n' "$2"; fi; }
com_mode() { if cfg_bool optimize_com 1; then echo optimize; else echo off; fi; }
die() { printf '%s\\n' "$*" >&2; exit 1; }
. "$PLATFORM_LIB"
'''


class PlatformTests(unittest.TestCase):
    def shell(self, body, *args, fixture=None, check=True):
        with tempfile.TemporaryDirectory() as directory:
            if fixture:
                fixture(pathlib.Path(directory))
            result = subprocess.run(['sh', '-c', helpers() + '\n' + body, 'platform-test', *map(str, args)],
                                    cwd=directory, env={**os.environ, 'PLATFORM_LIB': str(PLATFORMS)},
                                    capture_output=True, text=True, timeout=15)
            if check:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result

    def test_new_groups_opt_in_and_independent(self):
        result = self.shell('''
groups_enabled
printf '1\\n' > config/optimize_store
printf '1\\n' > config/optimize_switch
groups_active
''')
        self.assertEqual(result.stdout.splitlines(), ['cn', 'com', 'cn', 'com', 'store', 'switch'])

    def test_urls_keep_https_and_reject_unknown_authorities(self):
        valid = 'https://dl.delivery.mp.microsoft.com/file.pkg?P1=1999999999&P4=value'
        result = self.shell('url_scheme "$1"; url_host "$1"; url_path "$1"; platform_url_valid store "$1"', valid)
        self.assertEqual(result.stdout.splitlines(), ['https', 'dl.delivery.mp.microsoft.com', '/file.pkg?P1=1999999999&P4=value'])
        for url in ['https://user:secret@dl.delivery.mp.microsoft.com/file',
                    'https://dl.delivery.mp.microsoft.com.evil.example/file',
                    'https://dl.delivery.mp.microsoft.com:8443/file',
                    'https://dl.delivery.mp.microsoft.com/file#fragment',
                    'https://dl.delivery.mp.microsoft.com/file\nHeader: value',
                    'file:///etc/passwd']:
            with self.subTest(url=url):
                self.assertNotEqual(self.shell('platform_url_valid store "$1"', url, check=False).returncode, 0)

    def test_ps_uses_a_distinct_resource_for_each_domain(self):
        result = self.shell('''
for host in $PS_DOMAINS; do domain_url ps "$host" || echo awaiting-resource; done
''')
        urls = result.stdout.splitlines()
        self.assertTrue(urls[0].endswith('/networktest/get_192m'))
        self.assertIn('gs2.ww.prod.dl.playstation.net/gs2/appkgo/', urls[1])
        self.assertIn('zeus.dl.playstation.net/cdn/', urls[2])
        self.assertEqual(urls[3], 'awaiting-resource')

    def test_candidate_limit_keeps_the_primary_resource_dns_answer(self):
        result = self.shell(function('discover') + '\n' + function('candidates') + '''
printf '1\\n' > config/max_candidates
printf '0\\n' > config/use_isp_dns
printf '0\\n' > config/use_seeds
printf '223.5.5.5\\n' > config/probe_dns
printf 'https://tlu.dl.delivery.mp.microsoft.com/file.pkg\\n' > config/test_url_store
printf 'http://zeus.dl.playstation.net/game.pkg\\n' > config/test_url_ps
printf 'http://ctest-dl.p01.lp1.ctest.srv.nintendo.net/30m\\n' > config/test_url_switch
ecs_subnets() { :; }; curl() { :; }; task_event() { :; }
log_event_enabled() { return 1; }
nslookup() {
    case "$3" in
    tlu.dl.delivery.mp.microsoft.com|zeus.dl.playstation.net|ctest-dl.p01.lp1.ctest.srv.nintendo.net)
        echo 'Address: 9.9.9.9' ;;
    *) echo 'Address: 8.8.4.4' ;;
    esac
}
for group in store ps switch; do candidates "$group"; done
''')
        self.assertEqual(result.stdout.splitlines(), ['9.9.9.9'] * 3)

    def test_switch_game_link_requires_matching_validation_evidence(self):
        url = 'https://atum.hac.lp1.d4c.nintendo.net/private/game?token=PRIVATE_TOKEN'
        result = self.shell('''
printf '%s\\n' "$1" > config/verification_urls_switch
echo '208.67.222.222' > state/best_switch
echo 'ctest-dl-lp1.cdn.nintendo.net atum.hac.lp1.d4c.nintendo.net' > state/domains_switch
pinned_domains switch
group_result_ready switch; echo before:$?
printf '%s %s\\n' atum.hac.lp1.d4c.nintendo.net "$(resource_hash "$1")" > state/resources_switch
pinned_domains switch
group_result_ready switch; echo verified:$?
printf '%s/new-path\\n' "$1" > config/verification_urls_switch
pinned_domains switch
group_result_ready switch; echo changed:$?
rm config/verification_urls_switch
pinned_domains switch
group_result_ready switch; echo removed:$?
if grep -q PRIVATE_TOKEN state/resources_switch; then exit 1; fi
''', url)
        self.assertEqual(result.stdout.splitlines(), [
            SWITCH_TEST_HOSTS[0], *SWITCH_GAME_HOSTS[1:], 'before:1',
            SWITCH_TEST_HOSTS[0], *SWITCH_GAME_HOSTS, 'verified:0',
            SWITCH_TEST_HOSTS[0], *SWITCH_GAME_HOSTS[1:], 'changed:1',
            SWITCH_TEST_HOSTS[0], *SWITCH_GAME_HOSTS, 'removed:0'])

    def test_switch_default_domain_group_keeps_game_resource_optional(self):
        result = self.shell('''
configured_domains switch
domain_url switch atum.hac.lp1.d4c.nintendo.net || echo optional-game-link
group_user_agent switch
''')
        self.assertEqual(result.stdout.splitlines(), [
            *SWITCH_TEST_HOSTS, *SWITCH_GAME_HOSTS, 'optional-game-link', 'XboxDownload/Nintendo NX'])

    def test_switch_legacy_public_result_adds_default_games_once(self):
        result = self.shell('''
echo 208.67.222.222 > state/best_switch
echo ctest-dl-lp1.cdn.nintendo.net > state/domains_switch
pinned_domains switch
group_result_ready switch; echo ready:$?
commit_group_result switch 208.67.222.222 "$(pinned_domains switch | tr '\\n' ' ')"
pinned_domains switch
test ! -s state/resources_switch
''')
        self.assertEqual(result.stdout.splitlines(), [
            SWITCH_TEST_HOSTS[0], *SWITCH_GAME_HOSTS, 'ready:0',
            SWITCH_TEST_HOSTS[0], *SWITCH_GAME_HOSTS])

    def test_switch_default_games_require_a_verified_public_primary(self):
        for domains, primary in [(SWITCH_GAME_HOSTS[0], ''),
                                  (SWITCH_TEST_HOSTS[0], 'http://' + SWITCH_TEST_HOSTS[1] + '/30m')]:
            with self.subTest(domains=domains, primary=primary):
                result = self.shell('''
echo 208.67.222.222 > state/best_switch
printf '%s\\n' "$1" > state/domains_switch
printf '%s\\n' "$2" > config/test_url_switch
pinned_domains switch
group_result_ready switch; echo ready:$?
''', domains, primary)
                self.assertEqual(result.stdout.splitlines(), ['ready:1'])

    def test_switch_resource_sampling_separates_defaults_and_enhanced_links(self):
        game_url = 'https://' + SWITCH_GAME_HOSTS[0] + '/private-game?token=value'
        for mode, link, mapped in [
            ('good', '', [*SWITCH_TEST_HOSTS, *SWITCH_GAME_HOSTS]),
            ('game_fail', game_url, [*SWITCH_TEST_HOSTS, *SWITCH_GAME_HOSTS[1:]]),
            ('primary_fail', '', [SWITCH_TEST_HOSTS[1]])]:
            with self.subTest(mode=mode):
                result = self.shell(function('verify_domains') + '''
printf '%s\\n' "$2" > config/verification_urls_switch
alive_resource() {
    printf '%s\\n' "$2" >> tmp/probes
    case "$1:$2:$TEST_MODE" in
    *private-game*game_fail|*ctest-dl-lp1.cdn.nintendo.net*primary_fail) return 1 ;;
    esac
}
TEST_MODE=$1
verify_domains switch 208.67.222.222
echo resources
cat tmp/probes
''', mode, link)
                lines = result.stdout.splitlines()
                self.assertEqual(lines[0].split(), mapped)
                self.assertEqual(lines[1], 'resources')
                expected_urls = ['http://' + host + '/30m' for host in SWITCH_TEST_HOSTS]
                self.assertEqual(lines[2:], expected_urls + ([game_url] if link else []))

    def test_switch_link_removal_restores_a_game_excluded_by_failed_validation(self):
        url = 'https://' + SWITCH_GAME_HOSTS[0] + '/game?token=value'
        result = self.shell('''
printf '%s\\n' "$1" > config/verification_urls_switch
commit_group_result switch 208.67.222.222 'ctest-dl-lp1.cdn.nintendo.net nemof.p01.lp1.nemo.srv.nintendo.net nemof.hac.lp1.nemo.srv.nintendo.net'
pinned_domains switch
group_result_ready switch; echo failed:$?
rm config/verification_urls_switch
pinned_domains switch
group_result_ready switch; echo restored:$?
''', url)
        self.assertEqual(result.stdout.splitlines(), [
            SWITCH_TEST_HOSTS[0], *SWITCH_GAME_HOSTS[1:], 'failed:1',
            SWITCH_TEST_HOSTS[0], *SWITCH_GAME_HOSTS[1:], SWITCH_GAME_HOSTS[0], 'restored:0'])

    def test_switch_health_checks_only_public_and_configured_resources(self):
        url = 'https://' + SWITCH_GAME_HOSTS[0] + '/game?token=value'
        result = self.shell('''
commit_group_result switch 208.67.222.222 "$SWITCH_TEST_DOMAINS $SWITCH_GAME_DOMAINS"
alive_resource() { printf '%s\\n' "$2"; }
group_alive switch 208.67.222.222
printf '%s\\n' "$1" > config/verification_urls_switch
commit_group_result switch 208.67.222.222 "$SWITCH_TEST_DOMAINS $SWITCH_GAME_DOMAINS"
group_alive switch 208.67.222.222
rm config/verification_urls_switch
commit_group_result switch 208.67.222.222 "$SWITCH_TEST_DOMAINS $SWITCH_GAME_DOMAINS"
test ! -s state/resources_switch
''', url)
        self.assertEqual(result.stdout.splitlines(), ['http://' + SWITCH_TEST_HOSTS[0] + '/30m'] * 2 + [url])

    def test_commit_failure_restores_all_previous_group_files(self):
        result = self.shell('''
echo 8.8.4.4 > state/best_ps
echo old-domain > state/domains_ps
echo old-evidence > state/resources_ps
mv() { case "$2" in */best_ps) return 1 ;; *) command mv "$@" ;; esac; }
commit_group_result ps 9.9.9.9 gst.prod.dl.playstation.net; echo result:$?
cat state/best_ps state/domains_ps state/resources_ps
''')
        self.assertEqual(result.stdout.splitlines(), ['result:1', '8.8.4.4', 'old-domain', 'old-evidence'])

    def test_blocked_syslog_returns_without_holding_the_task(self):
        def fixture(directory):
            executable = directory / 'bin/logger'
            executable.parent.mkdir()
            executable.write_text('#!/bin/sh\nexec sleep 30\n')
            executable.chmod(0o755)

        started = time.monotonic()
        result = self.shell(function('close_service_fds') + '\n' + function('system_log') + '''
PATH="$PWD/bin:$PATH"; NAME=xbox-accel
system_log 'resource validation failed'
echo task-continued
''', fixture=fixture)
        self.assertLess(time.monotonic() - started, 6)
        self.assertEqual(result.stdout.splitlines(), ['task-continued'])

    def test_signed_store_links_require_fresh_expiry_and_valid_size(self):
        valid = 'https://dl.delivery.mp.microsoft.com/file?P1=1999999999&P4=signature'
        self.shell('store_link_valid "$1" 157286400', valid)
        for url, size in [(valid, '1024'), (valid, 'nan'),
                          (valid.replace('1999999999', '1'), '157286400'),
                          (valid + '&P1=1999999998', '157286400'),
                          (valid.replace('&P4=signature', ''), '157286400')]:
            with self.subTest(size=size):
                self.assertNotEqual(self.shell('store_link_valid "$1" "$2"', url, size, check=False).returncode, 0)

    def test_probe_preserves_sni_and_switch_header(self):
        result = self.shell('''
curl() { printf '%s\\n' "$@" > tmp/arguments; printf '206 1024 1000 0.1 0.2 9.9.9.9'; }
probe_resource 9.9.9.9 'https://atum.hac.lp1.d4c.nintendo.net/game?token=value' 0 1024 3 switch
cat tmp/arguments
''')
        arguments = result.stdout.splitlines()
        self.assertIn('atum.hac.lp1.d4c.nintendo.net:443:9.9.9.9', arguments)
        self.assertIn('XboxDownload/Nintendo NX', arguments)
        self.assertIn('https://atum.hac.lp1.d4c.nintendo.net/game?token=value', arguments)
        self.assertIn('0-1023', arguments)
        self.assertNotIn('-k', arguments)

    def test_diagnostic_group_values_are_data(self):
        message = '$(touch injected)"; touch injected; # unsafe-looking data'
        result = self.shell('''
. "$1"
task_snapshot() { :; }
json_add_object() { :; }; json_close_object() { :; }; json_add_int() { :; }
json_add_string() { printf '%s=%s\\n' "$1" "$2"; }
group_report switch running RUNNING "$2" 9.9.9.9 1000 1 1
add_task_group switch
test ! -e injected
''', DIAGNOSTICS, message)
        self.assertIn('message=' + message, result.stdout)
        self.assertIn('ip=9.9.9.9', result.stdout)

    def test_platform_configuration_rejects_local_candidates_and_bad_game_hosts(self):
        for key, value in [('extra_ips_store', '127.0.0.1'),
                           ('extra_ips_ps', '192.168.1.1'),
                           ('verification_urls_switch', 'https://ctest-dl-lp1.cdn.nintendo.net/30m'),
                           ('test_url_switch', 'https://atum.hac.lp1.d4c.nintendo.net/game')]:
            with self.subTest(key=key):
                result = self.shell('printf "%s\\n" "$2" > "config/$1"; validate_platform_config', key, value, check=False)
                self.assertNotEqual(result.returncode, 0)

    def test_cleanup_identifies_new_platform_rules_and_preserves_user_domains(self):
        result = self.shell('''
passwall_dnsmasq_confs() { echo copy.conf; }
''' + function('passwall_has_stale_rules') + '''
echo 'server=/dl.delivery.mp.microsoft.com/127.0.0.1#5335' > copy.conf
passwall_has_stale_rules ''; echo platform:$?
echo 'server=/user.example/127.0.0.1#5335' > copy.conf
passwall_has_stale_rules ''; echo user:$?
''')
        self.assertEqual(result.stdout.splitlines(), ['platform:0', 'user:1'])


if __name__ == '__main__':
    unittest.main()
