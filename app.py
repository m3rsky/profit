import os
import re
import csv
import json
import uuid
import secrets
import logging
import markdown
from io import StringIO
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
UTC = timezone.utc
LOCAL_TZ = ZoneInfo('Europe/Warsaw')
from functools import wraps
from logging.handlers import RotatingFileHandler
from threading import Lock
from flask import (Flask, render_template, redirect, url_for, request,
                   flash, jsonify, send_from_directory, abort, session, make_response)
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from markupsafe import Markup
from werkzeug.utils import secure_filename
from sqlalchemy import func, or_, and_, case
from sqlalchemy.exc import IntegrityError
from config import Config
from models import (db, get_or_404, User, ChecklistTemplate, Category, Task, Report, ReportItem,
                    Photo, AuditLog, Alert,
                    CabinetType, MaterialPrice, LaborRate, Quote, QuoteConfig,
                    CatalogProduct,
                    ChecklistSession, Installer, ReportItemInstaller,
                    QARReport, QARPhoto, QARCategory,
                    ProductionDepartment, DepartmentEmployee,
                    DailyBriefing, DocEntry, DocFile, DocNote,
                    RolePermission, MODULES, DEFAULT_PERMISSIONS, ROLES, ROLE_LABELS, ADMIN_ROLE_NAMES, LEGACY_ROLES,
                    roles_matching)
import anthropic
import briefing_service

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)

# ── Blueprints ─────────────────────────────────────────────────────────────────
from kosztorys import kosztorys_bp  # noqa: E402
app.register_blueprint(kosztorys_bp)

from qar import qar_bp  # noqa: E402
app.register_blueprint(qar_bp)
from qar.routes import _next_qar_number  # noqa: E402 — reużyte przez /api/v1/qar


from ustawienia import ustawienia_bp  # noqa: E402
app.register_blueprint(ustawienia_bp)

from dokumentacja import dokumentacja_bp  # noqa: E402
app.register_blueprint(dokumentacja_bp)

from produkcja import produkcja_bp  # noqa: E402
app.register_blueprint(produkcja_bp)

from kontrola import kontrola_bp  # noqa: E402
app.register_blueprint(kontrola_bp)

from prestiz_kj import prestiz_kj_bp  # noqa: E402
app.register_blueprint(prestiz_kj_bp)


# ── Logging ────────────────────────────────────────────────────────────────────
_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
os.makedirs(_log_dir, exist_ok=True)
_log_handler = RotatingFileHandler(
    os.path.join(_log_dir, 'psh_qc.log'), maxBytes=1_000_000, backupCount=5, encoding='utf-8'
)
_log_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
app.logger.addHandler(_log_handler)
app.logger.setLevel(logging.INFO)

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = ''
login_manager.login_message_category = 'warning'


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


SESSION_TIMEOUT = timedelta(hours=1)

# ── CSRF ───────────────────────────────────────────────────────────────────────
def _get_csrf_token() -> str:
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(32)
    return session['_csrf_token']

def _csrf_input():
    return Markup(f'<input type="hidden" name="_csrf_token" value="{_get_csrf_token()}">')

app.jinja_env.globals.update(csrf_token=_get_csrf_token, csrf_input=_csrf_input)

@app.template_filter('localdt')
def localdt_filter(dt, fmt='%d.%m.%Y %H:%M'):
    if dt is None:
        return ''
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(LOCAL_TZ).strftime(fmt)

# ── Brute-force protection ─────────────────────────────────────────────────────
_failed_attempts: dict = defaultdict(list)
_fa_lock = Lock()

def _is_rate_limited(ip: str) -> bool:
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=300)
    with _fa_lock:
        _failed_attempts[ip] = [t for t in _failed_attempts[ip] if t > cutoff]
        return len(_failed_attempts[ip]) >= 5

def _record_failure(ip: str) -> None:
    with _fa_lock:
        _failed_attempts[ip].append(datetime.now(UTC))

# ── Email validation ──────────────────────────────────────────────────────────
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

def _valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email))

# ── Audit log helper ──────────────────────────────────────────────────────────
def _audit(action: str, target_type: str = None, target_id: int = None, detail: str = None):
    try:
        uid = current_user.id if current_user.is_authenticated else None
        log = AuditLog(user_id=uid, action=action, target_type=target_type,
                       target_id=target_id, detail=detail, ip=request.remote_addr)
        db.session.add(log)
        db.session.commit()
        app.logger.info('AUDIT action=%s user=%s target=%s/%s detail=%s',
                        action, uid, target_type, target_id, detail)
    except Exception as exc:
        db.session.rollback()
        app.logger.error('Audit log error: %s', exc)

# ── Image verification ─────────────────────────────────────────────────────────
def _verify_image(file_stream) -> bool:
    try:
        from PIL import Image as PILImage
        img = PILImage.open(file_stream)
        img.verify()
        file_stream.seek(0)
        return True
    except Exception:
        file_stream.seek(0)
        return False

@app.before_request
def csrf_protect():
    if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
        return
    if request.endpoint in ('static', 'sw_js', 'manifest_json', 'manifest'):
        return
    if request.endpoint == 'api_desktop_login':
        return
    if request.path.startswith('/api/v1/'):
        # /api/v1/* jest chronione kluczem API (api_key_required), nie sesją
        # przeglądarki — CSRF sesyjny nie ma tu zastosowania.
        return
    token = session.get('_csrf_token')
    form_token = request.form.get('_csrf_token') or request.headers.get('X-CSRF-Token')
    if not token or not form_token or not secrets.compare_digest(token, form_token or ''):
        app.logger.warning('CSRF mismatch endpoint=%s ip=%s', request.endpoint, request.remote_addr)
        abort(403)

@app.before_request
def log_api_calls():
    if request.path.startswith('/api/'):
        app.logger.info('API %s %s user=%s is_admin=%s',
                        request.method, request.full_path,
                        current_user.get_id() if current_user.is_authenticated else 'anon',
                        getattr(current_user, 'is_admin', False))


@app.before_request
def check_session_timeout():
    if request.endpoint in ('login', 'static', 'sw_js', 'manifest_json'):
        return
    if current_user.is_authenticated:
        last = session.get('_last_activity')
        now = datetime.now(UTC)
        if last:
            last_dt = datetime.fromisoformat(last)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=UTC)
        if last and (now - last_dt) > SESSION_TIMEOUT:
            logout_user()
            session.clear()
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Sesja wygasła'}), 401
            flash('Sesja wygasła. Zaloguj się ponownie.', 'warning')
            return redirect(url_for('login'))
        session['_last_activity'] = now.isoformat()


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def superadmin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_superadmin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def kontroler_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_kontroler:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def api_key_required(f):
    """Zabezpiecza endpointy /api/v1/* kluczem API (nagłówek X-API-Key).

    Wymagane dla integracji zewnętrznych (np. Streamsoft) — bez tego dekoratora
    endpointy /api/v1/* byłyby publicznie dostępne bez żadnej autoryzacji."""
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get('X-API-Key', '')
        expected = app.config.get('API_KEY', '')
        if not expected or not key or not secrets.compare_digest(key, expected):
            app.logger.warning('API key mismatch endpoint=%s ip=%s', request.endpoint, request.remote_addr)
            return jsonify({'error': 'unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated


app.jinja_env.globals['today'] = None  # filled per-request below


# Pozycje menu, które użytkownik może przestawiać (klucz → domyślna pozycja z listy).
NAV_DEFAULT = ['dashboard', 'kontrola_etapowa', 'kontrola_koncowa', 'nowa_kontrola', 'zlecenia_qa', 'qar',
               'lista_produkcyjna', 'stanowisko', 'ustawienia', 'szablony', 'dokumentacja', 'kj_prestiz']


# Moduł -> endpoint startowy (do przekierowania z Dashboardu, gdy rola go nie ma)
MODULE_HOME = {
    'dashboard': 'dashboard', 'kontrola_etapowa': 'kontrola.list_records', 'kontrola_koncowa': 'reports_list',
    'qar': 'qar.list_reports', 'lista_produkcyjna': 'produkcja.index', 'stanowisko': 'produkcja.scan_page',
    'ustawienia': 'ustawienia.index', 'szablony': 'admin_templates', 'dokumentacja': 'dokumentacja.list_entries',
    'kj_prestiz': 'prestiz_kj.index',
}

# Endpointy z app.py przypisane do modułów w macierzy uprawnień (blueprinty mapuje _module_for_endpoint)
_KONTROLA_KONCOWA_ENDPOINTS = {
    'checklist_view', 'complete_checklist', 'new_checklist', 'checklist_from_qr', 'checklist_qr_lookup',
    'delete_photo', 'export_pdf', 'report_batch_delete', 'report_delete', 'report_detail', 'report_edit',
    'report_force_unlock', 'report_heartbeat', 'report_reopen', 'report_unlock', 'reports_bulk_delete',
    'reports_export_csv', 'reports_list', 'save_item_notes', 'set_item_installers', 'set_item_result',
    'set_item_value', 'upload_photo',
}
_STATYSTYKI_ENDPOINTS = {
    'admin_stats', 'admin_stats_debug', 'admin_briefing_detail', 'admin_briefing_generate',
    'admin_briefing_pdf', 'api_ng_task_reports', 'api_template_duration_reports',
}
_BLUEPRINT_MODULES = {'kontrola': 'kontrola_etapowa', 'qar': 'qar', 'dokumentacja': 'dokumentacja',
                      'ustawienia': 'ustawienia', 'prestiz_kj': 'kj_prestiz'}
_STANOWISKO_ENDPOINTS = {'scan_employee', 'scan_entry', 'scan_logout', 'scan_order', 'scan_page'}


def _module_for_endpoint(endpoint):
    """Moduł z macierzy uprawnień dla endpointu albo None (endpoint dostępny dla każdego zalogowanego)."""
    if '.' in endpoint:
        bp, name = endpoint.split('.', 1)
        if bp == 'produkcja':
            return 'stanowisko' if name in _STANOWISKO_ENDPOINTS else 'lista_produkcyjna'
        return _BLUEPRINT_MODULES.get(bp)
    if endpoint in _KONTROLA_KONCOWA_ENDPOINTS:
        return 'kontrola_koncowa'
    if endpoint in _STATYSTYKI_ENDPOINTS:
        return 'statystyki'
    if endpoint == 'admin_audit_log':
        return 'dziennik'
    if endpoint.startswith(('admin_template', 'admin_category', 'admin_task', 'admin_installers')) \
            or endpoint in ('api_categories_reorder', 'api_tasks_reorder'):
        return 'szablony'
    return None


def _first_allowed_url(user):
    """Adres pierwszego modułu dostępnego dla użytkownika (wg jego kolejności menu) albo None."""
    for key in _menu_keys_for(user):
        endpoint = MODULE_HOME.get(key)
        if endpoint and user.can(key):
            return url_for(endpoint)
    return None


def _menu_keys_for(user):
    """Klucze w kolejności użytkownika: zapisana lista (tylko znane klucze), potem reszta w kolejności domyślnej."""
    import json
    saved = []
    try:
        saved = [k for k in json.loads(user.menu_order or '[]') if k in NAV_DEFAULT]
    except (ValueError, TypeError):
        saved = []
    out = list(dict.fromkeys(saved))
    return out + [k for k in NAV_DEFAULT if k not in out]


@app.url_defaults
def _static_cache_bust(endpoint, values):
    """Dopisuje czas modyfikacji do adresów CSS/JS, żeby przeglądarka nie trzymała starej wersji."""
    if endpoint != 'static' or 'v' in values:
        return
    filename = values.get('filename', '')
    if not filename.endswith(('.css', '.js')):
        return
    try:
        values['v'] = int(os.path.getmtime(os.path.join(app.static_folder, filename)))
    except OSError:
        pass


@app.before_request
def _enforce_module_access():
    """Moduł niedostępny dla roli jest niewidoczny w menu, a wejście z adresu kończy się odmową."""
    endpoint = request.endpoint
    if not endpoint or not current_user.is_authenticated:
        return None
    module = _module_for_endpoint(endpoint)
    if module and not current_user.can(module):
        abort(403)
    return None


@app.context_processor
def _inject_menu():
    def menu_pos(key):
        """Pozycja pozycji menu (CSS order) wg kolejności zapisanej na koncie, None dla niezalogowanych."""
        if not current_user.is_authenticated:
            return None
        keys = _menu_keys_for(current_user)
        return keys.index(key) + 1 if key in keys else len(keys) + 1
    def can(module):
        return current_user.is_authenticated and current_user.can(module)
    return dict(menu_pos=menu_pos, can=can)


@app.route('/menu/kolejnosc', methods=['POST'])
@login_required
def menu_order_save():
    """Zapis własnej kolejności menu użytkownika (przeciąganie) albo powrót do domyślnej."""
    import json
    data = request.get_json(silent=True) or {}
    if data.get('reset'):
        current_user.menu_order = None
    else:
        keys = data.get('order')
        if (not isinstance(keys, list) or not all(isinstance(k, str) for k in keys)
                or any(k not in NAV_DEFAULT for k in keys)):
            return jsonify({'ok': False, 'error': 'Nieprawidłowa kolejność menu.'}), 400
        current_user.menu_order = json.dumps(list(dict.fromkeys(keys)))
    db.session.commit()
    return jsonify({'ok': True})


@app.context_processor
def _inject_globals():
    from datetime import date
    unread = 0
    pending_qa_reports = []
    pending_qa_count = 0
    if current_user.is_authenticated:
        unread = Alert.query.filter_by(recipient_id=current_user.id, is_read=False).count()
        if current_user.role_key == 'kontroler':
            cutoff_naive = (datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=30))
            admin_ids_sq = db.session.query(User.id).filter(User.role.in_(ADMIN_ROLE_NAMES))
            all_pending = (Report.query
                          .filter(
                              Report.status == 'in_progress',
                              Report.report_type == 'kontroler',
                              or_(
                                  Report.user_id == current_user.id,
                                  Report.user_id.in_(admin_ids_sq),
                                  and_(Report.locked_at.is_(None),
                                       Report.created_at < cutoff_naive),
                                  and_(Report.locked_at.isnot(None),
                                       Report.locked_at < cutoff_naive)
                              )
                          )
                          .order_by(Report.batch_index.asc(), Report.created_at.asc())
                          .limit(30).all())
            seen_batches = set()
            for r in all_pending:
                if r.batch_id:
                    if r.batch_id not in seen_batches:
                        seen_batches.add(r.batch_id)
                        pending_qa_reports.append(r)
                else:
                    pending_qa_reports.append(r)
            pending_qa_count = len(pending_qa_reports)
    return dict(unread_alerts=unread, today=date.today(),
                pending_qa_reports=pending_qa_reports,
                pending_qa_count=pending_qa_count)


def allowed_file(filename):
    return ('.' in filename and
            filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS'])


def allowed_pdf(filename):
    return ('.' in filename and
            filename.rsplit('.', 1)[1].lower() in app.config.get('PDF_EXTENSIONS', {'pdf'}))


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        ip = request.remote_addr or 'unknown'
        if _is_rate_limited(ip):
            flash('Zbyt wiele nieudanych prób. Spróbuj ponownie za 5 minut.', 'error')
            return render_template('login.html')
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user, remember=False)
            app.logger.info('LOGIN_OK user=%s ip=%s', username, ip)
            _audit('login', 'user', user.id)
            return redirect(request.args.get('next') or url_for('dashboard'))
        _record_failure(ip)
        app.logger.warning('LOGIN_FAIL user=%s ip=%s', username, ip)
        flash('Nieprawidłowa nazwa użytkownika lub hasło.', 'error')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# ── Theme preview (admin only) ────────────────────────────────────────────────

@app.route('/theme-preview')
@login_required
def theme_preview():
    if not current_user.is_admin:
        abort(403)
    return render_template('theme_preview.html')


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route('/')
@login_required
def dashboard():
    if not current_user.can('dashboard'):
        target = _first_allowed_url(current_user)
        if target:
            return redirect(target)
        abort(403)

    date_from = request.args.get('date_from', '')
    date_to   = request.args.get('date_to', '')
    q = Report.query
    if date_from:
        try:
            q = q.filter(Report.created_at >= datetime.strptime(date_from, '%Y-%m-%d'))
        except ValueError:
            date_from = ''
    if date_to:
        try:
            q = q.filter(Report.created_at < datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1))
        except ValueError:
            date_to = ''
    from sqlalchemy import func as _func
    _row = q.with_entities(
        _func.count(Report.id).label('total'),
        _func.sum(_func.cast(Report.status == 'completed', db.Integer)).label('completed'),
        _func.sum(_func.cast(Report.status == 'in_progress', db.Integer)).label('in_progress'),
    ).first()
    all_reports_count  = (_row.total or 0) if _row else 0
    completed_count    = (_row.completed or 0) if _row else 0
    in_progress_count  = (_row.in_progress or 0) if _row else 0
    rq = Report.query
    # 10 pozycji na liście, seria (batch_id) liczy się jako jedna pozycja
    recent_reports, _seen_units = [], set()
    for _r in rq.order_by(Report.created_at.desc()).limit(500):
        _unit = _r.batch_id or _r.id
        if _unit not in _seen_units:
            if len(_seen_units) >= 10:
                continue
            _seen_units.add(_unit)
        recent_reports.append(_r)
    templates = ChecklistTemplate.query.filter_by(is_active=True).order_by(ChecklistTemplate.name).all()
    if current_user.is_kontroler:
        all_pending_reports = (Report.query
                               .filter(Report.status == 'in_progress',
                                       Report.report_type == 'kontroler')
                               .order_by(Report.batch_index.asc(), Report.created_at.asc()).all())
        now_utc = datetime.now(UTC)
        takeover_delay = timedelta(minutes=30)
        # Jedna pozycja na serię — pierwszy nieukończony raport z batcha
        seen_batches = set()
        pending_order_reports = []
        for r in all_pending_reports:
            is_own = r.user_id == current_user.id
            if not is_own:
                # Cudzy raport: pomiń jeśli ktoś aktywnie pracuje (blokada <30 min)
                if r.lock_active:
                    continue
                # Pomiń jeśli nigdy nie otwierano i stworzono mniej niż 30 min temu
                if r.locked_at is None:
                    ca = r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=UTC)
                    if (now_utc - ca) < takeover_delay:
                        continue
            if r.batch_id:
                if r.batch_id not in seen_batches:
                    seen_batches.add(r.batch_id)
                    pending_order_reports.append(r)
            else:
                pending_order_reports.append(r)
    else:
        pending_order_reports = []
    quality_alerts = (Alert.query
                      .filter_by(recipient_id=current_user.id, alert_type='ng')
                      .order_by(Alert.created_at.desc())
                      .limit(5).all())
    # Liczba kontroli (raportów) z ostatnich 5 dni, wg lokalnej daty, do małego wykresu
    _today = datetime.now(LOCAL_TZ).date()
    _days = [_today - timedelta(days=i) for i in range(4, -1, -1)]
    _start_utc = datetime.combine(_days[0], datetime.min.time(), tzinfo=LOCAL_TZ).astimezone(UTC).replace(tzinfo=None)
    _per_day = dict.fromkeys(_days, 0)
    for (_created,) in rq.filter(Report.created_at >= _start_utc).with_entities(Report.created_at):
        _d = _created.replace(tzinfo=UTC).astimezone(LOCAL_TZ).date()
        if _d in _per_day:
            _per_day[_d] += 1
    _wd = ['pon', 'wt', 'śr', 'czw', 'pt', 'sob', 'ndz']
    daily_checks = [{'label': 'dziś' if d == _today else _wd[d.weekday()],
                     'date': d.strftime('%d.%m'), 'count': n} for d, n in _per_day.items()]
    # Top 10 punktów z wynikiem NG z ostatnich 2 tygodni (ukończone raporty), tylko admin.
    # Grupowanie po znormalizowanym tytule, tak jak w statystykach (kopie szablonów mają różne ID).
    top_ng = []
    if current_user.can('kontrola_koncowa'):
        _ng_cutoff = (datetime.now(UTC) - timedelta(days=14)).replace(tzinfo=None)
        _norm_title = func.lower(func.trim(func.rtrim(func.trim(Task.title), '.')))
        top_ng = [{'title': r.title, 'count': r.cnt} for r in (
            db.session.query(func.min(Task.title).label('title'),
                             func.count(ReportItem.id).label('cnt'))
            .join(ReportItem, ReportItem.task_id == Task.id)
            .join(Report, Report.id == ReportItem.report_id)
            .filter(ReportItem.result == 'ng',
                    Report.status == 'completed',
                    Report.completed_at >= _ng_cutoff)
            .group_by(_norm_title)
            .order_by(func.count(ReportItem.id).desc(), func.min(Task.title))
            .limit(10).all())]
    # 5 najaktywniejszych działów wg liczby raportów QAR z ostatnich 14 dni (dział z przypisanego pracownika)
    qar_by_dept = []
    if current_user.can('qar'):
        _qar_cutoff = (datetime.now(UTC) - timedelta(days=14)).replace(tzinfo=None)
        qar_by_dept = [{'name': r.name, 'count': r.cnt} for r in (
            db.session.query(ProductionDepartment.name.label('name'),
                             func.count(QARReport.id).label('cnt'))
            .join(DepartmentEmployee, DepartmentEmployee.department_id == ProductionDepartment.id)
            .join(QARReport, QARReport.employee_id == DepartmentEmployee.id)
            .filter(QARReport.created_at >= _qar_cutoff)
            .group_by(ProductionDepartment.id, ProductionDepartment.name)
            .order_by(func.count(QARReport.id).desc(), ProductionDepartment.name)
            .limit(5).all())]
    qar_date_from = (datetime.now(LOCAL_TZ).date() - timedelta(days=14)).isoformat()
    return render_template('dashboard.html',
                           qar_by_dept=qar_by_dept,
                           qar_date_from=qar_date_from,
                           top_ng=top_ng,
                           daily_checks=daily_checks,
                           recent_reports=recent_reports,
                           all_reports_count=all_reports_count,
                           completed_count=completed_count,
                           in_progress_count=in_progress_count,
                           date_from=date_from, date_to=date_to,
                           templates=templates,
                           pending_order_reports=pending_order_reports,
                           quality_alerts=quality_alerts)


