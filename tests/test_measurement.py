#!/usr/bin/env python3
"""独立测速复用真实采样流程，验证关闭功能、配置隔离和失败保留。"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_platforms import function, helpers, PLATFORMS

LIB = PLATFORMS.with_name('measurement.sh')
DIAGNOSTICS = PLATFORMS.with_name('diagnostics.sh')


class MeasurementTests(unittest.TestCase):
    def run_measurement(self, groups, fail=False, extra=''):
        with tempfile.TemporaryDirectory() as directory:
            body = helpers() + '\n' + '\n'.join(function(name) for name in (
                'fmt_speed', 'alive', 'stage_run', 'verify_domains', 'test_group', 'cmd_test')) + r'''
. "$MEASUREMENT_LIB"
. "$DIAGNOSTICS_LIB"
TASK_MEMORY_READY=1
flock() { return 0; }
lock() { :; }
task_begin() { TASK_ID=20261124T080000-12; TASK_MEMORY_READY=1; }
task_stage() { TASK_STAGE=$1; }
task_snapshot() { :; }
task_event() { printf '%s\n' "$2" >&2; }
say() { :; }
is_enabled() { return 1; }
candidates() { printf '8.8.4.4\n9.9.9.9\n'; }
passwall_direct() { touch route-mutated; }
apply() { touch application-called; }
probe() {
    [ "$FAIL" != 1 ] || { printf '404 0 0 0 0.1 0.2 %s' "$1"; return; }
    speed=20000000; [ "$1" != 9.9.9.9 ] || speed=30000000
    printf '206 1048576 %s 0 0.1 0.2 %s' "$speed" "$1"
}
save_measurement() { printf '%s %s\n' "$2" "$3" > "state/measurement_$1"; }
for group in cn com store ps xbox_old switch; do
    printf '0\n' > "config/optimize_$group"
    printf '8.8.4.4\n' > "state/best_$group"
    printf 'old-domain\n' > "state/domains_$group"
    printf '8.8.4.4 20000000 123\n' > "tmp/last_$group"
done
printf '100\n' > config/switch_threshold
printf 'invalid-port\n' > config/resolver_port
printf 'invalid-instance\n' > config/dnsmasq_instance
printf '0\n' > config/enabled
''' + extra + r'''
cmd_test "$1" measure
result=$?
printf 'exit=%s\n' "$result"
printf 'task=%s ps=%s cn=%s\n' "$TASK_CODE" "$T_PS_CODE" "$T_CN_CODE"
for group in cn com store ps xbox_old switch; do
    printf '%s=%s/%s/%s\n' "$group" "$(cat state/best_$group)" "$(cat state/domains_$group)" "$(cat tmp/last_$group)"
    [ ! -f "state/measurement_$group" ] || printf 'measured:%s=%s\n' "$group" "$(cat state/measurement_$group)"
    test "$(cat config/optimize_$group)" = 0 || exit 99
done
test ! -e route-mutated && test ! -e application-called || exit 98
'''
            result = subprocess.run(['sh', '-c', body, 'measurement-test', groups], cwd=directory,
                                    env={**os.environ, 'PLATFORM_LIB': str(PLATFORMS),
                                         'MEASUREMENT_LIB': str(LIB), 'DIAGNOSTICS_LIB': str(DIAGNOSTICS),
                                         'FAIL': '1' if fail else '0'}, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout + result.stderr

    def test_disabled_platform_measures_fastest_without_replacing_runtime(self):
        result = self.run_measurement('ps')
        self.assertIn('exit=0', result.splitlines())
        self.assertIn('measured:ps=9.9.9.9 30000000', result)
        self.assertIn('ps=8.8.4.4/old-domain/8.8.4.4 20000000 123', result)

    def test_multi_selection_ignores_feature_switches(self):
        result = self.run_measurement('cn,switch')
        self.assertIn('measured:cn=', result)
        self.assertIn('measured:switch=', result)
        self.assertEqual(result.count('measured:'), 2)

    def test_unrelated_platform_and_network_config_do_not_block_sampling(self):
        result = self.run_measurement('cn', extra="printf 'https://invalid.example/file\\n' > config/test_url_store\n")
        self.assertIn('exit=0', result.splitlines())
        self.assertEqual(result.count('measured:'), 1)

    def test_failed_sampling_preserves_runtime_and_saved_result(self):
        result = self.run_measurement('cn', fail=True)
        self.assertIn('exit=1', result.splitlines())
        self.assertNotIn('measured:', result)
        self.assertIn('cn=8.8.4.4/old-domain/8.8.4.4 20000000 123', result)

    def test_selection_is_a_fixed_allowlist(self):
        for value in ('ps;touch injected', ',ps', 'ps,', 'ps,,switch', 'ea', 'ps switch'):
            result = subprocess.run(['sh', '-c', '. "$1"; ALL_GROUPS="cn com store ps xbox_old switch"; group_valid() { case "$1" in cn|com|store|ps|xbox_old|switch) return 0 ;; *) return 1 ;; esac; }; measurement_groups "$2"',
                                     'groups-test', str(LIB), value], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0, value)


if __name__ == '__main__':
    unittest.main()
