#!/usr/bin/env python3
"""独立重定向的进程所有权、清理重试与升级迁移；使用隔离的主机夹具。"""
import pathlib
import shlex
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
LIB = ROOT / 'files/usr/share/xbox-accel/redirect.sh'
WEB_ROOT = '/var/etc/xbox-accel/redirect'


def process_fixture(root, pid, web_root, inode, binary=b'uhttpd-fixture'):
    process = root / 'proc' / str(pid)
    (process / 'fd').mkdir(parents=True)
    (root / 'reference-uhttpd').write_bytes(b'uhttpd-fixture')
    (process / 'exe').write_bytes(binary)
    (process / 'cmdline').write_bytes(b'opaque-worker\0-f\0-h\0' + web_root.encode() + b'\0')
    (process / 'stat').write_text(f'{pid} (opaque-worker) S ' + ' '.join(['0'] * 18 + ['100']) + '\n')
    (process / 'fd/5').symlink_to(f'socket:[{inode}]')


class RedirectTests(unittest.TestCase):
    def shell(self, body, fixture=None, process_mock=False):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for name in ('state', 'run', 'config'):
                (root / name).mkdir()
            if fixture:
                fixture(root)
            library = LIB
            if process_mock:
                library = root / 'redirect.sh'
                library.write_text(LIB.read_text().replace('/proc/', 'proc/').replace('/usr/sbin/uhttpd', 'reference-uhttpd'))
            helpers = f'''
NAME=xbox-accel; STATE_DIR=state; RUN_DIR=run; REDIRECT_CONF=redirect.conf
REDIRECT_ROOT={shlex.quote(WEB_ROOT)}; CGI_URL=/cgi-bin/xbox-accel-redirect
cfg() {{ if [ -s "config/$1" ]; then cat "config/$1"; else printf '%s\\n' "$2"; fi; }}
cfg_bool() {{ [ "$(cfg "$1" "$2")" = 1 ]; }}
note() {{ printf '%s\\n' "$*" >&2; }}
. {shlex.quote(str(library))}
'''
            result = subprocess.run(['sh', '-c', helpers + body], cwd=root,
                                    text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout.splitlines()

    def test_stop_verifies_binary_and_web_root_with_wrong_pid_cache(self):
        def fixture(root):
            process_fixture(root, 51, '/www', 201)
            process_fixture(root, 52, WEB_ROOT, 202)
            process_fixture(root, 53, WEB_ROOT, 203, binary=b'foreign-program')
        output = self.shell('''
echo 51 > run/redirect.pid
kill() { echo "signal:$*"; rm -rf "proc/$2"; }
sleep() { :; }
redirect_pids
stop_redirect; echo result:$?
test -f proc/51/exe && echo main-preserved
test -f proc/53/exe && echo foreign-preserved
''', fixture, process_mock=True)
        self.assertEqual(output, ['52', 'signal:-TERM 52', 'result:0', 'main-preserved', 'foreign-preserved'])

    def test_listener_reuse_accepts_own_socket_and_rejects_main_socket(self):
        def fixture(root):
            process_fixture(root, 51, '/www', 201)
            process_fixture(root, 52, WEB_ROOT, 202)
            (root / 'proc/net').mkdir()
            (root / 'proc/net/tcp6').touch()
            (root / 'proc/net/tcp').write_text('0: 0100007F:46A1 00000000:0000 0A 0 0 0 0 0 202\n')
        output = self.shell('''
redirect_port_available 18081; echo own:$?
echo '1: 0101A8C0:46A1 00000000:0000 0A 0 0 0 0 0 201' >> proc/net/tcp
redirect_port_available 18081; echo conflict:$?
''', fixture, process_mock=True)
        self.assertEqual(output, ['own:0', 'conflict:1'])

    def test_readiness_requires_both_listeners_owned_by_redirect_process(self):
        def fixture(root):
            process_fixture(root, 51, '/www', 201)
            process_fixture(root, 52, WEB_ROOT, 202)
        output = self.shell('''
lan_ip() { echo 192.168.1.1; }
netstat() { cat listeners; }
echo 'tcp 0 0 127.0.0.1:18081 0.0.0.0:* LISTEN 52/uhttpd' > listeners
redirect_ready; echo partial:$?
echo 'tcp 0 0 192.168.1.1:18081 0.0.0.0:* LISTEN 51/uhttpd' >> listeners
redirect_ready; echo foreign:$?
sed 's@51/uhttpd@52/uhttpd@' listeners > listeners.new; mv listeners.new listeners
redirect_ready; echo complete:$?
''', fixture, process_mock=True)
        self.assertEqual(output, ['partial:1', 'foreign:1', 'complete:0'])

    def test_failed_cleanup_keeps_runtime_and_ownership_for_retry(self):
        output = self.shell('''
echo active > redirect.conf
echo nft > state/redirect_backend
sync_redirect_include() { :; }
clear_redirect_nft() { [ -e ready ]; }
clear_redirect_iptables() { :; }
sync_redirect 0; echo first:$?
test -f redirect.conf && test -f state/redirect_backend && echo records-kept
touch ready
sync_redirect 0; echo second:$?
test ! -e redirect.conf && test ! -e state/redirect_backend && echo records-cleared
''')
        self.assertEqual(output, ['first:1', 'records-kept', 'second:0', 'records-cleared'])

    def test_legacy_router_address_migrates_after_old_service_cleared_web_state(self):
        output = self.shell('''
echo 192.168.1.1 > config/redirect_ip
redirect_local_ip() { [ "$1" = 192.168.1.1 ]; }
uci() {
    case "$*" in
    '-q set xbox-accel.main.redirect_ip='*) printf '%s\\n' "${3#*=}" > config/redirect_ip ;;
    '-q set xbox-accel.main.redirect_service_version='*) printf '%s\\n' "${3#*=}" > config/redirect_service_version ;;
    '-q commit xbox-accel') return 0 ;;
    *) return 1 ;;
    esac
}
redirect_ip
migrate_redirect_ip
redirect_ip
echo 192.168.1.1 > config/redirect_ip
redirect_ip
''')
        self.assertEqual(output, ['192.0.2.1', '192.0.2.1', '192.168.1.1'])

    def test_firewall_include_returns_promptly_when_apply_lock_is_busy(self):
        output = self.shell('''
echo 1 > config/enabled
echo redirect > run/applied_mode
echo '192.168.1.1 18081 192.0.2.1 br-lan nft' > redirect.conf
flock() { return 1; }
apply_redirect_firewall() { echo unexpected-mutation; }
restore_redirect_firewall; echo busy:$?
flock() { return 0; }
apply_redirect_firewall() { echo "restored:$*"; }
passwall_direct() { :; }
restore_redirect_firewall; echo free:$?
''')
        self.assertEqual(output, ['busy:0', 'restored:192.0.2.1 192.168.1.1 18081 br-lan nft', 'free:0'])

    def test_foreign_include_is_preserved_during_apply_and_cleanup(self):
        output = self.shell('''
SHARE_DIR=/usr/share/xbox-accel
uci() {
    case "$*" in
    '-q get firewall.xbox_accel_redirect') echo include ;;
    '-q get firewall.xbox_accel_redirect.path') echo /etc/user-firewall.sh ;;
    *) echo unexpected-mutation; return 1 ;;
    esac
}
sync_redirect_include 1; echo apply:$?
sync_redirect_include 0; echo cleanup:$?
''')
        self.assertEqual(output, ['apply:1', 'cleanup:1'])

    def test_iptables_backend_is_selected_when_nft_is_unavailable(self):
        output = self.shell('''
command() { [ "$1 $2" = '-v iptables' ]; }
redirect_backend
command() { return 1; }
redirect_backend; echo unavailable:$?
''')
        self.assertEqual(output, ['iptables', 'unavailable:1'])


if __name__ == '__main__':
    unittest.main()
