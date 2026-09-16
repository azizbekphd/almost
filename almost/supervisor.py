from __future__ import annotations

import collections
import hashlib
import json
import logging
import logging.handlers
import os
import queue
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from . import ssh
from .config import AlmostError, Config, Profile
from .runtime import Lock, Runtime, atomic_json, request, secure_open


def fingerprint(profile: Profile) -> str:
    return hashlib.sha256(repr(profile).encode()).hexdigest()


def cleanup_masters(profile: Profile, runtime: Runtime) -> None:
    """Called only while holding the supervisor lock, including crash recovery."""
    for path in runtime.directory.glob("ssh-*.sock"):
        info = path.lstat()
        if stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid():
            try:
                result = ssh.control(profile, path, "exit")
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise AlmostError(f"Could not stop an old app-owned SSH master at {path}; try 'almost stop' again.") from exc
            if result.returncode:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(0.3)
                    try:
                        probe.connect(str(path))
                    except (ConnectionRefusedError, FileNotFoundError):
                        pass
                    else:
                        raise AlmostError(f"An old app-owned SSH master is still responding at {path}; try 'almost stop' again.")
            path.unlink(missing_ok=True)


def ensure(config: Config, profile: Profile, runtime: Runtime) -> dict[str, Any]:
    status = request(runtime)
    if status is not None:
        if status.get("fingerprint") != fingerprint(profile):
            raise AlmostError("This profile's configuration changed. Run 'almost stop' and then 'almost up' to apply it.")
        if status.get("phase") == "stopping":
            raise AlmostError("This profile is stopping. Run the command again after it stops.")
        return request(runtime, "resume") or status
    ssh.executable()
    fd = secure_open(runtime.data / "bootstrap.log", os.O_CREAT | os.O_WRONLY | os.O_APPEND)
    env = dict(os.environ)
    package_root = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = package_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    try:
        child = subprocess.Popen(
            [sys.executable, "-m", "almost", "--config", str(config.path), "_serve", profile.name],
            stdin=subprocess.DEVNULL, stdout=fd, stderr=fd, start_new_session=True,
            close_fds=True, env=env,
        )
    finally:
        os.close(fd)
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        status = request(runtime, timeout=0.3)
        if status is not None:
            return status
        if child.poll() not in (None, 0):
            break
        time.sleep(0.1)
    raise AlmostError(f"The supervisor did not start. Inspect {runtime.data / 'bootstrap.log'}.")


