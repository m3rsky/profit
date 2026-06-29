"""
Silnik kalkulatora kosztorysów szaf elektrycznych.

Wzorzec oparty na arkuszu: Kalkulacja szablon PSH spawany tył.xlsx
Gestosc stali przyjeta jako 8.0 kg/m2/mm (z marginesem ~2% vs 7.85).
"""

STEEL_DENSITY = 8.0   # kg/m2/mm
WASTE_PCT     = 0.15  # 15% odpadu materialowego


def _element(name, qty, L, W, thickness, material='DC01', has_paint=True,
             unit='szt', prices=None):
    """Oblicza dane jednego elementu blaszanego."""
    prices = prices or {}
    if qty == 0:
        return {
            'name': name, 'qty': qty, 'unit': unit, 'material': material,
            'L': L, 'W': W, 'area': 0.0, 'thickness': thickness,
            'weight_per': 0.0, 'weight_total': 0.0,
            'cost_sheet': 0.0, 'cost_paint': 0.0,
            'cost_per': 0.0, 'cost_total': 0.0,
        }

    area    = L * W / 1_000_000             # m2
    weight  = area * thickness * STEEL_DENSITY  # kg (per piece)
    w_total = weight * qty

    if material == 'DC01':
        c_sheet = weight * prices.get('dc01', 4.0)
        c_paint = (area * prices.get('paint', 18.0)) if has_paint else 0.0
    elif material == 'DX51':
        c_sheet = weight * prices.get('dx51', 4.6)
        c_paint = 0.0
    elif material == 'INOX304':
        c_sheet = weight * prices.get('inox304', 18.0)
        c_paint = 0.0
    elif material == 'INOX316':
        c_sheet = weight * prices.get('inox316', 25.0)
        c_paint = 0.0
    else:
        c_sheet = weight * prices.get('dc01', 4.0)
        c_paint = 0.0

    c_per   = c_sheet + c_paint
    c_total = c_per * qty

    return {
        'name': name, 'qty': qty, 'unit': unit, 'material': material,
        'L': L, 'W': W,
        'area':         round(area, 2),
        'thickness':    thickness,
        'weight_per':   round(weight, 2),
        'weight_total': round(w_total, 2),
        'cost_sheet':   round(c_sheet, 2),
        'cost_paint':   round(c_paint, 2),
        'cost_per':     round(c_per, 2),
        'cost_total':   round(c_total, 2),
    }


def _finalize(elements, waste_cost, hw, svc, cfg) -> dict:
    """Oblicza sumy i ceny sprzedazy."""
    elements_cost = round(sum(e['cost_total'] for e in elements), 2)
    hw_cost       = round(sum(h['total'] for h in hw), 2)
    svc_cost      = round(sum(s['total'] for s in svc), 2)
    cost_total    = round(elements_cost + waste_cost + hw_cost + svc_cost, 2)

    margin        = cfg.get('margin', 2.15)
    discount_pct  = cfg.get('discount_pct', 0.0)
    bonus_pct     = cfg.get('bonus_pct', 0.0)

    price_catalog  = round(cost_total * margin, 2)
    price_discount = round(price_catalog * (1 - discount_pct / 100), 2)
    price_bonus    = round(price_discount * (1 - bonus_pct / 100), 2)

    profitability  = (round((price_bonus - cost_total) / price_bonus * 100, 2)
                      if price_bonus > 0 else 0.0)
    return {
        'elements_cost':  elements_cost,
        'waste_cost':     waste_cost,
        'hardware_cost':  hw_cost,
        'services_cost':  svc_cost,
        'cost_total':     cost_total,
        'margin':         margin,
        'price_catalog':  price_catalog,
        'discount_pct':   discount_pct,
        'price_discount': price_discount,
        'bonus_pct':      bonus_pct,
        'price_bonus':    price_bonus,
        'profitability':  profitability,
    }


