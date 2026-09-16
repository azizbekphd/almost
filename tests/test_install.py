import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from almost import __version__


class InstallTests(unittest.TestCase):
    def test_offline_install_update_and_existing_config(self):
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory(prefix="almost-install-") as directory:
            prefix = Path(directory) / "prefix with spaces"
            env = {**os.environ, "XDG_CONFIG_HOME": str(Path(directory) / "config")}
            for _ in range(2):
                result = subprocess.run([sys.executable, str(root / "scripts/install.py"), "--prefix", str(prefix)],
                                        capture_output=True, text=True, env=env, cwd=directory, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            launcher = prefix / "bin/almost"
            result = subprocess.run([str(launcher), "--version"], capture_output=True, text=True, env=env, cwd=directory)
            self.assertEqual(result.stdout.strip(), f"almost {__version__}")
            subprocess.run([str(launcher), "init"], capture_output=True, check=True, env=env, cwd=directory)
            config = Path(env["XDG_CONFIG_HOME"]) / "almost/config.toml"
            config.write_text(config.read_text() + "\n# preserved\n")
            subprocess.run([str(launcher), "init"], capture_output=True, check=True, env=env, cwd=directory)
            self.assertTrue(config.read_text().endswith("# preserved\n"))

    def test_refuses_to_replace_an_unrelated_launcher(self):
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory(prefix="almost-install-") as directory:
            prefix = Path(directory)
            launcher = prefix / "bin/almost"
            launcher.parent.mkdir()
            launcher.write_text("#!/bin/sh\necho unrelated\n")
            result = subprocess.run([sys.executable, str(root / "scripts/install.py"), "--prefix", directory],
                                    capture_output=True, text=True, cwd=directory, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Refusing to replace an unrelated executable", result.stderr)
            self.assertEqual(launcher.read_text(), "#!/bin/sh\necho unrelated\n")


if __name__ == "__main__":
    unittest.main()