# ── Checklist / Reports ───────────────────────────────────────────────────────

def _recent_duplicate_report(user_id, template_id, title, window_seconds=10):
    """Zwraca istniejący raport o tym samym tytule/szablonie utworzony przez
    tego samego użytkownika w ostatnich `window_seconds` sekund — zabezpieczenie
    przed duplikatami z podwójnego submitu formularza (double-click, wolna sieć,
    powrót przyciskiem „wstecz”)."""
    cutoff = datetime.now(UTC) - timedelta(seconds=window_seconds)
    candidate = (Report.query
                 .filter_by(user_id=user_id, template_id=template_id, title=title)
                 .order_by(Report.created_at.desc())
                 .first())
    if not candidate:
        return None
    created = candidate.created_at
    created = created if created.tzinfo else created.replace(tzinfo=UTC)
    return candidate if created >= cutoff else None


@app.route('/checklist/new', methods=['GET', 'POST'])
@login_required
@kontroler_required
def new_checklist():
    templates = ChecklistTemplate.query.filter_by(is_active=True, template_type='kontroler').order_by(ChecklistTemplate.name).all()
    if request.method == 'POST':
        tmpl_id     = request.form.get('template_id', type=int)
        tmpl        = get_or_404(ChecklistTemplate, tmpl_id)
        user_suffix = request.form.get('title', '').strip()
        quantity    = max(1, min(99, request.form.get('quantity', 1, type=int)))
        base_title  = f'{tmpl.name} – {user_suffix}' if user_suffix else \
                      f'{tmpl.name} – {datetime.now().strftime("%d.%m.%Y %H:%M")}'

        def _add_items(rpt):
            for cat in tmpl.categories.filter_by(is_active=True).order_by(Category.order):
                for task in cat.tasks.filter_by(is_active=True).order_by(Task.order):
                    db.session.add(ReportItem(report_id=rpt.id, task_id=task.id))

        if quantity > 1:
            # ── Tryb seryjny ──
            dup = _recent_duplicate_report(current_user.id, tmpl_id, f'{base_title} – 1/{quantity}')
            if dup:
                flash('Taka seria kontroli została już utworzona — otwieram istniejącą.', 'warning')
                return redirect(url_for('checklist_view', report_id=dup.id))
            bid = uuid.uuid4().hex
            first_id = None
            for i in range(1, quantity + 1):
                r = Report(user_id=current_user.id, template_id=tmpl_id,
                           title=f'{base_title} – {i}/{quantity}',
                           batch_id=bid, batch_index=i, batch_total=quantity)
                db.session.add(r)
                db.session.flush()
                _add_items(r)
                if i == 1:
                    first_id = r.id
            db.session.commit()
            flash(f'Utworzono serię {quantity} kontroli. Wypełnij po kolei.', 'info')
            return redirect(url_for('checklist_view', report_id=first_id))

        # ── Tryb pojedynczy ──
        dup = _recent_duplicate_report(current_user.id, tmpl_id, base_title)
        if dup:
            flash('Taka kontrola została już utworzona — otwieram istniejącą.', 'warning')
            return redirect(url_for('checklist_view', report_id=dup.id))

        report = Report(user_id=current_user.id, template_id=tmpl_id, title=base_title)
        db.session.add(report)
        db.session.flush()
        _add_items(report)
        db.session.commit()
        return redirect(url_for('checklist_view', report_id=report.id))

    return render_template('checklist_new.html', templates=templates)


def _qr_key(product_name, client, order_no):
    """Stabilny klucz kodu QR. Bez numeru ZO zwraca None, bo sam produkt i klient
    nie identyfikują jednoznacznie zlecenia (różne ZO mogą mieć ten sam produkt)."""
    if not order_no:
        return None
    norm = lambda s: ' '.join((s or '').upper().split())
    return f'{norm(product_name)}|{norm(client)}|{norm(order_no)}'


def _qr_title_prefix(product_name, client, order_no):
    parts = [product_name] + ([client] if client else []) + ([order_no] if order_no else [])
    return ' – '.join(parts)


def _find_qr_reports(product_name, client, order_no):
    """Listy kontrolne utworzone wcześniej z tego samego kodu QR (po `qr_key`,
    a dla starszych list bez klucza po schemacie tytułu „prefix – dd.mm.rrrr gg:mm”)."""
    key = _qr_key(product_name, client, order_no)
    if not key:
        return []
    prefix = _qr_title_prefix(product_name, client, order_no)
    esc = prefix.replace('!', '!!').replace('%', '!%').replace('_', '!_')
    legacy = and_(Report.qr_key.is_(None),
                  Report.title.like(esc + ' – __.__.____ __:__%', escape='!'))
    return (Report.query
            .filter(Report.report_type == 'kontroler', or_(Report.qr_key == key, legacy))
            .order_by(Report.created_at, Report.batch_index, Report.id).all())


def _qr_report_info(report):
    st = report.stats
    return {
        'id':       report.id,
        'title':    report.title,
        'status':   report.status,
        'done':     st['done'],
        'total':    st['total'],
        'url':      url_for('checklist_view', report_id=report.id),
        'action':   'Otwórz' if report.status == 'completed' else
                    ('Kontynuuj' if st['done'] else 'Rozpocznij'),
    }


@app.route('/checklist/qr-lookup', methods=['POST'])
@login_required
@kontroler_required
def checklist_qr_lookup():
    """Sprawdza po zeskanowaniu kodu, czy listy z tego QR już istnieją."""
    data = request.get_json(silent=True) or {}
    found = _find_qr_reports((data.get('p') or '').strip(),
                             (data.get('c') or '').strip(),
                             (data.get('o') or '').strip())
    return jsonify({'ok': True, 'reports': [_qr_report_info(r) for r in found]})


@app.route('/checklist/from-qr', methods=['POST'])
@login_required
@kontroler_required
def checklist_from_qr():
    data         = request.get_json(silent=True) or {}
    product_name = (data.get('p') or '').strip()
    client       = (data.get('c') or '').strip()
    order_no     = (data.get('o') or '').strip()
    try:
        quantity = max(1, min(99, int(data.get('q', 1))))
    except (TypeError, ValueError):
        quantity = 1

    if not product_name:
        return jsonify({'error': 'Brak nazwy produktu w kodzie QR'}), 400

    # Najpierw sprawdź, czy listy z tego kodu QR już istnieją (chyba że force)
    if not data.get('force'):
        existing = _find_qr_reports(product_name, client, order_no)
        if existing:
            target = next((r for r in existing if r.status != 'completed'), existing[0])
            return jsonify({
                'ok':       True,
                'existing': True,
                'redirect': url_for('checklist_view', report_id=target.id),
                'quantity': len(existing),
                'msg':      'Lista z tego kodu QR już istnieje, otwieram.',
            })

    tmpl = _find_matching_template(product_name, 'kontroler')
    if not tmpl:
        return jsonify({'error': f'Nie znaleziono szablonu dla „{product_name}"'}), 404

    # Buduj tytuł: produkt – klient – nr ZO – data
    parts = [product_name]
    if client:
        parts.append(client)
    if order_no:
        parts.append(order_no)
    now_str    = datetime.now().strftime('%d.%m.%Y %H:%M')
    base_title = ' – '.join(parts) + f' – {now_str}'

    first_title = f'{base_title} – 1/{quantity}' if quantity > 1 else base_title
    dup = _recent_duplicate_report(current_user.id, tmpl.id, first_title)
    if dup:
        return jsonify({
            'ok':       True,
            'redirect': url_for('checklist_view', report_id=dup.id),
            'template': tmpl.name,
            'quantity': quantity,
            'msg':      'Taka lista kontrolna została już utworzona — otwieram istniejącą.',
        })

    bid      = uuid.uuid4().hex if quantity > 1 else None
    first_id = None

    for i in range(1, quantity + 1):
        title = f'{base_title} – {i}/{quantity}' if quantity > 1 else base_title
        r = Report(
            user_id=current_user.id,
            template_id=tmpl.id,
            title=title,
            qr_key=_qr_key(product_name, client, order_no),
            batch_id=bid,
            batch_index=i if bid else None,
            batch_total=quantity if bid else None,
        )
        db.session.add(r)
        db.session.flush()
        for cat in tmpl.categories.filter_by(is_active=True).order_by(Category.order):
            for task in cat.tasks.filter_by(is_active=True).order_by(Task.order):
                db.session.add(ReportItem(report_id=r.id, task_id=task.id))
        if i == 1:
            first_id = r.id

    db.session.commit()
    _audit('qr_create_checklist', 'report', first_id,
           f'product={product_name} client={client} order={order_no} qty={quantity} tpl={tmpl.name}')

    msg = (f'Utworzono {quantity} list kontrolnych (seria).' if quantity > 1
           else 'Utworzono listę kontrolną.')
    return jsonify({
        'ok':       True,
        'redirect': url_for('checklist_view', report_id=first_id),
        'template': tmpl.name,
        'quantity': quantity,
        'msg':      msg,
    })


@app.route('/checklist/<int:report_id>')
@login_required
def checklist_view(report_id):
    report = get_or_404(Report, report_id)
    is_kontroler_report = report.report_type == 'kontroler' and current_user.is_kontroler
    if (report.user_id != current_user.id and not current_user.is_admin
            and not is_kontroler_report):
        abort(403)
    if report.status == 'in_progress':
        if report.lock_active and report.locked_by_id != current_user.id:
            exp = report.lock_expires_at.astimezone(LOCAL_TZ).strftime('%H:%M')
            flash(f'Lista jest aktualnie wypełniana przez „{report.locked_by.username}". '
                  f'Możliwość przejęcia po {exp} (30 min braku aktywności).', 'warning')
            return redirect(url_for('report_detail', report_id=report_id))
        now = datetime.now(UTC)
        report.locked_by_id = current_user.id
        report.locked_at    = now
        if report.started_at is None:
            report.started_at = now
        db.session.add(ChecklistSession(report_id=report.id, started_at=now))
        db.session.commit()
    items_by_category = {}
    for item in report.items.all():
        if item.task is None or item.task.category is None:
            continue
        cat = item.task.category
        items_by_category.setdefault(cat, []).append(item)
    installers = Installer.query.filter_by(is_active=True).order_by(Installer.name).all()
    qar_categories = [c.name for c in QARCategory.query.filter_by(is_active=True)
                      .order_by(QARCategory.order, QARCategory.name).all()]
    qar_departments = (ProductionDepartment.query.filter_by(is_active=True)
                       .order_by(ProductionDepartment.order, ProductionDepartment.name).all())
    # ZO do wstępnego wypełnienia raportu QAR: trzeci człon klucza QR listy (produkt|klient|ZO).
    qar_zo = ((report.qr_key or '').split('|') + ['', '', ''])[2]
    return render_template('checklist.html', report=report,
                           items_by_category=items_by_category, installers=installers,
                           qar_categories=qar_categories, qar_departments=qar_departments,
                           qar_zo=qar_zo)


@app.route('/checklist/<int:report_id>/complete', methods=['POST'])
@login_required
def complete_checklist(report_id):
    report = get_or_404(Report, report_id)
    is_kontroler_report = report.report_type == 'kontroler' and current_user.is_kontroler
    if (report.user_id != current_user.id and not current_user.is_admin
            and not is_kontroler_report):
        abort(403)
    if is_kontroler_report and report.user_id != current_user.id:
        report.user_id = current_user.id
    now_utc = datetime.now(UTC)
    for open_session in ChecklistSession.query.filter_by(
            report_id=report_id, ended_at=None).all():
        open_session.ended_at = now_utc
    total_seconds = 0
    for s in ChecklistSession.query.filter_by(report_id=report_id).all():
        if s.ended_at is None:
            continue
        s_start = s.started_at if s.started_at.tzinfo else s.started_at.replace(tzinfo=UTC)
        s_end   = s.ended_at   if s.ended_at.tzinfo   else s.ended_at.replace(tzinfo=UTC)
        total_seconds += int((s_end - s_start).total_seconds())
    report.status           = 'completed'
    report.completed_at     = now_utc
    report.locked_by_id     = None
    report.locked_at        = None
    report.duration_seconds = total_seconds if total_seconds > 0 else None
    db.session.flush()
    _create_ng_alerts_on_complete(report)
    db.session.commit()
    _audit('report_complete', 'report', report.id, report.title)
    sc = report.score
    score_msg = f' Ocena: {sc["grade"]}/6 ({sc["pct"]}% — {sc["ok"]}/{sc["scored"]} pkt).' if sc else ''

    # ── Seria: auto-przejście do następnej kontroli ──
    if report.batch_id and report.batch_index and report.batch_index < report.batch_total:
        next_r = Report.query.filter_by(
            batch_id=report.batch_id,
            batch_index=report.batch_index + 1
        ).first()
        if next_r and next_r.status == 'in_progress':
            flash(f'Kontrola {report.batch_index}/{report.batch_total} zakończona.{score_msg} '
                  f'Otwieranie następnej…', 'success')
            return redirect(url_for('checklist_view', report_id=next_r.id))

    if sc:
        flash(f'Raport zamknięty.{score_msg}', 'success')
    else:
        flash('Raport został zamknięty.', 'success')
    return redirect(url_for('report_detail', report_id=report.id))


@app.route('/reports')
@login_required
@kontroler_required
def reports_list():
    page        = request.args.get('page', 1, type=int)
    per_page    = request.args.get('per_page', 20, type=int)
    if per_page not in (10, 20, 25, 50, 100):
        per_page = 20
    tmpl_filter = request.args.get('template_id', type=int)
    status_f    = request.args.get('status', '')
    search_q    = request.args.get('q', '').strip()
    date_from   = request.args.get('date_from', '')
    date_to     = request.args.get('date_to', '')
    user_filter = request.args.get('user_id', type=int)

    if current_user.is_admin:
        q = Report.query
    else:
        # Kontroler widzi:
        # 1) swoje raporty (wszystkie statusy)
        # 2) cudze in-progress raporty kontrolne, po 30 min bezczynności
        admin_ids_sq = db.session.query(User.id).filter(User.role.in_(ADMIN_ROLE_NAMES))
        cutoff_naive = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=30)
        q = Report.query.filter(
            or_(
                Report.user_id == current_user.id,
                and_(
                    Report.report_type == 'kontroler',
                    Report.status == 'in_progress',
                    Report.user_id != current_user.id,
                    or_(
                        Report.user_id.in_(admin_ids_sq),  # admin: zawsze widoczne
                        and_(  # inni kontrolerzy: po 30 min braku aktywności
                            Report.locked_at.is_(None),
                            Report.created_at < cutoff_naive
                        ),
                        and_(
                            Report.locked_at.isnot(None),
                            Report.locked_at < cutoff_naive
                        )
                    )
                )
            )
        )
    if tmpl_filter:
        q = q.filter_by(template_id=tmpl_filter)
    if status_f in ('in_progress', 'completed'):
        q = q.filter_by(status=status_f)
    if search_q:
        q = q.filter(Report.title.ilike(f'%{search_q}%'))
    if date_from:
        try:
            q = q.filter(Report.created_at >= datetime.strptime(date_from, '%Y-%m-%d'))
        except ValueError:
            pass
    if date_to:
        try:
            q = q.filter(Report.created_at < datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1))
        except ValueError:
            pass
    if current_user.is_admin and user_filter:
        q = q.filter_by(user_id=user_filter)

    reports   = q.order_by(Report.created_at.desc()).paginate(page=page, per_page=per_page)
    templates = ChecklistTemplate.query.order_by(ChecklistTemplate.name).all()
    users     = User.query.order_by(User.username).all() if current_user.is_admin else []
    filters   = dict(template_id=tmpl_filter, status=status_f, q=search_q,
                     date_from=date_from, date_to=date_to, user_id=user_filter,
                     per_page=per_page)
    return render_template('reports_list.html', reports=reports,
                           templates=templates, users=users,
                           tmpl_filter=tmpl_filter, filters=filters,
                           per_page=per_page)


