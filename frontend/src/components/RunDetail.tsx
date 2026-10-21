import { useEffect, useState } from 'react'
import {
  getResearchRun,
  getRunMetrics,
  listReviewQueue,
  listRunEvidence,
  listRunMemory,
  listRunSteps,
  researchRunEventsUrl,
  retryResearchRun,
  startResearchRun,
  type AgentStep,
  type EvidenceRecord,
  type EvidenceReview,
  type ResearchRun,
  type RunMemory,
  type RunMetrics,
  type ReviewQueueItem,
} from '../api/runs'
import { EvidenceReviewPanel } from './EvidenceReviewPanel'
import { ReportDraft } from './ReportDraft'

const statusLabels: Record<ResearchRun['status'], string> = {
  pending: '待启动', queued: '排队中', running: '运行中', completed: '已完成', failed: '失败',
}

const actionLabels: Record<AgentStep['action_kind'], string> = {
  search: '检索文本块',
  extract_evidence: '提取并验证证据',
  synthesize_project: '跨论文自动综合',
  finish: '结束运行',
}

interface RunDetailProps {
  projectId: string
  runId: string
  onBack: () => void
  onOpen: (runId: string) => void
}

export function RunDetail({ projectId, runId, onBack, onOpen }: RunDetailProps) {
  const [run, setRun] = useState<ResearchRun | null>(null)
  const [evidence, setEvidence] = useState<EvidenceRecord[]>([])
  const [steps, setSteps] = useState<AgentStep[]>([])
  const [reviewQueue, setReviewQueue] = useState<ReviewQueueItem[]>([])
  const [metrics, setMetrics] = useState<RunMetrics | null>(null)
  const [memories, setMemories] = useState<RunMemory[]>([])
  const [reviewRevision, setReviewRevision] = useState(0)
  const [mode, setMode] = useState<'agent' | 'workflow'>('agent')
  const [loading, setLoading] = useState(true)
  const [starting, setStarting] = useState(false)
  const [retrying, setRetrying] = useState(false)
  const [error, setError] = useState('')
  const [actionError, setActionError] = useState('')
  const [refreshError, setRefreshError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    // 四份数据各有独立接口：运行、证据 Gate、Agent 轨迹、人工复核。
    Promise.all([
      getResearchRun(projectId, runId, controller.signal),
      listRunEvidence(projectId, runId, controller.signal),
      listRunSteps(projectId, runId, controller.signal),
      listReviewQueue(projectId, runId, controller.signal),
      getRunMetrics(projectId, runId, controller.signal),
      listRunMemory(projectId, runId, controller.signal),
    ])
      .then(([loadedRun, loadedEvidence, loadedSteps, loadedQueue, loadedMetrics, loadedMemories]) => {
        setRun(loadedRun)
        setEvidence(loadedEvidence)
        setSteps(loadedSteps)
        setReviewQueue(loadedQueue)
        setMetrics(loadedMetrics)
        setMemories(loadedMemories)
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '运行加载失败')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [projectId, runId])

  useEffect(() => {
    // 排队时就订阅：Worker 领取后，SSE 才能把 queued → running 推给页面。
    if (run?.status !== 'queued' && run?.status !== 'running') return
    const events = new EventSource(researchRunEventsUrl(projectId, runId))

    // SSE 的 step 事件对应 Agent 完成的一步；按编号去重以兼容自动重连补发。
    events.addEventListener('step', (event) => {
      try {
        const step = JSON.parse((event as MessageEvent<string>).data) as AgentStep
        setSteps((current) => current.some((item) => item.step_number === step.step_number)
          ? current
          : [...current, step].sort((left, right) => left.step_number - right.step_number))
      } catch {
        setRefreshError('收到无法识别的 Agent 步骤事件。')
      }
    })

    // 状态事件是服务端权威状态；终态到达后再读取最终证据和复核队列。
    events.addEventListener('status', (event) => {
      try {
        const latestRun = JSON.parse((event as MessageEvent<string>).data) as ResearchRun
        setRun(latestRun)
        setRefreshError('')
        if (latestRun.status === 'completed' || latestRun.status === 'failed') {
          events.close()
          Promise.all([
            listRunEvidence(projectId, runId),
            listReviewQueue(projectId, runId),
            listRunSteps(projectId, runId),
            getRunMetrics(projectId, runId),
            listRunMemory(projectId, runId),
          ])
            .then(([latestEvidence, latestQueue, latestSteps, latestMetrics, latestMemories]) => {
              setEvidence(latestEvidence)
              setReviewQueue(latestQueue)
              setSteps(latestSteps)
              setMetrics(latestMetrics)
              setMemories(latestMemories)
            })
            .catch((reason: unknown) => {
              setRefreshError(reason instanceof Error ? reason.message : '最终结果加载失败')
            })
        }
      } catch {
        setRefreshError('收到无法识别的运行状态事件。')
      }
    })

    events.onopen = () => setRefreshError('')
    // EventSource 会自动重连；后端按 Last-Event-ID 接续步骤，不丢失轨迹。
    events.onerror = () => setRefreshError('实时连接暂时中断，浏览器正在自动重连…')
    return () => {
      events.close()
    }
  }, [projectId, runId, run?.status])

  async function handleStart() {
    if (!run || run.status !== 'pending') return
    setActionError('')
    setStarting(true)
    try {
      // 用户明确点击才启动模型调用；默认动态 Agent，也可选择固定流程。
      const started = await startResearchRun(projectId, run.id, mode)
      setRun(started)
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : '启动失败')
    } finally {
      setStarting(false)
    }
  }

  async function handleRetry() {
    if (!run || run.status !== 'failed') return
    setActionError('')
    setRetrying(true)
    try {
      const retry = await retryResearchRun(projectId, run.id)
      // 新运行从 pending 开始，用户可再次选择 Agent 或 Workflow。
      onOpen(retry.id)
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : '创建重试运行失败')
    } finally {
      setRetrying(false)
    }
  }

  const acceptedCount = evidence.filter((item) => item.decision === 'accepted').length

  function handleReviewSaved(saved: EvidenceReview) {
    setReviewQueue((current) => current.map((item) => (
      item.evidence.id === saved.evidence_id
        ? { ...item, reviews: [...item.reviews, saved] }
        : item
    )))
    // 复核决定改变后，让报告区重新读取候选证据和 Markdown 草稿。
    setReviewRevision((revision) => revision + 1)
  }

  return (
    <div className="run-detail">
      <button className="back-button" type="button" onClick={onBack}>← 返回研究运行</button>
      {loading && <p className="state-message">正在读取运行详情…</p>}
      {!loading && error && <p className="state-message error" role="alert">加载失败：{error}</p>}
      {!loading && run && (
        <>
          <div className="detail-intro run-detail-intro">
            <div>
              <p className="eyebrow">RESEARCH RUN</p>
              <h1>{run.question}</h1>
              <p className="intro-copy">运行 ID {run.id}</p>
            </div>
            <span className={`status-badge run-status-${run.status}`}>{statusLabels[run.status]}</span>
          </div>

          <section className="panel run-summary" aria-label="运行状态与参数">
            <div className="run-facts">
              <div><span>执行模式</span><strong>{run.execution_mode === 'agent' ? '动态 Agent' : run.execution_mode === 'workflow' ? '固定流程' : '尚未启动'}</strong></div>
              <div><span>候选文本块</span><strong>{run.top_k}</strong></div>
              <div><span>最多证据卡</span><strong>{run.max_cards}</strong></div>
              <div><span>Agent 步骤</span><strong>{run.agent_steps_used ?? '—'}</strong></div>
            </div>
            {run.status === 'pending' && (
              <div className="run-start">
                <label htmlFor="execution-mode">执行方式</label>
                <select id="execution-mode" value={mode} onChange={(event) => setMode(event.target.value as 'agent' | 'workflow')} disabled={starting}>
                  <option value="agent">动态 Agent：模型按观察结果选择下一步</option>
                  <option value="workflow">固定流程：按既定步骤提取证据</option>
                </select>
                <button className="secondary-button" type="button" onClick={handleStart} disabled={starting}>
                  {starting ? '正在启动…' : '启动研究分析'}
                </button>
                <p className="field-help">启动后会调用检索和模型服务，可能产生 API 用量。</p>
                {actionError && <p className="error" role="alert">启动失败：{actionError}</p>}
              </div>
            )}
            {run.status === 'queued' && (
              <p className="run-note" role="status">任务已进入队列，等待 Worker 领取；此时尚未调用模型。</p>
            )}
            {run.status === 'running' && <p className="run-note" role="status">后台分析中，正在实时接收状态与 Agent 行动步骤。</p>}
            {run.status === 'failed' && (
              <div className="run-note error" role="alert">
                <p>运行失败：{run.error_message ?? '请查看后端日志'}</p>
                <button className="secondary-button" type="button" onClick={handleRetry} disabled={retrying}>
                  {retrying ? '正在创建重试…' : '创建重试运行'}
                </button>
                {actionError && <p role="alert">{actionError}</p>}
              </div>
            )}
            {run.status === 'completed' && run.agent_stop_reason === 'step_limit' && (
              <p className="run-note">Agent 达到最大步骤数后停止；请核对证据是否足以回答问题。</p>
            )}
            {refreshError && <p className="run-note error" role="alert">自动刷新失败：{refreshError}</p>}
          </section>

          {metrics && (
            <section className="panel run-summary" aria-labelledby="quality-heading">
              <div className="panel-heading">
                <div><p className="section-kicker">EVALUATION</p><h2 id="quality-heading">成本与质量信号</h2></div>
                <span className="panel-hint">指标分开呈现，不合成黑盒评分</span>
              </div>
              <div className="run-facts">
                <div><span>模型调用</span><strong>{metrics.model_usage.call_count}</strong></div>
                <div><span>总 Token</span><strong>{metrics.model_usage.total_tokens}</strong></div>
                <div><span>估算成本</span><strong>${metrics.model_usage.estimated_cost_usd}</strong></div>
                <div><span>模型耗时</span><strong>{(metrics.model_usage.duration_ms / 1000).toFixed(2)} 秒</strong></div>
                <div><span>Gate 通过</span><strong>{metrics.accepted_evidence_count} / {metrics.accepted_evidence_count + metrics.rejected_evidence_count}</strong></div>
                <div><span>人工确认</span><strong>{metrics.human_confirmed_count}</strong></div>
                <div><span>独立来源块</span><strong>{metrics.unique_evidence_chunk_count}</strong></div>
                <div><span>采用历史记忆</span><strong>{metrics.cross_run_memory_count}</strong></div>
              </div>
              {metrics.warnings.length > 0 && (
                <p className="run-note">质量提示：{metrics.warnings.join('、')}</p>
              )}
              {memories.length > 0 && (
                <details className="run-note">
                  <summary>查看本次使用的跨运行记忆（{memories.length}）</summary>
                  <ol>
                    {memories.map((memory) => (
                      <li key={memory.id}>
                        <p>{memory.summary}</p>
                        <small>来源运行 {memory.source_run_id} · 证据 {memory.source_evidence_id}</small>
                      </li>
                    ))}
                  </ol>
                </details>
              )}
            </section>
          )}

          <section className="panel run-results" aria-labelledby="evidence-heading">
            <div className="panel-heading">
              <div><p className="section-kicker">EVIDENCE</p><h2 id="evidence-heading">证据结果</h2></div>
              <span className="panel-hint">通过 {acceptedCount} / 总计 {evidence.length}</span>
            </div>
            {evidence.length === 0 ? (
              <p className="state-message">{run.status === 'queued' || run.status === 'running' ? '等待证据生成…' : '本次运行没有保存证据。'}</p>
            ) : (
              <ol className="evidence-list">
                {evidence.map((item) => (
                  <li className="evidence-card" key={item.id}>
                    <div className="evidence-card-top">
                      <span className={`status-badge ${item.decision === 'accepted' ? '' : 'run-status-failed'}`}>
                        {item.decision === 'accepted' ? '已通过 Gate' : '未通过 Gate'}
                      </span>
                      <span>原文第 {item.evidence.page_number} 页</span>
                    </div>
                    <h3>{item.evidence.claim}</h3>
                    <blockquote>{item.evidence.quote}</blockquote>
                    <p className="evidence-id">文本块 {item.evidence.chunk_id}</p>
                    {item.decision === 'rejected' && (
                      <p className="evidence-reason error">
                        {item.claim_support_check?.reason ?? item.citation_check.reason ?? '未通过证据检查'}
                      </p>
                    )}
                    {item.decision === 'accepted' && item.claim_support_check && (
                      <p className="evidence-reason">模型核验理由：{item.claim_support_check.reason}</p>
                    )}
                    {item.evidence.limitations.length > 0 && (
                      <p className="evidence-reason">局限：{item.evidence.limitations.join('；')}</p>
                    )}
                    {item.decision === 'accepted' && run.status === 'running' && (
                      <p className="evidence-reason">分析结束后可核对来源并进行人工复核。</p>
                    )}
                    {item.decision === 'accepted' && (run.status === 'completed' || run.status === 'failed') && (
                      <EvidenceReviewPanel
                        projectId={projectId}
                        runId={runId}
                        evidence={item}
                        reviews={reviewQueue.find((entry) => entry.evidence.id === item.id)?.reviews ?? []}
                        onReviewSaved={handleReviewSaved}
                      />
                    )}
                  </li>
                ))}
              </ol>
            )}
          </section>

          <ReportDraft
            projectId={projectId}
            runId={runId}
            runFinished={run.status === 'completed' || run.status === 'failed'}
            refreshKey={reviewRevision}
          />

          {run.execution_mode === 'agent' && (
            <section className="panel run-results" aria-labelledby="steps-heading">
              <div className="panel-heading">
                <div><p className="section-kicker">AGENT TRACE</p><h2 id="steps-heading">行动轨迹</h2></div>
                <span className="panel-hint">{steps.length} 步</span>
              </div>
              {steps.length === 0 ? <p className="state-message">尚无 Agent 步骤。</p> : (
                <ol className="step-list">
                  {steps.map((step) => (
                    <li key={step.id}>
                      <span className="step-number">{step.step_number}</span>
                      <div>
                        <h3>{actionLabels[step.action_kind]}</h3>
                        <p>
                          {step.action_kind === 'search'
                            ? `${step.action_payload.strategy ?? ''} · ${step.action_payload.query ?? ''}`
                            : step.action_kind === 'extract_evidence'
                              ? `选取 ${step.action_payload.chunk_ids?.length ?? 0} 个文本块`
                              : step.action_payload.reason}
                        </p>
                        {step.observation_summary && (
                          <details><summary>查看观察记录</summary><pre>{step.observation_summary}</pre></details>
                        )}
                      </div>
                    </li>
                  ))}
                </ol>
              )}
            </section>
          )}
        </>
      )}
    </div>
  )
}
