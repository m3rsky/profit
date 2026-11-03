"""Uprawnienia szczegółowe Kontroli Końcowej: akcje per rola (macierz) i wyjątki per użytkownik."""
import pytest

from app import db
from models import (User, Report, ChecklistTemplate, RolePermission, UserPermission,
                    DEFAULT_PERMISSIONS)
from test_app import login, _csrf

PW = 'Rola12345!'


@pytest.fixture
def kontroler(app):
    with app.app_context():
        u = User(username='rola_kj', email='rola_kj@test.pl', role='kontroler')
        u.set_password(PW)
        db.session.add(u)
        db.session.commit()
        uid = u.id
    yield uid
    with app.app_context():
        Report.query.filter(Report.title.like('UPR-%')).delete(synchronize_session=False)
        UserPermission.query.delete()
        RolePermission.query.delete()
        User.query.filter_by(username='rola_kj').delete()
        db.session.commit()


def _report(app, owner, status='completed'):
    with app.app_context():
        tmpl = ChecklistTemplate.query.filter_by(name='Test szablon').first()
        r = Report(title='UPR-raport', template_id=tmpl.id, user_id=owner, status=status,
                   report_type='kontroler')
        db.session.add(r)
        db.session.commit()
        return r.id


def _admin_id(app):
    with app.app_context():
        return User.query.filter_by(username='admin').first().id


def _set_role(client, grant):
    """Zapisuje macierz: domyślne uprawnienia ról plus `grant` (lista 'rola:klucz')."""
    login(client, 'super', 'Super1234!')
    granted = [f'{r}:{m}' for r, mods in DEFAULT_PERMISSIONS.items() for m in mods] + list(grant)
    client.post('/admin/users/uprawnienia', data={'perm': granted, '_csrf_token': _csrf(client)})
    client.get('/logout')


def _set_user(client, uid, **choices):
    login(client, 'super', 'Super1234!')
    data = {f'perm_{k.replace("__", ".")}': v for k, v in choices.items()}
    data['_csrf_token'] = _csrf(client)
    client.post(f'/admin/users/{uid}/uprawnienia', data=data)
    client.get('/logout')


class TestRoleActions:
    def test_kontroler_cannot_delete_or_reopen_by_default(self, app, client, kontroler):
        rid = _report(app, kontroler)
        login(client, 'rola_kj', PW)
        token = _csrf(client)
        assert client.post(f'/reports/{rid}/delete', data={'_csrf_token': token}).status_code == 403
        assert client.post(f'/reports/{rid}/reopen', data={'_csrf_token': token}).status_code == 403
        assert client.get(f'/reports/{rid}/edit').status_code == 403

    def test_role_grant_allows_delete(self, app, client, kontroler):
        rid = _report(app, kontroler)
        _set_role(client, ['kontroler:kontrola_koncowa.delete'])
        login(client, 'rola_kj', PW)
        resp = client.post(f'/reports/{rid}/delete', data={'_csrf_token': _csrf(client)})
        assert resp.status_code == 302
        with app.app_context():
            assert db.session.get(Report, rid) is None

    def test_administrator_keeps_actions_by_default(self, app, client, kontroler):
        rid = _report(app, kontroler)
        login(client, 'admin', 'Admin1234!')
        assert client.get(f'/reports/{rid}/edit').status_code == 200

    def test_matrix_page_lists_actions(self, client):
        login(client, 'super', 'Super1234!')
        page = client.get('/admin/users/uprawnienia').get_data(as_text=True)
        assert 'Usuwa raporty' in page and 'Wznawia zakończone raporty' in page


