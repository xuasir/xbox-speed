#!/usr/bin/env python3
"""Host-only DNS cleanup regression tests; never signal host/router processes."""
import pathlib
import re
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / 'files/usr/bin/xbox-accel'


def function(name):
    text = SCRIPT.read_text()
    return re.search(r'^' + name + r'\(\) \{\n.*?^\}', text, re.M | re.S).group(0)


def cleanup_helpers():
    return '\n'.join(function(name) for name in ('mark_main_reload', 'pending_cleanup_rules', 'remember_cleanup'))


def process_helpers():
    return "uniq_keep_order() { awk '!seen[$0]++'; }\n" + '\n'.join(function(name) for name in ('dnsmasq_process', 'dnsmasq_process_confs')).replace('/proc/', 'proc/').replace('/usr/sbin/dnsmasq', 'reference-dnsmasq')


def process_fixture(root, pid, conf, name='arbitrary-worker', form='-C', binary=b'dnsmasq-fixture'):
    process = root / 'proc' / str(pid)
    process.mkdir(parents=True)
    (root / 'reference-dnsmasq').write_bytes(b'dnsmasq-fixture')
    (process / 'exe').write_bytes(binary)
    (process / 'comm').write_text(name + '\n')
    (process / 'stat').write_text(f'{pid} ({name}) S ' + ' '.join(['0'] * 18 + ['100']) + '\n')
    (process / 'root').symlink_to('/')
    (process / 'cwd').symlink_to(root)
    if form in ('-C', '--conf-file'):
        args = [form, str(root / conf)]
    elif form == 'relative':
        args = ['-C', conf]
    else:
        args = [form + str(root / conf)]
    (process / 'cmdline').write_bytes(b'opaque-executable\0' + b'\0'.join(arg.encode() for arg in args) + b'\0')
    target = root / conf
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch()


