#!/usr/bin/env python3
"""Build reproducible architecture-independent IPK and complete source bundles.

Examples:
  python3 tools/build_ipk.py
  python3 tools/build_ipk.py --version '0.2.2~beta.17.g123456abcdef' --output-dir /tmp/package

Version overrides are applied only to packaged files; the working tree is not edited.
"""
import argparse
import gzip
import hashlib
import io
import json
import pathlib
import re
import tarfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILES = ROOT / 'files'
DIST = ROOT / 'dist'
NAME = 'luci-app-xbox-accel'
BASE_VERSION_RE = r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)'
VERSION_RE = re.compile(BASE_VERSION_RE + r'(?:~beta\.[1-9][0-9]*\.g[0-9a-f]{7,40})?')
EXECUTABLES = {
    'usr/bin/xbox-accel', 'etc/init.d/xbox-accel',
    'www/cgi-bin/xbox-accel-redirect', 'usr/libexec/rpcd/xbox-accel',
}
CONFFILES = ['/etc/config/xbox-accel']

POSTINST = '''#!/bin/sh
[ -n "${IPKG_INSTROOT}" ] && exit 0
/etc/init.d/xbox-accel enable
/etc/init.d/rpcd reload
rm -f /tmp/luci-indexcache*
rm -rf /tmp/luci-modulecache
[ "$(uci -q get xbox-accel.main.enabled)" = 1 ] && /etc/init.d/xbox-accel restart
exit 0
'''
PRERM = '''#!/bin/sh
[ -n "${IPKG_INSTROOT}" ] && exit 0
/etc/init.d/xbox-accel stop || exit 1
/etc/init.d/xbox-accel disable
exit 0
'''


def validate_version(value):
    if not isinstance(value, str) or not VERSION_RE.fullmatch(value):
        raise ValueError('version must be X.Y.Z or X.Y.Z~beta.NUMBER.gHEX_SHA')
    return value


def version():
    text = (FILES / 'usr/bin/xbox-accel').read_text(encoding='utf-8')
    match = re.search(r'^VERSION=(\S+)$', text, re.M)
    if not match:
        raise ValueError('missing VERSION in core script')
    return validate_version(match.group(1))


def add_bytes(tar, name, data, mode=0o644):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    info.uname = info.gname = 'root'
    info.mtime = 0
    tar.addfile(info, io.BytesIO(data))


def add_dir(tar, name):
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    info.uname = info.gname = 'root'
    info.mtime = 0
    tar.addfile(info)


def tar_gz(build):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w', format=tarfile.GNU_FORMAT) as tar:
        build(tar)
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode='wb', mtime=0) as gz:
        gz.write(raw.getvalue())
    return out.getvalue()


def payload():
    paths = sorted(p for p in FILES.rglob('*') if p.is_file() and p.name != '.DS_Store')
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()):
            raise ValueError(f'unsupported symlink or external file: {path}')
    return paths


def packaged_bytes(path, target_version):
    data = path.read_bytes()
    relative = path.relative_to(ROOT).as_posix()
    if relative == 'files/usr/bin/xbox-accel':
        data, count = re.subn(rb'^VERSION=\S+$', b'VERSION=' + target_version.encode(), data, flags=re.M)
        if count != 1:
            raise ValueError('expected one VERSION assignment')
    elif relative == 'Makefile':
        data, count = re.subn(rb'^PKG_VERSION:=\S+$', b'PKG_VERSION:=' + target_version.encode(), data, flags=re.M)
        if count != 1:
            raise ValueError('expected one PKG_VERSION assignment')
    return data


def add_tree_file(tar, name, data, mode, seen):
    parts = name.split('/')[:-1]
    for index in range(1, len(parts) + 1):
        directory = '/'.join(parts[:index])
        if directory not in seen:
            seen.add(directory)
            add_dir(tar, directory)
    add_bytes(tar, name, data, mode)


