import os
import re
import csv
import uuid
import secrets
import logging
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
                    Photo, AuditLog, Order, Alert,
                    CabinetType, MaterialPrice, LaborRate, Quote, QuoteConfig,
                    CatalogProduct,
                    SpawalniaOperator, SpawalniaRecord,
                    ChecklistSession, Installer,
                    QARReport, QARPhoto, QATask,
                    ProductionDepartment, DepartmentEmployee, RoutingTemplate,
                    RoutingTemplateStage, RoutingCard, RoutingCardStage, RoutingCardPhoto)

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)

# ── Blueprints ─────────────────────────────────────────────────────────────────
from kosztorys import kosztorys_bp  # noqa: E402
app.register_blueprint(kosztorys_bp)

from spawalnia import spawalnia_bp  # noqa: E402
app.register_blueprint(spawalnia_bp)

from qar import qar_bp  # noqa: E402
app.register_blueprint(qar_bp)
from qar.routes import _next_qar_number  # noqa: E402 — reużyte przez /api/v1/qar

from zadania_qa import zadania_qa_bp  # noqa: E402
app.register_blueprint(zadania_qa_bp)

from marszruta import marszruta_bp  # noqa: E402
app.register_blueprint(marszruta_bp)

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


def order_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_order_user:
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


def monter_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or (not current_user.is_monter and not current_user.is_admin):
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


