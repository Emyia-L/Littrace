from fastapi import APIRouter

router = APIRouter(
    tags=["Health"],
)

@router.get("/health", summary="检查服务状态")
def health_check() -> dict[str, str]:
    """
    检查服务状态的健康检查接口。

    Returns:
        dict[str, str]: 返回一个包含服务状态的字典。
    """
    return {
        "status": "ok",
        "service": "littrace-api",
    }