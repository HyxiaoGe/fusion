---
name: verified-research
version: 1.0.0
description: Build a verifiable evidence chain for requests requiring official primary sources and cross-source checking
allowed-tools:
  - web_search
  - url_read
---
# Verified Research

## Scope

Use this skill when the user explicitly requests official primary material, reliable sources, fact verification, or cross-checking.

## Research Method

1. Search for candidate sources first, then read the original material directly relevant to the conclusion. Do not answer from search snippets alone.
2. Prefer official announcements, official documentation, primary data, research papers, and other first-party sources.
3. Cross-check important facts that affect the conclusion with independent sources. When sources conflict, explain the differences instead of forcing them into one claim.
4. State only facts supported by sources. Clearly explain limits when information is missing, a page cannot be read, or evidence is insufficient.

## Output Rules

- Place citations near the corresponding fact or paragraph so the reader can tell which conclusion each source supports.
- Distinguish source facts, the source author's judgment, and conclusions synthesized from multiple sources.
- Never fabricate sources, titles, publication dates, authors, figures, links, or quoted material.
- Do not treat a search-result snippet as original material that has been read and verified.
