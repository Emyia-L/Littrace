import { useEffect, useState } from 'react'
import { getReportDraft, listReportCandidates } from '../api/runs'
import { MarkdownDocument } from './MarkdownDocument'

interface ReportDraftProps {
  projectId: string
  runId: string
  runFinished: boolean
  refreshKey: number
}

export function ReportDraft({ projectId, runId, runFinished, refreshKey }: ReportDraftProps) {
  const [markdown, setMarkdown] = useState('')
  const [candidateCount, setCandidateCount] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [retryCount, setRetryCount] = useState(0)

  useEffect(() => {
    if (!runFinished) return
    const controller = new AbortController()
    setLoading(true)
    setError('')

    // 一个接口给出报告正文，另一个给出通过 Gate 且最终人工确认的证据数量。
    Promise.all([
      getReportDraft(projectId, runId, controller.signal),
      listReportCandidates(projectId, runId, controller.signal),
    ])
      .then(([draft, candidates]) => {
        setMarkdown(draft)
        setCandidateCount(candidates.length)
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : '报告草稿加载失败')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [projectId, runId, runFinished, refreshKey, retryCount])

  return (
    <section className="panel run-results report-panel" aria-labelledby="report-heading">
      <div className="panel-heading">
        <div>
          <p className="section-kicker">REPORT DRAFT</p>
          <h2 id="report-heading">Markdown 报告草稿</h2>
        </div>
        <span className="panel-hint">{candidateCount} 条人工确认的证据</span>
      </div>
      <p className="report-explanation">
        草稿只收录 Gate 通过且最新一次人工决定为“确认”的证据。正文由后端按模板排版，不会额外生成新的论断。
      </p>
      {!runFinished && <p className="state-message">运行结束后即可生成报告草稿。</p>}
      {runFinished && loading && <p className="state-message">正在生成草稿…</p>}
      {runFinished && error && (
        <div className="report-error">
          <p className="error" role="alert">草稿加载失败：{error}</p>
          <button className="secondary-button" type="button" onClick={() => setRetryCount((count) => count + 1)}>
            重试加载草稿
          </button>
        </div>
      )}
      {runFinished && !loading && !error && (
        <>
          <MarkdownDocument
            markdown={markdown}
            filename={`littrace-report-${runId}.md`}
            printTarget="run"
          />
        </>
      )}
    </section>
  )
}
