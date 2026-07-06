from flask import Blueprint

marszruta_bp = Blueprint('marszruta', __name__, url_prefix='/marszruta')

from . import routes  # noqa: F401, E402
