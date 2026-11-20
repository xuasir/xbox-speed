#!/usr/bin/env python3
"""Exercise release orchestration with a fake gh CLI; never contacts GitHub."""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from build_ipk import build, version
from ci_release import resolve_release

SHA = '0123456789abcdef0123456789abcdef01234567'
FAKE_GH = '''#!/usr/bin/env python3
import json,os,pathlib,sys
args=sys.argv[1:]
log=pathlib.Path(os.environ['FAKE_GH_LOG'])
with log.open('a') as f: f.write(json.dumps(args)+'\\n')
state=pathlib.Path(os.environ['FAKE_GH_STATE'])
if args[0]=='api': print(os.environ.get('FAKE_REMOTE_SHA',os.environ['GITHUB_SHA'])); sys.exit(0)
if args[:2]==['release','view']: sys.exit(0 if state.exists() else 1)
if args[:2]==['release','create']: state.write_text('created')
if args[:2]==['release','upload'] and os.environ.get('FAKE_GH_FAIL_UPLOAD')=='1': sys.exit(1)
'''


@unittest.skipUnless(shutil.which('bash'), 'requires Bash')
class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.assets = self.root / 'assets'
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        fake = self.bin / 'gh'
        fake.write_text(FAKE_GH)
        fake.chmod(0o755)
        self.log = self.root / 'gh-log.jsonl'
        self.state = self.root / 'release-exists'

    def environment(self, channel):
        base = version()
        ref = 'refs/heads/release/test' if channel == 'beta' else 'refs/tags/v' + base
        meta = resolve_release('push', ref, '17', SHA, base, base)
        build(meta['version'], self.assets, SHA)
        return {
            **os.environ, 'PATH': str(self.bin) + os.pathsep + os.environ['PATH'],
            'GH_TOKEN': 'fake-test-token', 'GH_REPO': 'test-owner/test-repo', 'GITHUB_SHA': SHA,
            'RELEASE_CHANNEL': meta['channel'], 'RELEASE_VERSION': meta['version'],
            'RELEASE_TAG': meta['tag'], 'RELEASE_NAME': meta['name'],
            'FAKE_GH_LOG': str(self.log), 'FAKE_GH_STATE': str(self.state),
        }

    def run_script(self, env):
        return subprocess.run(['bash', str(ROOT / 'tools/publish_release.sh'), str(self.assets)],
                              env=env, text=True, capture_output=True, timeout=30)

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_beta_draft_then_upload_then_publish(self):
        env = self.environment('beta')
        result = self.run_script(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.commands()
        self.assertEqual([c[1] for c in commands], ['view', 'create', 'upload', 'edit'])
        self.assertIn('--draft', commands[1])
        self.assertIn('--target', commands[1])
        self.assertIn(SHA, commands[1])
        self.assertIn('--clobber', commands[2])
        self.assertIn('--prerelease=true', commands[3])
        self.assertIn('--latest=false', commands[3])
        self.assertIn('--draft=false', commands[3])
        self.assertNotIn(str(self.assets / 'RELEASE_NOTES.md'), commands[2])

    def test_formal_release_flags(self):
        result = self.run_script(self.environment('stable'))
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.commands()
        create=next(c for c in commands if c[:2]==['release','create'])
        self.assertIn('--verify-tag', create)
        self.assertIn('--prerelease=false', commands[-1])
        self.assertIn('--latest=true', commands[-1])

    def test_rerun_reuses_release(self):
        env = self.environment('beta')
        self.assertEqual(self.run_script(env).returncode, 0)
        self.log.write_text('')
        self.assertEqual(self.run_script(env).returncode, 0)
        self.assertEqual([c[1] for c in self.commands()], ['view', 'upload', 'edit'])

    def test_bad_checksum_never_calls_gh(self):
        env = self.environment('beta')
        (self.assets / 'INSTALL.txt').write_text('tampered')
        self.assertNotEqual(self.run_script(env).returncode, 0)
        self.assertEqual(self.commands(), [])

    def test_moved_tag_refuses_publication(self):
        env=self.environment('stable')
        env['FAKE_REMOTE_SHA']='f'*40
        self.assertNotEqual(self.run_script(env).returncode,0)
        self.assertFalse(any(c[:2]==['release','create'] for c in self.commands()))

    def test_upload_failure_does_not_publish(self):
        env = self.environment('beta')
        env['FAKE_GH_FAIL_UPLOAD'] = '1'
        self.assertNotEqual(self.run_script(env).returncode, 0)
        self.assertFalse(any(c[:2] == ['release', 'edit'] for c in self.commands()))


if __name__ == '__main__':
    unittest.main()