@app.context_processor
def _inject_globals():
    from datetime import date
    unread = 0
    pending_qa_reports = []
    pending_qa_count = 0
    if current_user.is_authenticated:
        unread = Alert.query.filter_by(recipient_id=current_user.id, is_read=False).count()
        if current_user.role == 'kontroler':
            cutoff_naive = (datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=30))
            admin_ids_sq = db.session.query(User.id).filter(User.role == 'admin')
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
    if current_user.role == 'konstruktor':
        return redirect(url_for('qar.list_reports'))

    # Pracownicy niebędący adminem dostają uproszczony dashboard z kafelkami
    if current_user.role in ('order', 'spawacz', 'monter'):
        # Minimalne dane potrzebne do kafelkowego dashboardu
        return render_template('dashboard.html',
                               recent_reports=[], all_reports_count=0,
                               completed_count=0, in_progress_count=0,
                               date_from='', date_to='', templates=[],
                               pending_order_reports=[], recent_orders=[],
                               orders_active=0, orders_ready=0)

    date_from = request.args.get('date_from', '')
    date_to   = request.args.get('date_to', '')
    q = Report.query if current_user.is_admin else Report.query.filter_by(user_id=current_user.id)
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
    rq = Report.query if current_user.is_admin else Report.query.filter_by(user_id=current_user.id)
    recent_reports     = rq.order_by(Report.created_at.desc()).limit(5).all()
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
    if current_user.is_admin:
        from sqlalchemy import asc, nullslast
        recent_orders   = (Order.query
                           .filter(Order.status.in_(['active', 'in_control', 'ready_to_ship']))
                           .order_by(nullslast(asc(Order.due_date)), Order.created_at.desc())
                           .limit(8).all())
        orders_active   = Order.query.filter(Order.status.in_(['active', 'in_control'])).count()
        orders_ready    = Order.query.filter_by(status='ready_to_ship').count()
    else:
        recent_orders   = []
        orders_active   = 0
        orders_ready    = 0
    if current_user.is_admin:
        quality_alerts = (Alert.query
                          .filter_by(recipient_id=current_user.id, alert_type='ng')
                          .order_by(Alert.created_at.desc())
                          .limit(5).all())
    else:
        quality_alerts = []
    return render_template('dashboard.html',
                           recent_reports=recent_reports,
                           all_reports_count=all_reports_count,
                           completed_count=completed_count,
                           in_progress_count=in_progress_count,
                           date_from=date_from, date_to=date_to,
                           templates=templates,
                           pending_order_reports=pending_order_reports,
                           recent_orders=recent_orders,
                           orders_active=orders_active,
                           orders_ready=orders_ready,
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
    active_orders = (Order.query
                     .filter(Order.status.in_(['active', 'in_control']))
                     .order_by(Order.number).all())
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
            # ── Tryb seryjny: bez powiązania z zamówieniem ──
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

        # ── Tryb pojedynczy: z opcjonalnym powiązaniem z zamówieniem ──
        dup = _recent_duplicate_report(current_user.id, tmpl_id, base_title)
        if dup:
            flash('Taka kontrola została już utworzona — otwieram istniejącą.', 'warning')
            return redirect(url_for('checklist_view', report_id=dup.id))

        explicit_order_id = request.form.get('order_id', type=int)
        linked_order = None
        if explicit_order_id:
            linked_order = Order.query.filter(
                Order.id == explicit_order_id, Order.status != 'shipped'
            ).first()
        if not linked_order and user_suffix:
            needle = user_suffix.upper().replace(' ', '').replace('-', '').replace('/', '')
            for o in Order.query.filter(Order.status != 'shipped').all():
                canon = o.number.upper().replace(' ', '').replace('-', '').replace('/', '')
                if canon in needle or needle in canon:
                    linked_order = o
                    break
        report = Report(user_id=current_user.id, template_id=tmpl_id,
                        title=base_title, order_id=linked_order.id if linked_order else None)
        db.session.add(report)
        db.session.flush()
        _add_items(report)
        if linked_order:
            if linked_order.status == 'active':
                linked_order.status = 'in_control'
            flash(f'Raport powiązany z zamówieniem {linked_order.number}.', 'success')
        db.session.commit()
        return redirect(url_for('checklist_view', report_id=report.id))

    return render_template('checklist_new.html', templates=templates,
                           active_orders=active_orders)


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
    is_monter_report = report.report_type == 'monter' and current_user.is_monter and report.user_id == current_user.id
    if (report.user_id != current_user.id and not current_user.is_admin
            and not is_kontroler_report and not is_monter_report):
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
    return render_template('checklist.html', report=report,
                           items_by_category=items_by_category, installers=installers)


@app.route('/checklist/<int:report_id>/complete', methods=['POST'])
@login_required
def complete_checklist(report_id):
    report = get_or_404(Report, report_id)
    is_kontroler_report = report.report_type == 'kontroler' and current_user.is_kontroler
    is_monter_report = report.report_type == 'monter' and current_user.is_monter and report.user_id == current_user.id
    if (report.user_id != current_user.id and not current_user.is_admin
            and not is_kontroler_report and not is_monter_report):
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
    if report.order_id:
        _notify_order_report_done(report)
        _check_order_complete(report.order)
        if report.report_type == 'monter':
            msg = (f'Montaż ukończony: {report.title} — '
                   f'przez {current_user.username}')
            for u in User.query.filter(User.role.in_(['admin', 'kontroler'])).all():
                db.session.add(Alert(recipient_id=u.id, message=msg,
                                     alert_type='info', order_id=report.order_id))
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


@app.route('/monter/pool')
@login_required
@monter_required
def monter_pool():
    """Lista dostępnych (nieprzypisanych) list montażowych do pobrania przez montera."""
    from sqlalchemy import asc, nullslast
    available = (Report.query
                 .join(Order, Report.order_id == Order.id)
                 .filter(Report.report_type == 'monter',
                         Report.status == 'in_progress',
                         Report.user_id == Report.user_id)  # placeholder — filtered below
                 .order_by(nullslast(asc(Order.due_date)), Report.created_at.asc())
                 .all())
    # Only show reports not yet taken by anyone (user_id points to order creator — not a monter)
    raw_pool = [r for r in available
                if not db.session.get(User, r.user_id) or db.session.get(User, r.user_id).role not in ('monter',)]

    # Grupuj serie — pokaż jeden wiersz na serię (pierwszy raport z batch)
    seen_batches = set()
    pool = []
    for r in raw_pool:
        if r.batch_id:
            if r.batch_id not in seen_batches:
                seen_batches.add(r.batch_id)
                pool.append(r)   # reprezentant serii (batch_index == 1)
        else:
            pool.append(r)

    my_reports = (Report.query
                  .filter(Report.report_type == 'monter',
                          Report.user_id == current_user.id)
                  .order_by(Report.created_at.desc()).all())
    return render_template('monter/pool.html', pool=pool, my_reports=my_reports)


@app.route('/monter/take/<int:report_id>', methods=['POST'])
@login_required
@monter_required
def monter_take(report_id):
    """Monter przypisuje sobie listę montażową z puli."""
    report = get_or_404(Report, report_id)
    if report.report_type != 'monter':
        abort(400)
    if report.status != 'in_progress':
        flash('Ta lista montażowa jest już zamknięta.', 'warning')
        return redirect(url_for('monter_pool'))
    owner = db.session.get(User, report.user_id)
    if owner and owner.role == 'monter' and report.user_id != current_user.id:
        flash('Ta lista jest już przypisana do innego montera.', 'warning')
        return redirect(url_for('monter_pool'))
    if report.batch_id:
        # Przejmij całą serię — przypisz wszystkie raporty z tego batcha do montera
        batch_reports = Report.query.filter_by(batch_id=report.batch_id).all()
        for r in batch_reports:
            r.user_id = current_user.id
        db.session.commit()
        _audit('monter_take', 'report', report.id, f'monter={current_user.username} batch={report.batch_id}')
        flash(f'Seria {report.batch_total} list montażowych przypisana do Ciebie.', 'success')
    else:
        report.user_id = current_user.id
        db.session.commit()
        _audit('monter_take', 'report', report.id, f'monter={current_user.username}')
        flash(f'Lista montażowa przypisana do Ciebie: {report.title}', 'success')
    return redirect(url_for('checklist_view', report_id=report.id))


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
        admin_ids_sq = db.session.query(User.id).filter(User.role == 'admin')
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
    is_monter_report = report.report_type == 'monter' and current_user.is_monter and report.user_id == current_user.id
    if (report.user_id != current_user.id and not current_user.is_admin
            and not is_kontroler_report and not is_monter_report):
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
    titles = [i.task.title if i.task else 'Zadanie' for i in ng_items]
    msg = f'Niezgodności ({len(ng_items)}): {report.title} — {", ".join(titles)}'
    if len(msg) > 512:
        msg = msg[:511] + '…'
    for u in User.query.filter(User.role.in_(['admin', 'kontroler'])).all():
        db.session.add(Alert(recipient_id=u.id, message=msg,
                             alert_type='ng', order_id=report.order_id))


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

    # ── Marszruta produkcji: wyniki wg działu (30 dni) ──────────────────────
    _stage_day_expr = func.substr(RoutingCardStage.checked_at, 1, 10)
    marszruta_dept_stats = (db.session.query(
        ProductionDepartment.name.label('dept_name'),
        func.count(RoutingCardStage.id).label('total'),
        func.sum(case((RoutingCardStage.result == 'ok', 1), else_=0)).label('ok'),
        func.sum(case((RoutingCardStage.result == 'ng', 1), else_=0)).label('ng'),
    ).join(RoutingCardStage, RoutingCardStage.department_id == ProductionDepartment.id)
     .filter(RoutingCardStage.checked_at.isnot(None),
             _stage_day_expr >= cutoff_str)
     .group_by(ProductionDepartment.id)
     .order_by(ProductionDepartment.order).all())

    # ── Marszruta produkcji: aktywność pracowników (wszystkie oceny) ────────
    marszruta_employee_stats = (db.session.query(
        DepartmentEmployee.name.label('emp_name'),
        ProductionDepartment.name.label('dept_name'),
        func.count(RoutingCardStage.id).label('total'),
        func.sum(case((RoutingCardStage.result == 'ng', 1), else_=0)).label('ng'),
    ).join(RoutingCardStage, RoutingCardStage.employee_id == DepartmentEmployee.id)
     .join(ProductionDepartment, DepartmentEmployee.department_id == ProductionDepartment.id)
     .group_by(DepartmentEmployee.id)
     .order_by(func.count(RoutingCardStage.id).desc())
     .limit(15).all())

    total_routing_cards = RoutingCard.query.count()
    _incomplete_card_ids = (db.session.query(RoutingCardStage.card_id)
                            .filter(RoutingCardStage.result.is_(None)).distinct())
    completed_routing_cards = (RoutingCard.query
                               .filter(RoutingCard.stages.any())
                               .filter(~RoutingCard.id.in_(_incomplete_card_ids))
                               .count())

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
                           marszruta_dept_stats=marszruta_dept_stats,
                           marszruta_employee_stats=marszruta_employee_stats,
                           total_routing_cards=total_routing_cards,
                           completed_routing_cards=completed_routing_cards))
    resp.headers['Cache-Control'] = 'no-store'
    return resp


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
    type_filter = request.args.get('type', '')  # '' | 'kontroler' | 'monter'
    q = ChecklistTemplate.query
    if type_filter in ('kontroler', 'monter'):
        q = q.filter_by(template_type=type_filter)
    templates = q.order_by(ChecklistTemplate.name).all()
    return render_template('admin/templates.html', templates=templates, type_filter=type_filter)


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
@admin_required
def admin_users():
    users = User.query.order_by(User.username).all()
    return render_template('admin/users.html', users=users)


