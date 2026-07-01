"""
Wspólne fixtures dla wszystkich plików testowych.

Flask nie pozwala wywołać `db.init_app()` drugi raz na tym samym obiekcie
`app` po obsłużeniu pierwszego żądania — dlatego fixture `app` musi być
zdefiniowana raz, w jednym miejscu, i współdzielona przez wszystkie testy
(test_app.py, test_api_v1.py).
"""
import os
import pytest
from sqlalchemy.pool import StaticPool
from app import app as flask_app, db as _db
from models import (User, ChecklistTemplate, Category, Task,
                    Order, CabinetType, MaterialPrice, LaborRate, Quote, QuoteConfig,
                    SpawalniaOperator, QARReport)

API_KEY = 'test-api-key-12345'


@pytest.fixture(scope='session')
def app():
    flask_app.config.update({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
        'SQLALCHEMY_ENGINE_OPTIONS': {
            'connect_args': {'check_same_thread': False},
            'poolclass': StaticPool,
        },
        'WTF_CSRF_ENABLED': False,
        'UPLOAD_FOLDER': os.path.join(os.path.dirname(__file__), 'tmp_uploads'),
        'SECRET_KEY': 'test-secret',
        'API_KEY': API_KEY,
    })
    flask_app.extensions.pop("sqlalchemy", None)
    _db.init_app(flask_app)
    with flask_app.app_context():
        _db.create_all()
        _seed()
    yield flask_app


def _seed():
    admin = User(username='admin', email='admin@test.pl', role='admin')
    admin.set_password('Admin1234!')
    user = User(username='oper', email='oper@test.pl', role='kontroler')
    user.set_password('Oper1234!')
    _db.session.add_all([admin, user])
    _db.session.flush()

    # Szablon używany przez tests/test_app.py (nazwa ma znaczenie — referencja po name).
    tmpl = ChecklistTemplate(name='Test szablon', is_active=True, template_type='kontroler')
    _db.session.add(tmpl)
    _db.session.flush()
    cat = Category(template_id=tmpl.id, name='Kategoria', order=0)
    _db.session.add(cat)
    _db.session.flush()
    task = Task(category_id=cat.id, title='Zadanie testowe', order=0, is_active=True)
    _db.session.add(task)

    # Szablon dedykowany testom /api/v1 (dopasowanie po tokenach nazwy produktu).
    api_tmpl = ChecklistTemplate(name='Kontrola ZO API', is_active=True, template_type='kontroler')
    _db.session.add(api_tmpl)
    _db.session.flush()
    api_cat = Category(template_id=api_tmpl.id, name='Kategoria API', order=0)
    _db.session.add(api_cat)
    _db.session.flush()
    _db.session.add(Task(category_id=api_cat.id, title='Zadanie API', order=0, is_active=True))
    _db.session.flush()

    order = Order(number='ZAM-API-001', product_name='Produkt testowy',
                  client='Klient testowy', quantity=1, created_by_id=admin.id)
    _db.session.add(order)

    cab = CabinetType(code='PSH_IP65', name='Szafa PSH IP65')
    _db.session.add(cab)
    _db.session.add(MaterialPrice(code='dc01', name='Blacha DC01', price=4.0, unit='PLN/kg'))
    _db.session.add(LaborRate(volume_range='do400', label='do 400 l', laser=1, bending=1,
                              welding=1, grinding=1, assembly=1, packaging=1))
    _db.session.flush()
    quote = Quote(number='KSZ-2026-0001', client_name='Klient testowy',
                  cabinet_type_id=cab.id, created_by_id=admin.id)
    _db.session.add(quote)
    _db.session.flush()
    _db.session.add(QuoteConfig(quote_id=quote.id, width=600, height=800, depth=300))

    _db.session.add(SpawalniaOperator(initials='JK', name='Jan Kowalski'))

    _db.session.add(QARReport(number='QAR-2026-0001', title='Test NCR',
                              description='Opis testowy', user_id=admin.id))
    _db.session.commit()


@pytest.fixture
def client(app):
    return app.test_client()
