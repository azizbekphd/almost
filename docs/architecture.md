# Processes and private state

The CLI launches a detached supervisor with a private Unix control socket and
an exclusive per-profile lock. The supervisor owns one OpenSSH master and adds
only the configured forwards through that master's control socket. A separate
foreground SSH process inherits your terminal directly; there is no terminal
emulator or keystroke logger in the app.

Two additive runtime tmux hooks remember the tracked client's session ID. IDs
survive renames; the memory disappears with the tmux server, preventing reused
IDs after a restart from pointing at an unrelated session. Hooks check both
client PID and creation time. Cleanup runs from the local app, not from a tmux
detach hook, for compatibility with tmux 3.2a. If cleanup cannot reach the server,
the next attachment replaces the old hooks. Existing hooks and tmux configuration
files are preserved.

Private state and rotating diagnostic logs live under `~/.local/state/almost`.
Control sockets live under `/tmp/almost-UID` to fit macOS socket path limits.
Directories use mode 0700 and state files use 0600. Stale PIDs are never used to
kill processes. After a supervisor crash, `almost up` or `almost stop` reclaims
only SSH masters in the app's own private socket directory.
