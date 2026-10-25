#!/usr/bin/env bash
# Notarize and staple a disk image, resumably.
#
#   packaging/notarize.sh dist/Reezyn-0.3.0.dmg
#
# Uploading and waiting are two separate phases. The submission id is written to
# dist/.notary/<version>.state as soon as the upload returns, so a run that dies
# while waiting resumes by polling that submission instead of uploading sixty
# megabytes again. An already-accepted submission is only stapled, and an image
# that already carries its ticket is left byte for byte alone.
#
# Environment:
#   NOTARY_PROFILE          keychain profile (default: reezyn)
#   NOTARY_KEY              App Store Connect API key (.p8) instead of a profile
#   NOTARY_KEY_ID           that key's Key ID
#   NOTARY_ISSUER           the issuer ID (omit for a single-user key)
#   NOTARY_TIMEOUT          seconds to wait for a verdict (default 1800)
#   NOTARY_POLL_INTERVAL    seconds between status checks (default 20)
#   NOTARY_ATTEMPTS         upload attempts (default 3)
#   NOTARY_UPLOAD_TIMEOUT   seconds per upload attempt (default 600)
#   NOTARY_RETRY_DELAY      seconds between upload attempts (default 10)
#   NOTARY_STATE_DIR        where submission ids are kept (default dist/.notary)

set -euo pipefail

cd "$(dirname "$0")/.."

DMG="${1:-}"
if [[ -z "$DMG" || ! -f "$DMG" ]]; then
  echo "usage: packaging/notarize.sh <path/to/Reezyn-X.Y.Z.dmg>" >&2
  exit 2
fi

NOTARY_TIMEOUT="${NOTARY_TIMEOUT:-1800}"
NOTARY_POLL_INTERVAL="${NOTARY_POLL_INTERVAL:-20}"
NOTARY_ATTEMPTS="${NOTARY_ATTEMPTS:-3}"
NOTARY_UPLOAD_TIMEOUT="${NOTARY_UPLOAD_TIMEOUT:-600}"
NOTARY_RETRY_DELAY="${NOTARY_RETRY_DELAY:-10}"

auth_args=()
if [[ -n "${NOTARY_KEY:-}" ]]; then
  auth_args=(--key "$NOTARY_KEY" --key-id "${NOTARY_KEY_ID:?set NOTARY_KEY_ID}")
  if [[ -n "${NOTARY_ISSUER:-}" ]]; then
    auth_args+=(--issuer "$NOTARY_ISSUER")
  fi
else
  NOTARY_PROFILE="${NOTARY_PROFILE:-reezyn}"
  auth_args=(--keychain-profile "$NOTARY_PROFILE")
fi

NAME="$(basename "$DMG")"
SHA="$(shasum -a 256 "$DMG" | awk '{print $1}')"
STATE_DIR="${NOTARY_STATE_DIR:-dist/.notary}"
STATE_FILE="$STATE_DIR/${NAME%.dmg}.state"

# Value of one key in the state file, or nothing.
state_get() {
  [[ -f "$STATE_FILE" ]] || return 0
  sed -n "s/^$1=//p" "$STATE_FILE" | head -1
}

