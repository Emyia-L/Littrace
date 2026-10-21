import { useEffect, useState, type FormEvent } from 'react'
import {
  createResearchRun,
  listResearchRuns,
  type ResearchRun,
} from '../api/runs'

const statusLabels: Record<ResearchRun['status'], string> = {
  pending: '待启动',
  queued: '排队中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
}

interface ResearchRunsProps {
  projectId: string
  initialQuestion: string
  hasPapers: boolean
  onOpen: (runId: string) => void
}

export function ResearchRuns({ projectId, initialQuestion, hasPapers, onOpen }: ResearchRunsProps) {
  const [runs, setRuns] = useState<ResearchRun[]>([])
  const [question, setQuestion] = useState(initialQuestion)
  // number 输入框在用户清空时会暂时是空字符串，提交时再转换更稳妥。
  const [topK, setTopK] = useState('8')
  const [maxCards, setMaxCards] = useState('3')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [formError, setFormError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    setQuestion(initialQuestion)
    setLoading(true)
    setLoadError('')
    listResearchRuns(projectId, controller.signal)
      .then(setRuns)
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setLoadError(reason instanceof Error ? reason.message : '运行历史加载失败')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [projectId, initialQuestion])

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setFormError('')
    const cleanQuestion = question.trim()
    if (!cleanQuestion || cleanQuestion.length > 1000) {
      setFormError('研究问题应为 1–1000 个字符。')
      return
    }
    const parsedTopK = Number(topK)
    const parsedMaxCards = Number(maxCards)
    if (!topK || !maxCards ||
        !Number.isInteger(parsedTopK) || parsedTopK < 1 || parsedTopK > 100 ||
        !Number.isInteger(parsedMaxCards) || parsedMaxCards < 1 || parsedMaxCards > 10) {
      setFormError('候选文本块需为 1–100，证据卡需为 1–10。')
      return
    }

    setSaving(true)
    try {
      // 这里只创建 pending 记录，不自动触发付费的模型执行。
      const run = await createResearchRun(projectId, {
        question: cleanQuestion,
        top_k: parsedTopK,
        max_cards: parsedMaxCards,
      })
      setRuns((current) => [run, ...current])
      onOpen(run.id)
    } catch (reason) {
      setFormError(reason instanceof Error ? reason.message : '创建研究运行失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="runs-section" aria-labelledby="runs-heading">
      <div className="runs-intro">
        <p className="section-kicker">RESEARCH RUNS</p>
        <h2 id="runs-heading">研究运行</h2>
        <p>每次运行保存问题、执行模式与证据记录，可在不同运行之间回看结果。</p>
      </div>
      <div className="workspace-grid">
        <section className="panel projects-panel" aria-labelledby="run-history-heading">
          <div className="panel-heading">
            <div>
              <p className="section-kicker">HISTORY</p>
              <h3 id="run-history-heading">运行历史</h3>
            </div>
            <span className="panel-hint">最近 20 次</span>
          </div>
          {loading && <p className="state-message">正在读取运行历史…</p>}
          {!loading && loadError && <p className="state-message error" role="alert">加载失败：{loadError}</p>}
          {!loading && !loadError && runs.length === 0 && (
            <p className="state-message">还没有研究运行。可在右侧创建第一条。</p>
          )}
          {!loading && !loadError && runs.length > 0 && (
            <ul className="project-list">
              {runs.map((run) => (
                <li className="project-card run-card" key={run.id}>
                  <div className="project-card-top">
                    <span className={`status-badge run-status-${run.status}`}>{statusLabels[run.status]}</span>
                    <time dateTime={run.created_at}>{new Date(run.created_at).toLocaleString('zh-CN')}</time>
                  </div>
                  <h4>{run.question}</h4>
                  <p className="project-question">
                    {run.execution_mode === 'agent' ? '动态 Agent' : run.execution_mode === 'workflow' ? '固定流程' : '尚未选择模式'}
                    <span className="metadata-separator">·</span>
                    top_k {run.top_k}
                    <span className="metadata-separator">·</span>
                    最多 {run.max_cards} 张证据卡
                  </p>
                  <button className="text-button" type="button" onClick={() => onOpen(run.id)}>
                    查看运行详情 →
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="panel create-panel" aria-labelledby="new-run-heading">
          <div className="panel-heading">
            <div>
              <p className="section-kicker">NEW RUN</p>
              <h3 id="new-run-heading">创建研究运行</h3>
            </div>
          </div>
          <p className="form-description">
            创建后先进入“待启动”；你可以在详情页明确选择动态 Agent 或固定流程。
          </p>
          {!hasPapers && <p className="form-description error">请先上传并解析论文，再启动研究。</p>}
          <form onSubmit={handleCreate}>
            <label htmlFor="run-question">研究问题</label>
            <textarea
              id="run-question"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              maxLength={1000}
              rows={4}
              required
            />
            <div className="run-settings">
              <div>
                <label htmlFor="run-top-k">候选文本块</label>
                <input id="run-top-k" type="number" min={1} max={100} value={topK}
                  onChange={(event) => setTopK(event.target.value)} required />
              </div>
              <div>
                <label htmlFor="run-max-cards">最多证据卡</label>
                <input id="run-max-cards" type="number" min={1} max={10} value={maxCards}
                  onChange={(event) => setMaxCards(event.target.value)} required />
              </div>
            </div>
            <p className="field-help">top_k 控制每次检索的候选数量；max_cards 限制一次提取的证据卡数。</p>
            {formError && <p className="form-message error" role="alert">{formError}</p>}
            <button className="primary-button" type="submit" disabled={saving || loading || !hasPapers}>
              {saving ? '创建中…' : '创建待启动运行'} <span aria-hidden="true">→</span>
            </button>
          </form>
        </section>
      </div>
    </section>
  )
}