class TestUserOverrides:
    def test_user_grant_without_role_grant(self, app, client, kontroler):
        rid = _report(app, kontroler)
        _set_user(client, kontroler, kontrola_koncowa__reopen='allow')
        login(client, 'rola_kj', PW)
        token = _csrf(client)
        assert client.post(f'/reports/{rid}/reopen', data={'_csrf_token': token}).status_code == 302
        assert client.post(f'/reports/{rid}/delete', data={'_csrf_token': token}).status_code == 403
        with app.app_context():
            assert db.session.get(Report, rid).status == 'in_progress'

    def test_user_deny_overrides_role(self, app, client, kontroler):
        rid = _report(app, kontroler)
        _set_role(client, ['kontroler:kontrola_koncowa.delete'])
        _set_user(client, kontroler, kontrola_koncowa__delete='deny')
        login(client, 'rola_kj', PW)
        assert client.post(f'/reports/{rid}/delete',
                           data={'_csrf_token': _csrf(client)}).status_code == 403

    def test_inherit_removes_override(self, app, client, kontroler):
        _set_user(client, kontroler, kontrola_koncowa__delete='allow')
        with app.app_context():
            assert UserPermission.query.filter_by(user_id=kontroler).count() == 1
        _set_user(client, kontroler, kontrola_koncowa__delete='inherit')
        with app.app_context():
            assert UserPermission.query.filter_by(user_id=kontroler).count() == 0

    def test_user_can_lose_module(self, client, kontroler):
        _set_user(client, kontroler, qar='deny')
        login(client, 'rola_kj', PW)
        assert client.get('/qar/').status_code == 403

    def test_view_all_shows_foreign_reports(self, app, client, kontroler):
        rid = _report(app, _admin_id(app))
        login(client, 'rola_kj', PW)
        assert client.get(f'/reports/{rid}/pdf').status_code == 403
        client.get('/logout')
        _set_user(client, kontroler, kontrola_koncowa__view_all='allow')
        login(client, 'rola_kj', PW)
        assert 'UPR-raport' in client.get('/reports').get_data(as_text=True)

    def test_only_superuser_edits_user_permissions(self, client, kontroler):
        login(client, 'admin', 'Admin1234!')
        assert client.get(f'/admin/users/{kontroler}/uprawnienia').status_code == 403
        client.get('/logout')
        login(client, 'super', 'Super1234!')
        page = client.get(f'/admin/users/{kontroler}/uprawnienia').get_data(as_text=True)
        assert 'Zezwól' in page and 'Usuwa raporty' in page

    def test_buttons_follow_permission(self, app, client, kontroler):
        _report(app, kontroler)
        login(client, 'rola_kj', PW)
        assert 'data-del-report="' not in client.get('/reports').get_data(as_text=True)
        client.get('/logout')
        _set_user(client, kontroler, kontrola_koncowa__delete='allow')
        login(client, 'rola_kj', PW)
        assert 'data-del-report="' in client.get('/reports').get_data(as_text=True)


