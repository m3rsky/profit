/* PSH QC – frontend logic */
const _csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';

// ── Service Worker ─────────────────────────────────────────────────────────
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js', { scope: '/' })
      .then(reg => {
        reg.addEventListener('updatefound', () => {
          const newSW = reg.installing;
          newSW.addEventListener('statechange', () => {
            if (newSW.state === 'installed' && navigator.serviceWorker.controller) {
              showUpdateToast();
            }
          });
        });
      })
      .catch(() => {});
  });
}

function showUpdateToast() {
  const toast = document.createElement('div');
  toast.className = 'alert alert-info';
  toast.style.cssText = 'position:fixed;bottom:16px;left:50%;transform:translateX(-50%);z-index:9999;white-space:nowrap;box-shadow:0 4px 12px rgba(0,0,0,.2)';
  toast.innerHTML = 'Dostępna aktualizacja. <button onclick="location.reload()" style="margin-left:8px;font-weight:700;background:none;border:none;cursor:pointer;color:inherit;text-decoration:underline">Odśwież</button>';
  document.body.appendChild(toast);
}

// ── PWA Install prompt ─────────────────────────────────────────────────────
let _installPrompt = null;
const installBanner  = document.getElementById('install-banner');
const btnInstall     = document.getElementById('btn-install');
const btnDismiss     = document.getElementById('btn-install-dismiss');

window.addEventListener('beforeinstallprompt', e => {
  e.preventDefault();
  _installPrompt = e;
  if (installBanner && !sessionStorage.getItem('install-dismissed')) {
    installBanner.hidden = false;
  }
});

if (btnInstall) {
  btnInstall.addEventListener('click', async () => {
    if (!_installPrompt) return;
    const { outcome } = await _installPrompt.prompt();
    _installPrompt = null;
    if (installBanner) installBanner.hidden = true;
  });
}

if (btnDismiss) {
  btnDismiss.addEventListener('click', () => {
    if (installBanner) installBanner.hidden = true;
    sessionStorage.setItem('install-dismissed', '1');
  });
}

window.addEventListener('appinstalled', () => {
  if (installBanner) installBanner.hidden = true;
  _installPrompt = null;
});

// ── Burger menu ────────────────────────────────────────────────────────────
const burger = document.getElementById('burger');
const nav    = document.getElementById('topbar-nav');
if (burger && nav) {
  burger.addEventListener('click', () => {
    const open = nav.classList.toggle('open');
    burger.setAttribute('aria-expanded', open);
  });
  document.addEventListener('click', e => {
    if (!burger.contains(e.target) && !nav.contains(e.target)) {
      nav.classList.remove('open');
      burger.setAttribute('aria-expanded', 'false');
    }
  });
}

// ── Flash auto-dismiss ─────────────────────────────────────────────────────
document.querySelectorAll('.alert[data-autodismiss]').forEach(el => {
  setTimeout(() => el.remove(), 4000);
});

// ── Progress bars — set width from data-pct attribute ─────────────────────
document.querySelectorAll('.progress-bar[data-pct]').forEach(el => {
  el.style.width = el.dataset.pct + '%';
});

// ── OK / NG / N/A result buttons ───────────────────────────────────────────
let pendingNgRow = null;