@app.route('/reports/export.csv')
@login_required
def reports_export_csv():
    q = Report.query if current_user.is_admin else Report.query.filter_by(user_id=current_user.id)
    tmpl_filter = request.args.get('template_id', type=int)
    status_f    = request.args.get('status', '')
    search_q    = request.args.get('q', '').strip()
    date_from   = request.args.get('date_from', '')
    date_to     = request.args.get('date_to', '')
    if tmpl_filter:
        q = q.filter_by(template_id=tmpl_filter)
    if status_f in ('in_progress', 'completed'):
        q = q.filter_by(status=status_f)
    if search_q:
        q = q.filter(Report.title.ilike(f'%{search_q}%'))
    if date_from:
        try:
            q = q.filter(Report.created_at >= datetime.strptime(date_from, '%Y-%m-%d'))
        except ValueError:
            pass
    if date_to:
        try:
            q = q.filter(Report.created_at < datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1))
        except ValueError:
            pass
    all_reports = q.order_by(Report.created_at.desc()).all()
    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(['ID', 'Tytuł', 'Operator', 'Szablon', 'Status', 'Data utworzenia',
                     'Data zamknięcia', 'Postęp %', 'OK', 'NG', 'N/A'])
    for r in all_reports:
        s = r.stats
        writer.writerow([
            r.id, r.title, r.author.username,
            r.template.name if r.template else '',
            'Zamknięty' if r.status == 'completed' else 'W trakcie',
            r.created_at.strftime('%d.%m.%Y %H:%M'),
            r.completed_at.strftime('%d.%m.%Y %H:%M') if r.completed_at else '',
            r.completion_percent, s['ok'], s['ng'], s['na'],
        ])
    output = make_response(si.getvalue())
    output.headers['Content-Type'] = 'text/csv; charset=utf-8'
    output.headers['Content-Disposition'] = 'attachment; filename="raporty.csv"'
    return output


@app.route('/reports/<int:report_id>')
@login_required
def report_detail(report_id):
    report = get_or_404(Report, report_id)
    is_kontroler_report = report.report_type == 'kontroler' and current_user.is_kontroler
    if (report.user_id != current_user.id and not current_user.is_admin
            and not is_kontroler_report):
        abort(403)
    items_by_category = {}
    for item in report.items.all():
        if item.task is None or item.task.category is None:
            continue
        cat = item.task.category
        items_by_category.setdefault(cat, []).append(item)
    return render_template('report_detail.html', report=report,
                           items_by_category=items_by_category)


# ── Edit / Reopen reports (admin) ────────────────────────────────────────────

@app.route('/reports/<int:report_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def report_edit(report_id):
    report = get_or_404(Report, report_id)
    users = User.query.order_by(User.username).all()
    if request.method == 'POST':
        report.title   = request.form.get('title', '').strip() or report.title
        report.notes   = request.form.get('notes', '').strip()
        new_user_id    = request.form.get('user_id', type=int)
        if new_user_id and db.session.get(User, new_user_id):
            report.user_id = new_user_id
        db.session.commit()
        flash('Raport zaktualizowany.', 'success')
        return redirect(url_for('report_detail', report_id=report.id))
    return render_template('admin/report_edit.html', report=report, users=users)


@app.route('/reports/<int:report_id>/reopen', methods=['POST'])
@login_required
@admin_required
def report_reopen(report_id):
    report = get_or_404(Report, report_id)
    ChecklistSession.query.filter_by(report_id=report_id).delete()
    now = datetime.now(UTC)
    report.status           = 'in_progress'
    report.completed_at     = None
    report.duration_seconds = None
    report.started_at       = None
    report.locked_by_id     = current_user.id
    report.locked_at        = now
    db.session.add(ChecklistSession(report_id=report.id, started_at=now))
    db.session.commit()
    _audit('report_reopen', 'report', report.id, report.title)
    flash('Raport wznowiony – można ponownie edytować.', 'success')
    return redirect(url_for('checklist_view', report_id=report.id))


@app.route('/api/report/<int:report_id>/heartbeat', methods=['POST'])
@login_required
def report_heartbeat(report_id):
    report = get_or_404(Report, report_id)
    if report.locked_by_id == current_user.id:
        report.locked_at = datetime.now(UTC)
        db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/report/<int:report_id>/unlock', methods=['POST'])
@login_required
def report_unlock(report_id):
    report = get_or_404(Report, report_id)
    if report.locked_by_id == current_user.id:
        now = datetime.now(UTC)
        for open_session in ChecklistSession.query.filter_by(
                report_id=report_id, ended_at=None).all():
            open_session.ended_at = now
        report.locked_by_id = None
        report.locked_at    = None
        db.session.commit()
    return jsonify({'ok': True})


@app.route('/reports/<int:report_id>/force-unlock', methods=['POST'])
@login_required
@admin_required
def report_force_unlock(report_id):
    report = get_or_404(Report, report_id)
    report.locked_by_id = None
    report.locked_at    = None
    db.session.commit()
    _audit('report_force_unlock', 'report', report.id, report.title)
    flash('Blokada raportu została zwolniona.', 'success')
    return redirect(url_for('checklist_view', report_id=report_id))


# ── Delete reports ────────────────────────────────────────────────────────────

@app.route('/reports/<int:report_id>/delete', methods=['POST'])
@login_required
@admin_required
def report_delete(report_id):
    report = get_or_404(Report, report_id)
    title = report.title
    _delete_reports([report])
    _audit('report_delete', 'report', report_id, title)
    flash(f'Raport „{title}" został usunięty.', 'success')
    return redirect(url_for('reports_list'))


@app.route('/reports/batch/<batch_id>/delete', methods=['POST'])
@login_required
@admin_required
def report_batch_delete(batch_id):
    reports = Report.query.filter_by(batch_id=batch_id).all()
    if not reports:
        flash('Nie znaleziono raportów w tej serii.', 'warning')
        return redirect(url_for('reports_list'))
    count = len(reports)
    _delete_reports(reports)
    _audit('report_batch_delete', detail=f'batch_id={batch_id} count={count}')
    flash(f'Usunięto serię {count} raportów.', 'success')
    return redirect(url_for('reports_list'))


@app.route('/reports/bulk-delete', methods=['POST'])
@login_required
@admin_required
def reports_bulk_delete():
    action = request.form.get('action')
    if action == 'selected':
        ids = request.form.getlist('report_ids', type=int)
        if not ids:
            flash('Nie zaznaczono żadnych raportów.', 'warning')
            return redirect(url_for('reports_list'))
        reports = Report.query.filter(Report.id.in_(ids)).all()
    elif action == 'older_week':
        cutoff = datetime.now(UTC) - timedelta(weeks=1)
        reports = Report.query.filter(Report.created_at < cutoff).all()
        if not reports:
            flash('Brak raportów starszych niż tydzień.', 'info')
            return redirect(url_for('reports_list'))
    elif action == 'all':
        reports = Report.query.all()
        if not reports:
            flash('Brak raportów do usunięcia.', 'info')
            return redirect(url_for('reports_list'))
    else:
        abort(400)
    count = len(reports)
    _delete_reports(reports)
    _audit('reports_bulk_delete', detail=f'count={count} action={action}')
    flash(f'Usunięto {count} {"raport" if count == 1 else "raporty" if 2 <= count <= 4 else "raportów"}.', 'success')
    return redirect(url_for('reports_list'))


def _delete_reports(reports):
    for report in reports:
        for item in report.items.all():
            for photo in item.photos.all():
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], photo.filename)
                if os.path.exists(filepath):
                    os.remove(filepath)
        db.session.delete(report)
    db.session.commit()


# ── API: Result / Notes / Photos ──────────────────────────────────────────────

def _can_edit_report(report):
    """Czy bieżący użytkownik może edytować raport."""
    if current_user.is_admin:
        return True
    if report.user_id == current_user.id:
        return True
    if report.report_type == 'kontroler' and current_user.is_kontroler:
        return True
    return False


def _create_ng_alerts_on_complete(report):
    """Tworzy jeden zbiorczy alert NG dla adminów i kontrolerów przy zamknięciu
    listy kontrolnej — na podstawie faktycznego stanu pozycji, więc omyłkowe NG
    poprawione przed zamknięciem nie generuje alertu."""
    ng_items = [i for i in report.items.all() if i.result == 'ng']
    if not ng_items:
        return
    titles = []
    for i in ng_items:
        t = i.task.title if i.task else 'Zadanie'
        if i.qar_report:
            t += f' ({i.qar_report.number})'
        titles.append(t)
    msg = f'Niezgodności ({len(ng_items)}): {report.title} — {", ".join(titles)}'
    if len(msg) > 512:
        msg = msg[:511] + '…'
    for u in User.query.filter(User.role.in_(roles_matching('administrator', 'kontroler'))).all():
        db.session.add(Alert(recipient_id=u.id, message=msg,
                             alert_type='ng'))


@app.route('/api/item/<int:item_id>/result', methods=['POST'])
@login_required
def set_item_result(item_id):
    item = get_or_404(ReportItem, item_id)
    if not _can_edit_report(item.report):
        return jsonify({'error': 'Forbidden'}), 403
    if item.report.status == 'completed':
        return jsonify({'error': 'Raport jest zamknięty'}), 400
    if item.report.lock_active and item.report.locked_by_id != current_user.id:
        return jsonify({'error': 'Raport jest edytowany przez innego użytkownika'}), 423
    result = request.json.get('result')
    if result not in ('ok', 'ng', 'na', 'dw', None):
        return jsonify({'error': 'Nieprawidłowy wynik'}), 400
    item.result     = result
    item.is_checked = result is not None
    item.checked_at = datetime.now(UTC) if result else None
    db.session.commit()
    s = item.report.stats
    return jsonify({'result': item.result, 'checked': item.is_checked,
                    'progress': item.report.completion_percent,
                    'stats': s})


@app.route('/api/item/<int:item_id>/value', methods=['POST'])
@login_required
def set_item_value(item_id):
    """Save a numeric measurement or text entry; auto-evaluates result."""
    item = get_or_404(ReportItem, item_id)
    if not _can_edit_report(item.report):
        return jsonify({'error': 'Forbidden'}), 403
    if item.report.status == 'completed':
        return jsonify({'error': 'Raport jest zamknięty'}), 400
    if item.report.lock_active and item.report.locked_by_id != current_user.id:
        return jsonify({'error': 'Raport jest edytowany przez innego użytkownika'}), 423
    value = request.json.get('value', '').strip()
    if not value:
        item.value_text = None
        item.result = None
        item.is_checked = False
        item.checked_at = None
        db.session.commit()
        return jsonify({'ok': True, 'result': None,
                        'progress': item.report.completion_percent,
                        'stats': item.report.stats})
    task = item.task
    item.value_text = value
    item.checked_at = datetime.now(UTC)
    if task.task_type == 'numeric':
        try:
            num = float(value.replace(',', '.'))
            in_range = True
            if task.value_min is not None and num < task.value_min:
                in_range = False
            if task.value_max is not None and num > task.value_max:
                in_range = False
            item.result = 'ok' if in_range else 'ng'
            item.is_checked = True
        except ValueError:
            return jsonify({'error': 'Nieprawidłowa wartość liczbowa'}), 400
    elif task.task_type == 'measurements':
        parts = [p.strip() for p in value.split('|')]
        parts = (parts + ['', '', ''])[:3]
        all_empty = all(p == '' for p in parts)
        item.value_text = None if all_empty else '|'.join(parts)
        if all_empty:
            item.checked_at = None
        item.is_checked = item.result is not None
    else:  # text — result set explicitly by OK/NG/NA buttons; just save value
        item.is_checked = item.result is not None
    db.session.commit()
    return jsonify({'ok': True, 'result': item.result, 'value': item.value_text,
                    'progress': item.report.completion_percent,
                    'stats': item.report.stats})


@app.route('/api/item/<int:item_id>/installers', methods=['POST'])
@login_required
def set_item_installers(item_id):
    """Save the installer(s) assigned to a checklist item (task_type == 'installer').
    Supports one or several monterzy per item, each with an optional role
    (e.g. 'Obudowa' / 'Drzwi') when the work on one product was split between them."""
    item = get_or_404(ReportItem, item_id)
    if not _can_edit_report(item.report):
        return jsonify({'error': 'Forbidden'}), 403
    if item.report.status == 'completed':
        return jsonify({'error': 'Raport jest zamknięty'}), 400
    if item.report.lock_active and item.report.locked_by_id != current_user.id:
        return jsonify({'error': 'Raport jest edytowany przez innego użytkownika'}), 423
    entries = request.json.get('installers', [])
    if not isinstance(entries, list):
        return jsonify({'error': 'Nieprawidłowe dane'}), 400

    ReportItemInstaller.query.filter_by(report_item_id=item.id).delete()
    seen_ids = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            installer_id = int(entry.get('installer_id'))
        except (TypeError, ValueError):
            continue
        if installer_id in seen_ids or not db.session.get(Installer, installer_id):
            continue
        seen_ids.add(installer_id)
        role = (entry.get('role') or '').strip() or None
        is_at_fault = bool(entry.get('is_at_fault', True))
        db.session.add(ReportItemInstaller(report_item_id=item.id,
                                           installer_id=installer_id, role=role,
                                           is_at_fault=is_at_fault))
    if seen_ids:
        item.checked_at = datetime.now(UTC)
    else:
        item.result = None
        item.is_checked = False
        item.checked_at = None
    db.session.commit()
    return jsonify({'ok': True, 'result': item.result,
                    'progress': item.report.completion_percent,
                    'stats': item.report.stats})


@app.route('/api/tasks/reorder', methods=['POST'])
@login_required
@admin_required
def api_tasks_reorder():
    items = request.json  # [{id: n}, ...] ordered by new position
    if not isinstance(items, list):
        return jsonify({'error': 'bad request'}), 400
    for idx, entry in enumerate(items):
        task = db.session.get(Task, entry.get('id'))
        if task:
            task.order = idx
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/categories/reorder', methods=['POST'])
@login_required
@admin_required
def api_categories_reorder():
    items = request.json  # [{id: n}, ...] ordered by new position
    if not isinstance(items, list):
        return jsonify({'error': 'bad request'}), 400
    for idx, entry in enumerate(items):
        cat = db.session.get(Category, entry.get('id'))
        if cat:
            cat.order = idx
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/item/<int:item_id>/notes', methods=['POST'])
@login_required
def save_item_notes(item_id):
    item = get_or_404(ReportItem, item_id)
    if not _can_edit_report(item.report):
        return jsonify({'error': 'Forbidden'}), 403
    item.notes = request.json.get('notes', '')
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/item/<int:item_id>/photo', methods=['POST'])
@login_required
def upload_photo(item_id):
    item = get_or_404(ReportItem, item_id)
    if not _can_edit_report(item.report):
        return jsonify({'error': 'Forbidden'}), 403
    if item.report.status == 'completed':
        return jsonify({'error': 'Raport jest zamknięty'}), 400
    file = request.files.get('photo')
    if not file or not allowed_file(file.filename):
        return jsonify({'error': 'Nieprawidłowy plik'}), 400
    if not _verify_image(file):
        return jsonify({'error': 'Plik nie jest prawidłowym obrazem'}), 400
    ext = file.filename.rsplit('.', 1)[1].lower()
    unique_name = f"{uuid.uuid4().hex}.{ext}"
    upload_path = app.config['UPLOAD_FOLDER']
    os.makedirs(upload_path, exist_ok=True)
    file.save(os.path.join(upload_path, unique_name))
    photo = Photo(report_item_id=item_id,
                  filename=unique_name,
                  original_name=secure_filename(file.filename))
    db.session.add(photo)
    db.session.commit()
    return jsonify({'ok': True, 'photo_id': photo.id,
                    'url': url_for('uploaded_file', filename=unique_name)})


