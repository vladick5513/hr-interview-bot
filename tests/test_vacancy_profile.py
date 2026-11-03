import io

import pytest
from aiogram.types import Document
from pydantic import ValidationError

from app.enums import QuestionMode, QuestionSource
from app.files import MAX_TXT_BYTES, TxtFileError, decode_text_file, read_txt_document
from app.hh.client import parse_vacancy_id
from app.hh.schemas import HHVacancy
from app.llm.schemas import (
    CompanyQuestionMatch,
    ProfileCriterion,
    ProfileQuestion,
    VacancyProfile,
)
from app.services.vacancy import (
    DraftQuestion,
    VacancyService,
    assemble_questions,
    parse_question_pool,
)
from app.vacancy.dao import VacancyDAO

POOL = ["Как вы внедряли Claude Code в команде?", "Как вы мотивируете людей?"]


def _profile(
    questions: list[ProfileQuestion], company: list[CompanyQuestionMatch] | None = None
) -> VacancyProfile:
    return VacancyProfile(
        title="Руководитель разработки",
        summary="Переводит команду на агентную разработку",
        criteria=[
            ProfileCriterion(
                title="Агентная разработка",
                description="Claude Code, MCP",
                weight=3,
                is_must_have=True,
            ),
            ProfileCriterion(
                title="Secure SDLC", description="SAST, секреты", weight=2, is_must_have=False
            ),
        ],
        questions=questions,
        company_questions=company or [],
    )


def test_questions_with_unknown_criterion_are_dropped():
    profile = _profile(
        [
            ProfileQuestion(criterion_number=1, text="ok"),
            ProfileQuestion(criterion_number=3, text="лишний"),
            ProfileQuestion(criterion_number=0, text="лишний"),
        ]
    )
    assert [q.text for q in profile.questions] == ["ok"]


def test_profile_without_criteria_is_rejected():
    with pytest.raises(ValidationError):
        VacancyProfile(title="t", summary="s", criteria=[], questions=[], company_questions=[])


def test_pool_parsing_strips_numbering_and_markers():
    text = "1. Первый вопрос?\n2) Второй\n\n- Третий\n• Четвёртый\n   Пятый  "
    assert parse_question_pool(text) == ["Первый вопрос?", "Второй", "Третий", "Четвёртый", "Пятый"]


GENERATED = [
    ProfileQuestion(criterion_number=1, text="Сгенерирован про агентов"),
    ProfileQuestion(criterion_number=2, text="Сгенерирован про безопасность"),
]


def test_generate_mode_uses_only_generated_questions():
    draft = assemble_questions(_profile(GENERATED), [], QuestionMode.GENERATE)
    assert [q.source for q in draft.questions] == [QuestionSource.GENERATED] * 2
    assert draft.unassigned == []


def test_company_mode_keeps_pool_text_verbatim_and_drops_generated():
    profile = _profile(GENERATED, [CompanyQuestionMatch(question_number=1, criterion_number=1)])
    draft = assemble_questions(profile, POOL, QuestionMode.COMPANY)
    assert draft.questions == [DraftQuestion(1, POOL[0], QuestionSource.COMPANY)]
    assert draft.unassigned == [POOL[1]]


def test_company_plus_adds_generated_only_for_uncovered_must_have():
    profile = _profile(
        GENERATED,
        [
            CompanyQuestionMatch(question_number=1, criterion_number=1),
            CompanyQuestionMatch(question_number=5, criterion_number=2),
            CompanyQuestionMatch(question_number=1, criterion_number=2),
        ],
    )
    draft = assemble_questions(profile, POOL, QuestionMode.COMPANY_PLUS)
    assert draft.questions == [DraftQuestion(1, POOL[0], QuestionSource.COMPANY)]

    uncovered = _profile(GENERATED, [CompanyQuestionMatch(question_number=2, criterion_number=2)])
    draft = assemble_questions(uncovered, POOL, QuestionMode.COMPANY_PLUS)
    assert draft.questions == [
        DraftQuestion(2, POOL[1], QuestionSource.COMPANY),
        DraftQuestion(1, "Сгенерирован про агентов", QuestionSource.GENERATED),
    ]


async def test_saved_vacancy_links_questions_to_criteria(session):
    profile = _profile([])
    questions = [
        DraftQuestion(2, "Про безопасность", QuestionSource.COMPANY),
        DraftQuestion(1, "Про агентов", QuestionSource.GENERATED),
    ]
    vacancy = await VacancyService(session).create("текст вакансии", profile, questions, 42)
    await session.commit()
    session.expunge_all()

    saved = await VacancyDAO(session).get_with_profile(vacancy.id)
    assert saved.title == "Руководитель разработки"
    assert saved.description == "текст вакансии"
    assert saved.owner_tg_id == 42
    by_id = {c.id: c.title for c in saved.criteria}
    assert [(q.position, q.text, q.source, by_id[q.criterion_id]) for q in saved.questions] == [
        (0, "Про безопасность", QuestionSource.COMPANY, "Secure SDLC"),
        (1, "Про агентов", QuestionSource.GENERATED, "Агентная разработка"),
    ]


@pytest.mark.parametrize(
    "data",
    [
        "Руководитель разработки".encode(),
        "﻿Руководитель разработки".encode(),
        "Руководитель разработки".encode("cp1251"),
    ],
    ids=["utf-8", "utf-8-bom", "cp1251"],
)
def test_txt_vacancy_encodings(data):
    assert decode_text_file(data) == "Руководитель разработки"


def test_hh_vacancy_html_to_text():
    vacancy = HHVacancy(
        name="Руководитель разработки (AI-native)",
        description="<p><strong>Задачи:</strong></p><ul><li>Агенты &amp; MCP</li></ul>",
        key_skills=[{"name": "LLM"}, {"name": "RAG"}],
    )
    assert vacancy.to_text() == (
        "Руководитель разработки (AI-native)\n\nЗадачи:\nАгенты & MCP\n\nКлючевые навыки: LLM, RAG"
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "https://belgorod.hh.ru/vacancy/139020577?hhtmFromLabel=employer_vacancy_tab",
            "139020577",
        ),
        ("https://hh.ru/vacancy/138595608", "138595608"),
        ("138595608", "138595608"),
        ("https://hh.ru/resume/0123abc", None),
        ("https://evilhh.ru/vacancy/1", None),
        ("Ищем руководителя разработки", None),
    ],
)
def test_parse_vacancy_id(text, expected):
    assert parse_vacancy_id(text) == expected


class FakeBot:
    def __init__(self, data: bytes):
        self.data = data

    async def download(self, document: Document) -> io.BytesIO:
        return io.BytesIO(self.data)


def _document(name: str, size: int = 100) -> Document:
    return Document(file_id="f", file_unique_id="u", file_name=name, file_size=size)


async def test_txt_document_is_read():
    text = await read_txt_document(FakeBot("Резюме".encode()), _document("cv.TXT"))
    assert text == "Резюме"


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (_document("cv.pdf"), "формате .txt"),
        (_document("cv.txt", size=MAX_TXT_BYTES + 1), "слишком большой"),
    ],
)
async def test_txt_document_is_rejected(document, message):
    with pytest.raises(TxtFileError, match=message):
        await read_txt_document(FakeBot(b""), document)
