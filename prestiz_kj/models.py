"""Tabele modułu poglądowego "KJ Prestiż" (prefiks `pkj_`). Moduł nie zapisuje do tabel spoza tego pliku."""
from datetime import datetime, timezone

from models import db
from . import clock

UTC = timezone.utc


def _now():
    return clock.now()


# ── Konfiguracja (K1-K4) ───────────────────────────────────────────────────────

class PkjOperation(db.Model):
    """Operacja trasy. `kj_required` to parametr K1: po meldunku RW na materiały jest wstrzymane."""
    __tablename__ = 'pkj_operations'
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(128), nullable=False, unique=True)
    order       = db.Column(db.Integer, default=0)
    kj_required = db.Column(db.Boolean, default=False, nullable=False)
    is_active   = db.Column(db.Boolean, default=True, nullable=False)


class PkjMaterialGroup(db.Model):
    """Grupa materiałów kontrolowanych wspólnie (K4)."""
    __tablename__ = 'pkj_material_groups'
    id   = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False, unique=True)


class PkjMaterial(db.Model):
    """Materiał (element wyrobu). `kj_flag` to parametr K2: bez flagi nie pokazuje się w aplikacji KJ."""
    __tablename__ = 'pkj_materials'
    id        = db.Column(db.Integer, primary_key=True)
    name      = db.Column(db.String(128), nullable=False, unique=True)
    kj_flag   = db.Column(db.Boolean, default=False, nullable=False)
    group_id  = db.Column(db.Integer, db.ForeignKey('pkj_material_groups.id'), nullable=True)
    order     = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    group     = db.relationship('PkjMaterialGroup')


# Zakres punktu kontrolnego (K3, A4)
SCOPE_ALL, SCOPE_OPERATION, SCOPE_MATERIAL, SCOPE_GROUP = 'all', 'operation', 'material', 'group'
POINT_SCOPES = (SCOPE_ALL, SCOPE_OPERATION, SCOPE_MATERIAL, SCOPE_GROUP)
POINT_KINDS = ('ok_nok', 'value')


class PkjCheckPoint(db.Model):
    """Punkt kontrolny: wspólny dla wszystkich operacji, operacji, materiału albo grupy materiałów."""
    __tablename__ = 'pkj_check_points'
    id          = db.Column(db.Integer, primary_key=True)
    title       = db.Column(db.String(256), nullable=False)
    help_text   = db.Column(db.Text, nullable=True)
    kind        = db.Column(db.String(8), default='ok_nok', nullable=False)   # ok_nok | value
    unit        = db.Column(db.String(16), nullable=True)
    min_value   = db.Column(db.Float, nullable=True)
    max_value   = db.Column(db.Float, nullable=True)
    image       = db.Column(db.String(128), nullable=True)                   # grafika punktu (plik)
    scope       = db.Column(db.String(10), default=SCOPE_ALL, nullable=False)
    operation_id = db.Column(db.Integer, db.ForeignKey('pkj_operations.id'), nullable=True)
    material_id  = db.Column(db.Integer, db.ForeignKey('pkj_materials.id'), nullable=True)
    group_id     = db.Column(db.Integer, db.ForeignKey('pkj_material_groups.id'), nullable=True)
    photo_required_on_nok = db.Column(db.Boolean, default=False, nullable=False)
    order       = db.Column(db.Integer, default=0)
    is_active   = db.Column(db.Boolean, default=True, nullable=False)
    operation   = db.relationship('PkjOperation')
    material    = db.relationship('PkjMaterial')
    group       = db.relationship('PkjMaterialGroup')

    @property
    def target_value(self):
        """Wartość pola wyboru zakresu na ekranie konfiguracji: all, op:<id>, mat:<id>, grp:<id>."""
        return {SCOPE_OPERATION: f'op:{self.operation_id}', SCOPE_MATERIAL: f'mat:{self.material_id}',
                SCOPE_GROUP: f'grp:{self.group_id}'}.get(self.scope, 'all')

    @property
    def target_label(self):
        if self.scope == SCOPE_OPERATION and self.operation:
            return f'Operacja: {self.operation.name}'
        if self.scope == SCOPE_MATERIAL and self.material:
            return f'Materiał: {self.material.name}'
        if self.scope == SCOPE_GROUP and self.group:
            return f'Grupa: {self.group.name}'
        return 'Wspólny'


class PkjReason(db.Model):
    """Słownik przyczyn niezgodności."""
    __tablename__ = 'pkj_reasons'
    id        = db.Column(db.Integer, primary_key=True)
    name      = db.Column(db.String(128), nullable=False, unique=True)
    order     = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True, nullable=False)


class PkjEmployee(db.Model):
    """Fikcyjny pracownik demo (wykonawca w meldunkach). Nie czyta tabel działów i pracowników profit."""
    __tablename__ = 'pkj_employees'
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(128), nullable=False)
    department = db.Column(db.String(64), nullable=True)
    is_active  = db.Column(db.Boolean, default=True, nullable=False)


