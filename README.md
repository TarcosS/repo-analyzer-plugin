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

```bash
claude plugin marketplace add TarcosS/repo-analyzer-plugin
claude plugin install repo-analyzer@repo-analyzer
```

Update later with `claude plugin marketplace update repo-analyzer`.

## Requirements

- Python 3.11+ (HTML report needs only the standard library).
- Word/PDF output (optional): `pip install python-docx pypdf playwright` and `playwright install chromium`.
  Without them the HTML report is still produced and the renderer prints the missing install command.
- `git` on PATH for hotspot / coupling / bus-factor facts.

## Use

Ask Claude Code: "analyze this repo" or "map the architecture of <path>". Output goes to `<repo>/.repo-analysis/`;
open `index.html`. The target repository is not modified.

## Branding

Brands stay on your machine; the plugin ships none. Put each brand in its own folder:

```
~/.claude/repo-analyzer/brands/        (or set REPO_ANALYZER_BRANDS)
  acme/
    logo.png          cover + page header logo in Word/PDF
    instruction.md    when to use this brand, and wording/tone rules for the report
```

Say "use the acme brand" and the report uses that folder. With no brand named, a brand is used only when its
`instruction.md` says it applies to the repo being analyzed (for example by path or organization).
