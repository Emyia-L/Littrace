from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from littrace.database.session import (
    get_db_session,
)

# Annotated 可以把 Python 类型和 FastAPI 依赖声明组合起来。
#
# 路由只需写：
#
#     session: DatabaseSession
#
# FastAPI 就会自动调用 get_db_session()，取得一个
# SQLAlchemy Session，并在请求结束后关闭它。

DatabaseSession = Annotated[
    Session,
    Depends(get_db_session),
]

