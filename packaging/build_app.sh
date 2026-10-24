#!/usr/bin/env bash
# Build, sign, and package Reezyn.app for distribution.
#
#   packaging/build_app.sh
#
# Outputs dist/Reezyn.app and dist/Reezyn-<version>.dmg, signed with the
# Developer ID Application identity. Notarization runs when credentials are
# provided through the environment:
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

echo "==> Building the disk image"
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

if [[ -n "${NOTARY_PROFILE:-}" ]]; then
  echo "==> Notarizing with keychain profile: $NOTARY_PROFILE"
  xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
elif [[ -n "${NOTARY_KEY:-}" ]]; then
  echo "==> Notarizing with API key: $NOTARY_KEY"
  notary_args=(--key "$NOTARY_KEY" --key-id "${NOTARY_KEY_ID:?set NOTARY_KEY_ID}")
  if [[ -n "${NOTARY_ISSUER:-}" ]]; then
    notary_args+=(--issuer "$NOTARY_ISSUER")
  fi
  xcrun notarytool submit "$DMG" "${notary_args[@]}" --wait
  xcrun stapler staple "$DMG"
else
  echo
  echo "Signed but not notarized. Gatekeeper rejects this build on other Macs"
  echo "until it is notarized; rerun with NOTARY_PROFILE or NOTARY_KEY set."
fi

echo
echo "App: $APP"
echo "DMG: $DMG"