@app.route('/api/photo/<int:photo_id>', methods=['DELETE'])
@login_required
def delete_photo(photo_id):
    photo = get_or_404(Photo, photo_id)
    item = photo.report_item
    if not _can_edit_report(item.report):
        return jsonify({'error': 'Forbidden'}), 403
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], photo.filename)
    if os.path.exists(filepath):
        os.remove(filepath)
    db.session.delete(photo)
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/uploads/<filename>')
@login_required
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/uploads/pdf/<filename>')
@login_required
def uploaded_pdf(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


# ── PDF Export ────────────────────────────────────────────────────────────────

@app.route('/reports/<int:report_id>/pdf')
@login_required
def export_pdf(report_id):
    from pdf_generator import generate_pdf
    report = get_or_404(Report, report_id)
    if report.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    items_by_category = {}
    for item in report.items.all():
        if item.task is None:
            continue  # zadanie zostalo usuniete po utworzeniu raportu
        cat = item.task.category
        items_by_category.setdefault(cat, []).append(item)
    return generate_pdf(report, items_by_category, app.config['UPLOAD_FOLDER'])


# ── Admin: Audit log ──────────────────────────────────────────────────────────

@app.route('/admin/audit-log')
@login_required
@admin_required
def admin_audit_log():
    page = request.args.get('page', 1, type=int)
    logs = (AuditLog.query.order_by(AuditLog.created_at.desc())
            .paginate(page=page, per_page=50))
    return render_template('admin/audit_log.html', logs=logs)


# ── Admin: Statistics ─────────────────────────────────────────────────────────

@app.route('/admin/stats')
@login_required
@admin_required
def admin_stats():
    from datetime import date as _date, timedelta as td
    now        = datetime.now(UTC).replace(tzinfo=None)  # naive UTC – spójne z SQLite string storage
    days       = 30
    cutoff     = now - timedelta(days=days)
    cutoff_str = cutoff.date().isoformat()  # "YYYY-MM-DD" – bezpieczne porównanie str

    # Reports per day (last 30 days) — tylko ukończone
    _day_expr = func.substr(Report.completed_at, 1, 10)
    daily_raw = (db.session.query(
        _day_expr.label('day'),
        func.count(Report.id).label('cnt')
    ).filter(Report.status == 'completed',
             _day_expr >= cutoff_str)
     .group_by(_day_expr).order_by(_day_expr).all())
    daily  = {r.day: r.cnt for r in daily_raw}
    labels = [(_date.today() - td(days=days - 1 - i)).isoformat() for i in range(days)]
    daily_counts = [daily.get(d, 0) for d in labels]

    # OK / NG / NA totals — ukończone raporty z ostatnich 30 dni
    _result_counts = {r: c for r, c in
        db.session.query(ReportItem.result, func.count(ReportItem.id))
        .join(Report, Report.id == ReportItem.report_id)
        .filter(Report.status == 'completed',
                func.substr(Report.completed_at, 1, 10) >= cutoff_str)
        .group_by(ReportItem.result).all()}
    ok_total   = _result_counts.get('ok', 0)
    ng_total   = _result_counts.get('ng', 0)
    na_total   = _result_counts.get('na', 0)
    none_total = _result_counts.get(None, 0)

    # Top 10 NG tasks (last 30 days) — grupuj po tytule, bo to samo zadanie
    # może mieć wiele ID (kopiowane szablony); min(id) jako reprezentant dla API
    _norm = lambda col: func.lower(func.trim(func.rtrim(func.trim(col), '.')))
    ng_tasks_raw = (db.session.query(
        func.min(Task.id).label('id'),
        func.min(Task.title).label('title'),
        func.count(ReportItem.id).label('cnt')
    ).join(ReportItem, ReportItem.task_id == Task.id)
     .join(Report, Report.id == ReportItem.report_id)
     .filter(ReportItem.result == 'ng',
             Report.status == 'completed',
             func.substr(Report.completed_at, 1, 10) >= cutoff_str)
     .group_by(_norm(Task.title)).order_by(func.count(ReportItem.id).desc())
     .limit(10).all())

    # User activity — tylko ukończone raporty
    user_stats = (db.session.query(
        User.username,
        func.count(Report.id).label('total'),
    ).join(Report, Report.user_id == User.id)
     .filter(Report.status == 'completed')
     .group_by(User.id).order_by(func.count(Report.id).desc()).all())

    # Duration stats per template (completed reports with duration recorded)
    dur_raw = (db.session.query(
        ChecklistTemplate.id.label('tpl_id'),
        ChecklistTemplate.name.label('tpl_name'),
        func.count(Report.id).label('cnt'),
        func.avg(Report.duration_seconds).label('avg_s'),
        func.min(Report.duration_seconds).label('min_s'),
        func.max(Report.duration_seconds).label('max_s'),
    ).join(Report, Report.template_id == ChecklistTemplate.id)
     .filter(Report.status == 'completed', Report.duration_seconds.isnot(None))
     .group_by(ChecklistTemplate.id)
     .order_by(func.avg(Report.duration_seconds).desc()).all())

    def _fmt_dur(secs):
        if secs is None:
            return '—'
        secs = int(secs)
        if secs < 60:
            return '< 1 min'
        h, rem = divmod(secs, 3600)
        m = rem // 60
        if h:
            return f'{h} h {m} min' if m else f'{h} h'
        return f'{m} min'

    duration_stats = [
        {
            'id':    r.tpl_id,
            'name':  r.tpl_name,
            'cnt':   r.cnt,
            'avg':   _fmt_dur(r.avg_s),
            'min':   _fmt_dur(r.min_s),
            'max':   _fmt_dur(r.max_s),
        }
        for r in dur_raw
    ]

    total_completed   = Report.query.filter_by(status='completed').count()
    total_in_progress = Report.query.filter_by(status='in_progress').count()
    total_users       = User.query.count()

    recent_briefings = (DailyBriefing.query
                        .order_by(DailyBriefing.generated_at.desc())
                        .limit(10).all())

    # ── Błędy NG wg montera (wybierany zakres dat, niezależny od reszty strony) ──
    # Infrastruktura hostingu potrafi zdublować query string, doklejając go
    # ponownie ze znakiem '?' na końcu ostatniego parametru (potwierdzone przez
    # curl -v — żądanie wychodzi czyste, serwer i tak zwraca zdublowany ciąg).
    # Poprawna data nigdy nie zawiera '?', więc obcinamy od niego obronnie.
    def _clean_date_param(raw):
        return raw.split('?', 1)[0] if raw else raw

    inst_date_from = _clean_date_param(request.args.get('inst_date_from', '')) or cutoff.date().isoformat()
    inst_date_to   = _clean_date_param(request.args.get('inst_date_to', ''))   or _date.today().isoformat()
    # Walidacja niezależna per pole — awaria jednego nie powinna kasować drugiego.
    try:
        datetime.strptime(inst_date_from, '%Y-%m-%d')
    except ValueError:
        inst_date_from = cutoff.date().isoformat()
    try:
        datetime.strptime(inst_date_to, '%Y-%m-%d')
    except ValueError:
        inst_date_to = _date.today().isoformat()

    installer_raw = (db.session.query(
        Installer.name.label('name'),
        func.sum(case((and_(ReportItem.result == 'ng', ReportItemInstaller.is_at_fault.is_(True)), 1),
                     else_=0)).label('ng'),
        func.sum(case((ReportItem.result == 'ok', 1), else_=0)).label('ok'),
    ).select_from(ReportItemInstaller)
     .join(Installer, Installer.id == ReportItemInstaller.installer_id)
     .join(ReportItem, ReportItem.id == ReportItemInstaller.report_item_id)
     .join(Task, Task.id == ReportItem.task_id)
     .join(Report, Report.id == ReportItem.report_id)
     .filter(Task.task_type == 'installer',
             ReportItem.result.in_(('ok', 'ng')),
             Report.status == 'completed',
             _day_expr >= inst_date_from, _day_expr <= inst_date_to)
     .group_by(Installer.name)
     .order_by(func.sum(case((ReportItem.result == 'ng', 1), else_=0)).desc())
     .all())
    installer_stats = [
        {'name': r.name, 'ng': r.ng, 'ok': r.ok, 'total': r.ng + r.ok,
         'ng_pct': round(r.ng / (r.ng + r.ok) * 100) if (r.ng + r.ok) else 0}
        for r in installer_raw
    ]

    resp = make_response(render_template('admin/stats.html',
                           labels=labels, daily_counts=daily_counts,
                           ok_total=ok_total, ng_total=ng_total,
                           na_total=na_total, none_total=none_total,
                           ng_tasks=ng_tasks_raw,
                           user_stats=user_stats,
                           duration_stats=duration_stats,
                           total_completed=total_completed,
                           total_in_progress=total_in_progress,
                           total_users=total_users,
                           recent_briefings=recent_briefings,
                           installer_stats=installer_stats,
                           inst_date_from=inst_date_from, inst_date_to=inst_date_to))
    resp.headers['Cache-Control'] = 'no-store'
    resp.headers['X-LiteSpeed-Cache-Control'] = 'no-cache'
    return resp


# ── Admin: Poranny Briefing (analiza Claude API) ─────────────────────────────

_BRIEFING_MAX_RANGE_DAYS = 366


@app.route('/admin/briefing/generate', methods=['POST'])
@login_required
@admin_required
def admin_briefing_generate():
    from datetime import date as _date

    start_raw = request.form.get('start_date', '')
    end_raw   = request.form.get('end_date', '')
    try:
        start_date = _date.fromisoformat(start_raw)
        end_date   = _date.fromisoformat(end_raw)
    except ValueError:
        return jsonify({'success': False, 'error': 'Podaj poprawny zakres dat (od–do).'}), 400

    if start_date > end_date:
        return jsonify({'success': False, 'error': 'Data początkowa musi być wcześniejsza niż końcowa.'}), 400
    if (end_date - start_date).days > _BRIEFING_MAX_RANGE_DAYS:
        return jsonify({'success': False, 'error': f'Zakres dat nie może przekraczać {_BRIEFING_MAX_RANGE_DAYS} dni.'}), 400

    if not app.config.get('ANTHROPIC_API_KEY'):
        return jsonify({'success': False, 'error': 'Usługa AI nie jest skonfigurowana — brak klucza API.'}), 400

    payload = briefing_service.generate_briefing_payload(start_date, end_date)

    try:
        result = briefing_service.call_claude(payload)
    except anthropic.RateLimitError:
        return jsonify({'success': False, 'error': 'Przekroczono limit zapytań do Claude API — spróbuj ponownie za chwilę.'}), 502
    except anthropic.AuthenticationError:
        app.logger.error('Briefing: błąd autoryzacji Claude API (nieprawidłowy klucz)')
        return jsonify({'success': False, 'error': 'Błąd konfiguracji usługi AI — skontaktuj się z administratorem systemu.'}), 502
    except anthropic.APIConnectionError:
        return jsonify({'success': False, 'error': 'Brak połączenia z Claude API.'}), 502
    except anthropic.APIStatusError as e:
        app.logger.error('Briefing: błąd Claude API status=%s', e.status_code)
        return jsonify({'success': False, 'error': 'Usługa AI zwróciła błąd — spróbuj ponownie później.'}), 502
    except briefing_service.BriefingGenerationError as e:
        return jsonify({'success': False, 'error': str(e)}), 502

    briefing = DailyBriefing(
        start_date=start_date, end_date=end_date,
        generated_by_user_id=current_user.id,
        model_used=result['model'],
        input_token_count=result['input_tokens'],
        output_token_count=result['output_tokens'],
        content=result['content'],
        raw_aggregated_data=json.dumps(payload, ensure_ascii=False),
    )
    db.session.add(briefing)
    db.session.commit()

    _audit('briefing_generated', 'DailyBriefing', briefing.id,
          f'{start_date.isoformat()}..{end_date.isoformat()}')

    return jsonify({'success': True, 'briefing': _briefing_to_dict(briefing)})


@app.route('/admin/briefing/<int:briefing_id>')
@login_required
@admin_required
def admin_briefing_detail(briefing_id):
    briefing = get_or_404(DailyBriefing, briefing_id)
    resp = jsonify({'success': True, 'briefing': _briefing_to_dict(briefing)})
    resp.headers['Cache-Control'] = 'no-store'
    resp.headers['X-LiteSpeed-Cache-Control'] = 'no-cache'
    return resp


def _serve_briefing_pdf(briefing):
    """Serwuje PDF briefingu z dysku (stały link) — generuje i zapisuje go
    przy pierwszym żądaniu, potem zawsze zwraca ten sam plik."""
    upload_path   = app.config['UPLOAD_FOLDER']
    download_name = f'Briefing_Kontroli_Jakosci_{briefing.start_date}_{briefing.end_date}.pdf'

    if not briefing.pdf_filename or not os.path.exists(os.path.join(upload_path, briefing.pdf_filename)):
        from pdf_generator import build_briefing_pdf_bytes
        pdf_bytes = build_briefing_pdf_bytes(briefing)
        os.makedirs(upload_path, exist_ok=True)
        filename = f'briefing_{briefing.id}_{uuid.uuid4().hex[:8]}.pdf'
        with open(os.path.join(upload_path, filename), 'wb') as f:
            f.write(pdf_bytes)
        briefing.pdf_filename = filename
        db.session.commit()

    return send_from_directory(upload_path, briefing.pdf_filename,
                               as_attachment=False, download_name=download_name)


@app.route('/admin/briefing/<int:briefing_id>/pdf')
@login_required
@admin_required
def admin_briefing_pdf(briefing_id):
    briefing = get_or_404(DailyBriefing, briefing_id)
    return _serve_briefing_pdf(briefing)


@app.route('/briefing/pdf/<token>')
def public_briefing_pdf(token):
    """Link do PDF bez logowania — dostępny wyłącznie pod długim, losowym
    tokenem (nie po numerze ID), żeby nie dało się go zgadnąć ani wyliczyć."""
    briefing = DailyBriefing.query.filter_by(public_token=token).first()
    if not briefing:
        abort(404)
    _audit('briefing_pdf_public_view', 'DailyBriefing', briefing.id)
    return _serve_briefing_pdf(briefing)


_BRIEFING_SECTION_STYLES = {
    'Podsumowanie':       ('bi-clipboard-data',      'blue'),
    'Anomalie i trendy':  ('bi-exclamation-triangle', 'warn'),
    'Priorytety':         ('bi-flag',                 'ok'),
}


def _render_briefing_html(content):
    """Renderuje markdown Claude jako kolorowe, podpisane sekcje zamiast gołego HTML."""
    parts = re.split(r'(?m)^##\s+(.+)$', content)
    html_parts = []
    if parts[0].strip():
        html_parts.append(markdown.markdown(parts[0].strip()))
    for i in range(1, len(parts), 2):
        title = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ''
        icon, tone = _BRIEFING_SECTION_STYLES.get(title, ('bi-info-circle', 'neutral'))
        body_html = markdown.markdown(body.strip())
        html_parts.append(
            f'<div class="briefing-section briefing-section--{tone}">'
            f'<div class="briefing-section-title"><i class="bi {icon}"></i> {title}</div>'
            f'<div class="briefing-section-body">{body_html}</div>'
            f'</div>'
        )
    return ''.join(html_parts)


def _get_or_create_public_token(briefing):
    if not briefing.public_token:
        briefing.public_token = secrets.token_urlsafe(32)
        db.session.commit()
    return briefing.public_token


def _briefing_to_dict(briefing):
    token = _get_or_create_public_token(briefing)
    return {
        'id': briefing.id,
        'start_date': briefing.start_date.isoformat(),
        'end_date': briefing.end_date.isoformat(),
        'generated_at': briefing.generated_at.isoformat(),
        'model_used': briefing.model_used,
        'input_tokens': briefing.input_token_count,
        'output_tokens': briefing.output_token_count,
        'content_html': _render_briefing_html(briefing.content),
        'public_pdf_url': url_for('public_briefing_pdf', token=token),
    }


# ── Admin: Stats debug (diagnostyka) ─────────────────────────────────────────

@app.route('/admin/stats/debug')
@login_required
@admin_required
def admin_stats_debug():
    from sqlalchemy import text
    total      = Report.query.count()
    completed  = Report.query.filter_by(status='completed').count()
    inprogress = Report.query.filter_by(status='in_progress').count()
    last10 = db.session.execute(text(
        "SELECT id, status, "
        "substr(created_at,1,19) as created_short, "
        "substr(completed_at,1,19) as completed_short "
        "FROM reports ORDER BY id DESC LIMIT 10"
    )).fetchall()
    daily_test = db.session.execute(text(
        "SELECT substr(completed_at,1,10) as day, count(*) as cnt "
        "FROM reports WHERE status='completed' AND completed_at IS NOT NULL "
        "GROUP BY substr(completed_at,1,10) ORDER BY day DESC LIMIT 10"
    )).fetchall()
    return jsonify({
        'total': total,
        'completed': completed,
        'in_progress': inprogress,
        'last_10_reports': [
            {'id': r[0], 'status': r[1], 'created_at': r[2], 'completed_at': r[3]}
            for r in last10
        ],
        'daily_completed_last_10_days': [
            {'day': r[0], 'cnt': r[1]}
            for r in daily_test
        ],
    })


# ── Admin: NG task reports (AJAX) ─────────────────────────────────────────────

@app.route('/api/ng-task-reports')
@login_required
@admin_required
def api_ng_task_reports():
    # Passenger/IQHost może zdublować query string: ?task_id=219?task_id=219
    # dlatego bierzemy surową wartość i wycinamy tylko część liczbową przed '?'/'&'
    _raw = request.args.get('task_id', '', type=str)
    try:
        task_id = int(_raw.split('?')[0].split('&')[0].strip())
    except (ValueError, AttributeError):
        task_id = None
    if not task_id:
        app.logger.warning('ng-task-reports: brak task_id, raw=%r', _raw)
        return jsonify({'task': None, 'reports': []})
    days_raw = request.args.get('days', '30', type=str)
    try:
        days = int(days_raw.split('?')[0].split('&')[0].strip())
    except (ValueError, AttributeError):
        days = 30
    cutoff_str = (datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)).date().isoformat()
    task = get_or_404(Task, task_id)
    # Normalizuj po stronie Pythona — SQLite lower() obsługuje tylko ASCII,
    # dlatego porównanie robimy w Pythonie, nie w SQL
    norm_title = task.title.strip().rstrip('. ').strip().lower()
    all_tasks = Task.query.all()
    task_ids = [
        t.id for t in all_tasks
        if t.title.strip().rstrip('. ').strip().lower() == norm_title
    ]
    if task_id not in task_ids:
        task_ids.append(task_id)
    app.logger.info('ng-task-reports task_id=%s norm_title=%r task_ids=%s cutoff=%s',
                    task_id, norm_title, task_ids, cutoff_str)
    items = (ReportItem.query
             .filter(ReportItem.task_id.in_(task_ids), ReportItem.result == 'ng')
             .join(Report, Report.id == ReportItem.report_id)
             .filter(Report.status == 'completed',
                     func.substr(Report.completed_at, 1, 10) >= cutoff_str)
             .order_by(func.substr(Report.completed_at, 1, 10).desc(), Report.id.desc())
             .all())
    app.logger.info('ng-task-reports found %d items', len(items))
    reports_out = []
    seen = set()
    for item in items:
        r = item.report
        if r.id in seen:
            continue
        seen.add(r.id)
        try:
            date_str = r.completed_at.strftime('%d.%m.%Y') if r.completed_at else '—'
        except Exception:
            date_str = str(r.completed_at)[:10] if r.completed_at else '—'
        reports_out.append({
            'id':     r.id,
            'title':  r.title,
            'author': r.author.username if r.author else '—',
            'date':   date_str,
            'url':    url_for('report_detail', report_id=r.id),
            'status': r.status,
        })
    resp = jsonify({'task': task.title, 'reports': reports_out})
    resp.headers['Cache-Control'] = 'no-store'
    return resp


# ── Admin: Template duration reports (AJAX) ───────────────────────────────────

@app.route('/api/template-duration-reports')
@login_required
@admin_required
def api_template_duration_reports():
    _raw = request.args.get('template_id', '', type=str)
    try:
        template_id = int(_raw.split('?')[0].split('&')[0].strip())
    except (ValueError, AttributeError):
        template_id = None
    if not template_id:
        return jsonify({'template': None, 'reports': []})
    template = get_or_404(ChecklistTemplate, template_id)
    reports = (Report.query
               .filter_by(template_id=template_id, status='completed')
               .filter(Report.duration_seconds.isnot(None))
               .order_by(Report.duration_seconds.desc())
               .limit(50).all())

    def _fmt(secs):
        if secs is None:
            return '—'
        secs = int(secs)
        if secs < 60:
            return '< 1 min'
        h, rem = divmod(secs, 3600)
        m = rem // 60
        return (f'{h} h {m} min' if m else f'{h} h') if h else f'{m} min'

    return jsonify({
        'template': template.name,
        'reports': [{
            'id':       r.id,
            'title':    r.title,
            'author':   r.author.username,
            'date':     r.created_at.strftime('%d.%m.%Y'),
            'duration': _fmt(r.duration_seconds),
            'url':      url_for('report_detail', report_id=r.id),
        } for r in reports]
    })


# ── Admin: Templates ──────────────────────────────────────────────────────────

@app.route('/admin/templates')
@login_required
@kontroler_required
def admin_templates():
    templates = ChecklistTemplate.query.order_by(ChecklistTemplate.name).all()
    return render_template('admin/templates.html', templates=templates)


@app.route('/admin/templates/export')
@login_required
@admin_required
def admin_templates_export():
    import json as _json
    templates = ChecklistTemplate.query.order_by(ChecklistTemplate.name).all()
    data = []
    for t in templates:
        cats = []
        for cat in t.categories.order_by(Category.order):
            tasks = []
            for task in cat.tasks.order_by(Task.order):
                tasks.append({
                    'title': task.title,
                    'description': task.description,
                    'order': task.order,
                    'is_active': task.is_active,
                    'requires_photo': task.requires_photo,
                })
            cats.append({
                'name': cat.name,
                'order': cat.order,
                'is_active': cat.is_active,
                'tasks': tasks,
            })
        data.append({
            'name': t.name,
            'description': t.description,
            'is_active': t.is_active,
            'categories': cats,
        })
    payload = _json.dumps({'templates': data}, ensure_ascii=False, indent=2)
    response = make_response(payload)
    response.headers['Content-Type'] = 'application/json; charset=utf-8'
    response.headers['Content-Disposition'] = 'attachment; filename="szablony.json"'
    return response


@app.route('/admin/templates/import', methods=['POST'])
@login_required
@admin_required
def admin_templates_import():
    import json as _json
    f = request.files.get('file')
    if not f or not f.filename.endswith('.json'):
        flash('Wybierz plik .json.', 'error')
        return redirect(url_for('admin_templates'))
    try:
        data = _json.loads(f.read().decode('utf-8'))
        templates_data = data.get('templates', [])
    except Exception:
        flash('Nieprawidłowy plik JSON.', 'error')
        return redirect(url_for('admin_templates'))

    count = 0
    for t in templates_data:
        tmpl = ChecklistTemplate(
            name=t.get('name', 'Szablon'),
            description=t.get('description'),
            is_active=t.get('is_active', True),
        )
        db.session.add(tmpl)
        db.session.flush()
        for c in t.get('categories', []):
            cat = Category(
                template_id=tmpl.id,
                name=c.get('name', 'Kategoria'),
                order=c.get('order', 0),
                is_active=c.get('is_active', True),
            )
            db.session.add(cat)
            db.session.flush()
            for tk in c.get('tasks', []):
                db.session.add(Task(
                    category_id=cat.id,
                    title=tk.get('title', ''),
                    description=tk.get('description'),
                    order=tk.get('order', 0),
                    is_active=tk.get('is_active', True),
                    requires_photo=tk.get('requires_photo', False),
                ))
        count += 1
    db.session.commit()
    flash(f'Zaimportowano {count} {"szablon" if count == 1 else "szablony" if 2 <= count <= 4 else "szablonów"}.', 'success')
    return redirect(url_for('admin_templates'))