class PkjSetting(db.Model):
    """Ustawienia modułu (klucz, wartość), m.in. warianty pytań otwartych."""
    __tablename__ = 'pkj_settings'
    key   = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.String(256), nullable=True)


# ── Zlecenia, meldunki, kontrole (etapy 2 do 4, schemat gotowy od etapu 1) ─────

class PkjOrder(db.Model):
    """Zlecenie produkcyjne (ZP) albo naprawcze (ZN). `source_return_id` bez klucza obcego (cykl z zawrotami)."""
    __tablename__ = 'pkj_orders'
    id               = db.Column(db.Integer, primary_key=True)
    number           = db.Column(db.String(64), nullable=False, unique=True)
    type             = db.Column(db.String(2), default='ZP', nullable=False)   # ZP | ZN
    parent_id        = db.Column(db.Integer, db.ForeignKey('pkj_orders.id'), nullable=True)
    source_return_id = db.Column(db.Integer, nullable=True)
    product          = db.Column(db.String(256), nullable=True)
    client           = db.Column(db.String(256), nullable=True)
    quantity         = db.Column(db.Integer, default=1)
    status           = db.Column(db.String(24), default='nowe', nullable=False)
    qr_key           = db.Column(db.String(512), nullable=True, index=True)
    is_demo          = db.Column(db.Boolean, default=True, nullable=False)
    is_demo_scenario = db.Column(db.Boolean, default=False, nullable=False)
    created_at       = db.Column(db.DateTime, default=_now)


class PkjOrderOperation(db.Model):
    __tablename__ = 'pkj_order_operations'
    id            = db.Column(db.Integer, primary_key=True)
    order_id      = db.Column(db.Integer, db.ForeignKey('pkj_orders.id'), nullable=False, index=True)
    operation_id  = db.Column(db.Integer, db.ForeignKey('pkj_operations.id'), nullable=True)
    operation_name = db.Column(db.String(128), nullable=False)
    position      = db.Column(db.Integer, default=0)
    kj_required   = db.Column(db.Boolean, default=False, nullable=False)
    status        = db.Column(db.String(16), default='oczekuje', nullable=False)
    reported_at   = db.Column(db.DateTime, nullable=True)
    employee_id   = db.Column(db.Integer, db.ForeignKey('pkj_employees.id'), nullable=True)
    employee_name = db.Column(db.String(128), nullable=True)
    added_manually = db.Column(db.Boolean, default=False, nullable=False)
    repair_for_return_id = db.Column(db.Integer, nullable=True)


class PkjOrderMaterial(db.Model):
    __tablename__ = 'pkj_order_materials'
    id                 = db.Column(db.Integer, primary_key=True)
    order_id           = db.Column(db.Integer, db.ForeignKey('pkj_orders.id'), nullable=False, index=True)
    order_operation_id = db.Column(db.Integer, db.ForeignKey('pkj_order_operations.id'), nullable=True)
    material_id        = db.Column(db.Integer, db.ForeignKey('pkj_materials.id'), nullable=True)
    material_name      = db.Column(db.String(128), nullable=False)
    quantity           = db.Column(db.Integer, default=1)
    rw_status          = db.Column(db.String(12), default='brak', nullable=False)
    element_status     = db.Column(db.String(24), default='oczekuje', nullable=False)


class PkjInspection(db.Model):
    __tablename__ = 'pkj_inspections'
    id                 = db.Column(db.Integer, primary_key=True)
    order_id           = db.Column(db.Integer, db.ForeignKey('pkj_orders.id'), nullable=False, index=True)
    order_operation_id = db.Column(db.Integer, db.ForeignKey('pkj_order_operations.id'), nullable=True)
    inspector_id       = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    inspector_name     = db.Column(db.String(128), nullable=True)
    performer_id       = db.Column(db.Integer, db.ForeignKey('pkj_employees.id'), nullable=True)
    performer_name     = db.Column(db.String(128), nullable=True)
    started_at         = db.Column(db.DateTime, default=_now)
    finished_at        = db.Column(db.DateTime, nullable=True)
    status             = db.Column(db.String(12), default='w_toku', nullable=False)  # w_toku|zakonczona|przerwana
    result             = db.Column(db.String(4), nullable=True)                      # OK | NOK
    round              = db.Column(db.Integer, default=1, nullable=False)
    previous_id        = db.Column(db.Integer, db.ForeignKey('pkj_inspections.id'), nullable=True)


class PkjInspectionMaterial(db.Model):
    __tablename__ = 'pkj_inspection_materials'
    id                = db.Column(db.Integer, primary_key=True)
    inspection_id     = db.Column(db.Integer, db.ForeignKey('pkj_inspections.id'), nullable=False, index=True)
    order_material_id = db.Column(db.Integer, db.ForeignKey('pkj_order_materials.id'), nullable=True)
    name              = db.Column(db.String(128), nullable=False)
    verdict           = db.Column(db.String(4), nullable=True)   # OK | NOK
    interrupted       = db.Column(db.Boolean, default=False, nullable=False)


