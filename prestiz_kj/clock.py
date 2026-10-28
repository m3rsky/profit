"""Zegar modułu. Zwykle zwraca aktualny czas. Generator danych historycznych ustawia czas tymczasowo, żeby kontrole,
zawroty i meldunki powstawały tymi samymi funkcjami co na ekranach, ale z datami z ostatnich tygodni."""
from datetime import datetime, timezone

_override = None


def now():
    return _override or datetime.now(timezone.utc)


def set_time(ts):
    global _override
    _override = ts


def clear():
    set_time(None)
