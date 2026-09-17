# Contributing

Bug reports and focused pull requests are welcome. Open a
[GitHub Issue](https://github.com/azizbekphd/almost/issues) before substantial
behavior changes. Use [SECURITY.md](SECURITY.md) for private vulnerability reports.

## Development

Use Python 3.11+ on macOS or Linux. There are no runtime dependencies:

```sh
python3 -m almost --help
python3 -m unittest discover -s tests -v
```

For editable installation and packaging tools:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e . build twine
```

## Tests

The default suite tests configuration, shell protocol handling, private state,
offline installation and verified release updates, and actual CLI subprocesses
with a deterministic SSH fixture. Process tests require Unix sockets and
pseudo-terminals; run outside sandboxes that prohibit those resources.

With local tmux 3.2+, exercise the actual shell scripts and hooks:

```sh
ALMOST_REAL_TMUX=1 python3 -m unittest discover -s tests -v
```

Tests create disposable tmux servers on unique named sockets and leave the
default local server alone. CI runs on macOS and Linux with Python 3.11–3.14.

For optional real SSH integration, supply a host you control:

```sh
ALMOST_SSH_HOST=my-test-server python3 -m unittest tests.test_live -v
```

This opt-in suite also requires remote Python 3. It creates a separate remote
tmux server, temporary HTTP service, and forwards on unused local ports. It
deliberately terminates utility-owned SSH connections and cleans up its resources.
It does not operate on the default remote tmux server. This suite is not run
against contributors' servers in CI.

## Before submitting

Run tests appropriate to the change. For installation or metadata changes,
build and verify both distribution formats:

```sh
.venv/bin/python -m build
.venv/bin/python -m twine check dist/*
.venv/bin/python scripts/check_dist.py
```

`check_dist.py` installs the wheel and extracted archive into temporary
environments and runs the installed CLI away from the checkout. Update docs
and changelog for user-visible changes. See [releasing](docs/releasing.md)
for versioning and publishing.
