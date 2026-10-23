/* Przestawianie pozycji menu przeciąganiem (mysz i dotyk). Kolejność zapisuje się na koncie użytkownika
   (POST /menu/kolejnosc). Pozycje mają data-nav-key, kontener data-nav-sortable, a kolejność wizualna to CSS order. */
(function () {
  var container = document.querySelector('[data-nav-sortable]');
  var editBtns = document.querySelectorAll('[data-nav-edit]');
  var bar = document.getElementById('nav-edit-bar');
  if (!container) return;

  var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
  var editing = false;
  var items = [];          // pozycje w kolejności wizualnej
  var original = [];       // kolejność sprzed edycji (do anulowania)
  var drag = null;

  function collect() {
    var list = Array.prototype.slice.call(container.querySelectorAll(':scope > [data-nav-key]'));
    list.sort(function (a, b) { return (parseInt(a.style.order, 10) || 0) - (parseInt(b.style.order, 10) || 0); });
    return list;
  }

  function applyOrder() {
    items.forEach(function (el, i) { el.style.order = String(i + 1); });
  }

  function isHorizontal() {
    var cs = window.getComputedStyle(container);
    return cs.display.indexOf('flex') >= 0 && cs.flexDirection.indexOf('row') === 0;
  }

  /* Strzałki w górę i w dół przy każdej pozycji: pewna alternatywa dla przeciągania na telefonie */
  function addControls() {
    items.forEach(function (el) {
      el.setAttribute('draggable', 'false');   // linki są domyślnie przeciągalne, co na dotyku przerywa nasze przeciąganie
      if (el.querySelector('.nav-move')) return;
      var box = document.createElement('span');
      box.className = 'nav-move';
      box.innerHTML = '<button type="button" data-nav-move="up" aria-label="Przesuń w górę"><i class="bi bi-chevron-up"></i></button>' +
                      '<button type="button" data-nav-move="down" aria-label="Przesuń w dół"><i class="bi bi-chevron-down"></i></button>';
      el.appendChild(box);
    });
  }

  function removeControls() {
    container.querySelectorAll('.nav-move').forEach(function (n) { n.remove(); });
    items.forEach(function (el) { el.removeAttribute('draggable'); });
  }

  function move(el, dir) {
    var from = items.indexOf(el);
    var to = dir === 'up' ? from - 1 : from + 1;
    if (from < 0 || to < 0 || to >= items.length) return;
    items.splice(from, 1);
    items.splice(to, 0, el);
    applyOrder();
  }

  function setEditing(on) {
    editing = on;
    if (on) { addControls(); } else { removeControls(); }
    document.body.classList.toggle('nav-editing', on);
    if (bar) bar.hidden = !on;
    editBtns.forEach(function (b) { b.hidden = on; });
    items.forEach(function (el) {
      if (on) { el.setAttribute('aria-grabbed', 'false'); } else { el.removeAttribute('aria-grabbed'); }
    });
  }

  function start() {
    items = collect();
    original = items.slice();
    setEditing(true);
  }

  function cancel() {
    items = original.slice();
    applyOrder();
    setEditing(false);
  }

  function post(body) {
    return fetch('/menu/kolejnosc', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: JSON.stringify(body),
    }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); });
  }

  function save() {
    post({ order: items.map(function (el) { return el.dataset.navKey; }) }).then(function (res) {
      if (res.ok && res.d.ok) { setEditing(false); } else { alert(res.d.error || 'Nie udało się zapisać kolejności.'); }
    }).catch(function () { alert('Błąd sieci. Spróbuj ponownie.'); });
  }

  function reset() {
    post({ reset: true }).then(function (res) {
      if (res.ok && res.d.ok) { window.location.reload(); }
    }).catch(function () { alert('Błąd sieci. Spróbuj ponownie.'); });
  }

  editBtns.forEach(function (b) { b.addEventListener('click', start); });
  var btnSave = document.querySelector('[data-nav-save]');
  var btnReset = document.querySelector('[data-nav-reset]');
  var btnCancel = document.querySelector('[data-nav-cancel]');
  if (btnSave) btnSave.addEventListener('click', save);
  if (btnReset) btnReset.addEventListener('click', reset);
  if (btnCancel) btnCancel.addEventListener('click', cancel);

  // W trybie edycji kliknięcie pozycji nie nawiguje, a strzałki przesuwają pozycję.
  container.addEventListener('click', function (e) {
    if (!editing) return;
    var mv = e.target.closest('[data-nav-move]');
    if (mv) {
      var host = mv.closest('[data-nav-key]');
      if (host) { move(host, mv.getAttribute('data-nav-move')); }
      e.preventDefault(); e.stopPropagation();
      return;
    }
    if (e.target.closest('[data-nav-key]')) { e.preventDefault(); e.stopPropagation(); }
  }, true);

  // Na dotyku długie przytrzymanie linku otwiera menu kontekstowe albo zaczyna natywne przeciąganie, co kasuje gest.
  container.addEventListener('contextmenu', function (e) { if (editing) e.preventDefault(); });
  container.addEventListener('dragstart', function (e) { if (editing) e.preventDefault(); });
  // Starsze przeglądarki mobilne: nie przewijaj strony, gdy trwa przeciąganie pozycji.
  document.addEventListener('touchmove', function (e) { if (drag) e.preventDefault(); }, { passive: false });

  container.addEventListener('pointerdown', function (e) {
    if (!editing) return;
    if (e.target.closest('[data-nav-move]')) return;   // strzałki mają własną obsługę
    var el = e.target.closest('[data-nav-key]');
    if (!el || e.button > 0) return;
    drag = { el: el, id: e.pointerId };
    el.classList.add('nav-dragging');
    el.setAttribute('aria-grabbed', 'true');
    try { el.setPointerCapture(e.pointerId); } catch (err) {}
    e.preventDefault();
  });

  container.addEventListener('pointermove', function (e) {
    if (!drag || e.pointerId !== drag.id) return;
    var horizontal = isHorizontal();
    var pos = horizontal ? e.clientX : e.clientY;
    var from = items.indexOf(drag.el);
    var to = from;
    for (var i = 0; i < items.length; i++) {
      if (items[i] === drag.el) continue;
      var r = items[i].getBoundingClientRect();
      var mid = horizontal ? (r.left + r.width / 2) : (r.top + r.height / 2);
      if (i < from && pos < mid) { to = Math.min(to, i); }
      if (i > from && pos > mid) { to = Math.max(to, i); }
    }
    if (to !== from) {
      items.splice(from, 1);
      items.splice(to, 0, drag.el);
      applyOrder();
    }
  });

  function endDrag(e) {
    if (!drag || (e && e.pointerId !== drag.id)) return;
    drag.el.classList.remove('nav-dragging');
    drag.el.setAttribute('aria-grabbed', 'false');
    drag = null;
  }
  container.addEventListener('pointerup', endDrag);
  container.addEventListener('pointercancel', endDrag);

  // Wejście z ustawień konta (?edytuj-menu=1): od razu otwórz menu w trybie przestawiania.
  var params = new URLSearchParams(window.location.search);
  if (params.get('edytuj-menu') === '1') {
    var burger = document.getElementById('burger');
    if (burger && window.getComputedStyle(burger).display !== 'none') { burger.click(); }  // telefon: rozwiń menu
    start();
    params.delete('edytuj-menu');
    var q = params.toString();
    window.history.replaceState(null, '', window.location.pathname + (q ? '?' + q : '') + window.location.hash);
  }
})();
