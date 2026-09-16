# Installation, upgrades, and removal

## Prerequisites

Use macOS or Linux with Python 3.11+ and OpenSSH. Check locally:

```sh
python3 --version
ssh -V
```

On macOS, Python can be installed with [Homebrew](https://brew.sh):

```sh
brew install python
```

On Debian/Ubuntu whose packaged Python is 3.11 or newer:

```sh
sudo apt-get update
sudo apt-get install python3 python3-venv openssh-client
```

If your distribution supplies an older Python, install Python 3.11+ through a
supported method for that distribution. The installer uses `python3` from PATH;
to choose another interpreter, run `python3.11 scripts/install.py` instead.
The offline installer does not require pip or ensurepip.

On the remote server, install tmux 3.2+ through its package manager if it is
missing. For example, on a compatible Debian/Ubuntu server:

```sh
sudo apt-get install tmux
tmux -V
```

The server also needs `sh`, `base64`, and `tr`, normally provided by the OS.
It does not need Python or an installation of `almost`.

## Download and verify a release

Download the source archive and checksums from the same tagged release:

```sh
curl -fL -o almost_cli-0.1.0.tar.gz https://github.com/azizbekphd/almost/releases/download/v0.1.0/almost_cli-0.1.0.tar.gz
curl -fL -o SHA256SUMS https://github.com/azizbekphd/almost/releases/download/v0.1.0/SHA256SUMS
```

Verify the archive on macOS:

```sh
awk '$2 == "almost_cli-0.1.0.tar.gz"' SHA256SUMS | shasum -a 256 -c -
```

Or on Linux:

```sh
awk '$2 == "almost_cli-0.1.0.tar.gz"' SHA256SUMS | sha256sum -c -
```

The result should say `OK`. Checksums detect download corruption; they are not
independent signatures. Then extract and install:

```sh
tar -xzf almost_cli-0.1.0.tar.gz
cd almost_cli-0.1.0
sh install.sh
export PATH="$HOME/.local/bin:$PATH"
almost --version
almost init
```

Edit the generated configuration before running `almost doctor`. See the
[first connection walkthrough](../README.md#first-connection).

The installer copies the package into a private virtual environment. You can
remove the extracted download afterward. It refuses to replace an executable
named `almost` unless that file is one of its own launchers.

For a custom installation prefix:

```sh
sh install.sh --prefix "$HOME/tools"
export PATH="$HOME/tools/bin:$PATH"
```

The environment is then at `~/tools/share/almost/venv`. Install as your normal
user. Select one installation method so another `almost` on PATH does not hide
the intended version (`command -v almost`).

## pipx or a normal virtual environment

With [pipx installed](https://pipx.pypa.io/stable/installation/):

```sh
pipx install --python python3 'https://github.com/azizbekphd/almost/releases/download/v0.1.0/almost_cli-0.1.0-py3-none-any.whl'
pipx ensurepath
```

Open a new terminal if PATH changes do not take effect. For a normal virtual
environment, download the wheel from the release, verify its checksum as above
using the wheel filename, and install the local file:

```sh
python3 -m venv "$HOME/.venvs/almost"
"$HOME/.venvs/almost/bin/python" -m pip install --no-index --no-deps ./almost_cli-0.1.0-py3-none-any.whl
"$HOME/.venvs/almost/bin/almost" --version
```

The wheel has no runtime dependencies. Its platform-neutral filename describes
the Python packaging format; the utility still requires macOS or Linux.
GitHub Releases is the supported distribution channel; these instructions do
not depend on the availability of an `almost-cli` package on PyPI.

## Run from source

```sh
git clone --branch v0.1.0 --depth 1 https://github.com/azizbekphd/almost.git
cd almost
python3 -m almost init
```

After editing the configuration, run `python3 -m almost doctor` and
`python3 -m almost`. You can also run `sh install.sh` from the clone.

## Upgrade

Stop every running profile before replacing installed files:

```sh
almost stop work
almost stop staging  # If this profile is running.
```

For the offline installer, download and extract the new release, then rerun
`sh install.sh` with the same prefix. For pipx, install the new release's wheel
URL with `pipx install --force URL`. For a normal environment, use its Python
to run `-m pip install --no-index --no-deps --upgrade /path/to/new.whl`.
Replace versions in download URLs and filenames with the desired release.

Check `almost --version`, run `almost doctor`, and reconnect. Configuration
and remote tmux sessions are preserved. Existing custom profiles remain valid.

## Uninstall

Stop each running profile first. For the default offline installation:

```sh
rm "$HOME/.local/bin/almost"
rm -r "$HOME/.local/share/almost/venv"
```

Use your custom prefix if applicable. For pipx use `pipx uninstall almost-cli`;
for a manually created virtual environment, remove that environment.

Configuration and diagnostics remain under `~/.config/almost` and
`~/.local/state/almost` (or your XDG directories). Remove those separately if
you no longer need them. Removing state discards this installation's tmux
session memory namespace. Remote sessions and programs remain running.
