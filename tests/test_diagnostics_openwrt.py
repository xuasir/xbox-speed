#!/usr/bin/env python3
"""Fault injection ONLY for a disposable OpenWrt Docker container."""
import json
import pathlib
import re
import subprocess
import sys
import time
import unittest

CONTAINER = sys.argv.pop(1) if len(sys.argv) > 1 else 'xa-test'


def run(command, check=True):
    result = subprocess.run(['docker','exec',CONTAINER,'sh','-c',command],text=True,capture_output=True,timeout=90)
    if check and result.returncode:
        raise AssertionError(f'{command}\nexit={result.returncode}\n{result.stdout}\n{result.stderr}')
    return result


def write(path, text):
    result=subprocess.run(['docker','exec','-i',CONTAINER,'sh','-c',f'cat > {path}; chmod 755 {path}'],input=text,text=True,capture_output=True)
    assert result.returncode==0,result.stderr


CURL='''#!/bin/sh
case "$*" in
*xbox-accel/selftest*) exec /usr/bin/curl "$@" ;;
*119.29.29.29/d*assets1.xboxlive.cn*) echo 9.9.9.9; exit 0 ;;
*119.29.29.29/d*) echo 8.8.4.4; exit 0 ;;
esac
mode=$(cat /tmp/xa-diag-mock/mode 2>/dev/null)
case "$mode" in
connect) echo 'curl: (7) Network is unreachable' >&2; printf '000 0 0 0 0 -'; exit 7 ;;
timeout) printf '206 64 0 0.1 4 9.9.9.9'; exit 28 ;;
zero_speed) printf '206 1048576 0 0.1 0.2 9.9.9.9'; exit 0 ;;
slow) sleep 20 ;;
partial_cn) case "$*" in *:9.9.9.9*) printf '404 10 0 0.1 0.2 9.9.9.9'; exit 0 ;; esac ;;
esac
printf '206 1048576 20000000 0.1 0.2 9.9.9.9'
'''
NS='''#!/bin/sh
case "$*" in
*127.0.0.1*) exec /usr/bin/nslookup "$@" ;;
*.cn*) printf 'Name: assets1.xboxlive.cn\\nAddress: 9.9.9.9\\n' ;;
*) printf 'Name: assets1.xboxlive.com\\nAddress: 8.8.4.4\\n' ;;
esac
'''


class DiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run('xbox-accel disable >/dev/null; cp /etc/config/xbox-accel /tmp/xa-diag-config-backup; mkdir -p /tmp/xa-diag-mock')
        write('/tmp/xa-diag-mock/curl',CURL);write('/tmp/xa-diag-mock/nslookup',NS)
        run("uci set xbox-accel.main.enabled=0; uci set xbox-accel.main.redirect_cn=0; uci set xbox-accel.main.isp=auto; uci set xbox-accel.main.passwall_direct=0; uci set xbox-accel.main.use_seeds=0; uci set xbox-accel.main.use_isp_dns=0; uci set xbox-accel.main.max_candidates=2; uci set xbox-accel.main.quick_size_mb=1; uci set xbox-accel.main.full_size_mb=1; uci set xbox-accel.main.cron='15 5 * * *'; uci commit xbox-accel; xbox-accel reset >/dev/null")

    @classmethod
    def tearDownClass(cls):
        run("uci -q del_list 'dhcp.@dnsmasq[0].address=/assets1.xboxlive.com/1.2.3.4' || true; uci commit dhcp; xbox-accel disable >/dev/null; cp /tmp/xa-diag-config-backup /etc/config/xbox-accel")

    def status(self):
        return json.loads(run('ubus call xbox-accel status').stdout)

    def wait_done(self, timeout=60):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            status=self.status()
            services=json.loads(run("ubus call service list '{\"name\":\"xbox-accel-task\"}'").stdout)
            active=any(i.get('running') for i in services.get('xbox-accel-task',{}).get('instances',{}).values())
            if not status['testing'] and not active:return status
            time.sleep(0.3)
        self.fail('task did not end')

    def test_01_partial_and_transport_errors(self):
        run('echo partial_cn > /tmp/xa-diag-mock/mode')
        partial=run('PATH=/tmp/xa-diag-mock:$PATH xbox-accel test',check=False)
        self.assertEqual(partial.returncode,2)
        record=self.status()['last_test']
        self.assertEqual(record['status'],'partial')
        self.assertEqual(record['groups']['cn']['status'],'failed')
        self.assertEqual(record['groups']['com']['status'],'success')
        self.assertEqual(record['application']['status'],'skipped')
        for mode,code in [('connect','CONNECT_FAILED'),('timeout','TIMEOUT')]:
            run(f'echo {mode} > /tmp/xa-diag-mock/mode')
            failed=run('PATH=/tmp/xa-diag-mock:$PATH xbox-accel test cn',check=False)
            self.assertEqual(failed.returncode,1)
            self.assertIn(code,failed.stderr)
            if mode=='connect': self.assertIn('Network is unreachable',failed.stderr)
            self.assertEqual(self.status()['last_test']['status'],'failed')
        run('rm -f /tmp/xa-diag-mock/mode; PATH=/tmp/xa-diag-mock:$PATH xbox-accel test')

    def test_02_interruption_and_corruption_not_success(self):
        run('cp /tmp/xbox-accel/live-task.json /tmp/xa-task-record-backup')
        write('/tmp/xbox-accel/live-task.json','{"status":"running","kind":"test","id":"interrupted-test","queued_at":1,"message":"not completed"}')
        self.assertEqual(self.status()['last_task']['status'],'interrupted')
        write('/tmp/xbox-accel/live-task.json','{broken-json')
        self.assertEqual(self.status()['last_task']['status'],'unknown')
        run('cp /tmp/xa-task-record-backup /tmp/xbox-accel/live-task.json')

    def test_03_dns_conflict_detected(self):
        run('uci set xbox-accel.main.enabled=1; uci commit xbox-accel; /etc/init.d/xbox-accel reload')
        run("uci add_list 'dhcp.@dnsmasq[0].address=/assets1.xboxlive.com/1.2.3.4'; uci commit dhcp; /etc/init.d/dnsmasq restart")
        run('xbox-accel start_verify')
        record=self.wait_done()['last_task']
        self.assertEqual(record['status'],'failed')
        self.assertIn(record['application']['code'],('DNS_MISMATCH','IPV6_BYPASS_POSSIBLE'))
        report=json.loads(run('cat /tmp/xbox-accel/last-application.json').stdout)
        self.assertTrue(any(r['code']=='DNS_MISMATCH' and r['expected']=='8.8.4.4' and r['observed']=='1.2.3.4' for r in report['environment']))
        self.assertEqual(report['summary']['xbox'],'unknown')

    def test_04_report_export_is_redacted_even_when_checks_fail(self):
        run("echo 'http://user:PRIVATE_EXPORT_PASS@assets1.xboxlive.com/private-report-path?token=SECRET_EXPORT_TEST ip=203.0.113.77 ipv6=fe80::abcd%br-lan mac=aa:bb:cc:dd:ee:ff' >> /tmp/xbox-accel/activity.log")
        admitted=json.loads(run("ubus call xbox-accel action '{\"command\":\"export\"}'").stdout)
        self.assertTrue(admitted['accepted'])
        status=self.wait_done()
        self.assertEqual(status['last_task']['code'],'REPORT_READY')
        self.assertEqual(status['last_task']['application']['status'],'failed')
        text=run('cat /tmp/xbox-accel/diagnostic-report.json').stdout
        report=json.loads(text)
        self.assertTrue(report['redacted'])
        self.assertNotIn('SECRET_EXPORT_TEST',text)
        self.assertNotIn('private-report-path',text)
        for private in ['PRIVATE_EXPORT_PASS','fe80::abcd','aa:bb:cc:dd:ee:ff']:
            self.assertNotIn(private,text)
        self.assertFalse(re.search(r'\b(?:\d{1,3}\.){3}\d{1,3}\b',text))
        self.assertEqual(report['current_checks']['summary']['router'],'failed')

    def test_05_unreadable_firewall_is_unknown(self):
        write('/tmp/xa-diag-mock/nft','#!/bin/sh\nexit 1\n')
        write('/tmp/xa-diag-mock/iptables-save','#!/bin/sh\nexit 1\n')
        result=json.loads(run('PATH=/tmp/xa-diag-mock:$PATH xbox-accel json_preflight').stdout)
        self.assertTrue(any(r['code']=='FIREWALL_UNREADABLE' and r['level']=='unknown' for r in result['environment']))

    def test_06_partial_firewall_read_remains_unknown(self):
        write('/tmp/xa-diag-mock/iptables-save','#!/bin/sh\nexit 0\n')
        result=json.loads(run('PATH=/tmp/xa-diag-mock:$PATH xbox-accel json_preflight').stdout)
        self.assertTrue(any(r['code']=='FIREWALL_PARTIAL_READ' and r['level']=='unknown' for r in result['environment']))

    def test_07_cli_failure_and_reboot_log_fallback(self):
        checked=run('PATH=/tmp/xa-diag-mock:$PATH xbox-accel check',check=False)
        self.assertEqual(checked.returncode,1)
        self.assertIn('DNS_MISMATCH',checked.stdout)
        self.assertIn('预期=8.8.4.4；实际=1.2.3.4',checked.stdout)
        before=self.status()
        self.assertEqual(before['last_task']['status'],'failed')
        run('test -s /etc/xbox-accel/activity-last.log; mv /tmp/xbox-accel /tmp/xa-diag-ram-backup')
        try:
            restored=self.status()
            self.assertEqual(restored['last_task']['id'],before['last_task']['id'])
            self.assertEqual(restored['last_task']['status'],'failed')
            self.assertEqual(restored['log_source'],'persisted')
            self.assertIn('TASK_FINAL',restored['log'])
            self.assertLessEqual(int(run('wc -c < /etc/xbox-accel/activity-last.log').stdout),131072)
        finally:
            run('rm -rf /tmp/xbox-accel; mv /tmp/xa-diag-ram-backup /tmp/xbox-accel')

    def test_08_zero_speed_and_cancellation_have_final_group_status(self):
        run('xbox-accel disable >/dev/null; echo zero_speed > /tmp/xa-diag-mock/mode')
        checked=run('PATH=/tmp/xa-diag-mock:$PATH xbox-accel test cn',check=False)
        self.assertEqual(checked.returncode,1)
        self.assertIn('INVALID_SPEED',checked.stderr)
        run('echo slow > /tmp/xa-diag-mock/mode')
        admitted=run('PATH=/tmp/xa-diag-mock:$PATH xbox-accel start_test cn').stdout
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            current=self.status()
            if current['testing'] and current['last_task']['stage']=='quick_cn': break
            time.sleep(0.2)
        else: self.fail('slow task did not start')
        self.assertIn(current['last_task']['id'],admitted)
        run('xbox-accel cancel')
        ended=self.wait_done()['last_task']
        self.assertEqual(ended['status'],'cancelled')
        self.assertEqual(ended['groups']['cn']['status'],'cancelled')
        self.assertGreater(ended['finished_at'],0)
        self.assertEqual(ended['exit_code'],130)
        run('rm -f /tmp/xa-diag-mock/mode')

    def test_09_full_domain_coverage_and_resource_failure_summary(self):
        run("uci -q del_list 'dhcp.@dnsmasq[0].address=/assets1.xboxlive.com/1.2.3.4'; uci commit dhcp; rm -f /tmp/xa-diag-mock/nft /tmp/xa-diag-mock/iptables-save; uci set xbox-accel.main.enabled=1; uci set xbox-accel.main.redirect_cn=1; uci commit xbox-accel; /etc/init.d/dnsmasq restart; /etc/init.d/xbox-accel reload")
        run('PATH=/tmp/xa-diag-mock:$PATH xbox-accel check')
        report=json.loads(run('cat /tmp/xbox-accel/last-application.json').stdout)
        hosts=set('assets1.xboxlive.cn assets2.xboxlive.cn d1.xboxlive.cn d2.xboxlive.cn assets1.xboxlive.com assets2.xboxlive.com d1.xboxlive.com d2.xboxlive.com xvcf1.xboxlive.com xvcf2.xboxlive.com dlassets.xboxlive.com dlassets2.xboxlive.com 5.xb.delivery.microsoft.com assets1.xboxlive.com.edgesuite.net cl-glcb907925.gcdn.co fg.microsoft.map.fastly.net'.split())
        for server in ['127.0.0.1:5335','127.0.0.1:53']:
            for host in hosts:
                for record in ['A','AAAA']:
                    row=next(r for r in report['environment'] if r['name']==f'{record} {host} @{server}')
                    self.assertEqual(row['level'],'ok',row)
            self.assertEqual(sum(r['name'].startswith('SRV ') and r['name'].endswith('@'+server) and r['level']=='ok' for r in report['environment']),3)
        self.assertEqual(report['summary']['router'],'verified')
        self.assertEqual(report['summary']['measurement'],'verified')
        self.assertEqual(report['summary']['xbox'],'unknown')
        run('echo partial_cn > /tmp/xa-diag-mock/mode')
        failed=run('PATH=/tmp/xa-diag-mock:$PATH xbox-accel check',check=False)
        self.assertEqual(failed.returncode,1)
        task=self.status()['last_task']
        self.assertEqual(task['status'],'failed')
        self.assertEqual(task['application']['code'],'HTTP_NOT_FOUND')
        status=self.status()
        self.assertEqual(status['last_application']['status'],'failed')
        self.assertEqual(status['last_application']['router'],'verified')
        self.assertEqual(status['last_application']['measurement'],'failed')
        run('rm -f /tmp/xa-diag-mock/mode')

    def test_10_cancel_queued_worker(self):
        run('xbox-accel disable >/dev/null')
        write('/tmp/xbox-accel/live-task.json',json.dumps({'id':'queued-cancel-test','kind':'test','trigger':'manual_ui','stage':'queued','status':'queued','queued_at':1,'started_at':0,'groups':{'cn':{'status':'skipped','code':'NOT_REQUESTED'},'com':{'status':'skipped','code':'NOT_REQUESTED'}}}))
        run('xbox-accel cancel')
        task=self.status()['last_task']
        self.assertEqual(task['id'],'queued-cancel-test')
        self.assertEqual(task['status'],'cancelled')
        self.assertEqual(task['exit_code'],130)

    def test_11_persistence_failure_keeps_current_result_and_warning(self):
        write('/tmp/xa-diag-mock/cp','#!/bin/sh\ncase "$2" in /etc/xbox-accel/*) exit 1 ;; esac\nexec /bin/cp "$@"\n')
        write('/tmp/xa-diag-mock/mv','#!/bin/sh\ncase "$2" in /etc/xbox-accel/activity-last.log) exit 1 ;; esac\nexec /bin/mv "$@"\n')
        try:
            tested=run('PATH=/tmp/xa-diag-mock:$PATH xbox-accel test cn')
            self.assertIn('PERSIST_FAILED',tested.stderr)
            self.assertIn('LOG_PERSIST_FAILED',tested.stderr)
            status=self.status()
            self.assertTrue(status['last_task']['persistence_warning'])
            self.assertEqual(status['last_test']['id'],status['last_task']['id'])
            self.assertEqual(status['last_test']['groups']['cn']['status'],'success')
        finally:
            run('rm -f /tmp/xa-diag-mock/cp /tmp/xa-diag-mock/mv')

    def test_12_passwall_empty_and_missing_members(self):
        write('/tmp/xa-diag-mock/nft','''#!/bin/sh
case "$*" in
"list table inet passwall"|"list set inet passwall psw_white") exit 0 ;;
"get element inet passwall psw_white "*) exit 1 ;;
esac
exec /usr/sbin/nft "$@"
''')
        run('uci set xbox-accel.main.enabled=1; uci set xbox-accel.main.redirect_cn=0; uci set xbox-accel.main.passwall_direct=1; uci commit xbox-accel; mv /etc/xbox-accel/best_cn /tmp/xa-diag-best-cn; mv /etc/xbox-accel/best_com /tmp/xa-diag-best-com')
        try:
            report=json.loads(run('PATH=/tmp/xa-diag-mock:$PATH xbox-accel json_env').stdout)
            row=next(r for r in report['environment'] if r['name']=='Passwall集合')
            self.assertEqual(row['level'],'skipped')
            self.assertEqual(row['code'],'NO_SELECTED_NODE')
            run('mv /tmp/xa-diag-best-cn /etc/xbox-accel/best_cn; mv /tmp/xa-diag-best-com /etc/xbox-accel/best_com')
            report=json.loads(run('PATH=/tmp/xa-diag-mock:$PATH xbox-accel json_env').stdout)
            self.assertEqual(report['summary']['direct_set'],'failed')
            self.assertTrue(any(r['code']=='DIRECT_SET_MISSING' and r['level']=='fail' for r in report['environment']))
        finally:
            run('test ! -f /tmp/xa-diag-best-cn || mv /tmp/xa-diag-best-cn /etc/xbox-accel/best_cn; test ! -f /tmp/xa-diag-best-com || mv /tmp/xa-diag-best-com /etc/xbox-accel/best_com; rm -f /tmp/xa-diag-mock/nft; uci set xbox-accel.main.enabled=0; uci set xbox-accel.main.passwall_direct=0; uci commit xbox-accel')


if __name__=='__main__':unittest.main()
