from __future__ import annotations

import os
import re
import select
import signal
import subprocess
import sys
import termios
import threading
import time
import uuid
from dataclasses import dataclass

from . import remote, ssh, supervisor
from .config import AlmostError, Config, Profile
from .runtime import Lock, Runtime, request


def display(value: str) -> str:
    return "".join(c if c.isprintable() else c.encode("unicode_escape").decode() for c in value)


class Cancelled(Exception):
    pass


class Watch:
    def __init__(self, runtime: Runtime, generation: str):
        self.runtime, self.generation = runtime, generation
        self.cancelled = threading.Event()
        self.done = threading.Event()
        self.reason = "Connection stopped."
        self.attempt: str | None = None
        self.thread = threading.Thread(target=self.monitor, daemon=True)
        self.handlers: dict[int, object] = {}

    def monitor(self) -> None:
        last_seen = time.monotonic()
        while not self.done.is_set():
            status = request(self.runtime, timeout=0.5)
            if status is not None:
                if status.get("generation") != self.generation or status.get("phase") in {"stopping", "stopped"}:
                    self.reason = "Connection stopped."
                    self.cancelled.set()
                    return
                last_seen = time.monotonic()
            elif time.monotonic() - last_seen > 6:
                self.reason = "The supervisor stopped. Run 'almost' to reconnect."
                self.cancelled.set()
                return
            self.done.wait(0.3)

    def __enter__(self) -> Watch:
        for sig in (signal.SIGTERM, signal.SIGHUP):
            self.handlers[sig] = signal.signal(sig, self.on_signal)
        self.thread.start()
        return self

    def on_signal(self, number: int, _frame: object) -> None:
        self.cancelled.set()
        if number == signal.SIGHUP:
            # A closed terminal returns EIO on output. Redirect before Python's
            # final stream flush so a deliberate terminal close exits cleanly.
            fd = os.open(os.devnull, os.O_RDWR)
            try:
                os.dup2(fd, 1)
                os.dup2(fd, 2)
            finally:
                os.close(fd)

    def __exit__(self, *_: object) -> None:
        self.done.set()
        self.thread.join(timeout=1)
        for sig, handler in self.handlers.items():
            signal.signal(sig, handler)

    def check(self) -> None:
        if self.cancelled.is_set():
            raise Cancelled(self.reason)

    def sleep(self, seconds: float) -> None:
        self.cancelled.wait(seconds)
        self.check()


@dataclass(frozen=True)
class Result:
    code: int
    stdout: str
    stderr: str
    duration: float


def run(profile: Profile, script: str, watch: Watch, *, interactive: bool = False) -> Result:
    watch.check()
    args = ssh.base(profile, tty=interactive) + [profile.host, remote.shell_command(script)]
    attributes = None
    if interactive and sys.stdin.isatty():
        attributes = termios.tcgetattr(sys.stdin.fileno())
        # Discard keystrokes typed while offline instead of delivering them later.
        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
    started = time.monotonic()
    child = subprocess.Popen(args, stdin=None if interactive else subprocess.DEVNULL,
                             stdout=None if interactive else subprocess.PIPE,
                             stderr=subprocess.PIPE, env=ssh.environment(), close_fds=True)
    diagnostics: list[bytes] = []
    reader: threading.Thread | None = None

    def stderr_reader() -> None:
        assert child.stderr is not None
        try:
            while chunk := os.read(child.stderr.fileno(), 4096):
                diagnostics.append(chunk)
                if len(diagnostics) > 16:
                    del diagnostics[0]
                try:
                    sys.stderr.buffer.write(chunk)
                    sys.stderr.buffer.flush()
                except (OSError, AttributeError):
                    pass
        finally:
            child.stderr.close()

    try:
        if interactive:
            reader = threading.Thread(target=stderr_reader, daemon=True)
            reader.start()
            while child.poll() is None:
                watch.check()
                time.sleep(0.1)
            reader.join(timeout=1)
            output, error = b"", b"".join(diagnostics)
        else:
            while True:
                watch.check()
                try:
                    output, error = child.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() - started > 20:
                        return Result(70, "", "Remote tmux command timed out. Check the server with 'almost doctor'.", time.monotonic() - started)
                    continue
        watch.check()
        return Result(child.returncode, output.decode("utf-8", "replace"),
                      error.decode("utf-8", "replace"), time.monotonic() - started)
    finally:
        ssh.terminate(child)
        if reader:
            reader.join(timeout=1)
        else:
            if child.stdout:
                child.stdout.close()
            if child.stderr:
                child.stderr.close()
        if attributes is not None:
            try:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, attributes)
            except (OSError, termios.error):
                pass
            if child.returncode != 0 or watch.cancelled.is_set():
                # A dropped SSH stream cannot deliver tmux's terminal teardown.
                # Restore ordinary screen/input modes before showing local prompts.
                reset = (b"\x1b[?1049l\x1b[?1l\x1b>\x1b[?25h\x1b[?1000l"
                         b"\x1b[?1002l\x1b[?1003l\x1b[?1005l\x1b[?1006l"
                         b"\x1b[?1004l\x1b[?2004l\x1b[?7727l\x1b[0m")
                try:
                    os.write(sys.stdout.fileno(), reset)
                except OSError:
                    pass


def prompt(message: str, watch: Watch) -> str:
    print(message, end="", flush=True)
    while True:
        watch.check()
        ready, _, _ = select.select([sys.stdin], [], [], 0.2)
        if ready:
            value = sys.stdin.readline()
            if not value:
                raise Cancelled("Disconnected. Tunnels continue in the background.")
            return value.strip()


