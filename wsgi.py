import sys
import os
import traceback

project_home = os.path.dirname(os.path.abspath(__file__))
if project_home not in sys.path:
    sys.path.insert(0, project_home)

instance_dir = os.path.join(project_home, 'instance')
os.makedirs(instance_dir, exist_ok=True)

try:
    from app import app as application, init_db
    init_db()
except Exception as e:
    def application(environ, start_response):
        error_msg = traceback.format_exc()
        output = f"<pre>BŁĄD STARTU APLIKACJI:\n\n{error_msg}</pre>"
        output = output.encode('utf-8')
        start_response('500 Internal Server Error', [
            ('Content-Type', 'text/html; charset=utf-8'),
            ('Content-Length', str(len(output)))
        ])
        return [output]
