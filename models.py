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
    def is_monter(self): return self.role == 'monter'
    @property
    def is_order_user(self): return self.role in ('admin', 'order')
    @property
    def is_spawacz(self): return self.role == 'spawacz'
    @property
    def is_spawalnia_user(self): return self.role in ('admin', 'kontroler', 'spawacz')
    @property
    def is_konstruktor(self): return self.role == 'konstruktor'
    @property
    def role_label(self):
        return {'admin': 'Administrator', 'order': 'Zamawiający',
                'kontroler': 'Kontroler', 'monter': 'Monter',
                'spawacz': 'Spawacz', 'konstruktor': 'Konstruktor'}.get(self.role, self.role)


class ChecklistTemplate(db.Model):
    __tablename__ = 'checklist_templates'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    template_type = db.Column(db.String(16), default='kontroler')  # kontroler | monter
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
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=True)
    title = db.Column(db.String(256), nullable=False)
    status = db.Column(db.String(32), default='in_progress')
    report_type = db.Column(db.String(16), default='kontroler')  # kontroler | monter
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    duration_seconds = db.Column(db.Integer, nullable=True)
    batch_id    = db.Column(db.String(32), nullable=True)
    batch_index = db.Column(db.Integer, nullable=True)
    batch_total = db.Column(db.Integer, nullable=True)
    notes        = db.Column(db.Text, nullable=True)
    locked_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    locked_at    = db.Column(db.DateTime, nullable=True)
    items = db.relationship('ReportItem', backref='report', lazy='dynamic',
                            cascade='all, delete-orphan')
    sessions  = db.relationship('ChecklistSession', back_populates='report',
                                lazy='dynamic', cascade='all, delete-orphan')
    template  = db.relationship('ChecklistTemplate')
    locked_by = db.relationship('User', foreign_keys=[locked_by_id])

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
    photos = db.relationship('Photo', backref='report_item', lazy='dynamic',
                             cascade='all, delete-orphan')
    task = db.relationship('Task')

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

class Order(db.Model):
    __tablename__ = 'orders'
    id            = db.Column(db.Integer, primary_key=True)
    number        = db.Column(db.String(64), unique=True, nullable=False)
    product_name  = db.Column(db.String(256), nullable=False)
    client        = db.Column(db.String(256), nullable=False)
    quantity      = db.Column(db.Integer, default=1)
    due_date      = db.Column(db.Date, nullable=True)
    status        = db.Column(db.String(32), default='active')
    # active / in_control / ready_to_ship / shipped
    notes         = db.Column(db.Text, nullable=True)
    external_number = db.Column(db.String(64), nullable=True)  # nr zamówienia w systemie ERP (np. Streamsoft)
    template_id        = db.Column(db.Integer, db.ForeignKey('checklist_templates.id'), nullable=True)
    monter_template_id = db.Column(db.Integer, db.ForeignKey('checklist_templates.id'), nullable=True)
    pdf_filename       = db.Column(db.String(256), nullable=True)
    created_by_id      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at         = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    created_by         = db.relationship('User', foreign_keys=[created_by_id])
    template           = db.relationship('ChecklistTemplate', foreign_keys=[template_id])
    monter_template    = db.relationship('ChecklistTemplate', foreign_keys=[monter_template_id])
    reports       = db.relationship('Report', backref='order', lazy='dynamic',
                                    foreign_keys='Report.order_id')

    STATUS_LABELS = {
        'active':        'Oczekuje',
        'in_control':    'W kontroli',
        'ready_to_ship': 'Gotowe do wysyłki',
        'shipped':       'Wysłane',
    }
    STATUS_CSS = {
        'active':        'status-info',
        'in_control':    'status-warning',
        'ready_to_ship': 'status-success',
        'shipped':       'status-neutral',
    }

    @property
    def status_label(self): return self.STATUS_LABELS.get(self.status, self.status)
    @property
    def status_css(self):   return self.STATUS_CSS.get(self.status, '')

    @property
    def reports_total(self):   return self.reports.count()
    @property
    def reports_done(self):    return self.reports.filter_by(status='completed').count()
    @property
    def progress_pct(self):
        t = self.reports_total
        return int(self.reports_done / t * 100) if t else 0

    @property
    def has_ng(self):
        return (ReportItem.query
                .join(Report)
                .filter(Report.order_id == self.id, ReportItem.result == 'ng')
                .count() > 0)


# ── Kosztorys ─────────────────────────────────────────────────────────────────

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
    order_id     = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=True)
    is_read      = db.Column(db.Boolean, default=False)
    created_at   = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    recipient    = db.relationship('User', foreign_keys=[recipient_id])
    order        = db.relationship('Order', foreign_keys=[order_id])


