"""Raporty KJ Prestiż: ekran z filtrami, wykres i tabelą, eksport do Excela oraz karta kontroli zlecenia (ekran i PDF)."""
import os

from flask import abort, current_app, make_response, redirect, render_template, request, url_for
from flask_login import login_required

from models import db
from . import prestiz_kj_bp
from .access import view_required
from .card_pdf import order_card_pdf
from .models import PkjOrder
from . import reports_data as rd
from .repairs import DECISIONS, RETURN_STATUS_LABELS
from .service import upload_folder
from .xlsx import report_workbook

XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


@prestiz_kj_bp.route('/raporty')
@login_required
@view_required
def reports_index():
    return render_template('prestiz_kj/raporty.html', reports=rd.REPORTS, orders=rd.cardable_orders()[:60])


@prestiz_kj_bp.route('/raporty/<slug>')
@login_required
@view_required
def report_page(slug):
    if slug not in rd.REPORTS:
        abort(404)
    report = rd.build_report(slug, request.args)
    query = rd.filters_to_query(report['filters'])
    if report.get('axis_param'):
        query[report['axis_param']] = report['axis']
    return render_template('prestiz_kj/raport.html', slug=slug, report=report, query=query,
                           options=rd.filter_options(), fmt=rd.format_cell, icon=rd.REPORTS[slug][1], zip_cols=zip)


@prestiz_kj_bp.route('/raporty/<slug>/xlsx')
@login_required
@view_required
def report_xlsx(slug):
    if slug not in rd.REPORTS:
        abort(404)
    report = rd.build_report(slug, request.args)
    resp = make_response(report_workbook(report))
    resp.headers['Content-Type'] = XLSX_MIME
    resp.headers['Content-Disposition'] = f'attachment; filename=kj-prestiz-{slug}.xlsx'
    return resp


def _card_order(order_id):
    order = db.session.get(PkjOrder, order_id) or abort(404)
    return order


@prestiz_kj_bp.route('/raporty/karta/<int:order_id>')
@login_required
@view_required
def report_card(order_id):
    order = _card_order(order_id)
    if order.type == 'ZN' and order.parent_id:
        return redirect(url_for('prestiz_kj.report_card', order_id=order.parent_id))
    return render_template('prestiz_kj/karta.html', card=rd.card_data(order), decisions=DECISIONS,
                           labels=RETURN_STATUS_LABELS, fmt=rd.format_cell)


@prestiz_kj_bp.route('/raporty/karta/<int:order_id>.pdf')
@login_required
@view_required
def report_card_pdf(order_id):
    order = _card_order(order_id)
    if order.type == 'ZN' and order.parent_id:
        order = _card_order(order.parent_id)
    resp = make_response(order_card_pdf(rd.card_data(order), upload_folder()))
    resp.headers['Content-Type'] = 'application/pdf'
    resp.headers['Content-Disposition'] = f'inline; filename=karta-kontroli-{order.number}.pdf'
    return resp
