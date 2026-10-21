/** 与 FastAPI 的 ProjectResponse 对应；时间在 JSON 中表现为 ISO 字符串。 */
import { requestJson } from './client'

export interface Project {
  id: string
  title: string
  research_question: string
  status: 'draft' | 'papers_uploaded' | 'ready_for_screening'
  created_at: string
  updated_at: string
}

/** 创建项目时只提交用户可填写的字段，ID 和状态由后端生成。 */
export interface CreateProjectInput {
  title: string
  research_question: string
}

/** 项目级报告由后端汇总所有已完成运行中的人工确认证据。 */
export interface ProjectReportDraft {
  project_id: string
  included_evidence_count: number
  included_comparison_count: number
  markdown: string
}

export type ComparisonDecision = 'mutually_support' | 'conflicting' | 'inconclusive'

export interface ComparisonCandidate {
  evidence_id: string
  run_id: string
  question: string
  relation: 'supports' | 'contradicts' | 'mixed' | 'insufficient'
  claim: string
  quote: string
  paper_id: string
  original_filename: string
  page_number: number
}

export interface EvidenceComparison {
  id: string
  review_order: number
  project_id: string
  left_evidence_id: string
  right_evidence_id: string
  decision: ComparisonDecision
  note: string | null
  created_at: string
}

export type SynthesisRelationship = 'agreement' | 'conflict' | 'complementary' | 'inconclusive'

export interface ProjectSynthesis {
  id: string
  project_id: string
  triggered_by_run_id: string | null
  question: string
  model: string
  sources: Array<{
    evidence_id: string
    paper_id: string
    original_filename: string
    page_number: number
    claim: string
    quote: string
  }>
  result: {
    accepted: Array<{
      title: string
      conclusion: string
      relationship: SynthesisRelationship
      source_evidence_ids: string[]
      limitations: string[]
      support_check: { verdict: 'supported'; reason: string }
    }>
    rejected: Array<{
      title: string
      conclusion: string
      relationship: SynthesisRelationship
      source_evidence_ids: string[]
      limitations: string[]
      rejection_reason: string
      support_check: { verdict: 'supported' | 'unsupported' | 'uncertain'; reason: string } | null
    }>
  }
  model_usage: {
    call_count: number
    prompt_tokens: number
    completion_tokens: number
    total_tokens: number
    duration_ms: number
    estimated_cost_usd: string
  }
  created_at: string
}

export function listProjects(signal?: AbortSignal): Promise<Project[]> {
  // 第一版页面取最近 100 个项目；以后可把 offset/limit 接到分页控件。
  return requestJson<Project[]>('/projects?offset=0&limit=100', { signal })
}

export function getProject(projectId: string, signal?: AbortSignal): Promise<Project> {
  return requestJson<Project>(`/projects/${encodeURIComponent(projectId)}`, { signal })
}

export function createProject(input: CreateProjectInput): Promise<Project> {
  return requestJson<Project>('/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
}

export function getProjectReportDraft(projectId: string, signal?: AbortSignal): Promise<ProjectReportDraft> {
  return requestJson<ProjectReportDraft>(
    `/projects/${encodeURIComponent(projectId)}/report-draft`,
    { signal },
  )
}

export function listComparisonCandidates(projectId: string, signal?: AbortSignal): Promise<ComparisonCandidate[]> {
  return requestJson<ComparisonCandidate[]>(
    `/projects/${encodeURIComponent(projectId)}/comparison-candidates`,
    { signal },
  )
}

export function listProjectComparisons(projectId: string, signal?: AbortSignal): Promise<EvidenceComparison[]> {
  return requestJson<EvidenceComparison[]>(
    `/projects/${encodeURIComponent(projectId)}/comparisons`,
    { signal },
  )
}

export function createProjectComparison(
  projectId: string,
  input: Pick<EvidenceComparison, 'left_evidence_id' | 'right_evidence_id' | 'decision' | 'note'>,
): Promise<EvidenceComparison> {
  return requestJson<EvidenceComparison>(`/projects/${encodeURIComponent(projectId)}/comparisons`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
}

export function listProjectSyntheses(projectId: string, signal?: AbortSignal): Promise<ProjectSynthesis[]> {
  return requestJson<ProjectSynthesis[]>(`/projects/${encodeURIComponent(projectId)}/syntheses`, { signal })
}

export function createProjectSynthesis(
  projectId: string,
  input: { question: string; max_sources?: number; max_points?: number },
): Promise<ProjectSynthesis> {
  return requestJson<ProjectSynthesis>(`/projects/${encodeURIComponent(projectId)}/syntheses`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
}