@app.route('/admin/templates/new', methods=['GET', 'POST'])
@login_required
@kontroler_required
def admin_template_new():
    if request.method == 'POST':
        tmpl = ChecklistTemplate(
            name=request.form['name'].strip(),
            description=request.form.get('description', '').strip(),
            template_type=request.form.get('template_type', 'kontroler'),
        )
        db.session.add(tmpl)
        db.session.commit()
        flash('Szablon dodany.', 'success')
        return redirect(url_for('admin_template_categories', tmpl_id=tmpl.id))
    return render_template('admin/template_form.html', template=None)


@app.route('/admin/templates/<int:tmpl_id>/edit', methods=['GET', 'POST'])
@login_required
@kontroler_required
def admin_template_edit(tmpl_id):
    tmpl = get_or_404(ChecklistTemplate, tmpl_id)
    if request.method == 'POST':
        tmpl.name = request.form['name'].strip()
        tmpl.description = request.form.get('description', '').strip()
        tmpl.template_type = request.form.get('template_type', 'kontroler')
        tmpl.is_active = 'is_active' in request.form
        db.session.commit()
        flash('Szablon zaktualizowany.', 'success')
        return redirect(url_for('admin_templates'))
    return render_template('admin/template_form.html', template=tmpl)


@app.route('/admin/templates/<int:tmpl_id>/copy', methods=['POST'])
@login_required
@kontroler_required
def admin_template_copy(tmpl_id):
    src = get_or_404(ChecklistTemplate, tmpl_id)
    copy = ChecklistTemplate(
        name=f'Kopia – {src.name}',
        description=src.description,
        is_active=src.is_active,
    )
    db.session.add(copy)
    db.session.flush()
    for cat in src.categories.order_by(Category.order):
        new_cat = Category(
            template_id=copy.id,
            name=cat.name,
            order=cat.order,
            is_active=cat.is_active,
        )
        db.session.add(new_cat)
        db.session.flush()
        for task in cat.tasks.order_by(Task.order):
            db.session.add(Task(
                category_id=new_cat.id,
                title=task.title,
                description=task.description,
                order=task.order,
                is_active=task.is_active,
                requires_photo=task.requires_photo,
            ))
    db.session.commit()
    flash(f'Skopiowano szablon jako „{copy.name}".', 'success')
    return redirect(url_for('admin_templates'))


