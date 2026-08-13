# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.x.y   | Latest release only |

Sentinel is in pre-1.0 development. Security fixes are applied to the
latest release only. Once the project reaches 1.0, this table will be
updated to reflect the long-term support policy.

## Reporting a Vulnerability

If you discover a security vulnerability in Sentinel, please report it
responsibly. **Do not open a public GitHub issue for security
vulnerabilities.**

### How to report

Send an email to **security@suse.com** with:

- A description of the vulnerability
- Steps to reproduce the issue
- The affected version(s)
- Any potential impact assessment

### What to expect

- **Acknowledgment**: we will acknowledge your report within 3 business
  days
- **Assessment**: we will assess the vulnerability and determine its
  severity within 10 business days
- **Resolution**: we will work on a fix and coordinate disclosure with
  you
- **Credit**: we will credit reporters in the release notes (unless you
  prefer to remain anonymous)

### Scope

The following are in scope for security reports:

- Authentication and authorization bypasses
- SQL injection, command injection, or other injection vulnerabilities
- Sensitive data exposure (credentials, PII, internal infrastructure
  details)
- Cross-site scripting (XSS) or cross-site request forgery (CSRF) in
  API responses
- Denial of service vulnerabilities in the API or background tasks
- Insecure default configurations
- Dependency vulnerabilities with a demonstrated exploit path

### Out of scope

- Vulnerabilities in dependencies without a demonstrated impact on
  Sentinel
- Issues that require physical access to the server
- Social engineering attacks
- Denial of service attacks that require significant resources

## Security Measures

Sentinel employs the following security practices:

- **Static analysis**: ruff, bandit, and mypy (strict mode) run on
  every pull request
- **Dependency auditing**: pip-audit checks for known vulnerabilities
  in dependencies
- **Container scanning**: Trivy scans the Docker image weekly
- **Secret detection**: gitleaks runs in pre-commit hooks to prevent
  accidental secret commits
- **Type safety**: strict static type checking catches type-related
  bugs before runtime
