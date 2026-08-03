"""Agregacja danych, anonimizacja i wywołanie Claude API dla modułu 'Poranny Briefing'."""
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import anthropic
from flask import current_app
from sqlalchemy import case, func

from models import DepartmentEmployee, QARReport, Report, ReportItem, Task, User, db

MODEL_NAME = 'claude-sonnet-5'

SYSTEM_PROMPT = """Jesteś analitykiem jakości i produkcji w firmie produkującej szafy metalowe \
(spawanie, obróbka blachy, kontrola jakości, montaż).

Otrzymujesz WYŁĄCZNIE zagregowane dane liczbowe w formacie JSON za wybrany okres. Wszystkie \
identyfikatory w danych (np. "Monter #3", "Kontroler #1") to pseudonimy — używaj ich dosłownie, \
nigdy nie wymyślaj prawdziwych imion ani nazwisk i nie zakładaj, że reprezentują konkretną osobę \
poza tym pseudonimem.

Przeanalizuj WYŁĄCZNIE podane dane (bez zmyślania faktów spoza nich) i przygotuj krótki raport \
w języku polskim, w formacie Markdown, z dokładnie trzema sekcjami:

## Podsumowanie
Zwięzłe podsumowanie stanu jakości i produkcji w tym okresie.

## Anomalie i trendy
Wykryte odchylenia, wzrosty/spadki, nietypowe wzorce w danych. Jeśli brak wyraźnych anomalii, \
napisz "Brak istotnych anomalii w tym okresie."

## Priorytety
Rekomendowane priorytety/obszary do uwagi na najbliższy okres, wynikające bezpośrednio z danych.

Zachowaj długość 300-600 słów. Jeśli w jakiejś kategorii brak danych, napisz "brak danych" \
zamiast dopowiadać.
"""


class BriefingGenerationError(Exception):
    """Błąd generowania briefingu, który nie jest bezpośrednim błędem HTTP Claude API."""


def _date_bounds(start_date, end_date):
    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date, datetime.min.time()) + timedelta(days=1)
    return start_dt, end_dt


def _pseudonym_map_for_employees(employee_ids):
    """DepartmentEmployee.id -> '{Dział} #{N}', numerowane per dział w ramach jednego uruchomienia."""
    if not employee_ids:
        return {}
    employees = (DepartmentEmployee.query
                 .filter(DepartmentEmployee.id.in_(employee_ids))
                 .order_by(DepartmentEmployee.department_id, DepartmentEmployee.id)
                 .all())
    counters = defaultdict(int)
    mapping = {}
    for emp in employees:
        dept_name = emp.department.name if emp.department else 'Dział nieznany'
        counters[dept_name] += 1
        mapping[emp.id] = f'{dept_name} #{counters[dept_name]}'
    return mapping


def _pseudonym_map_for_users(user_ids):
    """User.id -> '{Rola} #{N}', numerowane per rola w ramach jednego uruchomienia."""
    if not user_ids:
        return {}
    users = User.query.filter(User.id.in_(user_ids)).order_by(User.role, User.id).all()
    counters = defaultdict(int)
    mapping = {}
    for u in users:
        role = u.role_label
        counters[role] += 1
        mapping[u.id] = f'{role} #{counters[role]}'
    return mapping


def aggregate_qar(start_date, end_date):
    """Zagregowane, zanonimizowane statystyki niezgodności QAR w zakresie [start_date, end_date]."""
    start_dt, end_dt = _date_bounds(start_date, end_date)
    reports = (QARReport.query
               .filter(QARReport.created_at >= start_dt, QARReport.created_at < end_dt)
               .all())

    employee_ids = {r.employee_id for r in reports if r.employee_id}
    author_ids = {r.user_id for r in reports if r.user_id}
    employee_map = _pseudonym_map_for_employees(employee_ids)
    author_map = _pseudonym_map_for_users(author_ids)

    by_status = Counter(r.status for r in reports)
    by_category = Counter(r.category or 'Inne' for r in reports)

    dept_counts = Counter()
    employee_counts = Counter()
    for r in reports:
        if r.employee_id:
            dept_name = r.employee.department.name if r.employee and r.employee.department else 'Nieprzypisany'
            dept_counts[dept_name] += 1
            employee_counts[r.employee_id] += 1

    top_employees = [
        {'label': employee_map[emp_id], 'count': cnt}
        for emp_id, cnt in employee_counts.most_common(10)
    ]

    author_counts = Counter(r.user_id for r in reports if r.user_id)
    top_authors = [
        {'label': author_map[uid], 'count': cnt}
        for uid, cnt in author_counts.most_common(10)
    ]

    return {
        'period': {'start': start_date.isoformat(), 'end': end_date.isoformat()},
        'total': len(reports),
        'by_status': dict(by_status),
        'by_category': dict(by_category),
        'by_department': dict(dept_counts),
        'top_employees_by_report_count': top_employees,
        'top_report_authors': top_authors,
    }


