from app.enums import Verdict
from app.interview.models import InterviewQuestion
from app.llm.prompts import analyze_user
from app.services.report import render_coverage
from app.services.scoring import criterion_scores, make_verdict, total_score
from app.vacancy.models import Vacancy, VacancyCriterion, VacancyQuestion

LLM = VacancyCriterion(id=1, title="LLM", description="", weight=3, is_must_have=True)
PYTHON = VacancyCriterion(id=2, title="Python", description="", weight=1, is_must_have=False)


def _questions(*pairs: tuple[int, int | None]) -> list[InterviewQuestion]:
    return [InterviewQuestion(criterion_id=c, score=s) for c, s in pairs]


def test_criterion_score_is_mean_of_its_questions():
    scores = criterion_scores([LLM, PYTHON], _questions((1, 4), (1, 3), (2, 5)))
    assert [s.score for s in scores] == [3.5, 5]


def test_unchecked_criterion_is_none_and_excluded_from_total():
    scores = criterion_scores([LLM, PYTHON], _questions((1, 4)))
    assert scores[1].score is None
    assert total_score(scores) == 80.0


def test_total_is_weighted():
    scores = criterion_scores([LLM, PYTHON], _questions((1, 5), (2, 0)))
    assert total_score(scores) == 75.0


def test_total_none_when_nothing_scored():
    assert total_score(criterion_scores([LLM], [])) is None


def test_low_must_have_rejects_despite_high_total():
    scores = criterion_scores([LLM, PYTHON], _questions((1, 2), (2, 5)))
    assert make_verdict(scores, 100.0) is Verdict.REJECTED


def test_verdict_thresholds():
    scores = criterion_scores([LLM, PYTHON], _questions((1, 4), (2, 4)))
    assert make_verdict(scores, 80.0) is Verdict.RECOMMENDED
    assert make_verdict(scores, 60.0) is Verdict.CONSIDER
    assert make_verdict(scores, 40.0) is Verdict.REJECTED


def test_unchecked_must_have_blocks_recommendation():
    scores = criterion_scores([LLM, PYTHON], _questions((2, 5)))
    assert make_verdict(scores, 100.0) is Verdict.CONSIDER


def test_coverage_warns_when_criteria_unchecked():
    scores = criterion_scores([LLM, PYTHON], _questions((1, 4)))
    assert render_coverage(scores) == (
        "Проверено критериев: 1 из 2 — итог посчитан не по всему профилю вакансии"
    )
    full = criterion_scores([LLM, PYTHON], _questions((1, 4), (2, 3)))
    assert render_coverage(full) == "Проверено критериев: 2 из 2"


def test_analyze_prompt_marks_criteria_without_mandatory_question():
    vacancy = Vacancy(
        title="AI lead",
        description="",
        criteria=[LLM, PYTHON],
        questions=[VacancyQuestion(criterion_id=1, text="Про LLM", position=0)],
    )
    lines = {line.split("]")[0]: line for line in analyze_user(vacancy, "резюме", 3).splitlines()}
    assert lines["- id=1 [must-have, вес 3"].endswith("— обязательный вопрос есть")
    assert lines["- id=2 [вес 1"].endswith("— обязательного вопроса нет")