document.querySelectorAll('.btn-result').forEach(btn => {
  btn.addEventListener('click', async function () {
    const itemId  = this.dataset.itemId;
    const clicked = this.dataset.result;
    const row     = this.closest('.checklist-item');
    const current = row.querySelector('.btn-result.active')?.dataset.result ?? null;
    const newResult = current === clicked ? null : clicked;

    // Blokada: inne zadanie NG czeka na adnotację + zdjęcie
    if (pendingNgRow && pendingNgRow !== row) {
      const pNotes    = pendingNgRow.querySelector('.item-notes');
      const pHasPhoto = !!pendingNgRow.querySelector('.photo-thumb');
      if (pNotes && pNotes.value.trim() && pHasPhoto) {
        _clearNgNote(pendingNgRow); // oba warunki spełnione — odblokuj
      } else {
        _requireNgNote(pendingNgRow);
        return;
      }
    }

    try {
      const res = await fetch(`/api/item/${itemId}/result`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': _csrf() },
        body: JSON.stringify({ result: newResult }),
      });
      const data = await res.json();
      if (!res.ok) { alert(data.error || 'Błąd'); return; }
      row.querySelectorAll('.btn-result').forEach(b => b.classList.remove('active'));
      if (data.result) row.querySelector(`.btn-result[data-result="${data.result}"]`)?.classList.add('active');
      row.classList.toggle('result-ok',  data.result === 'ok');
      row.classList.toggle('result-ng',  data.result === 'ng');
      row.classList.toggle('result-na',  data.result === 'na');
      row.classList.toggle('result-dw', data.result === 'dw');
      const badge = row.querySelector('.value-result-badge');
      if (badge) {
        badge.textContent = data.result ? data.result.toUpperCase() : '';
        badge.className = 'value-result-badge' +
          (data.result === 'ok' ? ' badge-success' : data.result === 'ng' ? ' badge-error' : '');
      }
      updateProgress(data.progress, data.stats);
      if (data.result === 'ng') {
        const notes    = row.querySelector('.item-notes');
        const hasPhoto = !!row.querySelector('.photo-thumb');
        if (!notes || !notes.value.trim() || !hasPhoto) {
          _requireNgNote(row);
          return;
        }
        scrollToNextItem(row);
      } else if (data.result === 'dw') {
        if (pendingNgRow === row) _clearNgNote(row);
        const notes = row.querySelector('.item-notes');
        if (notes) {
          const y = notes.getBoundingClientRect().top + window.scrollY - 80;
          window.scrollTo({ top: y, behavior: 'smooth' });
          setTimeout(() => { notes.focus(); notes.style.borderColor = '#d97706'; }, 400);
          notes.addEventListener('blur', () => { notes.style.borderColor = ''; }, { once: true });
        }
      } else if (newResult !== null) {
        if (pendingNgRow === row) _clearNgNote(row);
        scrollToNextItem(row);
      } else {
        if (pendingNgRow === row) _clearNgNote(row);
      }
    } catch {
      alert('Błąd połączenia');
    }
  });
});

function scrollToNextItem(currentRow) {
  const items = Array.from(document.querySelectorAll('.checklist-item'));
  const idx   = items.indexOf(currentRow);
  // Find next item without a result
  for (let i = idx + 1; i < items.length; i++) {
    if (!items[i].querySelector('.btn-result.active')) {
      scrollToItem(items[i]);
      return;
    }
  }
  // All remaining done — scroll to the one right after current
  if (idx + 1 < items.length) scrollToItem(items[idx + 1]);
}

function scrollToItem(item) {
  const topOffset = 72; // clearance for sticky header / bottom bar
  const y = item.getBoundingClientRect().top + window.scrollY - topOffset;
  window.scrollTo({ top: y, behavior: 'smooth' });
}

function _requireNgNote(row) {
  pendingNgRow = row;
  row.classList.add('ng-note-required', 'ng-photo-required');
  if (!row.querySelector('.ng-note-warn')) {
    const warn = document.createElement('p');
    warn.className = 'ng-note-warn';
    const notes = row.querySelector('.item-notes');
    if (notes) notes.insertAdjacentElement('beforebegin', warn);
  }
  _updateNgWarn(row);
  const notes = row.querySelector('.item-notes');
  if (!notes) return;
  const y = notes.getBoundingClientRect().top + window.scrollY - 80;
  window.scrollTo({ top: y, behavior: 'smooth' });
  setTimeout(() => notes.focus(), 400);
}

function _updateNgWarn(row) {
  const warn = row.querySelector('.ng-note-warn');
  if (!warn) return;
  const hasNote  = !!(row.querySelector('.item-notes')?.value.trim());
  const hasPhoto = !!row.querySelector('.photo-thumb');
  let msg;
  if (!hasNote && !hasPhoto) msg = 'Wymagane: adnotacja i zdjęcie przy wyniku NG';
  else if (!hasNote)          msg = 'Wpisz adnotację, aby przejść dalej';
  else                        msg = 'Dodaj zdjęcie, aby przejść dalej';
  warn.innerHTML = '<i class="bi bi-exclamation-circle-fill"></i> ' + msg;
}

function _checkNgUnlock(row) {
  if (!pendingNgRow || pendingNgRow !== row) return;
  const hasNote  = !!(row.querySelector('.item-notes')?.value.trim());
  const hasPhoto = !!row.querySelector('.photo-thumb');
  if (hasNote && hasPhoto) {
    _clearNgNote(row);
    scrollToNextItem(row);
  } else {
    _updateNgWarn(row);
  }
}

function _clearNgNote(row) {
  pendingNgRow = null;
  row.classList.remove('ng-note-required', 'ng-photo-required');
  row.querySelector('.ng-note-warn')?.remove();
}

// ── Lightbox ───────────────────────────────────────────────────────────────
const lightbox = document.createElement('div');
lightbox.className = 'lightbox';
lightbox.innerHTML = '<button type="button" class="lightbox-close" aria-label="Zamknij">×</button><img src="" alt="">';
document.body.appendChild(lightbox);
const lbImg = lightbox.querySelector('img');

