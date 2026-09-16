"""Small POSIX-shell programs; no installed helper or Python is needed remotely."""
from __future__ import annotations

import base64
import re
import shlex
from dataclasses import dataclass

from .config import AlmostError, Profile


@dataclass(frozen=True)
class Session:
    id: str
    name: str


@dataclass(frozen=True)
class Snapshot:
    sessions: tuple[Session, ...]
    remembered: str | None
    attempt: str | None
    version: str
    server: str | None = None


def tmux_args(profile: Profile) -> list[str]:
    return ["tmux"] + (["-L", profile.tmux_socket] if profile.tmux_socket else [])


def prelude(profile: Profile) -> str:
    command = shlex.join(tmux_args(profile))
    return f'''set -eu
unset TMUX
command -v tmux >/dev/null 2>&1 || {{ printf '%s\\n' 'almost: tmux is not installed on the server.' >&2; exit 69; }}
tm() {{ {command} "$@"; }}
'''


def key_prefix(scope: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{24}", scope):
        raise ValueError("Invalid remote namespace")
    return f"@almost-{scope}"


def shell_command(script: str) -> str:
    return "sh -c " + shlex.quote(script)


def snapshot_script(profile: Profile, scope: str) -> str:
    key = key_prefix(scope)
    return prelude(profile) + f'''
printf '%s\\n' ALMOST_SNAPSHOT_1
printf 'VERSION\\t%s\\n' "$(tm -V)"
ids=$(tm list-sessions -F '#{{session_id}}' 2>/dev/null) || ids=
if [ -n "$ids" ]; then
    printf 'SERVER\\t%s\\n' "$(tm display-message -p '#{{pid}}:#{{start_time}}')"
    printf 'LAST\\t%s\\n' "$(tm show-options -svq {key}-last 2>/dev/null || :)"
    printf 'ATTEMPT\\t%s\\n' "$(tm show-options -svq {key}-attempt 2>/dev/null || :)"
    for id in $ids; do
        # Encode names so tabs, newlines, quotes and terminal escapes cannot alter
        # the protocol or become commands. Selection always uses the numeric ID.
        name=$(tm display-message -p -t "$id" '#{{session_name}}' 2>/dev/null | base64 | tr -d '\\n')
        [ -n "$name" ] && printf 'SESSION\\t%s\\t%s\\n' "$id" "$name"
    done
fi
printf '%s\\n' ALMOST_END
'''


def parse_snapshot(output: str) -> Snapshot:
    lines = output.splitlines()
    try:
        start = lines.index("ALMOST_SNAPSHOT_1")
        end = lines.index("ALMOST_END", start + 1)
    except ValueError:
        raise AlmostError("The server returned an incomplete tmux response. Run 'almost doctor'.") from None
    sessions: list[Session] = []
    remembered = attempt = server = None
    version = "unknown"
    for line in lines[start + 1:end]:
        parts = line.split("\t")
        if len(parts) == 2 and parts[0] == "VERSION":
            version = parts[1]
        elif len(parts) == 2 and parts[0] == "SERVER" and re.fullmatch(r"[0-9]+:[0-9]+", parts[1]):
            server = parts[1]
        elif len(parts) == 2 and parts[0] == "LAST":
            remembered = parts[1] if re.fullmatch(r"\$[0-9]+", parts[1]) else None
        elif len(parts) == 2 and parts[0] == "ATTEMPT":
            attempt = parts[1] if re.fullmatch(r"[a-f0-9]{32}", parts[1]) else None
        elif len(parts) == 3 and parts[0] == "SESSION" and re.fullmatch(r"\$[0-9]+", parts[1]):
            try:
                name = base64.b64decode(parts[2], validate=True).decode("utf-8", "replace").removesuffix("\n")
            except ValueError:
                raise AlmostError("Invalid session name encoding in the server response.") from None
            sessions.append(Session(parts[1], name))
        else:
            raise AlmostError("Invalid tmux response from the server. Run 'almost doctor'.")
    match = re.search(r"tmux (\d+)\.(\d+)", version)
    if not match or tuple(map(int, match.groups())) < (3, 2):
        raise AlmostError(f"Remote tmux 3.2 or newer is required (found {version}).")
    return Snapshot(tuple(sessions), remembered, attempt, version, server)


def create_script(profile: Profile, name: str) -> str:
    if not name or len(name) > 120 or any(ord(c) < 32 or ord(c) == 127 or c in ".:" for c in name):
        raise AlmostError("Session names must be 1–120 characters without '.', ':', or control characters.")
    # The name is a shell argument, never a shell or tmux command fragment.
    return prelude(profile) + "tm new-session -dP -F '#{session_id}' -s " + shlex.quote(name) + "\n"


def cleanup_script(profile: Profile, scope: str, attempt: str) -> str:
    """Run from the local app, never from a tmux detach hook (3.2 compatibility)."""
    key = key_prefix(scope)
    if not re.fullmatch(r"[a-f0-9]{32}", attempt):
        raise ValueError("Invalid attachment generation")
    return prelude(profile) + f'''
index=$(tm show-options -svq {key}-index 2>/dev/null || :)
case "$index" in ''|*[!0-9]*) exit 0 ;; esac
commands='set-option -su {key}-client ; set-option -su {key}-tty ; set-option -su {key}-generation'
for event in client-attached client-session-changed; do
    existing=$(tm show-options -gHv "$event[$index]" 2>/dev/null || :)
    case "$existing" in *'{key}-'*) commands="$commands ; set-hook -gu $event[$index]" ;; esac
done
tm if-shell -F '#{{==:#{{{key}-generation}},{attempt}}}' "$commands"
'''


def attach_script(profile: Profile, scope: str, session_id: str, attempt: str, server: str | None = None) -> str:
    key = key_prefix(scope)
    if not re.fullmatch(r"\$[0-9]+", session_id) or not re.fullmatch(r"[a-f0-9]{32}", attempt):
        raise ValueError("Invalid attachment identity")
    if server is not None and not re.fullmatch(r"[0-9]+:[0-9]+", server):
        raise ValueError("Invalid server identity")
    server_check = (f'''[ "$(tm display-message -p '#{{pid}}:#{{start_time}}' 2>/dev/null)" = {shlex.quote(server)} ] || exit 44\n'''
                    if server is not None else "")
    candidate = 1_000_000 + int(scope[:7], 16)
    # After first attachment, require creation time as well as PID. If a killed
    # local app cannot clean up its hooks, a later unrelated client reusing that
    # PID must not inherit its session memory.
    identity_guard = f"#{{||:#{{==:#{{{key}-identity}},}},#{{==:#{{{key}-identity}},#{{client_pid}}:#{{client_created}}}}}}"
    remember = (
        f"if-shell -F '#{{==:#{{client_pid}},#{{{key}-client}}}}' {{ "
        f"if-shell -F '{identity_guard}' {{ "
        f"set-option -sF {key}-last '#{{session_id}}' ; "
        f"set-option -sF {key}-identity '#{{client_pid}}:#{{client_created}}' ; "
        f"set-option -sF {key}-tty '#{{client_name}}' ; "
        f"set-option -s {key}-attempt {attempt} }} }}"
    )
    return prelude(profile) + server_check + f'''
tm has-session -t {shlex.quote(session_id)} 2>/dev/null || exit 44
tm set-option -s {key}-generation {attempt}
previous=$(tm show-options -svq {key}-identity 2>/dev/null || :)
if [ -n "$previous" ]; then
    tm list-clients -F '#{{client_name}} #{{client_pid}}:#{{client_created}}' | while read -r client identity; do
        if [ "$identity" = "$previous" ]; then tm detach-client -t "$client" || :; fi
    done
fi
index=$(tm show-options -svq {key}-index 2>/dev/null || :)
case "$index" in ''|*[!0-9]*) index={candidate} ;; esac
tries=0
while :; do
    occupied=0
    for event in client-attached client-session-changed; do
        existing=$(tm show-options -gHv "$event[$index]" 2>/dev/null || :)
        case "$existing" in ''|*'{key}-'*) ;; *) occupied=1 ;; esac
    done
    [ "$occupied" -eq 0 ] && break
    index=$((index + 1)); tries=$((tries + 1))
    [ "$tries" -lt 100 ] || {{ printf '%s\\n' 'almost: unable to reserve tmux hooks.' >&2; exit 70; }}
done
cleanup() {{
    current=$(tm show-options -svq {key}-client 2>/dev/null || :)
    if [ "$current" = "$$" ]; then
        for event in client-attached client-session-changed; do
            existing=$(tm show-options -gHv "$event[$index]" 2>/dev/null || :)
            case "$existing" in *'{key}-'*) tm set-hook -gu "$event[$index]" 2>/dev/null || : ;; esac
        done
        tm set-option -su {key}-client 2>/dev/null || :
        tm set-option -su {key}-identity 2>/dev/null || :
        tm set-option -su {key}-tty 2>/dev/null || :
        tm set-option -su {key}-generation 2>/dev/null || :
    fi
}}
trap cleanup EXIT
tm set-option -s {key}-client "$$"
tm set-option -s {key}-index "$index"
tm set-option -s {key}-identity ''
tm set-hook -g "client-attached[$index]" {shlex.quote(remember)}
tm set-hook -g "client-session-changed[$index]" {shlex.quote(remember)}
exec {shlex.join(tmux_args(profile))} attach-session -t {shlex.quote(session_id)}
'''
