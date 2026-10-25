# Releasing and notarization

`packaging/build_app.sh` builds `Reezyn.app`, signs it with the Developer ID
Application certificate, builds and signs the DMG, and — when notarization
credentials are present — submits the DMG to Apple and staples the ticket.

A build that is signed but **not notarized** is rejected by Gatekeeper on other
Macs. The script says so at the end. This page covers the remaining steps.

## One-step release

Once the notarization credentials are stored, the whole release is one command:

```bash
packaging/release.sh patch            # 0.2.0 -> 0.2.1
packaging/release.sh 0.3.0 --no-deploy
```

The script refuses to run on a dirty tree, then:

1. bumps `pyproject.toml`, `reezyn/__init__.py` and the version and both download
   links in `site/index.html`, and refreshes `uv.lock`;
2. runs the test suite;
3. builds and notarizes with `NOTARY_PROFILE` (default `reezyn`), failing if the
   build came out signed but not notarized;
4. copies the DMG into `site/downloads/`, removes the previous one, and stamps the
   new SHA-256 and size into the page;
5. commits the release;
6. deploys `site/` to `reezyn.elastikmind.com` through the Hostinger hosting MCP
   server, which authenticates through the Hostinger extension.

Flags: `--no-deploy` stops before step 6, `--no-notarize` signs only, and
`--skip-tests` skips step 2.

## Minimal checklist

Do this once on a Mac that has the Developer ID certificate:

1. Confirm the signing identity:
   `security find-identity -v -p codesigning`
2. Store notarization credentials under a keychain profile (see below):
   `xcrun notarytool store-credentials "reezyn-notary" …`
3. Build with the profile:
   `NOTARY_PROFILE=reezyn-notary packaging/build_app.sh`
4. Verify the result:
   `xcrun stapler validate dist/Reezyn-0.1.0.dmg`
   `spctl --assess --type open --context context:primary-signature --verbose=4 dist/Reezyn-0.1.0.dmg`

If step 3 ends with “Signed but not notarized,” the profile name did not reach
the build script or authentication failed. Read the notary log as described
below.

## What you need

1. An Apple Developer Program membership.
2. A **Developer ID Application** certificate in the login keychain:

   ```bash
   security find-identity -v -p codesigning
   ```

   Look for a line such as:

   ```
   "Developer ID Application: Joshua Harrison (H4G8F4Q8D2)"
   ```

   The build script uses that identity by default. Override it with
   `REEZYN_SIGN_IDENTITY` if your certificate has a different name.
3. Notarization credentials. Choose **one** of these:
   - an Apple ID and an **app-specific password**, or
   - an App Store Connect API key (`.p8` + Key ID + Issuer ID).

Do not put any of these in the repository. `notarytool` stores credentials in
the macOS keychain under a profile name; the build script only reads that profile
name from the environment.

## One-time credential setup

### Option A: Apple ID + app-specific password

Create an app-specific password at <https://appleid.apple.com> → **Sign-In and
Security** → **App-Specific Passwords**. Use that password here, never your
Apple ID password:

```bash
xcrun notarytool store-credentials "reezyn-notary" \
  --apple-id "you@example.com" \
  --team-id "H4G8F4Q8D2" \
  --password "abcd-efgh-ijkl-mnop"
```

The `--team-id` is the ten-character team ID shown in the certificate and in the
Apple Developer portal.

### Option B: App Store Connect API key

In App Store Connect → **Users and Access** → **Integrations** → **App Store
Connect API**, create a **Team Key**, download the `.p8` (only once), and note
its **Key ID** and the team's **Issuer ID**.

```bash
xcrun notarytool store-credentials "reezyn-notary" \
  --key "/absolute/path/AuthKey_ABC123DEFG.p8" \
  --key-id "ABC123DEFG" \
  --issuer "00000000-0000-0000-0000-000000000000"
```

For an individual API key, omit `--issuer` if App Store Connect does not show
one. Keep the `.p8` outside the repository.

Confirm the profile works:

```bash
xcrun notarytool history --keychain-profile "reezyn-notary"
```

An empty history is fine; an authentication error is not.

## Build and notarize

```bash
NOTARY_PROFILE=reezyn-notary packaging/build_app.sh
```

The script will:

1. render the icon and run PyInstaller;
2. codesign the app with `--options runtime`, a secure timestamp, and
   `packaging/Reezyn.entitlements`;
3. codesign the DMG;
4. submit the DMG with `notarytool submit --wait`;
5. staple the notarization ticket to the DMG.

If you prefer the API-key environment path instead of a stored profile:

