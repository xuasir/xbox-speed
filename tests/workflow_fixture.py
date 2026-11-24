#!/usr/bin/env python3
"""隔离后台工作流测试的数据，使用真实 UCI/procd/DNS 与可控下载资源。"""
import json
import re
import shlex
import subprocess
import sys
import time


GROUPS = ('cn', 'com', 'store', 'ps', 'xbox_old', 'switch')

CURL = r'''#!/bin/sh
work=${0%/bin/curl}
output=; previous=; range=0-1023; remote=9.9.9.9; url=
for argument in "$@"; do
    [ "$previous" != -o ] || output=$argument
    [ "$previous" != -r ] || range=$argument
    [ "$previous" != --resolve ] || remote=${argument##*:}
    previous=$argument; url=$argument
done
case "$url" in
*xbox-accel/selftest*) exec /usr/bin/curl "$@" ;;
*Game/GetAppPackage*) cp "$work/store.json" "$output"; exit 0 ;;
*119.29.29.29/d*) printf '9.9.9.9\n'; exit 0 ;;
esac
[ ! -e "$work/slow" ] || sleep 20
if [ -e "$work/fail" ] || { [ -e "$work/resource-fail" ] && [ "$range" = 0-1023 ]; }; then
    printf '404 0 0 0.1 0.2 %s' "$remote"; exit 0
fi
start=${range%-*}; end=${range#*-}; bytes=$((end-start+1))
speed=12000000; [ "$remote" != 8.8.4.4 ] || speed=6000000
printf '206 %s %s 0.1 0.2 %s' "$bytes" "$speed" "$remote"
'''

NSLOOKUP = r'''#!/bin/sh
case "$*" in *127.0.0.1*) exec /usr/bin/nslookup "$@" ;; esac
previous=; host=assets1.xboxlive.cn
for argument in "$@"; do
    case "$argument" in *xboxlive.*|*playstation.net|*nintendo.net|*microsoft.com) host=$argument ;; esac
done
printf 'Name: %s\nAddress: 9.9.9.9\n' "$host"
'''

CONNTRACK = r'''#!/bin/sh
work=${0%/bin/conntrack}
[ ! -e "$work/real-conntrack" ] || exec /usr/sbin/conntrack "$@"
[ ! -f "$work/conntrack.txt" ] || { cat "$work/conntrack.txt"; exit 0; }
bytes=$(($(date +%s)*500000))
printf 'ipv4 2 tcp 6 400 ESTABLISHED src=192.168.1.20 dst=9.9.9.9 sport=52001 dport=443 packets=4 bytes=500 src=9.9.9.9 dst=192.168.1.20 sport=443 dport=52001 packets=100 bytes=%s [ASSURED] mark=0 use=1\n' "$bytes"
'''

UCI = r'''#!/bin/sh
work=${0%/bin/uci}
if [ "$1" = commit ] && [ "$2" = xbox-accel ] && [ -e "$work/commit-fail" ]; then exit 1; fi
exec /sbin/uci "$@"
'''


