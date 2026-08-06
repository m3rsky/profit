"""Testy statystyki 'Błędy NG wg montera' na stronie /admin/stats."""
import re
from datetime import datetime

import pytest

from app import app as flask_app, db
from models import ChecklistTemplate, Category, Task, Report, ReportItem, User, Installer, ReportItemInstaller
from test_app import login, logout, _csrf


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

        jan = Installer(name='Jan Nowak', is_active=True)
        adam = Installer(name='Adam Wesoły', is_active=True)
        db.session.add_all([jan, adam])
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

        i1 = ReportItem(report_id=r1.id, task_id=installer_task.id, result='ok', is_checked=True)
        i2 = ReportItem(report_id=r2.id, task_id=installer_task.id, result='ng', is_checked=True)
        i3 = ReportItem(report_id=r3.id, task_id=installer_task.id, result='ng', is_checked=True)
        i_out = ReportItem(report_id=r_out.id, task_id=installer_task.id, result='ok', is_checked=True)
        db.session.add_all([i1, i2, i3, i_out])
        db.session.flush()

        db.session.add_all([
            ReportItemInstaller(report_item_id=i1.id, installer_id=jan.id),
            ReportItemInstaller(report_item_id=i2.id, installer_id=jan.id),
            ReportItemInstaller(report_item_id=i3.id, installer_id=jan.id),
            ReportItemInstaller(report_item_id=i_out.id, installer_id=adam.id),
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

    def test_stats_page_survives_duplicated_query_string(self, client, installer_stats_fixtures):
        """Regresja: infrastruktura hostingu potrafi zdublować query string,
        doklejając go ponownie ze znakiem '?' na końcu ostatniego parametru
        (np. 'inst_date_to=2018-06-30?inst_date_from=2018-06-01&inst_date_to=2018-06-30').
        Filtr powinien mimo to poprawnie zadziałać dla pierwszego (czystego) wystąpienia."""
        login(client, 'admin', 'Admin1234!')
        resp = client.get(
            '/admin/stats?inst_date_from=2018-06-01&inst_date_to=2018-06-30'
            '?inst_date_from=2018-06-01&inst_date_to=2018-06-30'
        )
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert installer_stats_fixtures['installer_name'] in html
        assert '67%' in html
        logout(client)


@pytest.fixture
def installer_ui_fixtures(app):
    """Świeży szablon/zadanie 'installer' + jeden pusty (in_progress) i jeden
    wypełniony dwoma monterami (completed) punkt checklisty."""
    with app.app_context():
        oper = User.query.filter_by(username='oper').first()

        tmpl = ChecklistTemplate(name='Szablon UI montera', is_active=True, template_type='monter')
        db.session.add(tmpl)
        db.session.flush()
        cat = Category(template_id=tmpl.id, name='Montaż', order=0)
        db.session.add(cat)
        db.session.flush()
        task = Task(category_id=cat.id, title='Monter', order=0,
                   is_active=True, task_type='installer')
        db.session.add(task)
        db.session.flush()

        jan = Installer(name='UI Jan', is_active=True)
        adam = Installer(name='UI Adam', is_active=True)
        db.session.add_all([jan, adam])
        db.session.flush()

        r_open = Report(user_id=oper.id, template_id=tmpl.id, title='UI open',
                        status='in_progress', report_type='monter')
        r_done = Report(user_id=oper.id, template_id=tmpl.id, title='UI done',
                        status='completed', report_type='monter',
                        completed_at=datetime(2026, 1, 1))
        db.session.add_all([r_open, r_done])
        db.session.flush()

        item_open = ReportItem(report_id=r_open.id, task_id=task.id)
        item_done = ReportItem(report_id=r_done.id, task_id=task.id,
                               result='ok', is_checked=True)
        db.session.add_all([item_open, item_done])
        db.session.flush()

        db.session.add_all([
            ReportItemInstaller(report_item_id=item_done.id, installer_id=jan.id, role='Obudowa'),
            ReportItemInstaller(report_item_id=item_done.id, installer_id=adam.id, role='Drzwi'),
        ])
        db.session.commit()

        ids = {'report_open_id': r_open.id, 'report_done_id': r_done.id,
               'item_open_id': item_open.id, 'jan_id': jan.id, 'adam_id': adam.id}
    return ids


class TestInstallerChecklistUI:
    def test_editable_checklist_renders_installer_rows(self, client, installer_ui_fixtures):
        login(client, 'admin', 'Admin1234!')
        resp = client.get(f"/checklist/{installer_ui_fixtures['report_open_id']}")
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'installer-rows' in html
        assert 'installer-row-add' in html
        logout(client)

    def test_completed_checklist_shows_both_installers_with_roles(self, client, installer_ui_fixtures):
        login(client, 'admin', 'Admin1234!')
        resp = client.get(f"/checklist/{installer_ui_fixtures['report_done_id']}")
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'Obudowa: UI Jan' in html
        assert 'Drzwi: UI Adam' in html
        logout(client)

    def test_save_two_installers_via_api(self, client, installer_ui_fixtures):
        login(client, 'admin', 'Admin1234!')
        token = _csrf(client)
        item_id = installer_ui_fixtures['item_open_id']
        resp = client.post(
            f'/api/item/{item_id}/installers',
            json={'installers': [
                {'installer_id': installer_ui_fixtures['jan_id'], 'role': 'Obudowa'},
                {'installer_id': installer_ui_fixtures['adam_id'], 'role': 'Drzwi'},
            ]},
            headers={'X-CSRF-Token': token},
        )
        assert resp.status_code == 200
        with flask_app.app_context():
            rows = ReportItemInstaller.query.filter_by(report_item_id=item_id).all()
            assert {(r.installer_id, r.role) for r in rows} == {
                (installer_ui_fixtures['jan_id'], 'Obudowa'),
                (installer_ui_fixtures['adam_id'], 'Drzwi'),
            }
        logout(client)

    def test_clearing_installers_resets_result(self, client, installer_ui_fixtures):
        login(client, 'admin', 'Admin1234!')
        token = _csrf(client)
        item_id = installer_ui_fixtures['item_open_id']
        client.post(f'/api/item/{item_id}/installers',
                   json={'installers': [{'installer_id': installer_ui_fixtures['jan_id']}]},
                   headers={'X-CSRF-Token': token})
        client.post(f'/api/item/{item_id}/result', json={'result': 'ok'},
                   headers={'X-CSRF-Token': token})
        resp = client.post(f'/api/item/{item_id}/installers', json={'installers': []},
                           headers={'X-CSRF-Token': token})
        assert resp.status_code == 200
        assert resp.get_json()['result'] is None
        with flask_app.app_context():
            assert ReportItemInstaller.query.filter_by(report_item_id=item_id).count() == 0
        logout(client)


def _stats_row(html, name):
    """Wyciąga (total, ng, ng_pct) dla wiersza montera z tabeli 'Błędy NG wg montera'."""
    m = re.search(
        rf'<td>{re.escape(name)}</td>\s*'
        r'<td class="text-center">(\d+)</td>\s*'
        r'<td class="text-center"><span class="badge badge-error">(\d+)</span></td>\s*'
        r'<td class="text-center">(\d+)%</td>',
        html)
    if not m:
        return None
    return {'total': int(m.group(1)), 'ng': int(m.group(2)), 'ng_pct': int(m.group(3))}


@pytest.fixture(scope='module')
def installer_fault_fixtures(app):
    """Punkt NG z dwoma monterami — jeden oznaczony jako winny, drugi nie —
    plus jeden punkt OK z samym winnym monterem, dla sprawdzenia sumowania."""
    with app.app_context():
        oper = User.query.filter_by(username='oper').first()

        tmpl = ChecklistTemplate(name='Szablon winy montera', is_active=True, template_type='monter')
        db.session.add(tmpl)
        db.session.flush()
        cat = Category(template_id=tmpl.id, name='Montaż', order=0)
        db.session.add(cat)
        db.session.flush()
        task = Task(category_id=cat.id, title='Monter', order=0,
                   is_active=True, task_type='installer')
        db.session.add(task)
        db.session.flush()

        guilty = Installer(name='Winny Monter', is_active=True)
        innocent = Installer(name='Niewinny Monter', is_active=True)
        db.session.add_all([guilty, innocent])
        db.session.flush()

        r_ng = Report(user_id=oper.id, template_id=tmpl.id, title='R NG', status='completed',
                     report_type='monter', completed_at=datetime(2019, 3, 5))
        r_ok = Report(user_id=oper.id, template_id=tmpl.id, title='R OK', status='completed',
                     report_type='monter', completed_at=datetime(2019, 3, 10))
        db.session.add_all([r_ng, r_ok])
        db.session.flush()

        item_ng = ReportItem(report_id=r_ng.id, task_id=task.id, result='ng', is_checked=True)
        item_ok = ReportItem(report_id=r_ok.id, task_id=task.id, result='ok', is_checked=True)
        db.session.add_all([item_ng, item_ok])
        db.session.flush()

        db.session.add_all([
            ReportItemInstaller(report_item_id=item_ng.id, installer_id=guilty.id,
                                role='Drzwi', is_at_fault=True),
            ReportItemInstaller(report_item_id=item_ng.id, installer_id=innocent.id,
                                role='Obudowa', is_at_fault=False),
            ReportItemInstaller(report_item_id=item_ok.id, installer_id=guilty.id, is_at_fault=True),
        ])
        db.session.commit()

        ids = {'report_ng_id': item_ng.report_id, 'guilty': guilty.name, 'innocent': innocent.name}
    return ids


class TestInstallerFault:
    def test_readonly_checklist_marks_only_guilty_installer(self, client, installer_fault_fixtures):
        login(client, 'admin', 'Admin1234!')
        resp = client.get(f"/checklist/{installer_fault_fixtures['report_ng_id']}")
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'Drzwi: Winny Monter (zawinił)' in html
        assert 'Obudowa: Niewinny Monter (zawinił)' not in html
        assert 'Obudowa: Niewinny Monter' in html
        logout(client)

    def test_stats_credit_ng_only_to_guilty_installer(self, client, installer_fault_fixtures):
        login(client, 'admin', 'Admin1234!')
        resp = client.get('/admin/stats?inst_date_from=2019-03-01&inst_date_to=2019-03-31')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        guilty_row = _stats_row(html, installer_fault_fixtures['guilty'])
        innocent_row = _stats_row(html, installer_fault_fixtures['innocent'])
        assert guilty_row == {'total': 2, 'ng': 1, 'ng_pct': 50}
        assert innocent_row == {'total': 0, 'ng': 0, 'ng_pct': 0}
        logout(client)
