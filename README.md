# coscribe

[![CI](https://github.com/OICWS/coscribe/actions/workflows/office-agent-ci.yml/badge.svg)](https://github.com/OICWS/coscribe/actions/workflows/office-agent-ci.yml)

A local, user-friendly assistant for office work. It reads and writes your
files, produces Word, Excel, PowerPoint and PDF documents, uses the web and
your tools, and can repeat work on a schedule. It needs only a model API key.

Website: https://oicws.github.io/coscribe/

- **Documents first.** High-quality `.pptx`, `.docx` and `.xlsx` output, checked
  by rendering the pages.
- **Your models.** Anthropic, OpenAI and Gemini directly, and any
  OpenAI-compatible API (DeepSeek, Kimi, GLM, local runners).
- **Connectors and skills.** Hosted services you sign in to once in your own
  browser, and skills (folders of instructions and files) you can read before
  adding.
- **You stay in control.** Permission modes, approvals for risky tools, and
  per-tool settings for each connector.

## Run it

```bash
cd office-agent
python3 -m venv .venv && source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev,web]"
pip install -U "google-genai>=2.13.0,<2.14.0"
cp .env.example .env                                   # add a model API key
(cd frontend && npm install && npm run build)
coscribe-web                                           # the web UI on http://127.0.0.1:8000
```

[`office-agent/README.md`](office-agent/README.md) has the full setup and the
features in detail. The Windows desktop app is built from
[`office-agent-desktop/`](office-agent-desktop/).

## Repository

| Path | |
|---|---|
| `office-agent/` | The Python backend, the React frontend and the tests |
| `office-agent-desktop/` | The Electron desktop shell |
| `docs/` | Engineering docs: the plan, decisions, the log of what shipped |
| `site/` | The project website (GitHub Pages) |

## Contributing

Developers and AI coding sessions: start with [`CLAUDE.md`](CLAUDE.md) (a map
of the code and what to read first) and [`CONTRIBUTING.md`](CONTRIBUTING.md).
Before every change run `cd office-agent && bash scripts/check.sh`.

Security issues: see [`SECURITY.md`](SECURITY.md). Behaviour in the project:
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

## License

[MIT](LICENSE)
