from app.enums import InterviewStatus, QuestionSource, Verdict
from app.interview.dao import InterviewDAO
from app.llm.client import LLMClient
from app.llm.schemas import AnswerEvaluation, CriterionMatch, GeneratedQuestion, ResumeAnalysis
from app.services.interview import InterviewService, evidence_in_answer
from app.services.report import render_html, render_summary
from app.vacancy.dao import VacancyDAO
from app.vacancy.models import Vacancy, VacancyCriterion


class FakeLLM(LLMClient):
    def __init__(self, evaluations: list[AnswerEvaluation]):
        super().__init__(api_key="test", model="test", base_url=None)
        self.evaluations = evaluations
        self.calls: list[dict] = []

    async def analyze_resume(
        self, vacancy: Vacancy, resume_text: str, questions_count: int
    ) -> ResumeAnalysis:
        return ResumeAnalysis(
            candidate_name="Иван Петров",
            summary="5 лет в ML",
            skills=["Python", "RAG"],
            total_experience_years=5,
            criteria_matches=[
                CriterionMatch(criterion_id=c.id, score=3, rationale="есть опыт")
                for c in vacancy.criteria
            ],
            questions=[
                GeneratedQuestion(criterion_id=1, text="Про ваш RAG", reason="проект X"),
                GeneratedQuestion(criterion_id=999, text="Лишний", reason=""),
            ],
        )

    async def evaluate_answer(
        self,
        *,
        vacancy_title: str,
        criterion: VacancyCriterion,
        resume_text: str,
        question: str,
        answer: str,
        follow_up: str | None,
        follow_up_answer: str | None,
    ) -> AnswerEvaluation:
        self.calls.append({"answer": answer, "follow_up_answer": follow_up_answer})
        return self.evaluations.pop(0)


def _evaluation(score: int, evidence: str = "", follow_up: str | None = None) -> AnswerEvaluation:
    return AnswerEvaluation(
        score=score,
        rationale="обоснование",
        evidence=evidence,
        is_shallow=follow_up is not None,
        follow_up=follow_up,
        discrepancy=None,
    )


async def _create(session, llm):
    vacancy = await VacancyDAO(session).get_with_profile(1)
    interview, questions = await InterviewService(session, llm).create(
        vacancy, 42, "резюме", "123456789"
    )
    await session.commit()
    session.expunge_all()
    return interview.id, questions


async def test_full_interview_flow(session, vacancy):
    llm = FakeLLM(
        [
            _evaluation(1, follow_up="Какой векторный поиск?"),
            _evaluation(4, evidence="Использовал pgvector"),
            _evaluation(5, evidence="выдуманная цитата"),
            _evaluation(4, evidence="гибридный поиск"),
        ]
    )
    interview_id, questions = await _create(session, llm)
    assert [(q.text, q.source) for q in questions] == [
        ("Про LLM", QuestionSource.COMPANY),
        ("Про Python", QuestionSource.GENERATED),
        ("Про ваш RAG", QuestionSource.RESUME),
    ]

    service = InterviewService(session, llm)
    interview = await InterviewDAO(session).get_full(interview_id)
    first = await service.start(interview, candidate_tg_id=7)
    assert first.text == "Про LLM"
    assert interview.status is InterviewStatus.IN_PROGRESS

    same = await service.answer(interview, "Делал RAG")
    assert same is first
    assert same.follow_up == "Какой векторный поиск?"

    second = await service.answer(interview, "Использовал pgvector и BM25")
    assert second.text == "Про Python"
    assert llm.calls[1] == {
        "answer": "Делал RAG",
        "follow_up_answer": "Использовал pgvector и BM25",
    }
    assert first.score == 4
    assert first.evidence_found is True

    third = await service.answer(interview, "Пишу на FastAPI")
    assert third.text == "Про ваш RAG"
    assert second.evidence_found is False

    assert await service.answer(interview, "Делали гибридный поиск") is None
    await session.commit()

    assert interview.status is InterviewStatus.FINISHED
    assert interview.total_score == 85.0
    assert interview.verdict is Verdict.RECOMMENDED

    session.expunge_all()
    saved = await InterviewDAO(session).get_full(interview_id)
    assert saved.status is InterviewStatus.FINISHED
    assert "Иван Петров" in render_html(saved)
    assert "85/100" in render_summary(saved)
    assert "Проверено критериев: 2 из 2\n" in render_summary(saved)


async def test_low_must_have_rejects(session, vacancy):
    llm = FakeLLM([_evaluation(1), _evaluation(5), _evaluation(2)])
    interview_id, _ = await _create(session, llm)
    service = InterviewService(session, llm)
    interview = await InterviewDAO(session).get_full(interview_id)
    await service.start(interview, candidate_tg_id=7)
    for text in ("ответ 1", "ответ 2", "ответ 3"):
        await service.answer(interview, text)
    assert interview.verdict is Verdict.REJECTED


def test_evidence_match_ignores_case_spaces_and_quotes():
    assert evidence_in_answer("«Использовал  PGVector»", "я использовал pgvector в проде")
    assert evidence_in_answer("Ёлка", "елка") is True
    assert evidence_in_answer("чего не было", "ответ") is False
    assert evidence_in_answer("", "ответ") is None
