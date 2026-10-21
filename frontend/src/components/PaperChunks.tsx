import { useEffect, useRef, useState } from 'react'
import {
  CHUNK_PAGE_SIZE,
  listPaperChunks,
  paperFileUrl,
  type Paper,
  type PaperChunk,
} from '../api/papers'

interface PaperChunksProps {
  projectId: string
  paper: Paper
  onBack: () => void
}

export function PaperChunks({ projectId, paper, onBack }: PaperChunksProps) {
  const [chunks, setChunks] = useState<PaperChunk[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [error, setError] = useState('')
  const [retryCount, setRetryCount] = useState(0)
  const moreRequest = useRef<AbortController | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    setChunks([])
    setLoading(true)
    setError('')
    // offset=0 表示第一页；组件重建或论文变化时重新从头读取。
    listPaperChunks(projectId, paper.id, 0, controller.signal)
      .then((firstPage) => {
        setChunks(firstPage)
        // API 没有 total 字段；满 20 条时再提供“加载更多”。
        setHasMore(firstPage.length === CHUNK_PAGE_SIZE)
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : '文本块加载失败')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => {
      controller.abort()
      moreRequest.current?.abort()
    }
  }, [projectId, paper.id, retryCount])

  async function loadMore() {
    if (loadingMore || !hasMore) return
    setError('')
    setLoadingMore(true)
    const controller = new AbortController()
    moreRequest.current = controller
    try {
      // 已显示的条数就是下一批 offset，后端按页码、页内序号稳定排序。
      const nextPage = await listPaperChunks(projectId, paper.id, chunks.length, controller.signal)
      setChunks((current) => [...current, ...nextPage])
      setHasMore(nextPage.length === CHUNK_PAGE_SIZE)
    } catch (reason) {
      if (!controller.signal.aborted) {
        setError(reason instanceof Error ? reason.message : '加载更多失败')
      }
    } finally {
      if (!controller.signal.aborted) setLoadingMore(false)
      moreRequest.current = null
    }
  }

  // 只按当前已加载的数据分组；页码来自 PDF，而非浏览器里的列表页码。
  const chunksByPage = new Map<number, PaperChunk[]>()
  for (const chunk of chunks) {
    const samePage = chunksByPage.get(chunk.page_number) ?? []
    samePage.push(chunk)
    chunksByPage.set(chunk.page_number, samePage)
  }

  return (
    <div className="paper-reader">
      <button className="back-button" type="button" onClick={onBack}>← 返回论文列表</button>
      <div className="detail-intro reader-intro">
        <div>
          <p className="eyebrow">PAPER TEXT</p>
          <h1>{paper.original_filename}</h1>
          <p className="intro-copy">
            {paper.page_count === null ? '页数未知' : `原始 PDF 共 ${paper.page_count} 页`}
            <span className="metadata-separator">·</span>
            已加载 {chunks.length} 个文本块
          </p>
        </div>
        <a
          className="outline-link"
          href={paperFileUrl(projectId, paper.id)}
          target="_blank"
          rel="noopener noreferrer"
        >查看原始 PDF ↗</a>
      </div>

      <section className="panel reader-panel" aria-labelledby="chunks-heading">
        <div className="panel-heading">
          <div>
            <p className="section-kicker">EXTRACTED TEXT</p>
            <h2 id="chunks-heading">按页核对文本块</h2>
          </div>
          <span className="panel-hint">页码从 1 开始</span>
        </div>

        {loading && <p className="state-message">正在读取解析结果…</p>}
        {!loading && chunks.length === 0 && !error && (
          <p className="state-message">这篇论文暂时没有可显示的文本块。</p>
        )}
        {!loading && chunks.length === 0 && error && (
          <div className="reader-footer">
            <p className="error" role="alert">加载失败：{error}</p>
            <button className="secondary-button" type="button" onClick={() => setRetryCount((n) => n + 1)}>
              重试
            </button>
          </div>
        )}

        {Array.from(chunksByPage, ([pageNumber, pageChunks]) => (
          <section className="chunk-page" key={pageNumber} aria-label={`第 ${pageNumber} 页`}>
            <div className="chunk-page-heading">
              <h3>第 {pageNumber} 页</h3>
              <a
                href={`${paperFileUrl(projectId, paper.id)}#page=${pageNumber}`}
                target="_blank"
                rel="noopener noreferrer"
              >定位原始 PDF ↗</a>
            </div>
            <ol className="chunk-list">
              {pageChunks.map((chunk) => (
                <li className="chunk-card" key={chunk.chunk_id}>
                  <div className="chunk-meta">
                    <span>文本块 {chunk.chunk_index}</span>
                    <code title="证据引用时使用的稳定文本块 ID">{chunk.chunk_id}</code>
                  </div>
                  <p>{chunk.text}</p>
                </li>
              ))}
            </ol>
          </section>
        ))}

        {chunks.length > 0 && (
          <div className="reader-footer">
            {error && <p className="error" role="alert">加载更多失败：{error}</p>}
            {hasMore ? (
              <button className="secondary-button" type="button" onClick={loadMore} disabled={loadingMore}>
                {loadingMore ? '正在加载…' : '加载更多文本块'}
              </button>
            ) : (
              <p className="reader-end">已显示全部文本块</p>
            )}
          </div>
        )}
      </section>
    </div>
  )
}