document.addEventListener('click', e => {
  const img = e.target.closest('.photo-zoomable');
  if (img) { lbImg.src = img.dataset.src || img.src; lightbox.classList.add('open'); }
  if (e.target === lightbox || e.target.classList.contains('lightbox-close')) {
    lightbox.classList.remove('open');
  }
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') lightbox.classList.remove('open');
});

function updateProgress(percent, stats) {
  const bar   = document.querySelector('.progress-bar');
  const label = document.getElementById('progress-pct');
  if (bar)   bar.style.width = percent + '%';
  if (label) label.textContent = percent + '%';
  if (stats) {
    document.querySelectorAll('.rs-ok').forEach(el => el.textContent = 'OK: ' + stats.ok);
    document.querySelectorAll('.rs-ng').forEach(el => el.textContent = 'NG: ' + stats.ng);
    document.querySelectorAll('.rs-na').forEach(el => el.textContent = 'N/A: ' + stats.na);
    document.querySelectorAll('.rs-dw').forEach(el => el.textContent = 'DW: ' + (stats.dw || 0));
  }
}

// ── Photo upload ───────────────────────────────────────────────────────────
document.querySelectorAll('.upload-zone').forEach(zone => {
  const input = zone.querySelector('input[type=file]');
  const itemId = zone.dataset.itemId;
  const thumbsContainer = document.querySelector(`.photo-thumbs[data-item-id="${itemId}"]`);

  zone.addEventListener('click', () => input.click());
  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
  zone.addEventListener('drop', e => {
    e.preventDefault();
    zone.classList.remove('drag-over');
    if (e.dataTransfer.files.length) uploadFile(e.dataTransfer.files[0], itemId, thumbsContainer);
  });

  input.addEventListener('change', () => {
    if (input.files.length) uploadFile(input.files[0], itemId, thumbsContainer);
    input.value = '';
  });
});

async function uploadFile(file, itemId, thumbsContainer) {
  const form = new FormData();
  form.append('photo', file);
  try {
    form.append('_csrf_token', _csrf());
    const res  = await fetch(`/api/item/${itemId}/photo`, { method: 'POST', body: form });
    const data = await res.json();
    if (!res.ok) { alert(data.error || 'Błąd przesyłania'); return; }
    if (thumbsContainer) {
      addThumb(thumbsContainer, data.photo_id, data.url);
      const ngRow = thumbsContainer.closest('.checklist-item');
      if (ngRow) _checkNgUnlock(ngRow);
    }
  } catch {
    alert('Błąd przesyłania zdjęcia');
  }
}

function addThumb(container, photoId, url) {
  const div = document.createElement('div');
  div.className = 'photo-thumb';
  div.dataset.photoId = photoId;
  div.innerHTML = `
    <img src="${url}" alt="zdjęcie">
    <button class="delete-photo" title="Usuń" onclick="deletePhoto(${photoId}, this)">×</button>
  `;
  container.appendChild(div);
}

async function deletePhoto(photoId, btn) {
  if (!confirm('Usunąć zdjęcie?')) return;
  try {
    const res = await fetch(`/api/photo/${photoId}`, {
      method: 'DELETE', headers: { 'X-CSRF-Token': _csrf() }
    });
    if (res.ok) btn.closest('.photo-thumb').remove();
  } catch {
    alert('Błąd usuwania zdjęcia');
  }
}

// ── Confirm delete ─────────────────────────────────────────────────────────
document.querySelectorAll('[data-confirm]').forEach(el => {
  el.addEventListener('click', e => {
    if (!confirm(el.dataset.confirm)) e.preventDefault();
  });
});

// ── Numeric / text value inputs ────────────────────────────────────────────
let valueTimer = null;

async function saveValue(input, allowAdvance) {
  const itemId = input.dataset.itemId;
  const row = input.closest('.checklist-item');
  const badge = input.closest('.value-row')?.querySelector('.value-result-badge');
  try {
    const res = await fetch(`/api/item/${itemId}/value`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': _csrf() },
      body: JSON.stringify({ value: input.value }),
    });
    const data = await res.json();
    if (!res.ok) return;
    if (row) {
      row.classList.toggle('result-ok', data.result === 'ok');
      row.classList.toggle('result-ng', data.result === 'ng');
      row.classList.remove('result-na');
    }
    if (badge) {
      badge.textContent = data.result ? data.result.toUpperCase() : '';
      badge.className = 'value-result-badge' +
        (data.result === 'ok' ? ' badge-success' : data.result === 'ng' ? ' badge-error' : '');
    }
    if (data.progress !== undefined) updateProgress(data.progress, data.stats);

    // Zadania numeryczne nie maja przyciskow OK/NG — wynik jest wyliczany
    // automatycznie z zakresu min/max. Poprawna wartosc ma przenosic dalej,
    // tak samo jak klikniecie OK — ale dopiero gdy uzytkownik skonczyl
    // wpisywanie (blur / Enter), nie przy kazdym auto-zapisie w trakcie pisania.
    const isAutoResult = row && !row.querySelector('.result-buttons');
    if (allowAdvance && isAutoResult && data.result === 'ok') {
      if (pendingNgRow === row) _clearNgNote(row);
      scrollToNextItem(row);
    }
  } catch { /* silent */ }
}

