"""Wysyłka poczty (SMTP) i powiadomienia e-mail.

Dane konta są w `.env` (POST_NAME, POST_PASS, SERV_SMTP, SERV_PORT, patrz `config.py`), a włączenie
wysyłki, adresaci i limit na godzinę w tabeli `app_settings` (edytuje je superuser w /admin/poczta).
Bez włączenia i adresatów żadna wiadomość nie wychodzi. Błąd poczty nigdy nie przerywa pracy aplikacji:
każde zdarzenie kończy się wpisem w Dzienniku zdarzeń (`mail_sent`, `mail_failed`).
"""
import re
import smtplib
import ssl
import threading
from datetime import datetime, timedelta, UTC
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from zoneinfo import ZoneInfo

from flask import current_app, request
from sqlalchemy.exc import IntegrityError

from models import db, AppSetting, AuditLog, Report, SentNotice

EMAIL_RE = re.compile(r'^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$')
SETTING_DEFAULTS = {'mail_enabled': '0', 'mail_report_done': '0', 'mail_recipients': '', 'mail_hourly_limit': '30'}
MAX_RECIPIENTS = 10
LOCAL_TZ = ZoneInfo('Europe/Warsaw')


# ── Ustawienia ────────────────────────────────────────────────────────────────

def get_settings():
    s = {k: AppSetting.get(k, v) for k, v in SETTING_DEFAULTS.items()}
    try:
        limit = int(s['mail_hourly_limit'])
    except ValueError:
        limit = 30
    return {
        'enabled': s['mail_enabled'] == '1',
        'report_done': s['mail_report_done'] == '1',
        'recipients': parse_recipients(s['mail_recipients']),
        'hourly_limit': limit,
    }


def parse_recipients(text):
    """Lista poprawnych adresów z tekstu rozdzielonego przecinkami, średnikami lub nowymi liniami."""
    seen, out = set(), []
    for part in re.split(r'[,;\s]+', text or ''):
        part = part.strip()
        if part and EMAIL_RE.match(part) and part.lower() not in seen:
            seen.add(part.lower())
            out.append(part)
    return out


def save_settings(form):
    """Zapisuje ustawienia z formularza. Zwraca listę błędów (puste: zapisano)."""
    errors = []
    raw = form.get('mail_recipients', '')
    tokens = [t for t in re.split(r'[,;\s]+', raw) if t]
    bad = [t for t in tokens if not EMAIL_RE.match(t)]
    if bad:
        errors.append('Niepoprawny adres: ' + ', '.join(bad[:3]))
    recipients = parse_recipients(raw)
    if len(recipients) > MAX_RECIPIENTS:
        errors.append(f'Najwyżej {MAX_RECIPIENTS} adresatów.')
    joined = ', '.join(recipients)
    if len(joined) > 250:
        errors.append('Lista adresów jest za długa.')
    try:
        limit = int(form.get('mail_hourly_limit', ''))
        if not 1 <= limit <= 500:
            raise ValueError
    except ValueError:
        errors.append('Limit wiadomości na godzinę: liczba od 1 do 500.')
        limit = None
    if errors:
        return errors
    AppSetting.set('mail_enabled', '1' if form.get('mail_enabled') else '0')
    AppSetting.set('mail_report_done', '1' if form.get('mail_report_done') else '0')
    AppSetting.set('mail_recipients', joined)
    AppSetting.set('mail_hourly_limit', str(limit))
    db.session.commit()
    return []


# ── Wysyłka ───────────────────────────────────────────────────────────────────

def is_configured(cfg=None):
    cfg = cfg or current_app.config
    return bool(cfg.get('MAIL_USER') and cfg.get('MAIL_PASSWORD') and cfg.get('MAIL_SMTP_HOST'))


def _audit(action, detail):
    db.session.add(AuditLog(user_id=None, action=action, target_type='mail', detail=detail[:500]))
    db.session.commit()


def sent_last_hour():
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    return AuditLog.query.filter(AuditLog.action == 'mail_sent', AuditLog.created_at >= since).count()


def _build(subject, body, recipients, user, from_name):
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = formataddr((from_name, user))
    msg['To'] = ', '.join(recipients)
    msg['Date'] = formatdate(localtime=True)
    msg['Message-ID'] = make_msgid(domain=user.split('@')[-1])
    msg.set_content(body)
    return msg


def _smtp_send(msg, cfg):
    host, port = cfg['MAIL_SMTP_HOST'], cfg['MAIL_PORT']
    ctx = ssl.create_default_context()
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=20, context=ctx)
    else:
        server = smtplib.SMTP(host, port, timeout=20)
        server.ehlo()
        server.starttls(context=ctx)
        server.ehlo()
    try:
        server.login(cfg['MAIL_USER'], cfg['MAIL_PASSWORD'])
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass


def _deliver(app, subject, body, recipients):
    """Wysyła wiadomość i zapisuje wynik w Dzienniku zdarzeń. Zwraca (ok, błąd)."""
    with app.app_context():
        cfg = app.config
        try:
            msg = _build(subject, body, recipients, cfg['MAIL_USER'],
                         AppSetting.get('mail_from_name', 'PSH QC'))
            _smtp_send(msg, cfg)
        except Exception as exc:
            err = f'{type(exc).__name__}: {exc}'[:200]
            app.logger.error('Mail: błąd wysyłki (%s)', err)
            try:
                _audit('mail_failed', f'{subject[:120]} | {err}')
            except Exception:
                db.session.rollback()
            return False, err
        try:
            _audit('mail_sent', f'{subject[:120]} | adresatów: {len(recipients)}')
        except Exception:
            db.session.rollback()
        return True, None


def send(subject, body, recipients, wait=False, ignore_switch=False):
    """Wysyła wiadomość do `recipients`. Domyślnie w tle (żeby nie spowalniać żądania), z `wait=True`
    czeka na wynik. Zwraca (ok, komunikat). Nigdy nie zgłasza wyjątku."""
    try:
        app = current_app._get_current_object()
        if not is_configured():
            return False, 'Konto pocztowe nie jest skonfigurowane w .env na tym serwerze.'
        s = get_settings()
        if not s['enabled'] and not ignore_switch:
            return False, 'Wysyłka jest wyłączona w ustawieniach poczty.'
        if not recipients:
            return False, 'Brak adresatów.'
        if sent_last_hour() >= s['hourly_limit']:
            _audit('mail_failed', f'{subject[:120]} | limit {s["hourly_limit"]} wiadomości na godzinę')
            return False, 'Przekroczono limit wiadomości na godzinę.'
        if wait or app.testing:
            ok, err = _deliver(app, subject, body, recipients)
            return ok, (err or 'Wysłano.')
        threading.Thread(target=_deliver, args=(app, subject, body, recipients), daemon=True).start()
        return True, 'Wysyłanie rozpoczęte.'
    except Exception as exc:
        current_app.logger.error('Mail: nieoczekiwany błąd (%s)', exc)
        db.session.rollback()
        return False, f'{type(exc).__name__}: {exc}'[:200]


# ── Powiadomienie: ukończenie kontroli ────────────────────────────────────────

def _report_link(report_id):
    root = request.url_root.rstrip('/')
    if root.startswith('http://') and not re.match(r'http://(localhost|127\.|192\.168\.)', root):
        root = 'https://' + root[len('http://'):]
    return f'{root}/reports/{report_id}'


def _parts(report):
    """(produkt, klient, ZO) z klucza QR listy, a bez niego z tytułu."""
    p, c, z = ((report.qr_key or '').split('|') + ['', '', ''])[:3]
    return (p.strip() or report.title or '-'), (c.strip() or '-'), (z.strip() or '-')


def notify_report_done(report):
    """Jedna wiadomość o ukończeniu całej serii kontroli (albo pojedynczej listy bez serii).
    Wysyła się dopiero, gdy ostatnia lista serii jest zakończona i tylko raz."""
    try:
        s = get_settings()
        if not (s['enabled'] and s['report_done'] and s['recipients']):
            return False
        if report.status != 'completed':
            return False
        if report.batch_id:
            reports = Report.query.filter_by(batch_id=report.batch_id, report_type='kontroler').all()
            if any(r.status != 'completed' for r in reports):
                return False
            key, qty, last = f'report_done:{report.batch_id}', len(reports), report
        else:
            key, qty, last = f'report_done:r{report.id}', 1, report
        if db.session.get(SentNotice, key):
            return False
        db.session.add(SentNotice(key=key))
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return False
        product, client, zo = _parts(last)
        done = last.completed_at or datetime.now(UTC)
        if done.tzinfo is None:
            done = done.replace(tzinfo=UTC)
        controller = last.author.username if getattr(last, 'author', None) else ''
        subject = f'Ukończono kontrolę: {client}, ZO {zo}, {product} ({qty} szt.)'
        lines = ['Kontrola jakości została ukończona.', '',
                 f'Klient: {client}', f'Numer ZO: {zo}', f'Produkt: {product}', f'Ilość: {qty} szt.']
        if controller:
            lines.append(f'Kontroler: {controller}')
        lines += [f'Ukończono: {done.astimezone(LOCAL_TZ).strftime("%d.%m.%Y %H:%M")}',
                  f'Raport: {_report_link(last.id)}']
        ok, _msg = send(subject, '\n'.join(lines), s['recipients'])
        return ok
    except Exception as exc:
        current_app.logger.error('Mail: powiadomienie o ukończeniu kontroli nie powiodło się (%s)', exc)
        db.session.rollback()
        return False
