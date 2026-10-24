# Troubleshooting remote access

Start with these three checks on the Mac:

```bash
# 1. Is Reezyn running?
pgrep -lf "/Applications/Reezyn.app/Contents/MacOS/Reezyn"

# 2. Is the loopback page listening?
lsof -nP -iTCP:8787 -sTCP:LISTEN

# 3. Is Tailscale publishing it?
tailscale serve status
tailscale status
```

And on the phone: is the Tailscale VPN **on**, signed in to the **same account**
as the Mac, and showing the Mac as reachable in the Tailscale app?

## The page is blank or returns 502

Tailscale is reachable, but nothing is behind its Serve mapping.

1. Check that Reezyn is running (`pgrep` above). Relaunch it from
   `/Applications/Reezyn.app` if not.
2. Check that the loopback page answers on the Mac:

   ```bash
   curl -i http://127.0.0.1:8787/
   ```

   If this fails, remote access is stopped or failed to bind. Open the Reezyn
   menu and turn **Remote access** off and on. Read the alert for the actual
   error.
3. Check `tailscale serve status`. It should map port 443 (or the `.ts.net`
   name) to `http://127.0.0.1:8787`. If the mapping points somewhere else,
   reset and republish:

   ```bash
   tailscale serve reset
   ```

   Then toggle remote access off and on in Reezyn.
4. Reload the phone page. The page is served with `Cache-Control: no-store`, so
   a normal pull-to-refresh is enough; clearing the Home Screen app is not
   usually necessary.

If Reezyn crashed, the Serve mapping stays behind. Relaunching Reezyn normally
brings the page back on the same port.

### Look for a crash

```bash
ls -lt ~/Library/Logs/DiagnosticReports/ | grep -i reezyn | head
```

Open the newest `.ips` file in Console, or inspect the top frames. The menu-bar
app should not need to be restarted during normal use; if it is crashing, include
the report with a bug report. `~/.reezyn/reezyn.log` has the last narrated lines
and remote events.

## “Remote access is local only”

Tailscale Serve is not enabled for the tailnet, or HTTPS certificates are off.

- Reezyn copies a one-time **enable link** when this happens. Open it, approve
  Serve, then turn remote access off and on.
- In the Tailscale admin console, enable **MagicDNS** and **HTTPS
  Certificates**.
- If your tailnet is managed, Serve may be disabled by policy; ask the tailnet
  admin.
- Check the exact error in **Remote access → Show connection…**.

The loopback URL (`http://127.0.0.1:8787/`) can still be tested on the Mac while
the tailnet publish is failing, but it will not work on the phone.

## The phone cannot open the `.ts.net` URL

- Is Tailscale running on the phone, and is its VPN toggle on?
- Is the phone signed in to the same tailnet? A device shared into another
  tailnet is not the same as being a member.
- Does `tailscale status` on the Mac show the phone as active?
- Try the Mac's Tailscale IP (`http://100.x.y.z:8787/`) only as a diagnostic. It
  is plain HTTP; microphone and speech may be blocked by the browser. Fix Serve
  instead of using the IP long-term.
- A certificate warning usually means HTTPS certificates are not enabled for the
  tailnet, or the phone has the wrong time.
- If the page loads on the Mac but not the phone, the problem is Tailscale, not
  Reezyn.

## Pairing code is rejected

- Codes expire 15 minutes after they are generated. Use **Remote access → New
  pairing code**.
- Only the current code works; generating a new one invalidates the old one.
- Repeated wrong codes rate-limit pairing for a short window. Wait a minute.
- If the code was entered correctly but the page returns to the pairing form,
  the cookie may be blocked. Allow cookies for the `.ts.net` site, then pair
  again. Private browsing can discard the session when the tab closes.
- **Forget paired phones** revokes every session. Pair again after using it.

## Send does nothing, or text lands in the wrong place

Sending uses synthetic keystrokes, so it is deliberately conservative.

- **Mac locked:** replies are queued (up to 20, for 30 minutes). The phone shows
  a banner; unlock the Mac and the queue flushes.
- **Accessibility off:** the phone shows a banner. Grant Reezyn Accessibility in
  System Settings → Privacy & Security → Accessibility.
- **Wrong app:** Reezyn brings the last allowlisted terminal forward, but it
  cannot focus an app that has quit. Open the terminal again and focus the
  agent's input on the Mac.
- **Not a text field:** click the prompt/input on the Mac once. Dictation and
  remote typing refuse to type into a non-text control.
- **No terminal yet:** Reezyn tracks the last allowlisted terminal. Use the Mac
  once with the agent focused, then try from the phone.

The allowlist is `voice.allow_apps` in `~/.reezyn/config.toml` (Terminal, iTerm2,
VS Code, Cursor, Ghostty, Warp, WezTerm, kitty, Alacritty, Hyper by default).

## Hold to talk produces no text

- The first microphone use requires a tap and a permission prompt; grant
  Microphone access to the browser for the `.ts.net` site.
- The Mac must be reachable: transcription happens on the Mac, not the phone.
- Speech Recognition permission must be granted to Reezyn on the Mac. Check
  **Voice permissions** in the menu.
- Very short recordings are rejected as too short. Hold for at least a syllable.
- Recordings are capped at 60 seconds.
- If the phone's microphone is being used by another app (or the AirPods moved
  to the Mac), the recording can be silent. Check the phone's input route.

## The phone is silent when reading aloud

This is the AirPods handoff problem the read-aloud design addresses.

1. Reload the page once after updating Reezyn.
2. Toggle **Read new lines aloud** off and on. iOS needs a user gesture to start
   audio.
3. While read-aloud is on, the Mac intentionally goes quiet and parks its
   microphone. If the Mac is still narrating, the phone has not claimed
   read-aloud: check the connection indicator and toggle again.
4. If the phone stays silent, turn read-aloud off, let the Mac speak once, then
   turn it back on. If the AirPods are still stuck on the Mac, switch them in
   Control Center or turn Bluetooth off and on.
5. If nothing helps, disconnect the AirPods on the phone, confirm sound through
   the phone speaker, then reconnect them.

If read-aloud is off, the Mac should narrate normally. If the Mac is silent with
read-aloud off, check the Mac's output device and volume, and check the Reezyn
log for `phone read-aloud is off; Mac narration is resumed`.

## Remote audio keeps switching between devices

- AirPods move to whichever device starts audio. Reezyn mutes the Mac while the
  phone reads, which is what keeps them on the phone.
- The Mac resumes after read-aloud is off, the page closes, or the phone stops
  checking in for 45 seconds.
- If the phone's screen locks and the browser suspends timers for a long time,
  the Mac may resume. Toggle read-aloud again when you come back.
- Do not leave read-aloud on if you want to listen on the Mac.

## Logs and diagnostics

```bash
# Reezyn's narration, device changes, and remote events
tail -f ~/.reezyn/reezyn.log

# Tailscale's view
tailscale status
tailscale serve status

# The page's local listener
lsof -nP -iTCP:8787 -sTCP:LISTEN

# macOS crash reports
ls -lt ~/Library/Logs/DiagnosticReports/ | grep -i reezyn | head
```

The menu has **Remote access → Show connection…**, **New pairing code**, and
**Forget paired phones**. **About Reezyn** and **Voice permissions** are useful
when the problem is TCC rather than the network.

Back to [Tailscale setup](Tailscale-Setup) or [Using the phone page](Using-the-Phone-Page).
