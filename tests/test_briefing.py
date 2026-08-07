"""Testy modułu 'Poranny Briefing' — agregacja, anonimizacja, trasy /admin/briefing/*.

Wywołania Claude API są zawsze mockowane — te testy nigdy nie łączą się z siecią.
"""
import json
import os
from datetime import date, datetime
from types import SimpleNamespace

import pytest

import briefing_service
from app import app as flask_app, db
from models import (ChecklistTemplate, Category, Task, Report, ReportItem,
                    QARReport, ProductionDepartment, DepartmentEmployee,
                    User, DailyBriefing)
from test_app import login, logout, _csrf

QC_START = date(2019, 1, 1)
QC_END   = date(2019, 1, 31)


@pytest.fixture(scope='module')
def briefing_fixtures(app):
    """Dane testowe odizolowane datą (2019-01) od reszty zestawu testów."""
    with app.app_context():
        admin = User.query.filter_by(username='admin').first()
        oper  = User.query.filter_by(username='oper').first()

        tmpl = ChecklistTemplate(name='Briefing szablon', is_active=True, template_type='kontroler')
        db.session.add(tmpl)
        db.session.flush()
        cat = Category(template_id=tmpl.id, name='Kategoria briefing', order=0)
        db.session.add(cat)
        db.session.flush()
        task_ok = Task(category_id=cat.id, title='Zadanie A', order=0, is_active=True)
        task_ng = Task(category_id=cat.id, title='Zadanie B', order=1, is_active=True)
        db.session.add_all([task_ok, task_ng])
        db.session.flush()

        dept = ProductionDepartment(name='Briefing-Spawalnia')
        db.session.add(dept)
        db.session.flush()
        employee = DepartmentEmployee(department_id=dept.id, name='Pracownik Testowy')
        db.session.add(employee)
        db.session.flush()

        # 2 zgłoszenia QAR w zakresie, 1 poza zakresem
        qar_in_1 = QARReport(number='QAR-BRIEF-0001', title='NCR 1', category='Spawanie',
                             description='opis 1', status='open',
                             user_id=oper.id, employee_id=employee.id,
                             created_at=datetime(2019, 1, 10))
        qar_in_2 = QARReport(number='QAR-BRIEF-0002', title='NCR 2', category='Spawanie',
                             description='opis 2', status='closed',
                             user_id=oper.id, employee_id=employee.id,
                             created_at=datetime(2019, 1, 20))
        qar_out = QARReport(number='QAR-BRIEF-0003', title='NCR poza zakresem', category='Inne',
                            description='opis 3', status='open',
                            user_id=oper.id, created_at=datetime(2020, 1, 1))
        db.session.add_all([qar_in_1, qar_in_2, qar_out])

        # 2 ukończone raporty QC w zakresie (1 OK-only, 1 z NG), 1 poza zakresem
        r1 = Report(user_id=oper.id, template_id=tmpl.id, title='Raport 1',
                    status='completed', report_type='kontroler',
                    completed_at=datetime(2019, 1, 15), duration_seconds=120)
        r2 = Report(user_id=oper.id, template_id=tmpl.id, title='Raport 2',
                    status='completed', report_type='kontroler',
                    completed_at=datetime(2019, 1, 16), duration_seconds=200)
        r_out = Report(user_id=oper.id, template_id=tmpl.id, title='Raport poza zakresem',
                       status='completed', report_type='kontroler',
                       completed_at=datetime(2020, 1, 1), duration_seconds=50)
        db.session.add_all([r1, r2, r_out])
        db.session.flush()

        db.session.add_all([
            ReportItem(report_id=r1.id, task_id=task_ok.id, is_checked=True, result='ok'),
            ReportItem(report_id=r1.id, task_id=task_ng.id, is_checked=True, result='ok'),
            ReportItem(report_id=r2.id, task_id=task_ok.id, is_checked=True, result='ok'),
            ReportItem(report_id=r2.id, task_id=task_ng.id, is_checked=True, result='ng'),
            ReportItem(report_id=r_out.id, task_id=task_ok.id, is_checked=True, result='ng'),
        ])
        db.session.commit()

        yield {'oper_username': oper.username, 'employee_name': employee.name}


