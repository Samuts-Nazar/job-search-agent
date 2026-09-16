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


class Relocation(BaseModel):
    willing: bool
    notes: str | None = None


class SalaryExpectation(BaseModel):
    currency: str
    monthly_min: int
    monthly_max: int


class YearsOfExperience(BaseModel):
    qa_automation: float | None = None
    manual_qa: float | None = None
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
