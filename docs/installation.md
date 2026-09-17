# Installation, upgrades, and removal

## Prerequisites

Use macOS, Linux, or Termux on Android with Python 3.11+ and OpenSSH. The download
examples also use `curl` and `tar`. Check locally:

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
sudo apt-get install python3 python3-venv openssh-client curl tar
```

On [Termux](https://github.com/termux/termux-packages):

```sh
pkg install python openssh curl tar
```

If an older Termux installation fails with permission denied for `/tmp/almost-UID`,
go directly to [older-version upgrade steps](#older-versions-without-update).

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

Find the latest stable version once, then download the source archive and
checksums from that same tagged release:

```sh
ALMOST_VERSION=$(curl -fsSL https://api.github.com/repos/azizbekphd/almost/releases/latest |
  python3 -c 'import json, sys; print(json.load(sys.stdin)["tag_name"].removeprefix("v"))') &&
ALMOST_ARCHIVE="almost_cli-${ALMOST_VERSION}.tar.gz" &&
ALMOST_RELEASE_URL="https://github.com/azizbekphd/almost/releases/download/v${ALMOST_VERSION}" &&
curl -fL -o "$ALMOST_ARCHIVE" "$ALMOST_RELEASE_URL/$ALMOST_ARCHIVE" &&
curl -fL -o SHA256SUMS "$ALMOST_RELEASE_URL/SHA256SUMS"
```

Keep using the same shell so these variables remain available. To select a
specific release instead, set `ALMOST_VERSION` to its version (without `v`)
instead of running the lookup. The [latest-release API](https://docs.github.com/en/rest/releases/releases#get-the-latest-release)
selects a published stable release.

Verify the archive on macOS:

```sh
awk -v archive="$ALMOST_ARCHIVE" '$2 == archive' SHA256SUMS | shasum -a 256 -c -
```

Or on Linux and Termux:

```sh
awk -v archive="$ALMOST_ARCHIVE" '$2 == archive' SHA256SUMS | sha256sum -c -
```

The result should say `OK`. Checksums detect download corruption; they are not
independent signatures. Once verification succeeds, extract and install:

```sh
tar -xzf "$ALMOST_ARCHIVE" &&
cd "almost_cli-${ALMOST_VERSION}" &&
sh install.sh &&
export PATH="$HOME/.local/bin:$PATH" &&
cd "$HOME" &&
almost --version &&
almost init
```

Edit the generated configuration before running `almost doctor`. See the
[first connection walkthrough](../README.md#first-connection).

Finish in your home directory before using the installed `almost` command. When
run inside an extracted archive or source checkout, Python can load that source
instead of the installed package. Use `python3 -m almost` when deliberately
running source.

The installer copies the package into a private virtual environment. You can
remove the extracted download afterward. It refuses to replace an executable
named `almost` unless that file is one of its own launchers.

For a custom installation prefix, replace the `sh install.sh` and `export PATH`
lines in the source-install example with:

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
ALMOST_VERSION=$(curl -fsSL https://api.github.com/repos/azizbekphd/almost/releases/latest |
  python3 -c 'import json, sys; print(json.load(sys.stdin)["tag_name"].removeprefix("v"))') &&
pipx install --python python3 "https://github.com/azizbekphd/almost/releases/download/v${ALMOST_VERSION}/almost_cli-${ALMOST_VERSION}-py3-none-any.whl" &&
pipx ensurepath
```

Open a new terminal if PATH changes do not take effect. For a normal virtual
environment, download the wheel and `SHA256SUMS` from the same release:

```sh
ALMOST_VERSION=$(curl -fsSL https://api.github.com/repos/azizbekphd/almost/releases/latest |
  python3 -c 'import json, sys; print(json.load(sys.stdin)["tag_name"].removeprefix("v"))') &&
ALMOST_ARCHIVE="almost_cli-${ALMOST_VERSION}-py3-none-any.whl" &&
ALMOST_RELEASE_URL="https://github.com/azizbekphd/almost/releases/download/v${ALMOST_VERSION}" &&
curl -fL -o "$ALMOST_ARCHIVE" "$ALMOST_RELEASE_URL/$ALMOST_ARCHIVE" &&
curl -fL -o SHA256SUMS "$ALMOST_RELEASE_URL/SHA256SUMS"
```

Use the checksum commands above with this wheel's `ALMOST_ARCHIVE`, then install
the verified local file:

