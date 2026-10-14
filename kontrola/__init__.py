from flask import Blueprint

kontrola_bp = Blueprint('kontrola', __name__, url_prefix='/kontrola')

from . import routes  # noqa: F401, E402
