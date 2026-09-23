Writing rules for short candidate-to-recruiter outreach text (LinkedIn
messages, cover letters). Shared across both -- write once here, reuse for
whichever format is being produced; the format-specific length limit and
delivery channel are supplied separately, not part of these rules.

- Use ONLY the candidate facts given. Never invent skills, projects,
  employers, numbers, or experience durations not present in them.
- Write in the posting's own language.
- Greeting: "Dear Hiring Team" in English (or "Dear <name>" if the
  posting names a specific recruiter). In Ukrainian, open with "Доброго
  дня".
- No sign-off phrase in Ukrainian: never "Дякую" or "З повагою" -- the
  message ends after its content. (English messages may close normally,
  e.g. "Best, <name>".)
- Banned phrases, in either language: "I am excited to apply",
  "passionate about", "I believe I would be a great fit", "з великим
  ентузіазмом", and any dash (em dash, en dash, or a hyphen used as
  standalone punctuation) joining two clauses -- use separate sentences
  instead. A hyphen inside a single compound word (e.g. "test-driven",
  "AI-агент") is fine.
- Never apologize for lacking experience, and never ask for "a chance"
  or "an opportunity to prove yourself" -- present the candidate as
  someone already doing this kind of work.
- Short, plain sentences. No AI-sounding padding or generic enthusiasm.

Status 2026-09-24: no `quality`-tier model has been chosen yet, so these
rules are currently enforced by a deterministic template
(`package.build_outreach_message`), not an LLM call -- the template is
hand-written to already satisfy every rule above. Once a model is picked,
both the outreach draft and the (currently on-hold) cover-letter feature
should generate from this same file rather than duplicating the rules.
