#!/usr/bin/env python3
"""Exercise init boot hook with isolated shell mocks (no host services)."""
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

class AutostartTests(unittest.TestCase):
    def boot(self, autostart, enabled):
        script = (ROOT / 'files/etc/init.d/xbox-accel').read_text()
        mocks = '''
config_load() { :; }
config_get_bool() { case "$3" in autostart) eval "$1=$AUTO" ;; enabled) eval "$1=$ENABLED" ;; esac; }
mock_prog() { echo "$1"; }
start() { start_service; }
procd_open_instance() { :; }
procd_set_param() { :; }
procd_close_instance() { :; }
PROG=mock_prog
CONF=/dev/null
boot
'''
        return subprocess.run(['sh', '-c', script + mocks], env={'AUTO': str(autostart), 'ENABLED': str(enabled), 'PATH': '/usr/bin:/bin'}, capture_output=True, text=True)

    def test_boot_without_autostart_cleans_only(self):
        result = self.boot(0, 1)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), '_teardown')

    def test_disabled_never_starts(self):
        result = self.boot(1, 0)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), '_teardown')

    def test_enabled_autostart_sets_up(self):
        result = self.boot(1, 1)
        # Empty mocked resolver config prevents actual procd registration.
        self.assertEqual(result.stdout.strip(), '_setup')

if __name__ == '__main__':
    unittest.main()
