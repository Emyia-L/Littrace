import { useState, type FormEvent } from 'react'
import { paperFileUrl } from '../api/papers'
import {
  createEvidenceReview,
  getEvidenceSource,
  type EvidenceRecord,
  type EvidenceReview,
  type EvidenceSource,
} from '../api/runs'

interface EvidenceReviewPanelProps {
  projectId: string
  runId: string
  evidence: EvidenceRecord
  reviews: EvidenceReview[]
  onReviewSaved: (review: EvidenceReview) => void
}

const reviewLabels: Record<EvidenceReview['decision'], string> = {
  confirmed: '人工确认',
  dismissed: '人工驳回',
}

export function EvidenceReviewPanel({
  projectId, runId, evidence, reviews, onReviewSaved,
}: EvidenceReviewPanelProps) {
  const [source, setSource] = useState<EvidenceSource | null>(null)
  const [sourceLoading, setSourceLoading] = useState(false)
  const [sourceError, setSourceError] = useState('')
  const [decision, setDecision] = useState<EvidenceReview['decision'] | ''>('')
  const [note, setNote] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [reviewError, setReviewError] = useState('')
  const [notice, setNotice] = useState('')

  const latestReview = reviews.at(-1)

  async function loadSource() {
    setSourceLoading(true)
    setSourceError('')
    try {
      // 服务端会再次核对证据所属项目、文本块和页码；前端不猜测 paper_id。
      const verified = await getEvidenceSource(projectId, runId, evidence.id)
      setSource(verified)
    } catch (reason) {
      setSourceError(reason instanceof Error ? reason.message : '来源加载失败')
    } finally {
      setSourceLoading(false)
    }
  }

  async function handleReview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setReviewError('')
    setNotice('')
    if (!source || !decision) {
      setReviewError('请先核对来源，再选择人工决定。')
      return
    }
    const cleanNote = note.trim()
    if (cleanNote.length > 2000) {
      setReviewError('复核说明不能超过 2000 字。')
      return
    }
    setSubmitting(true)
    try {
      const saved = await createEvidenceReview(
        projectId, runId, evidence.id, decision, cleanNote || null,
      )
      // 后端返回持久化后的记录，立即追加到历史；不修改原有 Gate 判断。
      onReviewSaved(saved)
      setDecision('')
      setNote('')
      setNotice('人工复核已保存；如需更正，可再追加一条决定。')
    } catch (reason) {
      setReviewError(reason instanceof Error ? reason.message : '复核提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="evidence-review">
      <div className="review-summary">
        <strong>人工复核</strong>
        <span className={`status-badge ${latestReview?.decision === 'dismissed' ? 'run-status-failed' : ''}`}>
          {latestReview ? reviewLabels[latestReview.decision] : '尚未复核'}
        </span>
        {reviews.length > 0 && <span className="review-count">共 {reviews.length} 次记录</span>}
      </div>

      {!source ? (
        <div className="review-source-action">
          <button className="secondary-button" type="button" onClick={loadSource} disabled={sourceLoading}>
            {sourceLoading ? '正在核对来源…' : '核对原文位置'}
          </button>
          {sourceError && <p className="error" role="alert">来源核对失败：{sourceError}</p>}
        </div>
      ) : (
        <div className="verified-source">
          <p>已定位：{source.original_filename}，第 {source.page_number} 页</p>
          <a
            href={`${paperFileUrl(projectId, source.paper_id)}#page=${source.page_number}`}
            target="_blank"
            rel="noopener noreferrer"
          >打开 PDF 对照原文 ↗</a>
          <p className="evidence-id">核对的文本块：{source.chunk_id}</p>
        </div>
      )}

      {source && (
        <form className="review-form" onSubmit={handleReview}>
          <fieldset>
            <legend>你的判断</legend>
            <label>
              <input type="radio" name={`review-${evidence.id}`} value="confirmed"
                checked={decision === 'confirmed'} onChange={() => setDecision('confirmed')} />
              确认：引文支持这项论断
            </label>
            <label>
              <input type="radio" name={`review-${evidence.id}`} value="dismissed"
                checked={decision === 'dismissed'} onChange={() => setDecision('dismissed')} />
              驳回：引文不足或结论有误
            </label>
          </fieldset>
          <label className="review-note-label" htmlFor={`review-note-${evidence.id}`}>复核说明（可选）</label>
          <textarea id={`review-note-${evidence.id}`} value={note} maxLength={2000} rows={2}
            onChange={(event) => setNote(event.target.value)} placeholder="记录判断依据，便于之后回看" />
          <button className="secondary-button" type="submit" disabled={submitting || !decision}>
            {submitting ? '正在保存…' : '保存人工决定'}
          </button>
          <p className="field-help">每次提交新增一条历史；最新记录决定当前人工状态。</p>
          {reviewError && <p className="error" role="alert">{reviewError}</p>}
          {notice && <p className="success" role="status">{notice}</p>}
        </form>
      )}

      {reviews.length > 0 && (
        <details className="review-history">
          <summary>查看人工复核历史</summary>
          <ol>
            {reviews.map((review) => (
              <li key={review.id}>
                <strong>{reviewLabels[review.decision]}</strong>
                <time dateTime={review.created_at}>{new Date(review.created_at).toLocaleString('zh-CN')}</time>
                {review.note && <p>{review.note}</p>}
              </li>
            ))}
          </ol>
        </details>
      )}
    </div>
  )
}
