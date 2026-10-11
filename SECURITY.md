<!-- Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved. -->
# Security policy

Toolsmith writes code with a language model and runs it. Security is the core of the product, and reports are welcome.

## Reporting a vulnerability

Please report vulnerabilities **privately** through GitHub: *Security* tab → *Report a vulnerability* in this repository. Do not open a public issue, pull request or discussion for a suspected vulnerability.

Include what you found, how to reproduce it, and the impact you expect. We will acknowledge a report within 5 working days and agree on a disclosure date with you. Please give us 90 days, or until a fix is released, before public disclosure.

Especially interesting: escapes from the sandbox (code running outside its job UID or reaching the network), crossing between tenants, identity spoofing past oauth2-proxy, prompt injection that gets a weakened rule or a dangerous tool installed without a human noticing, and secrets in logs or responses.

## Supported versions

Only the latest commit of `main` is supported. There are no backported fixes.

## How Toolsmith protects itself

- **Generated code** runs in a sandbox container without any network, as a private unprivileged UID per job with no capabilities, under CPU, memory, process and file limits, after static checks and with Python-level guards as a second layer. Details: `Docs/DECISIONS.md` (D-005, D-006) and `Docs/THREAT_MODEL.md`.
- **Model output is a proposal.** A deterministic gatekeeper enforces `backend/policy/policy.yaml`, tests every tool, validates every rule on independent data, and a human approves before anything is installed.
- **Identity** comes only from tokens signed by your identity provider (via oauth2-proxy) or from hashed API keys. Every tenant's data, tools and audit chain are separate.
- **Audit:** every approval and every sandbox execution is recorded in a per-tenant SHA-256 hash chain.
- **Supply chain:** hash-pinned dependencies, digest-pinned base images, vulnerability scans, secret scanning of the full Git history and SBOMs in CI.

## Known limitations

See `Docs/THREAT_MODEL.md` (residual risks) and the "Known limitations" section of the README.
