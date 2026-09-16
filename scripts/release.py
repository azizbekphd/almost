#!/usr/bin/env python3
"""Validate a release tag and generate its notes and artifact checksums."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import runpy


ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--notes", type=Path, required=True)
    args = parser.parse_args()
    version = runpy.run_path(str(ROOT / "almost/__init__.py"))["__version__"]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) or args.tag != f"v{version}":
        raise SystemExit(f"Release tag must match the source version: v{version}")
    changelog = (ROOT / "CHANGELOG.md").read_text()
    entry = re.search(rf"^## \[{re.escape(version)}\] - ([0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}})\n(.*?)(?=^## |^\[{re.escape(version)}\]:|\Z)",
                      changelog, flags=re.M | re.S)
    if not entry or not entry[2].strip():
        raise SystemExit(f"Missing dated changelog entry for {version}")
    dist = args.dist.resolve()
    expected = {f"almost_cli-{version}.tar.gz", f"almost_cli-{version}-py3-none-any.whl"}
    artifacts = sorted([*dist.glob("*.tar.gz"), *dist.glob("*.whl")])
    if {path.name for path in artifacts} != expected:
        raise SystemExit(f"Build a clean dist directory containing exactly: {sorted(expected)}")
    checksums = "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in artifacts)
    (dist / "SHA256SUMS").write_text(checksums)
    notes = entry[2].strip() + "\n\n"
    notes += "Download the source archive for the offline installer, or install the wheel with pipx. "
    notes += "See the [installation and setup guide](https://github.com/azizbekphd/almost/tree/" + args.tag + "). "
    notes += "`SHA256SUMS` contains checksums for both artifacts.\n"
    args.notes.write_text(notes)
    print(f"Validated {args.tag}; wrote SHA256SUMS and {args.notes}")


if __name__ == "__main__":
    main()
