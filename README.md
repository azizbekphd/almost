# almost

[![CI](https://github.com/azizbekphd/almost/actions/workflows/ci.yml/badge.svg)](https://github.com/azizbekphd/almost/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/azizbekphd/almost)](https://github.com/azizbekphd/almost/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Reliable SSH tunnels and tmux reconnection, entirely in your terminal.

`almost` starts port forwards in the background and attaches your terminal to
remote tmux. If SSH disconnects, it reconnects and returns to the session you
last used, including session switches made inside tmux. Detaching leaves the
tunnels running; stopping `almost` leaves your remote sessions and programs running.

It uses OpenSSH and the server's existing tmux installation. There are no Python
runtime dependencies, remote helper packages, tmux configuration edits, or login
services. Your local tmux configuration stays as it is.

## Requirements

| Where | Requirements |
| --- | --- |
| Local computer | macOS or Linux, Python 3.11+, OpenSSH (`ssh`) |
| Remote server | SSH access, tmux 3.2+, `sh`, `base64`, `tr` |
| Authentication | A verified server host key and non-interactive SSH authentication, usually an unlocked key in your SSH agent |

Native Windows is unsupported. For Windows, run inside WSL with Linux Python,
OpenSSH, and SSH keys configured there. WSL is not currently covered by CI.
Python is required only locally.

## Install

Download the versioned source archive and run its offline installer:

```sh
curl -fL -o almost_cli-0.1.0.tar.gz https://github.com/azizbekphd/almost/releases/download/v0.1.0/almost_cli-0.1.0.tar.gz
tar -xzf almost_cli-0.1.0.tar.gz
cd almost_cli-0.1.0
sh install.sh
export PATH="$HOME/.local/bin:$PATH"
almost --version
```

The installer creates `~/.local/share/almost/venv` and `~/.local/bin/almost`
without pip, downloads, or administrator access. Add the PATH line to `~/.zshrc`
or `~/.bashrc` if needed. Python's standard library `venv` module is required.

If you already use [pipx](https://pipx.pypa.io), install the release wheel instead:

```sh
pipx install 'https://github.com/azizbekphd/almost/releases/download/v0.1.0/almost_cli-0.1.0-py3-none-any.whl'
pipx ensurepath
```

The package is named `almost-cli`; its command is `almost`. Packages are
available through [GitHub Releases](https://github.com/azizbekphd/almost/releases)
with SHA-256 checksums. See [installation options](docs/installation.md) for
prerequisites, checksum verification, custom locations, upgrades, and removal.

## First connection

1. Configure an SSH alias in `~/.ssh/config`, using your hostname and username:

   ```sshconfig
   Host my-server
       HostName server.example.com
       User your-user
       IdentityFile ~/.ssh/id_ed25519
   ```

2. Connect once with ordinary SSH. Verify the server's host-key fingerprint
   through a trusted source before accepting it. Confirm tmux is installed:

   ```sh
   ssh my-server 'tmux -V'
   ssh -o BatchMode=yes -o StrictHostKeyChecking=yes my-server true
   ```

   The second command must succeed without a password or passphrase prompt.
   Load or unlock your key with `ssh-add` if needed. Existing SSH aliases,
   identities, ports, and ProxyJump settings are supported.

3. Create and edit the configuration:

   ```sh
   almost init
   # Open ~/.config/almost/config.toml in your editor.
   ```

   Replace `my-server` with your alias and choose the forwards you need:

   ```toml
   version = 1
   default_profile = "work"

   [profiles.work]
   host = "my-server"
   forwards = ["3000:localhost:3000", "8080:localhost:8080"]
   ```

   These examples expose remote ports 3000 and 8080 only on your computer's
   loopback address. Use `forwards = []` if you only need tmux. Stop any manual
   tunnels occupying your chosen local ports.

4. Check the setup and connect:

   ```sh
   almost doctor
   almost
   ```

   Choose an existing remote session on first use. If none exists, `almost`
   offers to create one with a name you enter. On later connections it resumes
   your remembered session. Remote tmux's usual commands and shortcuts work
   normally; the default detach shortcut is Ctrl-B followed by D.

## Daily commands

| Command | Purpose |
| --- | --- |
| `almost` | Start tunnels and connect to the default profile |
| `almost connect work --session api` | Attach to an explicit existing session |
| `almost up work` | Start only the background tunnels |
| `almost status work` | Show tunnel state and retry timing |
| `almost status work --json` | Return machine-readable status |
| `almost logs work --lines 100` | Show recent SSH diagnostics |
| `almost doctor work` | Check configuration, SSH, remote tmux, and local ports |
| `almost stop work` | Stop this profile's tunnels and interactive connection |
| `almost update` | Install the latest published release for an offline installation |

Profile names are optional and default to `default_profile`. To connect to a
different profile, use `almost connect staging`. `almost init` preserves an
existing file. `almost --config /path/to/config.toml COMMAND` uses a separate
configuration; put `--config` before the command. See `almost --help` and
`almost connect --help` for options.

Detaching, closing the terminal, or pressing Ctrl-C while reconnecting ends the
interactive connection while background tunnels keep running. `almost stop`
preserves remote tmux sessions and their programs. One managed interactive
client is allowed per profile; ordinary SSH/tmux clients can coexist.
The utility does not start at login: run it again after restarting your computer.

## Configuration and recovery

See [configuration](docs/configuration.md) for multiple profiles, IPv6 destinations,
named tmux sockets, and XDG paths. After changing a running profile, stop it and
start it again to apply the changes.

SSH keepalives normally detect a silent broken connection after about 15 seconds.
Retries back off to at most ten seconds. Authentication, host-key, and forwarding
setup errors pause tunnel retries until you fix the issue and run `almost up`
or `almost` again. See [troubleshooting](docs/troubleshooting.md).

A `connected` status means SSH and its local forwarding listeners are ready;
the remote application may still be stopped. Reconnection preserves surviving
tmux sessions but cannot restore programs lost in a server reboot or preserve
established TCP connections through a broken tunnel. Browser requests and
WebSockets may need a refresh. Other clients sharing a tmux session can change
its active window and pane, as with ordinary tmux.

## Development and support

Read [CONTRIBUTING.md](CONTRIBUTING.md) for tests and development setup,
[the architecture notes](docs/architecture.md) for process and state details,
and [the release guide](docs/releasing.md) for publishing a new version.
Changes are recorded in [CHANGELOG.md](CHANGELOG.md).

Report bugs through [GitHub Issues](https://github.com/azizbekphd/almost/issues).
Include your OS, `almost --version`, remote `tmux -V`, and redacted diagnostics.
Report security issues privately as described in [SECURITY.md](SECURITY.md).

Licensed under the [MIT License](LICENSE).
