from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from . import __version__, remote, ssh, supervisor
from .config import AlmostError, Config, Profile, EXAMPLE, config_path, load
from .connect import connect, display
from .runtime import Lock, Runtime, atomic_json, previous_state, private_dir, request, secure_open, wait_stopped


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="almost", description="Reliable SSH tunnels and tmux reconnection. With no command, connect to your default profile.")
    result.add_argument("--config", type=Path, default=config_path(), help="configuration file")
    result.add_argument("--version", action="version", version=f"almost {__version__}")
    sub = result.add_subparsers(dest="command", metavar="{init,connect,up,status,logs,doctor,stop,update}")
    sub.add_parser("init", help="create your initial configuration")
    updater = sub.add_parser("update", help="install the latest published release")
    updater.add_argument("--source", type=Path, help="install from a local source directory instead of downloading")
    for command, help_text in [
        ("connect", "start tunnels and attach to remembered tmux session"),
        ("up", "start background tunnels"), ("status", "show forwarding state"),
        ("logs", "show recent supervisor diagnostics"), ("doctor", "check SSH, tmux, and local ports"),
        ("stop", "stop tunnels and reconnection, preserving tmux sessions"),
        ("_serve", argparse.SUPPRESS),
    ]:
        child = sub.add_parser(command, help=help_text)
        child.add_argument("profile", nargs="?")
        if command == "connect":
            child.add_argument("--session", help="explicit existing tmux session name")
        if command == "status":
            child.add_argument("--json", action="store_true", help="machine-readable status")
        if command == "logs":
            child.add_argument("--lines", type=int, default=60)
    sub._choices_actions = [action for action in sub._choices_actions if action.dest != "_serve"]
    return result


def init(path: Path) -> int:
    path = path.expanduser().resolve()
    if path.exists():
        print(f"Configuration already exists: {path}")
        return 0
    private_dir(path.parent)
    fd = secure_open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w") as stream:
        stream.write(EXAMPLE)
    print(f"Created {path}")
    print("Edit this file: replace 'my-server' with your SSH host and choose your forwards.")
    print("Then run 'almost doctor' to check the setup, followed by 'almost' to connect.")
    return 0


def status_text(status: dict) -> None:
    print(f"{status.get('profile', 'Profile')}: {status.get('phase', 'stopped')}")
    if status.get("host"):
        print(f"  SSH host: {display(status['host'])}")
    for spec in status.get("forwards", []):
        print(f"  Forward: {display(spec)}")
    if not status.get("forwards") and status.get("running"):
        print("  No port forwards configured.")
    if status.get("retry_at"):
        print(f"  Next attempt in {max(0, status['retry_at'] - time.time()):.1f}s")
    if status.get("last_error"):
        print(f"  Last error: {display(status['last_error'])}")
    if status.get("help"):
        print(f"  {status['help']}")


def stop(profile: Profile, runtime: Runtime) -> int:
    status = request(runtime, "stop")
    if status is not None:
        if not wait_stopped(runtime, status["generation"], timeout=8):
            raise AlmostError("The supervisor is still stopping. Check 'almost status' or 'almost logs'.")
    else:
        lock = Lock(runtime.directory / "supervisor.lock")
        if not lock.acquire():
            raise AlmostError("The supervisor is running but did not respond. Inspect 'almost logs' and try again.")
        try:
            supervisor.cleanup_masters(profile, runtime)
            previous = previous_state(runtime)
            previous.update(profile=profile.name, host=profile.host, running=False, phase="stopped",
                            retry_at=None, connected_since=None)
            atomic_json(runtime.state, previous)
        finally:
            lock.close()
    print(f"Stopped {profile.name}. Remote tmux sessions are still running.")
    return 0


def doctor(profile: Profile, runtime: Runtime) -> int:
    print(f"Profile: {profile.name} → {display(profile.host)}")
    print(f"OpenSSH: {ssh.executable()}")
    print(f"Local state: {runtime.data}")
    status = request(runtime)
    problem = False
    for forward in profile.forwards:
        if status and status.get("phase") == "connected" and forward.spec in status.get("forwards", []):
            print(f"Port {forward.local_port}: managed by almost")
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", forward.local_port))
            except OSError as exc:
                print(f"Port {forward.local_port}: unavailable ({exc.strerror}). Stop its existing forwarding command or select another local port.")
                problem = True
            else:
                print(f"Port {forward.local_port}: available")
    print("Checking SSH authentication and remote tmux…", flush=True)
    try:
        check = subprocess.run(ssh.base(profile) + [profile.host, remote.shell_command(remote.snapshot_script(profile, runtime.scope))],
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, env=ssh.environment(), timeout=20)
    except subprocess.TimeoutExpired:
        print("SSH check timed out. Verify network access to the host.")
        return 1
    if check.returncode:
        print(display(check.stderr.strip()) or f"Remote check exited with status {check.returncode}.")
        explanation = ssh.failure(check.stderr)
        if explanation:
            print(explanation)
        return 1
    snapshot = remote.parse_snapshot(check.stdout)
    print(f"SSH authentication: OK; remote {snapshot.version}")
    if snapshot.sessions:
        print("Sessions: " + ", ".join(display(s.name) for s in snapshot.sessions))
    else:
        print("No tmux sessions exist; connecting will offer to create one.")
    print("Forwarding status describes SSH listeners; a remote application may still be stopped.")
    return 1 if problem else 0


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    args = parser().parse_args(argv)
    try:
        if args.command == "init":
            return init(args.config)
        if args.command == "update":
            from .update import update
            return update(args.source)
        config = load(args.config)
        profile = config.profile(getattr(args, "profile", None))
        runtime = Runtime.for_profile(config, profile)
        command = args.command or "connect"
        if command == "_serve":
            return supervisor.Supervisor(config, profile, runtime).run()
        if command == "connect":
            return connect(config, profile, runtime, getattr(args, "session", None))
        if command == "up":
            status = supervisor.ensure(config, profile, runtime)
            deadline = time.monotonic() + 7
            while status.get("phase") in {"starting", "connecting"} and time.monotonic() < deadline:
                time.sleep(0.15)
                status = request(runtime) or status
            status_text(status)
            return 1 if status.get("phase") == "blocked" else 0
        if command == "status":
            status = request(runtime)
            if status is None:
                status = previous_state(runtime)
                status.update(profile=profile.name, host=profile.host, running=False, phase="stopped", retry_at=None, connected_since=None)
            if args.json:
                print(json.dumps(status, indent=2))
            else:
                status_text(status)
            return 0
        if command == "stop":
            return stop(profile, runtime)
        if command == "doctor":
            return doctor(profile, runtime)
        if command == "logs":
            if not 1 <= args.lines <= 10000:
                raise AlmostError("--lines must be between 1 and 10000.")
            try:
                content = runtime.log.read_text(errors="replace").splitlines()
            except FileNotFoundError:
                print("No supervisor logs yet.")
            else:
                for line in content[-args.lines:]:
                    print(display(line))
            return 0
        return 0
    except AlmostError as exc:
        print(f"almost: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled. Existing background tunnels can be stopped with 'almost stop'.", file=sys.stderr)
        return 130
    except (OSError, ValueError) as exc:
        print(f"almost: {exc}", file=sys.stderr)
        return 1
