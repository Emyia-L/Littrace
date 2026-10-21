import { requestJson } from './client'

/** 与后端 PaperResponse 保持一致；storage_key 不会暴露给浏览器。 */
export interface Paper {
  id: string
  project_id: string
  original_filename: string
  content_hash: string
  status: 'uploaded' | 'parsing' | 'parsed' | 'failed'
  page_count: number | null
  created_at: string
  updated_at: string
}

/** 后端不会返回 embedding；前端只展示可审阅的原文与定位信息。 */
export interface PaperChunk {
  chunk_id: string
  paper_id: string
  page_number: number
  chunk_index: number
  text: string
  content_hash: string
}

export const CHUNK_PAGE_SIZE = 20

export function listPapers(projectId: string, signal?: AbortSignal): Promise<Paper[]> {
  return requestJson<Paper[]>(`/projects/${encodeURIComponent(projectId)}/papers`, { signal })
}

export function uploadPaper(projectId: string, file: File): Promise<Paper> {
  const form = new FormData()
  form.append('file', file)
  // 不设置 Content-Type：浏览器必须自行添加 multipart/form-data 的 boundary。
  return requestJson<Paper>(`/projects/${encodeURIComponent(projectId)}/papers`, {
    method: 'POST',
    body: form,
  })
}

export function listPaperChunks(
  projectId: string,
  paperId: string,
  offset: number,
  signal?: AbortSignal,
): Promise<PaperChunk[]> {
  const params = new URLSearchParams({
    offset: String(offset),
    limit: String(CHUNK_PAGE_SIZE),
  })
  return requestJson<PaperChunk[]>(
    `/projects/${encodeURIComponent(projectId)}/papers/${encodeURIComponent(paperId)}/chunks?${params}`,
    { signal },
  )
}

export function paperFileUrl(projectId: string, paperId: string): string {
  return `/api/projects/${encodeURIComponent(projectId)}/papers/${encodeURIComponent(paperId)}/file`
}
