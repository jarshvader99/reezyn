"""Wake phrase, intent, and dictation-command parsing.

Apple's recognizer has never heard of "Reezyn", so the wake word is matched
fuzzily against the spellings a speech model is likely to produce. A bare
"reezyn" has to be close; after a greeting ("hey") a looser match is allowed,
because that is where "hey reason" comes from.
"""

from __future__ import annotations

import re

WAKE_TARGET = "reezyn"
GREETINGS = frozenset({"hey", "hi", "ok", "okay", "yo", "hay", "a"})
# Spellings the recognizer has produced (or is likely to) for the name.
WAKE_ALIASES = frozenset(
    {
        "reezyn",
        "reesin",
        "reeson",
        "reezin",
        "reezon",
        "reazyn",
        "rezin",
        "reesyn",
        "resin",
        "risen",
        "reason",
        "reason's",
        "reasons",
    }
)

PUNCTUATION = re.compile(r"[^\w\s']")
WHITESPACE = re.compile(r"\s+")

CANCEL = frozenset(
    {
        "never mind",
        "nevermind",
        "cancel",
        "cancel that",
        "cancel listening",
        "stop listening",
        "forget it",
        "stop",
    }
)
SCRATCH = frozenset(
    {"scratch that", "delete that", "undo that", "scratch", "undo", "delete it"}
)
SEND = frozenset(
    {"send it", "send", "submit", "submit it", "enter", "send message", "send that"}
)
# Answers to "Ready to send?" after a dictation pause.
CONFIRM = frozenset(
    {"yes", "yeah", "yep", "sure", "okay", "ok", "go ahead", "send it now", "send now"}
)
KEEP = frozenset(
    {
        "no",
        "nope",
        "not yet",
        "not now",
        "wait",
        "keep editing",
        "keep going",
        "leave it",
        "don't send",
        "dont send",
    }
)

# The hot keyword that sends without waiting for the question. It is matched at
# the end of a sentence so it can be spoken as part of the dictation itself.
TRAILING_SEND = re.compile(
    r"(?:^|[\s,.;:!?])(?:send it now|send now|send it|send that|submit it|"
    r"go ahead and send|over and send)[\s.!?,]*$",
    re.IGNORECASE,
)
# Words that can introduce the keyword without being part of the message.
SEND_LEAD_INS = frozenset(
    {
        "",
        "please",
        "and",
        "then",
        "and then",
        "go ahead",
        "go ahead and",
        "yes",
        "yeah",
        "yep",
        "sure",
        "ok",
        "okay",
        "alright",
        "fine",
    }
)
ALLOW = frozenset(
    {
        "allow",
        "allow it",
        "allow once",
        "yes",
        "yeah",
        "yep",
        "approve",
        "okay",
        "ok",
        "go ahead",
    }
)
DENY = frozenset(
    {
        "deny",
        "no",
        "nope",
        "reject",
        "decline",
        "don't allow",
        "dont allow",
        "block",
        "cancel",
    }
)

# Longest phrases first so "exclamation point" wins over "point".
DICTATION = (
    ("exclamation point", "!"),
    ("exclamation mark", "!"),
    ("question mark", "?"),
    ("full stop", "."),
    ("open parenthesis", "("),
    ("close parenthesis", ")"),
    ("left parenthesis", "("),
    ("right parenthesis", ")"),
    ("open paren", "("),
    ("close paren", ")"),
    ("left paren", "("),
    ("right paren", ")"),
    ("open bracket", "["),
    ("close bracket", "]"),
    ("open brace", "{"),
    ("close brace", "}"),
    ("open quote", '"'),
    ("close quote", '"'),
    ("double quote", '"'),
    ("single quote", "'"),
    ("dollar sign", "$"),
    ("at sign", "@"),
    ("back tick", "`"),
    ("new line", "\n"),
    ("newline", "\n"),
    ("underscore", "_"),
    ("backslash", "\\"),
    ("backtick", "`"),
    ("semicolon", ";"),
    ("asterisk", "*"),
    ("asterix", "*"),
    ("ampersand", "&"),
    ("apostrophe", "'"),
    ("hyphen", "-"),
    ("percent", "%"),
    ("period", "."),
    ("comma", ","),
    ("colon", ":"),
    ("slash", "/"),
    ("dash", "-"),
    ("quote", '"'),
    ("pipe", "|"),
    ("caret", "^"),
    ("tilde", "~"),
    ("equals", "="),
    ("plus", "+"),
    ("hash", "#"),
)


