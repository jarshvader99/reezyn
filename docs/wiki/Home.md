# Reezyn wiki

Reezyn narrates coding-agent reasoning from the Mac menu bar. This wiki covers
the phone **remote access** feature, the Tailscale connection behind it, and how
to ship a signed and notarized build.

## Start here

| I want to… | Read |
| --- | --- |
| Connect my phone for the first time | [Tailscale setup](Tailscale-Setup) |
| Learn what the phone page can do | [Using the phone page](Using-the-Phone-Page) |
| Fix a blank page, pairing failure, or silent phone | [Troubleshooting](Troubleshooting) |
| Build, notarize, and verify a release | [Releasing and notarization](Releasing-and-Notarization) |

## How remote access fits together

```
iPhone / Android
  Tailscale app (same tailnet)
        │  HTTPS, tailnet only
        ▼
  https://<machine>.<tailnet>.ts.net
        │  Tailscale Serve
        ▼
  http://127.0.0.1:8787  (Reezyn on the Mac)
```

- Reezyn runs a small HTTP server on **loopback only** (`127.0.0.1:8787`).
- `tailscale serve` gives that loopback port an HTTPS name inside your tailnet.
  No router ports are opened and nothing is exposed to the public internet.
- The phone pairs once with a six-digit code from the Reezyn menu and keeps an
  HttpOnly session for 30 days.
- The page shows live activity, the recent feed, permission prompts, a reply
  box, hold-to-talk, read-aloud, and recap.

## Requirements

- macOS with Reezyn installed and running.
- A Tailscale account and the Tailscale app on both the Mac and the phone.
- Both devices signed in to the **same tailnet**.
- Tailscale's CLI integration installed on the Mac (the Reezyn menu links to
  the setting).
- If your tailnet requires it, HTTPS certificates and MagicDNS enabled in the
  Tailscale admin console.

## Security in one paragraph

Remote access is a tailnet-only page, not a public service. Sessions are random
bearer tokens stored only as SHA-256 hashes in `~/.reezyn/remote.json` (mode
`0600`). Pairing attempts are rate-limited, the pairing code expires after 15
minutes, and **Forget paired phones** revokes every session. The page cannot
press Return or type unless the Mac is unlocked, Reezyn has Accessibility
access, and the focused control passes Reezyn's checks. Phone recordings are
posted over the tailnet to the Mac and transcribed on-device; they are not
written to disk.

## Repository docs

These pages live in `docs/wiki/` so they can be reviewed with the code. The main
project README is at the repository root.
