from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
)

from pathlib import Path

class Settings(BaseSettings):
    deepseek_api_key: SecretStr
    deepseek_base_url: str
    deepseek_model: str
    database_url: str
    # MVP 阶段单个 PDF 最大允许 20 MB。
    # 后续可以通过环境变量 MAX_PDF_SIZE_BYTES 覆盖。
    max_pdf_size_bytes: int = 20 * 1024 * 1024
    # PDF 文件默认保存到：
    # backend/storage/papers
    #
    # 也可以通过 .env 中的 PAPER_STORAGE_DIR 覆盖。
    paper_storage_dir: Path = Path("storage/papers")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        
    )

@lru_cache
def get_settings() -> Settings:
    return Settings()