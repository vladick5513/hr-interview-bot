from enum import StrEnum


class InterviewStatus(StrEnum):
    CREATED = "created"
    IN_PROGRESS = "in_progress"
    FINISHED = "finished"


class QuestionSource(StrEnum):
    COMPANY = "company"
    GENERATED = "generated"
    RESUME = "resume"


class QuestionMode(StrEnum):
    GENERATE = "generate"
    COMPANY = "company"
    COMPANY_PLUS = "company_plus"


class ResponseStatus(StrEnum):
    NEW = "new"
    SCREENED = "screened"
    INVITED = "invited"
    REJECTED = "rejected"
    FAILED = "failed"


class Verdict(StrEnum):
    RECOMMENDED = "recommended"
    CONSIDER = "consider"
    REJECTED = "rejected"
