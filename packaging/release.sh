#!/usr/bin/env bash
# Release Reezyn end to end: bump, test, build, notarize, commit, deploy.
#
#   packaging/release.sh patch            # 0.2.2 -> 0.2.3
#   packaging/release.sh 0.3.0 --no-deploy
#   packaging/release.sh --resume         # finish a run that was cut off
#
# Flags:
#   --resume        release the version already in pyproject.toml, reusing a
#                   verified DMG and any notarization already in flight
#   --no-deploy     build and commit, but leave the site alone
#   --no-notarize   sign only; Gatekeeper will reject the DMG elsewhere
#   --skip-tests    commit without running the suite first
#   --publish-release
#                   push main and the tag, then publish the GitHub release with
#                   the DMG and update zip attached; safe to re-run on its own
#
# Every step is idempotent and notarization is resumable, so a run that dies
# part-way (a dropped upload, a closed terminal) is finished with --resume
# rather than by hand.
#
# Notarization uses NOTARY_PROFILE, defaulting to the "reezyn" keychain
# profile. The deploy step needs the Hostinger extension signed in once.

set -euo pipefail

cd "$(dirname "$0")/.."

SPEC=""
DEPLOY=1
NOTARIZE=1
TESTS=1
RESUME=0
PUBLISH_RELEASE=0

usage() {
  sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
}

for arg in "$@"; do
  case "$arg" in
    --resume) RESUME=1 ;;
    --no-deploy) DEPLOY=0 ;;
    --no-notarize) NOTARIZE=0 ;;
    --skip-tests) TESTS=0 ;;
    --publish-release) PUBLISH_RELEASE=1 ;;
    -h|--help) usage ;;
    -*) echo "unknown flag: $arg" >&2; exit 2 ;;
    *) SPEC="$arg" ;;
  esac
done

TMP="$(mktemp -d -t reezyn-release)"
trap 'rm -rf "$TMP"' EXIT

# Publishing on its own: the build is already tagged and pushed, so there is
# nothing to bump, test or sign — just the push and the GitHub release.
if [[ "$PUBLISH_RELEASE" -eq 1 && -z "$SPEC" && "$RESUME" -eq 0 ]]; then
  VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)"
  if [[ -z "$VERSION" ]]; then
    echo "Could not read the version from pyproject.toml" >&2
    exit 1
  fi
  DMG="dist/Reezyn-$VERSION.dmg"
  ZIP="dist/Reezyn-$VERSION.zip"
  RELEASE_ARGS=("$VERSION" "--dmg=$DMG")
  if [[ -f "$ZIP" ]]; then
    RELEASE_ARGS+=("--zip=$ZIP")
  fi
  echo "==> Pushing main and v$VERSION"
  git push origin main --follow-tags
  echo "==> Publishing the GitHub release"
  uv run python packaging/github_release.py publish "${RELEASE_ARGS[@]}"
  echo
  echo "Reezyn $VERSION is released on GitHub."
  exit 0
fi

if [[ "$RESUME" -eq 1 ]]; then
  VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)"
  if [[ -z "$VERSION" ]]; then
    echo "Could not read the version from pyproject.toml" >&2
    exit 1
  fi
  # A cut-off run leaves the version bump behind, and nothing else. Anything
  # more is an unrelated edit and the resume should not swallow it.
  ALLOWED=" pyproject.toml uv.lock reezyn/__init__.py site/index.html site/updates/latest.json "
  while read -r path; do
    [[ -n "$path" ]] || continue
    case "$ALLOWED" in
      *" $path "*) ;;
      *) echo "Refusing to resume with $path modified; commit or stash it first." >&2; exit 1 ;;
    esac
  done < <(git status --porcelain | awk '{print $2}')
  CURRENT="$VERSION"
  echo "==> Resuming Reezyn $VERSION"
else
  if [[ -z "$SPEC" ]]; then
    usage
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
fi

if [[ "$TESTS" -eq 1 && "$RESUME" -eq 0 ]]; then
  echo "==> Tests"
  uv run pytest -q
fi

DMG="dist/Reezyn-$VERSION.dmg"

if [[ "$RESUME" -eq 1 && -f "$DMG" ]] \
    && hdiutil verify "$DMG" >/dev/null 2>&1 \
    && codesign --verify "$DMG" 2>/dev/null; then
  echo "==> Reusing the verified disk image from the interrupted run"