def build(target_version=None, output_dir=None, commit=None):
    source_version = version()
    target_version = validate_version(target_version or source_version)
    if commit is not None and not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('commit must be a full lowercase Git SHA-1')
    output_dir = pathlib.Path(output_dir or DIST).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = payload()
    contents = {p: packaged_bytes(p, target_version) for p in paths}
    ipk_name = f'{NAME}_{target_version}-1_all.ipk'
    source_name = f'xbox-accel-{target_version}.tar.gz'
    info = {
        'package': NAME, 'version': target_version, 'revision': 1,
        'source_version': source_version, 'architecture': 'all',
        'channel': 'beta' if '~beta.' in target_version else 'stable',
        'commit': commit, 'ipk': ipk_name, 'source': source_name,
    }
    info_bytes = (json.dumps(info, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()
    instructions = f'''Xbox Accel {target_version} ({info['channel']})

OpenWrt with opkg:
  opkg update
  opkg install /root/{ipk_name}

Source installation (also supports apk-based OpenWrt):
  tar -xzf {source_name}
  cd xbox-accel-{target_version}
  sh install.sh

LuCI: Services -> Xbox 下载加速
The plugin is disabled by default. Review the environment checks before enabling.
Beta builds are prereleases; inspect BUILD_INFO.json for the source commit.
A .ipk cannot be installed by apk. See README.md for limitations.
'''.encode()
    control = (
        f'Package: {NAME}\nVersion: {target_version}-1\n'
        'Depends: luci-base, rpcd, curl, ca-bundle, uhttpd, jshn\n'
        'Section: luci\nArchitecture: all\nLicense: MIT\n'
        f'Installed-Size: {sum(len(data) for data in contents.values())}\n'
        'Description: Game download CDN optimization and Xbox HTTP redirect.\n'
        ' Uses the existing dnsmasq; does not proxy download traffic.\n'
    ).encode()

    def build_control(tar):
        add_dir(tar, '.')
        add_bytes(tar, './control', control)
        add_bytes(tar, './conffiles', ('\n'.join(CONFFILES) + '\n').encode())
        add_bytes(tar, './postinst', POSTINST.encode(), 0o755)
        add_bytes(tar, './prerm', PRERM.encode(), 0o755)

    def build_data(tar):
        seen = set()
        for path in paths:
            relative = path.relative_to(FILES).as_posix()
            mode = 0o755 if relative in EXECUTABLES else (0o600 if relative.startswith('etc/config/') else 0o644)
            add_tree_file(tar, './' + relative, contents[path], mode, seen)

    def build_outer(tar):
        add_bytes(tar, './debian-binary', b'2.0\n')
        add_bytes(tar, './data.tar.gz', tar_gz(build_data))
        add_bytes(tar, './control.tar.gz', tar_gz(build_control))

    ipk_path = output_dir / ipk_name
    ipk_path.write_bytes(tar_gz(build_outer))

    def build_source(tar):
        top = f'xbox-accel-{target_version}'
        extras = [ROOT / name for name in ('install.sh', 'README.md', 'Makefile', 'LICENSE', '.gitignore')]
        extras += sorted(p for folder in ('tests', 'tools') for p in (ROOT / folder).glob('*')
                         if p.is_file() and p.suffix in ('.py', '.js', '.sh'))
        extras += sorted(p for p in (ROOT / '.github').rglob('*') if p.is_file() and p.suffix in ('.yml', '.yaml'))
        extras += sorted(p for p in (ROOT / 'docs').rglob('*.md') if p.is_file())
        seen = set()
        for path in paths + extras:
            if path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()):
                raise ValueError(f'unsupported source path: {path}')
            relative = path.relative_to(ROOT).as_posix()
            executable = (relative.startswith('files/') and relative[6:] in EXECUTABLES) or relative.endswith('.sh')
            data = contents[path] if path in contents else packaged_bytes(path, target_version)
            add_tree_file(tar, f'{top}/{relative}', data, 0o755 if executable else 0o644, seen)
        add_bytes(tar, f'{top}/BUILD_INFO.json', info_bytes)
        add_bytes(tar, f'{top}/INSTALL.txt', instructions)

    source_path = output_dir / source_name
    source_path.write_bytes(tar_gz(build_source))
    info_path = output_dir / 'BUILD_INFO.json'
    instructions_path = output_dir / 'INSTALL.txt'
    info_path.write_bytes(info_bytes)
    instructions_path.write_bytes(instructions)
    assets = [ipk_path, source_path, info_path, instructions_path]
    checksum = ''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n' for p in sorted(assets))
    checksum_path = output_dir / 'SHA256SUMS.txt'
    checksum_path.write_text(checksum, encoding='utf-8')
    return {**info, 'assets': assets + [checksum_path]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', dest='target_version', help='override only the packaged version')
    parser.add_argument('--output-dir', type=pathlib.Path, default=DIST)
    parser.add_argument('--commit', help='source commit recorded in BUILD_INFO.json')
    args = parser.parse_args()
    try:
        result = build(args.target_version, args.output_dir, args.commit)
    except ValueError as error:
        parser.error(str(error))
    for path in result['assets']:
        print(path, path.stat().st_size)


if __name__ == '__main__':
    main()
