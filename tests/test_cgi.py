#!/usr/bin/env python3
"""Host-side redirect tests; no router changes."""
import os
import pathlib
import subprocess
import unittest

CGI = pathlib.Path(__file__).resolve().parents[1] / 'files/www/cgi-bin/xbox-accel-redirect'

class RedirectTests(unittest.TestCase):
    def run_cgi(self, host, uri):
        return subprocess.check_output(['/bin/sh', str(CGI)], env={**os.environ, 'HTTP_HOST': host, 'REQUEST_URI': uri}).decode()

    def test_path_query(self):
        self.assertIn('Location: http://assets1.xboxlive.cn/5/file?x=1&y=2\r\n', self.run_cgi('assets1.xboxlive.com', '/5/file?x=1&y=2'))

    def test_legacy(self):
        self.assertIn('Location: http://dlassets.xboxlive.cn/file', self.run_cgi('dlassets2.xboxlive.com:80', '/file'))

    def test_unknown(self):
        self.assertIn('404 Not Found', self.run_cgi('router.lan', '/missing'))

    def test_header_injection(self):
        self.assertIn('400 Bad Request', self.run_cgi('assets1.xboxlive.com', '/file\r\nX-Injected: yes'))
        self.assertIn('400 Bad Request', self.run_cgi('assets1.xboxlive.com', '/file\nX-Injected: yes'))

if __name__ == '__main__':
    unittest.main()
