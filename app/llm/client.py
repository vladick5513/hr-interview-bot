from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel, ValidationError

from app.enums import QuestionMode
from app.llm import prompts
from app.llm.exceptions import LLMTransportError, LLMUnexpectedResponse
from app.llm.schemas import AnswerEvaluation, ResumeAnalysis, VacancyProfile
from app.logger import logger
from app.vacancy.models import Vacancy, VacancyCriterion


class LLMClient:
    def __init__(self, api_key: str, model: str, base_url: str | None):
        self.openai = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    async def generate_vacancy_profile(
        self,
        vacancy_text: str,
        pool: list[str],
        mode: QuestionMode,
        previous: VacancyProfile | None = None,
        remarks: str | None = None,
    ) -> VacancyProfile:
        return await self._parse(
            prompts.PROFILE_SYSTEM,
            prompts.profile_user(vacancy_text, pool, mode, previous, remarks),
            VacancyProfile,
        )

    async def analyze_resume(
        self, vacancy: Vacancy, resume_text: str, questions_count: int
    ) -> ResumeAnalysis:
        return await self._parse(
            prompts.ANALYZE_SYSTEM,
            prompts.analyze_user(vacancy, resume_text, questions_count),
            ResumeAnalysis,
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
        return await self._parse(
            prompts.EVALUATE_SYSTEM,
            prompts.evaluate_user(
                vacancy_title,
                criterion,
                resume_text,
                question,
                answer,
                follow_up,
                follow_up_answer,
            ),
            AnswerEvaluation,
        )

    async def _parse[SchemaT: BaseModel](
        self, system: str, user: str, schema: type[SchemaT]
    ) -> SchemaT:
        logger.info("Запрос к LLM: %s", schema.__name__)
        try:
            completion = await self.openai.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format=schema,
            )
        except ValidationError as exc:
            raise LLMUnexpectedResponse(str(exc)) from exc
        except OpenAIError as exc:
            raise LLMTransportError(str(exc)) from exc
        logger.info("Ответ от LLM получен: %s", schema.__name__)
        message = completion.choices[0].message
        if message.parsed is None:
            raise LLMUnexpectedResponse(message.refusal or "empty response")
        return message.parsed
