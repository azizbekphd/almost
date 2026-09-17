# Troubleshooting

Start with `almost doctor PROFILE`, `almost status PROFILE`, and
`almost logs PROFILE --lines 100`. Omit `PROFILE` for the default. For custom
files, put `--config /path/to/config.toml` before every command.

## Command or Python not found

Add `~/.local/bin` to PATH for the offline installer, or run `pipx ensurepath`
for pipx. `command -v almost` identifies the selected installation.
`python3 --version` must report 3.11+. If the Python interpreter used for
installation was removed or moved during an upgrade, stop running profiles and
reinstall using an available interpreter.

## No configuration or an example host

Run `almost init`, then edit the printed file path. Replace `my-server` with
your SSH alias or host. `init` creates a template and does not discover servers.
It preserves existing files; use `--config` to create a separate template.

## Authentication or host-key errors

Verify the same host with ordinary SSH:

```sh
ssh my-server
ssh-add -l
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes my-server true
```

Load or unlock your key when needed. Password-only authentication cannot be
used for automatic reconnection. Verify and accept unknown keys through
ordinary SSH first. Investigate changed keys with the server operator;
`almost` never accepts them automatically.

Authentication, host-key, configuration, and forwarding setup errors pause
background retries. Correct the cause, then run `almost up PROFILE` or
`almost connect PROFILE` again. `doctor` does not itself resume a paused supervisor.

## Local port unavailable

Stop the manual tunnel or application owning the port, or choose another local
port. `almost` does not terminate unrelated processes to claim ports. Running
profiles cannot share a local port. Stop and restart after editing forwards.

## Connected, but the application is unreachable

Use `http://127.0.0.1:LOCAL_PORT` to reach the IPv4 listener. Check that the
remote application is running and reachable at the configured destination
from the SSH server. `connected` indicates a ready SSH listener, not application
health. Stopping the application does not cause an SSH reconnect loop.

Established TCP connections are lost when a tunnel breaks. Refresh pages or
restart application clients after network recovery when necessary.

## tmux missing, too old, or absent from SSH's PATH

`ssh my-server 'tmux -V'` must report tmux 3.2+. Install or upgrade tmux on the
server and make it available to non-interactive SSH commands. If your sessions
use `tmux -L NAME`, configure `tmux_socket` with that same name.

## Session disappeared or window changed

If the remembered session disappears, the chooser opens again. With no sessions,
`almost` offers to create one. Session IDs survive renames; session memory
disappears when the tmux server restarts. Reconnection cannot restore programs
lost in a server reboot. Other clients sharing the session can change its
active window and pane.

## Interactive connection already open

One managed interactive client is allowed per profile. Detach or close its
terminal before connecting again. `almost stop PROFILE` also ends its managed
connection and tunnels, preserving remote sessions. Ordinary clients can coexist.

## Supervisor did not start

Inspect the printed `bootstrap.log` path and profile logs. State directories
must belong to your user and support file locks; the environment must allow
Unix sockets. Sandboxes blocking local sockets cannot run the supervisor.
`almost stop PROFILE` reclaims only SSH masters in the utility's own socket
directory. Avoid manually killing PIDs copied from old state files.

## Permission denied for `/tmp/almost-UID` in Termux

Older versions hard-code `/tmp`, which Termux cannot write to. Install a version
containing the Termux temporary-directory fix. `almost` then uses Termux's
`$TMPDIR` and compact socket paths. Changing `$TMPDIR` alone does not fix an
older version. See [installation](installation.md) for upgrading.

## Reporting a bug

Use [GitHub Issues](https://github.com/azizbekphd/almost/issues) with reproduction
steps, OS and Python versions, `almost --version`, remote tmux version, and
redacted diagnostics. Remove private hostnames, usernames, paths, addresses,
and other values you do not want to share. Never include keys or credentials.
