# Reezyn — agent notes

## Security audits use the `security-audit` skill

Every security audit, security review, vulnerability hunt, or pen-test request
in this repository runs through the `security-audit` skill installed at
`.agents/skills/security-audit/` (Cloudflare, MIT). Load it with the skill tool
before starting and follow its workflow instead of an ad-hoc read-through:
reconnaissance and a coverage ledger, coverage-led hunting, a fresh verifier
for every candidate, `findings.json` checked with the skill's validators,
independent record verification, then the report.

Reasons this is the default rather than advice: it separates `confirmed` from
`needs_validation`, which matters here because the updater, the Tailscale
publish path, and the notarization step all depend on behavior that is not in
this repository. Guessing at that behavior is how a hardening note gets filed as
a vulnerability, or the reverse.

A narrow question ("is this function safe?") is guidance mode — read the
relevant skill files and answer. Only an explicit audit or pen-test request runs
the full six-phase workflow and writes report artifacts.

Keep audit output outside the repository unless a specific in-repo directory is
requested and version control ignores it.
