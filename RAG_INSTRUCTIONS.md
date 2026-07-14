# Dobby RAG — Knowledge Base Authoring Instructions

Use this document as the **Cursor prompt** when working in any project repo to produce markdown files for Dobby's RAG knowledge base on [nikhilbansal.dev](https://nikhilbansal.dev).

Copy everything under **[Cursor prompt](#cursor-prompt)** into a new Cursor chat in the target project. Fill in the [Project-specific inputs](#project-specific-inputs) at the bottom before sending.

---

## Purpose

Dobby is a portfolio chat assistant. Its knowledge comes from structured markdown files that are:

- **Ingested** with header-based chunking (`##` sections become chunks)
- **Filtered** using YAML frontmatter metadata (project, category, stack, employer, etc.)
- **Retrieved** at query time to ground answers — Dobby must not invent projects, features, or contact details

Write for **retrieval**: concrete facts, decisions, and outcomes — not marketing fluff.

---

## Folder structure

```
knowledge/
├── RAG_INSTRUCTIONS.md          ← this file (portfolio repo only)
├── nestiq/
│   ├── information/
│   │   ├── index.md             ← simple project: one file here
│   │   ├── architecture.md      ← complex project: multiple files
│   │   ├── technical-decisions.md
│   │   ├── faq.md
│   │   └── media.md
│   └── assets/
│       └── projects/nestiq/     ← screenshots, diagrams
├── reva/
│   ├── information/
│   └── assets/
├── experience/                  ← optional: CBA, Hevo, etc.
└── about/                       ← optional: bio, services, contact
```

### Naming rules

| Item | Convention |
|------|------------|
| Project slug | lowercase kebab-case (`neo-agentic-ecosystem`, `ipl-fan-vote`) |
| Markdown docs | `knowledge/<slug>/information/<file>.md` |
| Simple project | `knowledge/<slug>/information/index.md` only |
| Complex project | `knowledge/<slug>/information/*.md` (see multi-file rules) |
| Images | `knowledge/<slug>/assets/` (subfolders optional, e.g. `assets/projects/<slug>/`) |

Keep slugs aligned with the portfolio UI where possible (see `components/projects-grid.tsx`).

---

## File format

Every `.md` file has **YAML frontmatter** + a **markdown body** with `#` title and `##` sections.

### Required frontmatter (every file)

```yaml
---
# Identity
id: <slug>                      # same across all files for this project
name: <Human-readable name>
slug: <slug>
file: index | architecture | features | faq | <custom>

# Classification
year: <YYYY or null>
category: web | app | side-quest | platform | enterprise-ai | other
tags: [<keyword>, ...]
employer: null | <company name>
role: solo-builder | founding-engineer | team-lead | contributor
status: live | archived | internal | in-progress

# Discovery
one_liner: <Single sentence — what this project is>
stack: [<tech>, ...]            # flat list, deduplicated across project
links:
  - label: <label>
    url: <url>

# RAG / indexing
doc_type: project               # project | experience | about | faq
visibility: public              # public | internal
related_files:                  # sibling md files in this project folder
  - architecture.md
updated_at: <YYYY-MM-DD>
---
```

### Body structure

- One `#` title per file (e.g. `# NestIQ`, `# NestIQ — Architecture`)
- `##` = main sections → **these become RAG chunks**
- `###` = subsections within a chunk (or split into separate chunks for FAQ)

---

## Section menu — pick what fits

Not every project needs every section. Include only what applies.

### Core (usually in `index.md`)

| Section | When to include |
|---------|-----------------|
| `## Overview` | Always |
| `## Problem` | Always |
| `## Solution` | Always |
| `## Key Features` | Distinct user-facing capabilities exist |
| `## My Contribution` | Team or employer project — clarify what *Nikhil* owned |
| `## Outcomes & Metrics` | Measurable or qualitative results exist |
| `## Stack` | Always — group by Frontend / Backend / Data / Infra / AI |
| `## Links & Demos` | Live URL, repo, demo, social |
| `## Related Projects` | Ties to other portfolio work |

### Optional deep-dive sections

| Section | When to include |
|---------|-----------------|
| `## Architecture` | Multi-service or non-trivial data flow |
| `## Technical Decisions` | Tradeoffs worth explaining in interviews |
| `## Data Model` | Non-trivial schema or entities |
| `## API & Integrations` | External APIs, webhooks, third-party services |
| `## AI / ML` | LLMs, RAG, agents, embeddings, prompts |
| `## Auth & Security` | Auth flows, RBAC, sensitive domains |
| `## Deployment & Infra` | Docker, cloud, CI/CD, env setup |
| `## Screenshots & Media` | Visual walkthrough helps understanding |
| `## FAQ` | Anticipated visitor / recruiter questions |
| `## Timeline & Phases` | Long-running or phased delivery |
| `## Limitations & Future Work` | Honest scope boundaries |

**Rule:** If a section exceeds ~400 words, give it its own file or break into `###` subsections.

---

## Multi-file projects

Use **one `index.md` + companion files** when the project is complex:

```
knowledge/neo-agentic-ecosystem/
├── information/
│   ├── index.md                 # Overview, Problem, Solution, Features, Stack, Links
│   ├── architecture.md          # Architecture, data flow, agent design, integrations
│   ├── technical-decisions.md
│   ├── faq.md                   # ## Frequently Asked Questions, then ### per Q&A
│   └── media.md                 # Optional: screenshots with rich descriptions
└── assets/
    └── ...
```

Rules:

- `id`, `slug`, `name`, `stack`, `one_liner` stay **consistent** across all files
- Each file has a unique `file:` value in frontmatter
- Each file lists siblings in `related_files`
- `index.md` includes a short `## Documentation Map` listing companion files
- Don't duplicate full sections — index gets a 2–3 sentence summary + pointer to the deep-dive file

---

## Images

Images are stored separately; **text descriptions** are what RAG retrieves.

1. Store files under `knowledge/<slug>/assets/` (optionally nested, e.g. `knowledge/nestiq/assets/projects/nestiq/`)
2. Reference in markdown with descriptive alt text **and** a caption paragraph
3. Use repo-root paths in frontmatter `media.path` and image refs (e.g. `knowledge/nestiq/assets/projects/nestiq/pulse-dashboard.png`)
4. Reuse `public/projects/` assets in the portfolio repo when the same image applies

```markdown
## Screenshots & Media

### Pulse — neighbourhood sentiment

![Pulse dashboard showing neighbourhood news cards and sentiment tags for Koramangala](knowledge/nestiq/assets/projects/nestiq/pulse-dashboard.png)

The Pulse view aggregates local news and discussion signals so renters can compare neighbourhood vibe before shortlisting.
```

Optional structured block in frontmatter or a `media:` section:

```yaml
media:
  - id: pulse-dashboard
    path: knowledge/nestiq/assets/projects/nestiq/pulse-dashboard.png
    type: screenshot
    caption: Pulse neighbourhood sentiment view
    describes: Shows news cards and sentiment for Koramangala
```

---

## Writing rules for RAG quality

1. **Lead with facts** — names, technologies, responsibilities, scale, dates
2. **One idea per paragraph** — easier to chunk and retrieve
3. **Use exact product/feature names** as users would ask ("Pulse", "My Hub")
4. **FAQ format** — wrap all Q&As in a `## Frequently Asked Questions` section; each question as `### Question?` with a 2–4 sentence answer (split on `###` at ingest for one FAQ = one chunk)
5. **Screenshots** — never bare `![screenshot](url)`; always alt text + caption paragraph
6. **Stand-alone sections** — each `##` should make sense without reading prior sections
7. **No secrets** — no API keys, credentials, or internal URLs unless `visibility: internal`
8. **No invention** — if unverified, add `<!-- TODO: confirm with Nikhil -->`
9. **No duplication** — don't repeat the same paragraph across files

---

## Chunking & metadata (for awareness)

At ingest time:

- **Primary split:** each `##` section → one chunk
- **Secondary split:** sections > ~400–600 tokens → split on `###` or paragraphs with 10–15% overlap
- **FAQ:** split on `###` under `## Frequently Asked Questions` — one Q&A pair per chunk
- **Frontmatter:** attached as metadata on every chunk from that file, not embedded separately

Each chunk carries metadata like:

```json
{
  "doc_type": "project",
  "project_id": "nestiq",
  "project_name": "NestIQ",
  "year": 2024,
  "category": "web",
  "tags": ["full-stack", "data-intensive"],
  "stack": ["React", "Flask", "Supabase"],
  "section": "architecture",
  "source_file": "knowledge/nestiq/information/architecture.md",
  "file": "architecture"
}
```

---

## Cursor prompt

Copy from here into a Cursor chat in the **target project repo**:

---

### Task: Create Dobby RAG knowledge-base markdown for this project

You are helping build structured documentation for **Dobby**, a RAG-powered portfolio assistant on nikhilbansal.dev. Follow the authoring standard in the Dobby knowledge base spec (header-based chunking, YAML frontmatter, project-wise metadata).

#### Your job

1. Explore this codebase thoroughly (README, architecture, key modules, configs, deploy setup, APIs, data models, screenshots).
2. Produce one or more markdown files following the standard below.
3. Write for retrieval: concrete facts, decisions, and outcomes — not marketing fluff.
4. Do **not** invent metrics, features, or stack items. If unclear, add `<!-- TODO: confirm with Nikhil -->`.
5. Do **not** include secrets (API keys, internal URLs, credentials).

#### Output location & naming

```
knowledge/<slug>/information/
knowledge/<slug>/assets/            ← images
```

- `<slug>` = lowercase kebab-case (e.g. `nestiq`, `neo-agentic-ecosystem`)
- **Simple projects:** `knowledge/<slug>/information/index.md`
- **Complex projects:** split into multiple files under `information/` (index + architecture, faq, etc.)

#### Required frontmatter (every file)

```yaml
---
id: <slug>
name: <Human-readable name>
slug: <slug>
file: index | architecture | features | faq | <custom>
year: <YYYY or null>
category: web | app | side-quest | platform | enterprise-ai | other
tags: [<keyword>, ...]
employer: null | <company name>
role: solo-builder | founding-engineer | team-lead | contributor
status: live | archived | internal | in-progress
one_liner: <Single sentence>
stack: [<tech>, ...]
links:
  - label: <label>
    url: <url>
doc_type: project
visibility: public
related_files: []
updated_at: <YYYY-MM-DD>
---
```

#### Section menu — include only what fits

**Core (index.md):** Overview, Problem, Solution, Key Features, My Contribution, Outcomes & Metrics, Stack, Links & Demos, Related Projects

**Optional deep-dives (own file or extra sections):** Architecture, Technical Decisions, Data Model, API & Integrations, AI / ML, Auth & Security, Deployment & Infra, Screenshots & Media, FAQ, Timeline & Phases, Limitations & Future Work

If a section exceeds ~400 words → own file or `###` subsections.

#### Multi-file rules (complex projects)

- Consistent `id`, `slug`, `name`, `stack`, `one_liner` across all files
- Unique `file:` per document
- `index.md` has `## Documentation Map` listing companion files
- No duplicated full sections across files

#### Writing rules

- Facts first; exact feature/product names
- Each `##` section stands alone
- FAQ: `## Frequently Asked Questions` wrapper, then `### Question?` + 2–4 sentence answer per item
- Screenshots: descriptive alt + caption paragraph (store under `knowledge/<slug>/assets/`)
- Internal projects: `visibility: internal`

#### Deliverables

1. **File tree** of what you created
2. **Full markdown contents** (ready to save)
3. **TODO list** — anything unverified from the repo
4. **Suggested FAQ questions** you couldn't answer yet

#### Project-specific inputs

Fill in before running (or infer from repo):

- **Project name:**
- **Slug:**
- **Year:**
- **Category:**
- **Employer (if any):**
- **Live URL:**
- **Complexity:** simple (single index.md) | complex (multi-file)
- **Extra sections this project needs:**
- **Off-limits for public docs:**

Start by scanning the repo, then produce the markdown files.

---

## Follow-up prompts

Use these in the same repo after the first pass.

### Generate FAQ only

```
Read knowledge/<slug>/information/index.md (and companion files if they exist).
Create knowledge/<slug>/information/faq.md following the Dobby RAG standard.
Use ## Frequently Asked Questions as the wrapper section, then 8–12 ### Question? subsections with 2–4 sentence answers.
Cover: what it does, stack, scale, Nikhil's role, how to try it, comparisons to similar projects.
Mark unverified items with <!-- TODO: confirm with Nikhil -->.
```

### Generate architecture doc only

```
Read knowledge/<slug>/information/index.md and explore this codebase.
Create knowledge/<slug>/information/architecture.md following the Dobby RAG standard.
Cover: system components, data flow, key integrations, deployment topology, and agent/AI design if applicable.
Use mermaid diagrams where helpful. Keep each ## section under ~400 words.
```

### Expand an existing doc

```
Read knowledge/<slug>/information/ and identify gaps for RAG retrieval.
Add missing ## sections or companion files. Do not duplicate existing content.
List what you added and what still needs manual input from Nikhil.
```

---

## Checklist before indexing

- [ ] Frontmatter complete on every file
- [ ] `slug` matches folder name
- [ ] `stack` and `one_liner` consistent across multi-file projects
- [ ] Core sections present (Overview, Problem, Solution, Stack) or marked N/A
- [ ] Screenshots have alt text + caption paragraphs
- [ ] FAQ has `## Frequently Asked Questions` wrapper and 3+ real visitor questions (or dedicated faq.md)
- [ ] No secrets; `visibility: internal` set where needed
- [ ] TODOs listed for anything unverified
