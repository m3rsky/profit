"""Czas pracy nad listą kontrolną: odcinki pracy (`ChecklistSession`) liczone tylko, gdy użytkownik jest przy liście.

Zasady:
- wejście na listę otwiera odcinek pracy, wyjście (inna strona, zamknięcie karty, ukrycie karty, zablokowanie ekranu,
  wylogowanie) go zamyka, a powrót otwiera nowy;
- każdy zapis punktu i aktywny sygnał z przeglądarki odświeża `last_activity_at` odcinka;
- odcinek zamykany z powodu bezczynności (po `work_idle_minutes`, domyślnie 60 minut) kończy się w chwili ostatniej
  aktywności plus krótki zapas (`GRACE`), więc czas bez aktywności nigdy się nie sumuje;
- czas listy to suma odcinków po scaleniu nakładających się.
Daty w bazie są naiwne (UTC), tak jak w reszcie aplikacji.
"""
import time
from datetime import datetime, timedelta, UTC

from sqlalchemy import func, or_

from models import db, AppSetting, ChecklistSession, Report, ReportItem

GRACE = timedelta(seconds=120)
IDLE_DEFAULT_MINUTES = 60
IDLE_MIN, IDLE_MAX = 5, 480
_SWEEP_EVERY = 60          # sekundy między sprzątaniami bezczynnych odcinków (na proces)
_last_sweep = 0.0


def now():
    return datetime.now(UTC).replace(tzinfo=None)


def _naive(dt):
    return dt.replace(tzinfo=None) if dt is not None and dt.tzinfo is not None else dt


def idle_minutes():
    try:
        value = int(AppSetting.get('work_idle_minutes', IDLE_DEFAULT_MINUTES))
    except (TypeError, ValueError):
        return IDLE_DEFAULT_MINUTES
    return value if IDLE_MIN <= value <= IDLE_MAX else IDLE_DEFAULT_MINUTES


def idle_limit():
    return timedelta(minutes=idle_minutes())


def save_idle_minutes(raw):
    """Zapisuje próg bezczynności. Zwraca komunikat błędu albo None."""
    try:
        value = int(str(raw).strip())
    except ValueError:
        return 'Podaj liczbę minut.'
    if not IDLE_MIN <= value <= IDLE_MAX:
        return f'Dozwolone od {IDLE_MIN} do {IDLE_MAX} minut.'
    AppSetting.set('work_idle_minutes', value)
    db.session.commit()
    return None


def last_activity(session):
    """Ostatnia znana aktywność odcinka. Dla starych odcinków bez `last_activity_at` jest to ostatni zapis punktu."""
    started = _naive(session.started_at)
    if session.last_activity_at is not None:
        return max(_naive(session.last_activity_at), started)
    latest = (db.session.query(func.max(ReportItem.checked_at))
              .filter(ReportItem.report_id == session.report_id, ReportItem.checked_at >= started).scalar())
    return max(_naive(latest), started) if latest else started


def close_session(session, at=None, explicit=True):
    """Zamyka odcinek. `explicit` (wyjście użytkownika): koniec to `at`, o ile ostatnia aktywność nie jest starsza niż
    próg bezczynności. Inaczej (bezczynność, wygaśnięcie sesji) koniec to ostatnia aktywność plus `GRACE`."""
    at = _naive(at) or now()
    last = last_activity(session)
    if explicit and at - last <= idle_limit():
        end = at
    else:
        end = min(at, last + GRACE)
    session.ended_at = max(end, _naive(session.started_at))
    return session.ended_at


def open_sessions(report_id):
    return (ChecklistSession.query.filter_by(report_id=report_id, ended_at=None)
            .order_by(ChecklistSession.id).all())


def has_open(report_id):
    return ChecklistSession.query.filter_by(report_id=report_id, ended_at=None).count() > 0


def close_open(report_id, at=None, explicit=True):
    for s in open_sessions(report_id):
        close_session(s, at, explicit)


def open_session(report, at=None):
    """Otwiera nowy odcinek pracy (poprzednie otwarte odcinki tej listy są zamykane wg zasad bezczynności)."""
    at = _naive(at) or now()
    close_open(report.id, at, explicit=False)
    if report.started_at is None:
        report.started_at = at
    s = ChecklistSession(report_id=report.id, started_at=at, last_activity_at=at)
    db.session.add(s)
    return s


def touch(report, at=None):
    """Zapis aktywności: odświeża otwarty odcinek, a gdy go nie ma (np. zamknięty z bezczynności), otwiera nowy."""
    if report.status != 'in_progress':
        return None
    at = _naive(at) or now()
    sessions = open_sessions(report.id)
    if not sessions:
        return open_session(report, at)
    for extra in sessions[:-1]:           # zostaje najnowszy, starsze zamykamy
        close_session(extra, at, explicit=False)
    current = sessions[-1]
    current.last_activity_at = at
    return current


def total_seconds(report_id):
    """Suma czasu zamkniętych odcinków po scaleniu nakładających się. None, gdy brak czasu."""
    rows = (ChecklistSession.query.filter(ChecklistSession.report_id == report_id,
                                          ChecklistSession.ended_at.isnot(None))
            .order_by(ChecklistSession.started_at).all())
    total, cur_start, cur_end = 0.0, None, None
    for s in rows:
        a, b = _naive(s.started_at), _naive(s.ended_at)
        if b <= a:
            continue
        if cur_end is None or a > cur_end:
            if cur_end is not None:
                total += (cur_end - cur_start).total_seconds()
            cur_start, cur_end = a, b
        else:
            cur_end = max(cur_end, b)
    if cur_end is not None:
        total += (cur_end - cur_start).total_seconds()
    return int(total) if total > 0 else None


def finish(report, at=None):
    """Zamyka wszystkie odcinki listy przy jej zakończeniu i zwraca łączny czas pracy (sekundy) albo None."""
    at = _naive(at) or now()
    close_open(report.id, at, explicit=True)
    db.session.flush()
    return total_seconds(report.id)


def close_user_sessions(user_id, at=None, expired=False):
    """Zamyka odcinki list, które użytkownik ma zablokowane (wylogowanie albo wygaśnięcie sesji logowania)."""
    at = _naive(at) or now()
    sessions = (ChecklistSession.query.join(Report, Report.id == ChecklistSession.report_id)
                .filter(ChecklistSession.ended_at.is_(None), Report.locked_by_id == user_id).all())
    for s in sessions:
        close_session(s, at, explicit=not expired)
    if sessions:
        db.session.commit()
    return len(sessions)


def sweep(at=None, force=False):
    """Zamyka odcinki bezczynne dłużej niż próg (koniec: ostatnia aktywność plus `GRACE`).
    Bez harmonogramu: wywoływane przy żądaniach, nie częściej niż raz na minutę na proces."""
    global _last_sweep
    if not force and time.monotonic() - _last_sweep < _SWEEP_EVERY:
        return 0
    _last_sweep = time.monotonic()
    at = _naive(at) or now()
    cutoff = at - idle_limit()
    stale = ChecklistSession.query.filter(
        ChecklistSession.ended_at.is_(None),
        or_(ChecklistSession.last_activity_at < cutoff,
            (ChecklistSession.last_activity_at.is_(None)) & (ChecklistSession.started_at < cutoff))).all()
    closed = 0
    for s in stale:
        if at - last_activity(s) > idle_limit():
            close_session(s, at, explicit=False)
            closed += 1
    if closed:
        db.session.commit()
    return closed
