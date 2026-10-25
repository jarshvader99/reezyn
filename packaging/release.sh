#!/usr/bin/env bash
# Release Reezyn end to end: bump, test, build, notarize, commit, deploy.
#
#   packaging/release.sh patch            # 0.2.0 -> 0.2.1
#   packaging/release.sh 0.3.0 --no-deploy
#
# Flags:
#   --no-deploy     build and commit, but leave the site alone
#   --no-notarize   sign only; Gatekeeper will reject the DMG elsewhere
#   --skip-tests    commit without running the suite first
#
# Notarization uses NOTARY_PROFILE, defaulting to the "reezyn" keychain
# profile. The deploy step needs the Hostinger extension signed in once.

set -euo pipefail

cd "$(dirname "$0")/.."

SPEC=""
DEPLOY=1
NOTARIZE=1
TESTS=1

for arg in "$@"; do
  case "$arg" in
    --no-deploy) DEPLOY=0 ;;
    --no-notarize) NOTARIZE=0 ;;
    --skip-tests) TESTS=0 ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "unknown flag: $arg" >&2; exit 2 ;;
    *) SPEC="$arg" ;;
  esac
done

if [[ -z "$SPEC" ]]; then
  echo "usage: packaging/release.sh <patch|minor|major|X.Y.Z> [--no-deploy] [--no-notarize] [--skip-tests]" >&2
  exit 2
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Working tree is dirty. Commit or stash first:" >&2
  git status --short >&2
  exit 1
fi

CURRENT="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)"
VERSION="$(uv run python packaging/release_version.py next "$CURRENT" "$SPEC")"
echo "==> Releasing $CURRENT -> $VERSION"

uv run python packaging/release_version.py apply "$VERSION"
uv lock --quiet

if [[ "$TESTS" -eq 1 ]]; then
  echo "==> Tests"
  uv run pytest -q
fi

BUILD_LOG="$(mktemp -t reezyn-build)"
trap 'rm -f "$BUILD_LOG"' EXIT

if [[ "$NOTARIZE" -eq 1 ]]; then
  export NOTARY_PROFILE="${NOTARY_PROFILE:-reezyn}"
  echo "==> Building and notarizing (keychain profile: $NOTARY_PROFILE)"
  ./packaging/build_app.sh 2>&1 | tee "$BUILD_LOG"
  if grep -q "Signed but not notarized" "$BUILD_LOG"; then
    echo "Notarization was skipped; rebuild with a valid NOTARY_PROFILE." >&2
    exit 1
  fi
else
  echo "==> Building and signing only"
  ./packaging/build_app.sh 2>&1 | tee "$BUILD_LOG"
fi

DMG="dist/Reezyn-$VERSION.dmg"
if [[ ! -f "$DMG" ]]; then
  echo "No DMG at $DMG" >&2
  exit 1
fi

SHA="$(uv run python packaging/release_version.py stamp "$VERSION" "$DMG")"
echo "==> stamped site download: $(basename "$DMG") ($SHA)"

git add pyproject.toml uv.lock reezyn/__init__.py site/index.html
git commit -q -m "release: Reezyn $VERSION"
echo "==> committed $(git rev-parse --short HEAD)"

if [[ "$DEPLOY" -eq 1 ]]; then
  echo "==> Deploying the site"
  uv run python packaging/deploy_site.py \
    --domain "${REEZYN_SITE_DOMAIN:-reezyn.elastikmind.com}" \
    --path site
else
  echo "==> Skipped deploy. Upload site/ when ready."
fi

echo
echo "Reezyn $VERSION released."
echo "App: $PWD/dist/Reezyn.app"
echo "DMG: $DMG"
