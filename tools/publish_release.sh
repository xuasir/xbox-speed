#!/usr/bin/env bash
# Run only in the publish job. All gh commands target the explicitly configured repo/tag.
set -euo pipefail

: "${GH_TOKEN:?GitHub token is required}"
: "${GH_REPO:?GitHub repository is required}"
: "${RELEASE_TAG:?Release tag is required}"
: "${RELEASE_VERSION:?Release version is required}"
: "${RELEASE_CHANNEL:?Release channel is required}"
: "${RELEASE_NAME:?Release title is required}"
: "${GITHUB_SHA:?Source commit is required}"
asset_dir="${1:?Usage: bash tools/publish_release.sh ASSET_DIR}"
[[ -d "$asset_dir" ]] || { echo 'Artifact directory is missing' >&2; exit 1; }
[[ "$GH_REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || exit 1
[[ "$GITHUB_SHA" =~ ^[0-9a-f]{40}$ ]] || exit 1
case "$RELEASE_CHANNEL" in
  beta)
    [[ "$RELEASE_TAG" =~ ^beta-v[0-9]+\.[0-9]+\.[0-9]+-[1-9][0-9]*-g[0-9a-f]{12}$ ]] || exit 1
    prerelease=true
    latest=false
    ;;
  stable)
    [[ "$RELEASE_TAG" == "$RELEASE_VERSION" || "$RELEASE_TAG" == "v$RELEASE_VERSION" ]] || exit 1
    prerelease=false
    latest=true
    ;;
  *) echo 'Unknown release channel' >&2; exit 1 ;;
esac

# Validate metadata and every checksum before touching a remote release.
verified=$(python3 - "$asset_dir" "$RELEASE_VERSION" "$RELEASE_CHANNEL" "$GITHUB_SHA" <<'PY'
import hashlib,json,pathlib,re,sys
root=pathlib.Path(sys.argv[1])
version,channel,commit=sys.argv[2:]
if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(~beta\.[1-9][0-9]*\.g[0-9a-f]{7,40})?',version):
    raise SystemExit('Invalid package version')
if ('~beta.' in version)!=(channel=='beta'):
    raise SystemExit('Version/channel mismatch')
info=json.loads((root/'BUILD_INFO.json').read_text())
if (info['version'],info['channel'],info['commit'])!=(version,channel,commit):
    raise SystemExit('Build metadata does not match this workflow commit/version')
expected={f'luci-app-xbox-accel_{version}-1_all.ipk',f'xbox-accel-{version}.tar.gz','BUILD_INFO.json','INSTALL.txt'}
checks={}
for line in (root/'SHA256SUMS.txt').read_text().splitlines():
    digest,name=line.split('  ',1)
    if name not in expected or name in checks or not re.fullmatch('[0-9a-f]{64}',digest):
        raise SystemExit('Invalid checksum manifest')
    p=root/name
    if p.is_symlink() or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:
        raise SystemExit('Artifact checksum mismatch: '+name)
    checks[name]=digest
if set(checks)!=expected:
    raise SystemExit('Missing release artifacts')
for name in sorted(expected|{'SHA256SUMS.txt'}):
    print(root/name)
PY
)
assets=()
while IFS= read -r path; do assets+=("$path"); done <<< "$verified"
[[ "${#assets[@]}" == 5 ]] || exit 1

if [[ "$RELEASE_CHANNEL" == stable ]]; then
  remote_commit=$(gh api "repos/$GH_REPO/commits/$RELEASE_TAG" --jq '.sha')
  [[ "$remote_commit" == "$GITHUB_SHA" ]] || { echo 'Remote tag moved away from the triggering commit; refusing publication' >&2; exit 1; }
fi

notes="$asset_dir/RELEASE_NOTES.md"
cat > "$notes" <<EOF
## Xbox Accel $RELEASE_VERSION

- Channel: **$RELEASE_CHANNEL**
- Source: [$GITHUB_SHA](https://github.com/$GH_REPO/commit/$GITHUB_SHA)
- Package architecture: **all** (Shell/LuCI scripts; no architecture-specific binaries).
- Download the IPK, source bundle, build information and SHA256 checksums below.
- Follow **INSTALL.txt** for the exact generated filenames and installation commands.
- The plugin is disabled by default. Review environment checks before enabling it.
- Beta package versions use \`~beta\`, so the matching stable version sorts higher for opkg upgrades.
- Xbox/Passwall performance must still be verified on your actual device.
EOF

if ! gh release view "$RELEASE_TAG" --repo "$GH_REPO" >/dev/null 2>&1; then
  args=(--repo "$GH_REPO" --draft --title "$RELEASE_NAME" --notes-file "$notes")
  if [[ "$RELEASE_CHANNEL" == beta ]]; then
    args+=(--prerelease --target "$GITHUB_SHA")
  else
    args+=(--verify-tag)
  fi
  gh release create "$RELEASE_TAG" "${args[@]}"
fi
# Draft-first publication prevents exposing an initial release with missing assets.
# Re-running the same workflow safely replaces assets for the same version/tag.
gh release upload "$RELEASE_TAG" "${assets[@]}" --repo "$GH_REPO" --clobber
gh release edit "$RELEASE_TAG" --repo "$GH_REPO" --title "$RELEASE_NAME" \
  --notes-file "$notes" --draft=false --prerelease="$prerelease" --latest="$latest"
