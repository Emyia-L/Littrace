from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
)

from pathlib import Path
from decimal import Decimal

class Settings(BaseSettings):
    deepseek_api_key: SecretStr
    deepseek_base_url: str
    deepseek_model: str
    database_url: str
    # Embedding 与聊天模型分别配置：
    # DeepSeek 继续负责聊天；这些字段专门连接百炼的向量模型。
    embedding_api_key: SecretStr
    embedding_base_url: str
    embedding_model: str
    # 供应商价格可能变化，因此由环境变量提供，而不硬编码在业务代码中。
    # 单位均为“美元 / 一百万 token”；未配置时仍统计 token，但成本显示为 0。
    llm_input_cost_per_million_usd: Decimal = Decimal("0")
    llm_output_cost_per_million_usd: Decimal = Decimal("0")
    # MVP 阶段单个 PDF 最大允许 20 MB。
    # 后续可以通过环境变量 MAX_PDF_SIZE_BYTES 覆盖。
    max_pdf_size_bytes: int = 20 * 1024 * 1024
    # PDF 文件默认保存到：
    # backend/storage/papers
    #
    # 也可以通过 .env 中的 PAPER_STORAGE_DIR 覆盖。
    paper_storage_dir: Path = Path("storage/papers")

    # Redis 连接地址可能含密码，使用 SecretStr 避免它被意外打印到日志。
    # 暂时允许不配置：在切换执行入口前，现有 BackgroundTasks 仍可运行。
    redis_url: SecretStr | None = None

    # 查询 Embedding 缓存默认复用 REDIS_URL；生产环境可指定独立的缓存 Redis。
    # 未配置任何 Redis 地址时，Embedding 请求仍直接访问模型服务。
    embedding_cache_redis_url: SecretStr | None = None
    embedding_cache_enabled: bool = True
    embedding_cache_ttl_seconds: int = Field(default=86400, ge=60)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        
    )

@lru_cache
def get_settings() -> Settings:
    return Settings()