class TestAggregation:
    def test_aggregate_qar_counts_only_within_range(self, app, briefing_fixtures):
        with app.app_context():
            result = briefing_service.aggregate_qar(QC_START, QC_END)
            assert result['total'] == 2
            assert result['by_status'] == {'open': 1, 'closed': 1}
            assert result['by_category'] == {'Spawanie': 2}
            assert result['by_department'] == {'Briefing-Spawalnia': 2}

    def test_aggregate_qar_pseudonymizes_employee(self, app, briefing_fixtures):
        with app.app_context():
            result = briefing_service.aggregate_qar(QC_START, QC_END)
            labels = [e['label'] for e in result['top_employees_by_report_count']]
            assert labels == ['Briefing-Spawalnia #1']
            assert result['top_employees_by_report_count'][0]['count'] == 2

    def test_aggregate_qc_counts_and_results(self, app, briefing_fixtures):
        with app.app_context():
            result = briefing_service.aggregate_qc(QC_START, QC_END)
            assert result['total_completed'] == 2
            assert result['by_report_type'] == {'kontroler': 2}
            assert result['result_counts']['ok'] == 3
            assert result['result_counts']['ng'] == 1
            assert result['avg_duration_seconds_by_report_type']['kontroler'] == 160
            ng_titles = [t['title'] for t in result['top_ng_tasks']]
            assert 'Zadanie B' in ng_titles

    def test_aggregate_qc_date_boundaries_exclusive(self, app, briefing_fixtures):
        with app.app_context():
            narrow = briefing_service.aggregate_qc(date(2019, 1, 15), date(2019, 1, 15))
            assert narrow['total_completed'] == 1


class TestAnonymization:
    def test_no_real_names_leak_into_payload(self, app, briefing_fixtures):
        with app.app_context():
            payload = briefing_service.generate_briefing_payload(QC_START, QC_END)
            dumped = json.dumps(payload, ensure_ascii=False)
            assert briefing_fixtures['oper_username'] not in dumped
            assert briefing_fixtures['employee_name'] not in dumped


class FakeAnthropicClient:
    def __init__(self, content='## Podsumowanie\nTest.', stop_reason='end_turn'):
        self._content = content
        self._stop_reason = stop_reason
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        return SimpleNamespace(
            content=[SimpleNamespace(type='text', text=self._content)],
            stop_reason=self._stop_reason,
            model='claude-sonnet-5',
            usage=SimpleNamespace(input_tokens=123, output_tokens=45),
        )


class TestCallClaude:
    def test_call_claude_parses_response(self, app, briefing_fixtures):
        with app.app_context():
            fake = FakeAnthropicClient(content='## Podsumowanie\nWszystko w porządku.')
            result = briefing_service.call_claude({'a': 1}, client=fake)
            assert result['content'] == '## Podsumowanie\nWszystko w porządku.'
            assert result['model'] == 'claude-sonnet-5'
            assert result['input_tokens'] == 123
            assert result['output_tokens'] == 45

    def test_call_claude_refusal_raises(self, app, briefing_fixtures):
        with app.app_context():
            fake = FakeAnthropicClient(stop_reason='refusal')
            with pytest.raises(briefing_service.BriefingGenerationError):
                briefing_service.call_claude({'a': 1}, client=fake)


class TestBriefingRoute:
    def test_generate_requires_login(self, client, briefing_fixtures):
        # Bez sesji nie ma poprawnego tokenu CSRF, więc hook CSRF blokuje żądanie
        # (403) zanim w ogóle dojdzie do sprawdzenia @login_required.
        resp = client.post('/admin/briefing/generate',
                           data={'start_date': '2019-01-01', 'end_date': '2019-01-31'})
        assert resp.status_code in (302, 401, 403)

    def test_generate_requires_admin(self, client, briefing_fixtures):
        login(client, 'oper', 'Oper1234!')
        token = _csrf(client)
        resp = client.post('/admin/briefing/generate',
                           data={'start_date': '2019-01-01', 'end_date': '2019-01-31'},
                           headers={'X-CSRF-Token': token})
        assert resp.status_code == 403
        logout(client)

    def test_generate_without_csrf_blocked(self, client, briefing_fixtures):
        login(client, 'admin', 'Admin1234!')
        resp = client.post('/admin/briefing/generate',
                           data={'start_date': '2019-01-01', 'end_date': '2019-01-31'})
        assert resp.status_code == 403
        logout(client)

    def test_generate_validates_dates(self, client, briefing_fixtures):
        login(client, 'admin', 'Admin1234!')
        token = _csrf(client)
        resp = client.post('/admin/briefing/generate',
                           data={'start_date': '', 'end_date': ''},
                           headers={'X-CSRF-Token': token})
        assert resp.status_code == 400

        resp = client.post('/admin/briefing/generate',
                           data={'start_date': '2019-02-01', 'end_date': '2019-01-01'},
                           headers={'X-CSRF-Token': token})
        assert resp.status_code == 400
        logout(client)

    def test_generate_success_creates_briefing(self, client, app, briefing_fixtures, monkeypatch):
        monkeypatch.setattr(briefing_service, 'call_claude',
                            lambda payload, client=None: {
                                'content': '## Podsumowanie\nOK.',
                                'model': 'claude-sonnet-5',
                                'input_tokens': 10, 'output_tokens': 5,
                            })
        with app.app_context():
            flask_app.config['ANTHROPIC_API_KEY'] = 'test-key'

        login(client, 'admin', 'Admin1234!')
        token = _csrf(client)
        resp = client.post('/admin/briefing/generate',
                           data={'start_date': '2019-01-01', 'end_date': '2019-01-31'},
                           headers={'X-CSRF-Token': token})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert 'Podsumowanie' in data['briefing']['content_html']

        with app.app_context():
            assert DailyBriefing.query.filter_by(id=data['briefing']['id']).count() == 1
        logout(client)

    def test_detail_requires_admin(self, client, app, briefing_fixtures):
        with app.app_context():
            b = DailyBriefing.query.first()
            bid = b.id if b else None
        if bid is None:
            pytest.skip('brak zapisanego briefingu do przetestowania')
        resp = client.get(f'/admin/briefing/{bid}')
        assert resp.status_code in (302, 401)


