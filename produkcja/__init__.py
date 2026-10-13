from flask import Blueprint

produkcja_bp = Blueprint('produkcja', __name__, url_prefix='/produkcja')

# Funkcje z app.py (listy kontrolne, klucz QR). Rejestrowane z app.py, bo
# `from app import ...` przy `python app.py` załadowałby moduł drugi raz.
_helpers = {}


def register_helpers(**kwargs):
    _helpers.update(kwargs)


def helper(name):
    return _helpers[name]


from . import routes  # noqa: F401, E402
