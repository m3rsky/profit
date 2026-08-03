"""Testy statystyki 'Błędy NG wg montera' na stronie /admin/stats."""
from datetime import datetime

import pytest

from app import app as flask_app, db
from models import ChecklistTemplate, Category, Task, Report, ReportItem, User
from test_app import login, logout


@pytest.fixture(scope='module')
def installer_stats_fixtures(app):
    """Dane odizolowane datą (2018-06) od reszty zestawu testów."""
    with app.app_context():
        oper = User.query.filter_by(username='oper').first()

        tmpl = ChecklistTemplate(name='Szablon montera', is_active=True, template_type='monter')
        db.session.add(tmpl)
        db.session.flush()
        cat = Category(template_id=tmpl.id, name='Montaż', order=0)
        db.session.add(cat)
        db.session.flush()
        installer_task = Task(category_id=cat.id, title='Monter', order=0,
                              is_active=True, task_type='installer')
        db.session.add(installer_task)
        db.session.flush()

        # Jan Nowak: 1x OK, 2x NG w zakresie; Adam Wesoły: 1x OK poza zakresem (nie powinien się liczyć)
        r1 = Report(user_id=oper.id, template_id=tmpl.id, title='R1', status='completed',
                    report_type='monter', completed_at=datetime(2018, 6, 5))
        r2 = Report(user_id=oper.id, template_id=tmpl.id, title='R2', status='completed',
                    report_type='monter', completed_at=datetime(2018, 6, 10))
        r3 = Report(user_id=oper.id, template_id=tmpl.id, title='R3', status='completed',
                    report_type='monter', completed_at=datetime(2018, 6, 15))
        r_out = Report(user_id=oper.id, template_id=tmpl.id, title='R poza zakresem',
                       status='completed', report_type='monter', completed_at=datetime(2017, 1, 1))
        db.session.add_all([r1, r2, r3, r_out])
        db.session.flush()

        db.session.add_all([
            ReportItem(report_id=r1.id, task_id=installer_task.id,
                      value_text='Jan Nowak', result='ok', is_checked=True),
            ReportItem(report_id=r2.id, task_id=installer_task.id,
                      value_text='Jan Nowak', result='ng', is_checked=True),
            ReportItem(report_id=r3.id, task_id=installer_task.id,
                      value_text='Jan Nowak', result='ng', is_checked=True),
            ReportItem(report_id=r_out.id, task_id=installer_task.id,
                      value_text='Adam Wesoły', result='ok', is_checked=True),
        ])
        db.session.commit()

    return {'installer_name': 'Jan Nowak', 'outside_range_name': 'Adam Wesoły'}


class TestInstallerNgStats:
    def test_stats_page_shows_installer_ng_counts(self, client, installer_stats_fixtures):
        login(client, 'admin', 'Admin1234!')
        resp = client.get('/admin/stats?inst_date_from=2018-06-01&inst_date_to=2018-06-30')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'Błędy NG wg montera' in html
        assert installer_stats_fixtures['installer_name'] in html
        # 2 NG / 3 ocenione = 67%
        assert '67%' in html
        logout(client)

    def test_stats_page_excludes_reports_outside_range(self, client, installer_stats_fixtures):
        login(client, 'admin', 'Admin1234!')
        resp = client.get('/admin/stats?inst_date_from=2018-06-01&inst_date_to=2018-06-30')
        html = resp.data.decode('utf-8')
        assert installer_stats_fixtures['outside_range_name'] not in html
        logout(client)

    def test_stats_page_forbidden_for_non_admin(self, client, installer_stats_fixtures):
        login(client, 'oper', 'Oper1234!')
        resp = client.get('/admin/stats')
        assert resp.status_code == 403
        logout(client)