def choose(snapshot: remote.Snapshot, watch: Watch) -> remote.Session | str:
    if snapshot.sessions:
        print("\nRemote tmux sessions:")
        for index, session in enumerate(snapshot.sessions, 1):
            print(f"  {index}. {display(session.name)}")
        while True:
            answer = prompt("Session number (q to cancel): ", watch)
            if answer.lower() == "q":
                raise Cancelled("Disconnected. Tunnels continue in the background.")
            if answer.isascii() and answer.isdigit() and 1 <= int(answer) <= len(snapshot.sessions):
                return snapshot.sessions[int(answer) - 1]
            print("Enter one of the listed session numbers.")
    print("\nNo remote tmux sessions remain.")
    while True:
        answer = prompt("Name for a new session (leave empty to cancel): ", watch)
        if not answer:
            raise Cancelled("Disconnected. Tunnels continue in the background.")
        try:
            remote.create_script(Profile("validation", "unused", ()), answer)
        except AlmostError as exc:
            print(str(exc))
        else:
            return answer


def retry(result: Result, backoff: ssh.Backoff, watch: Watch) -> None:
    explanation = ssh.failure(result.stderr)
    if explanation:
        raise AlmostError(explanation + ("\n" + display(result.stderr.strip()) if result.stderr.strip() else ""))
    if result.code != 255 and result.code >= 0:
        raise AlmostError(display(result.stderr.strip()) or f"The remote command exited with status {result.code}.")
    delay = backoff.next()
    print(f"Connection interrupted. Retrying in {delay:.1f}s (Ctrl-C to stop).", file=sys.stderr)
    watch.sleep(delay)


def connect(config: Config, profile: Profile, runtime: Runtime, requested: str | None = None) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise AlmostError("'almost connect' needs an interactive terminal. Use 'almost up' for background forwarding.")
    lock = Lock(runtime.directory / "frontend.lock")
    if not lock.acquire():
        raise AlmostError(f"An interactive connection for {profile.name!r} is already open. Use that terminal or detach it first.")
    try:
        status = supervisor.ensure(config, profile, runtime)
        with Watch(runtime, status["generation"]) as watch:
            try:
                return connection_loop(profile, runtime, watch, requested)
            except Cancelled as exc:
                print(str(exc))
                return 0
            finally:
                if watch.attempt:
                    # Best effort when offline; the next attachment also replaces
                    # the old hooks. Never execute cleanup from a tmux detach hook.
                    try:
                        subprocess.run(ssh.base(profile) + [profile.host, remote.shell_command(remote.cleanup_script(profile, runtime.scope, watch.attempt))],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       env=ssh.environment(), timeout=3)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
    finally:
        lock.close()


def connection_loop(profile: Profile, runtime: Runtime, watch: Watch, requested: str | None) -> int:
    backoff = ssh.Backoff()
    selected: remote.Session | None = None
    last_attempt: str | None = None
    server_marker: str | None = None
    abnormal_exit = False
    while True:
        response = run(profile, remote.snapshot_script(profile, runtime.scope), watch)
        if response.code:
            retry(response, backoff, watch)
            continue
        snapshot = remote.parse_snapshot(response.stdout)
        if server_marker is not None and snapshot.server != server_marker:
            selected = None
            requested = None
            last_attempt = None
            abnormal_exit = False
        server_marker = snapshot.server
        by_id = {session.id: session for session in snapshot.sessions}
        # Only clear a pending explicit selection after a hook proves the prior
        # attempt really attached. A failed handshake must not lose that choice.
        if last_attempt is not None and snapshot.attempt == last_attempt:
            selected = None
            requested = None
        if abnormal_exit and snapshot.remembered in by_id:
            raise AlmostError("tmux exited unexpectedly. Run 'almost doctor' and check the terminal message above.")
        abnormal_exit = False
        if selected is not None:
            selected = by_id.get(selected.id)
        if requested is not None:
            selected = next((s for s in snapshot.sessions if s.name == requested), None)
            if selected is None:
                raise AlmostError(f"No tmux session named {requested!r}. Run 'almost' to choose an existing session.")
            requested = None
        elif selected is None:
            selected = by_id.get(snapshot.remembered)
        if selected is None:
            choice = choose(snapshot, watch)
            if isinstance(choice, str):
                created = run(profile, remote.create_script(profile, choice), watch)
                if created.code:
                    # Creation may have succeeded before SSH dropped. Query by
                    # the requested name on retry, never blindly create it twice.
                    if created.code == 255 and not ssh.failure(created.stderr):
                        retry(created, backoff, watch)
                        check = run(profile, remote.snapshot_script(profile, runtime.scope), watch)
                        if check.code == 0:
                            after = remote.parse_snapshot(check.stdout)
                            selected = next((s for s in after.sessions if s.name == choice), None)
                        if selected is None:
                            continue
                    else:
                        raise AlmostError(display(created.stderr.strip()) or "Could not create the tmux session.")
                else:
                    ids = re.findall(r"^\$[0-9]+$", created.stdout, re.MULTILINE)
                    if len(ids) != 1:
                        raise AlmostError("The server did not return the new tmux session ID.")
                    selected = remote.Session(ids[0], choice)
            else:
                selected = choice
        assert selected is not None
        last_attempt = uuid.uuid4().hex
        watch.attempt = last_attempt
        attached = run(profile, remote.attach_script(profile, runtime.scope, selected.id, last_attempt, server_marker),
                       watch, interactive=True)
        if attached.code == 0:
            print("Detached. Tunnels continue in the background; use 'almost stop' to stop them.")
            return 0
        if attached.duration >= 30:
            backoff.reset()
        if attached.code == 44:
            selected = None
            requested = None
            continue
        if attached.code == 1:
            abnormal_exit = True
            continue
        retry(attached, backoff, watch)