class WorkflowFixture:
    def __init__(self, container, work=None):
        if not re.fullmatch(r'xa-[a-z0-9-]+', container):
            raise ValueError('仅允许 xa- 前缀的临时测试容器')
        self.container, self.work = container, work

    def run(self, command, check=True, timeout=90):
        result = subprocess.run(['docker', 'exec', self.container, 'sh', '-c', command],
                                text=True, capture_output=True, timeout=timeout)
        if check and result.returncode:
            raise AssertionError(f'{command}\nexit={result.returncode}\n{result.stdout}\n{result.stderr}')
        return result

    def write(self, path, content, executable=False):
        command = 'cat > ' + shlex.quote(path)
        if executable:
            command += '; chmod 755 ' + shlex.quote(path)
        result = subprocess.run(['docker', 'exec', '-i', self.container, 'sh', '-c', command],
                                input=content, text=True, capture_output=True, timeout=20)
        if result.returncode:
            raise AssertionError(result.stderr)

    def status(self):
        return json.loads(self.run('ubus call xbox-accel status').stdout)

    def setup(self):
        status = self.status()
        if status['enabled'] or status['running'] or status['testing']:
            raise AssertionError('需要插件已关闭且空闲的临时 OpenWrt 容器')
        self.work = self.run('mktemp -d /tmp/xa-workflow-fixture.XXXXXX').stdout.strip()
        self._validate_work()
        self.run(f"""set -e
cp /usr/bin/xbox-accel {self.work}/core
cp /etc/config/xbox-accel {self.work}/config
cp /etc/config/dhcp {self.work}/dhcp
cp /etc/crontabs/root {self.work}/cron
if [ -f /tmp/dhcp.leases ]; then cp /tmp/dhcp.leases {self.work}/leases; fi
if [ -d /etc/xbox-accel ]; then tar -cf {self.work}/state.tar -C /etc/xbox-accel .; fi
if [ -d /tmp/xbox-accel ]; then tar -cf {self.work}/memory.tar -C /tmp/xbox-accel .; fi
mkdir -p {self.work}/bin
rm -rf /etc/xbox-accel /tmp/xbox-accel
mkdir -p /etc/xbox-accel /tmp/xbox-accel
""")
        try:
            for name, body in [('curl', CURL), ('nslookup', NSLOOKUP), ('conntrack', CONNTRACK), ('uci', UCI)]:
                self.write(self.work + '/bin/' + name, body, True)
            self.write(self.work + '/store.json', json.dumps({'code': 200, 'data': [{
                'size': 157286400,
                'url': 'https://dl.delivery.mp.microsoft.com/store/test.pkg?P1=' +
                       str(int(time.time()) + 86400) + '&P4=TEST_STORE_SIGNATURE'}]}))
            core = self.run('cat ' + self.work + '/core').stdout
            self.write('/usr/bin/xbox-accel', core.replace('\n', '\nPATH="' + self.work + '/bin:$PATH"\n', 1), True)
            self.run("""set -e
uci set xbox-accel.main.enabled=0
for group in cn com store ps xbox_old switch; do uci set "xbox-accel.main.optimize_$group=0"; done
uci set xbox-accel.main.redirect_cn=0
uci set xbox-accel.main.use_seeds=0
uci set xbox-accel.main.use_isp_dns=0
uci set xbox-accel.main.isp=auto
uci set xbox-accel.main.passwall_direct=0
uci set xbox-accel.main.passwall_restart=0
uci set xbox-accel.main.health_interval=0
uci set xbox-accel.main.schedule_type=off
uci set xbox-accel.main.quick_size_mb=1
uci set xbox-accel.main.full_size_mb=1
uci set xbox-accel.main.max_candidates=2
uci set xbox-accel.main.top_n=1
uci commit xbox-accel
""")
            self.write('/tmp/dhcp.leases', f'{int(time.time()) + 86400} aa:bb:cc:dd:ee:ff 192.168.1.20 TEST_DEVICE_NAME *\n')
        except BaseException:
            self.restore()
            raise
        return self.work

    def _validate_work(self):
        if not self.work or not re.fullmatch(r'/tmp/xa-workflow-fixture\.[A-Za-z0-9]+', self.work):
            raise ValueError('测试备份目录无效')

    def restore(self):
        self._validate_work()
        self.run(f"""set -e
rm -f {self.work}/slow {self.work}/fail {self.work}/resource-fail {self.work}/commit-fail
xbox-accel cancel >/dev/null 2>&1 || true
xbox-accel disable >/dev/null 2>&1 || true
cp {self.work}/core /usr/bin/xbox-accel
chmod 755 /usr/bin/xbox-accel
uci revert xbox-accel
cp {self.work}/config /etc/config/xbox-accel
cp {self.work}/dhcp /etc/config/dhcp
cp {self.work}/cron /etc/crontabs/root
if [ -f {self.work}/leases ]; then cp {self.work}/leases /tmp/dhcp.leases; else rm -f /tmp/dhcp.leases; fi
rm -rf /etc/xbox-accel /tmp/xbox-accel
mkdir -p /etc/xbox-accel /tmp/xbox-accel
if [ -f {self.work}/state.tar ]; then tar -xf {self.work}/state.tar -C /etc/xbox-accel; fi
if [ -f {self.work}/memory.tar ]; then tar -xf {self.work}/memory.tar -C /tmp/xbox-accel; fi
/etc/init.d/dnsmasq restart
rm -rf {self.work}
""", timeout=60)


if __name__ == '__main__':
    fixture = WorkflowFixture(sys.argv[1], sys.argv[3] if len(sys.argv) > 3 else None)
    if sys.argv[2] == 'setup':
        print(fixture.setup())
    elif sys.argv[2] == 'restore':
        fixture.restore()
    else:
        raise SystemExit('用法: workflow_fixture.py <容器> setup|restore [备份目录]')

