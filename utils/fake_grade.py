from __future__ import annotations

import re


MAX_GRADE = 3


_GRADE_RULES = (
    {
        "name": "torg",
        "score": 1,
        "include": (
            r"\bторг\b",
            r"\bмикро\s+торг\b",
            r"\bнебольш[а-я]*\s+торг\b",
            r"\bцена\s+обсужда[а-я]*\b",
            r"\bваш[иы]\s+цен[аы]\b",
            r"\bпредлагай[а-я]*\s+цен[аы]\b",
        ),
        "exclude": (
            r"\bбез\s+торга?\b",
            r"\bнет\s+торга\b",
            r"\bторга\s+нет\b",
            r"\bторг\s+не\s+",
        ),
    },
    {
        "name": "trade_in",
        "score": 2,
        "include": (
            r"\bобмен\b",
            r"\btrade[- ]?in\b",
            r"\bтрейд[- ]?ин\b",
            r"\bтрейдин\b",
        ),
        "exclude": (
            r"\bбез\s+обмена\b",
            r"\bобмена\s+нет\b",
            r"\bобмен\s+не\s+",
            r"\bбез\s+trade[- ]?in\b",
            r"\bбез\s+трейд[- ]?ин\b",
        ),
    },
    {
        "name": "installment",
        "score": 2,
        "include": (
            r"\bрассроч[а-я]*\b",
            r"\bпо\s+частям\b",
            r"\bоплата\s+частями\b",
            r"\bперв[а-я]+\s+взнос\b",
            r"\bкредит\b",
        ),
        "exclude": (
            r"\bбез\s+рассроч[а-я]*\b",
            r"\bрассроч[а-я]*\s+нет\b",
            r"\bрассроч[а-я]*\s+не\s+",
            r"\bбез\s+кредита\b",
        ),
    },
    {
        "name": "price_scheme",
        "score": 3,
        "include": (
            r"\bцен[аы].{0,40}\bс\s+уч[её]том\b",
            r"\bцен[аы].{0,40}\bпри\s+обмен[еа]\b",
            r"\bцен[аы].{0,40}\bпри\s+trade[- ]?in\b",
            r"\bцен[аы].{0,40}\bпри\s+трейд[- ]?ин\b",
            r"\bцен[аы].{0,40}\bпри\s+рассроч[а-я]*\b",
            r"\bцен[аы].{0,40}\bв\s+рассроч[а-я]*\b",
            r"\bцен[аы].{0,40}\bпри\s+кредит[еа]\b",
        ),
        "exclude": (
            r"\bбез\s+торга\b",
            r"\bбез\s+обмена\b",
            r"\bбез\s+рассроч[а-я]*\b",
            r"\bбез\s+trade[- ]?in\b",
        ),
    },
)


_GRADE_COMPILED = tuple(
    {
        **rule,
        "include": tuple(re.compile(pattern, re.I | re.S) for pattern in rule["include"]),
        "exclude": tuple(re.compile(pattern, re.I | re.S) for pattern in rule["exclude"]),
    }
    for rule in _GRADE_RULES
)


def _normalize_text(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip().lower().replace("ё", "е"))


def score_description(text: str) -> tuple[int, list[str]]:
    """Returns fake_grade from 0 to -3 and matched rule names."""
    normalized = _normalize_text(text)
    hits: list[str] = []
    scores: list[int] = []

    for rule in _GRADE_COMPILED:
        has_include = any(pattern.search(normalized) for pattern in rule["include"])
        has_exclude = any(pattern.search(normalized) for pattern in rule["exclude"])
        if has_include and not has_exclude:
            hits.append(str(rule["name"]))
            scores.append(int(rule["score"]))

    if not scores:
        return 0, []

    grade = max(scores)
    if len(hits) > 1 and grade < MAX_GRADE:
        grade += 1
    return -min(MAX_GRADE, grade), hits


def fake_grade_for_text(value: object) -> int:
    return score_description(str(value or ""))[0]