@app.route('/admin/users/new', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_user_new():
    if request.method == 'POST':
        username = request.form['username'].strip()
        email = request.form['email'].strip()
        password = request.form['password']
        role = request.form.get('role', 'user')
        if len(password) < 8:
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
@admin_required
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
        user.role = request.form.get('role', 'kontroler')
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


@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_user_delete(user_id):
    user = get_or_404(User, user_id)
    if user.id == current_user.id:
        flash('Nie możesz usunąć własnego konta.', 'error')
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


# ── Helpers: Order flow ───────────────────────────────────────────────────────

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


def _create_reports_for_template(order, template, rtype, user_id=None):
    """Tworzy quantity raportów jednego typu (kontroler lub monter).

    `user_id` pozwala nadpisać autora raportów, gdy wywołanie nie pochodzi
    z sesji przeglądarki (np. tworzenie zamówienia przez /api/v1/orders)."""
    uid = user_id if user_id is not None else current_user.id
    bid = uuid.uuid4().hex if order.quantity > 1 else None
    for unit in range(1, order.quantity + 1):
        label = 'kontrola QA' if rtype == 'kontroler' else 'montaż'
        title = f'{order.product_name} – {order.client} – {label} szt. {unit}/{order.quantity}'
        if order.due_date:
            title += f' (termin {order.due_date.strftime("%d.%m.%Y")})'
        report = Report(user_id=uid, template_id=template.id,
                        order_id=order.id, title=title, report_type=rtype,
                        batch_id=bid,
                        batch_index=unit if bid else None,
                        batch_total=order.quantity if bid else None)
        db.session.add(report)
        db.session.flush()
        for cat in template.categories.filter_by(is_active=True).order_by(Category.order):
            for task in cat.tasks.filter_by(is_active=True).order_by(Task.order):
                db.session.add(ReportItem(report_id=report.id, task_id=task.id))


def _create_order_reports(order, template, monter_template=None, user_id=None):
    """Tworzy quantity raportów powiązanych z zamówieniem + alerty."""
    _create_reports_for_template(order, template, 'kontroler', user_id=user_id)
    if monter_template:
        _create_reports_for_template(order, monter_template, 'monter', user_id=user_id)

    due_str = order.due_date.strftime('%d.%m.%Y') if order.due_date else '—'

    # Alert dla kontrolerów i adminów
    msg_k = (f'Nowe zlecenie kontroli: {order.product_name} '
             f'({order.number}) – {order.quantity} szt., klient: {order.client}, termin {due_str}')
    for u in User.query.filter(User.role.in_(['admin', 'kontroler'])).all():
        db.session.add(Alert(recipient_id=u.id, message=msg_k,
                             alert_type='urgent', order_id=order.id))

    # Alert dla monterów (tylko gdy jest szablon montera)
    if monter_template:
        msg_m = (f'Nowe zlecenie montażu: {order.product_name} '
                 f'({order.number}) – {order.quantity} szt., termin {due_str}')
        for u in User.query.filter_by(role='monter').all():
            db.session.add(Alert(recipient_id=u.id, message=msg_m,
                                 alert_type='info', order_id=order.id))

    order.status = 'in_control'
    order.template_id = template.id
    if monter_template:
        order.monter_template_id = monter_template.id


def _order_recipients(order, include_controllers=False):
    """Zwraca zbiór ID użytkowników do powiadamiania o zamówieniu."""
    ids = {order.created_by_id}
    roles = ['admin', 'order']
    if include_controllers:
        roles.append('kontroler')
    for u in User.query.filter(User.role.in_(roles)).all():
        ids.add(u.id)
    return ids


def _notify_order_report_done(report):
    """Powiadamia zamawiających i adminów o ukończeniu pojedynczej kontroli."""
    order = report.order
    done  = order.reports.filter_by(status='completed').count()
    total = order.reports.count()
    s     = report.stats
    ng_info = f' · NG: {s["ng"]}' if s['ng'] > 0 else ''
    msg = (f'Kontrola zakończona: {report.title}{ng_info} '
           f'— postęp zamówienia {order.number}: {done}/{total}')
    alert_type = 'success' if s['ng'] == 0 else 'urgent'
    for rid in _order_recipients(order):
        db.session.add(Alert(recipient_id=rid, message=msg,
                             alert_type=alert_type, order_id=order.id))


def _check_order_complete(order):
    """Jeśli wszystkie raporty zamówienia zamknięte — zmień status i powiadom wszystkich."""
    if order.reports_total == 0:
        return
    if order.reports_done < order.reports_total:
        return
    order.status = 'ready_to_ship'
    msg = (f'Zamówienie gotowe do wysyłki: {order.product_name} ({order.number}) '
           f'– {order.quantity} szt.')
    for rid in _order_recipients(order):
        db.session.add(Alert(recipient_id=rid, message=msg,
                             alert_type='success', order_id=order.id))
    app.logger.info('ORDER_COMPLETE order=%s', order.number)


# ── Zamówienia ────────────────────────────────────────────────────────────────

def _parse_order_date(s):
    try:
        return datetime.strptime(s, '%Y-%m-%d').date() if s else None
    except ValueError:
        return None


@app.route('/orders')
@login_required
@order_required
def orders_list():
    from sqlalchemy import asc, nullslast, func
    from sqlalchemy.orm import joinedload
    from datetime import date as _date

    status    = request.args.get('status', 'active')
    page      = request.args.get('page', 1, type=int)
    q_number  = request.args.get('q_number', '').strip()
    q_product = request.args.get('q_product', '').strip()
    q_client  = request.args.get('q_client', '').strip()

    q = Order.query
    if status == 'active':
        q = q.filter(Order.status.in_(['active', 'in_control']))
    elif status == 'ready':
        q = q.filter_by(status='ready_to_ship')
    elif status == 'shipped':
        q = q.filter_by(status='shipped')
    if q_number:
        q = q.filter(Order.number.ilike(f'%{q_number}%'))
    if q_product:
        q = q.filter(Order.product_name.ilike(f'%{q_product}%'))
    if q_client:
        q = q.filter(Order.client.ilike(f'%{q_client}%'))
    orders = q.order_by(nullslast(asc(Order.due_date)), Order.created_at.desc()).paginate(
        page=page, per_page=25, error_out=False)

    # Batch progress — zastępuje N+1 (reports_total/done/pct per order)
    order_ids = [o.id for o in orders.items]
    progress_map = {}
    if order_ids:
        for row in (db.session.query(
                Report.order_id,
                func.count(Report.id).label('total'),
                func.sum(func.cast(Report.status == 'completed', db.Integer)).label('done')
            ).filter(Report.order_id.in_(order_ids))
             .group_by(Report.order_id).all()):
            t, d = row.total, (row.done or 0)
            progress_map[row.order_id] = {
                'total': t, 'done': d,
                'pct': int(d / t * 100) if t else 0,
            }

    rq = (Report.query
          .filter(Report.order_id.isnot(None))
          .join(Order, Report.order_id == Order.id)
          .options(joinedload(Report.author), joinedload(Report.order)))
    if status == 'active':
        rq = rq.filter(Order.status.in_(['active', 'in_control']))
    elif status == 'ready':
        rq = rq.filter(Order.status == 'ready_to_ship')
    elif status == 'shipped':
        rq = rq.filter(Order.status == 'shipped')
    if q_number:
        rq = rq.filter(Order.number.ilike(f'%{q_number}%'))
    if q_product:
        rq = rq.filter(Order.product_name.ilike(f'%{q_product}%'))
    if q_client:
        rq = rq.filter(Order.client.ilike(f'%{q_client}%'))
    order_reports = rq.order_by(
        nullslast(asc(Order.due_date)), Report.created_at.asc()
    ).limit(100).all()

    # Batch stats dla order_reports — zastępuje N+1 (stats.ng + completion_percent)
    report_ng  = {}
    report_pct = {}
    r_ids = [r.id for r in order_reports]
    if r_ids:
        for row in (db.session.query(ReportItem.report_id,
                                     func.count(ReportItem.id).label('ng'))
                    .filter(ReportItem.report_id.in_(r_ids), ReportItem.result == 'ng')
                    .group_by(ReportItem.report_id).all()):
            report_ng[row.report_id] = row.ng
        for row in (db.session.query(
                ReportItem.report_id,
                func.count(ReportItem.id).label('total'),
                func.sum(func.cast(ReportItem.is_checked == True, db.Integer)).label('checked')
            ).filter(ReportItem.report_id.in_(r_ids))
             .group_by(ReportItem.report_id).all()):
            report_pct[row.report_id] = (
                int((row.checked or 0) / row.total * 100) if row.total else 0)

    # ── Statystyki dla mini-dash ─────────────────────────────────────────────
    today = _date.today()
    cnt_active     = Order.query.filter(Order.status.in_(['active', 'in_control'])).count()
    cnt_in_control = Order.query.filter_by(status='in_control').count()
    cnt_ready      = Order.query.filter_by(status='ready_to_ship').count()
    cnt_overdue    = Order.query.filter(
        Order.due_date < today,
        Order.status.in_(['active', 'in_control'])
    ).count()
    pct_in_control = round(cnt_in_control / cnt_active * 100) if cnt_active else 0

    return render_template('orders/list.html',
                           orders=orders, active_tab=status,
                           order_reports=order_reports,
                           progress_map=progress_map,
                           report_ng=report_ng,
                           report_pct=report_pct,
                           q_number=q_number, q_product=q_product, q_client=q_client,
                           cnt_active=cnt_active,
                           cnt_in_control=cnt_in_control,
                           cnt_ready=cnt_ready,
                           cnt_overdue=cnt_overdue,
                           pct_in_control=pct_in_control,
                           today=today)


@app.route('/orders/new', methods=['GET', 'POST'])
@login_required
@order_required
def orders_new():
    if request.method == 'POST':
        number  = request.form.get('number', '').strip()
        product = request.form.get('product_name', '').strip()
        client  = request.form.get('client', '').strip()
        qty     = request.form.get('quantity', 1, type=int)
        due     = _parse_order_date(request.form.get('due_date', ''))
        notes   = request.form.get('notes', '').strip() or None
        if not number or not product or not client:
            flash('Numer, nazwa produktu i klient są wymagane.', 'error')
        elif Order.query.filter_by(number=number).first():
            flash('Zamówienie o tym numerze już istnieje.', 'error')
        else:
            order = Order(number=number, product_name=product, client=client,
                          quantity=qty, due_date=due, notes=notes,
                          created_by_id=current_user.id)
            db.session.add(order)
            db.session.flush()

            # Optional PDF documentation uploaded at creation time
            pdf_file = request.files.get('pdf')
            if pdf_file and pdf_file.filename and allowed_pdf(pdf_file.filename):
                import uuid as _uuid
                ext = pdf_file.filename.rsplit('.', 1)[1].lower()
                pdf_filename = f'order_{order.id}_{_uuid.uuid4().hex[:8]}.{ext}'
                pdf_file.save(os.path.join(app.config['UPLOAD_FOLDER'], pdf_filename))
                order.pdf_filename = pdf_filename

            tpl = _find_matching_template(product, 'kontroler')
            monter_tpl = _find_matching_template(product, 'monter')
            if tpl:
                _create_order_reports(order, tpl, monter_tpl)
                monter_info = f' + {order.quantity} list montażowych ({monter_tpl.name})' if monter_tpl else ''
                flash(f'Zamówienie {number} dodane. Wygenerowano {qty} list kontrolnych '
                      f'(szablon: {tpl.name}){monter_info}.', 'success')
            else:
                flash(f'Zamówienie {number} dodane. Brak pasującego szablonu QA.', 'info')
            db.session.commit()
            _audit('order_create', 'order', order.id,
                   f'number={number} product={product} tpl={tpl.name if tpl else "none"}'
                   + (f' pdf={order.pdf_filename}' if order.pdf_filename else ''))
            return redirect(url_for('orders_detail', order_id=order.id))
    kontroler_tpls = ChecklistTemplate.query.filter_by(is_active=True, template_type='kontroler').order_by(ChecklistTemplate.name).all()
    monter_tpls    = ChecklistTemplate.query.filter_by(is_active=True, template_type='monter').order_by(ChecklistTemplate.name).all()
    return render_template('orders/new.html', kontroler_tpls=kontroler_tpls, monter_tpls=monter_tpls)


@app.route('/orders/import-csv', methods=['GET', 'POST'])
@login_required
@order_required
def orders_import_csv():
    import csv, io, uuid as _uuid

    def _parse_csv(file_bytes):
        """Parsuje CSV z separatorem ; i kodowaniem UTF-8-BOM lub cp1250."""
        for enc in ('utf-8-sig', 'cp1250', 'utf-8'):
            try:
                text = file_bytes.decode(enc)
                break
            except (UnicodeDecodeError, ValueError):
                continue
        else:
            return None, 'Nie udało się odczytać pliku – sprawdź kodowanie.'
        reader = csv.DictReader(io.StringIO(text), delimiter=';')
        rows = []
        for r in reader:
            rows.append({k.strip(): v.strip() for k, v in r.items()})
        return rows, None

    def _make_number(numer_wew, lp, existing_in_file):
        """Buduje unikalny numer zamówienia w systemie."""
        base = numer_wew.strip()
        if existing_in_file.get(base, 0) > 1:
            return f'{base}-{lp}'
        return base

    if request.method == 'POST':
        action = request.form.get('action', 'preview')

        # ── KROK 1: podgląd ──────────────────────────────────────────────────
        if action == 'preview':
            f = request.files.get('csv_file')
            if not f or not f.filename.lower().endswith('.csv'):
                flash('Wybierz plik CSV.', 'error')
                return render_template('orders/import_csv.html')

            raw = f.read()
            rows, err = _parse_csv(raw)
            if err:
                flash(err, 'error')
                return render_template('orders/import_csv.html')

            # Zlicz ile razy każdy Numer wew. występuje → unikalność numeru
            from collections import Counter
            num_counts = Counter(r.get('Numer wew.', '').strip() for r in rows)

            preview = []
            for r in rows:
                numer_wew = r.get('Numer wew.', '').strip()
                lp        = r.get('Lp', '1').strip()
                number    = _make_number(numer_wew, lp, num_counts)
                product   = r.get('Identyfikator', '').strip()
                client    = r.get('Kontrahent', '').strip()
                qty_raw   = r.get('Ilość', r.get('Ilość', '1')).strip().replace(',', '.')
                due_raw   = r.get('Termin dostawy', '').strip()
                notes_val = r.get('Numer zew.', '').strip() or None

                errors = []
                if not number:
                    errors.append('brak numeru')
                if not product:
                    errors.append('brak produktu')
                if not client:
                    errors.append('brak klienta')
                try:
                    qty = int(float(qty_raw)) if qty_raw else 1
                    if qty < 1:
                        qty = 1
                except ValueError:
                    qty = 1
                    errors.append(f'nieprawidłowa ilość ({qty_raw!r})')

                due = None
                if due_raw:
                    for fmt in ('%Y-%m-%d', '%d.%m.%Y', '%d-%m-%Y'):
                        try:
                            from datetime import date as _date
                            due = datetime.strptime(due_raw, fmt).date()
                            break
                        except ValueError:
                            pass

                duplicate = bool(Order.query.filter_by(number=number).first())
                tpl       = _find_matching_template(product, 'kontroler')
                monter_t  = _find_matching_template(product, 'monter')

                preview.append({
                    'number':   number,
                    'product':  product,
                    'client':   client,
                    'quantity': qty,
                    'due':      due.strftime('%d.%m.%Y') if due else '',
                    'due_iso':  due.strftime('%Y-%m-%d') if due else '',
                    'notes':    notes_val or '',
                    'errors':   errors,
                    'duplicate': duplicate,
                    'tpl':      tpl.name if tpl else '',
                    'monter_tpl': monter_t.name if monter_t else '',
                    'status':   'error' if errors else ('duplicate' if duplicate else 'ok'),
                })

            # Zapisz CSV jako plik tymczasowy — sesja cookie ma limit ~4KB
            tmp_name = f'_csv_tmp_{_uuid.uuid4().hex}.csv'
            tmp_path = os.path.join(app.config['UPLOAD_FOLDER'], tmp_name)
            with open(tmp_path, 'wb') as fh:
                fh.write(raw)
            return render_template('orders/import_csv.html', preview=preview, csv_tmp=tmp_name)

        # ── KROK 2: import ───────────────────────────────────────────────────
        elif action == 'import':
            tmp_name = request.form.get('csv_tmp', '').strip()
            # Zabezpieczenie przed path traversal
            if not tmp_name or '/' in tmp_name or '\\' in tmp_name or not tmp_name.startswith('_csv_tmp_'):
                flash('Nieprawidłowy plik tymczasowy – wgraj CSV ponownie.', 'error')
                return redirect(url_for('orders_import_csv'))

            tmp_path = os.path.join(app.config['UPLOAD_FOLDER'], tmp_name)
            if not os.path.exists(tmp_path):
                flash('Plik tymczasowy wygasł lub nie istnieje – wgraj CSV ponownie.', 'error')
                return redirect(url_for('orders_import_csv'))

            with open(tmp_path, 'rb') as fh:
                raw = fh.read()
            os.remove(tmp_path)

            rows, err = _parse_csv(raw)
            if err:
                flash(err, 'error')
                return redirect(url_for('orders_import_csv'))

            selected = set(request.form.getlist('selected_numbers'))
            if not selected:
                flash('Nie zaznaczono żadnych wierszy.', 'error')
                return redirect(url_for('orders_import_csv'))

            # Opcjonalny PDF wspólny dla całego importu
            pdf_filename_shared = None
            pdf_file = request.files.get('pdf')
            if pdf_file and pdf_file.filename and allowed_pdf(pdf_file.filename):
                ext = pdf_file.filename.rsplit('.', 1)[1].lower()
                pdf_filename_shared = f'import_{_uuid.uuid4().hex[:10]}.{ext}'
                pdf_file.save(os.path.join(app.config['UPLOAD_FOLDER'], pdf_filename_shared))

            from collections import Counter
            num_counts = Counter(r.get('Numer wew.', '').strip() for r in rows)

            created = skipped = errors_count = 0
            for r in rows:
                numer_wew = r.get('Numer wew.', '').strip()
                lp        = r.get('Lp', '1').strip()
                number    = _make_number(numer_wew, lp, num_counts)

                if number not in selected:
                    continue

                product  = r.get('Identyfikator', '').strip()
                client   = r.get('Kontrahent', '').strip()
                qty_raw  = r.get('Ilość', r.get('Ilość', '1')).strip().replace(',', '.')
                due_raw  = r.get('Termin dostawy', '').strip()
                notes_v  = r.get('Numer zew.', '').strip() or None

                if not number or not product or not client:
                    errors_count += 1
                    continue
                if Order.query.filter_by(number=number).first():
                    skipped += 1
                    continue

                try:
                    qty = max(1, int(float(qty_raw))) if qty_raw else 1
                except ValueError:
                    qty = 1

                due = None
                if due_raw:
                    for fmt in ('%Y-%m-%d', '%d.%m.%Y', '%d-%m-%Y'):
                        try:
                            due = datetime.strptime(due_raw, fmt).date()
                            break
                        except ValueError:
                            pass

                order = Order(number=number, product_name=product, client=client,
                              quantity=qty, due_date=due, notes=notes_v,
                              created_by_id=current_user.id,
                              pdf_filename=pdf_filename_shared)
                db.session.add(order)
                db.session.flush()

                tpl       = _find_matching_template(product, 'kontroler')
                monter_t  = _find_matching_template(product, 'monter')
                if tpl:
                    _create_order_reports(order, tpl, monter_t)

                _audit('order_create', 'order', order.id,
                       f'csv_import number={number} product={product} tpl={tpl.name if tpl else "none"}')
                created += 1

            db.session.commit()
            parts = [f'Utworzono: {created}']
            if skipped:
                parts.append(f'pominięto duplikaty: {skipped}')
            if errors_count:
                parts.append(f'błędy: {errors_count}')
            flash('. '.join(parts) + '.', 'success' if created else 'warning')
            return redirect(url_for('orders_list'))

    return render_template('orders/import_csv.html')


@app.route('/orders/<int:order_id>/edit', methods=['GET', 'POST'])
@login_required
@order_required
def orders_edit(order_id):
    order = get_or_404(Order, order_id)
    if not current_user.is_admin and order.created_by_id != current_user.id:
        abort(403)
    if request.method == 'POST':
        number  = request.form.get('number', '').strip()
        product = request.form.get('product_name', '').strip()
        client  = request.form.get('client', '').strip()
        qty     = request.form.get('quantity', 1, type=int)
        due     = _parse_order_date(request.form.get('due_date', ''))
        notes   = request.form.get('notes', '').strip() or None
        if not number or not product or not client:
            flash('Numer, nazwa produktu i klient są wymagane.', 'error')
        elif Order.query.filter(Order.number == number, Order.id != order_id).first():
            flash('Zamówienie o tym numerze już istnieje.', 'error')
        else:
            order.number      = number
            order.product_name = product
            order.client      = client
            order.quantity    = qty
            order.due_date    = due
            order.notes       = notes
            db.session.commit()
            _audit('order_edit', 'order', order.id, f'number={number}')
            flash(f'Zamówienie {number} zostało zaktualizowane.', 'success')
            return redirect(url_for('orders_detail', order_id=order.id))
    return render_template('orders/edit.html', order=order)


@app.route('/orders/<int:order_id>')
@login_required
def orders_detail(order_id):
    order = Order.query.filter_by(id=order_id).first()
    if not order:
        flash('Zamówienie nie istnieje lub zostało usunięte.', 'warning')
        return redirect(url_for('dashboard'))
    reports = order.reports.order_by(Report.id).all()
    kontroler_reports = [r for r in reports if r.report_type == 'kontroler']
    monter_reports    = [r for r in reports if r.report_type == 'monter']
    return render_template('orders/detail.html', order=order, reports=reports,
                           kontroler_reports=kontroler_reports, monter_reports=monter_reports)


@app.route('/orders/bulk-delete', methods=['POST'])
@login_required
@order_required
def orders_bulk_delete():
    action = request.form.get('action')
    if action == 'selected':
        ids = request.form.getlist('order_ids', type=int)
        if not ids:
            flash('Nie zaznaczono żadnych zamówień.', 'warning')
            return redirect(url_for('orders_list'))
        orders_q = Order.query.filter(Order.id.in_(ids)).all()
    elif action == 'all':
        orders_q = Order.query.all() if current_user.is_admin else \
                   Order.query.filter_by(created_by_id=current_user.id).all()
        if not orders_q:
            flash('Brak zamówień do usunięcia.', 'info')
            return redirect(url_for('orders_list'))
    else:
        abort(400)
    count = 0
    for order in orders_q:
        if not current_user.is_admin and order.created_by_id != current_user.id:
            continue
        for report in order.reports.all():
            _delete_reports([report])
        Alert.query.filter_by(order_id=order.id).delete()
        _audit('order_delete', 'order', order.id, f'bulk number={order.number}')
        db.session.delete(order)
        count += 1
    db.session.commit()
    flash(f'Usunięto {count} {"zamówienie" if count == 1 else "zamówienia" if 2 <= count <= 4 else "zamówień"} wraz z listami kontrolnymi.', 'success')
    return redirect(url_for('orders_list'))


@app.route('/orders/<int:order_id>/delete', methods=['POST'])
@login_required
@order_required
def orders_delete(order_id):
    order = Order.query.filter_by(id=order_id).first()
    if not order:
        flash('Zamówienie nie istnieje.', 'warning')
        return redirect(url_for('orders_list'))
    if not current_user.is_admin and order.created_by_id != current_user.id:
        abort(403)

    number = order.number
    for report in order.reports.all():
        _delete_reports([report])
    Alert.query.filter_by(order_id=order.id).delete()
    db.session.delete(order)
    db.session.commit()
    _audit('order_delete', 'order', order_id, f'number={number}')
    flash(f'Zamówienie {number} oraz powiązane listy kontrolne zostały usunięte.', 'success')
    return redirect(url_for('orders_list'))


@app.route('/orders/<int:order_id>/ship', methods=['POST'])
@login_required
@order_required
def orders_ship(order_id):
    order = get_or_404(Order, order_id)
    order.status = 'shipped'
    db.session.commit()
    _audit('order_ship', 'order', order.id)
    flash('Zamówienie oznaczone jako wysłane.', 'success')
    return redirect(url_for('orders_detail', order_id=order_id))


@app.route('/orders/<int:order_id>/regenerate', methods=['POST'])
@login_required
@order_required
def orders_regenerate(order_id):
    """Usuwa istniejące raporty (bez wypełnionych odpowiedzi) i tworzy nowe na podstawie szablonu."""
    order = get_or_404(Order, order_id)
    if not current_user.is_admin and order.created_by_id != current_user.id:
        abort(403)

    if order.status == 'shipped':
        flash('Nie można regenerować raportów dla wysłanego zamówienia.', 'error')
        return redirect(url_for('orders_detail', order_id=order_id))

    for report in order.reports.all():
        if report.items.filter(ReportItem.result.isnot(None)).count() > 0:
            flash('Nie można zregenerować — część list kontrolnych zawiera już wypełnione odpowiedzi.', 'error')
            return redirect(url_for('orders_detail', order_id=order_id))

    tpl = _find_matching_template(order.product_name, 'kontroler')
    if not tpl:
        flash('Nie znaleziono pasującego szablonu QA dla tej nazwy produktu.', 'error')
        return redirect(url_for('orders_detail', order_id=order_id))
    monter_tpl = _find_matching_template(order.product_name, 'monter')

    for report in order.reports.all():
        _delete_reports([report])
    order.status = 'active'
    order.template_id = None
    order.monter_template_id = None
    db.session.flush()

    _create_order_reports(order, tpl, monter_tpl)
    db.session.commit()
    _audit('order_regenerate', 'order', order.id, f'template={tpl.name}')
    flash(f'Raporty QA zregenerowane na podstawie szablonu „{tpl.name}".', 'success')
    return redirect(url_for('orders_detail', order_id=order_id))


@app.route('/orders/<int:order_id>/pdf', methods=['POST'])
@login_required
@order_required
def orders_upload_pdf(order_id):
    order = get_or_404(Order, order_id)
    if not current_user.is_admin and order.created_by_id != current_user.id:
        abort(403)
    f = request.files.get('pdf')
    if not f or not f.filename:
        flash('Nie wybrano pliku.', 'error')
        return redirect(url_for('orders_detail', order_id=order_id))
    if not allowed_pdf(f.filename):
        flash('Dozwolony tylko plik PDF.', 'error')
        return redirect(url_for('orders_detail', order_id=order_id))
    import uuid as _uuid
    ext = f.filename.rsplit('.', 1)[1].lower()
    filename = f'order_{order.id}_{_uuid.uuid4().hex[:8]}.{ext}'
    f.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    if order.pdf_filename:
        old_path = os.path.join(app.config['UPLOAD_FOLDER'], order.pdf_filename)
        if os.path.exists(old_path):
            os.remove(old_path)
    order.pdf_filename = filename
    db.session.commit()
    _audit('order_pdf_upload', 'order', order.id, f'file={filename}')
    flash('PDF dokumentacji technicznej został przesłany.', 'success')
    return redirect(url_for('orders_detail', order_id=order_id))


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
        'items': [{'id': a.id, 'message': a.message, 'type': a.alert_type,
                   'order_id': a.order_id} for a in latest],
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


@app.errorhandler(500)
def internal_error(e):
    app.logger.error('500 error: %s | path=%s | ip=%s', e, request.path, request.remote_addr,
                     exc_info=True)
    db.session.rollback()
    return render_template('errors/500.html'), 500


# ── Init DB ───────────────────────────────────────────────────────────────────

def init_db():
    os.makedirs('instance', exist_ok=True)
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['QAR_UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['MARSZRUTA_UPLOAD_FOLDER'], exist_ok=True)
    with app.app_context():
        _migrate_schema()
        db.create_all()
        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', email='admin@psh.pl', role='admin')
            admin.set_password('admin123')
            db.session.add(admin)
        if not ChecklistTemplate.query.first():
            _seed_demo_data()
        _seed_kosztorys()
        _seed_zadania_qa()
        _seed_marszruta()
        db.session.commit()


def _migrate_schema():
    """Add new columns to existing SQLite DB without dropping data."""
    from sqlalchemy import inspect, text
    engine = db.engine
    insp = inspect(engine)
    with engine.connect() as conn:
        if 'categories' in insp.get_table_names():
            cols = [c['name'] for c in insp.get_columns('categories')]
            if 'template_id' not in cols:
                conn.execute(text('ALTER TABLE categories ADD COLUMN template_id INTEGER'))
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
            if 'order_id' not in cols:
                conn.execute(text('ALTER TABLE reports ADD COLUMN order_id INTEGER REFERENCES orders(id)'))
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
        if 'checklist_templates' in insp.get_table_names():
            cols = [c['name'] for c in insp.get_columns('checklist_templates')]
            if 'template_type' not in cols:
                conn.execute(text("ALTER TABLE checklist_templates ADD COLUMN template_type VARCHAR(16) DEFAULT 'kontroler'"))
                conn.commit()
        if 'orders' in insp.get_table_names():
            cols = [c['name'] for c in insp.get_columns('orders')]
            for col, ddl in [
                ('monter_template_id', 'ALTER TABLE orders ADD COLUMN monter_template_id INTEGER REFERENCES checklist_templates(id)'),
                ('pdf_filename',       'ALTER TABLE orders ADD COLUMN pdf_filename VARCHAR(256)'),
                ('external_number',    'ALTER TABLE orders ADD COLUMN external_number VARCHAR(64)'),
            ]:
                if col not in cols:
                    conn.execute(text(ddl)); conn.commit()
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
        if 'spawalnia_records' in insp.get_table_names():
            cols = [c['name'] for c in insp.get_columns('spawalnia_records')]
            for col, ddl in [
                ('batch_id',           'ALTER TABLE spawalnia_records ADD COLUMN batch_id VARCHAR(32)'),
                ('batch_index',        'ALTER TABLE spawalnia_records ADD COLUMN batch_index INTEGER'),
                ('batch_total',        'ALTER TABLE spawalnia_records ADD COLUMN batch_total INTEGER'),
                ('przekatna_odchylka', 'ALTER TABLE spawalnia_records ADD COLUMN przekatna_odchylka FLOAT'),
                ('giecie_operator_id', 'ALTER TABLE spawalnia_records ADD COLUMN giecie_operator_id INTEGER REFERENCES giecie_operators(id)'),
                ('ciecie_operator_id', 'ALTER TABLE spawalnia_records ADD COLUMN ciecie_operator_id INTEGER REFERENCES ciecie_operators(id)'),
            ]:
                if col not in cols:
                    conn.execute(text(ddl)); conn.commit()
        if 'qar_reports' in insp.get_table_names():
            cols = [c['name'] for c in insp.get_columns('qar_reports')]
            for col, ddl in [
                ('zo_number',      'ALTER TABLE qar_reports ADD COLUMN zo_number VARCHAR(64)'),
                ('drawing_number', 'ALTER TABLE qar_reports ADD COLUMN drawing_number VARCHAR(64)'),
                ('employee_id',    'ALTER TABLE qar_reports ADD COLUMN employee_id INTEGER REFERENCES department_employees(id)'),
            ]:
                if col not in cols:
                    conn.execute(text(ddl)); conn.commit()
        # Ocena etapu marszruty zredukowana do PLUS/MINUS — DW (dopuszczone warunkowo) usunięte.
        if 'routing_card_stages' in insp.get_table_names():
            conn.execute(text("UPDATE routing_card_stages SET result='ng' WHERE result='dw'"))
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
        })
    return {
        'id': report.id, 'title': report.title, 'status': report.status,
        'author': report.author.username,
        'created_at': report.created_at.isoformat(),
        'started_at': report.started_at.isoformat() if report.started_at else None,
        'completed_at': report.completed_at.isoformat() if report.completed_at else None,
        'duration_seconds': report.duration_seconds,
        'duration_str': report.duration_str,
        'order': {
            'id': report.order.id, 'number': report.order.number,
            'product_name': report.order.product_name, 'client': report.order.client,
        } if report.order else None,
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
    order_number = request.args.get('order_number', '').strip()
    date_from    = request.args.get('date_from', '')
    date_to      = request.args.get('date_to', '')
    page         = request.args.get('page', 1, type=int)

    q = Report.query
    if status in ('in_progress', 'completed'):
        q = q.filter_by(status=status)
    if template_id:
        q = q.filter_by(template_id=template_id)
    if order_number:
        order = Order.query.filter(Order.number.ilike(f'%{order_number}%')).first()
        if order:
            q = q.filter_by(order_id=order.id)
        else:
            return jsonify({'items': [], 'page': 1, 'pages': 0, 'total': 0})
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
            'order_number': r.order.number if r.order else None,
            'author': r.author.username,
            'created_at': r.created_at.isoformat(),
            'completed_at': r.completed_at.isoformat() if r.completed_at else None,
            'completion_percent': r.completion_percent,
            'stats': r.stats,
        } for r in pag.items],
        'page': pag.page, 'pages': pag.pages, 'total': pag.total,
    })


