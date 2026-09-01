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
    MARSZRUTA_UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'marszruta_uploads')
    DOKUMENTACJA_UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'dokumentacja_uploads')
    MAX_CONTENT_LENGTH = 32 * 1024 * 1024
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
    PDF_EXTENSIONS = {'pdf'}
    # Moduł DOKUMENTACJA — osobne listy, nie ruszają walidacji innych modułów
    DOC_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'tif', 'tiff', 'heic', 'heif'}
    DOC_DOCUMENT_EXTENSIONS = {
        'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
        'odt', 'ods', 'odp', 'rtf', 'txt', 'csv',
        'dwg', 'dxf', 'svg', 'zip', 'rar', '7z',
    }
    API_KEY = os.environ.get('API_KEY', 'change-this-api-key')
    ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
