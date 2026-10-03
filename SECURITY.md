# Security

CV Tailor handles a CV, application answers and a local bearer token, so please report problems privately.

- Report vulnerabilities by opening a private security advisory on the repository (Security tab), not a public issue.
- Scope: the local companion, the pairing and authentication flow, DOCX handling, and the extension's autofill.
- The companion is designed to listen on loopback only and to require a bearer token. Anything that exposes it beyond `127.0.0.1`, lets a web page call it, or lets agent output write outside the workspace is in scope.

Never commit your workspace (`cv-tailor.json`, `data/`). The repository's `.gitignore` excludes them.
