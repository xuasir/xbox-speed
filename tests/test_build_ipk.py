#!/usr/bin/env python3
import hashlib
import io
import json
import pathlib
import sys
import tarfile
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import build_ipk

SHA = '0123456789abcdef0123456789abcdef01234567'


def inner_tar(ipk, member):
    with tarfile.open(ipk) as archive:
        content = archive.extractfile('./' + member).read()
    return tarfile.open(fileobj=io.BytesIO(content), mode='r:gz')


class BuildTests(unittest.TestCase):
    def test_default_stable_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = build_ipk.build(output_dir=temporary, commit=SHA)
            ver = build_ipk.version()
            package = pathlib.Path(temporary) / result['ipk']
            with inner_tar(package, 'control.tar.gz') as control:
                self.assertIn(f'Version: {ver}-1\n', control.extractfile('./control').read().decode())
                self.assertIn('conntrack', control.extractfile('./control').read().decode().split('Depends: ', 1)[1].split('\n', 1)[0])
                self.assertIn('jsonfilter', control.extractfile('./control').read().decode().split('Depends: ', 1)[1].split('\n', 1)[0])
                self.assertEqual(control.getmember('./postinst').mode, 0o755)
                self.assertIn('/usr/bin/xbox-accel _web_guard || true\n', control.extractfile('./postinst').read().decode())
            with inner_tar(package, 'data.tar.gz') as data:
                self.assertEqual(data.getmember('./usr/bin/xbox-accel').mode, 0o755)
                self.assertEqual(data.getmember('./etc/config/xbox-accel').mode, 0o600)
                self.assertEqual(data.getmember('./www/luci-static/resources/view/xbox-accel/overview.js').mode, 0o644)
                self.assertIn(f'VERSION={ver}\n', data.extractfile('./usr/bin/xbox-accel').read().decode())
                for module in ('platforms', 'diagnostics', 'verification', 'application', 'privacy', 'quick-check', 'redirect', 'measurement', 'records', 'observation'):
                    self.assertIsNotNone(data.extractfile(f'./usr/share/xbox-accel/{module}.sh'))
                    self.assertEqual(data.getmember(f'./usr/share/xbox-accel/{module}.sh').mode, 0o644)
                for script in ('redirect.cgi', 'firewall.sh'):
                    self.assertEqual(data.getmember(f'./usr/share/xbox-accel/{script}').mode, 0o755)
                self.assertNotIn('./www/cgi-bin/xbox-accel-redirect', data.getnames())
                for group in ('store', 'ps', 'xbox_old', 'switch'):
                    self.assertIsNotNone(data.extractfile(f'./usr/share/xbox-accel/seeds.{group}'))
            self.assertEqual(result['channel'], 'stable')
            self.assertEqual(len(result['assets']), 5)

    def test_beta_version_is_consistent_without_worktree_edits(self):
        before = (ROOT / 'files/usr/bin/xbox-accel').read_bytes()
        make_before = (ROOT / 'Makefile').read_bytes()
        ver = build_ipk.version() + '~beta.17.g0123456789ab'
        with tempfile.TemporaryDirectory() as temporary:
            result = build_ipk.build(ver, temporary, SHA)
            with inner_tar(pathlib.Path(temporary) / result['ipk'], 'data.tar.gz') as data:
                self.assertIn(f'VERSION={ver}\n', data.extractfile('./usr/bin/xbox-accel').read().decode())
            with tarfile.open(pathlib.Path(temporary) / result['source']) as source:
                top = f'xbox-accel-{ver}'
                self.assertIn(f'PKG_VERSION:={ver}\n', source.extractfile(top + '/Makefile').read().decode())
                self.assertIn(f'VERSION={ver}\n', source.extractfile(top + '/files/usr/bin/xbox-accel').read().decode())
                for file in ('BUILD_INFO.json', 'INSTALL.txt', '.github/workflows/release.yml', 'docs/RELEASING.md', 'tests/test_build_ipk.py'):
                    self.assertIsNotNone(source.extractfile(top + '/' + file))
                self.assertTrue(all(not name.startswith('/') and '..' not in pathlib.PurePosixPath(name).parts for name in source.getnames()))
            info = json.loads((pathlib.Path(temporary) / 'BUILD_INFO.json').read_text())
            self.assertEqual((info['version'], info['channel'], info['commit']), (ver, 'beta', SHA))
            self.assertIn(result['ipk'], (pathlib.Path(temporary) / 'INSTALL.txt').read_text())
        self.assertEqual(before, (ROOT / 'files/usr/bin/xbox-accel').read_bytes())
        self.assertEqual(make_before, (ROOT / 'Makefile').read_bytes())

    def test_reproducible_artifacts_and_checksums(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            a = build_ipk.build(output_dir=first, commit=SHA)
            b = build_ipk.build(output_dir=second, commit=SHA)
            self.assertEqual([p.name for p in a['assets']], [p.name for p in b['assets']])
            for p, q in zip(a['assets'], b['assets']):
                self.assertEqual(p.read_bytes(), q.read_bytes())
            for line in (pathlib.Path(first) / 'SHA256SUMS.txt').read_text().splitlines():
                digest, name = line.split('  ', 1)
                self.assertEqual(digest, hashlib.sha256((pathlib.Path(first) / name).read_bytes()).hexdigest())

    def test_invalid_versions_rejected(self):
        for value in ('../outside', 'v0.2.2', '0.2.2\nHeader: injected', '0.2.2-beta.1',
                      '0.2.2~beta.0.g0123456', '0.02.2'):
            with self.assertRaises(ValueError, msg=value):
                build_ipk.validate_version(value)
        with self.assertRaises(ValueError):
            build_ipk.build(commit='not-a-commit')


if __name__ == '__main__':
    unittest.main()