```bash
NOTARY_KEY="/absolute/path/AuthKey_ABC123DEFG.p8" \
NOTARY_KEY_ID="ABC123DEFG" \
NOTARY_ISSUER="00000000-0000-0000-0000-000000000000" \
packaging/build_app.sh
```

The output ends with:

```
App: dist/Reezyn.app
DMG: dist/Reezyn-0.1.0.dmg
```

and should **not** contain “Signed but not notarized.”

## Manual submission and verification

If a build was signed but not submitted, or you want to re-run notarization
without rebuilding, submit the existing DMG:

```bash
xcrun notarytool submit dist/Reezyn-0.1.0.dmg \
  --keychain-profile "reezyn-notary" --wait
```

Save the submission ID from the output, then, on success:

```bash
xcrun stapler staple dist/Reezyn-0.1.0.dmg
xcrun stapler validate dist/Reezyn-0.1.0.dmg
```

Verify the DMG:

```bash
spctl --assess --type open \
  --context context:primary-signature --verbose=4 \
  dist/Reezyn-0.1.0.dmg
```

Expected: `accepted` with `source=Notarized Developer ID`.

Verify the app's signature and hardened runtime:

```bash
codesign --verify --deep --strict --verbose=2 dist/Reezyn.app
codesign -dvvv --entitlements :- dist/Reezyn.app
spctl --assess --type execute --verbose=4 dist/Reezyn.app
```

The `codesign -dvvv` output should show `flags=0x10000(runtime)` (hardened
runtime) and a `TeamIdentifier` matching the certificate. The entitlements
should include:

- `com.apple.security.cs.disable-library-validation` — onnxruntime, cffi, and
  the espeak bridge load their own dylibs;
- `com.apple.security.device.audio-input` — AVAudioEngine input.

`spctl --assess --type execute` on the **unstapled app** can report
`rejected` even when the notarized DMG installs correctly. The ticket that
Gatekeeper uses for a DMG install is stapled to the DMG. To distribute the app
directly (zip or drag-copy), staple the app too:

```bash
ditto -c -k --keepParent dist/Reezyn.app dist/Reezyn.zip
xcrun notarytool submit dist/Reezyn.zip \
  --keychain-profile "reezyn-notary" --wait
xcrun stapler staple dist/Reezyn.app
xcrun stapler validate dist/Reezyn.app
```

## If notarization fails

Read the detailed log:

```bash
xcrun notarytool log <submission-id> --keychain-profile "reezyn-notary"
```

Common causes:

| Message | Fix |
| --- | --- |
| `The signature of the binary is invalid` / `code object is not signed at all` | A nested binary or framework is unsigned. Rebuild so PyInstaller's bundle is signed, or sign nested code before the outer app. `--deep` is already used; verify with `codesign --verify --deep --strict`. |
| `The executable does not have the hardened runtime enabled` | Sign with `--options runtime` (the script does). |
| `Invalid entitlement` / `not allowed` | Check `packaging/Reezyn.entitlements`; only the two entitlements above are expected. |
| `The software is not signed with a valid Developer ID certificate` | Wrong identity or an expired certificate. Run `security find-identity -v -p codesigning` and set `REEZYN_SIGN_IDENTITY`. |
| `You must agree to the Apple Developer Program License Agreement` | Accept the current agreement in App Store Connect. |
| Authentication / 401 from `notarytool` | Re-create the keychain profile; for Apple ID use an app-specific password, and check the team ID. |
| `The staple and validate action failed` | Submit succeeded but stapling ran on a different file or before the ticket was ready. Re-run `stapler staple` after `--wait` completes. |
| `The bundle is damaged` on another Mac | The build was not notarized/stapled, or it was modified after signing. Rebuild, notarize, staple, and copy the DMG without editing it. |

## CI notes

- Store `NOTARY_PROFILE` and the keychain item in the CI runner's keychain, or
  inject `NOTARY_KEY`, `NOTARY_KEY_ID`, and `NOTARY_ISSUER` as masked secrets.
- Never commit the `.p8`, the app-specific password, or a keychain export.
- Notarization is asynchronous; always use `--wait` (the script does) before
  stapling or publishing.
- Build on the same macOS architecture you ship. The current PyInstaller bundle
  is Apple Silicon; universal builds need a universal Python and universal
  native dependencies (`onnxruntime`, `portaudio`).
- Keep the signed `.app` and `.dmg` in `dist/`; both are git-ignored.

## Current release status

The repository's build script signs the app and DMG but deliberately does not
invent credentials. A build without `NOTARY_PROFILE` (or the API-key variables)
is **signed, not notarized**, and Gatekeeper rejects it on other Macs. This Mac
has a keychain profile named `reezyn`, which `packaging/release.sh` uses by
default; releases since 0.2.0 are notarized and stapled.

Back to [Home](Home).