```sh
python3 -m venv "$HOME/.venvs/almost" &&
"$HOME/.venvs/almost/bin/python" -m pip install --no-index --no-deps "./almost_cli-${ALMOST_VERSION}-py3-none-any.whl" &&
"$HOME/.venvs/almost/bin/almost" --version
```

The wheel has no runtime dependencies. Its platform-neutral filename describes
the Python packaging format; the utility still requires macOS, Linux, or Termux.
GitHub Releases is the supported distribution channel; these instructions do
not depend on the availability of an `almost-cli` package on PyPI.

## Run from source

```sh
ALMOST_VERSION=$(curl -fsSL https://api.github.com/repos/azizbekphd/almost/releases/latest |
  python3 -c 'import json, sys; print(json.load(sys.stdin)["tag_name"].removeprefix("v"))') &&
git clone --branch "v${ALMOST_VERSION}" --depth 1 https://github.com/azizbekphd/almost.git &&
cd almost &&
python3 -m almost init
```

After editing the configuration, run `python3 -m almost doctor` and
`python3 -m almost`. You can also run `sh install.sh` from the clone.

## Upgrade

### With `almost update`

`almost update` is available in offline installations starting with 0.2.0. If
your copy lacks that command, use [older-version steps](#older-versions-without-update)
instead.

Stop every running profile before replacing installed files:

```sh
almost stop  # Default profile, if running.
almost stop staging  # Any other running profile; use its actual name.
```

For installations made with the offline installer, run from your home directory:

```sh
cd "$HOME" &&
almost update
```

It checks the latest stable GitHub Release, downloads the source archive and
`SHA256SUMS`, verifies the archive, and installs into the existing prefix. It
requires no pip or additional Python packages. If your version is already
current, it leaves the installation alone. Active supervisors must be stopped
first; the updater reports an error if one is still running. Installation stages
the replacement package and restores the old package if the new CLI fails to start.

To install an unreleased fix from an updated checkout, or use already downloaded
and extracted source without network access:

```sh
almost update --source /path/to/almost
```

The source directory must contain `almost/` and `scripts/install.py`. This option
installs the files currently in that directory; it does not fetch Git changes.

For pipx, install the new release's wheel URL with `pipx install --force URL`.
For a normal environment, use its Python to run
`-m pip install --no-index --no-deps --upgrade /path/to/new.whl`.
Replace versions in download URLs and filenames with the desired release.

Check `almost --version`, run `almost doctor`, and reconnect. Configuration
and remote tmux sessions are preserved. Existing custom profiles remain valid.

### Older versions without `update`

If `almost update` reports an unknown command, install the latest source release
once using the commands below. Your configuration and state are preserved; you
do not need to run `almost init` again. Use your original `--prefix` if it was
customized.

If a foreground command is still running, press Ctrl-C first. Stop any working
profiles before installing. When an older Termux copy fails with permission
denied for `/tmp/almost-UID`, that failure happens before it starts a supervisor;
you can run these commands directly without `almost doctor`, `almost`, or
`almost stop` for that failed attempt.

```sh
ALMOST_VERSION=$(curl -fsSL https://api.github.com/repos/azizbekphd/almost/releases/latest |
  python3 -c 'import json, sys; print(json.load(sys.stdin)["tag_name"].removeprefix("v"))') &&
curl -fL -o "almost_cli-${ALMOST_VERSION}.tar.gz" "https://github.com/azizbekphd/almost/releases/download/v${ALMOST_VERSION}/almost_cli-${ALMOST_VERSION}.tar.gz" &&
tar -xzf "almost_cli-${ALMOST_VERSION}.tar.gz" &&
cd "almost_cli-${ALMOST_VERSION}" &&
sh install.sh &&
export PATH="$HOME/.local/bin:$PATH" &&
cd "$HOME" &&
almost --version
```

For checksum verification, use the [download and verification steps](#download-and-verify-a-release)
before extracting. For a custom installation, replace `sh install.sh` with
`sh install.sh --prefix /your/original/prefix` and add that prefix's `bin` to PATH.

Then check your existing configuration and connect:

```sh
almost doctor
almost
```

Future upgrades use `almost update`, after stopping any running profiles. For a
default profile:

```sh
cd "$HOME" &&
almost stop &&
almost update &&
almost doctor &&
almost
```

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
