from flask import Blueprint

kosztorys_bp = Blueprint('kosztorys', __name__, url_prefix='/kosztorys')

from . import routes  # noqa: F401, E402
