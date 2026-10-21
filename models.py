import secrets
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
from flask import abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


def get_or_404(model, ident, description=None):
    """Session.get()-based replacement for the legacy Query.get_or_404()."""
    obj = db.session.get(model, ident)
    if obj is None:
        abort(404, description=description)
    return obj


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(16), nullable=False, default='kontroler')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    # Własna kolejność pozycji menu (JSON z listą kluczy), puste: kolejność domyślna.
    menu_order = db.Column(db.Text, nullable=True)
    reports = db.relationship('Report', backref='author', lazy='dynamic',
                             foreign_keys='[Report.user_id]')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self): return self.role == 'admin'
    @property
    def is_kontroler(self): return self.role in ('admin', 'kontroler')
    @property
    def is_planning(self): return self.role in ('admin', 'planowanie')
    @property
    def is_production_station(self): return self.role in ('admin', 'planowanie', 'produkcja')
    @property
    def is_konstruktor(self): return self.role == 'konstruktor'
    @property
    def is_operator(self): return self.role == 'operator'
    @property
    def can_view_control(self): return self.role in ('admin', 'kontroler', 'operator')
    @property
    def can_enter_stock(self): return self.role in ('admin', 'kontroler', 'operator', 'produkcja')
    @property
    def role_label(self):
        return {'admin': 'Administrator',
                'kontroler': 'Kontroler',
                'konstruktor': 'Konstruktor',
                'planowanie': 'Planowanie', 'produkcja': 'Stanowisko produkcji',
                'operator': 'Operator'}.get(self.role, self.role)


class ChecklistTemplate(db.Model):
    __tablename__ = 'checklist_templates'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    template_type = db.Column(db.String(16), default='kontroler')  # tylko kontroler (typ monter usunięty)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    categories = db.relationship('Category', backref='template', lazy='dynamic',
                                 cascade='all, delete-orphan', order_by='Category.order')

    @property
    def task_count(self):
        return sum(c.tasks.filter_by(is_active=True).count()
                   for c in self.categories.filter_by(is_active=True))

    def __repr__(self):
        return f'<ChecklistTemplate {self.name}>'


class Category(db.Model):
    __tablename__ = 'categories'
    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey('checklist_templates.id'), nullable=False)
    name = db.Column(db.String(128), nullable=False)
    order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    # Domyślna kategoria QAR podpowiadana przy szybkim zgłoszeniu niezgodności
    # z punktu kontrolnego (nazwa z QARCategory; puste = użytkownik wybiera sam).
    qar_category = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    tasks = db.relationship('Task', backref='category', lazy='dynamic',
                            cascade='all, delete-orphan', order_by='Task.order')

    def __repr__(self):
        return f'<Category {self.name}>'


class Task(db.Model):
    __tablename__ = 'tasks'
    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=False)
    title = db.Column(db.String(256), nullable=False)
    description = db.Column(db.Text, nullable=True)
    order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    requires_photo = db.Column(db.Boolean, default=False)
    task_type = db.Column(db.String(16), default='ok_ng')  # ok_ng | numeric | text
    value_min = db.Column(db.Float, nullable=True)
    value_max = db.Column(db.Float, nullable=True)
    unit = db.Column(db.String(32), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    def __repr__(self):
        return f'<Task {self.title}>'


class Report(db.Model):
    __tablename__ = 'reports'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    template_id = db.Column(db.Integer, db.ForeignKey('checklist_templates.id'), nullable=True)
    title = db.Column(db.String(256), nullable=False)
    status = db.Column(db.String(32), default='in_progress')
    report_type = db.Column(db.String(16), default='kontroler')  # tylko kontroler (typ monter usunięty)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    duration_seconds = db.Column(db.Integer, nullable=True)
    batch_id    = db.Column(db.String(32), nullable=True)
    batch_index = db.Column(db.Integer, nullable=True)
    batch_total = db.Column(db.Integer, nullable=True)
    notes        = db.Column(db.Text, nullable=True)
    # Klucz kodu QR (produkt|klient|ZO), po którym skan tego samego kodu odnajduje listę.
    qr_key       = db.Column(db.String(256), nullable=True, index=True)
    locked_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    locked_at    = db.Column(db.DateTime, nullable=True)
    items = db.relationship('ReportItem', backref='report', lazy='dynamic',
                            cascade='all, delete-orphan')
    sessions  = db.relationship('ChecklistSession', back_populates='report',
                                lazy='dynamic', cascade='all, delete-orphan')
    template  = db.relationship('ChecklistTemplate')
    locked_by = db.relationship('User', foreign_keys=[locked_by_id])

    @property
    def zo(self):
        """Numer ZO listy: trzeci człon klucza QR (produkt|klient|ZO) albo pusty napis."""
        return ((self.qr_key or '').split('|') + ['', '', ''])[2]

    @property
    def completion_percent(self):
        total = self.items.count()
        if total == 0:
            return 0
        done = self.items.filter_by(is_checked=True).count()
        return int((done / total) * 100)

    @property
    def stats(self):
        items = self.items.all()
        ok  = sum(1 for i in items if i.result == 'ok')
        ng  = sum(1 for i in items if i.result == 'ng')
        na  = sum(1 for i in items if i.result == 'na')
        dw  = sum(1 for i in items if i.result == 'dw')
        return {'total': len(items), 'ok': ok, 'ng': ng, 'na': na,
                'dw': dw, 'done': ok + ng + na + dw}

    @property
    def score(self):
        s = self.stats
        scored = s['ok'] + s['ng']
        if scored == 0:
            return None
        pct = round(s['ok'] / scored * 100)
        if pct == 100:  grade = 6
        elif pct >= 86: grade = 5
        elif pct >= 71: grade = 4
        elif pct >= 51: grade = 3
        elif pct >= 31: grade = 2
        else:           grade = 1
        return {'pct': pct, 'grade': grade, 'ok': s['ok'], 'scored': scored, 'na': s['na']}

    @property
    def lock_active(self):
        if not self.locked_by_id or not self.locked_at:
            return False
        la = self.locked_at if self.locked_at.tzinfo else self.locked_at.replace(tzinfo=UTC)
        return (datetime.now(UTC) - la) < timedelta(minutes=30)

    @property
    def lock_expires_at(self):
        if not self.locked_at:
            return None
        la = self.locked_at if self.locked_at.tzinfo else self.locked_at.replace(tzinfo=UTC)
        return la + timedelta(minutes=30)

    @property
    def duration_str(self):
        s = self.duration_seconds
        if s is None:
            return None
        h, rem = divmod(int(s), 3600)
        m, sec = divmod(rem, 60)
        if h:
            return f'{h} h {m:02d} min {sec:02d} sek'
        if m:
            return f'{m} min {sec:02d} sek'
        return f'{sec} sek'

    def __repr__(self):
        return f'<Report {self.id} {self.title}>'


class ReportItem(db.Model):
    __tablename__ = 'report_items'
    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('reports.id'), nullable=False)
    task_id = db.Column(db.Integer, db.ForeignKey('tasks.id'), nullable=False)
    is_checked = db.Column(db.Boolean, default=False)
    result     = db.Column(db.String(4), nullable=True)   # 'ok' | 'ng' | None
    checked_at = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    value_text = db.Column(db.String(256), nullable=True)  # measured/entered value
    # Powiązanie z raportem QAR zgłoszonym z tego punktu kontrolnego (opcjonalne).
    qar_report_id = db.Column(db.Integer, db.ForeignKey('qar_reports.id',
                                                        ondelete='SET NULL'), nullable=True)
    photos = db.relationship('Photo', backref='report_item', lazy='dynamic',
                             cascade='all, delete-orphan')
    installers = db.relationship('ReportItemInstaller', backref='report_item',
                                 lazy='dynamic', cascade='all, delete-orphan')
    task = db.relationship('Task')
    qar_report = db.relationship('QARReport', foreign_keys=[qar_report_id],
                                 backref=db.backref('source_items', lazy='dynamic'))

    def __repr__(self):
        return f'<ReportItem report={self.report_id} task={self.task_id}>'


