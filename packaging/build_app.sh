#!/usr/bin/env bash
# Build, sign, and package Reezyn.app for distribution.
#
#   packaging/build_app.sh
#
# Outputs dist/Reezyn.app and dist/Reezyn-<version>.dmg, signed with the
# Developer ID Application identity. Notarization is delegated to
# packaging/notarize.sh, which can resume an interrupted submission, and runs
# when credentials are provided through the environment:
#
#   NOTARY_PROFILE      a profile stored with `xcrun notarytool store-credentials`
#   NOTARY_KEY          an App Store Connect API key (.p8) path
#   NOTARY_KEY_ID       that key's Key ID
#   NOTARY_ISSUER       the issuer ID (omit only for a single-user key)
#
# Override the signer with REEZYN_SIGN_IDENTITY.

set -euo pipefail

cd "$(dirname "$0")/.."

APP_NAME="Reezyn"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)"
IDENTITY="${REEZYN_SIGN_IDENTITY:-Developer ID Application: Joshua Harrison (H4G8F4Q8D2)}"

if ! security find-identity -v -p codesigning 2>/dev/null | grep -qF "$IDENTITY"; then
  echo "No signing identity matches: $IDENTITY" >&2
  echo "Pick one from this list and set REEZYN_SIGN_IDENTITY:" >&2
  security find-identity -v -p codesigning >&2 || true
  exit 1
fi

if [[ -z "$VERSION" ]]; then
  echo "Could not read the version from pyproject.toml" >&2
  exit 1
fi

echo "==> Rendering the app icon"
uv run python packaging/make_icon.py build/Reezyn.icns

echo "==> Building Reezyn.app with PyInstaller"
rm -rf build/pyinstaller dist
uv run pyinstaller --noconfirm --clean --distpath dist --workpath build/pyinstaller packaging/Reezyn.spec

APP="dist/$APP_NAME.app"
if [[ ! -d "$APP" ]]; then
  echo "PyInstaller did not produce $APP" >&2
  exit 1
fi

echo "==> Signing with: $IDENTITY"
codesign \
  --force \
  --deep \
  --timestamp \
  --options runtime \
  --entitlements packaging/Reezyn.entitlements \
  --sign "$IDENTITY" \
  "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

# A build that was killed can leave a diskimages-helper holding one of our
# artifacts open, and every later hdiutil call then stalls on it. Only helpers
# with a file open under dist/ or build/ are touched, so unrelated mounted
# images are left alone.
release_stale_helpers() {
  local pid
  local found
  found="$(lsof -n -w +D "$PWD/dist" +D "$PWD/build" 2>/dev/null \
    | awk 'NR > 1 && ($1 == "diskimages-helper" || $1 == "hdiutil") {print $2}' \
    | sort -u || true)"
  for pid in $found; do
    echo "==> Releasing stale disk image helper $pid"
    kill -TERM "$pid" 2>/dev/null || true
  done
  if [[ -n "$found" ]]; then
    sleep 1
    for pid in $found; do
      kill -KILL "$pid" 2>/dev/null || true
    done
  fi
}

echo "==> Building the disk image"
release_stale_helpers
STAGE="build/dmg"
DMG="dist/$APP_NAME-$VERSION.dmg"
rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
ditto "$APP" "$STAGE/$APP_NAME.app"
ln -s /Applications "$STAGE/Applications"
hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "$STAGE" \
  -ov \
  -format UDZO \
  "$DMG" >/dev/null
codesign --force --timestamp --sign "$IDENTITY" "$DMG"
hdiutil verify "$DMG" >/dev/null
echo "==> Disk image verified"

if [[ -n "${NOTARY_PROFILE:-}${NOTARY_KEY:-}" ]]; then
  # Resumable: an interrupted wait picks its submission back up instead of
  # uploading the disk image again.
  ./packaging/notarize.sh "$DMG"
else
  echo
  echo "Signed but not notarized. Gatekeeper rejects this build on other Macs"
  echo "until it is notarized; rerun with NOTARY_PROFILE or NOTARY_KEY set."
fi

echo
echo "App: $APP"
echo "DMG: $DMG"
