#!/usr/bin/env python3
"""用隔离数据目录验证有验收记录、测速记录和连接时的状态响应。"""
import io
import json
import pathlib
import re
import shlex
import subprocess
import sys
import tarfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTAINER = sys.argv[1] if len(sys.argv) > 1 else 'xa-kwrt-r66s'
assert re.fullmatch(r'xa-[a-z0-9-]+', CONTAINER), '仅允许 xa- 前缀的临时测试容器'
GROUPS = ('cn', 'com', 'store', 'ps', 'xbox_old', 'switch')


def run(command, timeout=60):
    return subprocess.run(['docker', 'exec', CONTAINER, 'sh', '-c', command],
                          text=True, capture_output=True, check=True, timeout=timeout)


def write(file, content):
    subprocess.run(['docker', 'exec', '-i', CONTAINER, 'sh', '-c', 'cat > ' + shlex.quote(file)],
                   input=content, text=True, capture_output=True, check=True, timeout=10)


def profile(content, function):
    renamed = '_status_profile_' + function
    content, count = re.subn(r'^' + function + r'\(\)', renamed + '()', content, count=1, flags=re.M)
    assert count == 1, function
    return content + f'''
{function}() {{
    local started finished result
    started=$(cut -d ' ' -f 1 /proc/uptime)
    {renamed} "$@"; result=$?
    finished=$(cut -d ' ' -f 1 /proc/uptime)
    printf 'STATUS_PHASE {function} %s %s\\n' "$started" "$finished" >&2
    return "$result"
}}
'''


