import base64
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from almost.config import AlmostError, EXAMPLE, Forward, Profile, load
from almost.remote import parse_snapshot, create_script
from almost.runtime import Lock, private_dir, secure_open
from almost.ssh import Backoff, failure


class ConfigTests(unittest.TestCase):
    def test_example_and_multiple_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(EXAMPLE + '\n[profiles.other]\nhost="another-host"\nforwards=[]\n')
            config = load(path)
            self.assertEqual(config.profile(None).host, "my-server")
            self.assertEqual(config.profile("other").forwards, ())
            self.assertEqual(config.profile(None).forwards[0].spec, "127.0.0.1:3000:localhost:3000")

    def test_rejects_invalid_config_before_processes_start(self):
        cases = [
            EXAMPLE.replace('host = "my-server"', 'host = "-oProxyCommand=bad"'),
            EXAMPLE.replace('"8080:localhost:8080"', '"3000:localhost:8080"'),
            EXAMPLE.replace('forwards = [', 'forwardz = ['),
            EXAMPLE.replace('version = 1', 'version = true'),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            for value in cases:
                with self.subTest(value=value):
                    path.write_text(value)
                    with self.assertRaises(AlmostError):
                        load(path)

    def test_ports_and_ipv6_destinations(self):
        self.assertEqual(Forward.parse("3000:[::1]:8080").spec, "127.0.0.1:3000:[::1]:8080")
        for value in ["0:host:8", "70000:host:8", "3:host:0", "3:$(touch marker):8", 3000, "0.0.0.0:3000:host:3"]:
            with self.subTest(value=value), self.assertRaises(AlmostError):
                Forward.parse(value)


class ProtocolTests(unittest.TestCase):
    def test_names_are_not_protocol_or_terminal_commands(self):
        name = "quote'$(echo bad)\t\n\x1b[31mUnicode Ω"
        encoded = base64.b64encode((name + "\n").encode()).decode()
        response = f"login banner\nALMOST_SNAPSHOT_1\nVERSION\ttmux 3.2a\nLAST\t$12\nATTEMPT\t{'a'*32}\nSESSION\t$12\t{encoded}\nALMOST_END\n"
        snapshot = parse_snapshot(response)
        self.assertEqual(snapshot.sessions[0].name, name)
        self.assertEqual(snapshot.remembered, "$12")

    def test_rejects_incomplete_old_or_invalid_protocol(self):
        for value in ["", "ALMOST_SNAPSHOT_1\nVERSION\ttmux 3.2a\n", "ALMOST_SNAPSHOT_1\nVERSION\ttmux 2.9\nALMOST_END", "ALMOST_SNAPSHOT_1\nVERSION\ttmux 3.2a\nSESSION\t$0\t!\nALMOST_END"]:
            with self.subTest(value=value), self.assertRaises(AlmostError):
                parse_snapshot(value)

    def test_invalid_creation_name_never_reaches_remote_shell(self):
        for name in ["", "a:b", "a.b", "a\nb", "a" * 121]:
            with self.assertRaises(AlmostError):
                create_script(Profile("test", "test", ()), name)


class SafetyTests(unittest.TestCase):
    def test_lock_and_symlink_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = private_dir(Path(directory) / "state")
            first, second = Lock(parent / "lock"), Lock(parent / "lock")
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.close()
            self.assertTrue(second.acquire())
            second.close()
            target = parent / "target"
            target.write_text("unchanged")
            (parent / "link").symlink_to(target)
            with self.assertRaises(OSError):
                secure_open(parent / "link", os.O_WRONLY)
            self.assertEqual(target.read_text(), "unchanged")

    def test_permanent_errors_pause_and_network_errors_retry(self):
        for message in ["Permission denied (publickey).", "Host key verification failed.", "bind [127.0.0.1]:3000: Address already in use", "Bad configuration option: wat"]:
            self.assertIsNotNone(failure(message))
        for message in ["Broken pipe", "Connection reset by peer", "Operation timed out", "Connection refused", "Could not resolve hostname"]:
            self.assertIsNone(failure(message))

    def test_backoff_is_bounded_and_resets(self):
        backoff = Backoff()
        with patch("random.uniform", return_value=1):
            self.assertEqual([backoff.next() for _ in range(8)], [1, 2, 4, 8, 10, 10, 10, 10])
            backoff.reset()
            self.assertEqual(backoff.next(), 1)


if __name__ == "__main__":
    unittest.main()
