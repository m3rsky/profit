from flask import Blueprint

qar_bp = Blueprint('qar', __name__, url_prefix='/qar')

from . import routes  # noqa: F401, E402