@app.route('/admin/templates/<int:tmpl_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_template_delete(tmpl_id):
    tmpl = get_or_404(ChecklistTemplate, tmpl_id)
    db.session.delete(tmpl)
    db.session.commit()
    flash('Szablon usunięty.', 'success')
    return redirect(url_for('admin_templates'))


@app.route('/admin/templates/bulk-delete', methods=['POST'])
@login_required
@admin_required
def admin_templates_bulk_delete():
    ids = request.form.getlist('tmpl_ids', type=int)
    if not ids:
        flash('Nie zaznaczono żadnych szablonów.', 'warning')
        return redirect(url_for('admin_templates'))
    templates = ChecklistTemplate.query.filter(ChecklistTemplate.id.in_(ids)).all()
    count = len(templates)
    for tmpl in templates:
        db.session.delete(tmpl)
    db.session.commit()
    _audit('templates_bulk_delete', detail=f'count={count}')
    flash(f'Usunięto {count} {"szablon" if count == 1 else "szablony" if 2 <= count <= 4 else "szablonów"}.', 'success')
    return redirect(url_for('admin_templates'))


# ── Admin: Categories (scoped to template) ────────────────────────────────────

@app.route('/admin/templates/<int:tmpl_id>/categories')
@login_required
@kontroler_required
def admin_template_categories(tmpl_id):
    tmpl = get_or_404(ChecklistTemplate, tmpl_id)
    categories = tmpl.categories.order_by(Category.order).all()
    return render_template('admin/categories.html', template=tmpl, categories=categories)


@app.route('/admin/templates/<int:tmpl_id>/categories/new', methods=['GET', 'POST'])
@login_required
@kontroler_required
def admin_category_new(tmpl_id):
    tmpl = get_or_404(ChecklistTemplate, tmpl_id)
    if request.method == 'POST':
        cat = Category(
            template_id=tmpl_id,
            name=request.form['name'].strip(),
            order=int(request.form.get('order', 0)),
        )
        db.session.add(cat)
        db.session.commit()
        flash('Kategoria dodana.', 'success')
        return redirect(url_for('admin_template_categories', tmpl_id=tmpl_id))
    return render_template('admin/category_form.html', template=tmpl, category=None)


@app.route('/admin/categories/<int:cat_id>/edit', methods=['GET', 'POST'])
@login_required
@kontroler_required
def admin_category_edit(cat_id):
    cat = get_or_404(Category, cat_id)
    if request.method == 'POST':
        cat.name = request.form['name'].strip()
        cat.order = int(request.form.get('order', 0))
        cat.is_active = 'is_active' in request.form
        db.session.commit()
        flash('Kategoria zaktualizowana.', 'success')
        return redirect(url_for('admin_template_categories', tmpl_id=cat.template_id))
    return render_template('admin/category_form.html', template=cat.template, category=cat)


@app.route('/admin/categories/<int:cat_id>/delete', methods=['POST'])
@login_required
@kontroler_required
def admin_category_delete(cat_id):
    cat = get_or_404(Category, cat_id)
    tmpl_id = cat.template_id
    db.session.delete(cat)
    db.session.commit()
    flash('Kategoria usunięta.', 'success')
    return redirect(url_for('admin_template_categories', tmpl_id=tmpl_id))


# ── Admin: Tasks ──────────────────────────────────────────────────────────────

@app.route('/admin/categories/<int:cat_id>/tasks')
@login_required
@kontroler_required
def admin_tasks(cat_id):
    cat = get_or_404(Category, cat_id)
    tasks = cat.tasks.order_by(Task.order).all()
    return render_template('admin/tasks.html', category=cat, tasks=tasks)


@app.route('/admin/categories/<int:cat_id>/tasks/new', methods=['GET', 'POST'])
@login_required
@kontroler_required
def admin_task_new(cat_id):
    cat = get_or_404(Category, cat_id)
    if request.method == 'POST':
        task_type = request.form.get('task_type', 'ok_ng')
        vmin = request.form.get('value_min', '').strip()
        vmax = request.form.get('value_max', '').strip()
        task = Task(
            category_id=cat_id,
            title=request.form['title'].strip(),
            description=request.form.get('description', '').strip(),
            order=int(request.form.get('order', 0)),
            is_active='is_active' in request.form,
            requires_photo='requires_photo' in request.form,
            task_type=task_type,
            value_min=float(vmin.replace(',', '.')) if vmin else None,
            value_max=float(vmax.replace(',', '.')) if vmax else None,
            unit=request.form.get('unit', '').strip() or None,
        )
        db.session.add(task)
        db.session.commit()
        flash('Zadanie dodane.', 'success')
        return redirect(url_for('admin_tasks', cat_id=cat_id))
    return render_template('admin/task_form.html', category=cat, task=None)


@app.route('/admin/tasks/<int:task_id>/edit', methods=['GET', 'POST'])
@login_required
@kontroler_required
def admin_task_edit(task_id):
    task = get_or_404(Task, task_id)
    if request.method == 'POST':
        task.title = request.form['title'].strip()
        task.description = request.form.get('description', '').strip()
        task.order = int(request.form.get('order', 0))
        task.is_active = 'is_active' in request.form
        task.requires_photo = 'requires_photo' in request.form
        task.task_type = request.form.get('task_type', 'ok_ng')
        vmin = request.form.get('value_min', '').strip()
        vmax = request.form.get('value_max', '').strip()
        task.value_min = float(vmin.replace(',', '.')) if vmin else None
        task.value_max = float(vmax.replace(',', '.')) if vmax else None
        task.unit = request.form.get('unit', '').strip() or None
        db.session.commit()
        flash('Zadanie zaktualizowane.', 'success')
        return redirect(url_for('admin_tasks', cat_id=task.category_id))
    return render_template('admin/task_form.html', category=task.category, task=task)


@app.route('/admin/tasks/<int:task_id>/delete', methods=['POST'])
@login_required
@kontroler_required
def admin_task_delete(task_id):
    task = get_or_404(Task, task_id)
    cat_id = task.category_id
    db.session.delete(task)
    db.session.commit()
    flash('Zadanie usunięte.', 'success')
    return redirect(url_for('admin_tasks', cat_id=cat_id))


@app.route('/admin/categories/<int:cat_id>/tasks/bulk-delete', methods=['POST'])
@login_required
@kontroler_required
def admin_tasks_bulk_delete(cat_id):
    cat = get_or_404(Category, cat_id)
    ids = request.form.getlist('task_ids', type=int)
    if not ids:
        flash('Nie zaznaczono żadnych zadań.', 'warning')
        return redirect(url_for('admin_tasks', cat_id=cat_id))
    tasks = Task.query.filter(Task.id.in_(ids), Task.category_id == cat_id).all()
    count = len(tasks)
    for task in tasks:
        db.session.delete(task)
    db.session.commit()
    flash(f'Usunięto {count} {"zadanie" if count == 1 else "zadania" if 2 <= count <= 4 else "zadań"}.', 'success')
    return redirect(url_for('admin_tasks', cat_id=cat_id))


# ── Admin: Installers (monterzy) ─────────────────────────────────────────────

@app.route('/admin/installers', methods=['GET', 'POST'])
@login_required
@kontroler_required
def admin_installers():
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add':
            name = request.form.get('name', '').strip()
            if not name:
                flash('Imię i nazwisko montera jest wymagane.', 'warning')
            else:
                inst = Installer(name=name)
                db.session.add(inst)
                db.session.commit()
                _audit('installer_add', 'Installer', inst.id, name)
                flash(f'Dodano montera „{name}".', 'success')

        elif action == 'toggle':
            inst = get_or_404(Installer, request.form.get('inst_id', type=int))
            inst.is_active = not inst.is_active
            db.session.commit()
            _audit('installer_toggle', 'Installer', inst.id,
                   f'{inst.name} -> {"active" if inst.is_active else "inactive"}')
            flash(f'Monter „{inst.name}" {"aktywowany" if inst.is_active else "dezaktywowany"}.', 'success')

        elif action == 'delete':
            inst = get_or_404(Installer, request.form.get('inst_id', type=int))
            name = inst.name
            db.session.delete(inst)
            db.session.commit()
            _audit('installer_delete', 'Installer', inst.id, name)
            flash(f'Monter „{name}" usunięty.', 'success')

        return redirect(url_for('admin_installers'))

    installers = Installer.query.order_by(Installer.name).all()
    return render_template('admin/installers.html', installers=installers)


# ── Admin: Users ──────────────────────────────────────────────────────────────

@app.route('/admin/users')
@login_required
@superadmin_required
def admin_users():
    users = User.query.order_by(User.username).all()
    return render_template('admin/users.html', users=users)


@app.route('/admin/users/new', methods=['GET', 'POST'])
@login_required
@superadmin_required
def admin_user_new():
    if request.method == 'POST':
        username = request.form['username'].strip()
        email = request.form['email'].strip()
        password = request.form['password']
        role = request.form.get('role', 'kontroler')
        if role not in ROLES:
            flash('Wybierz poprawną rolę.', 'error')
        elif len(password) < 8:
            flash('Hasło musi mieć co najmniej 8 znaków.', 'error')
        elif not _valid_email(email):
            flash('Nieprawidłowy format adresu e-mail.', 'error')
        elif User.query.filter_by(username=username).first():
            flash('Nazwa użytkownika jest już zajęta.', 'error')
        elif User.query.filter_by(email=email).first():
            flash('Użytkownik z tym adresem e-mail już istnieje.', 'error')
        else:
            u = User(username=username, email=email, role=role)
            u.set_password(password)
            db.session.add(u)
            try:
                db.session.commit()
                _audit('user_create', 'user', u.id, f'username={username}')
                flash('Użytkownik został dodany.', 'success')
                return redirect(url_for('admin_users'))
            except IntegrityError:
                db.session.rollback()
                flash('Użytkownik z tą nazwą lub adresem e-mail już istnieje.', 'error')
    return render_template('admin/user_form.html', user=None)


@app.route('/admin/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@superadmin_required
def admin_user_edit(user_id):
    user = get_or_404(User, user_id)
    if request.method == 'POST':
        new_email = request.form['email'].strip()
        new_password = request.form.get('password', '')
        if not _valid_email(new_email):
            flash('Nieprawidłowy format adresu e-mail.', 'error')
            return render_template('admin/user_form.html', user=user)
        if new_password and len(new_password) < 8:
            flash('Hasło musi mieć co najmniej 8 znaków.', 'error')
            return render_template('admin/user_form.html', user=user)
        conflict = User.query.filter_by(email=new_email).first()
        if conflict and conflict.id != user.id:
            flash('Użytkownik z tym adresem e-mail już istnieje.', 'error')
            return render_template('admin/user_form.html', user=user)
        user.email = new_email
        # Rola super admina jest stała, nie da się jej zdegradować z panelu
        new_role = request.form.get('role', 'kontroler')
        user.role = 'administrator' if user.is_superadmin else (new_role if new_role in ROLES else user.role_key)
        if new_password:
            user.set_password(new_password)
        try:
            db.session.commit()
            _audit('user_edit', 'user', user.id, f'email={new_email}')
            flash('Dane użytkownika zaktualizowane.', 'success')
            return redirect(url_for('admin_users'))
        except IntegrityError:
            db.session.rollback()
            flash('Adres e-mail jest już zajęty przez innego użytkownika.', 'error')
    return render_template('admin/user_form.html', user=user)


@app.route('/admin/users/uprawnienia', methods=['GET', 'POST'])
@login_required
@superadmin_required
def admin_permissions():
    """Macierz rola x moduł: superuser ustawia, które moduły widzi dana rola."""
    if request.method == 'POST':
        granted = set(request.form.getlist('perm'))
        for role in ROLES:
            for module in MODULES:
                row = RolePermission.query.filter_by(role=role, module=module).first()
                allowed = f'{role}:{module}' in granted
                if row is None:
                    db.session.add(RolePermission(role=role, module=module, allowed=allowed))
                else:
                    row.allowed = allowed
        db.session.commit()
        _audit('permissions_update', 'role_permissions', None, f'{len(granted)} uprawnień')
        flash('Uprawnienia ról zapisane.', 'success')
        return redirect(url_for('admin_permissions'))
    rows = {(r.role, r.module): r.allowed for r in RolePermission.query.all()}
    matrix = {role: {m: rows.get((role, m), m in DEFAULT_PERMISSIONS.get(role, ())) for m in MODULES}
              for role in ROLES}
    return render_template('admin/permissions.html', roles=ROLES, role_labels=ROLE_LABELS,
                           modules=MODULES, matrix=matrix)


@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@login_required
@superadmin_required
def admin_user_delete(user_id):
    user = get_or_404(User, user_id)
    if user.id == current_user.id:
        flash('Nie możesz usunąć własnego konta.', 'error')
    elif user.is_superadmin:
        flash('Konta super admina nie można usunąć.', 'error')
    else:
        uname = user.username
        # Reassign reports to current admin before deleting — user_id is NOT NULL
        for r in user.reports.all():
            r.user_id = current_user.id
        db.session.flush()
        db.session.delete(user)
        db.session.commit()
        _audit('user_delete', 'user', user_id, f'username={uname}')
        flash('Użytkownik usunięty.', 'success')
    return redirect(url_for('admin_users'))


# ── Ustawienia konta (każdy zalogowany, tylko własne dane) ───────────────────

@app.route('/konto/motyw', methods=['POST'])
@login_required
def account_theme():
    """Zapis motywu (jasny/ciemny) na koncie, wywoływany przełącznikiem motywu w menu."""
    theme = (request.get_json(silent=True) or {}).get('theme')
    if theme not in ('light', 'dark'):
        return jsonify({'ok': False, 'error': 'Nieprawidłowy motyw.'}), 400
    current_user.theme = theme
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/konto', methods=['GET', 'POST'])
@login_required
def account_settings():
    user = current_user._get_current_object()
    if request.method == 'POST':
        action = request.form.get('action', '')
        if action == 'email':
            new_email = request.form.get('email', '').strip()
            conflict = User.query.filter(func.lower(User.email) == new_email.lower()).first()
            if not _valid_email(new_email):
                flash('Nieprawidłowy format adresu e-mail.', 'error')
            elif conflict and conflict.id != user.id:
                flash('Użytkownik z tym adresem e-mail już istnieje.', 'error')
            else:
                user.email = new_email
                try:
                    db.session.commit()
                    _audit('account_email', 'user', user.id, f'email={new_email}')
                    flash('Adres e-mail został zapisany.', 'success')
                    return redirect(url_for('account_settings'))
                except IntegrityError:
                    db.session.rollback()
                    flash('Adres e-mail jest już zajęty.', 'error')
        elif action == 'theme':
            theme = request.form.get('theme', '')
            if theme in ('light', 'dark'):
                user.theme = theme
                db.session.commit()
                _audit('account_theme', 'user', user.id, f'theme={theme}')
                flash('Wygląd zapisany.', 'success')
                return redirect(url_for('account_settings'))
            flash('Nieprawidłowy motyw.', 'error')
        elif action == 'password':
            current_pw = request.form.get('current_password', '')
            new_pw = request.form.get('new_password', '')
            repeat_pw = request.form.get('repeat_password', '')
            if not user.check_password(current_pw):
                flash('Obecne hasło jest nieprawidłowe.', 'error')
            elif len(new_pw) < 8:
                flash('Nowe hasło musi mieć co najmniej 8 znaków.', 'error')
            elif new_pw != repeat_pw:
                flash('Nowe hasła nie są takie same.', 'error')
            elif new_pw == current_pw:
                flash('Nowe hasło musi się różnić od obecnego.', 'error')
            else:
                user.set_password(new_pw)
                db.session.commit()
                _audit('account_password', 'user', user.id)
                flash('Hasło zostało zmienione.', 'success')
                return redirect(url_for('account_settings'))
    return render_template('account.html', user=user,
                           can_reorder_menu=True,
                           role_label=user.role_label)


# ── Helpers: szablony i serie list ───────────────────────────────────────────────────────

def _find_matching_template(product_name, template_type='kontroler'):
    """Dopasowuje szablon po tokenach nazwy.
    Tokeny szablonu (min. 2 znaki, bez numerów wersji v1/v2...)
    są szukane w tokenach nazwy produktu.
    Wygrywa szablon z największą liczbą pasujących tokenów,
    przy remisie — z najdłuższym pasującym tokenem."""
    import re

    def _tokens(s):
        s = re.sub(r'\bv\d+\b', '', s, flags=re.IGNORECASE)
        parts = re.split(r'[^a-zA-Z0-9]+', s)
        return {p.lower() for p in parts if len(p) >= 1}

    product_tokens = _tokens(product_name)
    if not product_tokens:
        return None

    candidates = []
    for tpl in ChecklistTemplate.query.filter_by(is_active=True, template_type=template_type).all():
        tpl_tokens = _tokens(tpl.name)
        common = tpl_tokens & product_tokens
        if common:
            unmatched = len(tpl_tokens) - len(common)
            # Duża kara za nieużyte tokeny szablonu — template z 100% trafień
            # zawsze bije template z nieużytymi tokenami
            score = len(common) * 100 + max(len(t) for t in common) - unmatched * 1000
            candidates.append((score, tpl))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _create_checklist_series(product, client, zo, quantity, template, user_id, due=None):
    """Tworzy `quantity` list kontrolnych (seria) dla zlecenia z Listy produkcyjnej: klucz QR
    (produkt|klient|ZO) pozwala odnaleźć je skanem tego samego kodu. Zwraca liczbę utworzonych list."""
    quantity = max(1, int(quantity or 1))
    bid = uuid.uuid4().hex if quantity > 1 else None
    key = _qr_key(product, client, zo)
    label = zo or ''
    for unit in range(1, quantity + 1):
        title = f'{product} – {client or "-"} – kontrola QA szt. {unit}/{quantity}'
        if label:
            title += f' ({label})'
        if due:
            title += f' (termin {due.strftime("%d.%m.%Y")})'
        report = Report(user_id=user_id, template_id=template.id, title=title[:256], qr_key=key,
                        batch_id=bid, batch_index=unit if bid else None,
                        batch_total=quantity if bid else None)
        db.session.add(report)
        db.session.flush()
        for cat in template.categories.filter_by(is_active=True).order_by(Category.order):
            for task in cat.tasks.filter_by(is_active=True).order_by(Task.order):
                db.session.add(ReportItem(report_id=report.id, task_id=task.id))
    return quantity


# ── Alerty ────────────────────────────────────────────────────────────────────

@app.route('/alerts')
@login_required
def alerts_list():
    alerts = (Alert.query.filter_by(recipient_id=current_user.id)
              .order_by(Alert.created_at.desc()).limit(60).all())
    Alert.query.filter_by(recipient_id=current_user.id, is_read=False).update({'is_read': True})
    db.session.commit()
    return render_template('alerts/list.html', alerts=alerts)


@app.route('/alerts/poll')
@login_required
def alerts_poll():
    unread = Alert.query.filter_by(recipient_id=current_user.id, is_read=False).count()
    latest = (Alert.query
              .filter_by(recipient_id=current_user.id, is_read=False)
              .order_by(Alert.created_at.desc())
              .limit(3)
              .all())
    return jsonify({
        'unread': unread,
        'items': [{'id': a.id, 'message': a.message, 'type': a.alert_type} for a in latest],
    })


@app.route('/alerts/read/<int:alert_id>', methods=['POST'])
@login_required
def alert_read(alert_id):
    a = get_or_404(Alert, alert_id)
    if a.recipient_id == current_user.id:
        a.is_read = True
        db.session.commit()
    return redirect(request.form.get('next') or url_for('alerts_list'))


# ── Desktop API ───────────────────────────────────────────────────────────────

def _report_to_dict(r):
    return {
        'id': r.id, 'title': r.title, 'status': r.status,
        'created_at': localdt_filter(r.created_at), 'completed_at': localdt_filter(r.completed_at),
        'completion_percent': r.completion_percent,
        'author': r.author.username,
    }


@app.route('/api/desktop/login', methods=['POST'])
def api_desktop_login():
    data = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    ip = request.remote_addr or 'unknown'
    if _is_rate_limited(ip):
        return jsonify({'error': 'rate_limited'}), 429
    user = User.query.filter_by(username=username).first()
    if user and user.check_password(password):
        login_user(user, remember=False)
        session['_last_activity'] = datetime.now(UTC).isoformat()
        csrf = _get_csrf_token()
        _audit('login_desktop', 'user', user.id)
        return jsonify({'ok': True, 'username': user.username,
                        'role': user.role, 'csrf_token': csrf})
    _record_failure(ip)
    return jsonify({'error': 'invalid_credentials'}), 401


@app.route('/api/desktop/logout', methods=['POST'])
@login_required
def api_desktop_logout():
    logout_user()
    session.clear()
    return jsonify({'ok': True})


@app.route('/api/desktop/dashboard')
@login_required
def api_desktop_dashboard():
    uid = current_user.id
    total     = Report.query.filter_by(user_id=uid).count()
    completed = Report.query.filter_by(user_id=uid, status='completed').count()
    recent    = (Report.query.filter_by(user_id=uid)
                 .order_by(Report.created_at.desc()).limit(8).all())
    return jsonify({
        'total': total, 'completed': completed,
        'in_progress': total - completed,
        'recent': [_report_to_dict(r) for r in recent],
    })


@app.route('/api/desktop/templates')
@login_required
def api_desktop_templates():
    tmpls = ChecklistTemplate.query.filter_by(is_active=True).order_by(ChecklistTemplate.name).all()
    return jsonify([{'id': t.id, 'name': t.name} for t in tmpls])


@app.route('/api/desktop/reports/new', methods=['POST'])
@login_required
def api_desktop_new_report():
    data    = request.get_json(silent=True) or {}
    tmpl_id = data.get('template_id')
    tmpl    = get_or_404(ChecklistTemplate, tmpl_id)
    title   = (data.get('title', '').strip()
               or f'{tmpl.name} – {datetime.now().strftime("%d.%m.%Y %H:%M")}')
    report  = Report(user_id=current_user.id, template_id=tmpl_id, title=title)
    db.session.add(report)
    db.session.flush()
    for cat in tmpl.categories.filter_by(is_active=True).order_by(Category.order):
        for task in cat.tasks.filter_by(is_active=True).order_by(Task.order):
            db.session.add(ReportItem(report_id=report.id, task_id=task.id))
    db.session.commit()
    _audit('create_report', 'report', report.id, detail=title)
    return jsonify({'ok': True, 'report_id': report.id})


@app.route('/api/desktop/reports')
@login_required
def api_desktop_reports():
    page    = request.args.get('page', 1, type=int)
    status  = request.args.get('status', '')
    q_str   = request.args.get('q', '').strip()
    base_q  = Report.query if current_user.is_admin else Report.query.filter_by(user_id=current_user.id)
    if status:
        base_q = base_q.filter_by(status=status)
    if q_str:
        base_q = base_q.filter(Report.title.ilike(f'%{q_str}%'))
    pag = base_q.order_by(Report.created_at.desc()).paginate(page=page, per_page=20, error_out=False)
    return jsonify({
        'items': [_report_to_dict(r) for r in pag.items],
        'page': pag.page, 'pages': pag.pages, 'total': pag.total,
    })


@app.route('/api/desktop/reports/<int:report_id>')
@login_required
def api_desktop_report_detail(report_id):
    report = get_or_404(Report, report_id)
    if report.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    categories = []
    items_by_cat = {}
    for item in report.items.join(Task).join(Category).order_by(Category.order, Task.order).all():
        cat = item.task.category
        if cat.id not in items_by_cat:
            items_by_cat[cat.id] = {'id': cat.id, 'name': cat.name, 'items': []}
            categories.append(items_by_cat[cat.id])
        items_by_cat[cat.id]['items'].append({
            'id': item.id, 'task_id': item.task_id,
            'title': item.task.title,
            'description': item.task.description or '',
            'result': item.result,
            'notes': item.notes or '',
            'checked_at': localdt_filter(item.checked_at),
            'requires_photo': item.task.requires_photo,
            'photos': [{'id': p.id, 'filename': p.filename} for p in item.photos.all()],
        })
    return jsonify({
        'id': report.id, 'title': report.title,
        'status': report.status,
        'created_at': localdt_filter(report.created_at),
        'completed_at': localdt_filter(report.completed_at),
        'completion_percent': report.completion_percent,
        'stats': report.stats,
        'categories': categories,
    })


@app.route('/api/desktop/reports/<int:report_id>/complete', methods=['POST'])
@login_required
def api_desktop_complete_report(report_id):
    report = get_or_404(Report, report_id)
    if report.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    now_utc = datetime.now(UTC)
    report.status = 'completed'
    report.completed_at = now_utc
    ref = report.started_at or report.created_at
    ref = ref if ref.tzinfo else ref.replace(tzinfo=UTC)
    report.duration_seconds = int((now_utc - ref).total_seconds())
    db.session.commit()
    _audit('complete_report', 'report', report.id)
    return jsonify({'ok': True})


@app.route('/api/desktop/reports/<int:report_id>/reopen', methods=['POST'])
@login_required
def api_desktop_reopen_report(report_id):
    report = get_or_404(Report, report_id)
    if not current_user.is_admin:
        abort(403)
    report.status = 'in_progress'
    report.completed_at = None
    db.session.commit()
    _audit('reopen_report', 'report', report.id)
    return jsonify({'ok': True})


# ── PWA ───────────────────────────────────────────────────────────────────────

@app.route('/manifest.json')
def manifest():
    return send_from_directory('static', 'manifest.json')


@app.route('/sw.js')
def service_worker():
    response = send_from_directory('static', 'sw.js')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['Content-Type'] = 'application/javascript'
    return response


# ── Error handlers ────────────────────────────────────────────────────────────

@app.errorhandler(403)
def forbidden(e):
    return render_template('errors/403.html'), 403


@app.errorhandler(404)
def not_found(e):
    return render_template('errors/404.html'), 404


@app.errorhandler(413)
def payload_too_large(e):
    limit_mb = app.config.get('MAX_CONTENT_LENGTH', 0) // (1024 * 1024)
    msg = f'Plik jest za duży. Maksymalny rozmiar przesyłki to {limit_mb} MB.'
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify(ok=False, error=msg), 413
    flash(msg, 'error')
    return redirect(request.referrer or url_for('dashboard'))


@app.errorhandler(500)
def internal_error(e):
    app.logger.error('500 error: %s | path=%s | ip=%s', e, request.path, request.remote_addr,
                     exc_info=True)
    db.session.rollback()
    return render_template('errors/500.html'), 500


# ── Init DB ───────────────────────────────────────────────────────────────────

def _migrate_roles():
    """Zamienia stare nazwy ról na nowe (admin -> administrator, planowanie -> technolog itd.)."""
    for old, new in dict(LEGACY_ROLES, admin='administrator').items():
        User.query.filter_by(role=old).update({'role': new})
    db.session.commit()


def _seed_role_permissions():
    """Dopisuje brakujące wpisy macierzy rola x moduł wartościami domyślnymi (nie zmienia istniejących)."""
    existing = {(r.role, r.module) for r in RolePermission.query.all()}
    for role in ROLES:
        for module in MODULES:
            if (role, module) not in existing:
                db.session.add(RolePermission(role=role, module=module,
                                              allowed=module in DEFAULT_PERMISSIONS.get(role, ())))
    db.session.commit()


SUPERADMIN_EMAIL = 'palczowski@gmail.com'


def _mark_superadmin():
    """Oznacza konto właściciela jako super admina (tylko ono tworzy i edytuje konta).
    Flagi nie da się nadać z aplikacji, wyłącznie tutaj, przy starcie."""
    u = User.query.filter(func.lower(User.email) == SUPERADMIN_EMAIL).first()
    if u and not u.is_superadmin:
        u.is_superadmin = True
        u.role = 'administrator'
        db.session.commit()


def init_db():
    os.makedirs('instance', exist_ok=True)
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['QAR_UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['DOKUMENTACJA_UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['PRODUKCJA_FOLDER'], exist_ok=True)
    os.makedirs(app.config['PKJ_UPLOAD_FOLDER'], exist_ok=True)
    with app.app_context():
        _migrate_schema()
        db.create_all()
        _migrate_installer_data()
        _drop_orders_and_monter()
        _migrate_roles()
        _mark_superadmin()
        _seed_role_permissions()
        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', email='admin@psh.pl', role='administrator')
            admin.set_password('admin123')
            db.session.add(admin)
        if not ChecklistTemplate.query.first():
            _seed_demo_data()
        _seed_kosztorys()
        _seed_departments()
        _backfill_department_tokens()
        _seed_qar_categories()
        db.session.commit()


def _drop_orders_and_monter():
    """Usunięcie modułu Zamówienia i roli Monter (2026-10-21): konta o usuniętych rolach dostają rolę
    operatora (tylko odczyt), kasowane są szablony i listy montażowe, kolumny i tabela zamówień."""
    from sqlalchemy import inspect, text
    insp = inspect(db.engine)
    names = insp.get_table_names()
    with db.engine.begin() as conn:
        if 'users' in names:
            conn.execute(text("UPDATE users SET role='operator' WHERE role IN ('monter', 'order')"))
    if 'reports' in names and 'checklist_templates' in names:
        monter_reports = Report.query.filter_by(report_type='monter').all()
        if monter_reports:
            _delete_reports(monter_reports)
        for tpl in ChecklistTemplate.query.filter_by(template_type='monter').all():
            db.session.delete(tpl)
        db.session.commit()
    with db.engine.begin() as conn:
        for tbl, col in (('reports', 'order_id'), ('alerts', 'order_id'), ('prod_rows', 'order_id')):
            if tbl in names and col in [c['name'] for c in insp.get_columns(tbl)]:
                try:
                    conn.execute(text(f'ALTER TABLE {tbl} DROP COLUMN {col}'))
                except Exception as exc:   # starszy SQLite lub ograniczenie: kolumna zostaje, bez wpływu
                    app.logger.info('Kolumna %s.%s zostaje (nie da się jej usunąć): %s', tbl, col, str(exc)[:80])
        if 'orders' in names:
            conn.execute(text('DROP TABLE orders'))


def _migrate_installer_data():
    """Jednorazowe przeniesienie starych wpisów ReportItem.value_text (typ zadania
    'installer') do tabeli report_item_installers, wprowadzonej dla obsługi
    kilku monterów na jednym punkcie checklisty. Wymaga, by tabela już istniała
    (tworzona przez db.create_all() tuż wcześniej w init_db())."""
    from sqlalchemy import inspect
    insp = inspect(db.engine)
    if 'report_item_installers' not in insp.get_table_names():
        return
    candidates = (ReportItem.query
                  .join(Task, Task.id == ReportItem.task_id)
                  .filter(Task.task_type == 'installer',
                          ReportItem.value_text.isnot(None),
                          ReportItem.value_text != '')
                  .all())
    changed = False
    for item in candidates:
        if item.installers.count() > 0:
            continue
        name = item.value_text.strip()
        if not name:
            continue
        inst = Installer.query.filter_by(name=name).first()
        if not inst:
            inst = Installer(name=name, is_active=False)
            db.session.add(inst)
            db.session.flush()
        db.session.add(ReportItemInstaller(report_item_id=item.id, installer_id=inst.id))
        changed = True
    if changed:
        db.session.commit()


def _recompute_prod_row_dates_v2(conn):
    """Jednorazowo: filtr zakresu dat liczy po kolumnie DATA (rola `start`), a nie po terminie realizacji.
    Dopisuje rolę do istniejących arkuszy (po nazwach kolumn) i przelicza ProdRow.row_date."""
    from produkcja import excel_io
    from sqlalchemy import text
    for sid, cols_json, header_row, mapping_json in conn.execute(text(
            'SELECT id, columns_json, header_row, mapping_json FROM prod_sheets')).fetchall():
        mapping = json.loads(mapping_json or '{}')
        if 'start' not in mapping:
            found = excel_io.detect_mapping(json.loads(cols_json or '[]'), header_row is None).get('start')
            if found is not None:
                mapping['start'] = found
                conn.execute(text('UPDATE prod_sheets SET mapping_json = :m WHERE id = :i'),
                             {'m': json.dumps(mapping), 'i': sid})
        if not ({'start', 'order_date', 'due'} & set(mapping)):
            continue
        updates = []
        for rid, data in conn.execute(text('SELECT id, data FROM prod_rows WHERE sheet_id = :s'),
                                      {'s': sid}).fetchall():
            d = excel_io.row_date(json.loads(data or '[]'), mapping)
            updates.append({'d': d.isoformat() if d else None, 'i': rid})
        if updates:
            conn.execute(text('UPDATE prod_rows SET row_date = :d WHERE id = :i'), updates)
    conn.commit()


def _migrate_schema():
    """Add new columns to existing SQLite DB without dropping data."""
    from sqlalchemy import inspect, text
    engine = db.engine
    insp = inspect(engine)
    with engine.connect() as conn:
        if 'prod_entries' in insp.get_table_names():
            cols = [c['name'] for c in insp.get_columns('prod_entries')]
            if 'employee_name' not in cols:
                conn.execute(text('ALTER TABLE prod_entries ADD COLUMN employee_name VARCHAR(128)'))
                conn.commit()
        if 'prod_employees' in insp.get_table_names():
            cols = [c['name'] for c in insp.get_columns('prod_employees')]
            if 'badge_code' not in cols:
                conn.execute(text('ALTER TABLE prod_employees ADD COLUMN badge_code VARCHAR(64)'))
                conn.execute(text('CREATE INDEX IF NOT EXISTS ix_prod_employees_badge_code ON prod_employees (badge_code)'))
                conn.commit()
        if 'categories' in insp.get_table_names():
            cols = [c['name'] for c in insp.get_columns('categories')]
            if 'template_id' not in cols:
                conn.execute(text('ALTER TABLE categories ADD COLUMN template_id INTEGER'))
                conn.commit()
            if 'qar_category' not in cols:
                conn.execute(text('ALTER TABLE categories ADD COLUMN qar_category VARCHAR(64)'))
                conn.commit()
        if 'reports' in insp.get_table_names():
            cols = [c['name'] for c in insp.get_columns('reports')]
            if 'template_id' not in cols:
                conn.execute(text('ALTER TABLE reports ADD COLUMN template_id INTEGER'))
                conn.commit()
        if 'report_items' in insp.get_table_names():
            cols = [c['name'] for c in insp.get_columns('report_items')]
            if 'result' not in cols:
                conn.execute(text('ALTER TABLE report_items ADD COLUMN result VARCHAR(4)'))
                conn.commit()
        if 'reports' in insp.get_table_names():
            cols = [c['name'] for c in insp.get_columns('reports')]
            if 'qr_key' not in cols:
                conn.execute(text('ALTER TABLE reports ADD COLUMN qr_key VARCHAR(256)'))
                conn.execute(text('CREATE INDEX IF NOT EXISTS ix_reports_qr_key ON reports (qr_key)'))
                conn.commit()
        if 'reports' in insp.get_table_names():
            cols = [c['name'] for c in insp.get_columns('reports')]
            for col, ddl in [
                ('locked_by_id',     'ALTER TABLE reports ADD COLUMN locked_by_id INTEGER REFERENCES users(id)'),
                ('locked_at',        'ALTER TABLE reports ADD COLUMN locked_at DATETIME'),
                ('duration_seconds', 'ALTER TABLE reports ADD COLUMN duration_seconds INTEGER'),
                ('started_at',       'ALTER TABLE reports ADD COLUMN started_at DATETIME'),
                ('batch_id',         'ALTER TABLE reports ADD COLUMN batch_id VARCHAR(32)'),
                ('batch_index',      'ALTER TABLE reports ADD COLUMN batch_index INTEGER'),
                ('batch_total',      'ALTER TABLE reports ADD COLUMN batch_total INTEGER'),
            ]:
                if col not in cols:
                    conn.execute(text(ddl)); conn.commit()
        if 'tasks' in insp.get_table_names():
            cols = [c['name'] for c in insp.get_columns('tasks')]
            for col, ddl in [
                ('task_type', "ALTER TABLE tasks ADD COLUMN task_type VARCHAR(16) DEFAULT 'ok_ng'"),
                ('value_min', 'ALTER TABLE tasks ADD COLUMN value_min FLOAT'),
                ('value_max', 'ALTER TABLE tasks ADD COLUMN value_max FLOAT'),
                ('unit',      'ALTER TABLE tasks ADD COLUMN unit VARCHAR(32)'),
            ]:
                if col not in cols:
                    conn.execute(text(ddl)); conn.commit()
        if 'report_items' in insp.get_table_names():
            cols = [c['name'] for c in insp.get_columns('report_items')]
            if 'value_text' not in cols:
                conn.execute(text('ALTER TABLE report_items ADD COLUMN value_text VARCHAR(256)'))
                conn.commit()
            if 'qar_report_id' not in cols:
                conn.execute(text('ALTER TABLE report_items ADD COLUMN qar_report_id INTEGER '
                                  'REFERENCES qar_reports(id)'))
                conn.commit()
        if 'checklist_templates' in insp.get_table_names():
            cols = [c['name'] for c in insp.get_columns('checklist_templates')]
            if 'template_type' not in cols:
                conn.execute(text("ALTER TABLE checklist_templates ADD COLUMN template_type VARCHAR(16) DEFAULT 'kontroler'"))
                conn.commit()
        if 'reports' in insp.get_table_names():
            cols = [c['name'] for c in insp.get_columns('reports')]
            if 'report_type' not in cols:
                conn.execute(text("ALTER TABLE reports ADD COLUMN report_type VARCHAR(16) DEFAULT 'kontroler'"))
                conn.commit()
        if 'quote_configs' in insp.get_table_names():
            cols = [c['name'] for c in insp.get_columns('quote_configs')]
            for col, ddl in [
                ('monoblok',      'ALTER TABLE quote_configs ADD COLUMN monoblok INTEGER DEFAULT 0'),
                ('back_seal',     'ALTER TABLE quote_configs ADD COLUMN back_seal BOOLEAN DEFAULT 0'),
                ('lock_standard', 'ALTER TABLE quote_configs ADD COLUMN lock_standard BOOLEAN DEFAULT 0'),
                ('stud_m6_qty',   'ALTER TABLE quote_configs ADD COLUMN stud_m6_qty INTEGER DEFAULT 0'),
                ('nut_m8_qty',    'ALTER TABLE quote_configs ADD COLUMN nut_m8_qty INTEGER DEFAULT 0'),
                ('hinge_qty',     'ALTER TABLE quote_configs ADD COLUMN hinge_qty INTEGER DEFAULT 0'),
                ('screw_cap_qty', 'ALTER TABLE quote_configs ADD COLUMN screw_cap_qty INTEGER DEFAULT 0'),
                ('plug_qty',      'ALTER TABLE quote_configs ADD COLUMN plug_qty INTEGER DEFAULT 0'),
                ('inox_grade',    "ALTER TABLE quote_configs ADD COLUMN inox_grade VARCHAR(4) DEFAULT '304'"),
            ]:
                if col not in cols:
                    conn.execute(text(ddl)); conn.commit()
        # Migrate role: 'user' → 'kontroler'
        if 'users' in insp.get_table_names():
            conn.execute(text("UPDATE users SET role='kontroler' WHERE role='user'"))
            conn.commit()
        # Moduły "Kontrola etapów" i "Marszruta produkcji" (karty) zostały usunięte
        # (2026-10-16), zastąpiła je Kontrola zamówień. Kasujemy ich tabele, dzieci przed rodzicami.
        for tbl in ('interstage_checks', 'spawalnia_records', 'spawalnia_operators',
                    'giecie_operators', 'ciecie_operators',
                    'routing_card_photos', 'routing_card_stages', 'routing_cards'):
            if tbl in insp.get_table_names():
                conn.execute(text(f'DROP TABLE {tbl}')); conn.commit()
        # Rola Spawacz (tylko Kontrola etapów) przechodzi na Operatora.
        if 'users' in insp.get_table_names():
            conn.execute(text("UPDATE users SET role='operator' WHERE role='spawacz'"))
            conn.commit()
        # Moduł śledzenia produkcji (zlecenia, wózki z ilościami, ruchy, kontrole etapów, złom) został
        # usunięty (2026-10-21). Kasujemy jego tabele, dzieci przed rodzicami. Etapy, maszyny, bufory,
        # punkty kontrolne etapów i szablony produktów zostają.
        for tbl in ('scrap_records', 'stage_control_results', 'stage_controls', 'work_order_controls',
                    'movements', 'work_in_progress', 'stock_positions', 'stock_positions_old',
                    'work_order_component_stages', 'work_order_components', 'work_orders'):
            if tbl in insp.get_table_names():
                conn.execute(text(f'DROP TABLE {tbl}'))
                conn.commit()
        if 'alerts' in insp.get_table_names():
            conn.execute(text("DELETE FROM alerts WHERE message LIKE 'Do kontroli: ZO%'"))
            conn.commit()
        for tbl, col, ddl in (
                ('control_points', 'department_id',
                 'ALTER TABLE control_points ADD COLUMN department_id INTEGER REFERENCES production_departments(id)'),
                ('control_records', 'department_id',
                 'ALTER TABLE control_records ADD COLUMN department_id INTEGER REFERENCES production_departments(id)'),
                ('control_records', 'control_order_id',
                 'ALTER TABLE control_records ADD COLUMN control_order_id INTEGER REFERENCES control_orders(id)'),
                ('control_points', 'element_id',
                 'ALTER TABLE control_points ADD COLUMN element_id INTEGER REFERENCES control_elements(id)'),
                ('control_results', 'element_id',
                 'ALTER TABLE control_results ADD COLUMN element_id INTEGER REFERENCES control_elements(id)'),
                ('control_results', 'element_name',
                 'ALTER TABLE control_results ADD COLUMN element_name VARCHAR(128)')):
            if tbl in insp.get_table_names() and col not in [c['name'] for c in insp.get_columns(tbl)]:
                conn.execute(text(ddl))
                conn.commit()
        if 'users' in insp.get_table_names() and 'menu_order' not in [
                c['name'] for c in insp.get_columns('users')]:
            conn.execute(text('ALTER TABLE users ADD COLUMN menu_order TEXT'))
            conn.commit()
        if 'users' in insp.get_table_names() and 'theme' not in [
                c['name'] for c in insp.get_columns('users')]:
            conn.execute(text('ALTER TABLE users ADD COLUMN theme VARCHAR(8)'))
            conn.commit()
        if 'users' in insp.get_table_names() and 'is_superadmin' not in [
                c['name'] for c in insp.get_columns('users')]:
            conn.execute(text('ALTER TABLE users ADD COLUMN is_superadmin BOOLEAN NOT NULL DEFAULT 0'))
            conn.commit()
        # Lista produkcyjna: znacznik utworzenia list kontrolnych zastępuje powiązanie z zamówieniem.
        if 'prod_rows' in insp.get_table_names():
            prod_cols = [c['name'] for c in insp.get_columns('prod_rows')]
            if 'checklists_at' not in prod_cols:
                conn.execute(text('ALTER TABLE prod_rows ADD COLUMN checklists_at DATETIME'))
                if 'order_id' in prod_cols:
                    conn.execute(text('UPDATE prod_rows SET checklists_at = CURRENT_TIMESTAMP '
                                      'WHERE order_id IS NOT NULL'))
                conn.commit()
            if 'prod_user_views' in insp.get_table_names() and 'widths_json' not in [
                    c['name'] for c in insp.get_columns('prod_user_views')]:   # szerokości kolumn użytkownika
                conn.execute(text('ALTER TABLE prod_user_views ADD COLUMN widths_json TEXT'))
                conn.commit()
            if 'row_date' not in prod_cols:   # zakres dat widoku listy produkcyjnej
                conn.execute(text('ALTER TABLE prod_rows ADD COLUMN row_date DATE'))
                conn.execute(text('CREATE INDEX IF NOT EXISTS ix_prod_rows_row_date ON prod_rows (row_date)'))
                conn.commit()
            if 'prod_settings' in insp.get_table_names() and not conn.execute(text(
                    "SELECT 1 FROM prod_settings WHERE key = 'row_date_v2'")).fetchone() and conn.execute(text(
                    "SELECT 1 FROM prod_settings WHERE key = 'row_date_backfilled'")).fetchone():
                _recompute_prod_row_dates_v2(conn)    # bazy z pierwszą wersją dat: przelicz po kolumnie DATA
                conn.execute(text("INSERT INTO prod_settings (key, value) VALUES ('row_date_v2', '1')"))
                conn.commit()
            if 'prod_settings' in insp.get_table_names() and not conn.execute(text(
                    "SELECT 1 FROM prod_settings WHERE key = 'row_date_backfilled'")).fetchone():
                _recompute_prod_row_dates_v2(conn)    # świeża baza: od razu wg kolumny DATA
                conn.execute(text("INSERT INTO prod_settings (key, value) VALUES ('row_date_backfilled', '1')"))
                conn.execute(text("INSERT OR IGNORE INTO prod_settings (key, value) VALUES ('row_date_v2', '1')"))
                conn.commit()
        # Moduł Zadania QA został usunięty (2026-10-21), kasujemy jego tabelę.
        if 'qa_tasks' in insp.get_table_names():
            conn.execute(text('DROP TABLE qa_tasks'))
            conn.commit()
        if 'control_records' in insp.get_table_names() and 'employee_id' not in [
                c['name'] for c in insp.get_columns('control_records')]:
            conn.execute(text('ALTER TABLE control_records ADD COLUMN employee_id INTEGER '
                              'REFERENCES department_employees(id)'))
            conn.commit()
        if 'product_components' in insp.get_table_names():
            # Ilość na sztukę przeniesiona z szablonu na zlecenie (tabela istniała tylko w wersjach deweloperskich).
            cols = [c['name'] for c in insp.get_columns('product_components')]
            if 'per_unit' in cols:
                try:
                    conn.execute(text('ALTER TABLE product_components DROP COLUMN per_unit'))
                    conn.commit()
                except Exception as exc:
                    conn.rollback()
                    app.logger.warning('Nie usunięto kolumny per_unit z product_components: %s', exc)
        if 'department_employees' in insp.get_table_names():
            cols = [c['name'] for c in insp.get_columns('department_employees')]
            if 'token' not in cols:
                conn.execute(text('ALTER TABLE department_employees ADD COLUMN token VARCHAR(48)'))
                conn.commit()
            conn.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS ix_department_employees_token '
                              'ON department_employees (token)'))
            conn.commit()
        if 'qar_reports' in insp.get_table_names():
            cols = [c['name'] for c in insp.get_columns('qar_reports')]
            for col, ddl in [
                ('zo_number',      'ALTER TABLE qar_reports ADD COLUMN zo_number VARCHAR(64)'),
                ('drawing_number', 'ALTER TABLE qar_reports ADD COLUMN drawing_number VARCHAR(64)'),
                ('employee_id',    'ALTER TABLE qar_reports ADD COLUMN employee_id INTEGER REFERENCES department_employees(id)'),
            ]:
                if col not in cols:
                    conn.execute(text(ddl)); conn.commit()
        if 'report_item_installers' in insp.get_table_names():
            cols = [c['name'] for c in insp.get_columns('report_item_installers')]
            if 'is_at_fault' not in cols:
                conn.execute(text('ALTER TABLE report_item_installers ADD COLUMN is_at_fault BOOLEAN DEFAULT 1'))
                conn.commit()
        if 'daily_briefings' in insp.get_table_names():
            cols = [c['name'] for c in insp.get_columns('daily_briefings')]
            if 'pdf_filename' not in cols:
                conn.execute(text('ALTER TABLE daily_briefings ADD COLUMN pdf_filename VARCHAR(256)'))
                conn.commit()
            if 'public_token' not in cols:
                conn.execute(text('ALTER TABLE daily_briefings ADD COLUMN public_token VARCHAR(64)'))
                conn.commit()
        # audit_log / orders / alerts created by db.create_all()
        # qar_reports and qar_photos created by db.create_all() on first run


