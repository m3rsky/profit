from flask import Blueprint

ustawienia_bp = Blueprint('ustawienia', __name__, url_prefix='/ustawienia-produkcji')

from . import routes  # noqa: F401, E402
