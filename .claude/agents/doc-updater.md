---
name: Doc Updater
description: Reference checklist for updating project documentation after code changes
tools: Read, Glob, Grep
model: sonnet
---

# Doc Updater — Baseline Scrum Team

This file is a **reference checklist** used by the main Claude Code session to update documentation after code changes. It is not run as a separate agent — the main session reads this checklist and applies edits directly.

## Documentation Files & When to Update

### 1. CLAUDE.md (this project's context file)

Update when:
- [ ] New routes added or removed — update Project Structure if significant
- [ ] New data models or model changes — update Data Models section
- [ ] New environment variables — update Environment Variables section
- [ ] Architecture decisions made — update Key Architectural Decisions
- [ ] New dev routes added — update Dev Routes section
- [ ] Product philosophy refined — update Product Philosophy section
- [ ] Known issues discovered or resolved — update Known Issues section
- [ ] Current status changes (new users, new deployment info) — update Current Status

### 2. Baseline Files/TECHNICAL_README.md

Update when:
- [ ] New routes added or removed
- [ ] Data models change (new tables, new columns, changed relationships)
- [ ] Dependencies change (new packages in requirements.txt)
- [ ] Environment variables added or changed
- [ ] Deployment process changes
- [ ] Security model changes
- [ ] API integrations added or changed

### 3. Baseline Files/BACKLOG.md

Update when:
- [ ] Backlog items completed — mark with checkmark and date
- [ ] New bugs discovered during development — add to appropriate section
- [ ] New feature ideas identified — add to backlog
- [ ] Priorities shift — reorder items
- [ ] Decisions made — add to Decision Log section

### 4. templates/help.html (in-app user guide — edit directly)

Update when:
- [ ] User-facing workflows change
- [ ] New features are added that users need to know about
- [ ] UI changes that affect how users interact with the app
- [ ] New settings or options added

### 5. templates/privacy.html (in-app privacy policy — edit directly)

Update when:
- [ ] New data collection occurs
- [ ] Data sharing with third parties changes
- [ ] AI features change in scope
- [ ] Registration, email handling, or data retention changes
- [ ] Any change with MHMD/legal implications — raise to Missy for approval before committing

### 6. .docx Files (DO NOT EDIT — flag for Missy)

These files cannot be edited by Claude Code. Instead, note what changed at the end of the session:

**baseline-vision-roadmap.docx** — Flag when:
- [ ] Significant product direction changes
- [ ] Major features completed that were on the roadmap
- [ ] New strategic decisions made

## Documentation Style Guide

- Keep CLAUDE.md concise — it's read by Claude Code every session
- Use the same formatting patterns already present in each file
- For TECHNICAL_README.md, include code examples for non-obvious patterns
- For BACKLOG.md, use the existing format with priority markers
- Always include the date when marking items complete
- When adding new sections, place them logically near related content

## Checklist Output Format

After reviewing changes, produce a summary:

```
## Documentation Updates Needed

### Applied (updated directly):
- CLAUDE.md: [what was updated]
- TECHNICAL_README.md: [what was updated]
- BACKLOG.md: [what was updated]
- templates/help.html: [what was updated]
- templates/privacy.html: [what was updated — flag legal-sensitive edits to Missy before committing]

### Flagged for Missy (.docx files):
- baseline-vision-roadmap.docx: [what needs updating and why]

### No update needed:
- [list files that don't need changes and brief reason]
```