else
  BUILD_LOG="$TMP/build.log"
  if [[ "$NOTARIZE" -eq 1 ]]; then
    export NOTARY_PROFILE="${NOTARY_PROFILE:-reezyn}"
    echo "==> Building, signing and notarizing (keychain profile: $NOTARY_PROFILE)"
  else
    echo "==> Building and signing only"
  fi
  ./packaging/build_app.sh 2>&1 | tee "$BUILD_LOG"
  if grep -q "Signed but not notarized" "$BUILD_LOG"; then
    echo "Notarization was skipped; rebuild with a valid NOTARY_PROFILE." >&2
    exit 1
  fi
fi

if [[ ! -f "$DMG" ]]; then
  echo "No DMG at $DMG" >&2
  exit 1
fi

SHA="$(shasum -a 256 "$DMG" | awk '{print $1}')"
echo "==> $DMG ($SHA)"

ZIP="dist/Reezyn-$VERSION.zip"
PUBLISHED=0
if [[ "$RESUME" -eq 1 ]] \
    && [[ -f "site/downloads/Reezyn-$VERSION.dmg" ]] \
    && grep -q "\"version\": \"$VERSION\"" site/updates/latest.json 2>/dev/null; then
  PUBLISHED=1
  echo "==> The download page and the feed already point at $VERSION"
fi

SHA="$(uv run python packaging/release_version.py stamp "$VERSION" "$DMG")"
if [[ "$PUBLISHED" -eq 1 ]]; then
  echo "==> Download checksum still $SHA"
else
  echo "==> Stamped the site download ($SHA)"

  NOTES="$TMP/notes.txt"
  echo "==> Packaging the app for updates"
  ditto -c -k --keepParent "dist/Reezyn.app" "$ZIP"
  LAST_TAG="$(git describe --tags --abbrev=0 2>/dev/null || true)"
  if [[ -n "$LAST_TAG" ]]; then
    git log --pretty='%s' "$LAST_TAG"..HEAD > "$NOTES"
  else
    git log -20 --pretty='%s' > "$NOTES"
  fi
  uv run python packaging/release_version.py feed "$VERSION" "$ZIP" "$NOTES" >/dev/null
  echo "==> Published updates/latest.json for $VERSION"
fi


git add pyproject.toml uv.lock reezyn/__init__.py site/index.html site/updates/latest.json
if git diff --cached --quiet; then
  echo "==> Nothing to commit; HEAD already carries $VERSION"
else
  git commit -q -m "release: Reezyn $VERSION"
  echo "==> Committed $(git rev-parse --short HEAD)"
fi

if git rev-parse -q --verify "refs/tags/v$VERSION" >/dev/null; then
  echo "==> Tag v$VERSION already exists"
else
  git tag -a "v$VERSION" -m "Reezyn $VERSION"
  echo "==> Tagged v$VERSION"
fi

if [[ "$DEPLOY" -eq 1 ]]; then
  echo "==> Deploying the site"
  uv run python packaging/deploy_site.py \
    --domain "${REEZYN_SITE_DOMAIN:-reezyn.elastikmind.com}" \
    --path site
else
  echo "==> Skipped deploy. Upload site/ when ready."
fi

RELEASE_ARGS=("$VERSION" "--dmg=$DMG")
if [[ -f "$ZIP" ]]; then
  RELEASE_ARGS+=("--zip=$ZIP")
fi

if [[ "$PUBLISH_RELEASE" -eq 1 ]]; then
  echo "==> Pushing main and v$VERSION"
  git push origin main --follow-tags
  echo "==> Publishing the GitHub release"
  uv run python packaging/github_release.py publish "${RELEASE_ARGS[@]}"
fi

echo
echo "Reezyn $VERSION released."
echo "App: $PWD/dist/Reezyn.app"
echo "DMG: $DMG"
echo "SHA: $SHA"
if [[ "$PUBLISH_RELEASE" -eq 1 ]]; then
  echo "GitHub: https://github.com/$(git remote get-url origin | sed -e 's#.*github.com[:/]##' -e 's#\.git$##')/releases/tag/v$VERSION"
else
  echo "Push with: git push origin main --follow-tags"
  echo "Then publish the GitHub release: packaging/release.sh --publish-release"
fi
