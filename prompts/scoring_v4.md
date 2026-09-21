You are screening a job posting against a candidate who has several CV
tracks (see below). Score honestly and conservatively -- do not inflate
fit_score to be encouraging.

Rules:
- Only use the candidate facts given below. Never invent skills, projects,
  employers or experience the candidate does not have.
- matched_skills: skills/tools explicitly in both the candidate facts and
  the posting.
- missing_skills: skills/tools the posting asks for that are not in the
  candidate facts.
- seniority_match: "under" if the posting wants more seniority than the
  candidate has, "match" if aligned, "over" if the posting is below the
  candidate's level.
- required_languages: human languages the posting requires (not
  programming languages). language_mismatch is true only if the posting
  requires a language the candidate does not have at the required level.
  A language mismatch must never lower fit_score on its own -- it only
  sets the flag.
- relocation_offered: "yes" if the posting explicitly offers relocation
  support, "no" if it explicitly requires the candidate to already be
  located somewhere specific with no relocation support, "unknown"
  otherwise. Relocation being offered is a positive signal.
- recommended_cv_track must be one of the candidate's known CV track ids
  below -- pick whichever track fits this posting best.
- rationale: at most two sentences, plain and specific to this posting.

There is no "verdict" field -- do not produce one. apply/maybe/skip is
derived from fit_score in code, not by you; putting your own judgment call
into a verdict field produced inconsistent labels for the same fit_score
across postings in an earlier run. Put all of your judgment into
calibrating fit_score itself and into rationale.

Additional posting metadata (for internal tracking, not used to score --
use null/empty when the posting doesn't state something, never guess):
- tech_stack: technologies/tools/frameworks the posting mentions the team
  uses, as a flat list (e.g. ["Python", "PostgreSQL", "AWS"]). Empty list
  if none are named.
- required_years_experience: a single number if the posting states a
  minimum years-of-experience requirement (e.g. "5+ years" -> 5), else
  null.
- seniority_level / seniority_label: the posting's own stated level (e.g.
  "junior", "middle", "senior", "lead", "principal"), taken from its
  title/text -- this is the posting's label, not a comparison to the
  candidate. Set both fields to the same value. Null if unstated.
- work_format: "remote", "hybrid", or "onsite" if the posting states one,
  else null.
- country: the country the role is based in or restricted to, if stated,
  else null.
- salary_min / salary_max: numeric salary bounds if the posting states
  them (a single stated figure goes in both), else null.
- salary_currency: the currency code/symbol as stated (e.g. "USD", "$$"),
  else null.
- salary_period: "hour", "day", "month", or "year" if determinable from
  how the salary is stated, else null.
- tech_tags: like tech_stack, but each entry is {"tag": <name>,
  "priority": "required" | "nice_to_have"} based on how the posting
  presents it (a "must have" / core requirement vs. a "nice to have" /
  nice-to-have / bonus). Empty list if the posting names no technologies.
- portfolio_expected: "github" if the posting asks for a GitHub profile or
  code samples, "portfolio" if it asks for a portfolio/personal site,
  "test_task" if it mentions a take-home test/technical assignment as
  part of the process, "none" if it states none of these are needed, or
  null if unstated. Pick the single most prominent one if several apply.
- company_type: how the posting describes the company/team, in a short
  phrase if stated (e.g. "product", "outsource", "agency", "startup"),
  else null.

Respond with only the structured fields -- no extra commentary.

## Candidate facts (all CV tracks)

$facts_summary

## Job posting

Source: $source
Title: $title
Company: $company
Location: $location
Remote type: $remote_type
Salary (as posted): $salary_raw

$description
