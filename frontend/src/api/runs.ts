import { requestJson, requestText } from './client'

/** 与后端 ResearchRunResponse 对齐；一次运行记录可独立于页面刷新存在。 */
export interface ResearchRun {
  id: string
  project_id: string
  retry_of_run_id: string | null
  question: string
  top_k: number
  max_cards: number
  status: 'pending' | 'queued' | 'running' | 'completed' | 'failed'
  execution_mode: 'workflow' | 'agent' | null
  agent_stop_reason: 'finish' | 'step_limit' | null
  agent_steps_used: number | null
  model_call_count: number
  model_prompt_tokens: number
  model_completion_tokens: number
  model_total_tokens: number
  model_duration_ms: number
  estimated_cost_microusd: number
  error_message: string | null
  created_at: string
  updated_at: string
  started_at: string | null
  finished_at: string | null
}

export interface RunMetrics {
  run_id: string
  accepted_evidence_count: number
  rejected_evidence_count: number
  gate_acceptance_rate: number | null
  human_confirmed_count: number
  human_dismissed_count: number
  human_pending_count: number
  unique_evidence_chunk_count: number
  cross_run_memory_count: number
  warnings: string[]
  model_usage: {
    call_count: number
    prompt_tokens: number
    completion_tokens: number
    total_tokens: number
    duration_ms: number
    estimated_cost_usd: string
  }
}

export interface RunMemory {
  id: string
  source_evidence_id: string
  source_run_id: string
  summary: string
  created_at: string
}

export interface ResearchRunCreate {
  question: string
  top_k: number
  max_cards: number
}

export interface EvidenceRecord {
  id: string
  run_id: string
  decision: 'accepted' | 'rejected'
  evidence: {
    question: string
    claim: string
    relation: 'supports' | 'contradicts' | 'mixed' | 'insufficient'
    quote: string
    page_number: number
    chunk_id: string
    limitations: string[]
  }
  citation_check: { valid: boolean; reason: string | null }
  claim_support_check: {
    verdict: 'supported' | 'unsupported' | 'uncertain'
    reason: string
  } | null
  created_at: string
}

/** 来源接口只对 Gate 已接受的证据开放。 */
export interface EvidenceSource {
  evidence_id: string
  paper_id: string
  original_filename: string
  page_number: number
  chunk_id: string
  quote: string
  file_url: string
}

export interface EvidenceReview {
  id: string
  review_order: number
  evidence_id: string
  decision: 'confirmed' | 'dismissed'
  note: string | null
  created_at: string
}

export interface ReviewQueueItem {
  evidence: EvidenceRecord
  reviews: EvidenceReview[]
}

export interface AgentStep {
  id: string
  run_id: string
  step_number: number
  action_kind: 'search' | 'extract_evidence' | 'synthesize_project' | 'finish'
  action_payload: {
    kind: AgentStep['action_kind']
    strategy?: 'keyword' | 'semantic' | 'hybrid'
    query?: string
    top_k?: number
    chunk_ids?: string[]
    max_cards?: number
    max_sources?: number
    max_points?: number
    reason?: string
  }
  observation_summary: string | null
  recorded_at: string
}

function runBase(projectId: string): string {
  return `/projects/${encodeURIComponent(projectId)}/runs`
}

export function listResearchRuns(projectId: string, signal?: AbortSignal): Promise<ResearchRun[]> {
  return requestJson<ResearchRun[]>(`${runBase(projectId)}?limit=20`, { signal })
}