class TestOtherModules:
    """Akcje pozostałych modułów: domyślne role zachowują dawny dostęp, wyjątki na koncie go zmieniają."""

    def _qar(self, app, owner, status='closed'):
        from models import QARReport
        with app.app_context():
            r = QARReport(number='QAR-UPR-1', title='UPR-qar', description='x', user_id=owner, status=status)
            db.session.add(r)
            db.session.commit()
            return r.id

    def _cleanup(self, app):
        from models import QARReport, DocEntry
        with app.app_context():
            QARReport.query.filter(QARReport.number == 'QAR-UPR-1').delete()
            DocEntry.query.filter(DocEntry.title == 'UPR-doc').delete()
            db.session.commit()

    def test_qar_delete_and_reopen(self, app, client, kontroler):
        rid = self._qar(app, _admin_id(app))
        try:
            login(client, 'rola_kj', PW)
            token = _csrf(client)
            assert client.post(f'/qar/{rid}/delete', data={'_csrf_token': token}).status_code == 403
            assert client.post(f'/qar/{rid}/reopen', data={'_csrf_token': token}).status_code == 403
            client.get('/logout')
            _set_user(client, kontroler, qar__reopen='allow', qar__delete='allow')
            login(client, 'rola_kj', PW)
            token = _csrf(client)
            assert client.post(f'/qar/{rid}/reopen', data={'_csrf_token': token}).status_code == 302
            assert client.post(f'/qar/{rid}/delete', data={'_csrf_token': token}).status_code == 302
        finally:
            self._cleanup(app)

    def test_qar_stats_follow_permission(self, client, kontroler):
        login(client, 'rola_kj', PW)
        assert client.get('/qar/stats').status_code == 200
        client.get('/logout')
        _set_user(client, kontroler, qar__stats='deny')
        login(client, 'rola_kj', PW)
        assert client.get('/qar/stats').status_code == 403

    def test_kontrola_etapowa_points_and_delete(self, client, kontroler):
        login(client, 'rola_kj', PW)
        assert client.get('/kontrola/points').status_code == 403
        assert client.get('/kontrola/elementy').status_code == 403
        assert client.post('/kontrola/bulk-delete', data={'_csrf_token': _csrf(client)}).status_code == 403
        client.get('/logout')
        _set_user(client, kontroler, kontrola_etapowa__points='allow')
        login(client, 'rola_kj', PW)
        assert client.get('/kontrola/points').status_code == 200
        assert client.get('/kontrola/elementy').status_code == 200

    def test_kontrola_etapowa_scan_can_be_denied(self, client, kontroler):
        login(client, 'rola_kj', PW)
        assert client.get('/kontrola/scan').status_code == 200
        client.get('/logout')
        _set_user(client, kontroler, kontrola_etapowa__control='deny')
        login(client, 'rola_kj', PW)
        assert client.get('/kontrola/scan').status_code == 403

    def test_module_grant_opens_templates_for_kontroler(self, client, kontroler):
        login(client, 'rola_kj', PW)
        assert client.get('/admin/templates').status_code == 403
        client.get('/logout')
        _set_user(client, kontroler, szablony='allow', dziennik='allow', statystyki='allow')
        login(client, 'rola_kj', PW)
        assert client.get('/admin/templates').status_code == 200
        assert client.get('/admin/audit-log').status_code == 200
        assert client.get('/admin/stats').status_code == 200

    def test_briefing_needs_its_own_action(self, client, kontroler):
        _set_user(client, kontroler, statystyki='allow')
        login(client, 'rola_kj', PW)
        assert client.post('/admin/briefing/generate', data={'_csrf_token': _csrf(client)}).status_code == 403

    def test_dokumentacja_manage_all(self, app, client, kontroler):
        from models import DocEntry
        with app.app_context():
            e = DocEntry(title='UPR-doc', user_id=_admin_id(app))
            db.session.add(e)
            db.session.commit()
            eid = e.id
        try:
            login(client, 'rola_kj', PW)
            assert client.post(f'/dokumentacja/{eid}/usun',
                               data={'_csrf_token': _csrf(client)}).status_code in (403, 404)
            client.get('/logout')
            _set_user(client, kontroler, dokumentacja__manage_all='allow')
            login(client, 'rola_kj', PW)
            assert client.post(f'/dokumentacja/{eid}/usun',
                               data={'_csrf_token': _csrf(client)}).status_code == 302
        finally:
            self._cleanup(app)

    def test_lista_produkcyjna_import_needs_action(self, client, kontroler):
        _set_user(client, kontroler, lista_produkcyjna='allow')
        login(client, 'rola_kj', PW)
        assert client.get('/produkcja/import').status_code == 403
        client.get('/logout')
        _set_user(client, kontroler, lista_produkcyjna='allow', lista_produkcyjna__import='allow')
        login(client, 'rola_kj', PW)
        assert client.get('/produkcja/import').status_code == 200

    def test_kj_prestiz_actions(self, client, kontroler):
        _set_user(client, kontroler, kj_prestiz='allow')
        login(client, 'rola_kj', PW)
        assert client.get('/kj-prestiz/').status_code == 200
        assert client.get('/kj-prestiz/konfiguracja').status_code == 403
        client.get('/logout')
        _set_user(client, kontroler, kj_prestiz='allow', kj_prestiz__admin='allow')
        login(client, 'rola_kj', PW)
        assert client.get('/kj-prestiz/konfiguracja').status_code == 302  # przekierowanie na pierwszą stronę konfiguracji
