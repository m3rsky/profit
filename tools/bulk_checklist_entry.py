"""
Zewnętrzny program do zbiorczego wpisywania list kontrolnych do bazy PROFIT.

Do czego służy
---------------
Pozwala szybko "dopisać" do systemu serię list kontrolnych, które zostały
wypełnione ręcznie poza aplikacją (np. na papierze) — tak, jakby kontrola
została faktycznie przeprowadzona: każda lista trafia do bazy ze statusem
"zakończona", wszystkie punkty odhaczone jako OK, z podaną datą/godziną
kontroli.

Przykład użycia: 30 identycznych list kontrolnych tego samego produktu dla
klienta "X" — podajesz szablon, tytuł, ilość w serii, operatora i datę,
program tworzy wszystkie 30 wpisów jednym kliknięciem.

Jak to działa
-------------
Program nie łączy się bezpośrednio z bazą danych — rozmawia z serwerem
PROFIT przez istniejące REST API (`/api/v1/checklists`), zabezpieczone
kluczem API (nagłówek X-API-Key, patrz app.py: `api_key_required`). Dzięki
temu każdy wpis przechodzi przez tę samą walidację co reszta systemu i trafia
do dziennika audytu (AuditLog, akcja `api_create_checklist`).

Wymagania
---------
Tylko standardowa biblioteka Pythona (tkinter + urllib) — nie trzeba nic
instalować poza samym Pythonem 3.

Uruchomienie
------------
    python bulk_checklist_entry.py

Konfiguracja (adres serwera + klucz API) zapisywana jest lokalnie w
    ~/.profit_bulk_checklist_config.json
żeby nie wpisywać jej przy każdym uruchomieniu. Plik nie jest częścią
repozytorium (zawiera sekret) — nie kopiuj go między komputerami bez potrzeby.
"""
import json
import os
import ssl
import tkinter as tk
import urllib.error
import urllib.request
from datetime import datetime
from tkinter import messagebox, ttk

CONFIG_PATH = os.path.join(os.path.expanduser('~'), '.profit_bulk_checklist_config.json')


# ── Komunikacja z API ──────────────────────────────────────────────────────────

class ApiError(Exception):
    pass


def api_request(server_url, api_key, method, path, payload=None):
    url = server_url.rstrip('/') + path
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('X-API-Key', api_key)
    if data is not None:
        req.add_header('Content-Type', 'application/json')
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
            body = resp.read().decode('utf-8')
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf-8', errors='replace')
        try:
            detail = json.loads(body).get('error', body)
        except json.JSONDecodeError:
            detail = body or exc.reason
        raise ApiError(f'{exc.code}: {detail}') from exc
    except urllib.error.URLError as exc:
        raise ApiError(f'Nie można połączyć się z serwerem ({exc.reason})') from exc


# ── Konfiguracja lokalna ───────────────────────────────────────────────────────

def load_config():
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_config(server_url, api_key):
    try:
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump({'server_url': server_url, 'api_key': api_key}, f)
    except OSError:
        pass


# ── Aplikacja ──────────────────────────────────────────────────────────────────

class BulkChecklistApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('PROFIT – zbiorczy wpis list kontrolnych')
        self.resizable(False, False)
        self.templates = []   # [{'id', 'name', 'type', 'task_count'}]
        self.operators = []   # [{'id', 'username', 'role'}]
        self.orders = []      # [{'id', 'number', 'client', 'product_name', ...}]
        self.installers = []  # [{'id', 'name'}]
        self.chosen_installers = []  # [{'name', 'role'}] dodani do bieżącej serii

        cfg = load_config()
        self._build_connection_frame(cfg)
        self._build_form_frame()
        self._build_installers_frame()
        self._build_actions_frame()
        self._set_form_state('disabled')

        if cfg.get('server_url') and cfg.get('api_key'):
            self.after(200, self.connect)

    # -- Sekcja połączenia -----------------------------------------------------

    def _build_connection_frame(self, cfg):
        frame = ttk.LabelFrame(self, text='Połączenie z serwerem PROFIT')
        frame.grid(row=0, column=0, padx=10, pady=(10, 5), sticky='ew')

        ttk.Label(frame, text='Adres serwera:').grid(row=0, column=0, sticky='e', padx=5, pady=4)
        self.var_server = tk.StringVar(value=cfg.get('server_url', 'http://localhost:5000'))
        ttk.Entry(frame, textvariable=self.var_server, width=42).grid(row=0, column=1, padx=5, pady=4)

        ttk.Label(frame, text='Klucz API:').grid(row=1, column=0, sticky='e', padx=5, pady=4)
        self.var_key = tk.StringVar(value=cfg.get('api_key', ''))
        ttk.Entry(frame, textvariable=self.var_key, width=42, show='•').grid(row=1, column=1, padx=5, pady=4)

        self.var_remember = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text='Zapamiętaj na tym komputerze',
                        variable=self.var_remember).grid(row=2, column=1, sticky='w', padx=5)

        ttk.Button(frame, text='Połącz', command=self.connect).grid(row=0, column=2, rowspan=2, padx=8)

        self.lbl_status = ttk.Label(frame, text='Niepołączono', foreground='#a33')
        self.lbl_status.grid(row=3, column=0, columnspan=3, sticky='w', padx=5, pady=(0, 4))

    def connect(self):
        server = self.var_server.get().strip()
        key = self.var_key.get().strip()
        if not server or not key:
            messagebox.showerror('Błąd', 'Podaj adres serwera i klucz API.')
            return
        try:
            self.templates = api_request(server, key, 'GET', '/api/v1/templates')
            self.operators = api_request(server, key, 'GET', '/api/v1/users')
            try:
                self.orders = [o for o in api_request(server, key, 'GET', '/api/v1/orders')
                               if o.get('status') != 'shipped']
            except ApiError:
                self.orders = []
            try:
                self.installers = api_request(server, key, 'GET', '/api/v1/installers')
            except ApiError:
                self.installers = []
        except ApiError as exc:
            self.lbl_status.config(text='Błąd połączenia', foreground='#a33')
            messagebox.showerror('Nie udało się połączyć', str(exc))
            return

        if self.var_remember.get():
            save_config(server, key)

        self.lbl_status.config(text=f'Połączono — {len(self.templates)} szablonów, '
                                     f'{len(self.operators)} operatorów', foreground='#2a2')
        self.cmb_template['values'] = [t['name'] for t in self.templates]
        self.cmb_operator['values'] = [o['username'] for o in self.operators]
        self.cmb_order['values'] = [''] + [
            f"{o['number']} — {o['client']} — {o['product_name']}" for o in self.orders
        ]
        self.cmb_installer_pick['values'] = [i['name'] for i in self.installers]
        self._set_form_state('normal')

    # -- Sekcja formularza -------------------------------------------------------

    def _build_form_frame(self):
        frame = ttk.LabelFrame(self, text='Dane serii list kontrolnych')
        frame.grid(row=1, column=0, padx=10, pady=5, sticky='ew')

        r = 0
        ttk.Label(frame, text='Szablon:').grid(row=r, column=0, sticky='e', padx=5, pady=4)
        self.cmb_template = ttk.Combobox(frame, state='readonly', width=39)
        self.cmb_template.grid(row=r, column=1, padx=5, pady=4)

        r += 1
        ttk.Label(frame, text='Tytuł raportu:').grid(row=r, column=0, sticky='e', padx=5, pady=4)
        self.var_title = tk.StringVar()
        ttk.Entry(frame, textvariable=self.var_title, width=42).grid(row=r, column=1, padx=5, pady=4)
        ttk.Label(frame, text='np. „Kontrola – Klient X”; program doda „– 1/30” itd.',
                 foreground='#777').grid(row=r, column=2, sticky='w', padx=5)

        r += 1
        ttk.Label(frame, text='Ilość w serii:').grid(row=r, column=0, sticky='e', padx=5, pady=4)
        self.var_quantity = tk.StringVar(value='1')
        ttk.Spinbox(frame, from_=1, to=99, textvariable=self.var_quantity, width=6
                   ).grid(row=r, column=1, sticky='w', padx=5, pady=4)

        r += 1
        ttk.Label(frame, text='Operator (kontroler):').grid(row=r, column=0, sticky='e', padx=5, pady=4)
        self.cmb_operator = ttk.Combobox(frame, state='readonly', width=39)
        self.cmb_operator.grid(row=r, column=1, padx=5, pady=4)

        r += 1
        ttk.Label(frame, text='Zamówienie (opcjonalnie):').grid(row=r, column=0, sticky='e', padx=5, pady=4)
        self.cmb_order = ttk.Combobox(frame, state='readonly', width=39)
        self.cmb_order.grid(row=r, column=1, padx=5, pady=4)

        r += 1
        now = datetime.now()
        ttk.Label(frame, text='Data kontroli (RRRR-MM-DD):').grid(row=r, column=0, sticky='e', padx=5, pady=4)
        self.var_date = tk.StringVar(value=now.strftime('%Y-%m-%d'))
        ttk.Entry(frame, textvariable=self.var_date, width=14).grid(row=r, column=1, sticky='w', padx=5, pady=4)

        r += 1
        ttk.Label(frame, text='Godzina (GG:MM):').grid(row=r, column=0, sticky='e', padx=5, pady=4)
        self.var_time = tk.StringVar(value=now.strftime('%H:%M'))
        ttk.Entry(frame, textvariable=self.var_time, width=8).grid(row=r, column=1, sticky='w', padx=5, pady=4)

        r += 1
        ttk.Label(frame, text='Wszystkie punkty zostaną zapisane jako zakończone, wynik OK.',
                 foreground='#777').grid(row=r, column=0, columnspan=2, sticky='w', padx=5, pady=(0, 4))

        self._form_widgets = [self.cmb_template, self.cmb_operator, self.cmb_order]

    def _build_installers_frame(self):
        """Zadania typu 'installer' (np. „Montaż”) wymagają przypisania montera —
        samo zaznaczenie OK nie wystarcza (patrz ReportItemInstaller). Ta sama
        lista monterów zostaje przypisana do każdego takiego punktu w każdej
        liście kontrolnej tworzonej serii."""
        frame = ttk.LabelFrame(
            self, text='Monterzy (dla zadań typu „monter” w szablonie — jeśli szablon takich nie ma, pomiń)')
        frame.grid(row=2, column=0, padx=10, pady=5, sticky='ew')

        ttk.Label(frame, text='Monter:').grid(row=0, column=0, sticky='e', padx=5, pady=4)
        self.cmb_installer_pick = ttk.Combobox(frame, state='readonly', width=24)
        self.cmb_installer_pick.grid(row=0, column=1, padx=5, pady=4)

        ttk.Label(frame, text='Rola (opcjonalnie):').grid(row=0, column=2, sticky='e', padx=5, pady=4)
        self.var_installer_role = tk.StringVar()
        ttk.Entry(frame, textvariable=self.var_installer_role, width=16
                 ).grid(row=0, column=3, padx=5, pady=4)

        self.btn_add_installer = ttk.Button(frame, text='Dodaj do listy', command=self._add_installer)
        self.btn_add_installer.grid(row=0, column=4, padx=5, pady=4)

        self.lst_installers = tk.Listbox(frame, height=4, width=55)
        self.lst_installers.grid(row=1, column=0, columnspan=4, padx=5, pady=4, sticky='ew')

        self.btn_remove_installer = ttk.Button(frame, text='Usuń zaznaczonego',
                                               command=self._remove_installer)
        self.btn_remove_installer.grid(row=1, column=4, padx=5, pady=4, sticky='n')

        self._form_widgets += [self.cmb_installer_pick, self.btn_add_installer,
                               self.btn_remove_installer]

        self.btn_clear_installers = ttk.Button(frame, text='Wyczyść listę',
                                               command=self._clear_installers)
        self.btn_clear_installers.grid(row=2, column=4, padx=5, pady=(0, 4), sticky='n')
        self._form_widgets.append(self.btn_clear_installers)

    def _clear_installers(self):
        self.chosen_installers.clear()
        self.lst_installers.delete(0, 'end')

    def _add_installer(self):
        name = self.cmb_installer_pick.get().strip()
        if not name:
            messagebox.showerror('Brak danych', 'Wybierz montera z listy.')
            return
        role = self.var_installer_role.get().strip() or None
        self.chosen_installers.append({'name': name, 'role': role})
        label = f'{name} ({role})' if role else name
        self.lst_installers.insert('end', label)
        self.var_installer_role.set('')

    def _remove_installer(self):
        sel = self.lst_installers.curselection()
        if not sel:
            return
        idx = sel[0]
        self.lst_installers.delete(idx)
        del self.chosen_installers[idx]

    def _build_actions_frame(self):
        frame = ttk.Frame(self)
        frame.grid(row=3, column=0, pady=10)
        self.btn_submit = ttk.Button(frame, text='Zapisz serię do bazy', command=self.submit)
        self.btn_submit.pack()
        self._form_widgets.append(self.btn_submit)

    def _set_form_state(self, state):
        for w in getattr(self, '_form_widgets', []):
            w.configure(state=state if state == 'disabled' else
                        ('readonly' if isinstance(w, ttk.Combobox) else 'normal'))

    # -- Wysyłka -----------------------------------------------------------------

    def submit(self):
        tmpl_name = self.cmb_template.get().strip()
        operator = self.cmb_operator.get().strip()
        if not tmpl_name:
            messagebox.showerror('Brak danych', 'Wybierz szablon.')
            return
        if not operator:
            messagebox.showerror('Brak danych', 'Wybierz operatora.')
            return
        try:
            quantity = int(self.var_quantity.get())
            assert 1 <= quantity <= 99
        except (ValueError, AssertionError):
            messagebox.showerror('Błąd', 'Ilość w serii musi być liczbą od 1 do 99.')
            return

        date_s = self.var_date.get().strip()
        time_s = self.var_time.get().strip() or '00:00'
        try:
            performed_at = datetime.strptime(f'{date_s} {time_s}', '%Y-%m-%d %H:%M')
        except ValueError:
            messagebox.showerror('Błąd', 'Data musi być w formacie RRRR-MM-DD, godzina GG:MM.')
            return

        order_number = ''
        sel = self.cmb_order.get().strip()
        if sel:
            order_number = sel.split(' — ', 1)[0]

        payload = {
            'template_name': tmpl_name,
            'title': self.var_title.get().strip(),
            'quantity': quantity,
            'operator': operator,
            'order_number': order_number,
            'completed': True,
            'performed_at': performed_at.isoformat(),
            'installers': list(self.chosen_installers),
        }

        count_label = f'{quantity} list kontrolnych' if quantity > 1 else '1 listę kontrolną'
        if self.chosen_installers:
            names = ', '.join(f"{e['name']}" + (f" ({e['role']})" if e['role'] else '')
                              for e in self.chosen_installers)
            installers_line = f'\nmonterzy: {names}'
        else:
            installers_line = '\nmonterzy: brak (jeśli szablon ma zadanie montera, zostanie puste!)'
        if not messagebox.askyesno(
            'Potwierdź zapis',
            f'Zapisać do bazy {count_label} jako już zakończone (wynik OK),\n'
            f'operator: {operator}, data: {performed_at.strftime("%d.%m.%Y %H:%M")}?{installers_line}'
        ):
            return

        self.btn_submit.configure(state='disabled')
        try:
            result = api_request(self.var_server.get().strip(), self.var_key.get().strip(),
                                 'POST', '/api/v1/checklists', payload)
        except ApiError as exc:
            messagebox.showerror('Nie udało się zapisać', str(exc))
            return
        finally:
            self.btn_submit.configure(state='normal')

        if quantity > 1:
            titles = '\n'.join(f"  • {it['title']}" for it in result.get('items', []))
            messagebox.showinfo('Zapisano', f'Utworzono serię {quantity} list kontrolnych:\n{titles}')
        else:
            messagebox.showinfo('Zapisano', f"Utworzono listę kontrolną: {result.get('title')}")


if __name__ == '__main__':
    BulkChecklistApp().mainloop()