class TestBriefingPdf:
    def _make_briefing(self, app):
        with app.app_context():
            admin = User.query.filter_by(username='admin').first()
            b = DailyBriefing(
                start_date=QC_START, end_date=QC_END,
                generated_by_user_id=admin.id,
                model_used='claude-sonnet-5',
                input_token_count=10, output_token_count=5,
                content=('## Podsumowanie\nWszystko w porządku, **liczba NG** spadła.\n\n'
                         '## Anomalie i trendy\n- wzrost NG na linii 2\n- brak danych o spawaniu\n\n'
                         '## Priorytety\nSkupić się na kontroli linii 2.'),
            )
            db.session.add(b)
            db.session.commit()
            return b.id

    def test_pdf_requires_login(self, client, app, briefing_fixtures):
        bid = self._make_briefing(app)
        resp = client.get(f'/admin/briefing/{bid}/pdf')
        assert resp.status_code in (302, 401)

    def test_pdf_requires_admin(self, client, app, briefing_fixtures):
        bid = self._make_briefing(app)
        login(client, 'oper', 'Oper1234!')
        resp = client.get(f'/admin/briefing/{bid}/pdf')
        assert resp.status_code == 403
        logout(client)

    def test_pdf_unknown_briefing_404(self, client, briefing_fixtures):
        login(client, 'admin', 'Admin1234!')
        resp = client.get('/admin/briefing/999999/pdf')
        assert resp.status_code == 404
        logout(client)

    def test_pdf_generates_valid_pdf(self, client, app, briefing_fixtures):
        bid = self._make_briefing(app)
        login(client, 'admin', 'Admin1234!')
        resp = client.get(f'/admin/briefing/{bid}/pdf')
        assert resp.status_code == 200
        assert resp.headers['Content-Type'] == 'application/pdf'
        assert 'attachment' in resp.headers['Content-Disposition']
        assert resp.data.startswith(b'%PDF')
        logout(client)

    def test_pdf_persists_to_disk_and_reuses_saved_file(self, client, app, briefing_fixtures):
        bid = self._make_briefing(app)
        login(client, 'admin', 'Admin1234!')

        resp1 = client.get(f'/admin/briefing/{bid}/pdf')
        assert resp1.status_code == 200
        with app.app_context():
            saved_name = db.session.get(DailyBriefing, bid).pdf_filename
        assert saved_name
        filepath = os.path.join(flask_app.config['UPLOAD_FOLDER'], saved_name)
        assert os.path.exists(filepath)

        # Drugie zadanie ma zwrocic ten sam, juz zapisany plik (staly link).
        resp2 = client.get(f'/admin/briefing/{bid}/pdf')
        assert resp2.status_code == 200
        with app.app_context():
            assert db.session.get(DailyBriefing, bid).pdf_filename == saved_name
        logout(client)


class TestStatsTemplateSmoke:
    def test_admin_stats_renders_with_briefings(self, client, briefing_fixtures):
        login(client, 'admin', 'Admin1234!')
        resp = client.get('/admin/stats')
        assert resp.status_code == 200
        assert 'Briefing Kontroli Jakości'.encode('utf-8') in resp.data
        logout(client)