class ChecklistSession(db.Model):
    """Pojedyncza sesja pracy nad raportem — od otwarcia do opuszczenia/zamknięcia."""
    __tablename__ = 'checklist_sessions'
    id         = db.Column(db.Integer, primary_key=True)
    report_id  = db.Column(db.Integer, db.ForeignKey('reports.id'), nullable=False)
    started_at = db.Column(db.DateTime, nullable=False)
    ended_at   = db.Column(db.DateTime, nullable=True)
    report     = db.relationship('Report', back_populates='sessions')

    def __repr__(self):
        return f'<ChecklistSession report={self.report_id} start={self.started_at}>'


class Installer(db.Model):
    """Monter wybierany z listy przy wypełnianiu zadań typu 'installer' w checkliście."""
    __tablename__ = 'installers'
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(128), nullable=False)
    is_active  = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    def __repr__(self):
        return f'<Installer {self.name}>'


class ReportItemInstaller(db.Model):
    """Monter przypisany do punktu checklisty; opcjonalna rola (np. 'Obudowa', 'Drzwi')
    pozwala rozdzielić odpowiedzialność, gdy jeden produkt montuje kilku monterów."""
    __tablename__ = 'report_item_installers'
    id              = db.Column(db.Integer, primary_key=True)
    report_item_id  = db.Column(db.Integer, db.ForeignKey('report_items.id'), nullable=False)
    installer_id    = db.Column(db.Integer, db.ForeignKey('installers.id'), nullable=False)
    role            = db.Column(db.String(64), nullable=True)
    is_at_fault     = db.Column(db.Boolean, default=True)  # liczy się do statystyk NG tego montera
    installer       = db.relationship('Installer')

    def __repr__(self):
        return f'<ReportItemInstaller item={self.report_item_id} installer={self.installer_id} role={self.role}>'


class DailyBriefing(db.Model):
    """Podsumowanie analityczne wygenerowane przez Claude API za wybrany zakres dat."""
    __tablename__ = 'daily_briefings'
    id                   = db.Column(db.Integer, primary_key=True)
    start_date           = db.Column(db.Date, nullable=False)
    end_date             = db.Column(db.Date, nullable=False)
    generated_at         = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    generated_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    model_used           = db.Column(db.String(64), nullable=False)
    input_token_count    = db.Column(db.Integer, nullable=True)
    output_token_count   = db.Column(db.Integer, nullable=True)
    content              = db.Column(db.Text, nullable=False)
    raw_aggregated_data  = db.Column(db.Text, nullable=True)
    pdf_filename         = db.Column(db.String(256), nullable=True)
    public_token         = db.Column(db.String(64), unique=True, nullable=True)  # link bez logowania

    generated_by = db.relationship('User', foreign_keys=[generated_by_user_id])

    def __repr__(self):
        return f'<DailyBriefing {self.start_date}..{self.end_date}>'


class AuditLog(db.Model):
    __tablename__ = 'audit_log'
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    action      = db.Column(db.String(64), nullable=False)
    target_type = db.Column(db.String(32), nullable=True)
    target_id   = db.Column(db.Integer, nullable=True)
    detail      = db.Column(db.Text, nullable=True)
    ip          = db.Column(db.String(45), nullable=True)
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    user        = db.relationship('User', foreign_keys=[user_id])

    def __repr__(self):
        return f'<AuditLog {self.action} user={self.user_id}>'


class Photo(db.Model):
    __tablename__ = 'photos'
    id = db.Column(db.Integer, primary_key=True)
    report_item_id = db.Column(db.Integer, db.ForeignKey('report_items.id'), nullable=False)
    filename = db.Column(db.String(256), nullable=False)
    original_name = db.Column(db.String(256), nullable=True)
    uploaded_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    def __repr__(self):
        return f'<Photo {self.filename}>'


# ── Zamówienia klientów ───────────────────────────────────────────────────────

