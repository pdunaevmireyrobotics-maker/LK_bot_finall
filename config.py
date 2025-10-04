import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "Pavel_Andch")
    
    # Для Railway используем абсолютные пути
    REPORTS_FOLDER = os.getenv("REPORTS_FOLDER", "/tmp/reports")
    BACKUP_FOLDER = os.getenv("BACKUP_FOLDER", "/tmp/backups")
    CLOSED_SESSIONS_FOLDER = os.getenv("CLOSED_SESSIONS_FOLDER", "/tmp/closed_sessions")
    
    @classmethod
    def create_folders(cls):
        """Создание папок при инициализации"""
        for folder in [cls.REPORTS_FOLDER, cls.BACKUP_FOLDER, cls.CLOSED_SESSIONS_FOLDER]:
            if not os.path.exists(folder):
                os.makedirs(folder, exist_ok=True)

# Создаем папки при импорте
Config.create_folders()