def _waste_and_labor(elements, lr, prices):
    """Oblicza odpad i koszty robocizny (wspólny kod dla PSH IP65 / Kompakt)."""
    total_weight = sum(e['weight_total'] for e in elements)
    dc01_sheet   = sum(e['cost_sheet'] for e in elements
                       if e['material'] == 'DC01' and e['qty'] > 0)
    waste_weight = round(total_weight * WASTE_PCT, 2)
    waste_cost   = round(dc01_sheet * WASTE_PCT, 2)
    waste = {
        'name': f'Odpad {int(WASTE_PCT*100)}%', 'qty': 1, 'unit': 'szt',
        'material': 'DC01',
        'weight_total': waste_weight,
        'cost_total':   waste_cost,
    }
    labor_total = (lr.get('laser', 0) + lr.get('bending', 0) + lr.get('welding', 0) +
                   lr.get('grinding', 0) + lr.get('assembly', 0) + lr.get('packaging', 0))
    return waste, waste_cost, labor_total


def _standard_hardware(cfg, prices) -> list:
    """Elementy osprzętu wspólne dla PSH IP65 i Kompakt."""
    seal = prices.get('seal', 11.0)
    hw = []

    if cfg.get('has_mounting_plate'):
        qty_m6 = cfg.get('stud_m6_qty', 0)
        if qty_m6 > 0:
            hw.append({'name': 'Trzpień wstrzeliwany M6', 'qty': qty_m6, 'unit': 'szt',
                       'unit_price': prices.get('stud_m6', 0.20),
                       'total': round(qty_m6 * prices.get('stud_m6', 0.20), 2)})
        qty_m8 = cfg.get('nut_m8_qty', 0)
        if qty_m8 > 0:
            hw.append({'name': 'Nakrętka z podkładką M8', 'qty': qty_m8, 'unit': 'szt',
                       'unit_price': prices.get('nut_m8', 0.05),
                       'total': round(qty_m8 * prices.get('nut_m8', 0.05), 2)})

    n_cap = cfg.get('cable_entries', 0)
    if n_cap:
        qty_screw = cfg.get('screw_cap_qty', 0)
        if qty_screw > 0:
            hw.append({'name': 'Komplet śrub do kap', 'qty': qty_screw, 'unit': 'szt',
                       'unit_price': prices.get('screw_cap', 0.05),
                       'total': round(qty_screw * prices.get('screw_cap', 0.05), 2)})
        qty_plug = cfg.get('plug_qty', 0)
        if qty_plug > 0:
            hw.append({'name': 'Zaślepka otworów', 'qty': qty_plug, 'unit': 'szt',
                       'unit_price': prices.get('plug', 0.16),
                       'total': round(qty_plug * prices.get('plug', 0.16), 2)})

    W = cfg.get('width', 0)
    H = cfg.get('height', 0)
    if cfg.get('door_single'):
        sl = 2 * (H + W) / 1000
        hw.append({'name': 'Uszczelka drzwi pojedyncze', 'qty': 1, 'unit': 'kpl',
                   'unit_price': round(sl * seal, 2),
                   'total': round(sl * seal, 2)})
    if cfg.get('door_double'):
        sl = 2 * (2 * H + W) / 1000
        hw.append({'name': 'Uszczelka drzwi podwójne', 'qty': 1, 'unit': 'kpl',
                   'unit_price': round(sl * seal, 2),
                   'total': round(sl * seal, 2)})
    if n_cap:
        cap_sl = 2 * (300 + 150) / 1000
        hw.append({'name': 'Uszczelka kap', 'qty': n_cap, 'unit': 'kpl',
                   'unit_price': round(cap_sl * seal, 2),
                   'total': round(n_cap * cap_sl * seal, 2)})

    hinge_qty = cfg.get('hinge_qty', 0)
    if hinge_qty > 0:
        hw.append({'name': 'Zawias', 'qty': hinge_qty, 'unit': 'szt',
                   'unit_price': prices.get('hinge', 6.0),
                   'total': round(hinge_qty * prices.get('hinge', 6.0), 2)})

    if cfg.get('back_seal'):
        back_sl = 2 * (H + W) / 1000
        hw.append({'name': 'Uszczelka tyłu', 'qty': 1, 'unit': 'kpl',
                   'unit_price': round(back_sl * seal, 2),
                   'total': round(back_sl * seal, 2)})
    if cfg.get('lock_three_point'):
        hw.append({'name': 'Zamek trzypunktowy', 'qty': 1, 'unit': 'szt',
                   'unit_price': prices.get('lock_3pt', 50.0),
                   'total': prices.get('lock_3pt', 50.0)})
    if cfg.get('lock_cam'):
        hw.append({'name': 'Zamek krzywkowy', 'qty': 1, 'unit': 'szt',
                   'unit_price': prices.get('lock_cam', 5.0),
                   'total': prices.get('lock_cam', 5.0)})
    if cfg.get('lock_standard'):
        hw.append({'name': 'Zamek zwykły', 'qty': 1, 'unit': 'szt',
                   'unit_price': prices.get('lock_standard', 5.0),
                   'total': prices.get('lock_standard', 5.0)})
    return hw


