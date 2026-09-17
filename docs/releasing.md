# Publishing a release

The release version is defined once in `almost/__init__.py` as `__version__`.
Package metadata and the CLI read it from there. This is independent of the
configuration format's `version = 1`. During 0.x, describe any breaking changes
in the changelog before publishing a minor release.

1. Update `__version__`, add a dated `## [VERSION] - YYYY-MM-DD` entry in
   `CHANGELOG.md`, and update versioned download examples in the README and
   installation guide. Add the changelog's release link.
2. Run tests and build clean artifacts in a development virtual environment:

   ```sh
   ALMOST_REAL_TMUX=1 python3 -m unittest discover -s tests -v
   python3 -m venv .venv
   .venv/bin/python -m pip install build twine
   # Move or remove old build/ and dist/ directories if they exist.
   .venv/bin/python -m build
   .venv/bin/python -m twine check dist/*
   .venv/bin/python scripts/check_dist.py
   .venv/bin/python scripts/release.py --tag v0.2.0 --notes /tmp/almost-release-notes.md
   ```

   Replace the example tag with the new version. The package checker installs
   the wheel and extracted source away from the checkout, including paths with
   spaces. The release script rejects mismatched tags, missing changelog entries,
   and stale artifacts from other versions.

3. Commit, push `main`, and wait for CI to pass. Then create an annotated tag
   on that tested commit and push it:

   ```sh
   git tag -a v0.2.0 -m 'almost 0.2.0'
   git push origin v0.2.0
   ```

4. The release workflow runs the same macOS/Linux checks on the tag, builds and
   verifies packages, generates SHA-256 checksums and notes from the changelog,
   and publishes a GitHub Release. It uses the repository's built-in
   `GITHUB_TOKEN`; no personal access token or PyPI credentials are required.
5. Confirm the workflow succeeded, the release points to the intended tag, all
   three assets are available, and a downloaded archive installs successfully.

If publishing fails, resolve the error and rerun the failed workflow. An existing
draft can be completed on retry. A published release is never overwritten by
the workflow; publish a new patch version for corrected artifacts.

Distributions are published on GitHub Releases. PyPI publishing would require
separate package-name ownership and trusted-publisher setup.
