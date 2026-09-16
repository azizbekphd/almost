import os
import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

from almost.config import Profile
from almost.remote import attach_script, snapshot_script, parse_snapshot, create_script, cleanup_script
from tests.support import Terminal, wait_for


@unittest.skipUnless(os.environ.get("ALMOST_REAL_TMUX") == "1" and shutil.which("tmux"), "set ALMOST_REAL_TMUX=1 for isolated local tmux tests")
class TmuxTests(unittest.TestCase):
    def setUp(self):
        self.socket = "almost-test-" + uuid.uuid4().hex[:12]
        self.scope = uuid.uuid4().hex[:24]
        self.profile = Profile("test", "unused", (), self.socket)
        self.terminals = []
        self.tm("-f", "/dev/null", "new-session", "-d", "-s", "alpha", "sh")
        self.tm("new-session", "-d", "-s", "beta", "sh")

    def tearDown(self):
        for terminal in self.terminals:
            terminal.close()
        subprocess.run(["tmux", "-L", self.socket, "kill-server"], capture_output=True)

    def tm(self, *args):
        return subprocess.run(["tmux", "-L", self.socket, *args], capture_output=True, text=True, check=True).stdout.strip()

    def snapshot(self):
        value = subprocess.run(["sh", "-c", snapshot_script(self.profile, self.scope)], capture_output=True, text=True, check=True)
        return parse_snapshot(value.stdout)

    def attach(self, session):
        attempt = uuid.uuid4().hex
        terminal = Terminal(["sh", "-c", attach_script(self.profile, self.scope, session, attempt)])
        self.terminals.append(terminal)
        terminal.wait_for(lambda: self.snapshot().attempt == attempt)
        return terminal

    def client(self):
        return self.tm("show-options", "-sv", f"@almost-{self.scope}-tty")

    def test_switch_rename_detach_and_hook_preservation(self):
        self.tm("set-hook", "-g", "client-attached[0]", "set-option -s @user-hook ran")
        self.tm("set-hook", "-g", "client-detached[0]", "set-option -s @user-detach ran")
        terminal = self.attach("$0")
        self.assertEqual(self.tm("show-options", "-sv", "@user-hook"), "ran")
        self.tm("switch-client", "-c", self.client(), "-t", "$1")
        self.tm("rename-session", "-t", "$1", "renamed")
        terminal.wait_for(lambda: self.snapshot().remembered == "$1")
        self.tm("detach-client", "-t", self.client())
        self.assertEqual(terminal.wait_exit(), 0)
        self.assertEqual(self.snapshot().remembered, "$1")
        subprocess.run(["sh", "-c", cleanup_script(self.profile, self.scope, self.snapshot().attempt)], check=True)
        wait_for(lambda: self.scope not in self.tm("show-hooks", "-g"))
        self.assertEqual(self.tm("show-options", "-sv", "@user-detach"), "ran")
        second = self.attach(self.snapshot().remembered)
        self.assertEqual(self.tm("list-clients", "-F", "#{session_id}"), "$1")
        self.tm("detach-client", "-t", self.client())
        self.assertEqual(second.wait_exit(), 0)
        self.assertEqual(len(self.snapshot().sessions), 2)

    def test_unrelated_client_does_not_change_saved_session(self):
        owned = self.attach("$0")
        own_name = self.client()
        outsider = Terminal(["tmux", "-L", self.socket, "attach-session", "-t", "$1"])
        self.terminals.append(outsider)
        outsider.wait_for(lambda: len(self.tm("list-clients", "-F", "#{client_name}").splitlines()) == 2)
        others = [line for line in self.tm("list-clients", "-F", "#{client_name}").splitlines() if line != own_name]
        self.tm("switch-client", "-c", others[0], "-t", "$0")
        self.tm("switch-client", "-c", others[0], "-t", "$1")
        self.assertEqual(self.snapshot().remembered, "$0")
        self.tm("detach-client", "-t", others[0])
        self.assertEqual(outsider.wait_exit(), 0)
        self.assertIn(self.scope, self.tm("show-hooks", "-g"))
        self.tm("detach-client", "-t", own_name)
        self.assertEqual(owned.wait_exit(), 0)

    def test_hook_collision_and_deleted_session(self):
        index = 1_000_000 + int(self.scope[:7], 16)
        self.tm("set-hook", "-g", f"client-attached[{index}]", "set-option -s @other-hook ran")
        terminal = self.attach("$0")
        self.assertEqual(self.tm("show-options", "-sv", f"@almost-{self.scope}-index"), str(index + 1))
        self.tm("detach-client", "-t", self.client())
        self.assertEqual(terminal.wait_exit(), 0)
        self.tm("kill-session", "-t", "$0")
        snapshot = self.snapshot()
        self.assertNotIn(snapshot.remembered, {s.id for s in snapshot.sessions})
        self.assertIn("@other-hook", self.tm("show-hooks", "-g"))

    def test_shell_metacharacters_in_names_are_literal(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "marker"
            name = "x'$(touch " + str(marker) + ")"
            result = subprocess.run(["sh", "-c", create_script(self.profile, name)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists())
            self.assertIn(name, [s.name for s in self.snapshot().sessions])

    def test_old_cleanup_cannot_remove_new_client_hooks(self):
        first = self.attach("$0")
        old_attempt = self.snapshot().attempt
        self.tm("detach-client", "-t", self.client())
        self.assertEqual(first.wait_exit(), 0)
        second = self.attach("$1")
        subprocess.run(["sh", "-c", cleanup_script(self.profile, self.scope, old_attempt)], check=True)
        self.assertIn(self.scope, self.tm("show-hooks", "-g"))
        self.tm("switch-client", "-c", self.client(), "-t", "$0")
        second.wait_for(lambda: self.snapshot().remembered == "$0")

    def test_server_restart_does_not_reuse_stale_session_id(self):
        terminal = self.attach("$0")
        self.tm("detach-client", "-t", self.client())
        self.assertEqual(terminal.wait_exit(), 0)
        self.assertEqual(self.snapshot().remembered, "$0")
        old_server = self.snapshot().server
        self.tm("kill-server")
        self.tm("-f", "/dev/null", "new-session", "-d", "-s", "unrelated", "sh")
        snapshot = self.snapshot()
        self.assertEqual(snapshot.sessions[0].id, "$0")
        self.assertIsNone(snapshot.remembered)
        result = subprocess.run(["sh", "-c", attach_script(self.profile, self.scope, "$0", uuid.uuid4().hex, old_server)], capture_output=True)
        self.assertEqual(result.returncode, 44)


if __name__ == "__main__":
    unittest.main()