class CabinetType(db.Model):
    __tablename__ = 'cabinet_types'
    id          = db.Column(db.Integer, primary_key=True)
    code        = db.Column(db.String(32), unique=True, nullable=False)
    name        = db.Column(db.String(128), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_active   = db.Column(db.Boolean, default=True)
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    quotes      = db.relationship('Quote', back_populates='cabinet_type', lazy='dynamic')

    def __repr__(self):
        return f'<CabinetType {self.code}>'


class MaterialPrice(db.Model):
    __tablename__ = 'material_prices'
    id             = db.Column(db.Integer, primary_key=True)
    code           = db.Column(db.String(64), unique=True, nullable=False)
    name           = db.Column(db.String(128), nullable=False)
    price          = db.Column(db.Float, nullable=False)
    unit           = db.Column(db.String(32), nullable=False)
    updated_at     = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    updated_by_id  = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    updated_by     = db.relationship('User', foreign_keys=[updated_by_id])

    def __repr__(self):
        return f'<MaterialPrice {self.code}={self.price}>'


class LaborRate(db.Model):
    __tablename__ = 'labor_rates'
    id           = db.Column(db.Integer, primary_key=True)
    volume_range = db.Column(db.String(16), unique=True, nullable=False)  # do400 / do900 / pow900
    label        = db.Column(db.String(32), nullable=False)
    laser        = db.Column(db.Float, default=0.0)
    bending      = db.Column(db.Float, default=0.0)
    welding      = db.Column(db.Float, default=0.0)
    grinding     = db.Column(db.Float, default=0.0)
    assembly     = db.Column(db.Float, default=0.0)
    packaging    = db.Column(db.Float, default=0.0)
    updated_at   = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    @property
    def total(self):
        return self.laser + self.bending + self.welding + self.grinding + self.assembly + self.packaging

    def __repr__(self):
        return f'<LaborRate {self.volume_range}>'


class Quote(db.Model):
    __tablename__ = 'quotes'
    id              = db.Column(db.Integer, primary_key=True)
    number          = db.Column(db.String(32), unique=True, nullable=False)
    client_name     = db.Column(db.String(256), nullable=False)
    cabinet_type_id = db.Column(db.Integer, db.ForeignKey('cabinet_types.id'), nullable=False)
    status          = db.Column(db.String(16), default='draft')  # draft/sent/accepted/closed
    notes           = db.Column(db.Text, nullable=True)
    created_by_id   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at      = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    updated_at      = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    created_by      = db.relationship('User', foreign_keys=[created_by_id])
    cabinet_type    = db.relationship('CabinetType', back_populates='quotes')
    config          = db.relationship('QuoteConfig', back_populates='quote',
                                      uselist=False, cascade='all, delete-orphan')

    STATUS_LABELS = {
        'draft':    'Szkic',
        'sent':     'Wysłany',
        'accepted': 'Zaakceptowany',
        'closed':   'Zamknięty',
    }
    STATUS_CSS = {
        'draft':    'status-neutral',
        'sent':     'status-info',
        'accepted': 'status-success',
        'closed':   'status-warning',
    }

    @property
    def status_label(self): return self.STATUS_LABELS.get(self.status, self.status)
    @property
    def status_css(self):   return self.STATUS_CSS.get(self.status, '')

    def __repr__(self):
        return f'<Quote {self.number}>'


class QuoteConfig(db.Model):
    __tablename__ = 'quote_configs'
    id                  = db.Column(db.Integer, primary_key=True)
    quote_id            = db.Column(db.Integer, db.ForeignKey('quotes.id'), nullable=False, unique=True)
    # Wymiary (mm)
    width               = db.Column(db.Integer, nullable=False)
    height              = db.Column(db.Integer, nullable=False)
    depth               = db.Column(db.Integer, nullable=False)
    # Grubości
    thickness_body      = db.Column(db.Float, default=1.2)
    thickness_plate     = db.Column(db.Float, default=3.0)
    # Opcje
    has_mounting_plate  = db.Column(db.Boolean, default=True)
    back_welded         = db.Column(db.Boolean, default=True)
    back_screwed        = db.Column(db.Boolean, default=False)
    door_single         = db.Column(db.Boolean, default=True)
    door_double         = db.Column(db.Boolean, default=False)
    door_reinforcement  = db.Column(db.Integer, default=0)
    lock_three_point    = db.Column(db.Boolean, default=False)
    lock_cam            = db.Column(db.Boolean, default=False)
    lock_standard       = db.Column(db.Boolean, default=False)
    vertical_beam       = db.Column(db.Integer, default=0)
    plinth              = db.Column(db.Boolean, default=False)
    cable_entries       = db.Column(db.Integer, default=0)
    canopy              = db.Column(db.Boolean, default=False)
    monoblok            = db.Column(db.Integer, default=0)
    back_seal           = db.Column(db.Boolean, default=False)
    non_standard_color  = db.Column(db.Boolean, default=False)
    design_hours        = db.Column(db.Integer, default=0)
    # INOX — gatunek stali
    inox_grade          = db.Column(db.String(4), default='304')  # '304' lub '316'
    # Osprzęt — ilości ręczne
    stud_m6_qty         = db.Column(db.Integer, default=0)
    nut_m8_qty          = db.Column(db.Integer, default=0)
    hinge_qty           = db.Column(db.Integer, default=0)
    screw_cap_qty       = db.Column(db.Integer, default=0)
    plug_qty            = db.Column(db.Integer, default=0)
    # Wycena
    margin              = db.Column(db.Float, default=2.15)
    discount_pct        = db.Column(db.Float, default=0.0)
    bonus_pct           = db.Column(db.Float, default=0.0)
    # Snapshot kalkulacji (JSON)
    calculation         = db.Column(db.JSON, nullable=True)
    quote               = db.relationship('Quote', back_populates='config')

    def __repr__(self):
        return f'<QuoteConfig {self.quote_id} {self.width}x{self.height}x{self.depth}>'


class CatalogProduct(db.Model):
    __tablename__ = 'catalog_products'
    id            = db.Column(db.Integer, primary_key=True)
    code          = db.Column(db.String(32), unique=True, nullable=False)
    family        = db.Column(db.String(32), nullable=False)  # compact/ip65/inox/modular
    name          = db.Column(db.String(128), nullable=False)
    width         = db.Column(db.Integer, nullable=False)
    height        = db.Column(db.Integer, nullable=False)
    depth         = db.Column(db.Integer, nullable=False)
    catalog_price = db.Column(db.Float, default=0.0)
    is_active     = db.Column(db.Boolean, default=True)
    sort_order    = db.Column(db.Integer, default=0)

    def __repr__(self):
        return f'<CatalogProduct {self.code}>'


class Alert(db.Model):
    __tablename__ = 'alerts'
    id           = db.Column(db.Integer, primary_key=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    message      = db.Column(db.String(512), nullable=False)
    alert_type   = db.Column(db.String(32), default='info')  # info / urgent / success
    is_read      = db.Column(db.Boolean, default=False)
    created_at   = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    recipient    = db.relationship('User', foreign_keys=[recipient_id])


class QARReport(db.Model):
    __tablename__ = 'qar_reports'
    id             = db.Column(db.Integer, primary_key=True)
    number         = db.Column(db.String(20), unique=True, nullable=False)
    zo_number      = db.Column(db.String(64), nullable=True)
    drawing_number = db.Column(db.String(64), nullable=True)
    title          = db.Column(db.String(256), nullable=False)
    category       = db.Column(db.String(64), nullable=True)
    location       = db.Column(db.String(128), nullable=True)
    description    = db.Column(db.Text, nullable=False)
    findings       = db.Column(db.Text, nullable=True)
    resolution     = db.Column(db.Text, nullable=True)
    status         = db.Column(db.String(16), default='open')  # open | in_progress | closed
    employee_id    = db.Column(db.Integer, db.ForeignKey('department_employees.id'), nullable=True)
    user_id        = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    verified_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    verified_at    = db.Column(db.DateTime, nullable=True)
    created_at     = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    updated_at     = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    author      = db.relationship('User', foreign_keys=[user_id], backref='qar_reports')
    verified_by = db.relationship('User', foreign_keys=[verified_by_id])
    employee    = db.relationship('DepartmentEmployee', foreign_keys=[employee_id],
                                  backref=db.backref('qar_reports', lazy='dynamic'))
    photos      = db.relationship('QARPhoto', backref='report', lazy='dynamic',
                                  cascade='all, delete-orphan')

    STATUS_LABELS = {'open': 'Otwarty', 'in_progress': 'W toku', 'closed': 'Zamknięty'}
    STATUS_CSS    = {'open': 'status-warning', 'in_progress': 'status-info', 'closed': 'status-success'}

    @property
    def status_label(self): return self.STATUS_LABELS.get(self.status, self.status)
    @property
    def status_css(self):   return self.STATUS_CSS.get(self.status, '')

    def __repr__(self):
        return f'<QARReport {self.number}>'


class QARPhoto(db.Model):
    __tablename__ = 'qar_photos'
    id            = db.Column(db.Integer, primary_key=True)
    report_id     = db.Column(db.Integer, db.ForeignKey('qar_reports.id'), nullable=False)
    filename      = db.Column(db.String(256), nullable=False)
    original_name = db.Column(db.String(256), nullable=True)
    caption       = db.Column(db.String(256), nullable=True)
    created_at    = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    def __repr__(self):
        return f'<QARPhoto {self.filename}>'


class QARCategory(db.Model):
    """Kategoria niezgodności QAR (Spawanie, Cięcie, ...) — słownik edytowalny w panelu admina."""
    __tablename__ = 'qar_categories'
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(64), unique=True, nullable=False)
    order      = db.Column(db.Integer, default=0)
    is_active  = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    def __repr__(self):
        return f'<QARCategory {self.name}>'


# ── Marszruta produkcji ────────────────────────────────────────────────────────

class ProductionDepartment(db.Model):
    """Dział/etap produkcji (Cięcie, Laser, Gięcie, ...) — słownik edytowalny w panelu."""
    __tablename__ = 'production_departments'
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(64), unique=True, nullable=False)
    order      = db.Column(db.Integer, default=0)
    is_active  = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    employees  = db.relationship('DepartmentEmployee', back_populates='department',
                                 lazy='dynamic', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<ProductionDepartment {self.name}>'


class DepartmentEmployee(db.Model):
    """Pracownik przypisany do działu — wybierany z listy przy ocenie etapu."""
    __tablename__ = 'department_employees'
    id            = db.Column(db.Integer, primary_key=True)
    department_id = db.Column(db.Integer, db.ForeignKey('production_departments.id'), nullable=False)
    name          = db.Column(db.String(128), nullable=False)
    is_active     = db.Column(db.Boolean, default=True)
    created_at    = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    # Token plakietki QR (PSH-DEP:<token>). Nowy token unieważnia wydrukowaną plakietkę.
    token         = db.Column(db.String(48), unique=True, nullable=True,
                              default=lambda: secrets.token_hex(16))
    department    = db.relationship('ProductionDepartment', back_populates='employees')

    def __repr__(self):
        return f'<DepartmentEmployee {self.name}>'


class RoutingTemplate(db.Model):
    """Ścieżka produkcyjna — które działy (i w jakiej kolejności) dotyczą danego typu produktu.
    Dopasowywana do nazwy produktu z kodu QR tym samym algorytmem tokenowym co ChecklistTemplate."""
    __tablename__ = 'routing_templates'
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(128), nullable=False)
    is_active  = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    stages     = db.relationship('RoutingTemplateStage', backref='template', lazy='dynamic',
                                 cascade='all, delete-orphan', order_by='RoutingTemplateStage.order')

    @property
    def department_count(self):
        return self.stages.count()

    def __repr__(self):
        return f'<RoutingTemplate {self.name}>'


