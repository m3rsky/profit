import sys
import os

project_home = os.path.dirname(os.path.abspath(__file__))
if project_home not in sys.path:
    sys.path.insert(0, project_home)

from app import app as application

with application.app_context():
    os.makedirs(application.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(os.path.join(project_home, 'instance'), exist_ok=True)
    from models import db
    from app import _migrate_schema
    _migrate_schema()
    db.create_all()
