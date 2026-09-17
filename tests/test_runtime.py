import os
from pathlib import Path
import shutil
import socket
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

from almost.config import AlmostError, Config, Profile
from almost.runtime import Runtime


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="a-", dir="/tmp" if sys.platform == "darwin" else None)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.profile = Profile("test", "test", ())
        self.config = Config(self.root / "config.toml", "test", {"test": self.profile})
        (self.root / "state" / "almost").mkdir(parents=True, mode=0o700)
        environment = patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root / "state")})
        environment.start()
        self.addCleanup(environment.stop)

    def make_runtime(self):
        runtime = Runtime.for_profile(self.config, self.profile)
        self.addCleanup(shutil.rmtree, runtime.directory, True)
        return runtime

    def long_temporary_directory(self):
        if len(os.fsencode(self.root)) > 32:
            return Path(tempfile.gettempdir())
        temporary = self.root / ("x" * (32 - len(os.fsencode(self.root))))
        temporary.mkdir()
        return temporary

    def test_uses_writable_temporary_directory_on_linux(self):
        temporary = self.root if len(os.fsencode(self.root)) <= 37 else Path(tempfile.gettempdir())
        with patch("sys.platform", "linux"), \
                patch.dict(os.environ, {"TMPDIR": str(temporary)}), \
                patch("tempfile.tempdir", None):
            runtime = self.make_runtime()
            again = self.make_runtime()
        self.assertTrue(runtime.directory.is_relative_to(temporary))
        self.assertEqual(runtime, again)
        self.assertEqual(runtime.data, self.root / "state" / "almost" / runtime.scope)
        self.assertEqual(stat.S_IMODE(runtime.directory.stat().st_mode), 0o700)

    def test_keeps_short_mac_socket_path(self):
        def record_directory(path):
            if path.is_relative_to(self.root):
                path.mkdir(parents=True, exist_ok=True)
            return path

        with patch("sys.platform", "darwin"), \
                patch("almost.runtime.tempfile.gettempdir", return_value="/very/long/mac/temp/path"), \
                patch("almost.runtime.private_dir", side_effect=record_directory):
            runtime = Runtime.for_profile(self.config, self.profile)
        self.assertEqual(runtime.directory, Path("/tmp") / f"almost-{os.getuid()}" / runtime.scope)

    def test_termux_paths_fit_ssh_temporary_socket_limit(self):
        for temporary in ["/data/data/com.termux/files/usr/tmp", "/data/user/0/com.termux/files/usr/tmp"]:
            with self.subTest(temporary=temporary), \
                    patch("sys.platform", "linux"), \
                    patch("almost.runtime.tempfile.gettempdir", return_value=temporary), \
                    patch("almost.runtime.private_dir", side_effect=lambda path: path):
                runtime = Runtime.for_profile(self.config, self.profile)
                self.assertTrue(runtime.directory.is_relative_to(Path(temporary)))
                self.assertLess(len(os.fsencode(str(runtime.new_ssh_socket()) + "." + "x" * 16)), 108)

    def test_long_temporary_directory_supports_real_sockets(self):
        temporary = self.long_temporary_directory()
        with patch("sys.platform", "linux"), \
                patch("almost.runtime.tempfile.gettempdir", return_value=str(temporary)):
            runtime = self.make_runtime()
        for path in [runtime.socket, Path(str(runtime.new_ssh_socket()) + "." + "x" * 16)]:
            with self.subTest(path=path), socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(path))
                listener.listen()
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.connect(str(path))
                connection, _ = listener.accept()
                connection.close()
            path.unlink(missing_ok=True)

    def test_rejects_symlink_socket_directories(self):
        target = self.root / "target"
        target.mkdir(mode=0o700)
        temporary = self.root if len(os.fsencode(self.root)) <= 37 else Path(tempfile.gettempdir())
        for temporary in [temporary, self.long_temporary_directory()]:
            with self.subTest(temporary=temporary), patch("sys.platform", "linux"), \
                    patch("almost.runtime.tempfile.gettempdir", return_value=str(temporary)):
                runtime = self.make_runtime()
                runtime.directory.rmdir()
                runtime.directory.symlink_to(target)
                with self.assertRaisesRegex(AlmostError, "Unsafe state directory"):
                    Runtime.for_profile(self.config, self.profile)
                runtime.directory.unlink()

    def test_reports_temporary_directory_too_long(self):
        with patch("sys.platform", "linux"), \
                patch("almost.runtime.tempfile.gettempdir", return_value="/" + "x" * 100):
            with self.assertRaisesRegex(AlmostError, "TMPDIR"):
                Runtime.for_profile(self.config, self.profile)


if __name__ == "__main__":
    unittest.main()
