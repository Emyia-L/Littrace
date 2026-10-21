import ReactMarkdown from 'react-markdown'
import remarkBreaks from 'remark-breaks'
import remarkGfm from 'remark-gfm'

interface MarkdownDocumentProps {
  markdown: string
  filename: string
  printTarget: 'project' | 'run'
}

export function MarkdownDocument({ markdown, filename, printTarget }: MarkdownDocumentProps) {
  function downloadMarkdown() {
    // 把后端的原始草稿完整保存下来，方便在编辑器中继续修改。
    const file = new Blob([markdown], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(file)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = filename
    anchor.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 0)
  }

  function printDocument() {
    // 浏览器的“打印/另存为 PDF”保留排版；只显示当前报告，不打印工作台控件。
    document.body.dataset.printReport = printTarget
    window.addEventListener('afterprint', () => {
      delete document.body.dataset.printReport
    }, { once: true })
    window.print()
  }

  return (
    <>
      <div className="report-actions">
        <button className="secondary-button" type="button" onClick={printDocument} disabled={!markdown}>
          打印 / 保存为 PDF
        </button>
        <button className="secondary-button" type="button" onClick={downloadMarkdown} disabled={!markdown}>
          下载 Markdown
        </button>
      </div>
      {/* react-markdown 默认不执行草稿中的原始 HTML，来源文字仍按文本安全呈现。 */}
      <article className="report-document" data-report-print-target={printTarget}>
        {/* 后端模板用单个换行分隔“论断 / 来源 / 证据 ID”，显示时也保留这些换行。 */}
        <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{markdown}</ReactMarkdown>
      </article>
      <details className="report-source">
        <summary>查看 Markdown 原文</summary>
        <pre className="report-markdown">{markdown}</pre>
      </details>
    </>
  )
}
