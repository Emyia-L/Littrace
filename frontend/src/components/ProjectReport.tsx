import { useEffect, useState } from 'react'
import { getProjectReportDraft, type ProjectReportDraft } from '../api/projects'
import { MarkdownDocument } from './MarkdownDocument'

interface ProjectReportProps {
  projectId: string
  comparisonRevision: number
}

export function ProjectReport({ projectId, comparisonRevision }: ProjectReportProps) {
  const [report, setReport] = useState<ProjectReportDraft | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function loadReport() {
    setLoading(true)
    setError('')
    try {
      // 每次点击都重新从后端读取，确保刚完成的人工复核能反映在报告里。
      setReport(await getProjectReportDraft(projectId))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '项目综述加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (comparisonRevision === 0) return
    const controller = new AbortController()
    // 人工比较写入数据库后再读取报告，后端会包含最新的比较决定。
    getProjectReportDraft(projectId, controller.signal)
      .then((nextReport) => {
        if (!controller.signal.aborted) {
          setReport(nextReport)
          setError('')
        }
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : '项目综述加载失败')
        }
      })
    return () => controller.abort()
  }, [projectId, comparisonRevision])

  return (
    <section className="panel project-report-panel" aria-labelledby="project-report-heading">
      <div className="panel-heading">
        <div>
          <p className="section-kicker">PROJECT EVIDENCE REVIEW</p>
          <h2 id="project-report-heading">项目级证据综述</h2>
        </div>
        {report && (
          <span className="panel-hint">
            {report.included_evidence_count} 条证据 · {report.included_comparison_count} 条人工比较
          </span>
        )}
      </div>
      <p className="report-explanation">
        汇总本项目所有已完成运行中通过 Gate 且最新人工决定为“确认”的证据，并保留各自的问题、论文与页码。
        报告末尾单独列出最新人工保存的跨论文比较；系统不会根据证据卡关系标签自动判断论文间冲突。
      </p>
      <div className="project-report-controls">
        <button className="secondary-button" type="button" onClick={loadReport} disabled={loading}>
          {loading ? '正在整理证据…' : report ? '刷新项目综述' : '生成项目综述'}
        </button>
      </div>
      {error && <p className="state-message error" role="alert">综述加载失败：{error}</p>}
      {report && !loading && (
        <MarkdownDocument
          markdown={report.markdown}
          filename={`littrace-project-report-${projectId}.md`}
          printTarget="project"
        />
      )}
    </section>
  )
}
