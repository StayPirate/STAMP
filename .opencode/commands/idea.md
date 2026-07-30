---
description: Add a new idea to the brainstorming list in docs/drafts/ideas.md
---

The user wants to add the following idea to `docs/drafts/ideas.md`:

**$ARGUMENTS**

Follow these steps strictly:

1. **Read the file**: read `docs/drafts/ideas.md`. If it does not exist, create
   it with the following content:
   ```markdown
   # Ideas

   Brainstorming ideas for Sentinel. When an idea is promoted to a feature
   specification, it should be removed from this list.
   ```

2. **Check for duplicates**: compare the new idea semantically against ALL
   existing ideas in the file. If any existing idea is similar in meaning (not
   just exact match — consider synonyms, rephrasing, and overlapping scope):
   - List the similar idea(s) to the user
   - Ask the user if they still want to add it
   - If the user says no, stop here

3. **Add the idea**: append the idea as a new bullet point (`- `) at the end
   of the list. The idea MUST be written in English, regardless of the language
   used by the user to describe it. If the user provided the idea in a
   non-English language, translate it to English while preserving the original
   meaning. Do not add unnecessary embellishment or summarize it.

4. **Report**: tell the user what was added. Do NOT commit automatically —
   the file edit is staged and committed by the caller as part of the normal
   workflow.
