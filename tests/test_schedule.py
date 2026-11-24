#!/usr/bin/env python3
"""Exercise actual POSIX shell schedule helpers without router dependencies."""
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'files/usr/bin/xbox-accel').read_text()
HELPERS = SOURCE[SOURCE.index('schedule_cron() {'):SOURCE.index('\nhealth_label() {')]


def run_helper(function, **config):
    # Pass configuration as positional values, not executable shell interpolation.
    script = '''cfg() {
        case "$1" in
        schedule_type) v=$CONFIG_KIND ;; schedule_time) v=$CONFIG_CLOCK ;;
        schedule_weekday) v=$CONFIG_WEEKDAY ;; schedule_monthday) v=$CONFIG_MONTHDAY ;;
        cron) v=$CONFIG_LEGACY ;; esac
        printf '%s\\n' "${v:-$2}"
    }
    CONFIG_KIND=$1; CONFIG_CLOCK=$2; CONFIG_WEEKDAY=$3; CONFIG_MONTHDAY=$4; CONFIG_LEGACY=$5
    ''' + HELPERS + '\n' + function
    return subprocess.run(['sh', '-c', script, 'schedule-test',
                           *[config.get(key, '') for key in ('kind', 'clock', 'weekday', 'monthday', 'legacy')]],
                          capture_output=True, text=True)


class ScheduleTests(unittest.TestCase):
    def test_daily_weekly_monthly(self):
        for config, expected, label in [
            ({'kind': 'daily', 'clock': '05:15'}, '15 5 * * *', '每天 05:15'),
            ({'kind': 'weekly', 'clock': '23:59', 'weekday': '6'}, '59 23 * * 6', '每周六 23:59'),
            ({'kind': 'monthly', 'clock': '00:00', 'monthday': '31'}, '0 0 31 * *', '每月 31 号 00:00（无该日期的月份跳过）'),
        ]:
            with self.subTest(config=config):
                result = run_helper('schedule_cron', **config)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)
                self.assertEqual(run_helper('schedule_label', **config).stdout.strip(), label)

    def test_old_cron_preserved(self):
        for legacy in ['0 */6 * * *', '15 1 * * 7', '0 8 15 * *', '5 9 1,15 * *']:
            self.assertEqual(run_helper('schedule_cron', legacy=legacy).stdout.strip(), legacy)
        self.assertEqual(run_helper('schedule_label', legacy='15 1 * * 7').stdout.strip(), '每周日 01:15')
        self.assertEqual(run_helper('schedule_label', legacy='0 8 15 * *').stdout.strip(), '每月 15 号 08:00')
        self.assertEqual(run_helper('schedule_label', legacy='5 9 1,15 * *').stdout.strip(), '保留的自定义计划')

    def test_invalid_new_schedule_rejected(self):
        for config in [
            {'kind': 'hourly'}, {'kind': 'daily', 'clock': '24:00'},
            {'kind': 'daily', 'clock': '12:60'}, {'kind': 'daily', 'clock': '5:15'},
            {'kind': 'daily', 'clock': '12:00; reboot'},
            {'kind': 'weekly', 'weekday': '7'}, {'kind': 'weekly', 'weekday': '*'},
            {'kind': 'monthly', 'monthday': '0'}, {'kind': 'monthly', 'monthday': '32'},
            {'kind': 'monthly', 'monthday': '1; reboot'},
        ]:
            with self.subTest(config=config):
                self.assertNotEqual(run_helper('schedule_cron', **config).returncode, 0)
                self.assertEqual(run_helper('schedule_label', **config).stdout.strip(), '测速计划设置无效')

    def test_schedule_consumers_use_resolved_cron(self):
        self.assertIn('sched=$(schedule_cron)', SOURCE)
        self.assertIn('schedule=$(schedule_cron)', SOURCE)
        for filename in ('verification.sh', 'quick-check.sh'):
            text = (ROOT / 'files/usr/share/xbox-accel' / filename).read_text()
            self.assertIn('$(schedule_cron)', text)
            self.assertIn('$(schedule_label)', text)
            self.assertNotIn("$(cfg cron '15 5 * * *')", text)


if __name__ == '__main__':
    unittest.main()