document.querySelectorAll('.value-input').forEach(input => {
  input.addEventListener('input', function () {
    clearTimeout(valueTimer);
    valueTimer = setTimeout(() => saveValue(this, false), 700);
  });
  input.addEventListener('blur', function () {
    clearTimeout(valueTimer);
    saveValue(this, true);
  });
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); clearTimeout(valueTimer); saveValue(this, true); }
  });
});

// ── Measurements inputs ────────────────────────────────────────────────────
let measurementsTimer = null;

async function saveMeasurements(anyInput) {
  const itemId = anyInput.dataset.itemId;
  const inputs = document.querySelectorAll(`.measurement-input[data-item-id="${itemId}"]`);
  const value = Array.from(inputs).map(i => i.value.trim()).join('|');
  try {
    const res = await fetch(`/api/item/${itemId}/value`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': _csrf() },
      body: JSON.stringify({ value }),
    });
    const data = await res.json();
    if (!res.ok) return;
    if (data.progress !== undefined) updateProgress(data.progress, data.stats);
  } catch { /* silent */ }
}

document.querySelectorAll('.measurement-input').forEach(input => {
  input.addEventListener('input', function () {
    clearTimeout(measurementsTimer);
    measurementsTimer = setTimeout(() => saveMeasurements(this), 700);
  });
  input.addEventListener('blur', function () {
    clearTimeout(measurementsTimer);
    saveMeasurements(this);
  });
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); clearTimeout(measurementsTimer); saveMeasurements(this); }
  });
});

// ── Notes autosave ─────────────────────────────────────────────────────────
let notesTimer = null;
document.querySelectorAll('.item-notes').forEach(textarea => {
  textarea.addEventListener('input', function () {
    clearTimeout(notesTimer);
    notesTimer = setTimeout(async () => {
      const itemId = this.dataset.itemId;
      await fetch(`/api/item/${itemId}/notes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': _csrf() },
        body: JSON.stringify({ notes: this.value }),
      });
    }, 900);
  });
  textarea.addEventListener('blur', function () {
    if (pendingNgRow && pendingNgRow.contains(this) && this.value.trim()) {
      _checkNgUnlock(pendingNgRow);
    }
  });
});

// ── Reports bulk select ────────────────────────────────────────────────────
const headerCheck = document.getElementById('select-all');
const rowChecks   = document.querySelectorAll('.row-check');
const bulkCount   = document.getElementById('bulk-count');
const btnSelected = document.getElementById('btn-delete-selected');
const btnSelAll   = document.getElementById('btn-select-all');

function updateBar() {
  const n = document.querySelectorAll('.row-check:checked').length;
  if (bulkCount)   bulkCount.textContent = n + ' zaznaczonych';
  if (btnSelected) btnSelected.disabled = n === 0;
  const bar = document.getElementById('bulk-bar');
  if (bar) bar.classList.toggle('bulk-bar-active', n > 0);
  if (headerCheck) {
    headerCheck.indeterminate = n > 0 && n < rowChecks.length;
    headerCheck.checked = rowChecks.length > 0 && n === rowChecks.length;
  }
  if (btnSelAll) {
    const allChecked = rowChecks.length > 0 && n === rowChecks.length;
    btnSelAll.textContent = allChecked ? 'Odznacz wszystkie' : 'Zaznacz wszystkie';
  }
}

function toggleSelectAll() {
  const n = document.querySelectorAll('.row-check:checked').length;
  const allChecked = n === rowChecks.length && rowChecks.length > 0;
  rowChecks.forEach(cb => cb.checked = !allChecked);
  if (headerCheck) headerCheck.checked = !allChecked;
  updateBar();
}

if (headerCheck) headerCheck.addEventListener('change', () => {
  rowChecks.forEach(cb => cb.checked = headerCheck.checked);
  updateBar();
});
rowChecks.forEach(cb => cb.addEventListener('change', updateBar));

function selectOnly(id) {
  rowChecks.forEach(cb => { cb.checked = String(cb.value) === String(id); });
}
