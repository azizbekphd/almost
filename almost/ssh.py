from __future__ import annotations

import os
import random
import shutil
import subprocess
from pathlib import Path

from .config import AlmostError, Profile


def executable() -> str:
    path = shutil.which("ssh")
    if path is None:
        raise AlmostError("OpenSSH is not installed or 'ssh' is missing from PATH.")
    return path


def base(profile: Profile, *, tty: bool = False, master: Path | None = None) -> list[str]:
    options = {
        "BatchMode": "yes",
        "StrictHostKeyChecking": "yes",
        "ServerAliveInterval": "5",
        "ServerAliveCountMax": "3",
        "ConnectTimeout": "5",
        "ConnectionAttempts": "1",
        "ControlMaster": "yes" if master else "no",
        "ControlPath": str(master) if master else "none",
        "ControlPersist": "no",
        "ForkAfterAuthentication": "no",
        "StdinNull": "no",
        "RemoteCommand": "none",
        # Keep forwarding declarations in ~/.ssh/config out of managed processes.
        # The supervisor installs only its own forwards through its private master.
        "ClearAllForwardings": "yes",
        "ExitOnForwardFailure": "yes",
    }
    args = [executable(), "-tt" if tty else "-T"]
    for key, value in options.items():
        args.extend(["-o", f"{key}={value}"])
    return args


def environment() -> dict[str, str]:
    # Preserve the terminal's UTF-8 locale. Many SSH configurations forward LC_*
    # variables, so forcing LC_ALL=C here would also change remote tmux rendering.
    return dict(os.environ)


def control(profile: Profile, socket: Path, operation: str, *, timeout: float = 2.0) -> subprocess.CompletedProcess[str]:
    # A control operation never opens a replacement network connection. Ignoring
    # config here avoids importing forwards a second time from the host alias.
    args = [executable(), "-F", "/dev/null", "-S", str(socket), "-O", operation]
    if operation == "forward":
        args.extend(["-o", "ExitOnForwardFailure=yes"])
        for forward in profile.forwards:
            args.extend(["-L", forward.spec])
    args.append(profile.host)
    return subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          timeout=timeout, env=environment())


def failure(diagnostic: str) -> str | None:
    message = diagnostic.lower()
    categories = [
        (("permission denied", "authentication failed", "no supported authentication", "sign_and_send_pubkey", "agent refused operation", "too many authentication failures"),
         "SSH authentication failed. Unlock/load your key and verify 'ssh HOST', then run 'almost up' again."),
        (("host key verification failed", "remote host identification has changed", "no ed25519 host key is known", "no ecdsa host key is known", "no rsa host key is known", "offending "),
         "SSH could not verify the host key. Verify the server using ordinary SSH, then run 'almost up' again."),
        (("address already in use", "cannot listen to port", "cannot bind", "forwarding failed", "forward request failed", "port forwarding failed", "administratively prohibited"),
         "A port forward could not be established. Free the occupied local port or correct the forwarding configuration, then run 'almost up' again."),
        (("bad configuration option", "bad configuration options", "bad owner or permissions", "bad permissions", "invalid format", "bad local forwarding", "bad remote forwarding", "invalid command", "unsupported option", "no matching host key type", "no matching key exchange", "no matching cipher", "cannot stat", "could not open user", "bad stdio forwarding"),
         "SSH configuration or key settings need attention. Run 'almost doctor' and correct the reported error."),
    ]
    for patterns, explanation in categories:
        if any(pattern in message for pattern in patterns):
            return explanation
    return None


class Backoff:
    def __init__(self) -> None:
        self.failures = 0

    def reset(self) -> None:
        self.failures = 0

    def next(self) -> float:
        nominal = min(10.0, 2.0 ** min(self.failures, 4))
        self.failures += 1
        return min(10.0, nominal * random.uniform(0.8, 1.2))


def terminate(child: subprocess.Popen, timeout: float = 2.0) -> None:
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
