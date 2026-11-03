from html import escape

from app.enums import QuestionSource, ResponseStatus, Verdict
from app.hh.models import HHResponse
from app.interview.models import Interview, InterviewQuestion
from app.llm.schemas import ResumeAnalysis
from app.services.scoring import (
    CONSIDER_FROM,
    MUST_HAVE_MIN_SCORE,
    RECOMMEND_FROM,
    CriterionScore,
    criterion_scores,
)
from app.vacancy.models import Vacancy

VERDICT_LABELS = {
    Verdict.RECOMMENDED: "Рекомендован к следующему этапу",
    Verdict.CONSIDER: "На усмотрение рекрутера",
    Verdict.REJECTED: "Не рекомендован",
}

SOURCE_LABELS = {
    QuestionSource.COMPANY: "вопрос компании",
    QuestionSource.GENERATED: "обязательный, сгенерирован",
    QuestionSource.RESUME: "по резюме",
}


def _fmt(score: float | None) -> str:
    return "—" if score is None else f"{score:.1f}".removesuffix(".0")


def render_created(interview: Interview, vacancy: Vacancy, questions_count: int, link: str) -> str:
    analysis = ResumeAnalysis.model_validate(interview.resume_analysis)
    matches = {m.criterion_id: m.score for m in analysis.criteria_matches}
    lines = [
        "<b>Интервью создано</b>",
        f"Кандидат: {escape(interview.candidate_name)}",
        f"Вакансия: {escape(vacancy.title)}",
        "",
        escape(analysis.summary),
        "",
        "<b>Резюме vs критерии</b>",
    ]
    lines += [f"• {escape(c.title)}: {_fmt(matches.get(c.id))}/5" for c in vacancy.criteria]
    lines += [
        "",
        f"Вопросов: {questions_count}",
        f"Ссылка для кандидата:\n{link}",
    ]
    return "\n".join(lines)


def render_screened(response: HHResponse, vacancy: Vacancy) -> str:
    analysis = ResumeAnalysis.model_validate(response.resume_analysis)
    matches = {m.criterion_id: m for m in analysis.criteria_matches}
    lines = [
        "<b>Новый отклик</b>",
        f"Вакансия: {escape(vacancy.title)}",
        f"Кандидат: {escape(analysis.candidate_name)}",
        f"Резюме: <b>{_fmt(response.resume_score)}/100</b>",
        "",
        escape(analysis.summary),
        "",
    ]
    for c in vacancy.criteria:
        match = matches.get(c.id)
        must = " (must-have)" if c.is_must_have else ""
        score = match.score if match else "—"
        lines.append(f"• {escape(c.title)}{must}: {score}/5")
    return "\n".join(lines)


RESPONSE_MARKS = {
    ResponseStatus.SCREENED: "",
    ResponseStatus.INVITED: " · приглашён",
    ResponseStatus.REJECTED: " · отклонён",
}


def render_ranking(vacancy: Vacancy, responses: list[HHResponse]) -> str:
    if not responses:
        return f"По вакансии «{escape(vacancy.title)}» пока нет разобранных откликов."
    lines = [f"<b>Отклики: {escape(vacancy.title)}</b>", "Пригласить — нажмите на команду.", ""]
    for place, r in enumerate(responses, start=1):
        invite = f" /invite_{r.id}" if r.status is ResponseStatus.SCREENED else ""
        lines.append(
            f"{place}. {escape(r.candidate_name or '—')} — {_fmt(r.resume_score)}/100"
            f"{RESPONSE_MARKS[r.status]}{invite}"
        )
    return "\n".join(lines)


def render_coverage(scores: list[CriterionScore]) -> str:
    checked = sum(1 for s in scores if s.score is not None)
    text = f"Проверено критериев: {checked} из {len(scores)}"
    if checked < len(scores):
        text += " — итог посчитан не по всему профилю вакансии"
    return text


def render_summary(interview: Interview) -> str:
    scores = criterion_scores(interview.vacancy.criteria, interview.questions)
    discrepancies = sum(1 for q in interview.questions if q.discrepancy)
    lines = [
        "<b>Интервью завершено</b>",
        f"Кандидат: {escape(interview.candidate_name)}",
        f"Вакансия: {escape(interview.vacancy.title)}",
        f"Итог: <b>{_fmt(interview.total_score)}/100</b> — {VERDICT_LABELS[interview.verdict]}",
        render_coverage(scores),
        "",
    ]
    for s in scores:
        must = " (must-have)" if s.criterion.is_must_have else ""
        lines.append(f"• {escape(s.criterion.title)}{must}: {_fmt(s.score)}/5")
    lines += ["", f"Расхождений с резюме: {discrepancies}", "Полный отчёт — в файле."]
    return "\n".join(lines)


_CSS = """
body { font-family: system-ui, sans-serif; max-width: 960px; margin: 24px auto;
       padding: 0 16px; color: #1f2328; line-height: 1.5; }
h1 { margin-bottom: 4px; } .muted { color: #656d76; font-size: 14px; }
.verdict { font-size: 20px; margin: 16px 0; padding: 12px 16px; border-radius: 8px;
           background: #f6f8fa; }
table { border-collapse: collapse; width: 100%; } td, th { border: 1px solid #d0d7de;
        padding: 6px 10px; text-align: left; vertical-align: top; }
th { background: #f6f8fa; } .q { border: 1px solid #d0d7de; border-radius: 8px;
     padding: 12px 16px; margin: 12px 0; } .q h3 { margin: 0 0 8px; }
.answer { white-space: pre-wrap; background: #f6f8fa; padding: 8px 12px; border-radius: 6px; }
.warn { color: #9a6700; } .bad { color: #cf222e; } a { color: #0969da; }
"""