def aggregate_qc(start_date, end_date):
    """Zagregowane, zanonimizowane statystyki kontroli jakości (checklisty) w zakresie dat."""
    start_str = start_date.isoformat()
    end_str = end_date.isoformat()
    _day_expr = func.substr(Report.completed_at, 1, 10)

    reports = (Report.query
               .filter(Report.status == 'completed',
                       _day_expr >= start_str, _day_expr <= end_str)
               .all())

    author_ids = {r.user_id for r in reports if r.user_id}
    author_map = _pseudonym_map_for_users(author_ids)

    by_report_type = Counter(r.report_type for r in reports)

    result_counts = {r: c for r, c in
        db.session.query(ReportItem.result, func.count(ReportItem.id))
        .join(Report, Report.id == ReportItem.report_id)
        .filter(Report.status == 'completed', _day_expr >= start_str, _day_expr <= end_str)
        .group_by(ReportItem.result).all()}

    durations = defaultdict(list)
    grades = []
    for r in reports:
        if r.duration_seconds is not None:
            durations[r.report_type].append(r.duration_seconds)
        score = r.score
        if score is not None:
            grades.append(score['grade'])

    avg_duration_seconds = {
        rtype: round(sum(vals) / len(vals)) for rtype, vals in durations.items() if vals
    }
    avg_score_grade = round(sum(grades) / len(grades), 2) if grades else None

    _norm = lambda col: func.lower(func.trim(func.rtrim(func.trim(col), '.')))
    ng_tasks_raw = (db.session.query(
        func.min(Task.title).label('title'),
        func.count(ReportItem.id).label('cnt'),
    ).join(ReportItem, ReportItem.task_id == Task.id)
     .join(Report, Report.id == ReportItem.report_id)
     .filter(ReportItem.result == 'ng', Report.status == 'completed',
             _day_expr >= start_str, _day_expr <= end_str)
     .group_by(_norm(Task.title)).order_by(func.count(ReportItem.id).desc())
     .limit(10).all())
    top_ng_tasks = [{'title': t.title, 'count': t.cnt} for t in ng_tasks_raw]

    author_counts = Counter(r.user_id for r in reports if r.user_id)
    top_authors = [
        {'label': author_map[uid], 'count': cnt}
        for uid, cnt in author_counts.most_common(10)
    ]

    return {
        'period': {'start': start_str, 'end': end_str},
        'total_completed': len(reports),
        'by_report_type': dict(by_report_type),
        'result_counts': {
            'ok': result_counts.get('ok', 0),
            'ng': result_counts.get('ng', 0),
            'na': result_counts.get('na', 0),
            'dw': result_counts.get('dw', 0),
            'none': result_counts.get(None, 0),
        },
        'avg_duration_seconds_by_report_type': avg_duration_seconds,
        'avg_score_grade': avg_score_grade,
        'top_ng_tasks': top_ng_tasks,
        'top_report_authors': top_authors,
    }


def _get_client():
    return anthropic.Anthropic(api_key=current_app.config.get('ANTHROPIC_API_KEY') or None)


def call_claude(payload, client=None):
    """Wywołuje Claude API z zanonimizowanym payloadem i zwraca {content, model, input_tokens, output_tokens}."""
    client = client or _get_client()
    response = client.messages.create(
        model=MODEL_NAME,
        max_tokens=3000,
        thinking={'type': 'disabled'},
        system=SYSTEM_PROMPT,
        messages=[{'role': 'user', 'content': json.dumps(payload, ensure_ascii=False, indent=2)}],
    )
    if response.stop_reason == 'refusal':
        raise BriefingGenerationError('Claude odmówił wygenerowania analizy dla podanych danych.')
    text = ''.join(block.text for block in response.content if block.type == 'text')
    return {
        'content': text,
        'model': response.model,
        'input_tokens': response.usage.input_tokens,
        'output_tokens': response.usage.output_tokens,
    }


def generate_briefing_payload(start_date, end_date):
    """Buduje pełny, zanonimizowany payload (QAR + QC) gotowy do wysyłki do Claude API."""
    return {
        'okres': {'od': start_date.isoformat(), 'do': end_date.isoformat()},
        'niezgodnosci_qar': aggregate_qar(start_date, end_date),
        'kontrola_jakosci': aggregate_qc(start_date, end_date),
    }
