from flask import Blueprint

prestiz_kj_bp = Blueprint('prestiz_kj', __name__, url_prefix='/kj-prestiz')

from . import models  # noqa: F401, E402  (tabele pkj_* muszą być znane przed create_all)
from . import config_routes, demo_routes, order_routes, mobile_routes, return_routes, report_routes, scenario_routes, routes  # noqa: F401, E402
