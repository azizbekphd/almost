"""Update offline installations from verified GitHub release archives."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
from urllib.error import URLError
from urllib.request import Request, urlopen

from . import __version__
from .config import AlmostError, state_root
from .runtime import Lock, socket_directory


REPOSITORY = "https://github.com/azizbekphd/almost"
LATEST_RELEASE = "https://api.github.com/repos/azizbekphd/almost/releases/latest"


def installation_prefix() -> Path:
    environment = Path(sys.prefix).resolve()
    if environment.parts[-3:] == ("share", "almost", "venv"):
        prefix = environment.parents[2]
        launcher = prefix / "bin/almost"
        if launcher.is_file() and "# almost CLI launcher" in launcher.read_text():
            if Path(__file__).resolve().is_relative_to(environment):
                return prefix
    raise AlmostError("'almost update' requires an installation made with install.sh. "
                      "For pipx or pip installations, use that installer's upgrade command.")


def download(url: str) -> bytes:
    accept = "application/vnd.github+json" if url == LATEST_RELEASE else "application/octet-stream"
    request = Request(url, headers={"User-Agent": f"almost/{__version__}", "Accept": accept})
    try:
        with urlopen(request, timeout=30) as response:
            return response.read()
    except (OSError, URLError) as exc:
        raise AlmostError(f"Could not download {url}: {exc}") from exc


def latest_release() -> str:
    try:
        release = json.loads(download(LATEST_RELEASE))
        tag = release["tag_name"]
        if release.get("draft") or release.get("prerelease") or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
            raise ValueError("expected a stable version tag")
        names = {asset["name"] for asset in release["assets"]}
        if not {f"almost_cli-{tag[1:]}.tar.gz", "SHA256SUMS"} <= names:
            raise ValueError("source archive or SHA256SUMS is missing")
        return tag
    except (KeyError, TypeError, ValueError) as exc:
        raise AlmostError(f"Invalid latest release information: {exc}") from exc


def extract_release(tag: str, temporary: Path) -> Path:
    name = f"almost_cli-{tag[1:]}.tar.gz"
    url = f"{REPOSITORY}/releases/download/{tag}"
    checksums = download(f"{url}/SHA256SUMS").decode("utf-8")
    matches = [line.split()[0] for line in checksums.splitlines()
               if len(line.split()) == 2 and line.split()[1] == name]
    if len(matches) != 1 or not re.fullmatch(r"[0-9a-fA-F]{64}", matches[0]):
        raise AlmostError(f"Release checksums do not contain a unique SHA-256 for {name}.")
    content = download(f"{url}/{name}")
    if hashlib.sha256(content).hexdigest() != matches[0].lower():
        raise AlmostError(f"Checksum verification failed for {name}; installation was not changed.")
    archive_path = temporary / name
    archive_path.write_bytes(content)
    destination = temporary / "source"
    # Only regular files and directories from the expected source root are needed.
    # Reject links, special files and traversal before extracting anything.
    root_name = f"almost_cli-{tag[1:]}"
    try:
        with tarfile.open(archive_path) as archive:
            members = archive.getmembers()
            for member in members:
                path = Path(member.name)
                if (path.is_absolute() or ".." in path.parts or not path.parts
                        or path.parts[0] != root_name or not (member.isfile() or member.isdir())):
                    raise AlmostError(f"Unsafe release archive member: {member.name}")
            options = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
            archive.extractall(destination, members=members, **options)
    except tarfile.TarError as exc:
        raise AlmostError(f"Cannot extract release archive: {exc}") from exc
    return destination / root_name


def require_stopped() -> None:
    root = state_root()
    if not root.exists():
        return
    for data in root.iterdir():
        if not re.fullmatch(r"[0-9a-f]{24}", data.name):
            continue
        lock_path = socket_directory(data.name) / "supervisor.lock"
        if not lock_path.exists():
            continue
        lock = Lock(lock_path)
        if not lock.acquire():
            raise AlmostError("Stop running profiles with 'almost stop PROFILE' before updating.")
        lock.close()


def install_source(source: Path, prefix: Path) -> None:
    source = source.expanduser().resolve()
    installer = source / "scripts/install.py"
    if not installer.is_file() or not (source / "almost/__init__.py").is_file():
        raise AlmostError(f"Not an almost source directory: {source}")
    require_stopped()
    print(f"Updating {prefix / 'bin/almost'}…", flush=True)
    result = subprocess.run([sys.executable, str(installer), "--prefix", str(prefix)], cwd=source)
    if result.returncode:
        raise AlmostError(f"The update installer exited with status {result.returncode}.")


def update(source: Path | None = None) -> int:
    prefix = installation_prefix()
    with Lock(prefix / "share/almost/update.lock"):
        if source is not None:
            install_source(source, prefix)
            return 0
        print("Checking the latest almost release…", flush=True)
        tag = latest_release()
        if tuple(map(int, tag[1:].split("."))) <= tuple(map(int, __version__.split("."))):
            print(f"almost {__version__} is already up to date (latest release: {tag}).")
            return 0
        require_stopped()
        print(f"Downloading and verifying {tag}…", flush=True)
        with tempfile.TemporaryDirectory(prefix="almost-update-") as directory:
            install_source(extract_release(tag, Path(directory)), prefix)
    return 0
