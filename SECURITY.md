# Security policy

Security fixes are provided for the latest release. Update older installations
before reporting a problem when feasible.

Report vulnerabilities privately using
[GitHub's reporting form](https://github.com/azizbekphd/almost/security/advisories/new).
Include affected versions, reproduction steps, and expected impact. Do not
post exploit details or sensitive diagnostics in public issues.

`almost` relies on OpenSSH for encryption, host verification, and authentication.
It uses strict host-key verification, non-interactive authentication, and
loopback-only listeners. It does not collect passwords, passphrases, private
keys, or terminal keystrokes. Processes running as your user can access your
tunnels and private state; this utility does not isolate them from one another.

Diagnostics may include private values emitted by OpenSSH. Redact them before
sharing logs. Never include credentials or private key material in a report.
