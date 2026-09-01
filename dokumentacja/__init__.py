from flask import Blueprint

dokumentacja_bp = Blueprint('dokumentacja', __name__, url_prefix='/dokumentacja')

from . import routes  # noqa: F401, E402
