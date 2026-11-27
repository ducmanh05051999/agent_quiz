import os
import sys
import json
import logging
import keyring
from pathlib import Path
from dataclasses import dataclass, asdict

SERVICE_NAME = "QuizHelperAI_Portable"
KEY_USERNAME = "openai_api_key"


def get_base_dir() -> Path:
    """Lấy thư mục chứa file EXE (hoặc thư mục chạy script khi dev)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


@dataclass
class AppConfig:
    model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    confidence_threshold: int = 90
    auto_select: bool = False
    scan_interval_sec: float = 2.5
    max_tokens_per_request: int = 1200
    top_k_chunks: int = 4
    language_ocr: str = "vie+eng"


class ConfigManager:
    def __init__(self):
        self.base_dir = get_base_dir()
        self.docs_dir = self.base_dir / "documents"
        self.data_dir = self.base_dir / "data"
        self.logs_dir = self.base_dir / "logs"
        self.config_dir = self.base_dir / "config"
        self.tesseract_dir = self.base_dir / "tesseract"

        for folder in [self.docs_dir, self.data_dir, self.logs_dir, self.config_dir]:
            folder.mkdir(parents=True, exist_ok=True)

        self.config_file = self.config_dir / "settings.json"
        self.setup_logging()
        self.config = self.load_config()

    def setup_logging(self):
        log_file = self.logs_dir / "quizhelper.log"
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            handlers=[
                logging.FileHandler(log_file, encoding="utf-8"),
                logging.StreamHandler(sys.stdout),
            ],
        )
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("openai").setLevel(logging.WARNING)

    def load_config(self) -> AppConfig:
        if not self.config_file.exists():
            cfg = AppConfig()
            self.save_config(cfg)
            return cfg
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return AppConfig(**{k: v for k, v in data.items() if k in AppConfig.__dataclass_fields__})
        except Exception as e:
            logging.error(f"Lỗi đọc file cấu hình, khôi phục mặc định: {e}")
            return AppConfig()

    def save_config(self, config: AppConfig = None):
        if config:
            self.config = config
        tmp_file = self.config_file.with_suffix(".tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(asdict(self.config), f, indent=2, ensure_ascii=False)
        os.replace(tmp_file, self.config_file)

    def get_api_key(self) -> str:
        try:
            key = keyring.get_password(SERVICE_NAME, KEY_USERNAME)
            return key or ""
        except Exception as e:
            logging.error(f"Không thể đọc Windows Credential Manager: {e}")
            return ""

    def set_api_key(self, api_key: str):
        api_key = api_key.strip()
        if not api_key:
            try:
                keyring.delete_password(SERVICE_NAME, KEY_USERNAME)
            except Exception:
                pass
            return
        keyring.set_password(SERVICE_NAME, KEY_USERNAME, api_key)
        logging.info("Đã lưu OpenAI API Key an toàn vào Windows Credential Manager.")
