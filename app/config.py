import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./geo.db")
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", 50 * 1024 * 1024))  # 50 MB
MAX_UNZIPPED_BYTES = int(os.getenv("MAX_UNZIPPED_BYTES", 200 * 1024 * 1024))
ALLOWED_EXTENSIONS = {".zip", ".kml"}
