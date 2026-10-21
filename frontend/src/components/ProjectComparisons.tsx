import { useEffect, useMemo, useState } from 'react'
import {
  createProjectComparison,
  createProjectSynthesis,
  listComparisonCandidates,
  listProjectComparisons,
  listProjectSyntheses,
  type ComparisonCandidate,
  type ComparisonDecision,
  type EvidenceComparison,
  type ProjectSynthesis,
} from '../api/projects'

const decisionLabels: Record<ComparisonDecision, string> = {
  mutually_support: '相互支持',
  conflicting: '存在冲突',
  inconclusive: '尚不明确',
}

const relationshipLabels = {
  agreement: '结论一致', conflict: '结论冲突', complementary: '相互补充', inconclusive: '证据不足',
} as const

interface ProjectComparisonsProps {
  projectId: string
  onSaved: () => void
}

function candidateLabel(item: ComparisonCandidate): string {
  return `${item.original_filename} · 第 ${item.page_number} 页 · ${item.claim.slice(0, 90)}`
}

export function ProjectComparisons({ projectId, onSaved }: ProjectComparisonsProps) {
  const [candidates, setCandidates] = useState<ComparisonCandidate[]>([])
  const [comparisons, setComparisons] = useState<EvidenceComparison[]>([])
  const [syntheses, setSyntheses] = useState<ProjectSynthesis[]>([])
  const [leftId, setLeftId] = useState('')
  const [rightId, setRightId] = useState('')
  const [decision, setDecision] = useState<ComparisonDecision>('inconclusive')
  const [note, setNote] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  function applyLoadedData(
    nextCandidates: ComparisonCandidate[],
    nextComparisons: EvidenceComparison[],
  ) {
    setCandidates(nextCandidates)
    setComparisons(nextComparisons)
    setLeftId((current) => nextCandidates.some((item) => item.evidence_id === current)
      ? current : nextCandidates[0]?.evidence_id ?? '')
    setRightId((current) => nextCandidates.some((item) => item.evidence_id === current)
      ? current : '')
  }

  async function load() {
    setLoading(true)
    setError('')
    try {
      const [nextCandidates, nextComparisons, nextSyntheses] = await Promise.all([
        listComparisonCandidates(projectId),
        listProjectComparisons(projectId),
        listProjectSyntheses(projectId),
      ])
      applyLoadedData(nextCandidates, nextComparisons)
      setSyntheses(nextSyntheses)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '比较数据加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    const controller = new AbortController()
    // 初次加载使用异步请求；避免 effect 本身同步触发一次 loading 状态更新。
    Promise.all([
      listComparisonCandidates(projectId, controller.signal),
      listProjectComparisons(projectId, controller.signal),
      listProjectSyntheses(projectId, controller.signal),
    ])
      .then(([nextCandidates, nextComparisons, nextSyntheses]) => {
        if (!controller.signal.aborted) {
          applyLoadedData(nextCandidates, nextComparisons)
          setSyntheses(nextSyntheses)
        }
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : '比较数据加载失败')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [projectId])

  function refresh() {
    void load()
  }

  const left = candidates.find((item) => item.evidence_id === leftId)
  const rightOptions = useMemo(() => candidates.filter((item) =>
    item.evidence_id !== leftId && item.paper_id !== left?.paper_id && item.question === left?.question,
  ), [candidates, leftId, left?.paper_id, left?.question])
  const right = rightOptions.find((item) => item.evidence_id === rightId)
  const candidateById = useMemo(
    () => new Map(candidates.map((item) => [item.evidence_id, item])),
    [candidates],
  )
  const comparableQuestions = useMemo(() => {
    const papersByQuestion = new Map<string, Set<string>>()
    for (const item of candidates) {
      const papers = papersByQuestion.get(item.question) ?? new Set<string>()
      papers.add(item.paper_id)
      papersByQuestion.set(item.question, papers)
    }
    return [...papersByQuestion.entries()]
      .filter(([, papers]) => papers.size >= 2)
      .map(([question]) => question)
  }, [candidates])
  const [synthesisQuestion, setSynthesisQuestion] = useState('')

  useEffect(() => {
    setSynthesisQuestion((current) => comparableQuestions.includes(current)
      ? current : comparableQuestions[0] ?? '')
  }, [comparableQuestions])

  async function generateSynthesis() {
    if (!synthesisQuestion) return
    setGenerating(true)
    setError('')
    setNotice('')
    try {
      const created = await createProjectSynthesis(projectId, {
        question: synthesisQuestion,
        max_sources: 12,
        max_points: 5,
      })
      setSyntheses((current) => [created, ...current])
      setNotice(`自动综合已完成：${created.result.accepted.length} 条通过，${created.result.rejected.length} 条被证据约束拦截。`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '自动综合失败')
    } finally {
      setGenerating(false)
    }
  }

  async function saveComparison() {
    if (!left || !right) return
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const saved = await createProjectComparison(projectId, {
        left_evidence_id: left.evidence_id,
        right_evidence_id: right.evidence_id,
        decision,
        note: note.trim() || null,
      })
      setComparisons((current) => [
        saved,
        ...current.filter((item) => !samePair(item, saved)),
      ])
      setNotice('人工比较决定已保存，并保留在项目历史中。')
      // 通知同级的项目综述重新读取后端结果，确保比较结论立刻出现在报告中。
      onSaved()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '比较决定保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="panel project-report-panel" aria-labelledby="comparison-heading">
      <div className="panel-heading">
        <div>
          <p className="section-kicker">HUMAN CROSS-PAPER REVIEW</p>
          <h2 id="comparison-heading">跨论文观点比较</h2>
        </div>
        <span className="panel-hint">{candidates.length} 条可比较论断</span>
      </div>
      <p className="report-explanation">
        自动综合和人工比较都只使用同一研究问题、来自不同论文且已人工确认的论断；每条自动结论还会经过第二次论断支持检查。
      </p>
      {loading ? <p className="state-message">正在加载已确认的论断…</p> : (
        <div className="comparison-content">
          <h3>有证据约束的自动综合</h3>
          {comparableQuestions.length === 0 ? (
            <p className="state-message">至少需要两篇论文对同一问题各有一条人工确认证据。</p>
          ) : (
            <>
              <label className="comparison-label" htmlFor="synthesis-question">需要综合的问题</label>
              <select id="synthesis-question" value={synthesisQuestion} onChange={(event) => setSynthesisQuestion(event.target.value)}>
                {comparableQuestions.map((question) => <option key={question} value={question}>{question}</option>)}
              </select>
              <button className="secondary-button comparison-save" type="button" onClick={generateSynthesis} disabled={generating}>
                {generating ? '模型正在比较并验证…' : '生成跨论文自动综合'}
              </button>
            </>
          )}
          {syntheses.map((synthesis) => (
            <div className="comparison-preview" key={synthesis.id}>
              <p><strong>综合问题：</strong>{synthesis.question}</p>
              {synthesis.result.accepted.map((point) => (
                <div key={`${synthesis.id}-${point.title}`}>
                  <p><strong>{relationshipLabels[point.relationship]} · {point.title}</strong></p>
                  <p>{point.conclusion}</p>
                  <p className="comparison-source">证据：{point.source_evidence_ids.map((id) => {
                    const source = synthesis.sources.find((item) => item.evidence_id === id)
                    return source ? `${source.original_filename} 第 ${source.page_number} 页` : id
                  }).join('；')}</p>
                </div>
              ))}
              <p className="comparison-source">拦截 {synthesis.result.rejected.length} 条 · {synthesis.model_usage.total_tokens} tokens · ${synthesis.model_usage.estimated_cost_usd}</p>
            </div>
          ))}
          <hr />
          <h3>人工证据对比较</h3>
          {candidates.length < 2 && <p className="state-message">至少需要两条来自不同论文、针对同一问题的已确认论断。</p>}
          {candidates.length >= 2 && rightOptions.length === 0 && (
            <p className="state-message">当前没有可配对的论断；需要来自不同论文且针对同一问题的两条证据。</p>
          )}
          {candidates.length >= 2 && (
            <>
              <label className="comparison-label" htmlFor="comparison-left">论文 A 的证据</label>
              <select id="comparison-left" value={leftId} onChange={(event) => {
                setLeftId(event.target.value)
                setRightId('')
              }}>
                {candidates.map((item) => <option key={item.evidence_id} value={item.evidence_id}>{candidateLabel(item)}</option>)}
              </select>
              {left && <EvidencePreview item={left} />}

              <label className="comparison-label" htmlFor="comparison-right">论文 B 的证据（同一问题、不同论文）</label>
              <select id="comparison-right" value={rightId} onChange={(event) => setRightId(event.target.value)}>
                <option value="">选择另一篇论文中的论断</option>
                {rightOptions.map((item) => <option key={item.evidence_id} value={item.evidence_id}>{candidateLabel(item)}</option>)}
              </select>
              {right && <EvidencePreview item={right} />}

              <label className="comparison-label" htmlFor="comparison-decision">你的比较判断</label>
              <select id="comparison-decision" value={decision} onChange={(event) => setDecision(event.target.value as ComparisonDecision)}>
                {Object.entries(decisionLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
              <label className="comparison-label" htmlFor="comparison-note">理由或备注（可选）</label>
              <textarea id="comparison-note" rows={3} maxLength={2000} value={note} onChange={(event) => setNote(event.target.value)} />
              <button className="secondary-button comparison-save" type="button" onClick={saveComparison} disabled={!right || saving}>
                {saving ? '正在保存…' : '保存人工判断'}
              </button>
            </>
          )}
          {error && <p className="form-message error" role="alert">{error}</p>}
          {notice && <p className="form-message success" role="status">{notice}</p>}
        </div>
      )}
      <div className="comparison-history">
        <div className="panel-heading comparison-history-heading">
          <h3>最近比较</h3>
          <button className="text-button" type="button" onClick={refresh} disabled={loading}>刷新</button>
        </div>
        {comparisons.length === 0 ? <p className="state-message">尚无人工比较记录。</p> : (
          <ul className="comparison-list">
            {comparisons.map((item) => {
              const first = candidateById.get(item.left_evidence_id)
              const second = candidateById.get(item.right_evidence_id)
              return (
                <li key={item.id}>
                  <strong>{decisionLabels[item.decision]}</strong>
                  <p>{first ? candidateLabel(first) : `证据 ${item.left_evidence_id}`} ↔ {second ? candidateLabel(second) : `证据 ${item.right_evidence_id}`}</p>
                  {item.note && <p>{item.note}</p>}
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </section>
  )
}

function samePair(left: EvidenceComparison, right: EvidenceComparison): boolean {
  return (left.left_evidence_id === right.left_evidence_id && left.right_evidence_id === right.right_evidence_id) ||
    (left.left_evidence_id === right.right_evidence_id && left.right_evidence_id === right.left_evidence_id)
}

function EvidencePreview({ item }: { item: ComparisonCandidate }) {
  return (
    <div className="comparison-preview">
      <p><strong>问题：</strong>{item.question}</p>
      <p><strong>论断：</strong>{item.claim}</p>
      <blockquote>{item.quote}</blockquote>
      <p className="comparison-source">{item.original_filename} · 第 {item.page_number} 页 · 证据原关系：{item.relation}</p>
    </div>
  )
}