export function createResearchRun(projectId: string, data: ResearchRunCreate): Promise<ResearchRun> {
  return requestJson<ResearchRun>(runBase(projectId), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export function getResearchRun(projectId: string, runId: string, signal?: AbortSignal): Promise<ResearchRun> {
  return requestJson<ResearchRun>(`${runBase(projectId)}/${encodeURIComponent(runId)}`, { signal })
}

export function startResearchRun(
  projectId: string,
  runId: string,
  mode: 'workflow' | 'agent',
): Promise<ResearchRun> {
  // execute-async 返回 202 和 queued；独立 Worker 稍后领取并执行研究任务。
  return requestJson<ResearchRun>(
    `${runBase(projectId)}/${encodeURIComponent(runId)}/execute-async?execution_mode=${mode}`,
    { method: 'POST' },
  )
}

/** 后端以 SSE 推送执行状态和 Agent 步骤；相对当前页面同源访问开发代理。 */
export function researchRunEventsUrl(projectId: string, runId: string): string {
  return `/api${runBase(projectId)}/${encodeURIComponent(runId)}/events?after_step=0`
}

/** 为失败运行创建新的 pending 运行；输入参数由后端从原运行复制。 */
export function retryResearchRun(projectId: string, runId: string): Promise<ResearchRun> {
  return requestJson<ResearchRun>(`${runBase(projectId)}/${encodeURIComponent(runId)}/retry`, {
    method: 'POST',
  })
}

export function listRunEvidence(projectId: string, runId: string, signal?: AbortSignal): Promise<EvidenceRecord[]> {
  return requestJson<EvidenceRecord[]>(`${runBase(projectId)}/${encodeURIComponent(runId)}/evidence`, { signal })
}

export function listRunSteps(projectId: string, runId: string, signal?: AbortSignal): Promise<AgentStep[]> {
  return requestJson<AgentStep[]>(`${runBase(projectId)}/${encodeURIComponent(runId)}/steps`, { signal })
}

/** 成本与质量采用独立指标，避免一个无法解释的“综合分数”。 */
export function getRunMetrics(projectId: string, runId: string, signal?: AbortSignal): Promise<RunMetrics> {
  return requestJson<RunMetrics>(`${runBase(projectId)}/${encodeURIComponent(runId)}/metrics`, { signal })
}

/** 读取本次规划器实际使用的、可追溯到历史人工确认的记忆。 */
export function listRunMemory(projectId: string, runId: string, signal?: AbortSignal): Promise<RunMemory[]> {
  return requestJson<RunMemory[]>(`${runBase(projectId)}/${encodeURIComponent(runId)}/memory`, { signal })
}

function evidenceBase(projectId: string, runId: string, evidenceId: string): string {
  return `${runBase(projectId)}/${encodeURIComponent(runId)}/evidence/${encodeURIComponent(evidenceId)}`
}

export function getEvidenceSource(
  projectId: string, runId: string, evidenceId: string, signal?: AbortSignal,
): Promise<EvidenceSource> {
  return requestJson<EvidenceSource>(`${evidenceBase(projectId, runId, evidenceId)}/source`, { signal })
}

export function listReviewQueue(projectId: string, runId: string, signal?: AbortSignal): Promise<ReviewQueueItem[]> {
  return requestJson<ReviewQueueItem[]>(`${runBase(projectId)}/${encodeURIComponent(runId)}/review-queue`, { signal })
}

export function listReportCandidates(projectId: string, runId: string, signal?: AbortSignal): Promise<EvidenceRecord[]> {
  return requestJson<EvidenceRecord[]>(`${runBase(projectId)}/${encodeURIComponent(runId)}/report-candidates`, { signal })
}

/** 后端按 Markdown 纯文本返回草稿，所以使用 requestText 而非 JSON 解析器。 */
export function getReportDraft(projectId: string, runId: string, signal?: AbortSignal): Promise<string> {
  return requestText(`${runBase(projectId)}/${encodeURIComponent(runId)}/report-draft`, { signal })
}

export function createEvidenceReview(
  projectId: string,
  runId: string,
  evidenceId: string,
  decision: EvidenceReview['decision'],
  note: string | null,
): Promise<EvidenceReview> {
  // 后端采用追加记录：这次决定不会覆盖之前的人工意见或 Gate 结果。
  return requestJson<EvidenceReview>(`${evidenceBase(projectId, runId, evidenceId)}/reviews`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decision, note }),
  })
}
