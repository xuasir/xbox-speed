#!/usr/bin/env python3
"""Exercise the schedule converter and boot hook without touching host services."""
import pathlib
import re
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def function(path, name):
    return re.search(r'^' + name + r'\(\) \{\n.*?^\}',
                     (ROOT / path).read_text(), re.M | re.S).group(0)


class ScheduleStartupTests(unittest.TestCase):
    def run_shell(self, body):
        result = subprocess.run(['sh', '-c', body], text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout.strip()

    def schedule(self, kind, time='05:15', weekday='0', monthday='1'):
        converter = function('files/usr/bin/xbox-accel', 'schedule_cron')
        return self.run_shell('''
cfg() {
    case "$1" in
    schedule_type) printf '%s' "$KIND" ;;
    schedule_time) printf '%s' "$TIME" ;;
    schedule_weekday) printf '%s' "$WEEKDAY" ;;
    schedule_monthday) printf '%s' "$MONTHDAY" ;;
    cron) echo '0 */6 * * *' ;;
    esac
}
''' + converter + '\n' +
            f"KIND='{kind}'; TIME='{time}'; WEEKDAY='{weekday}'; MONTHDAY='{monthday}'\n" +
            'schedule_cron; echo result:$?')

    def test_calendar_schedules(self):
        self.assertEqual(self.schedule('daily', '00:00'), '0 0 * * *\nresult:0')
        self.assertEqual(self.schedule('daily', '23:59'), '59 23 * * *\nresult:0')
        for weekday in range(7):
            self.assertEqual(self.schedule('weekly', '08:35', str(weekday)),
                             f'35 8 * * {weekday}\nresult:0')
        for monthday in ('1', '28', '29', '30', '31'):
            self.assertEqual(self.schedule('monthly', '04:09', monthday=monthday),
                             f'9 4 {monthday} * *\nresult:0')

    def test_legacy_schedule_is_preserved(self):
        self.assertEqual(self.schedule('legacy'), '0 */6 * * *\nresult:0')

    def test_invalid_calendar_fields_are_rejected(self):
        for time in ('24:00', '12:60', '5:15', '12:00; reboot'):
            self.assertEqual(self.schedule('daily', time), 'result:1')
        for weekday in ('7', '-1', '1,3'):
            self.assertEqual(self.schedule('weekly', weekday=weekday), 'result:1')
        for monthday in ('0', '32', '1.5', '1,15'):
            self.assertEqual(self.schedule('monthly', monthday=monthday), 'result:1')
        self.assertEqual(self.schedule('hourly'), 'result:1')

    def test_boot_obeys_autostart_setting(self):
        boot = function('files/etc/init.d/xbox-accel', 'boot')
        for autostart, expected in (('1', 'start'), ('0', '_teardown')):
            output = self.run_shell('''
PROG=program
config_load() { :; }
config_get_bool() { eval "$1=$AUTOSTART"; }
program() { echo "$1"; }
start() { echo start; }
''' + boot + f'\nAUTOSTART={autostart}; boot')
            self.assertEqual(output, expected)

    def test_disabled_service_cleans_up_without_starting_resolver(self):
        start = function('files/etc/init.d/xbox-accel', 'start_service')
        output = self.run_shell('''
PROG=program
config_load() { :; }
config_get_bool() { eval "$1=0"; }
program() { echo "$1"; return 3; }
''' + start + '\nstart_service; echo result:$?')
        self.assertEqual(output, '_teardown\nresult:3')


if __name__ == '__main__':
    unittest.main()
