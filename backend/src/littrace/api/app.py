from fastapi import FastAPI

from littrace.api.routes.health import (
    router as health_router,
)
from littrace.api.routes.projects import (
    router as projects_router,
)
from littrace.api.routes.papers import (
    router as papers_router,
)

def create_app() -> FastAPI:
    """
    创建 FastAPI 应用实例。

    Returns:
        FastAPI: 返回一个 FastAPI 应用实例。
    """
    app = FastAPI(
        title="LitTrace API",
        description=(
            "面向计算机与 AI 文献的"
            "可追溯证据合成服务"
        ),
        version="0.1.0",
    )

    # 注册路由
    app.include_router(health_router)
    app.include_router(projects_router)
    app.include_router(papers_router)

    return app

app = create_app()