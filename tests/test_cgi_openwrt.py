#!/usr/bin/env python3
"""CGI and real HTTP regression tests in a disposable OpenWrt container.

Usage: python3 tests/test_cgi_openwrt.py xa-test
Uses a temporary Web root and uhttpd listener; preserves system configuration.
"""
import pathlib
import subprocess
import sys
import time
import unittest

import test_cgi


CONTAINER = sys.argv.pop(1) if len(sys.argv) > 1 else 'xa-test'
PORT = 18081


def docker(*args, input=None):
    return subprocess.check_output(
        ['docker', 'exec', *(['-i'] if input is not None else []), CONTAINER, *args],
        input=input, timeout=10,
    )


class OpenWrtRedirectTests(test_cgi.RedirectTests):
    @classmethod
    def setUpClass(cls):
        docker('test', '-f', '/etc/openwrt_release')
        root = docker('mktemp', '-d', '/tmp/xbox-accel-cgi.XXXXXX').decode().strip()
        if not root.startswith('/tmp/xbox-accel-cgi.') or '/' in root[len('/tmp/'):]:
            raise AssertionError(f'Unexpected temporary directory: {root!r}')
        cls.addClassCleanup(docker, 'rm', '-rf', root)
        cls.cgi = str(pathlib.PurePosixPath(root) / 'cgi-bin/xbox-accel-redirect')
        docker('sh', '-c', 'mkdir "$1/cgi-bin"; ln -s /www/cgi-bin/luci "$1/cgi-bin/luci"; ln -s /www/luci-static "$1/luci-static"', 'sh', root)
        docker('sh', '-c', 'cat > "$1"; chmod 755 "$1"', 'sh', cls.cgi, input=test_cgi.CGI.read_bytes())
        pid = docker(
            'sh', '-c',
            '/usr/sbin/uhttpd -f -h "$1" -x /cgi-bin -u /ubus -R -p "127.0.0.1:$2" '
            '-E /cgi-bin/xbox-accel-redirect >"$1/uhttpd.log" 2>&1 & echo $!',
            'sh', root, str(PORT),
        ).decode().strip()
        if not pid.isdigit():
            raise AssertionError(f'Unexpected uhttpd PID: {pid!r}')
        cls.addClassCleanup(docker, 'sh', '-c', 'kill "$1" 2>/dev/null || true', 'sh', pid)
        for _ in range(30):
            docker('kill', '-0', pid)
            try:
                cls.request('router.lan', '/luci-static/resources/luci.js')
                return
            except subprocess.CalledProcessError:
                time.sleep(0.1)
        raise AssertionError(docker('cat', root + '/uhttpd.log').decode())

    def run_cgi(self, host, uri):
        return docker('env', 'HTTP_HOST=' + host, 'REQUEST_URI=' + uri, '/bin/sh', self.cgi).decode()

    @staticmethod
    def request(host, uri, port=PORT):
        response = docker(
            '/usr/bin/curl', '-sS', '--noproxy', '*', '-m', '3', '-i',
            '-H', 'Host: ' + host, f'http://127.0.0.1:{port}{uri}',
        ).decode()
        headers, body = response.split('\r\n\r\n', 1)
        lines = headers.splitlines()
        status = int(lines[0].split()[1])
        fields = dict(line.split(': ', 1) for line in lines[1:] if ': ' in line)
        return status, fields, body

    def test_http_redirect(self):
        for host, uri, target in [
            ('assets1.xboxlive.com', '/xbox-accel/selftest?probe=1', 'assets1.xboxlive.cn'),
            ('assets1.xboxlive.com', '/5/file?x=1&y=2', 'assets1.xboxlive.cn'),
            ('dlassets2.xboxlive.com:80', '/file', 'dlassets.xboxlive.cn'),
        ]:
            with self.subTest(host=host, uri=uri):
                status, fields, _ = self.request(host, uri)
                self.assertEqual(status, 302)
                self.assertEqual(fields['Location'], f'http://{target}{uri}')

    def test_http_unknown_host(self):
        status, fields, _ = self.request('router.lan', '/xbox-accel/selftest?probe=1')
        self.assertEqual(status, 404)
        self.assertNotIn('Location', fields)

    def test_http_luci(self):
        uri = '/cgi-bin/luci/admin/services/xbox-accel'
        baseline_status, _, _ = self.request('router.lan', uri, port=80)
        status, fields, body = self.request('router.lan', uri)
        self.assertEqual(status, baseline_status)
        self.assertIn('name="luci_username"', body)
        self.assertNotIn('Location', fields)
        status, _, body = self.request('router.lan', '/luci-static/resources/luci.js')
        self.assertEqual(status, 200)
        self.assertIn('LuCI', body)


if __name__ == '__main__':
    unittest.main()