class Supervisor:
    def __init__(self, config: Config, profile: Profile, runtime: Runtime):
        self.config, self.profile, self.runtime = config, profile, runtime
        self.stopping = threading.Event()
        self.backoff = ssh.Backoff()
        self.child: subprocess.Popen | None = None
        self.reader: threading.Thread | None = None
        self.lines: queue.Queue[str] = queue.Queue()
        self.diagnostics: collections.deque[str] = collections.deque(maxlen=40)
        self.master: Path | None = None
        self.next_attempt = 0.0
        self.next_check = 0.0
        self.connected_at = 0.0
        self.logger = logging.getLogger(f"almost.{runtime.scope}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        fd = secure_open(runtime.log, os.O_CREAT | os.O_WRONLY | os.O_APPEND)
        os.close(fd)
        handler = logging.handlers.RotatingFileHandler(runtime.log, maxBytes=1_000_000, backupCount=3)
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        self.logger.addHandler(handler)
        self.handler = handler
        self.state: dict[str, Any] = {
            "profile": profile.name, "host": profile.host, "running": True,
            "generation": uuid.uuid4().hex, "pid": os.getpid(), "phase": "starting",
            "fingerprint": fingerprint(profile), "forwards": [f.spec for f in profile.forwards],
            "attempts": 0, "connected_since": None, "retry_at": None,
            "last_error": None, "help": None,
        }

    def update(self, phase: str, **values: Any) -> None:
        changed = self.state["phase"] != phase
        self.state.update(phase=phase, updated_at=time.time(), **values)
        atomic_json(self.runtime.state, self.state)
        if changed:
            self.logger.info("%s: %s", phase, self.state.get("last_error") or self.profile.host)

    def read_stderr(self, child: subprocess.Popen) -> None:
        assert child.stderr is not None
        try:
            for line in iter(child.stderr.readline, b""):
                self.lines.put(line.decode("utf-8", "replace").rstrip()[:8192])
        finally:
            child.stderr.close()

    def drain(self) -> None:
        while True:
            try:
                line = self.lines.get_nowait()
            except queue.Empty:
                return
            self.diagnostics.append(line)
            self.logger.info("ssh: %s", line)

    def stop_child(self) -> None:
        if self.child is not None:
            ssh.terminate(self.child)
            if self.reader:
                self.reader.join(timeout=1)
            self.drain()
            self.child = None
        if self.master is not None:
            self.master.unlink(missing_ok=True)
            self.master = None

    def start_child(self) -> None:
        self.diagnostics.clear()
        self.master = self.runtime.directory / f"ssh-{uuid.uuid4().hex}.sock"
        self.state["attempts"] += 1
        self.update("connecting", connected_since=None, retry_at=None)
        args = ssh.base(self.profile, master=self.master) + ["-N", self.profile.host]
        try:
            self.child = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.PIPE, env=ssh.environment(), close_fds=True)
        except OSError as exc:
            self.update("blocked", last_error=str(exc), help="Ensure OpenSSH is installed and available, then run 'almost up'.")
            self.master = None
            return
        self.reader = threading.Thread(target=self.read_stderr, args=(self.child,), daemon=True)
        self.reader.start()
        self.next_check = time.monotonic() + 0.1

    def retry(self, detail: str) -> None:
        help_text = ssh.failure(detail)
        if help_text:
            self.update("blocked", last_error=detail[-4096:], help=help_text,
                        connected_since=None, retry_at=None)
        else:
            delay = self.backoff.next()
            self.next_attempt = time.monotonic() + delay
            self.update("retrying", last_error=detail[-4096:], help=None,
                        connected_since=None, retry_at=time.time() + delay)

    def tick(self) -> None:
        self.drain()
        now = time.monotonic()
        if not self.profile.forwards:
            if self.state["phase"] != "connected":
                self.update("connected", connected_since=time.time(), last_error=None, help=None)
            return
        if self.child is None:
            if self.state["phase"] != "blocked" and now >= self.next_attempt:
                self.start_child()
            return
        code = self.child.poll()
        if code is not None:
            stable = self.state["phase"] == "connected" and now - self.connected_at >= 30
            self.stop_child()
            if stable:
                self.backoff.reset()
            detail = "\n".join(self.diagnostics) or f"SSH tunnel connection ended (status {code})."
            self.retry(detail)
            return
        if self.state["phase"] != "connecting" or now < self.next_check:
            return
        self.next_check = now + 0.3
        if self.master is None or not self.master.exists():
            return
        try:
            ready = ssh.control(self.profile, self.master, "check")
            if ready.returncode != 0:
                return
            result = ssh.control(self.profile, self.master, "forward")
        except subprocess.TimeoutExpired:
            self.stop_child()
            self.retry("Timed out while initializing SSH forwarding.")
            return
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip() or "SSH port forwarding failed."
            self.stop_child()
            self.retry(detail)
            return
        self.connected_at = time.monotonic()
        self.update("connected", connected_since=time.time(), last_error=None, help=None, retry_at=None)

    def handle(self, connection: socket.socket) -> None:
        with connection:
            connection.settimeout(0.5)
            try:
                message = bytearray()
                while b"\n" not in message:
                    chunk = connection.recv(4096)
                    if not chunk or len(message) + len(chunk) > 8192:
                        return
                    message.extend(chunk)
                command = json.loads(message.split(b"\n", 1)[0]).get("command")
                if command == "stop":
                    self.stopping.set()
                    self.update("stopping")
                elif command == "resume" and self.state["phase"] in {"blocked", "retrying"}:
                    self.next_attempt = 0
                    self.backoff.reset()
                    self.update("starting", last_error=None, help=None, retry_at=None)
                elif command not in {"status", "resume"}:
                    connection.sendall(b'{"error":"Unknown command"}\n')
                    return
                connection.sendall(json.dumps(self.state).encode() + b"\n")
            except (OSError, ValueError, AttributeError):
                return

    def run(self) -> int:
        os.umask(0o077)
        lock = Lock(self.runtime.directory / "supervisor.lock")
        if not lock.acquire():
            self.handler.close()
            self.logger.removeHandler(self.handler)
            return 0
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old_handlers = {}
        try:
            cleanup_masters(self.profile, self.runtime)
            self.runtime.socket.unlink(missing_ok=True)
            listener.bind(str(self.runtime.socket))
            listener.listen(16)
            listener.settimeout(0.15)
            for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                old_handlers[sig] = signal.signal(sig, lambda *_: self.stopping.set())
            self.update("starting")
            while not self.stopping.is_set():
                try:
                    connection, _ = listener.accept()
                except socket.timeout:
                    pass
                else:
                    self.handle(connection)
                if not self.stopping.is_set():
                    self.tick()
            return 0
        finally:
            self.stopping.set()
            self.stop_child()
            self.update("stopped", running=False, retry_at=None, connected_since=None)
            listener.close()
            self.runtime.socket.unlink(missing_ok=True)
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)
            lock.close()
            self.handler.close()
            self.logger.removeHandler(self.handler)