state_put() {
  mkdir -p "$STATE_DIR"
  local tmp
  tmp="$(mktemp "$STATE_DIR/.state.XXXXXX")"
  {
    printf 'sha256=%s\n' "${1:-$SHA}"
    printf 'id=%s\n' "${2:-}"
    printf 'status=%s\n' "${3:-}"
    printf 'updated=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$tmp"
  mv "$tmp" "$STATE_FILE"
}

# Run a command, killing it after a fixed number of seconds. Writes nothing to
# stdout; the caller redirects if it cares. Returns 124 on timeout.
run_with_timeout() {
  local seconds=$1
  shift
  "$@" &
  local pid=$! waited=0 stat
  while :; do
    stat="$(ps -o stat= -p "$pid" 2>/dev/null || true)"
    if [[ -z "$stat" || "$stat" == Z* ]]; then
      local rc=0
      wait "$pid" || rc=$?
      return $rc
    fi
    if (( waited >= seconds )); then
      kill -TERM "$pid" 2>/dev/null || true
      sleep 2
      kill -KILL "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
      return 124
    fi
    sleep 1
    waited=$(( waited + 1 ))
  done
}

# The submission's status, or "unknown" when Apple has not seen it (or has
# already forgotten it).
submit_status() {
  local id=$1 out
  if ! out="$(xcrun notarytool info "$id" "${auth_args[@]}" --output-format json 2>/dev/null)"; then
    echo unknown
    return 0
  fi
  printf '%s' "$out" | plutil -extract status raw -o - - 2>/dev/null || echo unknown
}

upload() {
  local attempt=1 out id
  while (( attempt <= NOTARY_ATTEMPTS )); do
    out="$(mktemp -t reezyn-notary)"
    echo "==> Uploading $NAME (attempt $attempt/$NOTARY_ATTEMPTS)" >&2
    if run_with_timeout "$NOTARY_UPLOAD_TIMEOUT" \
        xcrun notarytool submit "$DMG" "${auth_args[@]}" \
        --no-wait --output-format json >"$out" 2>&1; then
      id="$(plutil -extract id raw -o - "$out" 2>/dev/null || true)"
      if [[ -z "$id" ]]; then
        id="$(sed -n 's/.*"id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$out" | head -1)"
      fi
      if [[ -n "$id" ]]; then
        rm -f "$out"
        printf '%s\n' "$id"
        return 0
      fi
    fi
    echo "    attempt $attempt failed:" >&2
    tail -3 "$out" | sed 's/^/      /' >&2
    rm -f "$out"
    if (( attempt < NOTARY_ATTEMPTS )); then
      sleep "$NOTARY_RETRY_DELAY"
    fi
    attempt=$(( attempt + 1 ))
  done
  return 1
}

wait_for_verdict() {
  local id=$1 deadline=$(( SECONDS + NOTARY_TIMEOUT )) status
  while :; do
    status="$(submit_status "$id")"
    state_put "$SHA" "$id" "$status"
    case "$status" in
      Accepted)
        echo "==> Notarization accepted ($id)" >&2
        return 0
        ;;
      Invalid|Rejected)
        echo "==> Notarization $status; Apple's log follows." >&2
        xcrun notarytool log "$id" "${auth_args[@]}" >&2 || true
        return 1
        ;;
      unknown)
        echo "    waiting for Apple to register $id" >&2
        ;;
      *)
        echo "    $status" >&2
        ;;
    esac
    if (( SECONDS >= deadline )); then
      echo "==> Still $status after ${NOTARY_TIMEOUT}s. The submission continues" >&2
      echo "    server-side; re-run packaging/notarize.sh $DMG to pick it up." >&2
      return 1
    fi
    sleep "$NOTARY_POLL_INTERVAL"
  done
}

# Stapling rewrites the disk image, so it only happens when the ticket is not
# already attached: re-running this on a released artifact must leave its
# SHA-256, and therefore the checksum on the download page, untouched.
staple() {
  if xcrun stapler validate "$DMG" >/dev/null 2>&1; then
    echo "==> Ticket already attached to $NAME"
  else
    echo "==> Stapling the ticket to $NAME"
    xcrun stapler staple "$DMG"
    xcrun stapler validate "$DMG"
  fi
  hdiutil verify "$DMG" >/dev/null
}

id="$(state_get id)"
if [[ -n "$id" && "$(state_get sha256)" != "$SHA" ]]; then
  echo "==> $NAME changed since the last submission; uploading again"
  id=""
fi

if [[ -n "$id" ]]; then
  status="$(submit_status "$id")"
  case "$status" in
    Accepted)
      echo "==> Already notarized ($id)"
      state_put "$SHA" "$id" "$status"
      staple
      exit 0
      ;;
    unknown)
      echo "==> Submission $id is unknown to Apple; uploading again"
      id=""
      ;;
    Invalid|Rejected)
      echo "==> Submission $id was $status; Apple's log follows." >&2
      xcrun notarytool log "$id" "${auth_args[@]}" >&2 || true
      exit 1
      ;;
    *)
      echo "==> Resuming submission $id ($status)"
      ;;
  esac
fi

if [[ -z "$id" ]]; then
  if ! id="$(upload)"; then
    echo "Notarization upload failed after $NOTARY_ATTEMPTS attempts." >&2
    exit 1
  fi
  state_put "$SHA" "$id" "In Progress"
fi

wait_for_verdict "$id"
staple
state_put "$SHA" "$id" "Accepted"
echo "==> $NAME is notarized and stapled"
