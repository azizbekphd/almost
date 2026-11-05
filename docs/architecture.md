# Processes and private state

The CLI launches a detached supervisor with a private Unix control socket and
an exclusive per-profile lock. The supervisor owns one OpenSSH master and adds
only the configured forwards through that master's control socket. A separate
foreground SSH process inherits terminal input directly. When a local clipboard
tool is available, its output is relayed through a bounded OSC 52 parser that
sends tmux copies to the local clipboard and passes other output through.
Otherwise SSH inherits terminal output directly. There is no terminal emulator
or keystroke logger in the app.

The clipboard handler advertises clipboard support for the managed tmux client
with `-T clipboard`. If the remote server has `set-clipboard off`, attachment
enables `external` at runtime; an existing `on` setting is kept. Clipboard tools
run only locally, and no tmux configuration file or copy binding is changed.
Malformed, oversized, or unsuccessful clipboard messages pass through to the
terminal. Clipboard contents are not included in diagnostics or state files.

Two additive runtime tmux hooks remember the tracked client's session ID. IDs
survive renames; the memory disappears with the tmux server, preventing reused
IDs after a restart from pointing at an unrelated session. Hooks check both
client PID and creation time. Cleanup runs from the local app, not from a tmux
detach hook, for compatibility with tmux 3.2a. If cleanup cannot reach the server,
the next attachment replaces the old hooks. Existing hooks and tmux configuration
files are preserved.

Private state and rotating diagnostic logs live under `~/.local/state/almost`.
Control sockets live under `/tmp/almost-UID` on macOS to fit its socket path
limits. Linux and Termux use the system temporary directory, honoring `$TMPDIR`.
Long temporary paths use a compact per-profile directory and shorter SSH socket
names, with room reserved for OpenSSH's temporary socket suffix.
Directories use mode 0700 and state files use 0600. Stale PIDs are never used to
kill processes. After a supervisor crash, `almost up` or `almost stop` reclaims
only SSH masters in the app's own private socket directory.
