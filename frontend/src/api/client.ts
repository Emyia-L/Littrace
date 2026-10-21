const API_PREFIX = '/api'

async function getErrorMessage(response: Response): Promise<string> {
  let message = `请求失败（HTTP ${response.status}）`
  try {
    const body: unknown = await response.json()
    if (typeof body === 'object' && body !== null && 'detail' in body) {
      const detail = body.detail
      if (typeof detail === 'string') message = detail
    }
  } catch {
    // 非 JSON 错误页（例如开发代理暂时找不到后端）保留通用提示。
  }
  return message
}

/** 统一处理 JSON 响应与 FastAPI 错误，业务 API 只需描述路径和参数。 */
export async function requestJson<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, options)
  if (!response.ok) {
    throw new Error(await getErrorMessage(response))
  }
  return response.json() as Promise<T>
}

/** 有些接口返回 Markdown 等纯文本，单独处理以免 JSON 解析报错。 */
export async function requestText(path: string, options?: RequestInit): Promise<string> {
  const response = await fetch(`${API_PREFIX}${path}`, options)
  if (!response.ok) throw new Error(await getErrorMessage(response))
  return response.text()
}
