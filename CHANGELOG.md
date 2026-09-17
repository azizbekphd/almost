# Changelog

Release versions follow `MAJOR.MINOR.PATCH`; the configuration format has its own
`version = 1`. During 0.x, minor releases may introduce breaking changes,
which will be described here.

## [Unreleased]

- Add `almost update` to download and verify the latest stable GitHub Release
  for offline installations, preserving custom prefixes and configuration.
  `--source PATH` installs a local checkout or extracted release without network
  access. Failed replacement packages restore the previous installed package.
- Fix Termux startup failing with permission denied for `/tmp/almost-UID` by
  honoring the system temporary directory outside macOS. Compact socket paths
  also accommodate Termux's longer temporary directory and OpenSSH's suffix.

## [0.1.0] - 2026-09-16

Initial public release.

- Background OpenSSH tunnels with keepalives, bounded retries, and actionable
  paused states for authentication, host-key, and forwarding setup errors.
- Interactive remote tmux reconnection with session memory across switches and
  renames, preserving existing tmux hooks and configuration.
- Named profiles, loopback-only local forwards, IPv6 destinations, and named
  remote tmux sockets.
- `init`, `connect`, `up`, `status`, `logs`, `doctor`, and `stop` commands,
  machine-readable status, custom configuration files, and XDG paths.
- Offline installation without pip or runtime dependencies, release wheels,
  source archives, and SHA-256 checksums.
- Public setup, configuration, recovery, upgrade, and removal documentation,
  an MIT license, and automated package, process, and isolated tmux checks.

Requires macOS or Linux, local Python 3.11+ and OpenSSH, and remote tmux 3.2+.
Native Windows is unsupported; WSL is not covered by CI. Reconnection cannot
preserve established TCP connections or restore programs lost in a reboot.

[0.1.0]: https://github.com/azizbekphd/almost/releases/tag/v0.1.0
