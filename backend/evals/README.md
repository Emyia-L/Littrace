# LitTrace 离线评测（第一版）

这里放**AI 辅助首轮标注**的 JSON 案例；不要把真实用户的私有论文或密钥提交到仓库。所有标准仍需独立人工复核才能作为高置信度 benchmark。
当前评测只读取已经存在的研究运行，不重新调用模型，也不修改数据库。

新增或替换样本 PDF、修改引文后，先在 `backend` 目录运行 `uv run python -X utf8 -m evals.verify_local_cases`。该检查只读取本地文件，会核对 PDF 哈希、页数和块数，引文与答案要点引用的一致性，以及论断挑战和 Agent 场景的结构；它不能自动证明答案语义或弃答判断正确。

本地样本论文清单见 `paper_catalog.json`，任务状态见 `annotation_plan.md`，指标与待实施评分见 `metrics_coverage.md`。现有 20 个案例：17 道可回答题（14 个单篇、3 个跨论文）与 3 道弃答题。可回答题的引文均来自指定 PDF 页，且落在默认切块器的单个文本块内；没有独立人工复核记录。

`answer_rubrics.json` 为每题提供答案要点及其引文序号、不得无据声称的内容、跨论文关系与可比性限制或弃答理由。`claim_challenges.json` 提供 6 个支持性判断样本；`agent_behavior_scenarios.json` 提供 10 个规划、工具、记忆、综合、安全与预算场景。这三类文件是**标注和场景定义**，目前离线 CLI 不会自动读取或评分。

每个案例格式：

```json
{
  "case_id": "omniagent-problem-01",
  "question": "这篇论文主要解决了什么问题？",
  "paper_sha256": ["论文 PDF 的 64 位小写 SHA-256"],
  "gold_quotes": [
    {
      "paper_sha256": "同一论文 PDF 的 64 位小写 SHA-256",
      "page_number": 3,
      "quote": "人工标注的关键原文，至少 20 字符，且完整处于同一文本块内"
    }
  ]
}
```

`paper_sha256` 可以从已有论文的 `content_hash` 获取。案例中的 PDF 哈希集合必须与运行所在项目的论文集合**完全一致**，问题文本也必须一致；否则程序拒绝比较，防止不同实验条件混在一起。仅 3 道附有弃答理由的题将 `gold_quotes` 设为 `[]`；未标注题目不要创建案例文件。当前 CLI 对空 gold 只返回 `null` 召回率，**不会**自动判断是否正确弃答。

在 `backend` 目录执行，允许为同一案例传入两个或更多运行 ID：

```powershell
uv run python -m littrace.evaluation.cli --case evals/my_case.json --run-id <workflow-run-uuid> --run-id <agent-run-uuid>
```

结果中的 `observed_gold_recall` 只适用于保存了搜索轨迹的 Agent；Workflow 没有此轨迹时返回 `null`。`accepted_gold_recall` 表示多少人工标注的关键引文出现在通过 Gate、且仍满足基本引文约束的证据中。`citation_invariant_violations` 是数据库中“已接受但引文/支持检查不满足 Gate 规则”的数量，**不等于**对所有论断的真实错误率。

本版复用生产引文校验器的规范化规则，再用原文包含关系判断引文命中。标注过长、跨文本块，或 PDF 提取与人工标注存在其他差异时可能出现假阴性；应先人工检查失败案例，再考虑引入更宽松的匹配方式。正式的评测集应记录数据集版本、模型和提示词版本，并将与用户数据隔离的项目用于对比。

跨论文案例的 CLI 分数仍只衡量每篇论文的**证据覆盖**，不是综合论断准确度。尽管现已有关系、答案要点和限制标注，自动评分器尚未实现，且没有第二标注人复核记录。
