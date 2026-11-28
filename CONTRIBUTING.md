# Contributing to TechScout

Pick an issue on the [project board](https://github.com/users/han33-dotcom/projects/3) and follow the [team workflow](docs/team-workflow.md). Read the [project rules](AGENTS.md).

## Setup and tests

Use Python 3.11 for the shared baseline. Create a virtual environment at the repository root and install the pinned requirements.

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

Ordinary tests use offline fixtures and never call retailers or paid models. Live scout runs and evaluations are described in the [scout agent guide](docs/scout-agent.md). Keep credentials and local configuration private.

## Submit a change

Open a focused PR that links its issue. Record what changed, the checks you ran, observed results and limitations. For changes to scout behavior, include before-and-after results on the development requests, and never tune on the evaluation requests. Inspect your own diff and fix material findings, then merge once the required checks pass.

Coding assistants follow [AGENTS.md](AGENTS.md). [CLAUDE.md](CLAUDE.md) imports those same rules.
