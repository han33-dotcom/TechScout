# Contributing to TechScout

Claim a ready issue on the [project board](https://github.com/users/han33-dotcom/projects/2). Preserve existing work and branch from current main or the issue’s declared base. Read the [project rules](AGENTS.md).

## Setup and tests

Use Python 3.11 for the shared baseline. Create a virtual environment at the repository
root and install the current pinned backend requirements.

Windows PowerShell:
```powershell
py -3.11 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

macOS/Linux:
```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

Ordinary tests use offline fixtures. Use the [local development guide](docs/local-development.md) for PostgreSQL, frontend setup and recovery. Keep credentials and local configuration private.

## Submit a change

Open a focused PR and link the issue. Record checks, limitations and observed results. For buyer-facing changes, identify the [acceptance scenarios](docs/buyer-acceptance-matrix.md) and show what the buyer saw, on which revision, using live or fixture evidence. Inspect the diff and fix material findings. The author can merge once the required checks pass on the current branch, then verify the result and close the issue. [Team workflow](docs/team-workflow.md)

Coding assistants follow [AGENTS.md](AGENTS.md). [CLAUDE.md](CLAUDE.md) imports those same rules for Claude Code. [Collaborator setup](docs/collaborator-setup.md) explains how to verify a checkout.
