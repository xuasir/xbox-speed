#!/usr/bin/env python3
"""Exercise cleanup with real renamed DNS binaries in a disposable OpenWrt container.

Usage: python3 tests/test_dns_cleanup_openwrt.py xa-test
Creates private DNS listeners; leaves the router's configuration and services intact.
"""
import pathlib
import re
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTAINER = sys.argv.pop(1) if len(sys.argv) > 1 else 'xa-test'


def helpers():
    source = (ROOT / 'files/usr/bin/xbox-accel').read_text()
    names = ('pending_cleanup_rules', 'dnsmasq_process', 'dnsmasq_process_confs',
             'passwall_dnsmasq_confs', 'flush_front_cache')
    return '\n'.join(re.search(r'^' + name + r'\(\) \{\n.*?^\}', source, re.M | re.S).group(0)
                     for name in names)


class OpenWrtCleanupTests(unittest.TestCase):
    def test_real_cache_flush_for_arbitrary_names_preserves_other_services(self):
        script = '''
set -eu
test -f /etc/openwrt_release
work=/tmp/xbox-accel-cleanup-test.$$
mkdir "$work"
STATE_DIR=$work/state; RUN_DIR=$work/run; RESOLVER_CONF=$work/internal.conf
mkdir "$STATE_DIR" "$RUN_DIR"
base=49553
if netstat -lnptu | grep -Eq ':(49553|49554|49555|49556)[[:space:]]'; then
    rmdir "$STATE_DIR" "$RUN_DIR" "$work"; exit 1
fi
uniq_keep_order() { awk '!seen[$0]++'; }
cfg() { echo "$base"; }
uci() { return 1; }
DNS_INSTANCE=missing
CN_DOMAINS=''; COM_DOMAINS=assets1.xboxlive.com
COM_DL_DOMAINS=''; SRV_NAMES=''; CDN_HOSTS=''
note() { echo "$*" >&2; }
''' + helpers() + '''
cleanup() {
    for file in "$work"/*.pid; do
        [ -f "$file" ] || continue
        pid=$(cat "$file")
        if dnsmasq_process "$pid" && dnsmasq_process_confs "$pid" | grep -qF "$work/"; then
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done
    rm -rf "$work"
}
trap cleanup EXIT
system_pids=""
for p in /proc/[0-9]*; do
    dnsmasq_process "@{p##*/}" && system_pids="$system_pids @{p##*/}"
done
cat > "$work/upstream.conf" <<EOF
port=$base
listen-address=127.0.0.1
bind-interfaces
no-resolv
no-hosts
local-ttl=3600
pid-file=$work/upstream.pid
host-record=assets1.xboxlive.com,1.1.1.1
local=/assets1.xboxlive.com/
EOF
/usr/sbin/dnsmasq -C "$work/upstream.conf"
cp /usr/sbin/dnsmasq "$work/blue-engine"
ln -s /usr/sbin/dnsmasq "$work/green-worker"
for item in 'one' 'two' 'control'; do
    case "$item" in one) port=49554 ;; two) port=49555 ;; control) port=49556 ;; esac
    cat > "$work/$item rules.conf" <<EOF
port=$port
listen-address=127.0.0.1
bind-interfaces
no-resolv
no-hosts
cache-size=100
pid-file=$work/$item.pid
server=127.0.0.1#$base
EOF
    if [ "$item" != control ]; then
        echo "server=/assets1.xboxlive.com/127.0.0.1#$base" >> "$work/$item rules.conf"
    fi
done
"$work/blue-engine" --conf-file="$work/one rules.conf"
"$work/green-worker" -C"$work/two rules.conf"
/usr/sbin/dnsmasq -C "$work/control rules.conf"
echo "/assets1.xboxlive.com/127.0.0.1#$base" > "$STATE_DIR/main_rules"
sleep 1
for port in 49554 49555 49556; do
    test "$(dig @127.0.0.1 -p "$port" assets1.xboxlive.com A +short +time=1 +tries=1)" = 1.1.1.1
done
passwall_dnsmasq_confs > "$STATE_DIR/dns_copies"
test "$(wc -l < "$STATE_DIR/dns_copies")" = 2
grep -qxF "$work/one rules.conf" "$STATE_DIR/dns_copies"
grep -qxF "$work/two rules.conf" "$STATE_DIR/dns_copies"
kill -TERM "$(cat "$work/upstream.pid")"
sleep 1
sed -i 's/1.1.1.1/2.2.2.2/' "$work/upstream.conf"
/usr/sbin/dnsmasq -C "$work/upstream.conf"
sleep 1
for port in 49554 49555 49556; do
    test "$(dig @127.0.0.1 -p "$port" assets1.xboxlive.com A +short +time=1 +tries=1)" = 1.1.1.1
done
# Config withdrawal precedes cache flushing; the saved inventory covers it.
rm "$STATE_DIR/main_rules"
sed -i '/^server=\\/assets1.xboxlive.com\\//d' "$work/one rules.conf" "$work/two rules.conf"
flush_front_cache
sleep 1
for port in 49554 49555; do
    test "$(dig @127.0.0.1 -p "$port" assets1.xboxlive.com A +short +time=1 +tries=1)" = 2.2.2.2
done
test "$(dig @127.0.0.1 -p 49556 assets1.xboxlive.com A +short +time=1 +tries=1)" = 1.1.1.1
for pid in $system_pids; do kill -0 "$pid"; done
echo 'Renamed binary, symlink, config paths with spaces: cache flushed; unrelated cache and system DNS preserved.'
'''.replace('@{', '$' + '{')
        result = subprocess.run(['docker', 'exec', '-i', CONTAINER, 'sh'],
                                input=script, text=True, capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('system DNS preserved', result.stdout)


if __name__ == '__main__':
    unittest.main()

