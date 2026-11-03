from dataclasses import dataclass
from statistics import mean

from app.enums import Verdict
from app.interview.models import InterviewQuestion
from app.vacancy.models import VacancyCriterion

MAX_SCORE = 5
MUST_HAVE_MIN_SCORE = 3
RECOMMEND_FROM = 75
CONSIDER_FROM = 50


@dataclass(frozen=True)
class CriterionScore:
    criterion: VacancyCriterion
    score: float | None


def criterion_scores(
    criteria: list[VacancyCriterion], questions: list[InterviewQuestion]
) -> list[CriterionScore]:
    result = []
    for criterion in criteria:
        scores = [
            q.score for q in questions if q.criterion_id == criterion.id and q.score is not None
        ]
        result.append(CriterionScore(criterion, mean(scores) if scores else None))
    return result


def total_score(scores: list[CriterionScore]) -> float | None:
    checked = [s for s in scores if s.score is not None]
    weight = sum(s.criterion.weight for s in checked)
    if weight == 0:
        return None
    weighted = sum(s.criterion.weight * s.score for s in checked) / weight
    return round(weighted / MAX_SCORE * 100, 1)


def resume_score(criteria: list[VacancyCriterion], matches: dict[int, int]) -> float | None:
    checked = [c for c in criteria if c.id in matches]
    weight = sum(c.weight for c in checked)
    if weight == 0:
        return None
    weighted = sum(c.weight * matches[c.id] for c in checked) / weight
    return round(weighted / MAX_SCORE * 100, 1)


def make_verdict(scores: list[CriterionScore], total: float | None) -> Verdict:
    must_have = [s for s in scores if s.criterion.is_must_have]
    if any(s.score is not None and s.score < MUST_HAVE_MIN_SCORE for s in must_have):
        return Verdict.REJECTED
    if total is None or total < CONSIDER_FROM:
        return Verdict.REJECTED
    if total >= RECOMMEND_FROM and all(s.score is not None for s in must_have):
        return Verdict.RECOMMENDED
    return Verdict.CONSIDER
