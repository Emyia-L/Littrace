import { useEffect, useState, type FormEvent } from 'react'
import { createProject, listProjects, type Project } from './api/projects'
import { ProjectDetail } from './components/ProjectDetail'
import './App.css'

const statusLabels: Record<Project['status'], string> = {
  draft: '草稿',
  papers_uploaded: '已上传论文',
  ready_for_screening: '待筛选',
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date(value))
}

function projectIdFromHash(): string | null {
  // 论文阅读页是项目详情的子页面；两种地址都由同一个项目组件承载。
  return window.location.hash.match(/^#\/projects\/([^/?#]+)(?:\/(?:papers|runs)\/[^/?#]+)?$/)?.[1] ?? null
}

function App() {
  const [selectedProjectId, setSelectedProjectId] = useState(projectIdFromHash)
  const [projects, setProjects] = useState<Project[]>([])
  const [title, setTitle] = useState('')
  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [formError, setFormError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    const followHash = () => setSelectedProjectId(projectIdFromHash())
    window.addEventListener('hashchange', followHash)
    return () => window.removeEventListener('hashchange', followHash)
  }, [])

  function openProject(projectId: string) {
    window.location.hash = `#/projects/${projectId}`
    setSelectedProjectId(projectId)
  }

  function backToProjects() {
    window.location.hash = '#/'
    setSelectedProjectId(null)
  }

  useEffect(() => {
    // 卸载页面时取消请求；React 开发模式可能重新执行 effect，
    // AbortController 可防止上一次请求随后覆盖当前页面状态。
    const controller = new AbortController()
    listProjects(controller.signal)
      .then(setProjects)
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setLoadError(error instanceof Error ? error.message : '项目加载失败')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [])

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault() // 阻止浏览器默认提交导致页面刷新。
    setFormError('')
    setNotice('')

    const cleanTitle = title.trim()
    const cleanQuestion = question.trim()
    if (!cleanTitle || !cleanQuestion) {
      setFormError('请填写项目名称和研究问题。')
      return
    }

    setSaving(true)
    try {
      const project = await createProject({
        title: cleanTitle,
        research_question: cleanQuestion,
      })
      // 后端返回的是已保存的完整项目，直接放到列表首位。
      setProjects((current) => [project, ...current])
      setTitle('')
      setQuestion('')
      setNotice('项目已创建，可以继续上传论文。')
      openProject(project.id)
    } catch (error) {
      setFormError(error instanceof Error ? error.message : '创建项目失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand" aria-label="LitTrace 文献证据工作台">
          <span className="brand-mark">L<span>·</span>T</span>
          <span className="brand-name">LitTrace</span>
        </div>
        <span className="topbar-caption">可追溯文献研究工作台</span>
      </header>

      {selectedProjectId ? (
        <ProjectDetail
          projectId={selectedProjectId}
          onBack={backToProjects}
          onProjectChanged={(updated) => setProjects((current) => current.map(
            (item) => item.id === updated.id ? updated : item,
          ))}
        />
      ) : (
      <main className="main-content">
        <div className="page-intro">
          <div>
            <p className="eyebrow">LITTRACE / RESEARCH WORKSPACE</p>
            <h1>让每一条研究结论<br />都有出处。</h1>
            <p className="intro-copy">从问题出发，组织论文，追踪 Agent 的检索与判断，最终回到可核对的原文证据。</p>
          </div>
          <div className="project-count" aria-label={`当前显示 ${projects.length} 个项目`}>
            <strong>{projects.length}</strong>
            <span>个项目</span>
          </div>
        </div>

        <div className="workflow-strip" aria-label="研究流程">
          <span><b>01</b> 建立问题</span>
          <span><b>02</b> 上传论文</span>
          <span><b>03</b> 检索并核验证据</span>
          <span><b>04</b> 人工复核与报告</span>
        </div>

        <div className="workspace-grid">
          <section className="panel projects-panel" aria-labelledby="projects-heading">
            <div className="panel-heading">
              <div>
                <p className="section-kicker">PROJECTS</p>
                <h2 id="projects-heading">项目列表</h2>
              </div>
              <span className="panel-hint">最近创建优先</span>
            </div>

            {loading && <p className="state-message">正在加载项目…</p>}
            {!loading && loadError && (
              <p className="state-message error" role="alert">
                无法加载项目：{loadError}。请确认 FastAPI 已在 127.0.0.1:8000 启动。
              </p>
            )}
            {!loading && !loadError && projects.length === 0 && (
              <div className="empty-state">
                <span className="empty-icon" aria-hidden="true">＋</span>
                <h3>还没有研究项目</h3>
                <p>在右侧填写研究问题，创建你的第一个项目。</p>
              </div>
            )}
            {!loading && projects.length > 0 && (
              <ul className="project-list">
                {projects.map((project) => (
                  <li className="project-card" key={project.id}>
                    <div className="project-card-top">
                      <span className="status-badge">{statusLabels[project.status]}</span>
                      <time dateTime={project.created_at}>{formatDate(project.created_at)}</time>
                    </div>
                    <h3>{project.title}</h3>
                    <p className="project-question">{project.research_question}</p>
                    <p className="project-id">ID {project.id}</p>
                    <button className="text-button" type="button" onClick={() => openProject(project.id)}>
                      查看项目与论文 →
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="panel create-panel" aria-labelledby="create-heading">
            <div className="panel-heading">
              <div>
                <p className="section-kicker">NEW RESEARCH</p>
                <h2 id="create-heading">创建项目</h2>
              </div>
            </div>
            <p className="form-description">先记录研究主题与核心问题，后续再向项目上传论文。</p>
            <form onSubmit={handleCreate}>
              <label htmlFor="project-title">项目名称</label>
              <input
                id="project-title"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                maxLength={200}
                placeholder="例如：多模态 Agent 文献研究"
                required
              />

              <label htmlFor="research-question">研究问题</label>
              <textarea
                id="research-question"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                maxLength={5000}
                rows={5}
                placeholder="例如：主动感知代理解决了什么问题？"
                required
              />
              <p className="field-help">尽量写成可回答的具体问题，便于后续检索和核验证据。</p>

              {formError && <p className="form-message error" role="alert">{formError}</p>}
              {notice && <p className="form-message success" role="status">{notice}</p>}
              <button className="primary-button" type="submit" disabled={saving || loading}>
                {saving ? '创建中…' : '创建研究项目'}
                <span aria-hidden="true">→</span>
              </button>
            </form>
          </section>
        </div>
      </main>
      )}
    </div>
  )
}

export default App
