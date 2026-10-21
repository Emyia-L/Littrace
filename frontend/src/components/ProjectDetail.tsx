import { useEffect, useRef, useState, type FormEvent } from 'react'
import { getProject, type Project } from '../api/projects'
import { listPapers, paperFileUrl, uploadPaper, type Paper } from '../api/papers'
import { PaperChunks } from './PaperChunks'
import { ResearchRuns } from './ResearchRuns'
import { RunDetail } from './RunDetail'
import { ProjectReport } from './ProjectReport'
import { ProjectComparisons } from './ProjectComparisons'

const paperStatusLabels: Record<Paper['status'], string> = {
  uploaded: '已上传',
  parsing: '解析中',
  parsed: '已解析',
  failed: '处理失败',
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date(value))
}

function paperIdFromHash(projectId: string): string | null {
  const match = window.location.hash.match(/^#\/projects\/([^/?#]+)\/papers\/([^/?#]+)$/)
  // 只处理当前项目的论文地址；避免切换项目时复用旧论文 ID。
  return match?.[1] === projectId ? match[2] : null
}

function runIdFromHash(projectId: string): string | null {
  const match = window.location.hash.match(/^#\/projects\/([^/?#]+)\/runs\/([^/?#]+)$/)
  return match?.[1] === projectId ? match[2] : null
}

interface ProjectDetailProps {
  projectId: string
  onBack: () => void
  onProjectChanged: (project: Project) => void
}

export function ProjectDetail({ projectId, onBack, onProjectChanged }: ProjectDetailProps) {
  const [project, setProject] = useState<Project | null>(null)
  const [papers, setPapers] = useState<Paper[]>([])
  const [selectedPaperId, setSelectedPaperId] = useState(() => paperIdFromHash(projectId))
  const [selectedRunId, setSelectedRunId] = useState(() => runIdFromHash(projectId))
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [uploadError, setUploadError] = useState('')
  const [notice, setNotice] = useState('')
  const [comparisonRevision, setComparisonRevision] = useState(0)
  const fileInput = useRef<HTMLInputElement>(null)

  useEffect(() => {
    // 用户前进/后退或直接粘贴链接时，让页面状态跟随地址栏。
    const followHash = () => {
      setSelectedPaperId(paperIdFromHash(projectId))
      setSelectedRunId(runIdFromHash(projectId))
    }
    followHash()
    window.addEventListener('hashchange', followHash)
    return () => window.removeEventListener('hashchange', followHash)
  }, [projectId])

  function openPaper(paperId: string) {
    window.location.hash = `#/projects/${projectId}/papers/${paperId}`
    setSelectedPaperId(paperId)
  }

  function backToPapers() {
    window.location.hash = `#/projects/${projectId}`
    setSelectedPaperId(null)
  }

  function openRun(runId: string) {
    window.location.hash = `#/projects/${projectId}/runs/${runId}`
    setSelectedRunId(runId)
  }

  function backToRuns() {
    window.location.hash = `#/projects/${projectId}`
    setSelectedRunId(null)
  }

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setLoadError('')
    // 项目基本信息和论文列表互不依赖，可以同时请求。
    Promise.all([
      getProject(projectId, controller.signal),
      listPapers(projectId, controller.signal),
    ])
      .then(([loadedProject, loadedPapers]) => {
        setProject(loadedProject)
        setPapers(loadedPapers)
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setLoadError(error instanceof Error ? error.message : '项目加载失败')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [projectId])

  async function handleUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setUploadError('')
    setNotice('')
    if (!selectedFile) {
      setUploadError('请先选择一份 PDF 文件。')
      return
    }
    if (!selectedFile.name.toLowerCase().endsWith('.pdf')) {
      setUploadError('目前只支持 PDF 文件。')
      return
    }
    if (selectedFile.size > 20 * 1024 * 1024) {
      setUploadError('文件超过默认的 20 MB 上传限制。')
      return
    }

    setUploading(true)
    try {
      // 上传接口会同步完成解析与向量化，较大论文可能需要等待。
      const paper = await uploadPaper(projectId, selectedFile)
      setPapers((current) => [paper, ...current])
      // 后端在第一次成功上传后会把 draft 改为 papers_uploaded。
      setProject((current) => current?.status === 'draft'
        ? { ...current, status: 'papers_uploaded' }
        : current)
      if (project?.status === 'draft') {
        onProjectChanged({ ...project, status: 'papers_uploaded' })
      }
      setSelectedFile(null)
      if (fileInput.current) fileInput.current.value = ''
      setNotice(`「${paper.original_filename}」已解析并加入论文列表。`)
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : '上传失败')
    } finally {
      setUploading(false)
    }
  }

  const selectedPaper = papers.find((paper) => paper.id === selectedPaperId)

  return (
    <main className="main-content detail-main">
      {!selectedPaperId && !selectedRunId && (
        <button className="back-button" type="button" onClick={onBack}>← 返回项目列表</button>
      )}
      {loading && <p className="state-message">正在加载项目与论文…</p>}
      {!loading && loadError && (
        <p className="state-message error" role="alert">加载失败：{loadError}</p>
      )}
      {!loading && project && selectedPaperId && selectedPaper && (
        <PaperChunks
          key={selectedPaper.id}
          projectId={projectId}
          paper={selectedPaper}
          onBack={backToPapers}
        />
      )}
      {!loading && project && selectedPaperId && !selectedPaper && (
        <div className="reader-missing">
          <p className="state-message error" role="alert">该项目中找不到这篇论文。</p>
          <button className="secondary-button" type="button" onClick={backToPapers}>返回论文列表</button>
        </div>
      )}
      {!loading && project && selectedRunId && (
        <RunDetail
          key={selectedRunId}
          projectId={projectId}
          runId={selectedRunId}
          onBack={backToRuns}
          onOpen={openRun}
        />
      )}
      {!loading && project && !selectedPaperId && !selectedRunId && (
        <>
          <div className="detail-intro">
            <div>
              <p className="eyebrow">PROJECT DETAIL</p>
              <h1>{project.title}</h1>
              <p className="intro-copy">研究问题：{project.research_question}</p>
            </div>
            <span className="detail-count">{papers.length} 篇论文</span>
          </div>

          <div className="workspace-grid">
            <section className="panel projects-panel" aria-labelledby="papers-heading">
              <div className="panel-heading">
                <div>
                  <p className="section-kicker">PAPER LIBRARY</p>
                  <h2 id="papers-heading">论文列表</h2>
                </div>
                <span className="panel-hint">最近上传优先</span>
              </div>
              {papers.length === 0 ? (
                <div className="empty-state">
                  <span className="empty-icon" aria-hidden="true">＋</span>
                  <h3>这个项目还没有论文</h3>
                  <p>上传带文本层的 PDF，系统会提取页面并建立检索索引。</p>
                </div>
              ) : (
                <ul className="project-list">
                  {papers.map((paper) => (
                    <li className="project-card paper-card" key={paper.id}>
                      <div className="project-card-top">
                        <span className="status-badge">{paperStatusLabels[paper.status]}</span>
                        <time dateTime={paper.created_at}>{formatDate(paper.created_at)}</time>
                      </div>
                      <h3>{paper.original_filename}</h3>
                      <p className="project-question">
                        {paper.page_count === null ? '页数未知' : `${paper.page_count} 页`}
                        <span className="metadata-separator">·</span>
                        SHA-256 {paper.content_hash.slice(0, 12)}…
                      </p>
                      <button className="text-button" type="button" onClick={() => openPaper(paper.id)}>
                        查看解析文本 →
                      </button>
                      <span className="paper-action-separator" aria-hidden="true">·</span>
                      <a
                        className="paper-link"
                        href={paperFileUrl(projectId, paper.id)}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        查看原始 PDF ↗
                      </a>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="panel create-panel" aria-labelledby="upload-heading">
              <div className="panel-heading">
                <div>
                  <p className="section-kicker">ADD PAPER</p>
                  <h2 id="upload-heading">上传论文</h2>
                </div>
              </div>
              <p className="form-description">支持含可提取文字的 PDF。扫描版 PDF 暂未接入 OCR。</p>
              <form onSubmit={handleUpload}>
                <label htmlFor="paper-file">选择 PDF 文件</label>
                <input
                  ref={fileInput}
                  id="paper-file"
                  className="file-input"
                  type="file"
                  accept=".pdf,application/pdf"
                  onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)}
                  disabled={uploading}
                />
                <p className="field-help">单文件默认不超过 20 MB；上传后会同步解析、切块并生成向量。</p>
                {uploadError && <p className="form-message error" role="alert">{uploadError}</p>}
                {notice && <p className="form-message success" role="status">{notice}</p>}
                <button className="primary-button" type="submit" disabled={uploading || !selectedFile}>
                  {uploading ? '解析与入库中，请稍候…' : '上传并解析 PDF'}
                  <span aria-hidden="true">→</span>
                </button>
              </form>
            </section>
          </div>
          <ResearchRuns
            projectId={projectId}
            initialQuestion={project.research_question}
            hasPapers={papers.some((paper) => paper.status === 'parsed')}
            onOpen={openRun}
          />
          <ProjectReport key={`${projectId}-report`} projectId={projectId} comparisonRevision={comparisonRevision} />
          <ProjectComparisons
            key={`${projectId}-comparisons`}
            projectId={projectId}
            onSaved={() => setComparisonRevision((revision) => revision + 1)}
          />
        </>
      )}
    </main>
  )
}