# ── REST API v1 — integracja z ERP ────────────────────────────────────────────

def _api_checklist_dict(report):
    items = []
    for item in report.items.order_by(ReportItem.id).all():
        items.append({
            'id': item.id,
            'category': item.task.category.name,
            'task': item.task.title,
            'task_type': item.task.task_type,
            'unit': item.task.unit,
            'value_min': item.task.value_min,
            'value_max': item.task.value_max,
            'value': item.value_text,
            'result': item.result,
            'notes': item.notes,
            'checked_at': item.checked_at.isoformat() if item.checked_at else None,
            'installers': [
                {'name': ri.installer.name, 'role': ri.role, 'is_at_fault': ri.is_at_fault}
                for ri in item.installers.all()
            ] if item.task.task_type == 'installer' else None,
        })
    return {
        'id': report.id, 'title': report.title, 'status': report.status,
        'author': report.author.username,
        'created_at': report.created_at.isoformat(),
        'started_at': report.started_at.isoformat() if report.started_at else None,
        'completed_at': report.completed_at.isoformat() if report.completed_at else None,
        'duration_seconds': report.duration_seconds,
        'duration_str': report.duration_str,
        'template': report.template.name if report.template else None,
        'stats': report.stats,
        'score': report.score,
        'compliant': report.stats['ng'] == 0,
        'items': items,
    }


@app.route('/api/v1/templates', methods=['GET'])
@api_key_required
def api_v1_templates():
    templates = (ChecklistTemplate.query
                 .filter_by(is_active=True)
                 .order_by(ChecklistTemplate.name).all())
    return jsonify([{
        'id': t.id, 'name': t.name, 'type': t.template_type,
        'task_count': t.task_count,
    } for t in templates])


@app.route('/api/v1/checklists', methods=['GET'])
@api_key_required
def api_v1_checklists():
    status       = request.args.get('status', '')
    template_id  = request.args.get('template_id', type=int)
    date_from    = request.args.get('date_from', '')
    date_to      = request.args.get('date_to', '')
    page         = request.args.get('page', 1, type=int)

    q = Report.query
    if status in ('in_progress', 'completed'):
        q = q.filter_by(status=status)
    if template_id:
        q = q.filter_by(template_id=template_id)
    if date_from:
        try:
            q = q.filter(Report.created_at >= datetime.strptime(date_from, '%Y-%m-%d'))
        except ValueError:
            pass
    if date_to:
        try:
            q = q.filter(Report.created_at < datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1))
        except ValueError:
            pass

    pag = q.order_by(Report.created_at.desc()).paginate(page=page, per_page=50, error_out=False)
    return jsonify({
        'items': [{
            'id': r.id, 'title': r.title, 'status': r.status,
            'template': r.template.name if r.template else None,
            'author': r.author.username,
            'created_at': r.created_at.isoformat(),
            'completed_at': r.completed_at.isoformat() if r.completed_at else None,
            'completion_percent': r.completion_percent,
            'stats': r.stats,
        } for r in pag.items],
        'page': pag.page, 'pages': pag.pages, 'total': pag.total,
    })


def _resolve_operator(data):
    """Znajduje użytkownika-operatora po `operator` (username) lub `operator_id`.
    Gdy żadne z nich nie podano — zachowanie wsteczne integracji istniejących
    (np. Streamsoft): pierwszy użytkownik z rolą admin.
    Zwraca (User, None) albo (None, komunikat_błędu)."""
    operator_id = data.get('operator_id')
    operator_name = (data.get('operator') or '').strip()
    if operator_id:
        user = db.session.get(User, operator_id)
    elif operator_name:
        user = User.query.filter(User.username.ilike(operator_name)).first()
    else:
        user = User.query.filter(User.role.in_(ADMIN_ROLE_NAMES)).first()
        if not user:
            return None, 'Brak użytkownika systemowego'
        return user, None
    if not user:
        return None, f'Operator {operator_name or operator_id!r} nie istnieje'
    if not user.is_kontroler:
        return None, f'Użytkownik {user.username!r} nie ma uprawnień kontrolera'
    return user, None


def _parse_performed_at(s):
    if not s:
        return datetime.now(UTC)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _resolve_installers(data):
    """Rozwiązuje `installers` z payloadu na listę gotową do zapisu jako
    ReportItemInstaller. Każdy wpis to nazwa montera (string) albo obiekt
    {installer_id|name, role, is_at_fault}. Ta sama lista jest przypisywana
    do KAŻDEGO punktu checklisty typu 'installer' w KAŻDYM raporcie serii —
    czyli jednej ekipy montującej cały produkt/serię.
    Zwraca (lista, None) albo (None, komunikat_błędu)."""
    raw = data.get('installers')
    if not raw:
        return [], None
    if not isinstance(raw, list):
        return None, 'installers musi być listą'
    resolved = []
    for entry in raw:
        if isinstance(entry, str):
            entry = {'name': entry}
        if not isinstance(entry, dict):
            return None, 'Każdy wpis w installers musi być tekstem albo obiektem {name, role}'
        inst = None
        if entry.get('installer_id'):
            inst = db.session.get(Installer, entry['installer_id'])
        elif (entry.get('name') or '').strip():
            inst = Installer.query.filter(Installer.name.ilike(entry['name'].strip())).first()
        else:
            return None, 'Każdy wpis w installers musi mieć name albo installer_id'
        if not inst or not inst.is_active:
            label = entry.get('name') or entry.get('installer_id')
            return None, f'Monter {label!r} nie istnieje lub jest nieaktywny'
        resolved.append({
            'installer_id': inst.id,
            'role': (entry.get('role') or '').strip() or None,
            'is_at_fault': bool(entry.get('is_at_fault', True)),
        })
    return resolved, None


