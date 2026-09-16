"""Opt-in integration against an SSH host. Uses only an isolated tmux server."""
import http.client
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

from almost import remote, ssh
from almost.config import Profile, load
from almost.runtime import Runtime, request
from tests.support import Terminal, wait_for


@unittest.skipUnless(os.environ.get("ALMOST_SSH_HOST"), "set ALMOST_SSH_HOST for isolated real SSH integration")
class LiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="almost-live-")
        self.directory = Path(self.temporary.name)
        self.environment = {**os.environ, "XDG_STATE_HOME": str(self.directory / "state"), "XDG_CONFIG_HOME": str(self.directory / "config")}
        self.env_patch = patch.dict(os.environ, self.environment)
        self.env_patch.start()
        self.socket = "almost-test-" + uuid.uuid4().hex[:12]
        self.host = os.environ["ALMOST_SSH_HOST"]
        self.profile = Profile("test", self.host, (), self.socket)
        self.terminals = []
        self.runtime = None
        self.addCleanup(self.cleanup)
        self.tm("-f", "/dev/null", "new-session", "-d", "-s", "alpha", "sh")
        self.tm("new-session", "-d", "-s", "beta", "sh")
        code = '''from http.server import HTTPServer, BaseHTTPRequestHandler
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"almost integration")
    def log_message(self, *args): pass
server=HTTPServer(("127.0.0.1", 0), Handler)
print("ALMOST_PORT="+str(server.server_port), flush=True)
server.serve_forever()
'''
        self.tm("new-window", "-d", "-t", "alpha", "-n", "web", "python3 -u -c " + shlex.quote(code))
        def find_port():
            screen = self.tm("capture-pane", "-p", "-t", "alpha:web")
            match = re.search(r"ALMOST_PORT=(\d+)", screen)
            return int(match[1]) if match else None
        destination = wait_for(find_port)
        self.ports = []
        for _ in range(2):
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                self.ports.append(probe.getsockname()[1])
        self.path = self.directory / "config.toml"
        self.path.write_text('version=1\ndefault_profile="test"\n[profiles.test]\nhost=' + json.dumps(self.host) +
                             '\ntmux_socket=' + json.dumps(self.socket) + '\nforwards=' +
                             json.dumps([f"{port}:127.0.0.1:{destination}" for port in self.ports]) + '\n')
        self.config = load(self.path)
        self.profile = self.config.profile(None)
        self.runtime = Runtime.for_profile(self.config, self.profile)

    def cleanup(self):
        for terminal in self.terminals:
            terminal.close()
        if self.runtime is not None:
            self.cli("stop", check=False)
        subprocess.run(ssh.base(self.profile) + [self.host, shlex.join(["tmux", "-L", self.socket, "kill-server"])],
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=15)
        if self.runtime is not None:
            shutil.rmtree(self.runtime.directory, ignore_errors=True)
        self.env_patch.stop()
        self.temporary.cleanup()

    def tm(self, *args):
        result = subprocess.run(ssh.base(self.profile) + [self.host, shlex.join(["tmux", "-L", self.socket, *args])],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def cli(self, *args, check=True):
        result = subprocess.run([sys.executable, "-m", "almost", "--config", str(self.path), *args],
                                capture_output=True, text=True, env=self.environment, timeout=20)
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def snapshot(self):
        result = subprocess.run(ssh.base(self.profile) + [self.host, remote.shell_command(remote.snapshot_script(self.profile, self.runtime.scope))],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return remote.parse_snapshot(result.stdout)

    def client(self):
        return self.tm("show-options", "-sv", f"@almost-{self.runtime.scope}-tty")

    def start_terminal(self, *args):
        terminal = Terminal([sys.executable, "-m", "almost", "--config", str(self.path), "connect", *args], env=self.environment)
        self.terminals.append(terminal)
        return terminal

    def response(self, port):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            connection.request("GET", "/")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"almost integration")
        finally:
            connection.close()

    def test_end_to_end_recovery_and_lifecycle(self):
        print("\nLive check: real forwarding and supervisor recovery", flush=True)
        self.cli("up")
        status = request(self.runtime)
        self.assertEqual(status["phase"], "connected", self.cli("logs").stdout)
        for port in self.ports:
            self.response(port)
        first_pid = status["pid"]
        self.cli("up")
        self.assertEqual(request(self.runtime)["pid"], first_pid)
        master = next(self.runtime.directory.glob("ssh-*.sock"))
        self.assertEqual(ssh.control(self.profile, master, "exit").returncode, 0)
        wait_for(lambda: (request(self.runtime) or {}).get("attempts", 0) >= 2)
        wait_for(lambda: (request(self.runtime) or {}).get("phase") == "connected")
        for port in self.ports:
            self.response(port)

        print("Live check: reclaim owned tunnels after supervisor termination", flush=True)
        os.kill(request(self.runtime)["pid"], signal.SIGKILL)
        wait_for(lambda: request(self.runtime) is None)
        self.cli("up")
        for port in self.ports:
            self.response(port)

        print("Live check: terminal reconnection returns to switched session and pane", flush=True)
        self.tm("set-hook", "-g", "client-attached[0]", "set-option -s @existing-hook ran")
        terminal = self.start_terminal("--session", "alpha")
        first = terminal.wait_for(lambda: self.snapshot().attempt)
        client = self.client()
        self.tm("switch-client", "-c", client, "-t", "beta")
        self.tm("new-window", "-t", "beta", "-n", "second", "sh")
        self.tm("split-window", "-t", "beta:second", "sh")
        position = self.tm("display-message", "-p", "-t", "beta:", "#{session_id}:#{window_id}:#{pane_id}")
        self.assertEqual(self.snapshot().remembered, "$1")
        listing = subprocess.check_output(["ps", "-axo", "pid=,ppid=,comm="], text=True)
        children = [int(row.split(None, 2)[0]) for row in listing.splitlines()
                    if len(row.split(None, 2)) == 3 and row.split(None, 2)[1] == str(terminal.child.pid)
                    and Path(row.split(None, 2)[2]).name == "ssh"]
        self.assertEqual(len(children), 1)
        os.kill(children[0], signal.SIGKILL)
        terminal.wait_for(lambda: self.snapshot().attempt not in (None, first), timeout=30)
        self.assertEqual(self.snapshot().remembered, "$1")
        self.assertEqual(self.tm("display-message", "-p", "-t", "beta:", "#{session_id}:#{window_id}:#{pane_id}"), position)
        self.assertEqual(len(self.snapshot().sessions), 2)
        self.tm("detach-client", "-t", self.client())
        self.assertEqual(terminal.wait_exit(), 0)
        self.assertIn("@existing-hook", self.tm("show-hooks", "-g"))
        self.assertNotIn(self.runtime.scope, self.tm("show-hooks", "-g"))
        previous = self.snapshot().attempt
        reopened = self.start_terminal()
        reopened.wait_for(lambda: self.snapshot().attempt not in (None, previous))
        self.assertNotIn("Remote tmux sessions:", reopened.text)
        self.assertEqual(self.snapshot().remembered, "$1")
        reopened.hangup()
        self.assertEqual(reopened.wait_exit(), 0)
        for port in self.ports:
            self.response(port)

        print("Live check: occupied port blocks tunnels without blocking editing", flush=True)
        self.cli("stop")
        with socket.socket() as occupied:
            occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            occupied.bind(("127.0.0.1", self.ports[0]))
            occupied.listen()
            result = self.cli("up", check=False)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertEqual(request(self.runtime)["phase"], "blocked")
            previous = self.snapshot().attempt
            editing = self.start_terminal("--session", "beta")
            editing.wait_for(lambda: self.snapshot().attempt not in (None, previous))
            self.cli("stop")
            self.assertEqual(editing.wait_exit(), 0)
        self.cli("up")
        for port in self.ports:
            self.response(port)
        self.cli("stop")
        self.assertEqual(len(self.snapshot().sessions), 2)


if __name__ == "__main__":
    unittest.main()
