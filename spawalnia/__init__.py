from flask import Blueprint

spawalnia_bp = Blueprint('spawalnia', __name__, url_prefix='/spawalnia')

from . import routes  # noqa: F401, E402