class RoutingTemplateStage(db.Model):
    """Jeden dział w ścieżce szablonu, z kolejnością wykonania."""
    __tablename__ = 'routing_template_stages'
    id            = db.Column(db.Integer, primary_key=True)
    template_id   = db.Column(db.Integer, db.ForeignKey('routing_templates.id'), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('production_departments.id'), nullable=False)
    order         = db.Column(db.Integer, default=0)
    department    = db.relationship('ProductionDepartment')

    def __repr__(self):
        return f'<RoutingTemplateStage template={self.template_id} dept={self.department_id}>'


class ProductionStage(db.Model):
    """Etap produkcji (Wycinanie, Gięcie, ..., Pakowanie). Nazwy, kolejność i ustawienia
    edytowalne. Pracownicy etapu pochodzą z jednego działu."""
    __tablename__ = 'production_stages'
    id               = db.Column(db.Integer, primary_key=True)
    name             = db.Column(db.String(64), nullable=False)
    order            = db.Column(db.Integer, default=0)
    is_active        = db.Column(db.Boolean, default=True)
    level            = db.Column(db.String(8), default='element')   # element | produkt
    control_required = db.Column(db.Boolean, default=False)
    department_id    = db.Column(db.Integer, db.ForeignKey('production_departments.id'), nullable=True)
    created_at       = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    department       = db.relationship('ProductionDepartment')
    machines         = db.relationship('Machine', backref='stage', lazy='dynamic',
                                       order_by='Machine.name')
    buffers          = db.relationship('Buffer', backref='stage', lazy='dynamic',
                                       order_by='Buffer.code')

    LEVEL_LABELS = {'element': 'Element', 'produkt': 'Produkt'}

    @property
    def level_label(self):
        return self.LEVEL_LABELS.get(self.level, self.level)

    def __repr__(self):
        return f'<ProductionStage {self.name}>'


