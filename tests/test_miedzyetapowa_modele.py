"""Kontrola międzyetapowa, etap 1: modele danych."""
from models import (db, ControlOrder, ProductionStage, Buffer, StageReport, BufferEntry,
                    Nonconformity, ReworkItem, RemakeItem, DefectCause, BufferTransport)


def test_przeplyw_modeli(app):
    with app.app_context():
        order = ControlOrder(zo_number='ZO-MET-1', product_name='PSH 600x800', quantity=10)
        stage = ProductionStage(name='Giecie test', order=99)
        buf = Buffer(code='ZZ901')
        cause = DefectCause(name='Wymiar test')
        db.session.add_all([order, stage, buf, cause])
        db.session.flush()

        rep = StageReport(control_order_id=order.id, stage_id=stage.id, stage_name=stage.name,
                          employee_name='Jan K.', qty_ok=9, qty_ng=1)
        db.session.add(rep)
        db.session.flush()
        entry = BufferEntry(buffer_id=buf.id, control_order_id=order.id, report_id=rep.id, qty=9)
        nc = Nonconformity(control_order_id=order.id, report_id=rep.id, qty=1, cause_id=cause.id,
                           cause_name=cause.name, employee_name='Jan K.')
        db.session.add_all([entry, nc])
        db.session.flush()
        rw = ReworkItem(nonconformity_id=nc.id, control_order_id=order.id, qty=1, stage_id=stage.id)
        rm = RemakeItem(control_order_id=order.id, nonconformity_id=nc.id, qty=1, reason='zlom')
        db.session.add_all([rw, rm, BufferTransport(buffer_id=buf.id)])
        db.session.commit()

        assert entry.check_status == 'czeka' and entry.is_active_entry
        assert nc.status == 'do_weryfikacji'
        assert rw.status == 'do_poprawy' and rm.status == 'otwarte'
        assert order.stage_reports.count() == 1
        assert buf.entries.count() == 1
        assert order.remake_items.count() == 1
