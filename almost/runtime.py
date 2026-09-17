from __future__ import annotations

import fcntl
import hashlib
import json
import os
import socket
import stat
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import AlmostError, Config, Profile, state_root


SSH_SOCKET_ID_LENGTH = 16


def private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise AlmostError(f"Unsafe state directory: {path}")
    if stat.S_IMODE(info.st_mode) != 0o700:
        path.chmod(0o700)
    return path


def socket_directory(scope: str) -> Path:
    # macOS's TMPDIR is too long for Unix sockets. Other platforms, including
    # Termux, need their writable temporary directory instead of a fixed /tmp.
    temporary = Path("/tmp") if sys.platform == "darwin" else Path(tempfile.gettempdir())
    limit = 104 if sys.platform == "darwin" else 108
    # OpenSSH appends a dot and 16 random characters while creating its socket.
    longest_name = f"ssh-{'0' * SSH_SOCKET_ID_LENGTH}.sock.{'0' * 16}"
    directory = temporary / f"almost-{os.getuid()}" / scope
    if len(os.fsencode(directory / longest_name)) >= limit:
        directory = temporary / f"a-{scope}"
    if len(os.fsencode(directory / longest_name)) >= limit:
        raise AlmostError(f"Temporary directory is too long for Unix sockets: {temporary}. "
                          "Set TMPDIR to a shorter writable absolute path.")
    if directory.parent != temporary:
        private_dir(directory.parent)
    return private_dir(directory)


def secure_open(path: Path, flags: int) -> int:
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
        os.close(fd)
        raise AlmostError(f"Unsafe state file: {path}")
    os.fchmod(fd, 0o600)
    return fd


def atomic_json(path: Path, value: Any) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class Lock:
    def __init__(self, path: Path):
        self.path = path
        self.fd: int | None = None

    def acquire(self, *, blocking: bool = False) -> bool:
        fd = secure_open(self.path, os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            os.close(fd)
            return False
        self.fd = fd
        return True

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self) -> Lock:
        if not self.acquire():
            raise AlmostError(f"Another process holds {self.path.name}.")
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


@dataclass(frozen=True)
class Runtime:
    scope: str
    directory: Path
    data: Path

    @classmethod
    def for_profile(cls, config: Config, profile: Profile) -> Runtime:
        root = private_dir(state_root())
        identity_lock = Lock(root / "identity.lock")
        identity_lock.acquire(blocking=True)
        try:
            identity_path = root / "identity"
            fd = secure_open(identity_path, os.O_CREAT | os.O_RDWR)
            with os.fdopen(fd, "r+") as stream:
                identity = stream.read().strip()
                if not identity:
                    identity = uuid.uuid4().hex
                    stream.write(identity + "\n")
                if not re_identity(identity):
                    raise AlmostError(f"Invalid installation identity: {identity_path}")
        finally:
            identity_lock.close()
        scope = hashlib.sha256(f"{identity}\0{config.path}\0{profile.name}".encode()).hexdigest()[:24]
        return cls(scope, socket_directory(scope), private_dir(root / scope))

    @property
    def socket(self) -> Path:
        return self.directory / "control.sock"

    def new_ssh_socket(self) -> Path:
        return self.directory / f"ssh-{uuid.uuid4().hex[:SSH_SOCKET_ID_LENGTH]}.sock"

    @property
    def log(self) -> Path:
        return self.data / "almost.log"

    @property
    def state(self) -> Path:
        return self.data / "status.json"


def re_identity(value: str) -> bool:
    return len(value) == 32 and all(c in "0123456789abcdef" for c in value)


def request(runtime: Runtime, command: str = "status", timeout: float = 1.0) -> dict[str, Any] | None:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(str(runtime.socket))
            client.sendall(json.dumps({"command": command}).encode() + b"\n")
            data = bytearray()
            while b"\n" not in data:
                chunk = client.recv(4096)
                if not chunk:
                    return None
                data.extend(chunk)
                if len(data) > 65536:
                    return None
            value = json.loads(data.split(b"\n", 1)[0])
            return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def previous_state(runtime: Runtime) -> dict[str, Any]:
    try:
        value = json.loads(runtime.state.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def wait_stopped(runtime: Runtime, generation: str, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = request(runtime, timeout=0.3)
        if status is not None and status.get("generation") != generation:
            return True
        if status is None:
            lock = Lock(runtime.directory / "supervisor.lock")
            if lock.acquire():
                lock.close()
                return True
        time.sleep(0.1)
    return False