class Machine(db.Model):
    """Maszyna przypisana do etapu (np. Laser 1). Etap z maszynami wymaga wyboru maszyny przy przejęciu."""
    __tablename__ = 'machines'
    id         = db.Column(db.Integer, primary_key=True)
    stage_id   = db.Column(db.Integer, db.ForeignKey('production_stages.id'), nullable=False)
    name       = db.Column(db.String(64), nullable=False)
    is_active  = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    def __repr__(self):
        return f'<Machine {self.name}>'


class Buffer(db.Model):
    """Bufor (miejsce odkładania elementów) z etykietą QR i kodem kreskowym. `code` to dokładnie
    tekst zakodowany na etykiecie (np. 0001, A001). Etap może być nieprzypisany."""
    __tablename__ = 'buffers'
    id         = db.Column(db.Integer, primary_key=True)
    # Przestarzałe: bufor (wózek) nie jest przypisany do etapu, etap ma ilość leżąca na buforze.
    stage_id   = db.Column(db.Integer, db.ForeignKey('production_stages.id'), nullable=True)
    code       = db.Column(db.String(64), unique=True, nullable=False)
    name       = db.Column(db.String(128), nullable=True)
    number     = db.Column(db.String(32), nullable=True)
    is_active  = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    @property
    def display_name(self):
        return self.name or self.code

    def __repr__(self):
        return f'<Buffer {self.code}>'


class ProductTemplate(db.Model):
    """Szablon produktu (rodzina, np. PSH albo PSH INOX): elementy produktu z ilością na sztukę
    i trasą przez etapy. Dopasowywany do nazwy produktu z kodu QR zlecenia tym samym algorytmem
    tokenów co szablony kontroli."""
    __tablename__ = 'product_templates'
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(128), nullable=False)
    is_active  = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    components = db.relationship('ProductComponent', backref='template', lazy='select',
                                 cascade='all, delete-orphan',
                                 order_by='ProductComponent.position, ProductComponent.id')

    def __repr__(self):
        return f'<ProductTemplate {self.name}>'


class ProductComponent(db.Model):
    """Element produktu w szablonie (nazwa i trasa). Ilość elementu na sztukę produktu zależy od
    zlecenia (np. dwie pary drzwi), więc jest podawana dopiero przy tworzeniu zlecenia."""
    __tablename__ = 'product_components'
    id        = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey('product_templates.id'), nullable=False)
    name      = db.Column(db.String(128), nullable=False)
    position  = db.Column(db.Integer, default=0)
    stage_links = db.relationship('ProductComponentStage', backref='component',
                                  cascade='all, delete-orphan', lazy='select')

    @property
    def stages(self):
        """Etapy trasy w kolejności etapów produkcji."""
        return sorted((l.stage for l in self.stage_links if l.stage),
                      key=lambda st: (st.order or 0, st.id))

    @property
    def stage_ids(self):
        return {l.stage_id for l in self.stage_links}

    def __repr__(self):
        return f'<ProductComponent {self.name}>'


class ProductComponentStage(db.Model):
    """Etap należący do trasy elementu szablonu."""
    __tablename__ = 'product_component_stages'
    __table_args__ = (db.UniqueConstraint('component_id', 'stage_id', name='uq_prodcomp_stage'),)
    id           = db.Column(db.Integer, primary_key=True)
    component_id = db.Column(db.Integer, db.ForeignKey('product_components.id'), nullable=False)
    stage_id     = db.Column(db.Integer, db.ForeignKey('production_stages.id'), nullable=False)
    stage        = db.relationship('ProductionStage')


