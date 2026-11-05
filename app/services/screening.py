from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import ResponseStatus
from app.hh.client import HHClient
from app.hh.dao import HHResponseDAO
from app.hh.exceptions import HHAPIError, HHError, HHTransportError
from app.hh.models import HHResponse
from app.interview.models import Interview, InterviewQuestion
from app.llm.client import LLMClient
from app.llm.exceptions import LLMError
from app.llm.schemas import ResumeAnalysis
from app.logger import logger
from app.services.hh import HHService
from app.services.interview import PERSONAL_QUESTIONS, InterviewService
from app.services.scoring import resume_score
from app.vacancy.dao import VacancyDAO
from app.vacancy.models import Vacancy

MAX_ATTEMPTS = 3


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, HHAPIError):
        return exc.status_code == 429 or exc.status_code >= 500
    return isinstance(exc, HHTransportError | LLMError)


class VacancyAlreadyTracked(Exception):
    pass


async def track_vacancy(
    session: AsyncSession,
    hh: HHClient,
    vacancy: Vacancy,
    hr_tg_id: int,
    hh_vacancy_id: str,
    webhook_url: str,
) -> None:
    linked = await VacancyDAO(session).find_by_hh_id(hh_vacancy_id)
    if linked is not None and linked.id != vacancy.id:
        raise VacancyAlreadyTracked(linked.title)
    await HHService(session, hh).ensure_subscription(hr_tg_id, webhook_url)
    vacancy.hh_vacancy_id = hh_vacancy_id
    vacancy.owner_tg_id = hr_tg_id


async def register_response(
    session: AsyncSession, topic_id: str, hh_vacancy_id: str | None
) -> bool:
    if hh_vacancy_id is None:
        return False
    vacancy = await VacancyDAO(session).find_by_hh_id(hh_vacancy_id)
    responses = HHResponseDAO(session)
    if vacancy is None or await responses.exists(topic_id):
        return False
    await responses.add(topic_id=topic_id, vacancy_id=vacancy.id, status=ResponseStatus.NEW)
    logger.info("Отклик поставлен в очередь: %s", topic_id)
    return True


class ScreeningService:
    def __init__(self, session: AsyncSession, llm: LLMClient, hh: HHClient):
        self.session = session
        self.llm = llm
        self.hh = hh
        self.vacancies = VacancyDAO(session)

    async def screen(self, response: HHResponse) -> Vacancy | None:
        vacancy = await self.vacancies.get_with_profile(response.vacancy_id)
        if vacancy is None or vacancy.owner_tg_id is None:
            self._fail(response, "У вакансии нет владельца с подключённым hh.ru")
            return None
        try:
            resume = await HHService(self.session, self.hh).get_negotiation_resume(
                vacancy.owner_tg_id, response.topic_id
            )
            resume_text = resume.to_text()
            await self.session.commit()
            analysis = await self.llm.analyze_resume(vacancy, resume_text, PERSONAL_QUESTIONS)
        except (HHError, LLMError) as exc:
            if _is_retryable(exc) and response.attempts + 1 < MAX_ATTEMPTS:
                response.attempts += 1
                response.error = str(exc)
            else:
                self._fail(response, str(exc))
            return None

        matches = {m.criterion_id: m.score for m in analysis.criteria_matches}
        response.candidate_name = analysis.candidate_name
        response.resume_text = resume_text
        response.resume_analysis = analysis.model_dump(mode="json")
        response.resume_score = resume_score(vacancy.criteria, matches)
        response.status = ResponseStatus.SCREENED
        response.screened_at = datetime.now(UTC)
        response.error = None
        logger.info("Отклик оценён: %s, балл резюме: %s", response.id, response.resume_score)
        return vacancy

    async def invite(
        self, response: HHResponse, hr_tg_id: int
    ) -> tuple[Interview, list[InterviewQuestion], Vacancy]:
        vacancy = await self.vacancies.get_with_profile(response.vacancy_id)
        analysis = ResumeAnalysis.model_validate(response.resume_analysis)
        interview, questions = await InterviewService(self.session, self.llm).create_from_analysis(
            vacancy, hr_tg_id, response.resume_text, analysis, response.topic_id
        )
        response.status = ResponseStatus.INVITED
        response.interview_id = interview.id
        logger.info("Кандидат приглашён: отклик %s, интервью %s", response.id, interview.id)
        return interview, questions, vacancy

    @staticmethod
    def _fail(response: HHResponse, error: str) -> None:
        response.status = ResponseStatus.FAILED
        response.attempts += 1
        response.error = error
