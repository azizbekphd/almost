#!/usr/bin/env python3
"""Verify release metadata and install both artifacts away from the checkout."""
from __future__ import annotations

import argparse
import configparser
from email.parser import Parser
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile


ROOT = Path(__file__).resolve().parent.parent
VERSION = runpy.run_path(str(ROOT / "almost/__init__.py"))["__version__"]


def run(args: list[str], cwd: Path, env: dict[str, str]) -> str:
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(f"Command failed: {args}\n{result.stdout}\n{result.stderr}")
    return result.stdout


def check_cli(launcher: Path, directory: Path, env: dict[str, str]) -> None:
    if run([str(launcher), "--version"], directory, env).strip() != f"almost {VERSION}":
        raise RuntimeError("Installed CLI version does not match the source version")
    run([str(launcher), "--help"], directory, env)
    config = directory / "user config.toml"
    run([str(launcher), "--config", str(config), "init"], directory, env)
    config.write_text(config.read_text() + "\n# user setting\n")
    run([str(launcher), "--config", str(config), "init"], directory, env)
    if not config.read_text().endswith("# user setting\n"):
        raise RuntimeError("init overwrote an existing user configuration")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    dist = args.dist.resolve()
    wheel = dist / f"almost_cli-{VERSION}-py3-none-any.whl"
    source = dist / f"almost_cli-{VERSION}.tar.gz"
    with zipfile.ZipFile(wheel) as archive:
        metadata_path = f"almost_cli-{VERSION}.dist-info/METADATA"
        metadata = Parser().parsestr(archive.read(metadata_path).decode())
        for key, expected in [("Name", "almost-cli"), ("Version", VERSION),
                              ("Requires-Python", ">=3.11"), ("License-Expression", "MIT")]:
            if metadata[key] != expected:
                raise RuntimeError(f"Incorrect wheel metadata {key}: {metadata[key]!r}")
        if metadata.get_all("Requires-Dist"):
            raise RuntimeError("Wheel unexpectedly has runtime dependencies")
        entry = configparser.ConfigParser()
        entry.read_string(archive.read(metadata_path.replace("METADATA", "entry_points.txt")).decode())
        if entry["console_scripts"]["almost"] != "almost.cli:main":
            raise RuntimeError("Missing almost console entry point")
        if any(name.startswith("tests/") for name in archive.namelist()):
            raise RuntimeError("Wheel unexpectedly contains the test package")

    with tempfile.TemporaryDirectory(prefix="almost-dist-") as temporary:
        directory = Path(temporary)
        env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
        with tarfile.open(source) as archive:
            prefix = f"almost_cli-{VERSION}/"
            required = {"install.sh", "scripts/install.py", "README.md", "LICENSE",
                        "CHANGELOG.md", "examples/config.toml", "docs/installation.md",
                        "docs/configuration.md", "docs/troubleshooting.md", "docs/releasing.md",
                        "tests/support.py", "tests/__init__.py", "almost/__init__.py"}
            missing = {prefix + name for name in required} - set(archive.getnames())
            if missing:
                raise RuntimeError(f"Source distribution is incomplete: {sorted(missing)}")
            archive.extractall(directory / "source", filter="data")
        extracted = directory / "source" / f"almost_cli-{VERSION}"
        install_prefix = directory / "offline prefix with spaces"
        run([sys.executable, str(extracted / "scripts/install.py"), "--prefix", str(install_prefix)], directory, env)
        check_cli(install_prefix / "bin/almost", directory, env)
        print("Extracted source archive: offline install and CLI checks passed")

        wheel_environment = directory / "wheel venv"
        venv.EnvBuilder(with_pip=True).create(wheel_environment)
        python = wheel_environment / "bin/python"
        run([str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)], directory, env)
        run([str(python), "-I", "-c",
             "import almost, importlib.metadata; "
             "assert almost.__version__ == importlib.metadata.version('almost-cli')"], directory, env)
        check_cli(wheel_environment / "bin/almost", directory, env)
        print("Wheel: offline install, metadata, and CLI checks passed")


if __name__ == "__main__":
    main()