@app.route('/api/v1/checklists', methods=['POST'])
@api_key_required
def api_v1_create_checklist():
    data        = request.get_json(silent=True) or {}
    template_id = data.get('template_id')
    if not template_id:
        return jsonify({'error': 'template_id jest wymagane'}), 400

    tmpl = db.session.get(ChecklistTemplate, template_id)
    if not tmpl or not tmpl.is_active:
        return jsonify({'error': 'Szablon nie istnieje lub jest nieaktywny'}), 404

    title = (data.get('title', '').strip()
             or f'{tmpl.name} – {datetime.now().strftime("%d.%m.%Y %H:%M")}')

    order_id = None
    order_number = data.get('order_number', '').strip()
    if order_number:
        order = Order.query.filter_by(number=order_number).first()
        if not order:
            return jsonify({'error': f'Zamówienie {order_number!r} nie istnieje'}), 404
        order_id = order.id

    author = User.query.filter_by(role='admin').first()
    if not author:
        return jsonify({'error': 'Brak użytkownika systemowego'}), 500

    report = Report(user_id=author.id, template_id=template_id,
                    title=title, order_id=order_id)
    db.session.add(report)
    db.session.flush()
    for cat in tmpl.categories.filter_by(is_active=True).order_by(Category.order):
        for task in cat.tasks.filter_by(is_active=True).order_by(Task.order):
            db.session.add(ReportItem(report_id=report.id, task_id=task.id))
    db.session.commit()
    _audit('api_create_checklist', 'report', report.id, title)
    return jsonify({
        'ok': True, 'id': report.id, 'title': report.title,
        'template': tmpl.name,
        'order_number': order_number or None,
        'created_at': report.created_at.isoformat(),
    }), 201


