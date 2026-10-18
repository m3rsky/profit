from flask import Blueprint

kp_bp = Blueprint('kp', __name__, url_prefix='/kontrolaprodukcji')

from . import routes  # noqa: F401, E402
from . import station  # noqa: F401, E402
