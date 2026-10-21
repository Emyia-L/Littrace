from openai import OpenAI

from littrace.config import get_settings

# 每次请求最多发送 20 个文本块，避免一次请求塞入过多内容。
EMBEDDING_BATCH_SIZE = 20

def create_embedding_client() -> OpenAI:
    """创建连接千问 Embedding 服务的客户端。"""

    settings = get_settings()

    return OpenAI(
        # 向量服务的密钥与 DeepSeek 聊天密钥分开读取。
        api_key=settings.embedding_api_key.get_secret_value(),
        # 兼容 OpenAI 的服务地址；SDK 会在请求时补上 embeddings 路径。
        base_url=settings.embedding_base_url,
        timeout=60.0,
        max_retries=1,
    )

def create_embedding(text: str) -> list[float]:
    """把单段文本转换成向量，供单条测试或调用使用。"""

    return create_embeddings([text])[0]

def create_embeddings(texts: list[str]) -> list[list[float]]:
    """批量把文本转换为向量，并按输入顺序返回。"""

    if any(not text.strip() for text in texts):
        raise ValueError("待转换的文本中不能包含空文本")

    if not texts:
        return []

    settings = get_settings()
    client = create_embedding_client()
    vectors: list[list[float]] = []

    # 把较长的文本列表拆成多个小批次，逐批请求 Embedding 服务。
    for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
        batch = texts[start : start + EMBEDDING_BATCH_SIZE]

        response = client.embeddings.create(
            model=settings.embedding_model,
            input=batch,
            encoding_format="float",
        )

        # 服务返回的每个向量带有输入序号。
        # 按序号排序，确保向量顺序与原文本顺序一致。
        batch_vectors = sorted(
            response.data,
            key=lambda item: item.index,
        )

        if len(batch_vectors) != len(batch):
            raise ValueError("Embedding 服务返回的向量数量与输入文本数量不一致")

        vectors.extend(item.embedding for item in batch_vectors)

    return vectors

