# Using the phone page

Open the Tailscale HTTPS address from [Tailscale setup](Tailscale-Setup) and pair
once. The page is a small, quiet console for when you step away from the Mac.

## The screen

- **Live** — the active agent and project, the current line, the last six
  spoken lines, and what the Mac's microphone is hearing. A colored dot shows
  whether Reezyn is speaking, listening, or paused.
- **Permission card** — appears when Claude Code or Gemini raises a permission
  prompt. **Allow** presses Return; **Deny** presses Escape in the last
  terminal. The card disappears when the prompt is answered or the agent moves
  on. Permission prompts expire on the agent's own timer, so answer them
  promptly.
- **Reply box / Send** — types your text into the focused terminal on the Mac
  and presses Return. It does not submit by magic: the Mac must be unlocked and
  the focus checks must pass.
- **Hold to talk** — records on the phone, posts the audio over the tailnet, and
  transcribes it on the Mac with Apple's on-device recognizer. The text lands in
  the reply box without sending, so you can edit it first.
- **Recap** — asks the Mac for the last 45 minutes of work and speaks the
  summary on the Mac (and on the phone when read-aloud is on).
- **Read new lines aloud** — uses the phone's own speech synthesis for new lines
  and permission prompts.
- **Connection indicator** — `connecting`, `live`, `reconnecting`, or `offline`.
  The page reconnects the event stream automatically.

## Read aloud and AirPods

AirPods automatically switch to whichever device starts playing audio. Reezyn
narrates often, so without help the Mac would keep pulling the headphones away
from the phone and the phone would fall silent.

While phone read-aloud is on:

- the Mac pauses narration and releases its audio output;
- the Mac parks its microphone and presence bed, because an open input or output
  stream also holds Bluetooth headphones;
- the phone keeps a short heartbeat and a quiet audio anchor so it keeps the
  headphones and iOS keeps speech synthesis alive.

The Mac resumes as soon as you turn read-aloud off, close the page, or the phone
stops checking in for 45 seconds. Lines spoken on the phone still appear in the
feed and remain available to **Recap**; they are not lost just because the Mac
was quiet.

If the phone goes silent after moving the AirPods:

1. Reload the page once after updating Reezyn (the page is served with
   `Cache-Control: no-store`, so a normal reload is enough).
2. Toggle **Read new lines aloud** off and on. On iOS, that tap is what allows
   audio to start.
3. If it is still silent, turn read-aloud off to let the Mac speak again, then
   try the toggle once more.

See [Troubleshooting](Troubleshooting) if the page itself will not load.

## What the phone can and cannot do

It **can**:

- read live state and the recent feed;
- type a reply into the focused terminal and press Return;
- press Return or Escape to answer a permission prompt;
- record a question and have the Mac transcribe it;
- read new lines aloud on the phone;
- ask for a recap.

It **cannot**:

- type while the Mac is locked (replies queue instead);
- type without Accessibility access on the Mac;
- type into an app that is not on the voice allowlist;
- type when the focused control is not a text field;
- start the Mac's microphone, unlock the Mac, or bypass TCC;
- work without Tailscale connected on the phone.

## Pairing and sessions

- A pairing code is shown in **Remote access → Show connection…** and expires
  after 15 minutes. **New pairing code** issues another.
- Pairing sets an HttpOnly session cookie for 30 days. The phone remembers it
  across reloads and Home Screen launches.
- **Forget paired phones** revokes every session immediately. Pair again with a
  new code.
- If the cookie expires or is revoked, the page shows the pairing form instead of
  the feed.

## Home Screen

Add the page to the Home Screen for a standalone window. On iOS: Share → **Add
to Home Screen**. The page supplies a manifest, icon, and standalone display
mode. The microphone and read-aloud still require a tap before iOS starts audio.

## Privacy

- The phone page is reachable only over your tailnet.
- Phone audio is posted to the Mac over that tailnet and transcribed on-device.
  It is not written to disk.
- Spoken lines are written to `~/.reezyn/reezyn.log` for debugging, like the rest
  of Reezyn's narration.
- Replies are logged only as `remote send`; the text itself is not logged.

Back to [Tailscale setup](Tailscale-Setup) or [Troubleshooting](Troubleshooting).
