"""Requests about people are outside Groundtruth's scope."""

import re

PEOPLE = re.compile(r"\b(who owns?|owner(?:ship)?|resident(?:s)?|homeowner(?:s)?|people|person|school district|safest neighbourhood|safest neighborhood)\b", re.I)


def declined_reason(request: str) -> str | None:
    return "people_data" if PEOPLE.search(request) else None
