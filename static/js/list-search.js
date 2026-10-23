/* Wspólne wyszukiwanie list (STANDARD_LIST.md): wysuwanie filtrów na telefonie i szukanie skanem kodu QR.
   Markup pochodzi z makra templates/_list_search.html (klasy .ls-*, atrybuty data-ls-toggle i data-ls-qr). */
(function () {
  /* ── Przycisk "Filtry": wysuwa i chowa dodatkowe opcje (telefon) ── */
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-ls-toggle]');
    if (!btn) return;
    var box = document.getElementById(btn.getAttribute('data-ls-toggle'));
    if (!box) return;
    var open = box.classList.toggle('open');
    btn.setAttribute('aria-expanded', String(open));
  });

  /* ── Szukanie skanem QR: kod zlecenia (pole "o", a gdy brak, "p") trafia do pola szukania ── */
  var modal = null, msg = null, reader = null, running = false, activeInput = null;
  var DEFAULT_MSG = 'Skieruj kamerę na kod QR umieszczony na dokumencie lub produkcie.';

  function say(text, error) {
    if (!msg) return;
    msg.textContent = text;
    msg.classList.toggle('error', !!error);
  }

  function build() {
    if (modal) return;
    modal = document.createElement('div');
    modal.className = 'ls-qr-modal';
    modal.hidden = true;
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('aria-label', 'Skanowanie kodu QR do wyszukania');
    modal.innerHTML =
      '<div class="ls-qr-box">' +
      '<div class="ls-qr-head"><span><i class="bi bi-qr-code-scan"></i> Skanuj kod QR zlecenia</span>' +
      '<button type="button" class="btn btn-ghost btn-sm" data-ls-qr-close aria-label="Zamknij"><i class="bi bi-x-lg"></i></button></div>' +
      '<div id="ls-qr-reader"></div><div class="ls-qr-msg"></div></div>';
    document.body.appendChild(modal);
    msg = modal.querySelector('.ls-qr-msg');
    modal.addEventListener('click', function (e) {
      if (e.target === modal || e.target.closest('[data-ls-qr-close]')) close();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && modal && !modal.hidden) close();
    });
    window.addEventListener('beforeunload', stop);
  }

  function loadLib(cb) {
    if (window.Html5Qrcode) { cb(); return; }
    var sc = document.createElement('script');
    sc.src = 'https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js';
    sc.onload = cb;
    sc.onerror = function () { say('Nie udało się załadować skanera. Sprawdź połączenie.', true); };
    document.head.appendChild(sc);
  }

  function stop() {
    if (reader && running) { reader.stop().catch(function () {}); }
    running = false;
  }

  function close() { stop(); if (modal) modal.hidden = true; say(DEFAULT_MSG, false); }

  function valueFrom(text) {
    text = (typeof repairPolishText === 'function' ? repairPolishText(text.trim()) : text.trim());
    try {
      var obj = JSON.parse(text);
      if (obj && typeof obj === 'object') { return String(obj.o || obj.p || '').trim(); }
    } catch (err) {}
    return text;
  }

  function onScan(text) {
    stop();
    var value = valueFrom(text);
    if (!value) { say('Nie rozpoznano kodu QR. Spróbuj ponownie.', true); start(); return; }
    activeInput.value = value;
    modal.hidden = true;
    activeInput.form.submit();
  }

  function start() {
    loadLib(function () {
      reader = new Html5Qrcode('ls-qr-reader');
      Html5Qrcode.getCameras().then(function (cams) {
        if (!cams || !cams.length) { say('Brak dostępnej kamery w urządzeniu.', true); return; }
        reader.start(cams[cams.length - 1].id, { fps: 10, qrbox: { width: 240, height: 240 } }, onScan, function () {})
          .then(function () { running = true; })
          .catch(function (err) { say('Nie można uruchomić kamery: ' + err, true); });
      }).catch(function () { say('Brak dostępu do kamery. Sprawdź uprawnienia przeglądarki.', true); });
    });
  }

  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-ls-qr]');
    if (!btn) return;
    var row = btn.closest('.ls-row');
    activeInput = row ? row.querySelector('.ls-input') : null;
    if (!activeInput) return;
    build();
    modal.hidden = false;
    say(DEFAULT_MSG, false);
    start();
  });
})();
