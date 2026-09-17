import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from almost import __version__
from almost.config import AlmostError, Config, Profile
from almost.runtime import Lock, Runtime
from almost import update


ROOT = Path(__file__).resolve().parent.parent


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="almost-update-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.prefix = self.root / "prefix with spaces"
        self.source = self.root / "new source"
        shutil.copytree(ROOT / "almost", self.source / "almost", ignore=shutil.ignore_patterns("__pycache__"))
        (self.source / "scripts").mkdir()
        shutil.copyfile(ROOT / "scripts/install.py", self.source / "scripts/install.py")
        (self.source / "almost/__init__.py").write_text('__version__ = "99.88.77"\n')
        self.env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
        self.env.update(XDG_CONFIG_HOME=str(self.root / "config"), XDG_STATE_HOME=str(self.root / "state"))
        environment = patch.dict(os.environ, self.env)
        environment.start()
        self.addCleanup(environment.stop)

    def install(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/install.py"), "--prefix", str(self.prefix)],
                                env=self.env, cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return self.prefix / "bin/almost"

    def archive(self, member=None):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            if member is None:
                archive.add(self.source, arcname="almost_cli-99.88.77")
            else:
                archive.addfile(member, io.BytesIO(b"x") if member.isfile() else None)
        return stream.getvalue()

    def downloads(self, content, checksum=None):
        name = "almost_cli-99.88.77.tar.gz"
        checksum = checksum or hashlib.sha256(content).hexdigest()
        return [io.BytesIO(json.dumps({"tag_name": "v99.88.77", "assets": [{"name": name}, {"name": "SHA256SUMS"}]}).encode()),
                io.BytesIO(f"{checksum}  {name}\n".encode()), io.BytesIO(content)]

    def test_local_source_updates_installed_command_without_config(self):
        launcher = self.install()
        result = subprocess.run([str(launcher), "update", "--source", str(self.source)],
                                env=self.env, cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        version = subprocess.check_output([str(launcher), "--version"], env=self.env, text=True, cwd=self.root)
        self.assertEqual(version.strip(), "almost 99.88.77")
        self.assertFalse((self.root / "config/almost/config.toml").exists())

    def test_release_update_verifies_checksum_and_preserves_config(self):
        launcher = self.install()
        config = self.root / "config/almost/config.toml"
        config.parent.mkdir(parents=True)
        config.write_text("# private settings\n")
        with patch("almost.update.installation_prefix", return_value=self.prefix), \
                patch("almost.update.urlopen", side_effect=self.downloads(self.archive())) as network:
            self.assertEqual(update.update(), 0)
        self.assertEqual(network.call_count, 3)
        self.assertEqual(network.call_args_list[0].args[0].get_header("Accept"), "application/vnd.github+json")
        self.assertEqual(config.read_text(), "# private settings\n")
        version = subprocess.check_output([str(launcher), "--version"], env=self.env, text=True, cwd=self.root)
        self.assertEqual(version.strip(), "almost 99.88.77")

    def test_already_current_does_not_download_or_install(self):
        self.prefix.joinpath("share/almost").mkdir(parents=True)
        release = {"tag_name": f"v{__version__}", "assets": [{"name": f"almost_cli-{__version__}.tar.gz"}, {"name": "SHA256SUMS"}]}
        with patch("almost.update.installation_prefix", return_value=self.prefix), \
                patch("almost.update.urlopen", return_value=io.BytesIO(json.dumps(release).encode())) as network, \
                patch("almost.update.subprocess.run") as install:
            self.assertEqual(update.update(), 0)
        self.assertEqual(network.call_count, 1)
        install.assert_not_called()

    def test_checksum_failure_leaves_installed_version(self):
        launcher = self.install()
        with patch("almost.update.installation_prefix", return_value=self.prefix), \
                patch("almost.update.urlopen", side_effect=self.downloads(self.archive(), "0" * 64)):
            with self.assertRaisesRegex(AlmostError, "Checksum verification failed"):
                update.update()
        version = subprocess.check_output([str(launcher), "--version"], env=self.env, text=True, cwd=self.root)
        self.assertEqual(version.strip(), f"almost {__version__}")

    def test_rejects_unsafe_archive_members(self):
        members = [tarfile.TarInfo("almost_cli-99.88.77/../../outside"), tarfile.TarInfo("/absolute"),
                   tarfile.TarInfo("different-root/file"), tarfile.TarInfo("almost_cli-99.88.77/link")]
        members[-1].type = tarfile.SYMTYPE
        members[-1].linkname = "../../outside"
        for member in members:
            content = self.archive(member)
            with self.subTest(name=member.name), \
                    patch("almost.update.download", side_effect=[
                        f"{hashlib.sha256(content).hexdigest()}  almost_cli-99.88.77.tar.gz\n".encode(), content]):
                with self.assertRaisesRegex(AlmostError, "Unsafe release archive member"):
                    update.extract_release("v99.88.77", self.root)
        self.assertFalse((self.root / "source").exists())

    def test_failed_source_install_restores_installed_version(self):
        launcher = self.install()
        (self.source / "almost/__init__.py").write_text("raise RuntimeError('broken release')\n")
        result = subprocess.run([str(launcher), "update", "--source", str(self.source)],
                                env=self.env, cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        version = subprocess.check_output([str(launcher), "--version"], env=self.env, text=True, cwd=self.root)
        self.assertEqual(version.strip(), f"almost {__version__}")

    def test_rejects_update_while_supervisor_lock_is_held(self):
        profile = Profile("test", "test", ())
        config = Config(self.root / "config.toml", "test", {"test": profile})
        runtime = Runtime.for_profile(config, profile)
        self.addCleanup(shutil.rmtree, runtime.directory, True)
        with Lock(runtime.directory / "supervisor.lock"):
            with self.assertRaisesRegex(AlmostError, "Stop running profiles"):
                update.require_stopped()

    def test_network_failure_is_actionable(self):
        with patch("almost.update.urlopen", side_effect=URLError("offline")):
            with self.assertRaisesRegex(AlmostError, "Could not download.*offline"):
                update.latest_release()

    def test_source_checkout_is_not_mistaken_for_an_installed_copy(self):
        with self.assertRaisesRegex(AlmostError, "requires an installation"):
            update.installation_prefix()


if __name__ == "__main__":
    unittest.main()
