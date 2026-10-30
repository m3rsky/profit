import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'psh-qc-secret-key-2024')
    SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(BASE_DIR, 'instance', 'psh_qc.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
    QAR_UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'qar_uploads')
    # Dokumentacja: pliki poza static, serwowane tylko przez trasę z logowaniem
    DOKUMENTACJA_UPLOAD_FOLDER = os.path.join(BASE_DIR, 'instance', 'dokumentacja_uploads')
    # Dawna lokalizacja (pod static); init_db przenosi stąd pliki do nowej
    DOKUMENTACJA_LEGACY_FOLDER = os.path.join(BASE_DIR, 'static', 'dokumentacja_uploads')
    # Moduł KJ Prestiż (pogląd): grafiki punktów i zdjęcia, serwowane przez blueprint
    PKJ_UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'pkj_uploads')
    # Pliki listy produkcyjnej (import/kopie) poza static, żeby nie były serwowane publicznie
    PRODUKCJA_FOLDER = os.path.join(BASE_DIR, 'instance', 'produkcja')
    # Twardy limit żądania (Flask). Wyższy niż 32 MB tylko ze względu na pliki STEP
    # w Dokumentacji; dla pozostałych tras pilnuje DEFAULT_MAX_UPLOAD (app.py).
    MAX_CONTENT_LENGTH = 200 * 1024 * 1024
    DEFAULT_MAX_UPLOAD = 32 * 1024 * 1024
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
    PDF_EXTENSIONS = {'pdf'}
    # Moduł DOKUMENTACJA — osobne listy, nie ruszają walidacji innych modułów
    DOC_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'tif', 'tiff', 'heic', 'heif'}
    DOC_DOCUMENT_EXTENSIONS = {
        'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
        'odt', 'ods', 'odp', 'rtf', 'txt', 'csv',
        'dwg', 'dxf', 'svg', 'zip', 'rar', '7z',
        'step', 'stp',
    }
    # Modele 3D (podgląd w przeglądarce) mogą być duże: osobny limit na plik
    DOC_STEP_EXTENSIONS = {'step', 'stp'}
    DOC_MAX_FILE_BYTES = 32 * 1024 * 1024
    DOC_STEP_MAX_BYTES = 200 * 1024 * 1024
    # Od tego rozmiaru przeglądarka prosi o potwierdzenie przed wysłaniem
    DOC_CONFIRM_MB = 50
    API_KEY = os.environ.get('API_KEY', 'change-this-api-key')
    ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
