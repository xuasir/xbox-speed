#!/usr/bin/env python3
"""Host-side redirect tests; no router changes."""
import os
import pathlib
import subprocess
import unittest

CGI = pathlib.Path(__file__).resolve().parents[1] / 'files/usr/share/xbox-accel/redirect.cgi'

class RedirectTests(unittest.TestCase):
    def run_cgi(self, host, uri):
        return subprocess.check_output(['/bin/sh', str(CGI)], env={**os.environ, 'HTTP_HOST': host, 'REQUEST_URI': uri}).decode()

    def test_path_query(self):
        self.assertIn('Location: http://assets1.xboxlive.cn/5/file?x=1&y=2\r\n', self.run_cgi('assets1.xboxlive.com', '/5/file?x=1&y=2'))

    def test_selfcheck_path(self):
        uri = '/xbox-accel/selftest?probe=1'
        response = self.run_cgi('assets1.xboxlive.com', uri)
        self.assertIn('Status: 302 Found\r\n', response)
        self.assertIn(f'Location: http://assets1.xboxlive.cn{uri}\r\n', response)

    def test_legacy(self):
        self.assertIn('Location: http://dlassets.xboxlive.cn/file', self.run_cgi('dlassets2.xboxlive.com:80', '/file'))

    def test_unknown(self):
        self.assertIn('404 Not Found', self.run_cgi('router.lan', '/missing'))

    def test_header_injection(self):
        self.assertIn('400 Bad Request', self.run_cgi('assets1.xboxlive.com', '/file\r\nX-Injected: yes'))
        self.assertIn('400 Bad Request', self.run_cgi('assets1.xboxlive.com', '/file\nX-Injected: yes'))

    def test_control_characters(self):
        # NUL cannot be passed through a process environment.
        for value in [*range(1, 32), 127]:
            with self.subTest(value=value):
                response = self.run_cgi('assets1.xboxlive.com', '/5/ABC' + chr(value) + 'DEF')
                self.assertIn('Status: 400 Bad Request\r\n', response)
                self.assertNotIn('Location:', response)

    def test_encoded_characters(self):
        uri = '/content/%E4%B8%AD%E6%96%87?token=a%0D%0Ab%7Fc'
        response = self.run_cgi('assets1.xboxlive.com', uri)
        self.assertIn(f'Location: http://assets1.xboxlive.cn{uri}\r\n', response)

if __name__ == '__main__':
    unittest.main()
