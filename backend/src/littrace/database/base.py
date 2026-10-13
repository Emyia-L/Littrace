#SQLAlchemy 基类
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    """
    所有的数据库模型类都应该继承自这个基类
    """
    pass