class PkjResult(db.Model):
    """Wynik punktu. Treść punktu jest kopią z chwili kontroli (A5), edycja punktów nie zmienia historii."""
    __tablename__ = 'pkj_results'
    id                     = db.Column(db.Integer, primary_key=True)
    inspection_id          = db.Column(db.Integer, db.ForeignKey('pkj_inspections.id'), nullable=False, index=True)
    inspection_material_id = db.Column(db.Integer, db.ForeignKey('pkj_inspection_materials.id'), nullable=True)
    point_id               = db.Column(db.Integer, db.ForeignKey('pkj_check_points.id'), nullable=True)
    position               = db.Column(db.Integer, default=0)
    section                = db.Column(db.String(128), nullable=True)
    title                  = db.Column(db.String(256), nullable=False)
    kind                   = db.Column(db.String(8), default='ok_nok')
    unit                   = db.Column(db.String(16), nullable=True)
    min_value              = db.Column(db.Float, nullable=True)
    max_value              = db.Column(db.Float, nullable=True)
    photo_required_on_nok  = db.Column(db.Boolean, default=False)
    result                 = db.Column(db.String(3), nullable=True)   # OK | NOK | ND
    value                  = db.Column(db.Float, nullable=True)
    notes                  = db.Column(db.Text, nullable=True)
    out_of_range           = db.Column(db.Boolean, default=False)


class PkjPhoto(db.Model):
    __tablename__ = 'pkj_photos'
    id         = db.Column(db.Integer, primary_key=True)
    result_id  = db.Column(db.Integer, db.ForeignKey('pkj_results.id'), nullable=False, index=True)
    filename   = db.Column(db.String(128), nullable=False)
    caption    = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, default=_now)


class PkjReturn(db.Model):
    """Zawrót wadliwego elementu. `repair_order_id` bez klucza obcego (cykl ze zleceniami)."""
    __tablename__ = 'pkj_returns'
    id                = db.Column(db.Integer, primary_key=True)
    inspection_id     = db.Column(db.Integer, db.ForeignKey('pkj_inspections.id'), nullable=True)
    order_material_id = db.Column(db.Integer, db.ForeignKey('pkj_order_materials.id'), nullable=True)
    result_id         = db.Column(db.Integer, db.ForeignKey('pkj_results.id'), nullable=True)
    reason_id         = db.Column(db.Integer, db.ForeignKey('pkj_reasons.id'), nullable=True)
    reason_name       = db.Column(db.String(128), nullable=True)
    description       = db.Column(db.Text, nullable=True)
    decision          = db.Column(db.String(8), default='poprawka', nullable=False)   # poprawka | zlom
    status            = db.Column(db.String(20), default='utworzony', nullable=False)
    repair_order_id   = db.Column(db.Integer, nullable=True)
    created_by        = db.Column(db.String(128), nullable=True)
    created_at        = db.Column(db.DateTime, default=_now)


class PkjReturnOperation(db.Model):
    __tablename__ = 'pkj_return_operations'
    id             = db.Column(db.Integer, primary_key=True)
    return_id      = db.Column(db.Integer, db.ForeignKey('pkj_returns.id'), nullable=False, index=True)
    operation_id   = db.Column(db.Integer, db.ForeignKey('pkj_operations.id'), nullable=True)
    operation_name = db.Column(db.String(128), nullable=False)
    position       = db.Column(db.Integer, default=0)
    added_manually = db.Column(db.Boolean, default=False, nullable=False)


class PkjEvent(db.Model):
    """Oś czasu zlecenia: meldunki, kontrole, zawroty, symulowane dokumenty RW i ZN."""
    __tablename__ = 'pkj_events'
    id       = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('pkj_orders.id'), nullable=False, index=True)
    ts       = db.Column(db.DateTime, default=_now)
    type     = db.Column(db.String(24), nullable=False)
    text     = db.Column(db.String(512), nullable=False)
    ref_type = db.Column(db.String(24), nullable=True)
    ref_id   = db.Column(db.Integer, nullable=True)
    actor    = db.Column(db.String(128), nullable=True)


# Tabele zleceń, od zależnych do głównych (kolejność kasowania przy resecie demo)
ORDER_TABLES_DELETE_ORDER = (PkjPhoto, PkjResult, PkjInspectionMaterial, PkjInspection, PkjReturnOperation,
                             PkjReturn, PkjEvent, PkjOrderMaterial, PkjOrderOperation, PkjOrder)
CONFIG_TABLES_DELETE_ORDER = (PkjCheckPoint, PkjMaterial, PkjMaterialGroup, PkjOperation, PkjReason,
                              PkjEmployee, PkjSetting)
