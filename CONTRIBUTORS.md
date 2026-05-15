# Contributing to Claude Usage Tracker

Thank you for your interest in contributing. This is a personal utility project
and contributions are welcome in any form — bug reports, feature ideas, code, or
documentation improvements.

---

## How to contribute

### Reporting bugs

Open an issue on GitHub with:
- Your OS and Python version
- Which browser you use for claude.ai
- Steps to reproduce the problem
- What you expected vs. what happened
- Relevant output from the terminal or browser console

### Suggesting features

Open an issue describing the feature and the problem it solves. A short description
of why it's useful to other users is more persuasive than a long spec.

### Submitting code

1. Fork the repository and create a branch from `main`
2. Make your changes — keep commits focused and descriptive
3. Test manually: run `./run.sh`, sync, and verify the dashboard looks right
4. Open a pull request with a clear description of what changed and why

There are no automated tests at present, so please describe how you verified
your change works.

### Documentation

Corrections to the README, CHANGELOG, or inline comments are always welcome.
Open a PR directly for small fixes; open an issue first for larger restructuring.

---

## Code style

- Python: follow PEP 8; prefer clarity over cleverness
- JavaScript: vanilla ES2020+, no build step
- Keep dependencies minimal — the agent (`sync_agent.py`) must remain stdlib-only

---

## AI assistance

AI-assisted contributions are welcome. If you used an AI tool to help write or
review your code, you're encouraged (but not required) to note it in your PR
description. The quality and correctness of the contribution matters more than
how it was produced.

If an AI tool co-authored a commit, the standard convention used in this repo is:

```
Co-Authored-By: Claude <noreply@anthropic.com>
```

---

## Security issues

Please do not open public issues for security vulnerabilities. Instead, contact
the maintainer directly at **jim@legalmed.pro** so the issue can be assessed and
addressed before public disclosure.

---

## Contributors

| Name | Role |
|------|------|
| [Jim Dawdy](https://github.com/jimdawdy-hub) | Author & maintainer |

Prior art this project builds on:

| Project | Contribution |
|---------|-------------|
| [phuryn/claude-usage](https://github.com/phuryn/claude-usage) | JSONL scanning approach and core parsing logic |
| [IgniteStudiosLtd/claude-usage-tool](https://github.com/IgniteStudiosLtd/claude-usage-tool) | Browser-based usage page scraping concept |

---

## License

By contributing, you agree that your contributions will be licensed under the
same [MIT License](LICENSE) that covers this project.
