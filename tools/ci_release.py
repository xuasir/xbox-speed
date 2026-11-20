#!/usr/bin/env python3
"""Resolve CI release metadata without evaluating branch/tag strings as shell code."""
import argparse
import json
import os
import pathlib
import re

from build_ipk import BASE_VERSION_RE, ROOT, version


def resolve_release(event, ref, run_number, sha, core_version, sdk_version):
    if event != 'push':
        raise ValueError('only push events may publish releases')
    if not re.fullmatch(BASE_VERSION_RE, core_version):
        raise ValueError('keep a plain X.Y.Z base version in the repository, not a beta suffix')
    if sdk_version != core_version:
        raise ValueError('Makefile PKG_VERSION must match the core VERSION')
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise ValueError('GITHUB_SHA must be a full lowercase Git SHA-1')
    if not re.fullmatch(r'[1-9][0-9]*', str(run_number)):
        raise ValueError('GITHUB_RUN_NUMBER must be a positive integer')

    if ref == 'refs/heads/release/test':
        short = sha[:12]
        target_version = f'{core_version}~beta.{run_number}.g{short}'
        return {
            'channel': 'beta', 'version': target_version,
            'tag': f'beta-v{core_version}-{run_number}-g{short}',
            'name': f'Beta v{core_version} (build {run_number}, g{short})',
            'prerelease': 'true', 'latest': 'false', 'commit': sha,
        }

    if ref.startswith('refs/tags/'):
        tag = ref.removeprefix('refs/tags/')
        match = re.fullmatch(r'v?(' + BASE_VERSION_RE + r')', tag)
        if not match:
            raise ValueError('formal release tags must be vX.Y.Z or X.Y.Z; prerelease tags are not formal releases')
        tag_version = match.group(1)
        if tag_version != core_version:
            raise ValueError(f'tag version {tag_version} does not match source VERSION {core_version}; update core and Makefile before tagging')
        return {
            'channel': 'stable', 'version': tag_version, 'tag': tag,
            'name': f'Xbox Accel v{tag_version}',
            'prerelease': 'false', 'latest': 'true', 'commit': sha,
        }

    raise ValueError('beta builds are allowed only from the release/test branch')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--event', default=os.environ.get('GITHUB_EVENT_NAME', ''))
    parser.add_argument('--ref', default=os.environ.get('GITHUB_REF', ''))
    parser.add_argument('--run-number', default=os.environ.get('GITHUB_RUN_NUMBER', ''))
    parser.add_argument('--sha', default=os.environ.get('GITHUB_SHA', ''))
    parser.add_argument('--github-output', type=pathlib.Path, default=os.environ.get('GITHUB_OUTPUT'))
    args = parser.parse_args()
    makefile = (ROOT / 'Makefile').read_text(encoding='utf-8')
    match = re.search(r'^PKG_VERSION:=(\S+)$', makefile, re.M)
    try:
        if not match:
            raise ValueError('missing PKG_VERSION in Makefile')
        result = resolve_release(args.event, args.ref, args.run_number, args.sha, version(), match.group(1))
    except ValueError as error:
        parser.error(str(error))
    if args.github_output:
        with pathlib.Path(args.github_output).open('a', encoding='utf-8') as output:
            for key, value in result.items():
                output.write(f'{key}={value}\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