class DocEntry(db.Model):
    """Teczka dokumentacji — tytuł + opcjonalny opis, z załączonymi
    dokumentami, zdjęciami i notatkami."""
    __tablename__ = 'doc_entries'
    id          = db.Column(db.Integer, primary_key=True)
    title       = db.Column(db.String(256), nullable=False)
    description = db.Column(db.Text, nullable=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    updated_at  = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    author = db.relationship('User', foreign_keys=[user_id])
    files  = db.relationship('DocFile', backref='entry', lazy='dynamic',
                             cascade='all, delete-orphan')
    notes  = db.relationship('DocNote', backref='entry', lazy='dynamic',
                             cascade='all, delete-orphan')

    @property
    def documents(self):
        return self.files.filter_by(kind='document').order_by(DocFile.created_at).all()

    @property
    def images(self):
        return self.files.filter_by(kind='image').order_by(DocFile.created_at).all()

    @property
    def document_count(self):
        return self.files.filter_by(kind='document').count()

    @property
    def image_count(self):
        return self.files.filter_by(kind='image').count()

    @property
    def note_count(self):
        return self.notes.count()

    @property
    def notes_desc(self):
        return self.notes.order_by(DocNote.created_at.desc()).all()

    def __repr__(self):
        return f'<DocEntry {self.id} {self.title!r}>'


class DocFile(db.Model):
    """Pojedynczy załącznik teczki — dokument z dysku lub zdjęcie."""
    __tablename__ = 'doc_files'
    id               = db.Column(db.Integer, primary_key=True)
    entry_id         = db.Column(db.Integer, db.ForeignKey('doc_entries.id'), nullable=False)
    kind             = db.Column(db.String(16), nullable=False, default='document')  # document | image
    filename         = db.Column(db.String(256), nullable=False)   # nazwa na dysku (uuid.ext)
    original_name    = db.Column(db.String(256), nullable=True)
    preview_filename = db.Column(db.String(256), nullable=True)     # np. JPEG dla HEIC; NULL = podgląd = sam plik
    thumb_filename   = db.Column(db.String(256), nullable=True)     # miniatura zdjęcia
    mime_type        = db.Column(db.String(128), nullable=True)
    size_bytes       = db.Column(db.Integer, nullable=True)
    caption          = db.Column(db.String(256), nullable=True)
    user_id          = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at       = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    author = db.relationship('User', foreign_keys=[user_id])

    @property
    def ext(self):
        return (self.original_name or self.filename).rsplit('.', 1)[-1].lower() if '.' in (self.original_name or self.filename) else ''

    @property
    def is_pdf(self):
        return self.ext == 'pdf'

    @property
    def preview_name(self):
        """Nazwa pliku do podglądu inline (preview jeśli jest, inaczej oryginał)."""
        return self.preview_filename or self.filename

    @property
    def previewable(self):
        """Czy da się pokazać podgląd w aplikacji (obraz albo PDF)."""
        return self.kind == 'image' or self.is_pdf or (
            self.preview_filename or '').lower().endswith('.pdf')

    @property
    def size_human(self):
        n = self.size_bytes or 0
        for unit in ('B', 'KB', 'MB', 'GB'):
            if n < 1024:
                return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
            n /= 1024
        return f'{n:.1f} TB'

    ICONS = {
        'pdf': 'bi-file-earmark-pdf', 'doc': 'bi-file-earmark-word', 'docx': 'bi-file-earmark-word',
        'xls': 'bi-file-earmark-excel', 'xlsx': 'bi-file-earmark-excel', 'csv': 'bi-file-earmark-spreadsheet',
        'ppt': 'bi-file-earmark-ppt', 'pptx': 'bi-file-earmark-ppt',
        'txt': 'bi-file-earmark-text', 'odt': 'bi-file-earmark-text', 'ods': 'bi-file-earmark-spreadsheet',
        'zip': 'bi-file-earmark-zip', 'rar': 'bi-file-earmark-zip', '7z': 'bi-file-earmark-zip',
        'dwg': 'bi-file-earmark-ruled', 'dxf': 'bi-file-earmark-ruled',
    }

    @property
    def icon(self):
        if self.kind == 'image':
            return 'bi-file-earmark-image'
        return self.ICONS.get(self.ext, 'bi-file-earmark')

    def __repr__(self):
        return f'<DocFile {self.id} {self.kind} {self.filename}>'


class DocNote(db.Model):
    """Notatka tekstowa dopięta do teczki dokumentacji."""
    __tablename__ = 'doc_notes'
    id         = db.Column(db.Integer, primary_key=True)
    entry_id   = db.Column(db.Integer, db.ForeignKey('doc_entries.id'), nullable=False)
    body       = db.Column(db.Text, nullable=False)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    author = db.relationship('User', foreign_keys=[user_id])

    def __repr__(self):
        return f'<DocNote {self.id} entry={self.entry_id}>'


# ── Lista produkcyjna (import z Excela) ───────────────────────────────────────

class ProdFile(db.Model):
    """Plik .xlsx powiązany z listą produkcyjną: wczytany oryginał, kopia z
    wprowadzonych zmian (export) albo automatyczny backup sprzed ponownego wczytania."""
    __tablename__ = 'prod_files'
    id            = db.Column(db.Integer, primary_key=True)
    kind          = db.Column(db.String(16), nullable=False)   # import | export | backup
    original_name = db.Column(db.String(256), nullable=False)
    stored_name   = db.Column(db.String(256), nullable=False)
    size          = db.Column(db.Integer, default=0)
    note          = db.Column(db.String(256), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at    = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    created_by    = db.relationship('User', foreign_keys=[created_by_id])

    KIND_LABELS = {'import': 'Wczytany plik', 'export': 'Kopia ze zmianami',
                   'backup': 'Backup przed ponownym wczytaniem'}

    @property
    def kind_label(self): return self.KIND_LABELS.get(self.kind, self.kind)


class ProdSheet(db.Model):
    """Jeden arkusz wczytanego pliku (np. PSH, SP, RZ RW IF)."""
    __tablename__ = 'prod_sheets'
    id              = db.Column(db.Integer, primary_key=True)
    name            = db.Column(db.String(64), unique=True, nullable=False)
    position        = db.Column(db.Integer, default=0)
    hidden          = db.Column(db.Boolean, default=False)
    header_row      = db.Column(db.Integer, nullable=True)      # numer wiersza nagłówków w Excelu
    columns_json    = db.Column(db.Text, nullable=False, default='[]')   # nazwy kolumn
    mapping_json    = db.Column(db.Text, nullable=False, default='{}')   # pole logiczne -> indeks kolumny
    formula_cols_json = db.Column(db.Text, nullable=False, default='[]') # kolumny liczone formułą
    deleted_rows_json = db.Column(db.Text, nullable=False, default='[]') # usunięte wiersze źródłowe
    file_id         = db.Column(db.Integer, db.ForeignKey('prod_files.id'), nullable=True)
    imported_at     = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    rows            = db.relationship('ProdRow', backref='sheet', lazy='dynamic',
                                      cascade='all, delete-orphan')

    def _load(self, field, default):
        import json
        try:
            return json.loads(getattr(self, field) or '')
        except ValueError:
            return default

    @property
    def columns(self): return self._load('columns_json', [])
    @property
    def mapping(self): return self._load('mapping_json', {})
    @property
    def formula_cols(self): return self._load('formula_cols_json', [])
    @property
    def deleted_rows(self): return self._load('deleted_rows_json', [])


class ProdRow(db.Model):
    """Jedno zlecenie (wiersz) arkusza. Wartości komórek w `data` (lista JSON
    wyrównana do kolumn arkusza), formuły oryginału w `formulas`."""
    __tablename__ = 'prod_rows'
    id        = db.Column(db.Integer, primary_key=True)
    sheet_id  = db.Column(db.Integer, db.ForeignKey('prod_sheets.id'), nullable=False, index=True)
    position  = db.Column(db.Integer, nullable=False, default=0)   # numer wiersza w Excelu
    src_row   = db.Column(db.Integer, nullable=True)               # None = dodany w aplikacji
    data      = db.Column(db.Text, nullable=False, default='[]')
    formulas  = db.Column(db.Text, nullable=True)                  # {kol: "=..."}
    changed   = db.Column(db.Text, nullable=True)                  # [kol,...] zmienione w aplikacji
    zo        = db.Column(db.String(64), nullable=True, index=True)
    search    = db.Column(db.Text, nullable=True)
    # Czas utworzenia list kontrolnych dla wiersza (znacznik powiązania z Kontrolą Końcową).
    checklists_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (db.Index('ix_prod_rows_sheet_pos', 'sheet_id', 'position'),)


class ProdSheetAccess(db.Model):
    """Dostęp użytkownika (roli Planowanie) do wybranego arkusza."""
    __tablename__ = 'prod_sheet_access'
    id       = db.Column(db.Integer, primary_key=True)
    user_id  = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    sheet_id = db.Column(db.Integer, db.ForeignKey('prod_sheets.id'), nullable=False)
    __table_args__ = (db.UniqueConstraint('user_id', 'sheet_id', name='uq_prod_access'),)


# ── Lista produkcyjna: pracownicy, etapy, wpisy ze skanowania ─────────────────

class ProdEmployee(db.Model):
    """Pracownik produkcji identyfikowany plakietką QR (token). Imię, inicjały i
    przydziały można zmieniać bez przedruku kodu, bo kod zawiera tylko token."""
    __tablename__ = 'prod_employees'
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(128), nullable=False)
    initials   = db.Column(db.String(16), nullable=False)
    token      = db.Column(db.String(48), unique=True, nullable=False)
    # Kod z istniejącej plakietki (np. kod kreskowy "JR12"), zapisany bez spacji, wielkimi literami
    badge_code = db.Column(db.String(64), nullable=True, index=True)
    is_active  = db.Column(db.Boolean, default=True)
    notes      = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    departments = db.relationship('ProdEmployeeDept', backref='employee', lazy='dynamic',
                                  cascade='all, delete-orphan')

    @property
    def department_ids(self):
        return {d.department_id for d in self.departments.all()}


class ProdEmployeeDept(db.Model):
    """Przydział pracownika do działu (słownik działów z modułu Marszruta)."""
    __tablename__ = 'prod_employee_departments'
    id            = db.Column(db.Integer, primary_key=True)
    employee_id   = db.Column(db.Integer, db.ForeignKey('prod_employees.id'), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('production_departments.id'), nullable=False)
    department    = db.relationship('ProductionDepartment')
    __table_args__ = (db.UniqueConstraint('employee_id', 'department_id', name='uq_prod_emp_dept'),)


class ProdStageColumn(db.Model):
    """Kolumna arkusza będąca etapem produkcji (GIĘCIE, SPAW. itd.), do której
    pracownik wpisuje się skanem. Opcjonalnie przypisana do działu."""
    __tablename__ = 'prod_stage_columns'
    id            = db.Column(db.Integer, primary_key=True)
    sheet_id      = db.Column(db.Integer, db.ForeignKey('prod_sheets.id'), nullable=False, index=True)
    col_index     = db.Column(db.Integer, nullable=False)
    label         = db.Column(db.String(64), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('production_departments.id'), nullable=True)
    is_active     = db.Column(db.Boolean, default=True)
    department    = db.relationship('ProductionDepartment')
    __table_args__ = (db.UniqueConstraint('sheet_id', 'col_index', name='uq_prod_stage_col'),)


class ProdEntry(db.Model):
    """Historia wpisów pracowników w kolumny etapów (kto, gdzie, kiedy)."""
    __tablename__ = 'prod_entries'
    id           = db.Column(db.Integer, primary_key=True)
    employee_id  = db.Column(db.Integer, db.ForeignKey('prod_employees.id'), nullable=True)
    # imię pracownika zapisane przy usunięciu go z bazy, żeby historia nie traciła autora wpisu
    employee_name = db.Column(db.String(128), nullable=True)
    row_id       = db.Column(db.Integer, db.ForeignKey('prod_rows.id'), nullable=True)
    user_id      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)  # konto stanowiska
    sheet_name   = db.Column(db.String(64), nullable=True)
    zo           = db.Column(db.String(64), nullable=True)
    product      = db.Column(db.String(256), nullable=True)
    col_label    = db.Column(db.String(64), nullable=True)
    value_before = db.Column(db.String(128), nullable=True)
    value_after  = db.Column(db.String(128), nullable=True)
    created_at   = db.Column(db.DateTime, default=lambda: datetime.now(UTC), index=True)
    employee     = db.relationship('ProdEmployee')


