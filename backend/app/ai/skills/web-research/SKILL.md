---
name: web-research
description: Method for answering questions that need current or verifiable information from the web, such as recent events, releases, prices, policies, official statements, or facts the user wants checked against sources. Covers choosing queries, deciding which pages to read in full, cross-checking key facts, and when to stop searching. Not needed when general knowledge already answers the question.
metadata:
  fusion-tools: web_search url_read
  adapted-from: "DeerFlow deep-research skill (MIT, github.com/bytedance/deer-flow): date-aware queries and reading full sources; stop condition added for Fusion"
---

# Web research

## 1. Decide what the answer depends on

Before the first search, name the one to three facts the answer hinges on (for example: the release date, the current price, what the official notice says). Every search afterwards should serve one of them. Facts the answer does not need are not worth a search.

## 2. Write targeted queries

- Take the current date from the system prompt and match the precision to the question: "today" needs the full date, "recently" needs the month, "this year" needs the year. Never fall back to a past year out of habit.
- Name the specific entity and attribute ("Python 3.14 release date"), not a broad topic ("Python news").
- When the answer should come from an official source, say so with the search intent or domains instead of hoping it ranks first.
- Rephrase only when results miss the fact. Do not send near-duplicate queries.

## 3. Read selectively

- A snippet is enough to confirm a simple, uncontested fact.
- Read the full page when the answer depends on exact numbers, dates, conditions, or wording, or when snippets disagree.
- Prefer primary sources (the official site, original announcement, filing, or documentation) over aggregators and reposts.
- Note each source's publication date; time-sensitive facts expire.

## 4. Cross-check what drives the answer

A number, date, or claim that the conclusion rests on should come from the primary source or agree across two independent sources. When sources conflict, report the conflict and say which source is more authoritative or more recent.

## 5. Stop as soon as you can answer

Stop searching once every fact from step 1 is supported. Further searching is a cost to the user, not extra quality. Clear signals to stop:

- New results repeat what you already have.
- The remaining uncertainty would not change the answer.
- A fact is still missing after two or three well-aimed attempts. Stop and state what could not be confirmed instead of continuing to search.

## 6. Write the answer

- Lead with the answer, then the supporting facts with their sources.
- Separate confirmed facts from your own inference, and give dates for anything time-sensitive.
- Leave out findings that do not bear on the question.
