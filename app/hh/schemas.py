import html
import re

from pydantic import BaseModel


class HHNamed(BaseModel):
    name: str


class HHExperience(BaseModel):
    start: str | None = None
    end: str | None = None
    company: str | None = None
    position: str | None = None
    description: str | None = None


class HHEducationItem(BaseModel):
    name: str | None = None
    organization: str | None = None
    year: int | None = None


class HHEducation(BaseModel):
    level: HHNamed | None = None
    primary: list[HHEducationItem] = []


class HHLanguage(BaseModel):
    name: str
    level: HHNamed | None = None


class HHTotalExperience(BaseModel):
    months: int


class HHResume(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    middle_name: str | None = None
    title: str | None = None
    total_experience: HHTotalExperience | None = None
    skill_set: list[str] = []
    skills: str | None = None
    experience: list[HHExperience] = []
    education: HHEducation | None = None
    language: list[HHLanguage] = []

    def to_text(self) -> str:
        name = " ".join(p for p in (self.last_name, self.first_name, self.middle_name) if p)
        lines = [name or "Кандидат"]
        if self.title:
            lines.append(f"Желаемая должность: {self.title}")
        if self.total_experience:
            years, months = divmod(self.total_experience.months, 12)
            lines.append(f"Общий стаж: {years} лет {months} мес.")
        if self.skill_set:
            lines.append(f"Навыки: {', '.join(self.skill_set)}")
        if self.skills:
            lines += ["", "О себе:", self.skills]
        if self.experience:
            lines += ["", "Опыт работы:"]
        for job in self.experience:
            period = f"{job.start or '?'} — {job.end or 'по настоящее время'}"
            lines.append(f"- {period}, {job.company or '—'}, {job.position or '—'}")
            if job.description:
                lines.append(job.description)
        if self.education and (self.education.level or self.education.primary):
            level = self.education.level.name if self.education.level else ""
            lines += ["", f"Образование: {level}"]
            lines += [
                f"- {e.name or '—'}, {e.organization or '—'}, {e.year or '—'}"
                for e in self.education.primary
            ]
        if self.language:
            languages = ", ".join(
                f"{lang.name} ({lang.level.name})" if lang.level else lang.name
                for lang in self.language
            )
            lines += ["", f"Языки: {languages}"]
        return "\n".join(lines)


class HHVacancy(BaseModel):
    name: str
    description: str
    key_skills: list[HHNamed] = []

    def to_text(self) -> str:
        body = html.unescape(re.sub(r"<[^>]+>", "\n", self.description))
        body = re.sub(r"\n\s*\n+", "\n", body).strip()
        text = f"{self.name}\n\n{body}"
        if self.key_skills:
            text += f"\n\nКлючевые навыки: {', '.join(s.name for s in self.key_skills)}"
        return text


class HHTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int


class HHNegotiationResume(BaseModel):
    url: str


class HHNegotiation(BaseModel):
    id: str
    resume: HHNegotiationResume | None


class HHSubscriptionAction(BaseModel):
    type: str


class HHSubscription(BaseModel):
    id: str
    url: str
    actions: list[HHSubscriptionAction]


class HHSubscriptions(BaseModel):
    items: list[HHSubscription]


class HHWebhookEvent(BaseModel):
    id: str
    action_type: str
    payload: dict


class HHNewNegotiationPayload(BaseModel):
    topic_id: str
    vacancy_id: str | None
