# Connect Reezyn to your phone over Tailscale

This is the first-time setup. It takes about five minutes. You need the Mac and
the phone on the **same Tailscale account** (the same tailnet).

Reezyn uses Tailscale **Serve**, not Funnel. Serve is reachable only from your
tailnet; Funnel would publish to the public internet and is deliberately not
used.

## 1. Install Tailscale on the Mac

Install the Mac app from <https://tailscale.com/download/mac> or the Mac App
Store, open it, and sign in.

Then install the command-line integration, which Reezyn uses to publish the
page:

1. Click the Tailscale icon in the menu bar.
2. Open **Settings → CLI integration**.
3. Click **Install Now**.

Confirm the CLI is available:

```bash
tailscale version
tailscale status
```

If `tailscale` is not on your `PATH`, the app also works here:

```bash
/Applications/Tailscale.app/Contents/MacOS/Tailscale version
```

For convenience in the rest of this guide:

```bash
alias tailscale="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
```

You do **not** need `sudo` for `tailscale serve` on the macOS app.

## 2. Install Tailscale on the phone

Install Tailscale from the App Store or Play Store, sign in with the **same
account**, and turn the VPN on. The phone must show as connected, not merely
installed.

Check that the Mac and phone can see each other:

```bash
tailscale status
```

Each device should appear in the list with a `100.x.y.z` address.

## 3. Check the tailnet prerequisites

In the [Tailscale admin console](https://login.tailscale.com/admin):

- **DNS → MagicDNS** should be enabled. Reezyn publishes the page at the
  machine's `.ts.net` name.
- **DNS → HTTPS Certificates** should be enabled if your tailnet does not
  already have certificates. Serve needs a certificate to terminate HTTPS.
- If you use a managed tailnet, make sure Serve is allowed by policy.

If HTTPS certificates are off, `tailscale serve` reports an error and the
Reezyn menu shows it. Enable certificates, then turn remote access off and on.

## 4. Turn on remote access in Reezyn

1. Click the Reezyn seedling in the Mac menu bar.
2. Choose **Remote access → Turn on remote access**.
3. The first time, Tailscale may ask you to approve Serve. Reezyn copies the
   one-time enable link and offers to open it. Open the link, approve Serve,
   then turn remote access **off and on** once.
4. Reezyn shows a URL like:

   ```
   https://your-mac.your-tailnet.ts.net
   ```

   The menu title changes to **Remote access · on** when it is published.

If the menu says **Remote access is local only**, Serve was not published. Read
the message in the menu, use the copied enable link, and toggle remote access
again. The loopback page may already work at
`http://127.0.0.1:8787/` on the Mac while the tailnet publish is pending.

## 5. Pair the phone

1. On the phone, with Tailscale connected, open the `https://…ts.net` URL.
2. On the Mac, open **Remote access → Show connection…**. It shows the URL and a
   six-digit **pairing code**.
3. Enter the code on the phone and tap **Pair**.
4. The phone now has a session that lasts 30 days. The code itself expires after
   15 minutes; use **New pairing code** if it runs out.

Add the page to the phone's Home Screen for a standalone app. On iOS, use Share
→ **Add to Home Screen**; the page supplies a web app manifest and icon.

Once paired, the phone can:

- watch the live agent, project, current line, and recent feed;
- answer permission prompts with **Allow** / **Deny**;
- send a typed reply, or hold **Hold to talk** and release to transcribe;
- turn on **Read new lines aloud**;
- request a **Recap**.

See [Using the phone page](Using-the-Phone-Page) for the details.

## 6. Verify the connection

On the Mac:

```bash
tailscale status
tailscale serve status
curl -I http://127.0.0.1:8787/
curl -I https://your-mac.your-tailnet.ts.net/
```

Expected: the loopback page returns `200`, and `tailscale serve status` shows a
mapping from the HTTPS name to `http://127.0.0.1:8787`.

From the phone's browser, the URL should load the Reezyn page. If it does not,
work through [Troubleshooting](Troubleshooting).

## Daily use

- Leave Tailscale connected on the phone when you want the page.
- The Mac stays awake while remote access is on (`caffeinate`), but macOS still
  locks on its own schedule or when the lid closes.
- Sending types into the focused terminal and presses Return. A locked Mac
  queues replies (up to 20, for 30 minutes) and flushes them when you unlock.
- Turn remote access off in the Reezyn menu when you are done. The Tailscale
  Serve mapping remains after Reezyn stops, pointing at a closed port; remove it
  with `tailscale serve reset` if you want the name gone.
- **Forget paired phones** revokes every phone session.

## Useful commands

```bash
# Is Tailscale connected, and what is this machine's name?
tailscale status

# What is Serve publishing?
tailscale serve status
tailscale serve status --json

# Remove Reezyn's Serve mapping (does not uninstall Tailscale)
tailscale serve reset

# Watch Reezyn's log while testing
tail -f ~/.reezyn/reezyn.log
```

## Remote access settings

The menu edits `~/.reezyn/config.toml`:

```toml
[remote]
enabled = true
port = 8787             # loopback only; Tailscale Serve publishes it
keep_awake = true       # caffeinate while remote access is on
queue_when_locked = true
read_aloud = false      # initial default for the phone page
session_days = 30       # how long a paired phone stays paired
```

Changes to `config.toml` are read when the menu-bar app starts. Use the menu for
normal changes.

## Security notes for reviewers

- The HTTP server binds to `127.0.0.1`; it is not reachable from the LAN.
- Tailscale Serve terminates HTTPS with a tailnet certificate. There is no
  public DNS record and no forwarded router port.
- Pairing issues a random bearer token in an HttpOnly, SameSite=Strict cookie.
  The store keeps only the SHA-256 hash on disk.
- Cross-origin POSTs are refused, pairing attempts are rate-limited, and the
  pairing code is never served by the page.
- The page can only type when the screen is unlocked, Accessibility is granted,
  the frontmost app is on the voice allowlist, and the focused control is a text
  field.
- Do not use Tailscale Funnel with Reezyn. Funnel exposes the page to the public
  internet, which is outside the threat model.
