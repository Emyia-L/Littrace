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

from littrace.api.routes.search import (
    router as search_router,
)
from littrace.api.routes.evidence import (
    router as evidence_router,
)
from littrace.api.routes.research_runs import (
    router as research_runs_router,
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

    # 注册项目全文搜索接口。
    # 只有注册后，路由才会参与请求处理并显示在 Swagger 中。
    app.include_router(search_router)

    # 注册证据提取接口。
    app.include_router(evidence_router)
    # 将研究运行接口注册到当前 FastAPI 应用。
    # 注册后，接口才能接收请求并显示在 Swagger 文档中。
    app.include_router(research_runs_router)

    return app

app = create_app()