@app.route('/api/v1/checklists/<int:report_id>', methods=['GET'])
@api_key_required
def api_v1_checklist_detail(report_id):
    report = get_or_404(Report, report_id)
    return jsonify(_api_checklist_dict(report))


def _api_order_dict(o):
    return {
        'id': o.id, 'number': o.number, 'external_number': o.external_number,
        'product_name': o.product_name,
        'client': o.client, 'quantity': o.quantity,
        'due_date': o.due_date.isoformat() if o.due_date else None,
        'status': o.status,
        'template': o.template.name if o.template else None,
        'monter_template': o.monter_template.name if o.monter_template else None,
        'reports_total': o.reports_total, 'reports_done': o.reports_done,
        'has_ng': o.has_ng,
        'created_at': o.created_at.isoformat(),
    }


@app.route('/api/v1/orders', methods=['GET'])
@api_key_required
def api_v1_orders():
    external_number = request.args.get('external_number', '').strip()
    q = Order.query
    if external_number:
        q = q.filter_by(external_number=external_number)
    else:
        q = q.filter(Order.status != 'shipped')
    orders = q.order_by(Order.created_at.desc()).all()
    return jsonify([_api_order_dict(o) for o in orders])


@app.route('/api/v1/orders/<int:order_id>', methods=['GET'])
@api_key_required
def api_v1_order_detail(order_id):
    order = get_or_404(Order, order_id)
    return jsonify(_api_order_dict(order))


