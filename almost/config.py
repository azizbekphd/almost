from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path


class AlmostError(Exception):
    """An actionable error suitable for display without a traceback."""


EXAMPLE = '''# SSH authentication and host settings come from ~/.ssh/config.
version = 1
default_profile = "work"

[profiles.work]
host = "my-server" # Replace with your SSH alias or user@hostname.
# Local port : destination host (as seen from the server) : destination port.
# Local listeners always bind to 127.0.0.1.
forwards = ["3000:localhost:3000", "8080:localhost:8080"]

# Additional profiles can use other SSH aliases:
# [profiles.staging]
# host = "staging-server"
# forwards = ["9000:localhost:9000"]
# tmux_socket = "development" # Optional: equivalent to tmux -L development.
'''


def config_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "almost/config.toml"


def state_root() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "almost"


@dataclass(frozen=True)
class Forward:
    local_port: int
    host: str
    remote_port: int

    @classmethod
    def parse(cls, value: object) -> Forward:
        if not isinstance(value, str):
            raise AlmostError("Each forward must be a string such as '3000:localhost:3000'.")
        match = re.fullmatch(r"([0-9]+):(\[[0-9a-fA-F:.]+\]|[A-Za-z0-9_.-]+):([0-9]+)", value)
        if not match:
            raise AlmostError(f"Invalid forward {value!r}; use local_port:host:remote_port.")
        local, host, remote = match.groups()
        if not 1 <= int(local) <= 65535 or not 1 <= int(remote) <= 65535:
            raise AlmostError(f"Ports must be between 1 and 65535: {value!r}.")
        return cls(int(local), host, int(remote))

    @property
    def spec(self) -> str:
        return f"127.0.0.1:{self.local_port}:{self.host}:{self.remote_port}"


@dataclass(frozen=True)
class Profile:
    name: str
    host: str
    forwards: tuple[Forward, ...]
    tmux_socket: str | None = None


@dataclass(frozen=True)
class Config:
    path: Path
    default_profile: str
    profiles: dict[str, Profile]

    def profile(self, name: str | None) -> Profile:
        key = name or self.default_profile
        try:
            return self.profiles[key]
        except KeyError:
            raise AlmostError(f"Unknown profile {key!r}. Available: {', '.join(self.profiles)}") from None


def load(path: Path) -> Config:
    path = path.expanduser().resolve()
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except FileNotFoundError:
        raise AlmostError(f"No configuration at {path}. Run 'almost init' first.") from None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise AlmostError(f"Cannot read {path}: {exc}") from exc
    unknown = set(data) - {"version", "default_profile", "profiles"}
    if unknown:
        raise AlmostError(f"Unknown configuration keys: {', '.join(sorted(unknown))}")
    if type(data.get("version")) is not int or data["version"] != 1:
        raise AlmostError("Configuration must specify version = 1.")
    raw_profiles = data.get("profiles")
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise AlmostError("Configure at least one [profiles.NAME] table.")
    profiles = {}
    for name, value in raw_profiles.items():
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name):
            raise AlmostError(f"Invalid profile name {name!r}.")
        if not isinstance(value, dict) or set(value) - {"host", "forwards", "tmux_socket"}:
            raise AlmostError(f"Profile {name!r} accepts only host, forwards, and tmux_socket.")
        host = value.get("host")
        if not isinstance(host, str) or not host or host.startswith("-") or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in host):
            raise AlmostError(f"Profile {name!r} needs a valid SSH host alias.")
        raw_forwards = value.get("forwards", [])
        if not isinstance(raw_forwards, list):
            raise AlmostError(f"Profile {name!r}: forwards must be an array.")
        forwards = tuple(Forward.parse(item) for item in raw_forwards)
        if len({f.local_port for f in forwards}) != len(forwards):
            raise AlmostError(f"Profile {name!r} forwards the same local port more than once.")
        socket = value.get("tmux_socket")
        if socket is not None and (not isinstance(socket, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", socket)):
            raise AlmostError(f"Profile {name!r}: tmux_socket must contain only letters, numbers, '_' or '-'.")
        profiles[name] = Profile(name, host, forwards, socket)
    default = data.get("default_profile")
    if not isinstance(default, str) or default not in profiles:
        raise AlmostError("default_profile must name a configured profile.")
    return Config(path, default, profiles)
