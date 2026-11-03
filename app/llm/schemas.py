from typing import Self

from pydantic import BaseModel, Field, model_validator


class ProfileCriterion(BaseModel):
    title: str
    description: str
    weight: int = Field(ge=1, le=3)
    is_must_have: bool


class ProfileQuestion(BaseModel):
    criterion_number: int
    text: str


class CompanyQuestionMatch(BaseModel):
    question_number: int
    criterion_number: int


class VacancyProfile(BaseModel):
    title: str
    summary: str
    criteria: list[ProfileCriterion]
    questions: list[ProfileQuestion]
    company_questions: list[CompanyQuestionMatch]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if not self.criteria:
            raise ValueError("Профиль без критериев")
        count = len(self.criteria)
        self.questions = [q for q in self.questions if 1 <= q.criterion_number <= count]
        self.company_questions = [
            q for q in self.company_questions if 1 <= q.criterion_number <= count
        ]
        return self


class CriterionMatch(BaseModel):
    criterion_id: int
    score: int = Field(ge=0, le=5)
    rationale: str


class GeneratedQuestion(BaseModel):
    criterion_id: int
    text: str
    reason: str


class ResumeAnalysis(BaseModel):
    candidate_name: str
    summary: str
    skills: list[str]
    total_experience_years: float | None
    criteria_matches: list[CriterionMatch]
    questions: list[GeneratedQuestion]


class AnswerEvaluation(BaseModel):
    score: int = Field(ge=0, le=5)
    rationale: str
    evidence: str
    is_shallow: bool
    follow_up: str | None
    discrepancy: str | None
