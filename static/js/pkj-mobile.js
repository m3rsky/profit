/* Aplikacja KJ (moduł KJ Prestiż): autozapis oceny punktu, wartości, uwag, zdjęcia i wykonawcy.
   Każde dotknięcie zapisuje się od razu, więc przerwana praca nie gubi ocen. */
(function () {
  var csrf = function () { var m = document.querySelector('meta[name="csrf-token"]'); return m ? m.content : ''; };

  function send(url, body, isForm) {
    var opts = { method: 'POST', headers: { 'X-CSRF-Token': csrf() }, credentials: 'same-origin' };
    if (isForm) { opts.body = body; }
    else { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    return fetch(url, opts).then(function (r) { return r.json().catch(function () { return { ok: false, error: 'Błąd serwera.' }; }); });
  }

  function toast(text) {
    var t = document.getElementById('m-toast');
    if (!t) { t = document.createElement('div'); t.id = 'm-toast'; t.className = 'm-toast'; document.body.appendChild(t); }
    t.textContent = text; t.hidden = false;
    clearTimeout(t._h); t._h = setTimeout(function () { t.hidden = true; }, 3500);
  }

  function updateProgress(data) {
    if (data.done === undefined) return;
    var done = document.getElementById('m-done'), bar = document.getElementById('m-bar');
    if (done) done.textContent = data.done;
    if (bar) bar.style.width = (data.total ? Math.round(100 * data.done / data.total) : 0) + '%';
  }

  function applyResult(card, data) {
    card.querySelectorAll('.m-b').forEach(function (b) { b.classList.toggle('on', b.dataset.v === data.result); });
    card.classList.toggle('m-pt--range', !!data.out_of_range);
    var warn = card.querySelector('.m-range-warn');
    if (warn) warn.hidden = !data.out_of_range;
    var more = card.querySelector('.m-more');
    if (more && data.result === 'NOK') more.open = true;
    updateProgress(data);
  }

  function save(card, body) {
    return send(card.dataset.url, body).then(function (data) {
      if (!data.ok) { toast(data.error || 'Nie udało się zapisać.'); return data; }
      applyResult(card, data);
      return data;
    }).catch(function () { toast('Brak połączenia. Spróbuj ponownie.'); });
  }

  /* OK / NOK / ND: ponowne dotknięcie aktywnego przycisku czyści ocenę */
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('.m-b');
    if (!btn) return;
    var card = btn.closest('.m-pt');
    save(card, { result: btn.classList.contains('on') ? '' : btn.dataset.v });
  });

  /* Wartość mierzona: zapis po zakończeniu wpisywania, ocena ustawia się sama (poza zakresem = NOK) */
  document.addEventListener('change', function (e) {
    var input = e.target.closest('.m-val-input');
    if (input) { save(input.closest('.m-pt'), { value: input.value }); return; }
    var notes = e.target.closest('.m-notes');
    if (notes) { save(notes.closest('.m-pt'), { notes: notes.value }); return; }
    var perf = e.target.closest('#m-performer');
    if (perf) {
      send(perf.dataset.performerUrl, { employee_id: perf.value }).then(function (d) {
        toast(d.ok ? 'Zmieniono wykonawcę.' : (d.error || 'Nie udało się zapisać.'));
      });
      return;
    }
    var file = e.target.closest('.m-photo-btn input[type=file]');
    if (file && file.files && file.files[0]) {
      var card = file.closest('.m-pt'), form = new FormData();
      form.append('photo', file.files[0]);
      toast('Wysyłanie zdjęcia…');
      send(card.dataset.photoUrl, form, true).then(function (d) {
        file.value = '';
        if (!d.ok) { toast(d.error || 'Nie udało się wysłać zdjęcia.'); return; }
        var span = document.createElement('span');
        span.className = 'm-ph'; span.dataset.pid = d.photo_id;
        span.dataset.del = d.delete_url;
        span.innerHTML = '<a target="_blank"><img alt="Zdjęcie"></a><button type="button" class="m-ph-del" aria-label="Usuń zdjęcie">&times;</button>';
        span.querySelector('a').href = d.url; span.querySelector('img').src = d.url;
        card.querySelector('.m-photos').appendChild(span);
        toast('Dodano zdjęcie.');
      }).catch(function () { toast('Brak połączenia. Spróbuj ponownie.'); });
    }
  });

  document.addEventListener('click', function (e) {
    var del = e.target.closest('.m-ph-del');
    if (!del) return;
    var ph = del.closest('.m-ph');
    if (!confirm('Usunąć zdjęcie?')) return;
    send(ph.dataset.del, {}).then(function (d) {
      if (d.ok) ph.remove(); else toast(d.error || 'Nie udało się usunąć zdjęcia.');
    });
  });
})();
