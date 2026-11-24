#!/usr/bin/env python3
"""Check service lock ownership across a real shell-spawned background worker."""
import fcntl
import json
import os
import pathlib
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DESCRIPTORS = (7, 8, 9, 42, 63, 1000)


@unittest.skipUnless(shutil.which('bash'), 'requires bash for OpenWrt-style high descriptors')
class ServiceLockTests(unittest.TestCase):
    def service_action(self, contents, action='stop', limit=1):
        source = (ROOT / 'files/usr/bin/xbox-accel').read_text()
        helpers = '\n'.join(re.search(r'^' + name + r'\(\) \{\n.*?^\}', source, re.M | re.S).group(0)
                            for name in ('close_service_fds', 'kill_tree', 'run_service_action'))
        # An exec-only fixture has no descendants; every signalled PID is our child.
        helpers = 'children_of() { :; }\n' + helpers.replace('/bin/sh /etc/rc.common', '/bin/bash')
        with tempfile.TemporaryDirectory() as directory:
            worker = pathlib.Path(directory) / 'init.sh'
            worker.write_text(contents)
            script = helpers + f'''
RUN_DIR={shlex.quote(directory)}
INIT={shlex.quote(str(worker))}
XBOX_ACCEL_SERVICE_TIMEOUT={limit}
run_service_action {shlex.quote(action)}
'''
            started = time.monotonic()
            result = subprocess.run([shutil.which('bash'), '-c', script], capture_output=True,
                                    text=True, timeout=6)
            self.assertEqual(list(pathlib.Path(directory).glob('service-timeout.*')), [])
            return result, time.monotonic() - started

    def test_entire_service_operation_has_deadline(self):
        result, elapsed = self.service_action('exec sleep 60\n')
        self.assertEqual(result.returncode, 124, result.stdout + result.stderr)
        self.assertIn('SERVICE_TIMEOUT', result.stderr)
        self.assertLess(elapsed, 5)

    def test_service_failure_is_returned_immediately(self):
        result, elapsed = self.service_action('exit 17\n')
        self.assertEqual(result.returncode, 17, result.stdout + result.stderr)
        self.assertLess(elapsed, 3)

    def test_background_service_releases_locks_while_parent_keeps_ownership(self):
        source = (ROOT / 'files/usr/bin/xbox-accel').read_text()
        svc = '\n'.join(re.search(r'^' + name + r'\(\) \{\n.*?^\}', source, re.M | re.S).group(0)
                        for name in ('close_service_fds', 'svc'))
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            worker = root / 'worker.py'
            worker.write_text('''import json, os, pathlib, sys, time
root = pathlib.Path(sys.argv[1])
opened = []
for fd in (7, 8, 9, 42, 63, 1000):
    try:
        os.fstat(fd)
        opened.append(fd)
    except OSError:
        pass
(root / 'worker.json').write_text(json.dumps({'pid': os.getpid(), 'opened': opened}))
time.sleep(60)
''')
            locker = root / 'locker.py'
            locker.write_text('''import fcntl
for fd in (7, 8, 9, 42, 63, 1000):
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
''')
            service = root / 'service.sh'
            service.write_text('''#!/bin/sh
"$1" "$2" "$3" </dev/null >/dev/null 2>&1 &
echo "$!" > "$3/worker.pid"
''')
            quote = shlex.quote
            script = svc + '\n' + '\n'.join(
                f'exec {fd}>{quote(str(root / str(fd)))}' for fd in DESCRIPTORS
            ) + f'''
{quote(sys.executable)} {quote(str(locker))} || exit 1
svc /bin/sh {quote(str(service))} {quote(sys.executable)} {quote(str(worker))} {quote(directory)} || exit 1
echo ready
read -r release
'''
            parent = subprocess.Popen(
                [shutil.which('bash'), '-c', script], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                deadline = time.monotonic() + 5
                while True:
                    try:
                        worker_info = json.loads((root / 'worker.json').read_text())
                        break
                    except (FileNotFoundError, json.JSONDecodeError):
                        pass
                    if parent.poll() is not None:
                        self.fail('service exited early: ' + parent.stderr.read())
                    if time.monotonic() >= deadline:
                        self.fail('background worker did not become ready')
                    time.sleep(0.02)
                self.assertEqual(parent.stdout.readline().strip(), 'ready')
                self.assertIsNone(parent.poll())
                # The caller retains every lock while the nested service runs.
                for fd in DESCRIPTORS:
                    with (root / str(fd)).open('w') as lock:
                        with self.assertRaises(BlockingIOError, msg=f'parent lost fd {fd}'):
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                parent.communicate('\n', timeout=5)
                self.assertEqual(parent.returncode, 0)
                os.kill(worker_info['pid'], 0)
                # A subsequent init/upgrade can acquire the locks while the worker lives.
                for fd in DESCRIPTORS:
                    with (root / str(fd)).open('w') as lock:
                        try:
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            self.fail(f'background service retained fd {fd}')
                self.assertEqual(worker_info['opened'], [])
            finally:
                if parent.poll() is None:
                    parent.kill()
                    parent.communicate(timeout=5)
                pid_file = root / 'worker.pid'
                if pid_file.exists():
                    try:
                        os.kill(int(pid_file.read_text()), signal.SIGTERM)
                    except ProcessLookupError:
                        pass


if __name__ == '__main__':
    unittest.main()