work = run('mktemp -d /tmp/xa-status-smoke.XXXXXX').stdout.strip()
assert re.fullmatch(r'/tmp/xa-status-smoke\.[a-zA-Z0-9]+', work)
try:
    core = (ROOT / 'files/usr/bin/xbox-accel').read_text()
    for name, value in {'STATE_DIR':work + '/state', 'TMP_DIR':work + '/memory',
                        'RUN_DIR':work + '/run', 'SHARE_DIR':work + '/share',
                        'RESOLVER_CONF':work + '/resolver.conf', 'REDIRECT_CONF':work + '/redirect.conf',
                        'REDIRECT_ROOT':work + '/redirect'}.items():
        core = re.sub(r'^' + name + r'=.*$', name + '=' + value, core, count=1, flags=re.M)
    core = core.replace('\n', f'\nPATH="{work}/bin:$PATH"\nexport UCI_CONFIG_DIR="{work}/config"\n', 1)
    marker = '\ncase "$1" in\ntest)'
    assert marker in core
    core = core.replace(marker, '\ncase "$1" in\n_fixture_signatures) configuration_signature; observation_signature; exit ;;\nesac\n' + marker, 1)
    config = (ROOT / 'files/etc/config/xbox-accel').read_text()
    for key in ('enabled',) + tuple('optimize_' + group for group in GROUPS):
        config = re.sub(r"(option " + key + r") '[01]'", r"\1 '1'", config)
    files = {'core':core, 'bin/uci':f'#!/bin/sh\nexec /sbin/uci -c "{work}/config" "$@"\n',
             'config/xbox-accel':config, 'config/dhcp':"config dnsmasq\n",
             'config/network':"config interface 'lan'\n option ipaddr '192.168.1.1'\n",
             'config/uhttpd':"config uhttpd 'main'\n", 'run/.keep':'', 'memory/.keep':''}
    for path in (ROOT / 'files/usr/share/xbox-accel').iterdir():
        if path.is_file():
            content = path.read_text()
            for function in {'application.sh':['append_application_summary'],
                             'measurement.sh':['append_measurements'],
                             'observation.sh':['observation_document'],
                             'diagnostics.sh':['json_append_document']}.get(path.name, []):
                content = profile(content, function)
            files['share/' + path.name] = content
    domains = {'cn':'assets1.xboxlive.cn', 'com':'assets1.xboxlive.com',
               'store':'dl.delivery.mp.microsoft.com', 'ps':'gst.prod.dl.playstation.net',
               'xbox_old':'dlassets.xboxlive.cn', 'switch':'ctest-dl-lp1.cdn.nintendo.net'}
    now = int(time.time())
    for group in GROUPS:
        files['state/best_' + group] = '9.9.9.9\n'
        files['state/domains_' + group] = domains[group] + '\n'
        files['memory/last_' + group] = f'9.9.9.9 12000000 {now}\n'
        files['state/measurements/' + group + '.json'] = json.dumps({
            'id':'fixture-measurement', 'group':group, 'ip':'9.9.9.9', 'speed_bps':12000000,
            'tested_at':now, 'resource_signature':'changed-resource', 'domains':domains[group]})
    task = {'id':'fixture-task', 'status':'success', 'kind':'test', 'code':'OK',
            'groups':{group:{'status':'success', 'code':'OK', 'ip':'9.9.9.9'} for group in GROUPS}}
    files['memory/live-task.json'] = json.dumps(task)
    files['memory/last-speed-task.json'] = json.dumps(task)
    files['memory/activity.log'] = ''.join(f'11-25 12:00:00 fixture {index} ' + 'x' * 160 + '\n' for index in range(300))
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w') as tar:
        for name, content in files.items():
            data = content.encode(); info = tarfile.TarInfo(name); info.size = len(data)
            info.mode = 0o755 if name in ('core', 'bin/uci') else 0o644
            tar.addfile(info, io.BytesIO(data))
    subprocess.run(['docker', 'exec', '-i', CONTAINER, 'tar', '-xf', '-', '-C', work],
                   input=archive.getvalue(), capture_output=True, check=True, timeout=20)
    signature, observation_signature = run(shlex.quote(work + '/core') + ' _fixture_signatures').stdout.splitlines()
    now = int(time.time())
    report = {'checked_at':now, 'configuration_signature':signature,
              'summary':{'router':'verified', 'measurement':'verified', 'direct_set':'verified', 'passed':192,
                         'groups':{group:{'status':'verified', 'passed':32} for group in GROUPS}},
              'environment':[{'name':f'fixture {index}', 'level':'ok', 'code':'VERIFIED', 'scope':'router',
                              'detail':'x' * 160, 'expected':'expected', 'observed':'observed'} for index in range(192)]}
    observation = {'enabled':True, 'available':True, 'observed_at':now, 'configuration_signature':observation_signature,
                   'devices':[{'ip':f'192.168.1.{20+index}', 'name':f'device {index}'} for index in range(32)],
                   'flows':[{'device_ip':f'192.168.1.{20+index}', 'target_ip':'9.9.9.9', 'groups':'cn',
                             'status':'matched', 'path':'direct', 'protocol':'tcp', 'target_port':443,
                             'shared_node':False, 'activity':'downloading', 'observed_at':now,
                             'speed_bps':12000000, 'bytes_received':120000000} for index in range(32)],
                   'redirects':[{'device_ip':'192.168.1.20', 'observed_at':now,
                                 'host':'assets1.xboxlive.com', 'target_host':'assets1.xboxlive.cn'} for _ in range(100)]}
    write(work + '/memory/last-application.json', json.dumps(report))
    write(work + '/memory/observation.json', json.dumps(observation))
    started = time.monotonic()
    result = run(shlex.quote(work + '/core') + ' json_status')
    elapsed = time.monotonic() - started
    status = json.loads(result.stdout)
    assert status['enabled'] and status['last_application']['same_configuration']
    assert status['last_application']['status'] == 'verified' and status['last_application']['passed'] == 192
    assert status['last_task']['id'] == 'fixture-task' and len(status['measurements']) == 6
    assert len(status['observation']['flows']) == 32 and len(status['observation']['redirects']) == 100
    assert status['log'].splitlines()[-1].startswith('11-25 12:00:00 fixture 299 ')
    phases = []
    for line in result.stderr.splitlines():
        if line.startswith('STATUS_PHASE '):
            _, name, begin, end = line.split(); phases.append({'phase':name, 'seconds':round(float(end)-float(begin), 3)})
    # 配置更新后，保留历史验收结果并明确标记适用状态。
    write(work + '/config/xbox-accel', config.replace("option enabled '1'", "option enabled '0'"))
    changed = json.loads(run(shlex.quote(work + '/core') + ' json_status').stdout)
    assert changed['enabled'] is False
    assert changed['last_application']['same_configuration'] is False and changed['last_application']['stale'] is True
    assert changed['last_application']['passed'] == 192
    print(json.dumps({'result':'PASS', 'seconds':round(elapsed, 3), 'bytes':len(result.stdout.encode()), 'phases':phases}))
finally:
    run('rm -rf ' + shlex.quote(work))