# ── Spawalnia ──────────────────────────────────────────────────────────────────

class SpawalniaOperator(db.Model):
    __tablename__ = 'spawalnia_operators'
    id         = db.Column(db.Integer, primary_key=True)
    initials   = db.Column(db.String(10), unique=True, nullable=False)
    name       = db.Column(db.String(128), nullable=False)
    is_active  = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    records    = db.relationship('SpawalniaRecord', back_populates='operator', lazy='dynamic')

    def __repr__(self):
        return f'<SpawalniaOperator {self.initials}>'


class GiecieOperator(db.Model):
    __tablename__ = 'giecie_operators'
    id         = db.Column(db.Integer, primary_key=True)
    initials   = db.Column(db.String(10), unique=True, nullable=False)
    name       = db.Column(db.String(128), nullable=False)
    is_active  = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    records    = db.relationship('SpawalniaRecord', back_populates='giecie_operator', lazy='dynamic')

    def __repr__(self):
        return f'<GiecieOperator {self.initials}>'


class CiecieOperator(db.Model):
    __tablename__ = 'ciecie_operators'
    id         = db.Column(db.Integer, primary_key=True)
    initials   = db.Column(db.String(10), unique=True, nullable=False)
    name       = db.Column(db.String(128), nullable=False)
    is_active  = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    records    = db.relationship('SpawalniaRecord', back_populates='ciecie_operator', lazy='dynamic')

    def __repr__(self):
        return f'<CiecieOperator {self.initials}>'


class SpawalniaRecord(db.Model):
    __tablename__ = 'spawalnia_records'
    id                 = db.Column(db.Integer, primary_key=True)
    zo_number          = db.Column(db.String(64), nullable=False)
    otworowanie        = db.Column(db.String(2), nullable=True)   # OK | NG
    przekatna          = db.Column(db.String(2), nullable=True)   # OK | NG
    przekatna_odchylka = db.Column(db.Float, nullable=True)       # odchyłka w mm
    pomiar1            = db.Column(db.Float, nullable=True)
    pomiar2            = db.Column(db.Float, nullable=True)
    pomiar3            = db.Column(db.Float, nullable=True)
    jakosc_wyciecia    = db.Column(db.String(2), nullable=True)   # OK | NG
    operator_id        = db.Column(db.Integer, db.ForeignKey('spawalnia_operators.id'), nullable=True)
    giecie_operator_id = db.Column(db.Integer, db.ForeignKey('giecie_operators.id'), nullable=True)
    ciecie_operator_id = db.Column(db.Integer, db.ForeignKey('ciecie_operators.id'), nullable=True)
    batch_id           = db.Column(db.String(32), nullable=True)
    batch_index        = db.Column(db.Integer, nullable=True)
    batch_total        = db.Column(db.Integer, nullable=True)
    created_by_id      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at         = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    updated_at         = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    operator           = db.relationship('SpawalniaOperator', back_populates='records')
    giecie_operator    = db.relationship('GiecieOperator', back_populates='records')
    ciecie_operator    = db.relationship('CiecieOperator', back_populates='records')
    created_by         = db.relationship('User', foreign_keys=[created_by_id])

    @property
    def has_ng(self):
        return 'NG' in [self.otworowanie, self.przekatna, self.jakosc_wyciecia]

    @property
    def is_empty(self):
        return all(v is None for v in [
            self.otworowanie, self.przekatna, self.jakosc_wyciecia,
            self.pomiar1, self.pomiar2, self.pomiar3,
        ])

    def __repr__(self):
        return f'<SpawalniaRecord {self.zo_number}>'


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
    user_id        = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    verified_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    verified_at    = db.Column(db.DateTime, nullable=True)
    created_at     = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    updated_at     = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    author      = db.relationship('User', foreign_keys=[user_id], backref='qar_reports')
    verified_by = db.relationship('User', foreign_keys=[verified_by_id])
    photos      = db.relationship('QARPhoto', backref='report', lazy='dynamic',
                                  cascade='all, delete-orphan')

    STATUS_LABELS = {'open': 'Otwarty', 'in_progress': 'W toku', 'closed': 'Zamknięty'}
    STATUS_CSS    = {'open': 'status-warning', 'in_progress': 'status-info', 'closed': 'status-success'}

    CATEGORIES = ['Spawanie', 'Montaż', 'Materiał', 'Malowanie', 'Konstrukcja', 'Dokumentacja', 'Inne']

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