@app.route('/api/v1/orders', methods=['POST'])
@api_key_required
def api_v1_create_order():
    data    = request.get_json(silent=True) or {}
    number  = (data.get('number') or '').strip()
    product = (data.get('product_name') or '').strip()
    client  = (data.get('client') or '').strip()
    if not number or not product or not client:
        return jsonify({'error': 'number, product_name i client są wymagane'}), 400
    if Order.query.filter_by(number=number).first():
        return jsonify({'error': f'Zamówienie {number!r} już istnieje'}), 409

    try:
        quantity = max(1, int(data.get('quantity', 1)))
    except (TypeError, ValueError):
        quantity = 1
    due = _parse_order_date((data.get('due_date') or '').strip())
    external_number = (data.get('external_number') or '').strip() or None

    admin_user = User.query.filter_by(role='admin').first()
    if not admin_user:
        return jsonify({'error': 'Brak użytkownika systemowego (admin)'}), 500

    order = Order(number=number, product_name=product, client=client,
                  quantity=quantity, due_date=due, external_number=external_number,
                  created_by_id=admin_user.id)
    db.session.add(order)
    db.session.flush()

    tpl        = _find_matching_template(product, 'kontroler')
    monter_tpl = _find_matching_template(product, 'monter')
    if tpl:
        _create_order_reports(order, tpl, monter_tpl, user_id=admin_user.id)
    db.session.commit()
    _audit('api_order_create', 'order', order.id,
           f'number={number} external={external_number} tpl={tpl.name if tpl else "none"}')
    return jsonify({
        'ok': True, 'id': order.id, 'number': order.number,
        'template_matched': tpl.name if tpl else None,
        'monter_template_matched': monter_tpl.name if (tpl and monter_tpl) else None,
    }), 201


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
    if report.order_id:
        _notify_order_report_done(report)
        _check_order_complete(report.order)
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


