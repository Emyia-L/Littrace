from pathlib import Path
from typing import Annotated

import typer

from littrace.evidence.gate import run_evidence_gate
from littrace.ingestion.chunker import chunk_pages
from littrace.ingestion.pdf_parser import extract_pages
from littrace.retriever.keyword_retriever import select_candidate_chunks


app = typer.Typer(
    help="LitTrace 可追溯文献证据提取工具",
)


@app.command()
def extract(
    pdf: Annotated[
        Path,
        typer.Option(
            "--pdf",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="需要分析的 PDF 文件",
        ),
    ],
    question: Annotated[
        str,
        typer.Option(
            "--question",
            help="需要研究的问题",
        ),
    ],
    paper_id: Annotated[
        str,
        typer.Option(
            "--paper-id",
            help="论文的唯一标识",
        ),
    ] = "paper-001",
    max_chunks: Annotated[
        int,
        typer.Option(
            "--max-chunks",
            min=1,
            max=30,
            help="本次最多发送给模型的 chunk 数量",
        ),
    ] = 8,
    max_cards: Annotated[
        int,
        typer.Option(
            "--max-cards",
            min=1,
            max=10,
            help="模型最多生成的证据卡数量",
        ),
    ] = 3,
) -> None:
    """
    从 PDF 中提取并验证 Evidence Cards。
    """

    try:
        typer.echo("1. 正在解析 PDF……")

        pages = extract_pages(pdf)

        typer.echo(
            f"   已提取 {len(pages)} 页文本"
        )

        typer.echo("2. 正在切分论文文本……")

        chunks = chunk_pages(
            pages=pages,
            paper_id=paper_id,
        )

        if not chunks:
            raise ValueError(
                "PDF 没有生成任何可用文本块"
            )

        typer.echo(
            f"   已生成 {len(chunks)} 个文本块"
        )

        candidate_chunks = select_candidate_chunks(
            question=question,
            chunks=chunks,
            top_k=max_chunks,
        )

        if len(chunks) > max_chunks:
            typer.echo(
                "  已经选择最相关的，"
                f"{len(candidate_chunks)} 个文本块"
            )

        typer.echo("3. 正在提取并验证证据……")

        result = run_evidence_gate(
            question=question,
            chunks=candidate_chunks,
            max_cards=max_cards,
        )

        typer.echo("4. 证据处理完成")
        typer.echo(
            f"   验证通过：{len(result.accepted)} 条"
        )
        typer.echo(
            f"   验证失败：{len(result.rejected)} 条"
        )

        typer.echo("\n完整结果：")

        typer.echo(
            result.model_dump_json(
                indent=2,
            )
        )

    except Exception as error:
        typer.echo(
            f"执行失败：{error}",
            err=True,
        )
        raise typer.Exit(code=1) from error


if __name__ == "__main__":
    app()