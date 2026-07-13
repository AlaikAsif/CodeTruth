# Security Policy

## Supported Versions

CodeTruth is pre-1.0 and moving fast. Security fixes are made against the
latest `0.x` release on `main`; older `0.x` releases are not backported.

| Version | Supported          |
| ------- | ------------------ |
| 0.7.x   | :white_check_mark: |
| < 0.7   | :x:                |

Once CodeTruth reaches 1.0, this table will be updated to track supported
major/minor lines with a defined backport window.

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Instead, report privately via one of:

- [GitHub Security Advisories](https://github.com/AlaikAsif/CodeTruth/security/advisories/new)
  for this repository (preferred)
- Email: security reports can be sent to the maintainer at the address on
  the [GitHub profile](https://github.com/AlaikAsif)

When reporting, please include:

- A description of the vulnerability and its potential impact
- Steps to reproduce, or a proof-of-concept if available
- The affected version/commit

### What to expect

- **Acknowledgement** within 3 business days of your report.
- **Status updates** at least every 7 days while the issue is being
  investigated.
- If accepted, a fix is prioritized and released as soon as reasonably
  possible; you'll be credited in the release notes unless you prefer to
  remain anonymous.
- If declined (e.g. not a security issue, or out of scope), you'll receive
  an explanation of the reasoning.

### Scope

CodeTruth is a static-analysis tool that reads source code to recommend
(never apply) deletions. Relevant vulnerability classes include:

- Arbitrary code execution while scanning untrusted repositories
- Path traversal or unsafe file writes via `.codetruth.toml` or scan input
- Issues in the MCP server (`codetruth[mcp]`) that could let an agent act
  outside its intended, advisory-only scope

Denial-of-service via pathological input on very large repos is a known
tradeoff of static analysis and is lower priority unless it causes crashes
or resource exhaustion disproportionate to input size.