def _api_spawalnia_record_dict(r):
    return {
        'id': r.id, 'batch_index': r.batch_index, 'batch_total': r.batch_total,
        'is_empty': r.is_empty, 'has_ng': r.has_ng,
        'otworowanie': r.otworowanie, 'przekatna': r.przekatna,
        'przekatna_odchylka': r.przekatna_odchylka,
        'pomiar1': r.pomiar1, 'pomiar2': r.pomiar2, 'pomiar3': r.pomiar3,
        'jakosc_wyciecia': r.jakosc_wyciecia,
        'operator': r.operator.initials if r.operator else None,
        'giecie_operator': r.giecie_operator.initials if r.giecie_operator else None,
        'ciecie_operator': r.ciecie_operator.initials if r.ciecie_operator else None,
        'created_at': r.created_at.isoformat(),
        'updated_at': r.updated_at.isoformat(),
    }


@app.route('/api/v1/spawalnia/<path:zo_number>', methods=['GET'])
@api_key_required
def api_v1_spawalnia_get(zo_number):
    zo_number = zo_number.strip()
    records = SpawalniaRecord.query.filter_by(zo_number=zo_number).all()
    records.sort(key=lambda r: (r.batch_index if r.batch_index is not None else 9999, r.created_at))
    return jsonify({
        'zo_number': zo_number,
        'records': [_api_spawalnia_record_dict(r) for r in records],
    })