def render_html(interview: Interview) -> str:
    analysis = ResumeAnalysis.model_validate(interview.resume_analysis)
    scores = criterion_scores(interview.vacancy.criteria, interview.questions)
    finished = interview.finished_at.strftime("%d.%m.%Y %H:%M UTC") if interview.finished_at else ""
    verdict = VERDICT_LABELS[interview.verdict] if interview.verdict else "—"
    hh = (
        f" · отклик hh {escape(interview.hh_negotiation_id)}" if interview.hh_negotiation_id else ""
    )
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Интервью: {escape(interview.candidate_name)}</title><style>{_CSS}</style></head>
<body>
<h1>{escape(interview.candidate_name)}</h1>
<div class="muted">{escape(interview.vacancy.title)} · {finished} · id {interview.id}{hh}</div>
<div class="verdict">Итог: <b>{_fmt(interview.total_score)}/100</b> — {verdict}
<div class="muted">{render_coverage(scores)}</div></div>
<h2>Критерии</h2>
{_criteria_table(scores, analysis, interview.questions)}
{_discrepancies(interview.questions)}
<h2>Резюме</h2>
<p>{escape(analysis.summary)}</p>
<p class="muted">Стаж: {_fmt(analysis.total_experience_years)} лет ·
Навыки: {escape(", ".join(analysis.skills))}</p>
<h2>Вопросы и ответы</h2>
{"".join(_question_block(q, interview) for q in interview.questions)}
<h2>Как считается итог</h2>
<p class="muted">Каждый ответ оценивается по шкале 0–5. Балл критерия — среднее по его вопросам,
итог — взвешенное среднее по проверенным критериям, переведённое в 0–100.
Must-have ниже {MUST_HAVE_MIN_SCORE} — «не рекомендован». Иначе от {RECOMMEND_FROM} —
«рекомендован», от {CONSIDER_FROM} — «на усмотрение», ниже — «не рекомендован».
Колонка «Резюме» в итог не входит, это ориентир для сравнения.</p>
</body></html>
"""


def _criteria_table(
    scores: list[CriterionScore], analysis: ResumeAnalysis, questions: list[InterviewQuestion]
) -> str:
    matches = {m.criterion_id: m for m in analysis.criteria_matches}
    rows = []
    for s in scores:
        c = s.criterion
        match = matches.get(c.id)
        resume = (
            f"{match.score}<div class='muted'>{escape(match.rationale)}</div>" if match else "—"
        )
        links = ", ".join(
            f"<a href='#q{q.position + 1}'>{q.position + 1}</a>"
            for q in questions
            if q.criterion_id == c.id
        )
        low = c.is_must_have and s.score is not None and s.score < MUST_HAVE_MIN_SCORE
        interview_score = f"<span class='bad'>{_fmt(s.score)}</span>" if low else _fmt(s.score)
        rows.append(
            f"<tr><td><b>{escape(c.title)}</b><div class='muted'>{escape(c.description)}</div>"
            f"</td><td>{c.weight}</td><td>{'да' if c.is_must_have else ''}</td>"
            f"<td>{resume}</td><td>{interview_score}</td><td>{links or '—'}</td></tr>"
        )
    return (
        "<table><tr><th>Критерий</th><th>Вес</th><th>Must-have</th>"
        "<th>Резюме, 0–5</th><th>Интервью, 0–5</th><th>Вопросы</th></tr>"
        f"{''.join(rows)}</table>"
    )


def _discrepancies(questions: list[InterviewQuestion]) -> str:
    items = [
        f"<li><a href='#q{q.position + 1}'>Вопрос {q.position + 1}</a>: "
        f"{escape(q.discrepancy)}</li>"
        for q in questions
        if q.discrepancy
    ]
    if not items:
        return ""
    return f"<h2>Расхождения с резюме</h2><ul>{''.join(items)}</ul>"


def _question_block(q: InterviewQuestion, interview: Interview) -> str:
    criterion = next(c for c in interview.vacancy.criteria if c.id == q.criterion_id)
    parts = [
        f"<div class='q' id='q{q.position + 1}'>",
        f"<h3>{q.position + 1}. {escape(q.text)}</h3>",
        f"<div class='muted'>{escape(criterion.title)} · {SOURCE_LABELS[q.source]} · "
        f"балл <b>{_fmt(q.score)}</b>/5</div>",
        f"<p class='answer'>{escape(q.answer or 'нет ответа')}</p>",
    ]
    if q.follow_up:
        parts += [
            f"<p><b>Уточнение:</b> {escape(q.follow_up)}</p>",
            f"<p class='answer'>{escape(q.follow_up_answer or 'нет ответа')}</p>",
        ]
    if q.rationale:
        parts.append(f"<p><b>Обоснование:</b> {escape(q.rationale)}</p>")
    if q.evidence:
        mark = "" if q.evidence_found else " <span class='warn'>⚠ цитата не найдена в ответе</span>"
        parts.append(f"<p><b>Цитата:</b> «{escape(q.evidence)}»{mark}</p>")
    if q.discrepancy:
        parts.append(f"<p class='bad'><b>Расхождение с резюме:</b> {escape(q.discrepancy)}</p>")
    parts.append("</div>")
    return "".join(parts)