def _standard_services(cfg, prices, labor_total) -> list:
    """Pozycje usługowe wspólne dla PSH IP65 i Kompakt."""
    svc = []
    svc.append({'name': 'Robocizna', 'qty': 1, 'unit': 'kpl',
                'unit_price': round(labor_total, 2),
                'total': round(labor_total, 2)})

    if cfg.get('non_standard_color'):
        svc.append({'name': 'Kolor niestandardowy', 'qty': 1, 'unit': 'szt',
                    'unit_price': prices.get('custom_color', 100.0),
                    'total': prices.get('custom_color', 100.0)})

    dh = cfg.get('design_hours', 0)
    if dh:
        dph = prices.get('design_hour', 200.0)
        svc.append({'name': 'Koszt projektowania', 'qty': dh, 'unit': 'h',
                    'unit_price': dph, 'total': round(dh * dph, 2)})

    svc.append({'name': 'Opakowanie', 'qty': 1, 'unit': 'szt',
                'unit_price': prices.get('packaging', 2.0),
                'total': prices.get('packaging', 2.0)})
    svc.append({'name': 'Naklejki komplet', 'qty': 1, 'unit': 'kpl',
                'unit_price': prices.get('labels', 2.0),
                'total': prices.get('labels', 2.0)})
    svc.append({'name': 'Transport', 'qty': 1, 'unit': 'szt',
                'unit_price': prices.get('transport', 100.0),
                'total': prices.get('transport', 100.0)})
    svc.append({'name': 'Koszty stałe', 'qty': 1, 'unit': 'szt',
                'unit_price': prices.get('fixed_costs', 50.0),
                'total': prices.get('fixed_costs', 50.0)})
    return svc


# ─────────────────────────────────────────────────────────────────────────────
#  PSH IP65 – szafa stojąca stalowa
# ─────────────────────────────────────────────────────────────────────────────

