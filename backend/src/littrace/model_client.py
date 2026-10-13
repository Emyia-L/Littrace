from openai import OpenAI

from littrace.config import get_settings

def create_model_client() -> OpenAI:
    """
    创建 OpenAI 模型客户端。
    """

    settings = get_settings()

    return OpenAI(
        api_key=(
            settings.deepseek_api_key.get_secret_value()
        ),
        base_url=settings.deepseek_base_url,
        timeout=180.0,
        max_retries=1,
    )

def check_model_connection() -> str:
    """
    检查模型客户端连接是否正常。
    """

    settings = get_settings()
    client = create_model_client()

    response = client.chat.completions.create(
        model=settings.deepseek_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是一个 AI 助手。"
                    "请用中文回答问题。"
                )
            },
            {
                "role": "user",
                "content": (
                    "请确认你能正常连接到模型。"
                )
            }
        ],
        max_tokens=50,
    )

    content = response.choices[0].message.content

    if not content:
        raise ValueError("模型返回的内容为空")

    return content

if __name__ == "__main__":
    # 测试模型客户端连接
    try:
        result = check_model_connection()
        print("模型连接成功，返回内容：", result)
    except Exception as e:
        print("模型连接失败，错误信息：", str(e))
