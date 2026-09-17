# Configuration reference

`almost init` creates `~/.config/almost/config.toml` without overwriting an
existing file. Edit the placeholder host and forwards before connecting.
See [examples/config.toml](../examples/config.toml) for the generated template.

```toml
version = 1
default_profile = "work"

[profiles.work]
host = "my-server"
forwards = ["3000:localhost:3000", "8080:localhost:8080"]

[profiles.staging]
host = "staging-server"
forwards = ["9000:localhost:3000"]
tmux_socket = "development"

[profiles.shell]
host = "your-user@server.example.com"
forwards = []
```

| Key | Meaning |
| --- | --- |
| `version` | Configuration format; must be the integer `1`, independently of the release version |
| `default_profile` | Configured profile to use when none is supplied |
| `profiles.NAME.host` | SSH alias or host argument, such as `my-server` or `user@hostname` |
| `profiles.NAME.forwards` | Local forwards; omitted or empty means no tunnels |
| `profiles.NAME.tmux_socket` | Optional named remote tmux socket, equivalent to `tmux -L NAME`; omitted uses the default server |

Unknown keys and malformed settings are rejected before starting connections.
Profile names must start with a letter or digit and use only letters, digits,
`_`, `-`, or `.`, up to 64 characters. Named tmux sockets allow letters, digits,
`_`, or `-`, up to 64 characters; this is a socket name, not a path.

## Forward syntax

Entries are `local_port:destination_host:destination_port`. For example:

```toml
forwards = ["9000:localhost:3000", "9001:[::1]:3000"]
```

Destinations are resolved and reached from the SSH server. Ports must be
between 1 and 65535; local ports must be unique within a profile. Listeners
always bind to IPv4 `127.0.0.1`, including for IPv6 destinations. Profiles
running together also need distinct local ports. Prefer ports above 1023.

All forwards in a profile share one managed SSH connection. If a listener
cannot be created, that forwarding connection stops and reports an error.
The interactive connection is independent. A ready listener does not prove
that the destination application is running.

## SSH settings

Authentication, identities, hostnames, ports, and ProxyJump come from OpenSSH's
normal configuration. Use `Port` in `~/.ssh/config` for a nonstandard SSH port.
`almost` uses batch authentication and strict host-key checking: verify the host
through ordinary SSH and unlock or load your key before connecting.

`almost` controls connections, terminal allocation, remote commands,
keepalives, and forwarding. SSH configuration `LocalForward`, `RemoteForward`,
and `DynamicForward` entries are cleared for managed connections. Add required
local forwards to the TOML file instead.

## Paths and lifecycle

| Setting | Default |
| --- | --- |
| Configuration | `~/.config/almost/config.toml`, or `$XDG_CONFIG_HOME/almost/config.toml` |
| Private state and logs | `~/.local/state/almost`, or `$XDG_STATE_HOME/almost` |
| Private control and SSH sockets | `/tmp/almost-UID` on macOS; the system temporary directory (honoring `$TMPDIR`) on Linux and Termux |

Socket directories normally use `almost-UID/SCOPE` under the temporary directory.
Longer temporary paths, such as Termux's `$TMPDIR`, use `a-SCOPE` directly under
that directory to fit Unix socket path limits. These directories remain private
to your user. Stop running profiles before changing `$TMPDIR`, and use the same
value for subsequent commands. If the path is still too long, `almost` asks you
to choose a shorter writable absolute `$TMPDIR`.

Use absolute paths for XDG variables. Select a custom configuration with the
global option before the command:

```sh
almost --config /path/to/config.toml init
almost --config /path/to/config.toml connect staging
```

After editing a running profile, stop and restart it using the same configuration:

```sh
almost stop work
almost up work
almost connect work
```

Stop a profile before removing or renaming it in the file. Each configuration
path and profile name identifies separate local processes and remote session
memory. Keep using the same configuration path to retain that memory.
