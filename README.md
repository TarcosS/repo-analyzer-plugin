# repo-analyzer

A Claude Code plugin that analyzes a whole repository and produces a multi-page HTML report plus Word and PDF
documents: overview, architecture diagrams, feature map, execution flows (sequence diagrams), code health
(git hotspots, coupling, bus factor, findings) and an onboarding guide. English and Turkish by default.

## Contents

| Path                    | What                                                                                |
| :---------------------- | :---------------------------------------------------------------------------------- |
| `skills/repo-analyzer/` | The skill and its scripts (fact collector, renderer, Word/PDF export)               |
| `agents/`               | Five subagents: architect, feature mapper, flow tracer, quality auditor, translator |

## Install

From a local clone:

```bash
claude plugin marketplace add /path/to/repo-analyzer-plugin
claude plugin install repo-analyzer@repo-analyzer
```

From a Git host, replace the path with the repository URL or `owner/repo`.

## Requirements

- Python 3.11+ (HTML report needs only the standard library).
- Word/PDF output (optional): `pip install python-docx pypdf playwright` and `playwright install chromium`.
  Without them the HTML report is still produced and the renderer prints the missing install command.
- `git` on PATH for hotspot / coupling / bus-factor facts.

## Use

Ask Claude Code: "analyze this repo" or "map the architecture of <path>". Output goes to `<repo>/.repo-analysis/`;
open `index.html`. The target repository is not modified.