class DNSCleanupTests(unittest.TestCase):
    def run_shell(self, body, fixture=None):
        with tempfile.TemporaryDirectory() as directory:
            if fixture:
                fixture(pathlib.Path(directory))
            p = subprocess.run(['sh', '-c', body], cwd=directory, text=True,
                               capture_output=True, timeout=10)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            return p.stdout

    def test_teardown_always_flushes_previous_instance_after_copy_sync(self):
        output = self.run_shell('''
STATE_DIR=state; RUN_DIR=run; RESOLVER_CONF=resolver; DNS_INSTANCE=new
mkdir state run
echo old > state/main_instance
echo /assets1.xboxlive.cn/127.0.0.1#5444 > state/main_rules
apply_lock() { :; }; cancel_task() { :; }; uniq_keep_order() { awk '!seen[$0]++'; }
passwall_dnsmasq_confs() { :; }
sync_main() { rm state/main_instance state/main_rules; return 1; }
sync_uhttpd() { :; }; sync_cron() { :; }
sync_passwall() { echo "sync:$1"; }
flush_front_cache() { echo "flush:$1"; }
''' + cleanup_helpers() + '\n' + function('teardown') + '\nteardown; teardown\n')
        self.assertEqual(output.splitlines(), [
            'sync:/assets1.xboxlive.cn/127.0.0.1#5444', 'flush:old',
            'sync:', 'flush:new'])

    def test_copy_owned_old_port_and_current_rules_but_not_user_domain(self):
        output = self.run_shell('''
STATE_DIR=state; mkdir state
CN_DOMAINS=assets1.xboxlive.cn
COM_DOMAINS=assets1.xboxlive.com
cfg() { echo 5335; }
passwall_dnsmasq_confs() { echo copy.conf; }
''' + function('passwall_has_stale_rules') + '\n' + function('passwall_out_of_sync') + '''
echo 'server=/assets1.xboxlive.cn/127.0.0.1#5444' > copy.conf
passwall_out_of_sync '/assets1.xboxlive.cn/127.0.0.1#5444'; echo old:$?
echo 'server=/user.example/127.0.0.1#5335' > copy.conf
passwall_out_of_sync; echo user:$?
echo 'server=/assets1.xboxlive.com/127.0.0.1#5335' > copy.conf
passwall_out_of_sync; echo current:$?
echo '/assets1.xboxlive.com/127.0.0.1#5335' > state/main_rules
passwall_out_of_sync; echo desired:$?
''')
        self.assertEqual(output.splitlines(), ['old:0', 'user:1', 'current:0', 'desired:1'])

    def test_flush_missing_main_still_signals_only_matching_copy(self):
        helper = process_helpers() + '\n' + function('flush_front_cache').replace('/proc/', 'proc/')
        def fixture(root):
            process_fixture(root, 11, 'copy.conf', name='renamed-dns-worker')
            process_fixture(root, 12, 'unrelated.conf')
            process_fixture(root, 13, 'copy.conf', name='dnsmasq', binary=b'other-executable')
        output = self.run_shell('''
DNS_INSTANCE=missing
uci() { return 1; }
passwall_dnsmasq_confs() { echo "$PWD/copy.conf"; }
kill() { echo "signal:$*"; }
''' + helper + '\nflush_front_cache\n', fixture)
        self.assertEqual(output.splitlines(), ['signal:-HUP 11'])

    def test_flush_arbitrary_copy_names_and_config_argument_forms(self):
        helper = process_helpers() + '\n' + function('flush_front_cache').replace('/proc/', 'proc/').replace('/var/etc/dnsmasq.conf.', '$PWD/main.')
        def fixture(root):
            process_fixture(root, 21, 'main.main_id', name='router-primary')
            process_fixture(root, 22, 'default.conf', name='anything', form='--conf-file=')
            process_fixture(root, 23, 'acl_5.conf', name='worker-5', form='-C')
            process_fixture(root, 24, 'unrelated.conf', name='dnsmasq_default')
            process_fixture(root, 25, 'default.conf', name='dnsmasq', binary=b'sing-box')
            process_fixture(root, 26, 'resolver.conf', name='dnsmasq')
            process_fixture(root, 27, 'short.conf', form='-C')
            process_fixture(root, 28, 'long.conf', form='--conf-file')
            process_fixture(root, 29, 'joined.conf', form='-C')
            (root / 'proc/29/cmdline').write_bytes(b'anything\0-C' + str(root / 'joined.conf').encode() + b'\0')
            process_fixture(root, 30, 'relative.conf', form='relative')
        output = self.run_shell('''
DNS_INSTANCE=main
uci() { echo 'dhcp.main_id=dnsmasq'; }
passwall_dnsmasq_confs() {
    for name in default acl_5 short long joined relative; do echo "$PWD/$name.conf"; done
}
kill() { echo "signal:$*"; }
''' + helper + '\nflush_front_cache\n', fixture)
        self.assertEqual(output.splitlines(), [
            'signal:-HUP 21', 'signal:-HUP 22', 'signal:-HUP 23',
            'signal:-HUP 27', 'signal:-HUP 28', 'signal:-HUP 29', 'signal:-HUP 30'])

    def test_disabled_restart_retains_explicit_addition_sync_preference(self):
        output = self.run_shell('''
passwall_out_of_sync() { return 0; }
passwall_has_stale_rules() { return 1; }
passwall_dnsmasq_confs() { :; }; uniq_keep_order() { awk '!seen[$0]++'; }
cfg_bool() { return 1; }
note() { echo "$*"; }
''' + function('sync_passwall') + '\nsync_passwall old-rule\n')
        self.assertIn('待同步', output)

    def test_cleanup_restarts_copy_even_with_addition_restart_disabled(self):
        helper = function('sync_passwall').replace('[ -x "$service" ]', 'true')
        output = self.run_shell('''
STATE_DIR=state; mkdir state
passwall_out_of_sync() { [ ! -f synced ]; }
passwall_has_stale_rules() { return 0; }
passwall_dnsmasq_confs() { :; }; uniq_keep_order() { awk '!seen[$0]++'; }
cfg_bool() { return 1; }
note() { :; }
svc() { echo "reload:$*"; touch synced; }
''' + helper + '\nsync_passwall old-rule\n')
        self.assertEqual(output.strip(), 'reload:/etc/init.d/passwall restart')

    def test_failed_cleanup_retains_previous_port_for_retry_and_flushes_cache(self):
        output = self.run_shell('''
STATE_DIR=state; RUN_DIR=run; RESOLVER_CONF=resolver; DNS_INSTANCE=new
mkdir state run
echo old > state/main_instance
echo /assets1.xboxlive.cn/127.0.0.1#5444 > state/main_rules
apply_lock() { :; }; cancel_task() { :; }; uniq_keep_order() { awk '!seen[$0]++'; }
passwall_dnsmasq_confs() { :; }
sync_main() { rm -f state/main_instance state/main_rules; return 1; }
sync_uhttpd() { :; }; sync_cron() { :; }
sync_passwall() { echo "sync:$1"; return 1; }
flush_front_cache() { echo "flush:$1"; }
''' + cleanup_helpers() + '\n' + function('teardown') + '''
teardown; echo result:$?
rm -rf run; mkdir run
teardown; echo result:$?
cat state/pending_rules
''')
        self.assertEqual(output.splitlines(), [
            'sync:/assets1.xboxlive.cn/127.0.0.1#5444', 'flush:old', 'result:1',
            'sync:/assets1.xboxlive.cn/127.0.0.1#5444', 'flush:old', 'result:1',
            '/assets1.xboxlive.cn/127.0.0.1#5444'])

    def test_restart_failure_is_returned(self):
        helper = function('sync_passwall').replace('[ -x "$service" ]', 'true')
        output = self.run_shell('''
passwall_out_of_sync() { return 0; }
passwall_has_stale_rules() { return 0; }
passwall_dnsmasq_confs() { :; }; uniq_keep_order() { awk '!seen[$0]++'; }
note() { :; }; svc() { return 1; }
''' + helper + '\nsync_passwall old-rule; echo result:$?\n')
        self.assertEqual(output.strip(), 'result:1')


    def test_discovery_uses_binary_and_owned_rules_with_arbitrary_paths(self):
        helper = process_helpers() + '\n' + cleanup_helpers() + '\n' + function('passwall_dnsmasq_confs').replace('/proc/', 'proc/')
        def fixture(root):
            process_fixture(root, 41, 'custom directory/one.conf', name='opaque')
            (root / 'custom directory/one.conf').write_text('server=/assets1.xboxlive.com/127.0.0.1#5444\n')
            process_fixture(root, 42, 'custom/two.conf', name='dnsmasq', binary=b'sing-box')
            (root / 'custom/two.conf').write_text('server=/assets1.xboxlive.com/127.0.0.1#5444\n')
            process_fixture(root, 43, 'custom/user.conf', name='router-dns')
            (root / 'custom/user.conf').write_text('server=/user.example/127.0.0.1#5335\n')
        output = self.run_shell('''
STATE_DIR=state; RUN_DIR=run; RESOLVER_CONF=resolver
mkdir state run
echo /assets1.xboxlive.com/127.0.0.1#5444 > state/pending_rules
cfg() { echo 5335; }
''' + helper + '\npasswall_dnsmasq_confs | sed "s|$PWD/||"\n', fixture)
        self.assertEqual(output.strip(), 'custom directory/one.conf')

    def test_signal_failure_is_propagated(self):
        helper = process_helpers() + '\n' + function('flush_front_cache').replace('/proc/', 'proc/')
        output = self.run_shell('''
DNS_INSTANCE=missing
uci() { return 1; }
passwall_dnsmasq_confs() { echo "$PWD/copy.conf"; }
note() { :; }; kill() { return 1; }
''' + helper + '\nflush_front_cache; echo result:$?\n',
            lambda root: process_fixture(root, 44, 'copy.conf'))
        self.assertEqual(output.strip(), 'result:1')

    def test_cleanup_failure_stages_retain_records(self):
        for stage in ('main', 'web', 'cron', 'cache'):
            with self.subTest(stage=stage):
                output = self.run_shell('''
STATE_DIR=state; RUN_DIR=run; RESOLVER_CONF=resolver; DNS_INSTANCE=main
mkdir state run
echo main > state/main_instance
echo /assets1.xboxlive.com/127.0.0.1#5335 > state/main_rules
apply_lock() { :; }; cancel_task() { :; }; uniq_keep_order() { awk '!seen[$0]++'; }
passwall_dnsmasq_confs() { :; }
sync_main() { [ "$FAIL_STAGE" != main ] && return 1; return 2; }
sync_uhttpd() { [ "$FAIL_STAGE" != web ]; }
sync_cron() { [ "$FAIL_STAGE" != cron ]; }
sync_passwall() { :; }
flush_front_cache() { [ "$FAIL_STAGE" != cache ]; }
''' + cleanup_helpers() + '\n' + function('teardown') +
                    f'\nFAIL_STAGE={stage}\nteardown; echo result:$?\ncat state/pending_rules\n')
                self.assertEqual(output.splitlines(), ['result:1', '/assets1.xboxlive.com/127.0.0.1#5335'])

    def test_http_restore_retry_keeps_original_backup(self):
        output = self.run_shell('''
STATE_DIR=state; CGI_URL=/cgi-bin/xbox-accel-redirect
mkdir state
echo /original-error > state/uhttpd_error_page
echo "$CGI_URL" > current
uci() {
    case "$*" in
    '-q get uhttpd.main') echo uhttpd ;;
    '-q get uhttpd.main.error_page') cat current ;;
    '-q set uhttpd.main.error_page='*) printf '%s\\n' "${3#*=}" > current ;;
    '-q commit uhttpd') return 0 ;;
    *) return 1 ;;
    esac
}
svc() { [ -e ready ]; }
''' + function('sync_uhttpd') + '''
sync_uhttpd 0; echo first:$?
test -f state/uhttpd_error_page && echo backup-kept
touch ready
sync_uhttpd 0; echo second:$?
cat current
test ! -e state/uhttpd_error_page && echo backup-cleared
''')
        self.assertEqual(output.splitlines(), ['first:1', 'backup-kept', 'second:0', '/original-error', 'backup-cleared'])

    def test_cron_retry_reloads_after_file_was_already_cleaned(self):
        output = self.run_shell('''
STATE_DIR=state; TMP_DIR=tmp; CRON_FILE="$PWD/cron"; CRON_TAG='# xbox-accel'
mkdir state tmp
printf '%s\\n' '0 1 * * * user-job' '0 2 * * * plugin # xbox-accel' > cron
svc() { [ -e ready ]; }
''' + function('sync_cron') + '''
sync_cron 0; echo first:$?
test -e state/cron_reload_needed && echo retry-kept
touch ready
sync_cron 0; echo second:$?
cat cron
test ! -e state/cron_reload_needed && echo retry-cleared
''')
        self.assertEqual(output.splitlines(), ['first:1', 'retry-kept', 'second:0', '0 1 * * * user-job', 'retry-cleared'])

    def test_main_rule_removal_failure_retains_ownership(self):
        output = self.run_shell('''
STATE_DIR=state; RUN_DIR=run; DNS_INSTANCE=main; CN_DOMAINS=assets1.xboxlive.cn
mkdir state run
echo /assets1.xboxlive.cn/127.0.0.1#5335 > state/main_rules
cfg() { echo 5335; }; uniq_keep_order() { awk '!seen[$0]++'; }
main_rules_now() { cat state/main_rules; }
uci() { case "$*" in '-q get dhcp.main') echo dnsmasq ;; *) return 1 ;; esac; }
''' + cleanup_helpers() + '\n' + function('sync_main') + '''
sync_main 0; echo result:$?
cat state/main_rules
''')
        self.assertEqual(output.splitlines(), ['result:2', '/assets1.xboxlive.cn/127.0.0.1#5335'])



    def test_main_commit_failure_is_retried_before_ownership_is_cleared(self):
        output = self.run_shell('''
STATE_DIR=state; RUN_DIR=run; DNS_INSTANCE=main; CN_DOMAINS=assets1.xboxlive.cn
mkdir state run
echo /assets1.xboxlive.cn/127.0.0.1#5335 > state/main_rules
cp state/main_rules live_rules
cfg() { echo 5335; }; uniq_keep_order() { awk '!seen[$0]++'; }
main_rules_now() { cat live_rules; }
uci() {
    case "$*" in
    '-q get dhcp.main') echo dnsmasq ;;
    '-q del_list dhcp.main.server='*) : > live_rules ;;
    '-q commit dhcp') [ -e ready ] ;;
    *) return 1 ;;
    esac
}
''' + cleanup_helpers() + '\n' + function('sync_main') + '''
sync_main 0; echo first:$?
test -s state/main_rules && echo ownership-kept
touch ready
sync_main 0; echo second:$?
test ! -e state/main_rules && echo ownership-cleared
test -e state/main_reload_needed && echo service-reload-required
''')
        self.assertEqual(output.splitlines(), ['first:2', 'ownership-kept', 'second:0', 'ownership-cleared', 'service-reload-required'])

    def test_pid_reuse_skips_signal(self):
        helper = process_helpers().replace('dnsmasq_process_confs()', 'original_process_confs()') + '\n' + function('flush_front_cache').replace('/proc/', 'proc/')
        output = self.run_shell('''
DNS_INSTANCE=missing
uci() { return 1; }
passwall_dnsmasq_confs() { echo "$PWD/copy.conf"; }
kill() { echo "signal:$*"; }
''' + helper + '''
dnsmasq_process_confs() {
    original_process_confs "$1"
    if [ ! -e changed ]; then
        touch changed
        sed 's/100$/200/' proc/45/stat > newstat
        mv newstat proc/45/stat
    fi
}
flush_front_cache
''', lambda root: process_fixture(root, 45, 'copy.conf'))
        self.assertEqual(output.strip(), '')



if __name__ == '__main__':
    unittest.main()
