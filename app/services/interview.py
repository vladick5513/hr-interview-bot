from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import InterviewStatus, QuestionSource
from app.interview.dao import InterviewDAO, InterviewQuestionDAO
from app.interview.models import Interview, InterviewQuestion
from app.llm.client import LLMClient
from app.llm.schemas import AnswerEvaluation, ResumeAnalysis
from app.services.scoring import criterion_scores, make_verdict, total_score
from app.vacancy.models import Vacancy

PERSONAL_QUESTIONS = 3


def next_question(interview: Interview) -> InterviewQuestion | None:
    return next((q for q in interview.questions if not q.is_done), None)


def evidence_in_answer(evidence: str, *answers: str | None) -> bool | None:
    if not evidence.strip():
        return None
    text = _normalize(" ".join(a for a in answers if a))
    return _normalize(evidence.strip(" «»\"'.,…")) in text


def _normalize(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


class InterviewService:
    def __init__(self, session: AsyncSession, llm: LLMClient):
        self.session = session
        self.llm = llm
        self.interviews = InterviewDAO(session)
        self.questions = InterviewQuestionDAO(session)

    async def create(
        self, vacancy: Vacancy, hr_tg_id: int, resume_text: str, hh_negotiation_id: str | None
    ) -> tuple[Interview, list[InterviewQuestion]]:
        await self.session.commit()
        analysis = await self.llm.analyze_resume(vacancy, resume_text, PERSONAL_QUESTIONS)
        return await self.create_from_analysis(
            vacancy, hr_tg_id, resume_text, analysis, hh_negotiation_id
        )

    async def create_from_analysis(
        self,
        vacancy: Vacancy,
        hr_tg_id: int,
        resume_text: str,
        analysis: ResumeAnalysis,
        hh_negotiation_id: str | None,
    ) -> tuple[Interview, list[InterviewQuestion]]:
        criterion_ids = {c.id for c in vacancy.criteria}
        personal = [q for q in analysis.questions if q.criterion_id in criterion_ids]
        interview = await self.interviews.add(
            vacancy_id=vacancy.id,
            hr_tg_id=hr_tg_id,
            candidate_name=analysis.candidate_name,
            hh_negotiation_id=hh_negotiation_id,
            resume_text=resume_text,
            resume_analysis=analysis.model_dump(mode="json"),
            status=InterviewStatus.CREATED,
        )
        rows = [
            {"criterion_id": q.criterion_id, "text": q.text, "source": q.source}
            for q in vacancy.questions
        ] + [
            {"criterion_id": q.criterion_id, "text": q.text, "source": QuestionSource.RESUME}
            for q in personal[:PERSONAL_QUESTIONS]
        ]
        for position, row in enumerate(rows):
            row.update(interview_id=interview.id, position=position)
        questions = await self.questions.add_many(rows)
        return interview, questions

    async def start(self, interview: Interview, candidate_tg_id: int) -> InterviewQuestion:
        if interview.status is InterviewStatus.CREATED:
            interview.candidate_tg_id = candidate_tg_id
            interview.status = InterviewStatus.IN_PROGRESS
            interview.started_at = datetime.now(UTC)
        question = next_question(interview)
        if question is None:
            raise RuntimeError(f"Interview {interview.id} has no questions")
        return question

    async def answer(self, interview: Interview, text: str) -> InterviewQuestion | None:
        question = next_question(interview)
        if question is None:
            raise RuntimeError(f"Interview {interview.id} has no pending questions")
        criterion = next(c for c in interview.vacancy.criteria if c.id == question.criterion_id)
        is_follow_up = question.answer is not None
        answer = question.answer if is_follow_up else text
        follow_up_answer = text if is_follow_up else None

        await self.session.commit()
        # Поля пишем только после ответа LLM: при ошибке в сессии ничего не останется
        evaluation = await self.llm.evaluate_answer(
            vacancy_title=interview.vacancy.title,
            criterion=criterion,
            resume_text=interview.resume_text,
            question=question.text,
            answer=answer,
            follow_up=question.follow_up,
            follow_up_answer=follow_up_answer,
        )

        question.answer = answer
        question.follow_up_answer = follow_up_answer
        self._apply_evaluation(question, evaluation)
        if evaluation.is_shallow and evaluation.follow_up and not is_follow_up:
            question.follow_up = evaluation.follow_up

        upcoming = next_question(interview)
        if upcoming is None:
            self._finish(interview)
        return upcoming

    @staticmethod
    def _apply_evaluation(question: InterviewQuestion, evaluation: AnswerEvaluation) -> None:
        question.score = evaluation.score
        question.rationale = evaluation.rationale
        question.evidence = evaluation.evidence or None
        question.evidence_found = evidence_in_answer(
            evaluation.evidence, question.answer, question.follow_up_answer
        )
        question.discrepancy = evaluation.discrepancy

    @staticmethod
    def _finish(interview: Interview) -> None:
        scores = criterion_scores(interview.vacancy.criteria, interview.questions)
        total = total_score(scores)
        interview.total_score = total
        interview.verdict = make_verdict(scores, total)
        interview.status = InterviewStatus.FINISHED
        interview.finished_at = datetime.now(UTC)
