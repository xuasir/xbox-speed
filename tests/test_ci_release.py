#!/usr/bin/env python3
import pathlib
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'tools'))
from ci_release import resolve_release

SHA = '0123456789abcdef0123456789abcdef01234567'


class ReleaseMetadataTests(unittest.TestCase):
    def resolve(self, ref, **overrides):
        values = dict(event='push', ref=ref, run_number='17', sha=SHA,
                      core_version='0.2.2', sdk_version='0.2.2')
        values.update(overrides)
        return resolve_release(**values)

    def test_beta_branch(self):
        result = self.resolve('refs/heads/release/test')
        self.assertEqual(result['version'], '0.2.2~beta.17.g0123456789ab')
        self.assertEqual(result['channel'], 'beta')
        self.assertEqual(result['prerelease'], 'true')
        self.assertEqual(result['latest'], 'false')
        self.assertTrue(result['tag'].startswith('beta-v'))
        self.assertFalse(result['tag'].startswith('v'))

    def test_formal_tags(self):
        for tag in ('v0.2.2', '0.2.2'):
            result = self.resolve('refs/tags/' + tag)
            self.assertEqual(result['version'], '0.2.2')
            self.assertEqual(result['channel'], 'stable')
            self.assertEqual(result['prerelease'], 'false')
            self.assertEqual(result['tag'], tag)

    def test_unapproved_refs_and_tags(self):
        for ref in ('refs/heads/main', 'refs/heads/test', 'refs/heads/release/test/other',
                    'refs/tags/v0.2.2-beta.1', 'refs/tags/beta-v0.2.2-1-g0123456789ab',
                    'refs/tags/v0.2.2\nmalicious', 'refs/tags/v0.02.2'):
            with self.assertRaises(ValueError, msg=ref):
                self.resolve(ref)

    def test_mismatched_source_versions(self):
        with self.assertRaises(ValueError):
            self.resolve('refs/tags/v0.3.0')
        with self.assertRaises(ValueError):
            self.resolve('refs/heads/release/test', sdk_version='0.3.0')
        with self.assertRaises(ValueError):
            self.resolve('refs/heads/release/test', core_version='0.2.2~beta.1.g0123456')

    def test_invalid_context(self):
        for overrides in ({'event': 'pull_request'}, {'sha': 'not-a-sha'}, {'run_number': '0'},
                          {'run_number': '1\nkey=bad'}, {'sha': SHA.upper()}):
            with self.assertRaises(ValueError):
                self.resolve('refs/heads/release/test', **overrides)

    @unittest.skipUnless(shutil.which('dpkg'), 'dpkg comparison is checked on Linux CI')
    def test_beta_sorts_below_matching_stable(self):
        beta = self.resolve('refs/heads/release/test')['version'] + '-1'
        subprocess.run(['dpkg', '--compare-versions', beta, 'lt', '0.2.2-1'], check=True)
        subprocess.run(['dpkg', '--compare-versions', '0.2.2~beta.9.g0123456789ab-1', 'lt', beta], check=True)


if __name__ == '__main__':
    unittest.main()
