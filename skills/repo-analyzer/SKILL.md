---
name: repo-analyzer
description: Analyzes an entire repository and produces a multi-page HTML report plus Word and PDF documents, in English and Turkish by default — overview, architecture diagrams (context/containers/components, import graph, data model), feature map, feature execution flows as sequence diagrams, code health (git hotspots, coupling, bus factor, findings) and an onboarding guide with starter/assessment tasks. Use when the user asks to analyze, map, document, audit or "understand" a codebase or repo, wants architecture/feature/flow diagrams, or asks what a repo contains.
---

# Repo analyzer

Pipeline: **deterministic facts → parallel subagents write JSON (source language) → verification → translation →
parity check → deterministic render to HTML, Word and PDF.**
The model never writes HTML or documents; it writes structured JSON that `render_report.py` validates and turns into
pages and documents. This keeps output consistent, cheap to re-render, and checkable (every file reference is verified
against the repo, and translations are checked against the source structure).

`SKILL_DIR` below means the directory containing this SKILL.md. Resolve it to an absolute path before running commands.

## Inputs

- Target repo: the path the user gave, otherwise the current working directory.
- Output dir `<out>`: `<repo>/.repo-analysis/` unless the user asks for another location (the collector skips this folder).
- Languages: **English and Turkish** (`en,tr`) unless the user asks for only one. The first language is the _source_:
  the analysis subagents write it, `repo-report-translator` produces the others. If the user wants a single language, use
  only that one and skip translation. JSON keys and enum values are always English.
- Formats: HTML + Word + PDF unless the user asks otherwise (`--formats`).

Layout:

```
<out>/facts.json, facts_summary.md       deterministic facts (shared)
<out>/data/en/*.json  <out>/data/tr/*.json   agent output per language
<out>/en/  *.html  <repo>-analysis-en.docx  <repo>-analysis-en.pdf
<out>/tr/  *.html  <repo>-analysis-tr.docx  <repo>-analysis-tr.pdf
<out>/index.html                          language chooser
```

## Steps

### 1. Collect facts (no LLM)

```bash
python3 SKILL_DIR/scripts/collect_facts.py <repo> --out <out>
```

Then read `<out>/facts_summary.md` fully. Do not read `facts.json` (large; it is for the renderer).

### 2. Phase 1 — three subagents in parallel (one message, three Agent calls)

Delegate to `repo-architect`, `repo-feature-mapper` and `repo-quality-auditor` (installed with this plugin they are
listed as `repo-analyzer:repo-architect` etc.; use the name the agent list shows). Each task prompt must contain:

```
REPO: <absolute repo path>
FACTS: <out>/facts_summary.md
OUTPUT: <out>/data/<src>/<architecture|features|health>.json
LANG: <src>
Extra focus: <anything the user asked for, else "none">
```

If the subagents are not installed, do the same work yourself sequentially using the schemas in `SKILL_DIR/../../agents/*.md`.

### 3. Phase 2 — flow tracing (fan-out)

Read `<out>/data/<src>/features.json`. Pick the most important features (array order; max 12, skip pure tooling unless
it is the product). Split them into groups of ~3 and start one `repo-flow-tracer` per group **in parallel**, each with its own
`OUTPUT: <out>/data/<src>/flows-<n>.json` and `FEATURES: <out>/data/<src>/features.json, ids: a, b, c`.

### 4. Write `overview.json` yourself

While or after phase 2 runs, write `<out>/data/<src>/overview.json` from FACTS, README and what the subagents reported:

```json
{
  "one_liner": "one sentence: what this repo is",
  "purpose": "3-6 sentences: problem solved, main users, how it is delivered",
  "audience": "who uses/operates it",
  "tech_stack": [
    {
      "name": "FastAPI",
      "category": "backend",
      "role": "HTTP API",
      "refs": ["app/main.py"]
    }
  ],
  "how_to_run": [
    {
      "step": "Install dependencies",
      "command": "pip install -r requirements.txt",
      "ref": "requirements.txt"
    }
  ],
  "reading_order": [
    { "ref": "app/main.py", "why": "wires routers and config" }
  ],
  "glossary": [{ "term": "", "meaning": "", "refs": [] }],
  "starter_tasks": [
    {
      "title": "",
      "type": "bugfix|feature|refactor|test|docs",
      "difficulty": "easy|medium|hard",
      "description": "",
      "refs": ["path:line"],
      "acceptance": ["criterion"]
    }
  ]
}
```

`how_to_run` must come from real manifests/Makefile/Dockerfile/README commands — never invent commands.
`starter_tasks`: 4–6 realistic tasks grounded in actual findings/TODOs/gaps (mix of types and difficulty). They double as
repo-based assessment tasks, so each needs clear acceptance criteria.
Refs format everywhere: `relative/path[:LINE[-END]]`.

### 5. Verify the source language, fix

```bash
python3 SKILL_DIR/scripts/render_report.py --out <out> --langs <src> --check
```

Fix every reported issue in `data/<src>/` (missing keys, paths that don't exist, line numbers out of range,
Mermaid lint). Small things directly; for large gaps re-run the relevant subagent. At most 2 rounds.
Translate only after the source is clean — otherwise every fix has to be made twice.

### 6. Translate (skip for a single language)

For each other language, start `repo-report-translator` subagents **in parallel** (one message), e.g. three instances:
`overview.json, architecture.json` / `features.json, health.json` / all `flows-*.json`. Each prompt:

```
SOURCE: <out>/data/<src>
TARGET: <out>/data/<lang>
FILES: <file names>
LANG: <lang>
```

### 7. Verify everything, render

```bash
python3 SKILL_DIR/scripts/render_report.py --out <out> --check          # all languages + translation parity
python3 SKILL_DIR/scripts/render_report.py --out <out>                  # html + docx + pdf for every language
```

Parity issues mean the translation changed something it must not (ids, refs, enums, commands, list lengths, diagram
skeleton): fix the target file to match the source. If the render prints `[docs] skipped Word/PDF: missing ...`, tell the
user the exact install command it printed; HTML is still produced. For offline machines add `--mermaid-js <path to mermaid.min.js>`.
`--logo <png>` puts a brand logo on the Word/PDF cover and every page header. Pass it only when the user gives a logo
or names a brand (see Branding).

## Branding

Brands live on the user's machine, not in this plugin: `$REPO_ANALYZER_BRANDS`, else `~/.claude/repo-analyzer/brands/`.

```
brands/<brand-name>/
  logo.png          used on the Word/PDF cover and page headers
  instruction.md    when to use this brand, plus any wording/tone rules for the report text
```

- The user names a brand ("use the ananas-studio brand") → use that folder. If it does not exist, list the folders
  that do and ask.
- No brand named → read each `instruction.md`; use a brand only if its instruction says it applies to this repo
  (for example by path or organization). Otherwise use no brand.
- Read the chosen `instruction.md` before step 4 and follow it in the text you and the subagents write.
- Render with `--brand <brand-name>` (it picks `logo.png`). Say in the report-back which brand was used.

### 8. Report back

Tell the user where `<out>/index.html` and the `.docx` / `.pdf` files are, the 3 most important findings, and anything
that could not be determined. Keep it short.

## Rules

- Never read `.env`, private keys or credential files; never put secret values in any output.
- Do not modify the target repository. Only write inside the output dir.
- Mermaid syntax errors are shown inline in the report with the source; `--check` catches the common ones.
- For very large repos (> ~5k files), tell subagents which top-level directories to focus on, based on FACTS.