class QATask(db.Model):
    """Stała lista zadań karty kontroli jakości — bez historii dziennej,
    kontroler zaznacza wykonanie i uwagi na bieżąco."""
    __tablename__ = 'qa_tasks'
    id            = db.Column(db.Integer, primary_key=True)
    order         = db.Column(db.Integer, default=0)
    title         = db.Column(db.String(256), nullable=False)
    description   = db.Column(db.Text, nullable=True)
    is_done       = db.Column(db.Boolean, default=False)
    notes         = db.Column(db.Text, nullable=True)
    is_active     = db.Column(db.Boolean, default=True)
    updated_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    updated_at    = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    created_at    = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    updated_by = db.relationship('User', foreign_keys=[updated_by_id])

    def __repr__(self):
        return f'<QATask {self.title}>'


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
    department    = db.relationship('ProductionDepartment', back_populates='employees')
    stages        = db.relationship('RoutingCardStage', back_populates='employee', lazy='dynamic')

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


class RoutingCard(db.Model):
    """Karta marszrutowa — jedna na zlecenie/ZO (całą partię), utworzona ze skanu QR.

    `identifier` odpowiada polu "o" (numer zamówienia) z tego samego kodu QR,
    który obsługuje dziś /checklist/from-qr — niepowiązana z modelem Order
    (wzorem SpawalniaRecord.zo_number), żeby skan działał również dla zleceń
    bez założonego Order w systemie."""
    __tablename__ = 'routing_cards'
    id            = db.Column(db.Integer, primary_key=True)
    identifier    = db.Column(db.String(64), nullable=False)
    product_name  = db.Column(db.String(256), nullable=False)
    client        = db.Column(db.String(256), nullable=True)
    quantity      = db.Column(db.Integer, default=1)
    template_id   = db.Column(db.Integer, db.ForeignKey('routing_templates.id'), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at    = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    template      = db.relationship('RoutingTemplate')
    created_by    = db.relationship('User', foreign_keys=[created_by_id])
    stages        = db.relationship('RoutingCardStage', back_populates='card', lazy='dynamic',
                                    cascade='all, delete-orphan', order_by='RoutingCardStage.order')

    @property
    def is_complete(self):
        stages = self.stages.all()
        return bool(stages) and all(s.result is not None for s in stages)

    @property
    def status_label(self):
        if not self.stages.count():
            return 'Brak etapów'
        return 'Zakończona' if self.is_complete else 'W toku'

    @property
    def has_ng(self):
        return self.stages.filter_by(result='ng').first() is not None

    def __repr__(self):
        return f'<RoutingCard {self.identifier}>'


class RoutingCardStage(db.Model):
    """Ocena jednego działu na konkretnej karcie marszrutowej."""
    __tablename__ = 'routing_card_stages'
    id            = db.Column(db.Integer, primary_key=True)
    card_id       = db.Column(db.Integer, db.ForeignKey('routing_cards.id'), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('production_departments.id'), nullable=False)
    order         = db.Column(db.Integer, default=0)
    employee_id   = db.Column(db.Integer, db.ForeignKey('department_employees.id'), nullable=True)
    result        = db.Column(db.String(4), nullable=True)   # ok | ng | None
    notes         = db.Column(db.Text, nullable=True)
    checked_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    checked_at    = db.Column(db.DateTime, nullable=True)
    card          = db.relationship('RoutingCard', back_populates='stages')
    department    = db.relationship('ProductionDepartment')
    employee      = db.relationship('DepartmentEmployee', back_populates='stages')
    checked_by    = db.relationship('User', foreign_keys=[checked_by_id])
    photos        = db.relationship('RoutingCardPhoto', backref='stage', lazy='dynamic',
                                    cascade='all, delete-orphan')

    RESULT_LABELS = {'ok': 'Zgodne', 'ng': 'Niezgodne'}

    @property
    def result_label(self):
        return self.RESULT_LABELS.get(self.result, '—')

    def __repr__(self):
        return f'<RoutingCardStage card={self.card_id} dept={self.department_id}>'


class RoutingCardPhoto(db.Model):
    __tablename__ = 'routing_card_photos'
    id            = db.Column(db.Integer, primary_key=True)
    stage_id      = db.Column(db.Integer, db.ForeignKey('routing_card_stages.id'), nullable=False)
    filename      = db.Column(db.String(256), nullable=False)
    original_name = db.Column(db.String(256), nullable=True)
    created_at    = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    def __repr__(self):
        return f'<RoutingCardPhoto {self.filename}>'
