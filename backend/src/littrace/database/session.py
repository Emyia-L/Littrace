#数据库连接和 Session
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from littrace.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    echo=False
)

SessionLocal = sessionmaker(
    bind=engine,
    class_=Session,
    autoflush=False,
    expire_on_commit=False,
)

def get_db_session() -> Generator[
    Session, 
    None, 
    None
]:
    """
    获取数据库会话的生成器函数。

    Yields:
        Session: SQLAlchemy 的数据库会话对象。
    """

    with SessionLocal() as session:
        yield session