def normalize(text: str) -> str:
    value = text.lower().replace("\u2019", "'")
    value = PUNCTUATION.sub(" ", value)
    return WHITESPACE.sub(" ", value).strip()


def words(text: str) -> list[str]:
    value = normalize(text)
    return value.split() if value else []


def _distance(first: str, second: str, limit: int = 3) -> int:
    if abs(len(first) - len(second)) > limit:
        return limit + 1
    previous = list(range(len(second) + 1))
    for index, char in enumerate(first, start=1):
        current = [index]
        for position, other in enumerate(second, start=1):
            current.append(
                min(
                    previous[position] + 1,
                    current[position - 1] + 1,
                    previous[position - 1] + (char != other),
                )
            )
        previous = current
    return previous[-1]


def is_wake_word(word: str, loose: bool = False) -> bool:
    word = word.strip("'\"").lower()
    if not word:
        return False
    if word in WAKE_ALIASES:
        return True
    return _distance(word, WAKE_TARGET, 2 if loose else 1) <= (2 if loose else 1)


def find_wake(text: str, wake_word: str = "hey reezyn") -> tuple[int, int] | None:
    """The (start, end) word span of a wake phrase at the start of an utterance."""

    tokens = words(text)
    if not tokens:
        return None
    phrase = words(wake_word) or [WAKE_TARGET]
    if len(phrase) > 1 and tokens[0] in GREETINGS:
        if len(tokens) > 1 and is_wake_word(tokens[1], loose=True):
            return (0, 2)
        return None
    if is_wake_word(tokens[0], loose=False):
        return (0, 1)
    return None


def strip_wake(text: str, span: tuple[int, int] | None = None) -> str:
    """Remove the wake phrase from the front, keeping the command's words."""

    span = span or find_wake(text)
    if span is None:
        return text.strip()
    tokens = text.split()
    keep = tokens[span[1] :]
    if not keep:
        return ""
    return " ".join(keep).lstrip(" ,.;:!?-")


def parse_intent(
    text: str, permission: bool = False, confirmation: bool = False
) -> tuple[str, str]:
    """Classify an utterance. Anything that is not a command is text to type."""

    key = normalize(text)
    if not key:
        return ("empty", "")
    first = key.split()[0]

    if confirmation:
        tokens = key.split()
        if key in CONFIRM or (
            len(tokens) <= 3 and first in {"yes", "yeah", "yep", "sure", "ok", "okay"}
        ):
            return ("send", "")
        if key in KEEP or (len(tokens) <= 3 and first in {"no", "nope", "wait"}):
            return ("keep", "")
        # "Scratch that" and "never mind" still work while the question is open;
        # anything else is more dictation, not an answer.
        if key not in CANCEL and key not in SCRATCH and key not in SEND:
            return ("text", text)

    if permission:
        # Only a short answer counts as a decision. "Yes, but use staging" is a
        # sentence to type, not a silent press of Return.
        tokens = key.split()
        if len(tokens) <= 3:
            if key in ALLOW or first in {"yes", "yeah", "yep", "allow", "approve", "okay", "ok"}:
                return ("allow", "")
            if key in DENY or first in {"no", "nope", "deny", "reject", "decline", "block"}:
                return ("deny", "")
        return ("text", text)

    if key in CANCEL:
        return ("cancel", "")
    if key in SCRATCH:
        return ("scratch", "")
    if key in SEND:
        return ("send", "")
    return ("text", text)


def take_trailing_send(text: str) -> tuple[str, bool]:
    """Split a send keyword off the end of a dictation.

    "Add a login test, send it now" types the request and submits it in one
    breath, with no confirmation pause.
    """

    match = TRAILING_SEND.search(text)
    if match is None:
        return (text, False)
    remaining = text[: match.start()].rstrip(" \t,.;:!?-")
    if normalize(remaining) not in SEND_LEAD_INS:
        return (remaining, True)
    return ("", True)


def expand_dictation(text: str) -> str:
    """Turn spoken punctuation into the characters a prompt needs."""

    # Keep spoken newlines at the edges; only ordinary spaces are trimmed.
    value = text.strip(" \t")
    for phrase, symbol in DICTATION:
        pattern = re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE)
        # A function replacement keeps backslash from being read as an escape.
        value = pattern.sub(lambda _match, replacement=symbol: replacement, value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" +([_\-/.,;:!?)\]}])", r"\1", value)
    # Glue a symbol to the word that follows it, so "snake underscore case"
    # becomes snake_case rather than "snake _ case".
    value = re.sub(r"([_\-/(\[{]) +", r"\1", value)
    return value.strip(" \t")
