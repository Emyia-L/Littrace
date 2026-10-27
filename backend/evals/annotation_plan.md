# LitTrace 论文评测集：标注准备

本目录下的 `paper_catalog.json` 是 2026-10-22 对 `backend/samples/*.pdf` 的快照，不是论文全文，也不会自动随新 PDF 更新。所有文件哈希均是**原始 PDF 字节**的 SHA-256。页码从 PDF 第一页开始计数，与 `extract_pages()` / `chunk_pages()` 的 `page_number` 一致。

目前 7 篇论文共 116 页，按默认 `max_chars=1200`、`overlap=150` 会生成 510 个文本块。`02_ChemCrow_NatureMI_2024.pdf` 的第 12、13 页是期刊附加的图像式 reporting summary；现有文本解析器不适合从这两页建立原文引文标准。其他各页均有可提取文本。所有单个 PDF 都低于当前 20 MiB 上传上限。

## 标注状态与任务池

`ready` 表示问题、原文引文、页码、PDF 哈希和当前切块结果都经过首轮核验，可用来计算**关键引文召回**；本轮由 AI 辅助选取并核验，尚无独立人工复核记录。原有任务池 17 题现已全部形成案例，另加入 3 道“特定问题无充分证据”的弃答题，共 20 题。`gold_quotes: []` 在这里仅用于这 3 道有明确弃答理由的题，**不表示未标注**。

| 案例 ID | 论文/语料 | 候选问题 | 状态 |
| --- | --- | --- | --- |
| omni-main-problem | OmniAgent | 这篇论文主要解决了什么问题？ | ready：`omniagent_main_problem.json` |
| omni-audio-guided | OmniAgent | 音频引导的事件定位如何帮助主动感知？ | ready：`omniagent_audio_guided.json` |
| autonomy-problem | Variable Autonomy | 移动操作机器人为什么需要可变自主性，主要挑战是什么？ | ready：`variable_autonomy_problem.json` |
| autonomy-future | Variable Autonomy | 综述提出了哪些后续研究方向？ | ready：`variable_autonomy_future.json` |
| chemcrow-problem | ChemCrow | ChemCrow 要弥补语言模型在化学任务中的哪些不足？ | ready：`chemcrow_problem.json` |
| chemcrow-tools | ChemCrow | ChemCrow 如何使用专门工具，其效果有哪些论文内证据？ | ready：`chemcrow_tools.json` |
| robot-design-role | Robot Design | 论文中的语言模型和人分别承担什么设计工作？ | ready：`robot_design_roles.json` |
| robot-design-limits | Robot Design | 这个设计案例显示了语言模型的哪些能力边界？ | ready：`robot_design_limits.json` |
| palme-grounding | PaLM-E | PaLM-E 如何把传感信息与语言联系起来？ | ready：`palme_grounding.json` |
| palme-tasks | PaLM-E | 论文用哪些具身任务检验模型能力？ | ready：`palme_tasks.json` |
| ecot-problem | ECoT | 标准思维链为什么不足以直接用于机器人动作策略？ | ready：`ecot_problem.json` |
| ecot-method | ECoT | ECoT 引入哪些具身推理步骤，论文报告了什么效果？ | ready：`ecot_method.json` |
| robotwin-data-generation | RoboTwin 2.0 | RoboTwin 2.0 如何利用多模态模型和仿真反馈自动生成双臂任务数据？ | ready：`robotwin_data_generation.json` |
| robotwin-results | RoboTwin 2.0 | 论文如何评估领域随机化与生成数据对鲁棒性的作用？ | ready：`robotwin_results.json` |
| cross-embodied | PaLM-E + ECoT | 两篇论文如何分别处理具身信息的输入与行动前推理？ | ready：`cross_embodied_grounding_reasoning.json`，跨论文 |
| cross-tools | ChemCrow + OmniAgent | 两篇论文如何组织专业工具及其调用？ | ready：`cross_tools_organization.json`，跨论文 |
| cross-human-role | Variable Autonomy + Robot Design | 两篇论文中人的参与和系统自主性如何定位？ | ready：`cross_human_role.json`，跨论文 |
| no-answer-omni-chinese-latency | OmniAgent | 中文语音数据集的毫秒级平均延迟？ | 首轮弃答标注：`no_answer_omni_latency.json` |
| no-answer-chemcrow-api-cost | ChemCrow | 全部实验 API 调用总费用？ | 首轮弃答标注：`no_answer_chemcrow_api_cost.json` |
| no-answer-cross-same-benchmark | PaLM-E + ECoT | 相同测试集和训练条件下的成功率差值？ | 首轮弃答标注：`no_answer_cross_same_benchmark.json` |

17 道可回答题在 `answer_rubrics.json` 中另有答案要点、引文序号、禁止无据声称的内容；3 道弃答题有具体缺证据理由。无答案判断来自本轮全文检索与相关内容检查，不代表已被独立人审确认。不得把其他研究或模型常识补成 PDF 的 gold 引文。

## 每个案例的标注流程

1. 先固定**语料集合**：单篇案例单独建项目并只上传该 PDF；跨论文案例只上传表中列出的论文。现有评测读取器要求项目中的 PDF 哈希集合与案例完全一致。不同版本 PDF 即使标题相同，也要重新核验。
2. 阅读相关 PDF 的完整页，包括图题、表题、脚注和限制说明。每个独立答案要点选 1 段**连续原文**，记录 PDF 页码；避免只从摘要抄多个同义句。优先覆盖问题背景、方法与关键结果等不同位置。
3. 对每段引文运行 LitTrace 的 `normalize_text()` 和 `chunk_pages()` 验证：规范化后至少 20 字符；在指定页的同一文本块内能找到；最好不要跨块。确认后再写入 `EvalCase.gold_quotes`。
4. 复核问题与引用的**语义关系**：文字存在只是定位通过，不自动证明它回答了问题。必要时由第二人复核有争议的案例，并记录争议原因。
5. 用同一问题和同一 PDF 集合分别运行 Workflow、Agent。比较前固定模型/提示词版本、检索参数；跨运行记忆可能改变 Agent 输入，故配对实验应使用隔离项目或明确记录记忆状态。
6. 若新增或替换 PDF，重新核验 `paper_catalog.json` 中的哈希、页数和块数。`samples/*.pdf` 被 Git 忽略；在其他机器复现时必须另行准备**相同字节**的文件，不能只拉取 Git 仓库。

当前 CLI 自动计算的仍是关键引文观察/接受率及运行轨迹指标；`answer_rubrics.json`、`claim_challenges.json` 和 `agent_behavior_scenarios.json` 是已准备好的评分标准与场景标签，**尚未接入 CLI 自动评分**。跨论文的关系标签不能代替独立专家判断。哪些指标已有标注、哪些必须通过真实运行采集，见 `metrics_coverage.md`。