@app.route('/api/v1/spawalnia/<path:zo_number>', methods=['POST'])
@api_key_required
def api_v1_spawalnia_create(zo_number):
    zo_number = zo_number.strip()
    if not zo_number:
        return jsonify({'error': 'Numer ZO jest wymagany'}), 400
    data = request.get_json(silent=True) or {}
    try:
        quantity = max(1, min(99, int(data.get('quantity', 1))))
    except (TypeError, ValueError):
        quantity = 1
    admin_user = User.query.filter_by(role='admin').first()
    if not admin_user:
        return jsonify({'error': 'Brak użytkownika systemowego (admin)'}), 500

    bid = uuid.uuid4().hex if quantity > 1 else None
    created_ids = []
    for i in range(1, quantity + 1):
        rec = SpawalniaRecord(
            zo_number=zo_number, batch_id=bid,
            batch_index=i if quantity > 1 else None,
            batch_total=quantity if quantity > 1 else None,
            created_by_id=admin_user.id,
        )
        db.session.add(rec)
        db.session.flush()
        created_ids.append(rec.id)
    db.session.commit()
    _audit('api_spawalnia_create', 'SpawalniaRecord', created_ids[0], f'ZO={zo_number} qty={quantity}')
    return jsonify({'ok': True, 'zo_number': zo_number, 'created_ids': created_ids}), 201


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
    admin_user = User.query.filter_by(role='admin').first()
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


def _seed_zadania_qa():
    """Inicjuje domyślną listę zadań karty kontroli jakości (na podstawie
    papierowej karty wykonania zadań – produkcja rozdzielnic)."""
    if QATask.query.first():
        return
    default_tasks = [
        ('Kontrola przekątnej PSH/PS',
         'Sprawdzenie wymiarów i przekątnych konstrukcji/obudowy, weryfikacja zgodności '
         'z dokumentacją techniczną, zapis wyników oraz zgłoszenie ewentualnych odchyłek.'),
        ('Zebranie dotyczące kontroli jakości prowadzone przez Marka',
         'Omówienie błędów na podstawie statystyk i raportu QAR, analiza niezgodności, '
         'wyników kontroli, reklamacji, priorytetów na zmianę oraz działań korygujących.'),
        ('Kontrola przekątnej - hala A',
         'Kontrola wymiarów i przekątnych obudów PSH/PS, sprawdzenie zgodności '
         'z dokumentacją oraz zapis wyników kontroli.'),
        ('Kontrola jakości obudów typowych',
         'Sprawdzenie zgodności wykonania obudów standardowych z dokumentacją, kontrola '
         'wymiarów, kompletności, estetyki wykonania, oznaczeń oraz ewentualnych uszkodzeń.'),
        ('Kontrola obudów z aplikacją',
         'Weryfikacja wykonania obudów zgodnie z dokumentacją, wymaganiami klienta '
         'i zapisami w aplikacji kontrolnej.'),
        ('Wyjaśnianie reklamacji',
         'Analiza zgłoszeń reklamacyjnych, sprawdzanie przyczyn niezgodności, kontakt '
         'z produkcją oraz przygotowanie informacji zwrotnej.'),
        ('Zebrania dotyczące kontroli jakości',
         'Omówienie bieżących problemów jakościowych, ustalenie działań korygujących '
         'i przekazanie informacji do odpowiednich działów.'),
        ('Przygotowywanie raportów niedoskonałości',
         'Wpisywanie wykrytych niezgodności, dokumentowanie zdjęć/opisów, klasyfikacja '
         'problemów oraz przekazanie raportów do dalszej analizy.'),
        ('Aktualizacje systemowe',
         'Wprowadzanie i aktualizacja danych w systemach jakościowych, uzupełnianie '
         'statusów kontroli, raportów, reklamacji oraz zapisów dotyczących niezgodności.'),
        ('Podsumowanie zmiany',
         'Zamknięcie dokumentacji, przekazanie informacji przełożonemu lub kolejnej '
         'zmianie oraz uporządkowanie tematów otwartych.'),
    ]
    for i, (title, description) in enumerate(default_tasks):
        db.session.add(QATask(title=title, description=description, order=i))


def _seed_marszruta():
    """Inicjuje domyślną listę działów produkcyjnych dla modułu Marszruta produkcji."""
    if ProductionDepartment.query.first():
        return
    default_departments = [
        'Cięcie', 'Laser', 'Gięcie', 'Zgrzewanie', 'Spawanie',
        'Czyszczenie', 'Mycie', 'Malowanie', 'Składanie', 'Kontrola Jakości',
    ]
    for i, name in enumerate(default_departments):
        db.session.add(ProductionDepartment(name=name, order=i))


if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