class ProdSetting(db.Model):
    __tablename__ = 'prod_settings'
    key   = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.String(256), nullable=True)

    @staticmethod
    def get(key, default=None):
        row = db.session.get(ProdSetting, key)
        return row.value if row and row.value is not None else default

    @staticmethod
    def set(key, value):
        row = db.session.get(ProdSetting, key)
        if row:
            row.value = value
        else:
            db.session.add(ProdSetting(key=key, value=value))


# ── Kontrola zamówień (stałe punkty kontrolne) ─────────────────────────────────

class ControlPoint(db.Model):
    """Stały, edytowalny przez admina punkt kontrolny. Jedna wspólna lista dla wszystkich zamówień."""
    __tablename__ = 'control_points'
    id          = db.Column(db.Integer, primary_key=True)
    order       = db.Column(db.Integer, default=0)
    title       = db.Column(db.String(256), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_active   = db.Column(db.Boolean, default=True)
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    # Dział, do którego należy punkt. Pusty: punkt ogólny (trafia do każdej kontroli).
    department_id = db.Column(db.Integer, db.ForeignKey('production_departments.id'), nullable=True)
    department  = db.relationship('ProductionDepartment', foreign_keys=[department_id])

    def __repr__(self):
        return f'<ControlPoint {self.title}>'


class ControlOrder(db.Model):
    """Zlecenie na liście Kontroli zamówień (para ZO + produkt ze skanu QR). Ma historię kontroli po
    działach (`ControlRecord.control_order_id`), każda kontrola to dział, osoba i wynik."""
    __tablename__ = 'control_orders'
    id           = db.Column(db.Integer, primary_key=True)
    zo_number    = db.Column(db.String(64), nullable=False, index=True)
    product_name = db.Column(db.String(256), nullable=True)
    client       = db.Column(db.String(256), nullable=True)
    quantity     = db.Column(db.Integer, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at   = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    created_by   = db.relationship('User', foreign_keys=[created_by_id])
    controls     = db.relationship('ControlRecord', back_populates='control_order',
                                   order_by='ControlRecord.id.desc()')

    def __repr__(self):
        return f'<ControlOrder {self.zo_number} {self.product_name}>'


class ControlRecord(db.Model):
    """Kontrola jednego zamówienia (ZO) po stałych punktach.
    round=1: kontrola wstępna. Po kliknięciu 'Wykonane' przez operatora powstaje
    round=2 (kontrola sprawdzająca) powiązana przez previous_id."""
    __tablename__ = 'control_records'
    id           = db.Column(db.Integer, primary_key=True)
    zo_number    = db.Column(db.String(64), nullable=False, index=True)
    product_name = db.Column(db.String(256), nullable=True)
    client       = db.Column(db.String(256), nullable=True)
    quantity     = db.Column(db.Integer, nullable=True)
    round        = db.Column(db.Integer, default=1, nullable=False)
    previous_id  = db.Column(db.Integer, db.ForeignKey('control_records.id'), nullable=True)
    status       = db.Column(db.String(16), default='in_progress')  # in_progress | completed
    user_id      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at   = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    completed_at = db.Column(db.DateTime, nullable=True)
    done_by_id   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    done_at      = db.Column(db.DateTime, nullable=True)
    # Pracownik, który wykonał kontrolowaną pracę (dział wynika z pracownika). Opcjonalny.
    employee_id  = db.Column(db.Integer, db.ForeignKey('department_employees.id'), nullable=True)
    # Kontrola działu (jedna ocena, bez cyklu). Pusty: kontrola ogólna z cyklem wstępna, Wykonane, sprawdzająca.
    department_id = db.Column(db.Integer, db.ForeignKey('production_departments.id'), nullable=True)
    # Zlecenie z listy zleceń, do którego należy kontrola działu (puste: stara kontrola ogólna).
    control_order_id = db.Column(db.Integer, db.ForeignKey('control_orders.id'), nullable=True, index=True)

    controller = db.relationship('User', foreign_keys=[user_id])
    department = db.relationship('ProductionDepartment', foreign_keys=[department_id])
    control_order = db.relationship('ControlOrder', back_populates='controls')
    employee   = db.relationship('DepartmentEmployee', foreign_keys=[employee_id])
    done_by    = db.relationship('User', foreign_keys=[done_by_id])
    previous   = db.relationship('ControlRecord', remote_side=[id],
                                 backref=db.backref('followup', uselist=False))
    results    = db.relationship('ControlResult', back_populates='record',
                                 cascade='all, delete-orphan',
                                 order_by='ControlResult.position, ControlResult.id')

    @property
    def result(self):
        """Wynik zakończonej kontroli: 'NG' gdy choć jeden punkt NG, inaczej 'OK'."""
        if self.status != 'completed':
            return None
        return 'NG' if any(r.result == 'NG' for r in self.results) else 'OK'

    @property
    def performer(self):
        """Wykonawca pracy: z najnowszej kontroli cyklu (sprawdzającej, jeśli ma wskazanego)."""
        fu = self.followup
        return (fu.employee if fu is not None and fu.employee is not None else self.employee)

    @property
    def ng_count(self):
        return sum(1 for r in self.results if r.result == 'NG')

    @property
    def rated_count(self):
        return sum(1 for r in self.results if r.result)

    @property
    def state(self):
        """Stan cyklu (dla kontroli wstępnej): in_control | to_do | recheck | closed.
        Kontrola działu nie ma cyklu: po zakończeniu jest od razu zamknięta."""
        if self.status != 'completed':
            return 'in_control'
        if self.department_id is not None:
            return 'closed'
        if not self.done_at:
            return 'to_do'
        if self.followup is not None and self.followup.status == 'completed':
            return 'closed'
        return 'recheck'

    def __repr__(self):
        return f'<ControlRecord {self.zo_number} r{self.round}>'


CONTROL_STATE_LABELS = {
    'in_control': 'W trakcie kontroli',
    'to_do':      'Do wykonania',
    'recheck':    'Wykonane, czeka na kontrolę sprawdzającą',
    'closed':     'Zamknięte',
}


class ControlResult(db.Model):
    """Ocena jednego punktu w kontroli. Tytuł jest kopią z chwili utworzenia kontroli,
    więc późniejsza edycja listy punktów nie zmienia historii."""
    __tablename__ = 'control_results'
    id            = db.Column(db.Integer, primary_key=True)
    record_id     = db.Column(db.Integer, db.ForeignKey('control_records.id'), nullable=False)
    point_id      = db.Column(db.Integer, db.ForeignKey('control_points.id'), nullable=True)
    position      = db.Column(db.Integer, default=0)
    title         = db.Column(db.String(256), nullable=False)
    description   = db.Column(db.Text, nullable=True)
    result        = db.Column(db.String(2), nullable=True)   # OK | NG | None
    notes         = db.Column(db.Text, nullable=True)
    qar_report_id = db.Column(db.Integer, db.ForeignKey('qar_reports.id'), nullable=True)
    checked_at    = db.Column(db.DateTime, nullable=True)

    record     = db.relationship('ControlRecord', back_populates='results')
    qar_report = db.relationship('QARReport', foreign_keys=[qar_report_id])


class StageControlPoint(db.Model):
    """Punkt kontrolny etapu (osobna lista dla każdego etapu produkcji)."""
    __tablename__ = 'stage_control_points'
    id          = db.Column(db.Integer, primary_key=True)
    stage_id    = db.Column(db.Integer, db.ForeignKey('production_stages.id'), nullable=False, index=True)
    order       = db.Column(db.Integer, default=0)
    title       = db.Column(db.String(256), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_active   = db.Column(db.Boolean, default=True)
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    stage       = db.relationship('ProductionStage', backref=db.backref('control_points', lazy='dynamic',
                                  order_by='StageControlPoint.order, StageControlPoint.id'))


class ProductTemplateControl(db.Model):
    """Domyślny plan kontroli produktu: etapy, na których zlecenie z tego szablonu ma kontrolę."""
    __tablename__ = 'product_template_controls'
    __table_args__ = (db.UniqueConstraint('template_id', 'stage_id', name='uq_tpl_control_stage'),)
    id          = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey('product_templates.id'), nullable=False)
    stage_id    = db.Column(db.Integer, db.ForeignKey('production_stages.id'), nullable=False)
    template    = db.relationship('ProductTemplate', backref=db.backref(
        'control_plan', cascade='all, delete-orphan', lazy='select'))
    stage       = db.relationship('ProductionStage')
