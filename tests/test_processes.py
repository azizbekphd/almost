"""Process-level failure tests, using a deterministic SSH executable fixture."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from almost.config import load
from almost.runtime import Runtime, request
from tests.support import Terminal, wait_for


FIXTURE = r'''
import os,sys,time
from pathlib import Path
root=Path(os.environ["ALMOST_TEST_FIXTURE"])
with (root/"attempts").open("a") as log: log.write(str(time.monotonic())+"\n")
mode=(root/"mode").read_text().strip()
if mode == "auth":
    print("Permission denied (publickey).",file=sys.stderr);sys.exit(255)
if mode == "hostkey":
    print("Host key verification failed.",file=sys.stderr);sys.exit(255)
if mode in ("empty", "chooser"):
    if "ALMOST_SNAPSHOT_1" in sys.argv[-1]:
        print("ALMOST_SNAPSHOT_1\nVERSION\ttmux 3.2a")
        if mode == "chooser":print("SESSION\t$0\tYWxwaGEK\nSESSION\t$1\tYmV0YQo=")
        print("ALMOST_END");sys.exit(0)
    if "attach-session" in sys.argv[-1] or "tm new-session" in sys.argv[-1]:
        with (root/"mutations").open("a") as log:log.write("command\n")
    sys.exit(0)
print("ssh: connect to host test port 22: Connection refused",file=sys.stderr)
sys.exit(255)
'''


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="almost-process-")
        self.root = Path(self.temporary.name)
        (self.root / "mode").write_text("offline")
        (self.root / "attempts").touch()
        binaries = self.root / "bin"
        binaries.mkdir()
        executable = binaries / "ssh"
        executable.write_text(f"#!{sys.executable}\n" + FIXTURE)
        executable.chmod(0o700)
        self.environment = {**os.environ, "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
                            "XDG_STATE_HOME": str(self.root / "state"), "ALMOST_TEST_FIXTURE": str(self.root)}
        self.env_patch = patch.dict(os.environ, self.environment)
        self.env_patch.start()
        self.path = self.root / "config.toml"
        self.path.write_text('version=1\ndefault_profile="test"\n[profiles.test]\nhost="test"\nforwards=["53999:localhost:53999"]\n')
        self.config = load(self.path)
        self.profile = self.config.profile(None)
        self.runtime = Runtime.for_profile(self.config, self.profile)
        self.terminals = []

    def tearDown(self):
        for terminal in self.terminals:
            terminal.close()
        self.cli("stop", check=False)
        shutil.rmtree(self.runtime.directory, ignore_errors=True)
        self.env_patch.stop()
        self.temporary.cleanup()

    def cli(self, *args, check=True):
        result = subprocess.run([sys.executable, "-m", "almost", "--config", str(self.path), *args],
                                env=self.environment, capture_output=True, text=True, timeout=15)
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def attempts(self):
        return [float(line) for line in (self.root / "attempts").read_text().splitlines()]

    def terminal(self):
        result = Terminal([sys.executable, "-m", "almost", "--config", str(self.path)], env=self.environment)
        self.terminals.append(result)
        return result

    def test_retry_backoff_pause_resume_and_stop(self):
        self.cli("up")
        wait_for(lambda: len(self.attempts()) >= 3, timeout=10)
        attempts = self.attempts()
        self.assertGreaterEqual(attempts[1] - attempts[0], 0.7)
        self.assertGreaterEqual(attempts[2] - attempts[1], 1.5)
        (self.root / "mode").write_text("auth")
        wait_for(lambda: (request(self.runtime) or {}).get("phase") == "blocked", timeout=12)
        count = len(self.attempts())
        time.sleep(1.3)
        self.assertEqual(len(self.attempts()), count)
        (self.root / "mode").write_text("offline")
        self.cli("up")
        self.assertGreater(len(self.attempts()), count)
        before = time.monotonic()
        self.cli("stop")
        self.assertLess(time.monotonic() - before, 3)
        count = len(self.attempts())
        time.sleep(1.1)
        self.assertEqual(len(self.attempts()), count)
        self.assertIsNone(request(self.runtime))

    def test_host_key_failure_does_not_loop(self):
        (self.root / "mode").write_text("hostkey")
        self.assertEqual(self.cli("up", check=False).returncode, 1)
        self.assertEqual(request(self.runtime)["phase"], "blocked")
        count = len(self.attempts())
        time.sleep(1)
        self.assertEqual(len(self.attempts()), count)

    def test_stop_cancels_offline_terminal_and_prevents_duplicate_frontends(self):
        terminal = self.terminal()
        terminal.wait_for(lambda: "Retrying in" in terminal.text)
        duplicate = self.terminal()
        self.assertEqual(duplicate.wait_exit(), 1)
        self.assertIn("already open", duplicate.text)
        self.cli("stop")
        self.assertEqual(terminal.wait_exit(), 0)
        count = len(self.attempts())
        time.sleep(1)
        self.assertEqual(len(self.attempts()), count)

    def test_control_c_cancels_attachment_but_preserves_tunnels(self):
        terminal = self.terminal()
        terminal.wait_for(lambda: "Retrying in" in terminal.text)
        terminal.send(b"\x03")
        self.assertEqual(terminal.wait_exit(), 130)
        self.assertTrue(request(self.runtime)["running"])

    def test_empty_server_offers_creation_without_automatically_creating(self):
        self.path.write_text(self.path.read_text().replace('["53999:localhost:53999"]', '[]'))
        (self.root / "mode").write_text("empty")
        terminal = self.terminal()
        terminal.wait_for(lambda: "Name for a new session" in terminal.text)
        self.assertFalse((self.root / "mutations").exists())
        terminal.send(b"\n")
        self.assertEqual(terminal.wait_exit(), 0)
        self.assertFalse((self.root / "mutations").exists())

    def test_first_use_session_chooser(self):
        self.path.write_text(self.path.read_text().replace('["53999:localhost:53999"]', '[]'))
        (self.root / "mode").write_text("chooser")
        terminal = self.terminal()
        terminal.wait_for(lambda: "Session number" in terminal.text)
        self.assertIn("1. alpha", terminal.text)
        self.assertIn("2. beta", terminal.text)
        terminal.send(b"2\n")
        self.assertEqual(terminal.wait_exit(), 0)
        self.assertEqual((self.root / "mutations").read_text().count("command"), 1)


if __name__ == "__main__":
    unittest.main()