def calculate_psh_ip65(cfg: dict, prices: dict, labor_rates: dict) -> dict:
    """Kalkulacja kosztów szafy PSH IP65 (stojąca, spawana lub przykręcana)."""
    W  = cfg['width']
    H  = cfg['height']
    D  = cfg['depth']
    tb = cfg.get('thickness_body', 1.2)
    tp = cfg.get('thickness_plate', 3.0)

    volume = round(W * H * D / 1_000_000, 0)
    if volume <= 400:
        lr_key = 'do400'
    elif volume <= 900:
        lr_key = 'do900'
    else:
        lr_key = 'pow900'

    lr = labor_rates.get(lr_key, {})
    p  = prices

    mono = cfg.get('monoblok', 0)   # Monoblok zastępuje Boki + Góra/dół
    mono_L = H + 2 * (D + 25)
    mono_W = W + 2 * (D + 25)

    elements = []
    elements.append(_element('Monoblok',          mono,     mono_L, mono_W, tb, 'DC01', True, 'szt', p))
    elements.append(_element('Boki',              0 if mono else 2, H, D + 50, tb, 'DC01', True, 'szt', p))
    elements.append(_element('Góra/dół',          0 if mono else 2, W, D + 50, tb, 'DC01', True, 'szt', p))
    elements.append(_element('Tył spawany',
                             1 if cfg.get('back_welded') else 0,
                             H, W, tb, 'DC01', True, 'szt', p))
    elements.append(_element('Tył przykręcany',
                             1 if cfg.get('back_screwed') else 0,
                             H, W, tb, 'DC01', True, 'szt', p))
    elements.append(_element('Wzmocnienie tył',
                             1 if cfg.get('back_welded') else 0,
                             H - 50, 60, 1.5, 'DC01', True, 'szt', p))
    elements.append(_element('Drzwi pojedyncze',
                             1 if cfg.get('door_single') else 0,
                             H + 50, W + 50, tb, 'DC01', True, 'szt', p))
    dbl = 1 if cfg.get('door_double') else 0
    elements.append(_element('Drzwi prawe', dbl, H + 50, W // 2 + 50, tb, 'DC01', True, 'szt', p))
    elements.append(_element('Drzwi lewe',  dbl, H + 50, W // 2 + 50, tb, 'DC01', True, 'szt', p))
    elements.append(_element('Wzmocnienie drzwi',
                             cfg.get('door_reinforcement', 0),
                             H - 50, 60, 1.5, 'DC01', True, 'szt', p))
    elements.append(_element('Belka pionowa IP65',
                             cfg.get('vertical_beam', 0),
                             H + 50, 120, tb, 'DC01', True, 'szt', p))
    elements.append(_element('Kapa na przewody',
                             cfg.get('cable_entries', 0),
                             300, 150, 1.5, 'DC01', True, 'szt', p))
    plinth_L = 2 * (W + D)
    elements.append(_element('Cokół',
                             1 if cfg.get('plinth') else 0,
                             plinth_L, 150, 2.0, 'DC01', True, 'kpl', p))
    elements.append(_element('Płyta montażowa',
                             1 if cfg.get('has_mounting_plate') else 0,
                             H - 50, W - 50, tp, 'DX51', False, 'szt', p))
    elements.append(_element('Prowadnice do płyty',
                             6 if cfg.get('has_mounting_plate') else 0,
                             550, 120, 2.0, 'DX51', False, 'szt', p))
    elements.append(_element('Daszek',
                             1 if cfg.get('canopy') else 0,
                             W + 200, W + 50, tb, 'DC01', True, 'szt', p))

    waste, waste_cost, labor_total = _waste_and_labor(elements, lr, p)
    hw  = _standard_hardware(cfg, p)
    svc = _standard_services(cfg, p, labor_total)

    totals = _finalize(elements, waste_cost, hw, svc, cfg)
    return {
        'volume':       volume,
        'volume_range': lr_key,
        'labor_detail': lr,
        'labor_total':  round(labor_total, 2),
        'elements':     elements,
        'waste':        waste,
        'hardware':     hw,
        'services':     svc,
        **totals,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  PSH Kompakt – mała szafa ścienna
# ─────────────────────────────────────────────────────────────────────────────

def calculate_psh_compact(cfg: dict, prices: dict, labor_rates: dict) -> dict:
    """Kalkulacja kosztów szafy kompaktowej ściennej PSH IP65."""
    W  = cfg['width']
    H  = cfg['height']
    D  = cfg['depth']
    tb = cfg.get('thickness_body', 1.2)
    tp = cfg.get('thickness_plate', 3.0)

    volume = round(W * H * D / 1_000_000, 0)
    # Kompaktowe szafy ścienne są zawsze małe – zawsze do400
    lr_key = 'do400'
    lr = labor_rates.get(lr_key, {})
    p  = prices

    elements = []
    elements.append(_element('Boki',             2, H, D + 30,      tb, 'DC01', True, 'szt', p))
    elements.append(_element('Góra/dół',         2, W, D + 30,      tb, 'DC01', True, 'szt', p))
    elements.append(_element('Tył',
                             1 if cfg.get('back_welded') or cfg.get('back_screwed') else 1,
                             H, W, tb, 'DC01', True, 'szt', p))
    elements.append(_element('Drzwi',
                             1 if cfg.get('door_single') else 0,
                             H + 30, W + 30, tb, 'DC01', True, 'szt', p))
    elements.append(_element('Płyta montażowa',
                             1 if cfg.get('has_mounting_plate') else 0,
                             H - 30, W - 30, tp, 'DX51', False, 'szt', p))
    elements.append(_element('Kapa na przewody',
                             cfg.get('cable_entries', 0),
                             200, 100, 1.2, 'DC01', True, 'szt', p))

    waste, waste_cost, labor_total = _waste_and_labor(elements, lr, p)
    hw  = _standard_hardware(cfg, p)
    svc = _standard_services(cfg, p, labor_total)

    totals = _finalize(elements, waste_cost, hw, svc, cfg)
    return {
        'volume':       volume,
        'volume_range': lr_key,
        'labor_detail': lr,
        'labor_total':  round(labor_total, 2),
        'elements':     elements,
        'waste':        waste,
        'hardware':     hw,
        'services':     svc,
        **totals,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  PSH INOX – szafa ze stali nierdzewnej
# ─────────────────────────────────────────────────────────────────────────────

def calculate_psh_inox(cfg: dict, prices: dict, labor_rates: dict) -> dict:
    """Kalkulacja kosztów szafy INOX 304 (brak malowania, wyższe koszty rob.)."""
    W  = cfg['width']
    H  = cfg['height']
    D  = cfg['depth']
    tb = cfg.get('thickness_body', 1.5)   # INOX standardowo grubszy
    tp = cfg.get('thickness_plate', 3.0)

    volume = round(W * H * D / 1_000_000, 0)
    if volume <= 400:
        lr_key = 'do400'
    elif volume <= 900:
        lr_key = 'do900'
    else:
        lr_key = 'pow900'

    lr = labor_rates.get(lr_key, {})
    p  = prices
    # Gatunek INOX: '304' (domyślnie) lub '316'
    grade = cfg.get('inox_grade', '304')
    mat   = 'INOX316' if grade == '316' else 'INOX304'
    factor_key = 'inox316_labor_factor' if grade == '316' else 'inox_labor_factor'
    factor = prices.get(factor_key, 1.6 if grade == '316' else 1.4)
    labor_total = (lr.get('laser', 0) + lr.get('bending', 0) +
                   lr.get('welding', 0) * factor +
                   lr.get('grinding', 0) * factor +
                   lr.get('assembly', 0) + lr.get('packaging', 0))

    elements = []
    elements.append(_element('Boki',            2, H, D + 50,       tb, mat, False, 'szt', p))
    elements.append(_element('Góra/dół',        2, W, D + 50,       tb, mat, False, 'szt', p))
    elements.append(_element('Tył',
                             1 if cfg.get('back_welded') or cfg.get('back_screwed') else 0,
                             H, W, tb, mat, False, 'szt', p))
    elements.append(_element('Wzmocnienie tył',
                             1 if cfg.get('back_welded') else 0,
                             H - 50, 60, 1.5, mat, False, 'szt', p))
    elements.append(_element('Drzwi pojedyncze',
                             1 if cfg.get('door_single') else 0,
                             H + 50, W + 50, tb, mat, False, 'szt', p))
    dbl = 1 if cfg.get('door_double') else 0
    elements.append(_element('Drzwi prawe', dbl, H + 50, W // 2 + 50, tb, mat, False, 'szt', p))
    elements.append(_element('Drzwi lewe',  dbl, H + 50, W // 2 + 50, tb, mat, False, 'szt', p))
    elements.append(_element('Kapa na przewody',
                             cfg.get('cable_entries', 0),
                             300, 150, 1.5, mat, False, 'szt', p))
    plinth_L = 2 * (W + D)
    elements.append(_element('Cokól',
                             1 if cfg.get('plinth') else 0,
                             plinth_L, 150, 2.0, mat, False, 'kpl', p))
    elements.append(_element('Płyta montażowa',
                             1 if cfg.get('has_mounting_plate') else 0,
                             H - 50, W - 50, tp, 'DX51', False, 'szt', p))

    # Odpad INOX (liczymy od blachy wybranego gatunku)
    total_weight = sum(e['weight_total'] for e in elements)
    inox_sheet   = sum(e['cost_sheet'] for e in elements
                       if e['material'] == mat and e['qty'] > 0)
    waste_weight = round(total_weight * WASTE_PCT, 2)
    waste_cost   = round(inox_sheet * WASTE_PCT, 2)
    waste = {
        'name': f'Odpad {int(WASTE_PCT*100)}%', 'qty': 1, 'unit': 'szt',
        'material': mat,
        'weight_total': waste_weight,
        'cost_total':   waste_cost,
    }

    hw = []
    if cfg.get('has_mounting_plate'):
        qty_m6 = cfg.get('stud_m6_qty', 0)
        if qty_m6 > 0:
            hw.append({'name': 'Trzpień wstrzeliwany M6', 'qty': qty_m6, 'unit': 'szt',
                       'unit_price': p.get('stud_m6', 0.20),
                       'total': round(qty_m6 * p.get('stud_m6', 0.20), 2)})
    seal = p.get('seal', 11.0)
    if cfg.get('door_single'):
        sl = 2 * (H + W) / 1000
        hw.append({'name': 'Uszczelka drzwi', 'qty': 1, 'unit': 'kpl',
                   'unit_price': round(sl * seal, 2), 'total': round(sl * seal, 2)})
    hinge_qty = cfg.get('hinge_qty', 0)
    if hinge_qty > 0:
        hw.append({'name': 'Zawias INOX', 'qty': hinge_qty, 'unit': 'szt',
                   'unit_price': p.get('hinge', 6.0),
                   'total': round(hinge_qty * p.get('hinge', 6.0), 2)})
    if cfg.get('lock_three_point'):
        hw.append({'name': 'Zamek trzypunktowy', 'qty': 1, 'unit': 'szt',
                   'unit_price': p.get('lock_3pt', 50.0), 'total': p.get('lock_3pt', 50.0)})
    if cfg.get('lock_cam'):
        hw.append({'name': 'Zamek krzywkowy', 'qty': 1, 'unit': 'szt',
                   'unit_price': p.get('lock_cam', 5.0), 'total': p.get('lock_cam', 5.0)})

    svc = []
    svc.append({'name': 'Robocizna (INOX)', 'qty': 1, 'unit': 'kpl',
                'unit_price': round(labor_total, 2), 'total': round(labor_total, 2)})
    dh = cfg.get('design_hours', 0)
    if dh:
        dph = p.get('design_hour', 200.0)
        svc.append({'name': 'Koszt projektowania', 'qty': dh, 'unit': 'h',
                    'unit_price': dph, 'total': round(dh * dph, 2)})
    svc.append({'name': 'Opakowanie', 'qty': 1, 'unit': 'szt',
                'unit_price': p.get('packaging', 2.0), 'total': p.get('packaging', 2.0)})
    svc.append({'name': 'Naklejki komplet', 'qty': 1, 'unit': 'kpl',
                'unit_price': p.get('labels', 2.0), 'total': p.get('labels', 2.0)})
    svc.append({'name': 'Transport', 'qty': 1, 'unit': 'szt',
                'unit_price': p.get('transport', 100.0), 'total': p.get('transport', 100.0)})
    svc.append({'name': 'Koszty stałe', 'qty': 1, 'unit': 'szt',
                'unit_price': p.get('fixed_costs', 50.0), 'total': p.get('fixed_costs', 50.0)})

    totals = _finalize(elements, waste_cost, hw, svc, cfg)
    return {
        'volume':       volume,
        'volume_range': lr_key,
        'labor_detail': lr,
        'labor_total':  round(labor_total, 2),
        'elements':     elements,
        'waste':        waste,
        'hardware':     hw,
        'services':     svc,
        **totals,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  PSH Modular – szafa z szynami DIN
# ─────────────────────────────────────────────────────────────────────────────

def calculate_psh_modular(cfg: dict, prices: dict, labor_rates: dict) -> dict:
    """Kalkulacja kosztów szafy modularnej z szynami DIN."""
    W  = cfg['width']
    H  = cfg['height']
    D  = cfg['depth']
    tb = cfg.get('thickness_body', 1.2)

    volume = round(W * H * D / 1_000_000, 0)
    if volume <= 400:
        lr_key = 'do400'
    elif volume <= 900:
        lr_key = 'do900'
    else:
        lr_key = 'pow900'

    lr = labor_rates.get(lr_key, {})
    p  = prices

    elements = []
    elements.append(_element('Boki',       2, H, D + 50,  tb,  'DC01', True, 'szt', p))
    elements.append(_element('Góra/dół',   2, W, D + 50,  tb,  'DC01', True, 'szt', p))
    elements.append(_element('Tył',
                             1 if cfg.get('back_welded') or cfg.get('back_screwed') else 1,
                             H, W, tb, 'DC01', True, 'szt', p))
    elements.append(_element('Drzwi pojedyncze',
                             1 if cfg.get('door_single') else 0,
                             H + 50, W + 50, tb, 'DC01', True, 'szt', p))
    dbl = 1 if cfg.get('door_double') else 0
    elements.append(_element('Drzwi prawe', dbl, H + 50, W // 2 + 50, tb, 'DC01', True, 'szt', p))
    elements.append(_element('Drzwi lewe',  dbl, H + 50, W // 2 + 50, tb, 'DC01', True, 'szt', p))
    elements.append(_element('Kapa na przewody',
                             cfg.get('cable_entries', 0),
                             300, 150, 1.5, 'DC01', True, 'szt', p))
    plinth_L = 2 * (W + D)
    elements.append(_element('Cokół',
                             1 if cfg.get('plinth') else 0,
                             plinth_L, 150, 2.0, 'DC01', True, 'kpl', p))

    waste, waste_cost, labor_total = _waste_and_labor(elements, lr, p)

    # Szyny DIN: ile rzędów × długość (szerokość szafy w metrach)
    din_rows = cfg.get('vertical_beam', 0)  # 'vertical_beam' = rzędy szyn
    hw = []
    if din_rows > 0:
        din_m = din_rows * W / 1000
        hw.append({'name': 'Szyna DIN 35mm', 'qty': din_rows, 'unit': 'szt',
                   'unit_price': round(W / 1000 * p.get('din_rail', 5.0), 2),
                   'total': round(din_m * p.get('din_rail', 5.0), 2)})

    hw += _standard_hardware(cfg, p)

    svc = _standard_services(cfg, p, labor_total)

    totals = _finalize(elements, waste_cost, hw, svc, cfg)
    return {
        'volume':       volume,
        'volume_range': lr_key,
        'inox_grade':   grade,
        'labor_detail': lr,
        'labor_total':  round(labor_total, 2),
        'elements':     elements,
        'waste':        waste,
        'hardware':     hw,
        'services':     svc,
        **totals,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Dispatcher
# ─────────────────────────────────────────────────────────────────────────────

_CALCULATORS = {
    'PSH_IP65':    calculate_psh_ip65,
    'PSH_COMPACT': calculate_psh_compact,
    'PSH_INOX':    calculate_psh_inox,
    'PSH_MODULAR': calculate_psh_modular,
}


def calculate(family_code: str, cfg: dict, prices: dict, labor_rates: dict) -> dict:
    """Wybiera i uruchamia kalkulator na podstawie kodu rodziny szafy."""
    fn = _CALCULATORS.get(family_code, calculate_psh_ip65)
    return fn(cfg, prices, labor_rates)


def get_prices_dict(material_prices) -> dict:
    """Konwertuje liste obiektow MaterialPrice na slownik {code: price}."""
    return {mp.code: mp.price for mp in material_prices}


def get_labor_dict(labor_rates) -> dict:
    """Konwertuje liste obiektow LaborRate na slownik {range: {field: value}}."""
    result = {}
    for lr in labor_rates:
        result[lr.volume_range] = {
            'laser':    lr.laser,
            'bending':  lr.bending,
            'welding':  lr.welding,
            'grinding': lr.grinding,
            'assembly': lr.assembly,
            'packaging': lr.packaging,
        }
    return result
