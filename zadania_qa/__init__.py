from flask import Blueprint

zadania_qa_bp = Blueprint('zadania_qa', __name__, url_prefix='/zadania-qa')

from . import routes  # noqa: F401, E402
