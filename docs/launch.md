# Launch: getting the first thousand downloads

The repo, the cask, the films and the download count are already in place. What
follows is the order to post them in, the copy to post, and the one thing to say
when someone notices the age of the project.

## The two-day thing

Do not hide it and do not backdate anything. Commit dates are public, and a
history that has been rewritten is the only thing here that would look bad.
Own it in the first sentence of every post:

> Reezyn is two days old. It is MIT, 281 tests, and about 14,000 lines of Python
> that run entirely on your Mac with no network calls. You can read all of it
> this afternoon.

"Built in a weekend" is a feature for a small developer tool, not a red flag:
it means the scope is honest, the dependency list is short, and there is no
funding to recoup. What reads as suspicious is a two-day project asking for
money or an account. This one asks for neither.

Two supporting facts do more work than any amount of polish:

- The source is public and the tests run in CI-shaped commands: `uv run pytest`.
- The app is signed with a Developer ID and notarized, so it opens without a
  warning. Say that; it is rare for a weekend project.

## What to post, where

| Channel | Asset | Why |
| --- | --- | --- |
| X | `short-vertical.mp4` (32 s, 9:16) | Plays inline in the timeline; the first second is the hook |
| LinkedIn | `short.mp4` (60 s, 16:9) | Reads as a product update, tolerates a longer caption |
| Show HN | `short.mp4` link + repo | This crowd wants the source, the tests, and the local-first argument |
| r/ClaudeAI, r/ChatGPTCoding | `short-vertical.mp4` | They already know the problem of never seeing the reasoning |
| r/macapps | `brag.mp4` (24 s) + cask line | That board installs from Homebrew and cares about the menu bar |
| Mastodon, Bluesky | `short-vertical.mp4` | Same as X, no algorithm to fight |
| Product Hunt | `brag.mp4`, gallery from `site/assets` | Later, once the first hundred installs have shaken out the bugs |

Order: X first (fastest feedback, and the vertical cut is built for it), then
Show HN the same morning, then the subreddits a day apart so comments do not
overlap, then LinkedIn once there is a real install number to quote.

## Copy

X, with `short-vertical.mp4` attached:

> Your coding agent thinks in full sentences. You see almost none of them.
>
> Reezyn speaks the reasoning your agent already writes down. Locally. Kokoro
> voice, no cloud, no summary model, no account.
>
> Free, MIT, macOS. Two days old, 281 tests.
>
> brew install --cask jarshvader99/tap/reezyn
> https://reezyn.elastikmind.com

Show HN, title: `Show HN: Reezyn – your coding agent's reasoning, spoken locally`

> I kept losing the thread of what the agent was doing, so I made it talk.
>
> Reezyn runs in the menu bar, watches Claude Code, Codex, Gemini CLI, Cursor,
> OpenCode and Freebuff, and speaks short excerpts of the reasoning they already
> write to their local session logs. No LLM in the loop: it reads the sentence
> the agent wrote, cleans it up, and hands it to Kokoro on-device.
>
> The queue is the interesting part. Agents fire tool calls in bursts, so it
> waits for a pause, keeps the most recent useful thought, dedupes against the
> last 30 seconds, and drops anything stale rather than reading a backlog.
>
> It is local: no cloud calls, no telemetry, no transcript upload. It can also
> take a spoken reply ("Reezyn, rerun the auth tests") and type it at the caret
> without submitting, and it serves a page on your tailnet so you can answer a
> permission prompt from your phone.
>
> macOS 13+, Apple silicon, signed and notarized, MIT. Built in a weekend;
> 281 tests; the source is the README.
>
> https://reezyn.elastikmind.com

Replies to "why should I trust a two-day project":

> You shouldn't, yet. Read it first: the adapter, the coalescer and the audio
> path are three files and there is a test per behaviour. It has no network code
> in the narration path at all, which is the claim that matters here, and you can
> check that with a packet capture in five minutes.

## What to measure

Both numbers are counts, not people, and neither uses a cookie or a script:

```bash
python packaging/download_counts.py --github          # release assets
python packaging/download_counts.py --log access.log  # the site's own log
```

Read them a day apart, not hourly. The useful ratio is installs to repo visits:
if the site brings traffic and the cask brings installs, the page is doing its
job and the copy is not.

## What not to do

- No backdated commits, no invented contributors, no fake stars.
- No "we" for a one-person project, and no roadmap of features that do not exist.
- No astroturfed replies. The privacy claim is checkable, so let people check it.