@app.route('/api/v1/checklists', methods=['POST'])
@api_key_required
def api_v1_create_checklist():
    """Tworzy jeden raport albo — z `quantity` > 1 — całą serię (batch), tak jak
    tryb seryjny w UI (`new_checklist`). Służy do zbiorczego wpisywania do bazy
    list kontrolnych wypełnionych ręcznie poza systemem (np. na papierze)."""
    data        = request.get_json(silent=True) or {}
    template_id = data.get('template_id')
    template_name = (data.get('template_name') or '').strip()

    tmpl = None
    if template_id:
        tmpl = db.session.get(ChecklistTemplate, template_id)
    elif template_name:
        tmpl = ChecklistTemplate.query.filter(
            ChecklistTemplate.name.ilike(template_name), ChecklistTemplate.is_active == True
        ).first()
    else:
        return jsonify({'error': 'template_id lub template_name jest wymagane'}), 400
    if not tmpl or not tmpl.is_active:
        return jsonify({'error': 'Szablon nie istnieje lub jest nieaktywny'}), 404

    operator, err = _resolve_operator(data)
    if err:
        return jsonify({'error': err}), 404

    installers_spec, err = _resolve_installers(data)
    if err:
        return jsonify({'error': err}), 404

    try:
        quantity = max(1, min(99, int(data.get('quantity', 1))))
    except (TypeError, ValueError):
        return jsonify({'error': 'quantity musi być liczbą całkowitą 1-99'}), 400

    completed = bool(data.get('completed', False))
    performed_at = _parse_performed_at(data.get('performed_at'))
    if performed_at is None:
        return jsonify({'error': 'performed_at musi być datą w formacie ISO 8601'}), 400

    user_suffix = (data.get('title') or '').strip()
    base_title  = (user_suffix
                   or f'{tmpl.name} – {performed_at.strftime("%d.%m.%Y %H:%M")}')

    def _fill_items(rpt):
        for cat in tmpl.categories.filter_by(is_active=True).order_by(Category.order):
            for task in cat.tasks.filter_by(is_active=True).order_by(Task.order):
                item = ReportItem(report_id=rpt.id, task_id=task.id)
                if completed:
                    item.is_checked = True
                    item.result     = 'ok'
                    item.checked_at = performed_at
                db.session.add(item)
                if task.task_type == 'installer' and installers_spec:
                    db.session.flush()  # potrzebne item.id dla FK
                    for spec in installers_spec:
                        db.session.add(ReportItemInstaller(
                            report_item_id=item.id, installer_id=spec['installer_id'],
                            role=spec['role'], is_at_fault=spec['is_at_fault']))

    bid = uuid.uuid4().hex if quantity > 1 else None
    created = []
    for i in range(1, quantity + 1):
        title = f'{base_title} – {i}/{quantity}' if quantity > 1 else base_title
        report = Report(
            user_id=operator.id, template_id=tmpl.id, title=title,
            created_at=performed_at,
            batch_id=bid, batch_index=i if bid else None,
            batch_total=quantity if bid else None,
        )
        if completed:
            report.status       = 'completed'
            report.started_at   = performed_at
            report.completed_at = performed_at
        db.session.add(report)
        db.session.flush()
        _fill_items(report)
        created.append(report)

    db.session.commit()
    _audit('api_create_checklist', 'report', created[0].id,
           f'{base_title} x{quantity} operator={operator.username} completed={completed}')

    if quantity == 1:
        r = created[0]
        return jsonify({
            'ok': True, 'id': r.id, 'title': r.title, 'template': tmpl.name,
            'operator': operator.username,
            'status': r.status, 'created_at': r.created_at.isoformat(),
        }), 201
    return jsonify({
        'ok': True, 'batch_id': bid, 'quantity': quantity, 'template': tmpl.name,
        'operator': operator.username,
        'items': [{'id': r.id, 'title': r.title, 'status': r.status} for r in created],
    }), 201


@app.route('/api/v1/users', methods=['GET'])
@api_key_required
def api_v1_users():
    """Lista kontrolerów/adminów — do wyboru operatora w zewnętrznych narzędziach."""
    users = User.query.filter(User.role.in_(roles_matching('administrator', 'kontroler'))).order_by(User.username).all()
    return jsonify([{'id': u.id, 'username': u.username, 'role': u.role} for u in users])


@app.route('/api/v1/installers', methods=['GET'])
@api_key_required
def api_v1_installers():
    """Lista aktywnych monterów — do przypisywania w punktach checklisty typu
    'installer' przy zbiorczym tworzeniu list kontrolnych przez API."""
    installers = Installer.query.filter_by(is_active=True).order_by(Installer.name).all()
    return jsonify([{'id': i.id, 'name': i.name} for i in installers])


@app.route('/api/v1/checklists/<int:report_id>', methods=['GET'])
@api_key_required
def api_v1_checklist_detail(report_id):
    report = get_or_404(Report, report_id)
    return jsonify(_api_checklist_dict(report))


@app.route('/api/v1/report/<int:report_id>', methods=['GET'])
@api_key_required
def api_v1_report(report_id):
    report = get_or_404(Report, report_id)
    return jsonify(_api_checklist_dict(report))


@app.route('/api/v1/checklists/<int:report_id>/start', methods=['POST'])
@api_key_required
def api_v1_start_checklist(report_id):
    report = get_or_404(Report, report_id)
    if report.status == 'completed':
        return jsonify({'error': 'Raport jest już zakończony'}), 400
    now = datetime.now(UTC)
    if report.started_at is None:
        report.started_at = now
    db.session.add(ChecklistSession(report_id=report.id, started_at=now))
    db.session.commit()
    _audit('api_checklist_start', 'report', report.id, report.title)
    return jsonify(_api_checklist_dict(report))


@app.route('/api/v1/checklists/<int:report_id>/complete', methods=['POST'])
@api_key_required
def api_v1_complete_checklist(report_id):
    report = get_or_404(Report, report_id)
    if report.status == 'completed':
        return jsonify({'error': 'Raport jest już zakończony'}), 400
    now_utc = datetime.now(UTC)
    for open_session in ChecklistSession.query.filter_by(report_id=report_id, ended_at=None).all():
        open_session.ended_at = now_utc
    total_seconds = 0
    for s in ChecklistSession.query.filter_by(report_id=report_id).all():
        if s.ended_at is None:
            continue
        s_start = s.started_at if s.started_at.tzinfo else s.started_at.replace(tzinfo=UTC)
        s_end   = s.ended_at   if s.ended_at.tzinfo   else s.ended_at.replace(tzinfo=UTC)
        total_seconds += int((s_end - s_start).total_seconds())
    report.status           = 'completed'
    report.completed_at     = now_utc
    report.locked_by_id     = None
    report.locked_at        = None
    report.duration_seconds = total_seconds if total_seconds > 0 else None
    db.session.flush()
    db.session.commit()
    _audit('api_checklist_complete', 'report', report.id, report.title)
    return jsonify(_api_checklist_dict(report))


@app.route('/api/v1/prices', methods=['GET'])
@api_key_required
def api_v1_prices():
    return jsonify([{
        'code': p.code, 'name': p.name, 'price': p.price, 'unit': p.unit,
        'updated_at': p.updated_at.isoformat() if p.updated_at else None,
    } for p in MaterialPrice.query.order_by(MaterialPrice.code).all()])


@app.route('/api/v1/labor-rates', methods=['GET'])
@api_key_required
def api_v1_labor_rates():
    return jsonify([{
        'volume_range': r.volume_range, 'label': r.label,
        'laser': r.laser, 'bending': r.bending, 'welding': r.welding,
        'grinding': r.grinding, 'assembly': r.assembly, 'packaging': r.packaging,
        'total': r.total,
    } for r in LaborRate.query.all()])


@app.route('/api/v1/quotes', methods=['GET'])
@api_key_required
def api_v1_quotes():
    status_f = request.args.get('status', '')
    q = Quote.query
    if status_f in ('draft', 'sent', 'accepted', 'closed'):
        q = q.filter_by(status=status_f)
    quotes = q.order_by(Quote.created_at.desc()).limit(200).all()
    return jsonify([{
        'id': quote.id, 'number': quote.number, 'client_name': quote.client_name,
        'cabinet_type': quote.cabinet_type.name if quote.cabinet_type else None,
        'status': quote.status, 'created_at': quote.created_at.isoformat(),
    } for quote in quotes])


@app.route('/api/v1/quotes/<int:quote_id>', methods=['GET'])
@api_key_required
def api_v1_quote_detail(quote_id):
    quote = get_or_404(Quote, quote_id)
    cfg = quote.config
    return jsonify({
        'id': quote.id, 'number': quote.number, 'client_name': quote.client_name,
        'cabinet_type': quote.cabinet_type.name if quote.cabinet_type else None,
        'status': quote.status, 'created_at': quote.created_at.isoformat(),
        'dimensions': {'width': cfg.width, 'height': cfg.height, 'depth': cfg.depth} if cfg else None,
        'calculation': cfg.calculation if cfg else None,
    })


@app.route('/api/v1/qar', methods=['GET'])
@api_key_required
def api_v1_qar_list():
    status_f = request.args.get('status', '')
    q = QARReport.query
    if status_f in ('open', 'in_progress', 'closed'):
        q = q.filter_by(status=status_f)
    reports = q.order_by(QARReport.created_at.desc()).limit(200).all()
    return jsonify([{
        'id': r.id, 'number': r.number, 'zo_number': r.zo_number,
        'drawing_number': r.drawing_number, 'title': r.title, 'category': r.category,
        'status': r.status, 'created_at': r.created_at.isoformat(),
    } for r in reports])


@app.route('/api/v1/qar/<int:report_id>', methods=['GET'])
@api_key_required
def api_v1_qar_detail(report_id):
    r = get_or_404(QARReport, report_id)
    return jsonify({
        'id': r.id, 'number': r.number, 'zo_number': r.zo_number,
        'drawing_number': r.drawing_number, 'title': r.title, 'category': r.category,
        'location': r.location, 'description': r.description, 'findings': r.findings,
        'resolution': r.resolution, 'status': r.status,
        'created_at': r.created_at.isoformat(),
        'verified_at': r.verified_at.isoformat() if r.verified_at else None,
    })


@app.route('/api/v1/qar', methods=['POST'])
@api_key_required
def api_v1_qar_create():
    data        = request.get_json(silent=True) or {}
    title       = (data.get('title') or '').strip()
    description = (data.get('description') or '').strip()
    if not title or not description:
        return jsonify({'error': 'title i description są wymagane'}), 400
    admin_user = User.query.filter(User.role.in_(ADMIN_ROLE_NAMES)).first()
    if not admin_user:
        return jsonify({'error': 'Brak użytkownika systemowego (admin)'}), 500
    report = QARReport(
        number=_next_qar_number(), title=title,
        zo_number=(data.get('zo_number') or '').strip() or None,
        drawing_number=(data.get('drawing_number') or '').strip() or None,
        category=(data.get('category') or '').strip() or None,
        location=(data.get('location') or '').strip() or None,
        description=description, user_id=admin_user.id,
    )
    db.session.add(report)
    db.session.commit()
    _audit('api_qar_create', 'qar_report', report.id, f'number={report.number}')
    return jsonify({'ok': True, 'id': report.id, 'number': report.number}), 201


def _seed_demo_data():
    templates_data = [
        ('Szafa elektryczna – standard', 'Kontrola jakości szafy elektrycznej', [
            ('PRZED ZŁOŻENIEM', 0, [
                'Sprawdzenie dokumentacji technicznej',
                'Weryfikacja listy materiałów (BOM)',
                'Kontrola stanu magazynowego komponentów',
                'Sprawdzenie narzędzi montażowych',
            ]),
            ('MONTAŻ', 1, [
                'Montaż szyny DIN',
                'Instalacja aparatury elektrycznej',
                'Prowadzenie przewodów zgodnie ze schematem',
                'Oznaczenie przewodów i aparatów',
                'Dokręcenie zacisków – moment 1,2 Nm',
            ]),
            ('KONTROLA KOŃCOWA', 2, [
                'Pomiar rezystancji izolacji',
                'Test funkcjonalny układu sterowania',
                'Sprawdzenie poprawności oznaczeń',
                'Dokumentacja fotograficzna wyrobu',
                'Kompletność dokumentacji dostawczej',
            ]),
        ]),
        ('Rozdzielnica PSH – odbiór', 'Odbiór techniczny rozdzielnicy PSH', [
            ('DOKUMENTACJA', 0, [
                'Kompletność schematu elektrycznego',
                'Zgodność z zamówieniem klienta',
            ]),
            ('OGLĘDZINY', 1, [
                'Stan obudowy i lakieru',
                'Poprawność oznaczeń aparatów',
                'Montaż dławnic i uszczelnień',
            ]),
            ('POMIARY', 2, [
                'Ciągłość obwodów ochronnych',
                'Rezystancja izolacji > 1 MΩ',
                'Próba napięciowa 1 kV / 1 min',
            ]),
        ]),
    ]
    for tmpl_name, tmpl_desc, cats in templates_data:
        tmpl = ChecklistTemplate(name=tmpl_name, description=tmpl_desc)
        db.session.add(tmpl)
        db.session.flush()
        for cat_name, cat_order, tasks in cats:
            cat = Category(template_id=tmpl.id, name=cat_name, order=cat_order)
            db.session.add(cat)
            db.session.flush()
            for i, title in enumerate(tasks):
                db.session.add(Task(category_id=cat.id, title=title, order=i))


def _seed_kosztorys():
    """Inicjuje dane bazowe dla modułu kosztorysów."""
    cabinet_defaults = [
        ('PSH_IP65',    'Szafa PSH IP65',              'Szafa stalowa wodoodporna IP65, stojąca, tył spawany lub przykręcany'),
        ('PSH_COMPACT', 'Szafa PSH Kompakt',           'Szafa kompaktowa ścienna IP65, mała (200–1200 mm)'),
        ('PSH_INOX',    'Szafa PSH INOX',              'Szafa ze stali nierdzewnej AISI 304, IP66'),
        ('PSH_MODULAR', 'Szafa PSH Modular (DIN)',     'Szafa z szynami DIN, przeznaczona pod aparaturę modułową'),
    ]
    for code, name, desc in cabinet_defaults:
        if not CabinetType.query.filter_by(code=code).first():
            db.session.add(CabinetType(code=code, name=name, description=desc))

    default_prices = [
        ('dc01',              'Blacha DC01',                    4.00,  'PLN/kg'),
        ('dx51',              'Blacha DX51 (ocynk)',             4.60,  'PLN/kg'),
        ('inox304',           'Blacha INOX 304',               18.00,  'PLN/kg'),
        ('paint',             'Malowanie proszkowe',            18.00,  'PLN/m2'),
        ('seal',              'Uszczelka (wylanie)',            11.00,  'PLN/m'),
        ('hinge',             'Zawias',                          6.00,  'PLN/szt'),
        ('lock_3pt',          'Zamek trzypunktowy',             50.00,  'PLN/szt'),
        ('lock_cam',          'Zamek krzywkowy',                 5.00,  'PLN/szt'),
        ('stud_m6',           'Trzpień wstrzeliwany M6',         0.20,  'PLN/szt'),
        ('nut_m8',            'Nakrętka z podkładką M8',         0.05,  'PLN/szt'),
        ('screw_cap',         'Śruba do kap',                    0.05,  'PLN/szt'),
        ('plug',              'Zaślepka otworów',                0.16,  'PLN/szt'),
        ('din_rail',          'Szyna DIN 35mm',                  5.00,  'PLN/m'),
        ('transport',         'Transport',                      100.00,  'PLN/szt'),
        ('packaging',         'Opakowanie',                       2.00,  'PLN/szt'),
        ('labels',            'Naklejki komplet',                 2.00,  'PLN/kpl'),
        ('fixed_costs',       'Koszty stałe (sprz./licencje)',  50.00,  'PLN/szt'),
        ('custom_color',      'Kolor niestandardowy',           100.00,  'PLN/szt'),
        ('design_hour',       'Koszt projektowania',            200.00,  'PLN/h'),
        ('inox_labor_factor',     'Mnożnik robocizny INOX 304',       1.40,  'mnożnik'),
        ('inox316',               'Blacha INOX 316',                 25.00,  'PLN/kg'),
        ('inox316_labor_factor',  'Mnożnik robocizny INOX 316',       1.60,  'mnożnik'),
        ('lock_standard',         'Zamek zwykły',                     5.00,  'PLN/szt'),
    ]
    for code, name, price, unit in default_prices:
        if not MaterialPrice.query.filter_by(code=code).first():
            db.session.add(MaterialPrice(code=code, name=name, price=price, unit=unit))

    default_rates = [
        ('do400',  'do 400 dm³',   120, 50,  80,  50, 40, 20),
        ('do900',  'do 900 dm³',   150, 55, 100,  60, 50, 25),
        ('pow900', 'pow. 900 dm³', 180, 60, 140,  80, 70, 30),
    ]
    for vrange, label, laser, bend, weld, grind, assy, pack in default_rates:
        if not LaborRate.query.filter_by(volume_range=vrange).first():
            db.session.add(LaborRate(
                volume_range=vrange, label=label,
                laser=laser, bending=bend, welding=weld,
                grinding=grind, assembly=assy, packaging=pack,
            ))

    # Katalog produktow PSH Kompakt (standardowe rozmiary)
    compact_products = [
        ('KMP-200200', 200,  200, 155,  359.0,  0),
        ('KMP-300200', 300,  200, 155,  399.0,  1),
        ('KMP-300300', 300,  300, 155,  449.0,  2),
        ('KMP-400300', 400,  300, 155,  499.0,  3),
        ('KMP-400400', 400,  400, 200,  549.0,  4),
        ('KMP-500400', 500,  400, 200,  599.0,  5),
        ('KMP-500500', 500,  500, 200,  649.0,  6),
        ('KMP-600400', 600,  400, 200,  649.0,  7),
        ('KMP-600600', 600,  600, 250,  749.0,  8),
        ('KMP-800600', 800,  600, 250,  899.0,  9),
        ('KMP-800800', 800,  800, 300, 1099.0, 10),
        ('KMP-1000800',1000, 800, 300, 1299.0, 11),
        ('KMP-10001000',1000,1000, 300, 1499.0, 12),
        ('KMP-1200800',1200, 800, 300, 1599.0, 13),
        ('KMP-12001000',1200,1000, 300,1799.0, 14),
        ('KMP-12001200',1200,1200, 400,2112.0, 15),
    ]
    for code, w, h, d, price, sord in compact_products:
        if not CatalogProduct.query.filter_by(code=code).first():
            name = f'PSH Kompakt {w}x{h}x{d}'
            db.session.add(CatalogProduct(
                code=code, family='compact', name=name,
                width=w, height=h, depth=d,
                catalog_price=price, sort_order=sord,
            ))


def _seed_departments():
    """Inicjuje domyślną listę działów produkcyjnych (Ustawienia produkcji)."""
    if ProductionDepartment.query.first():
        return
    default_departments = [
        'Cięcie', 'Laser', 'Gięcie', 'Zgrzewanie', 'Spawanie',
        'Czyszczenie', 'Mycie', 'Malowanie', 'Składanie', 'Kontrola Jakości',
    ]
    for i, name in enumerate(default_departments):
        db.session.add(ProductionDepartment(name=name, order=i))


def _backfill_department_tokens():
    """Nadaje token plakietki pracownikom działów dodanym przed wprowadzeniem plakietek."""
    for emp in DepartmentEmployee.query.filter(DepartmentEmployee.token.is_(None)).all():
        emp.token = secrets.token_hex(16)


def _seed_qar_categories():
    """Inicjuje domyślną listę kategorii raportów QAR."""
    if QARCategory.query.first():
        return
    default_categories = [
        'Spawanie', 'Montaż', 'Materiał', 'Malowanie', 'Konstrukcja',
        'Dokumentacja', 'Cięcie', 'Gięcie', 'Czyszczenie', 'Szlifiernia', 'Myjnia', 'Inne',
    ]
    for i, name in enumerate(default_categories):
        db.session.add(QARCategory(name=name, order=i))


# Funkcje listy kontrolnej udostępnione modułowi produkcja (bez importu `from app`,
# który przy uruchomieniu `python app.py` załadowałby moduł drugi raz).
from produkcja import register_helpers  # noqa: E402
register_helpers(find_matching_template=_find_matching_template,
                 create_checklist_series=_create_checklist_series,
                 qr_key=_qr_key,
                 find_qr_reports=_find_qr_reports)


if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
