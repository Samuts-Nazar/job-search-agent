"""Pydantic schemas for data/facts.yaml and data/answers.yaml.

See PROJECT.md §3 (fact registry / answer bank) and §5.7 (knockout
questions -- work authorization, time zone, years of experience,
relocation, salary -- answered only from answers.yaml, never generated).
Both example files under data.example/ set `example: true`; real copies
must set it to false (or omit it) -- see data_guard.py, which also refuses
data that still has an example file's identity values.
"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class Links(BaseModel):
    linkedin: str
    github: str | None = None
    portfolio: str | None = None


class Candidate(BaseModel):
    name: str
    email: EmailStr
    phone: str
    city: str
    country: str
    timezone: str
    links: Links


class CvTrack(BaseModel):
    id: str
    title: str


class Skill(BaseModel):
    name: str
    tracks: list[str] = Field(default_factory=list)
    # Groups skills in the CV's Technical Skills section (PROJECT.md
    # §8.2) -- required so every skill is intentionally placed, not
    # silently uncategorized.
    category: str


class Project(BaseModel):
    id: str
    name: str
    tracks: list[str] = Field(default_factory=list)
    summary: str = ""
    metrics: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    dates: str = ""


class ExperienceEntry(BaseModel):
    id: str
    employer: str
    title: str
    tracks: list[str] = Field(default_factory=list)
    dates: str
    bullets: list[str] = Field(default_factory=list)


class EducationEntry(BaseModel):
    institution: str
    degree: str
    dates: str


class Language(BaseModel):
    name: str
    cefr: str


class Facts(BaseModel):
    example: bool = False
    candidate: Candidate
    cv_tracks: list[CvTrack] = Field(min_length=1)
    skills: list[Skill] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    experience: list[ExperienceEntry] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    languages: list[Language] = Field(min_length=1)


class WorkAuthorization(BaseModel):
    eu: str
    us: str
    uk: str
    ukraine: str
    # Country-code -> free-text status, for countries not covered by the
    # eu/us/uk/ukraine buckets above (e.g. a residence-permit country with
    # its own visa/status rules). Optional: most applications never ask.
    per_country: dict[str, str] = Field(default_factory=dict)


class Relocation(BaseModel):
    willing: bool
    notes: str | None = None


class SalaryExpectation(BaseModel):
    currency: str
    monthly_min: int
    monthly_max: int
    # A single preferred figure within [monthly_min, monthly_max], for forms
    # that ask for one number instead of a range. Optional.
    monthly_target: int | None = None


class WorkFormat(BaseModel):
    remote: bool = False
    hybrid: bool = False
    onsite: bool = False
    # Minimum monthly figure (same currency as salary_expectation) at which
    # an onsite/hybrid role becomes acceptable. Optional, only meaningful
    # when onsite or hybrid is true.
    onsite_salary_floor: int | None = None


class Availability(BaseModel):
    night_shifts: bool = False
    weekends: bool = False
    shift_notes: str | None = None


class TestTask(BaseModel):
    willing: bool | None = None
    max_hours: float | None = None


class YearsOfExperience(BaseModel):
    qa_automation: float | None = None
    manual_qa: float | None = None
    ai_automation: float | None = None
    ml_ai: float | None = None
    general: float | None = None


class Answers(BaseModel):
    example: bool = False
    work_authorization: WorkAuthorization
    relocation: Relocation
    notice_period_days: int
    salary_expectation: SalaryExpectation
    timezone: str
    languages: list[Language] = Field(min_length=1)
    links: Links
    years_of_experience: YearsOfExperience = Field(default_factory=YearsOfExperience)
    # Everything below is optional -- PROJECT.md §5.7's knockout-question
    # answer bank only needs to cover what a form actually asks, and not
    # every application asks about shift work, contract type, or a paid
    # test task. Missing ones are reported as a data_guard warning (not a
    # failure) so a first-time answers.yaml isn't blocked on filling in
    # sections that may never come up.
    work_format: WorkFormat | None = None
    availability: Availability | None = None
    employment_types: list[str] = Field(default_factory=list)
    test_task: TestTask | None = None
