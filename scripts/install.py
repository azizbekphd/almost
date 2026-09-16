#!/usr/bin/env python3
"""Install without network access, pip, or third-party Python packages."""
import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import venv


def main() -> None:
    if sys.version_info < (3, 11):
        raise SystemExit("almost requires Python 3.11 or newer.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local")
    args = parser.parse_args()
    prefix = args.prefix.expanduser().resolve()
    environment = prefix / "share/almost/venv"
    launcher = prefix / "bin/almost"
    source = Path(__file__).resolve().parent.parent / "almost"
    if launcher.exists() and "# almost CLI launcher" not in launcher.read_text(errors="replace"):
        raise SystemExit(f"Refusing to replace an unrelated executable: {launcher}")
    environment.parent.mkdir(parents=True, exist_ok=True)
    venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
    python = environment / "bin/python"
    site = Path(subprocess.check_output([str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True).strip())
    destination = site / "almost"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("#!/bin/sh\n# almost CLI launcher\nexec " + shlex.quote(str(python)) + ' -m almost "$@"\n')
    launcher.chmod(0o755)
    subprocess.run([str(launcher), "--version"], check=True)
    print(f"Installed {launcher}")
    if str(launcher.parent) not in os.environ.get("PATH", "").split(os.pathsep):
        print(f"Add this directory to PATH: {launcher.parent}")
        print(f'For sh/bash/zsh: export PATH={shlex.quote(str(launcher.parent))}:"$PATH"')
    print("Next: almost init, edit the generated configuration, then almost doctor")


if __name__ == "__main__":
    main()
