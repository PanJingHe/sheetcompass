<div align="center">

# SheetCompass

### 面向智能体电子表格推理的分层关系图

面向复杂多表工作簿的图引导电子表格理解、代码生成与结果验证框架。

[![Paper](https://img.shields.io/badge/WSDM-2027-6f42c1.svg)](https://doi.org/10.1145/3862322.3863471)
[![DOI](https://img.shields.io/badge/DOI-10.1145%2F3862322.3863471-blue.svg)](https://doi.org/10.1145/3862322.3863471)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)

[English](./README.md) | [简体中文](./README_CN.md)

</div>

## 项目简介

电子表格的语义不仅存在于单元格内容中，还隐含在表格边界、列布局以及跨工作表的数据依赖中。将工作簿直接转换为扁平的 Markdown 文本虽然保留了文字内容，却会丢失大量结构信息。因此，大语言模型很难可靠地识别连接键、追踪跨表依赖，并完成长流程的电子表格操作。

**SheetCompass** 将工作簿转换为结构化证据空间，以解决上述问题。它通过分层关系图（Hierarchical Relational Graph，HRG）表示工作表中的表格与列，检索与当前任务相关的子图，将用户指令定位到经过验证的电子表格实体，并据此生成可执行的电子表格操作。WSDM 2027 论文中描述的完整框架进一步将该图结构与多级记忆机制以及 Explorer-Programmer-Reflector 工作流结合，实现迭代式验证和纠错。

## 为什么使用 SheetCompass？

- **结构感知推理：**表节点与列节点保留扁平化过程中容易丢失的工作簿组织信息。
- **跨表语义定位：**语义边能够显式表示位于不同工作表中的共享产品标识、价格列与销售列等逻辑关系。
- **紧凑的任务上下文：**智能体只检索相关节点及其连接路径，而不需要将整个工作簿全部发送给大语言模型。
- **可执行输出：**系统将完成定位的计划转换为 Python 与 `openpyxl` 操作，并生成修改后的 `.xlsx` 文件。
- **闭环可靠性：**代码执行失败时可以触发修复；论文中的完整框架还通过独立的 Reflector 验证工作簿最终状态。
- **可追踪实验：**系统会保存提示词、图结构轨迹、生成代码、执行日志、输出文件、评测结果、Token 用量和预估 API 成本。

## 方法

### 1. 分层关系图

SheetCompass 首先检测工作簿中的表格区域，然后构建包含多层电子表格实体的关系图：

- **表节点（Table Node）：**描述检测到的表格及其所在工作表、单元格范围、规模和模式信息。
- **列节点（Column Node）：**记录列名、位置、推断类型、空值比例和代表性取值。
- **结构边（Structural Edge）：**编码工作表与表格、表格与列之间的显式包含关系。
- **语义边（Semantic Edge）：**连接物理位置相距较远但逻辑相关的实体，例如不同工作表中的连接键。

### 2. 多级记忆

完整的 SheetCompass 框架维护三类互补的记忆：

- **电子表格图记忆：**存储经过验证的工作簿结构状态。
- **专家知识记忆：**提供可复用的电子表格操作知识、工具使用方法和领域经验。
- **推理经验记忆：**记录成功与失败的执行轨迹，帮助系统避免重复犯错。

### 3. 多智能体工作流

- **Explorer** 分解用户指令，识别与任务相关的图节点，并检索紧凑且连通的子图。
- **Programmer** 将完成定位的子问题转换为可执行的电子表格操作。
- **Reflector** 根据任务检查清单验证输出工作簿，并在任务未完成时返回明确的诊断信息。

当前 `main` 分支提供的可运行流程如下：

```mermaid
flowchart LR
    A[输入工作簿] --> B[表格检测与模式抽取]
    B --> C[结构图构建]
    C --> D[LLM 语义增强]
    E[用户指令] --> F[问题分解]
    D --> G[图结构定位]
    F --> G
    G --> H[生成 Python 与 openpyxl 代码]
    H --> I[本地执行代码]
    I -->|执行失败| J[代码修复]
    J --> I
    I --> K[输出工作簿并评测]
```

## 主要实验结果

SheetCompass 在 SheetCopilot Benchmark（SCB）、SpreadsheetBench（SB）和 SheetRM 上进行了评测。下表结果以百分比表示，并由三次独立运行取平均值；所有指标均为数值越高越好。

| 主干模型 | SCB Exec@1 | SCB Pass@1 | SB Soft | SB Hard | SheetRM Exec@1 | SheetRM Pass@1 |
|---|---:|---:|---:|---:|---:|---:|
| Qwen3.5-Plus | 95.1 | 63.2 | 22.6 | 18.3 | 93.7 | 42.5 |
| GPT-5 | **96.4** | **71.3** | **24.7** | **22.0** | **94.2** | **52.3** |

消融实验表明，移除分层关系图会造成最大的整体性能下降。在 GPT-5 上，SCB Pass@1 从 71.3 降至 56.4，SB Hard 从 22.0 降至 14.5，SheetRM Pass@1 从 52.3 降至 41.4。完整的基线对比、消融实验、效率分析和案例研究请参阅[论文](https://doi.org/10.1145/3862322.3863471)。

## 当前代码状态

当前公开版本已经包含：

- 表格区域检测与模式抽取；
- 结构图构建和基于大语言模型的语义增强；
- 用户指令分解与图结构定位；
- Python/`openpyxl` 代码生成、本地执行以及一次代码修复；
- SpreadsheetBench 和 SheetCopilot 的数据加载与评测适配；
- 样本级运行轨迹、输出工作簿、汇总指标和 API 用量统计。

论文还报告了完整的持久化记忆生命周期、完整的 Explorer-Programmer-Reflector 协作流程以及 SheetRM 实验。这些组件目前尚未全部接入 `main.py` 的命令行入口。复现论文表格时，请注意论文完整框架与当前公开运行入口之间的差异。

## 仓库结构

```text
sheetcompass/
├── main.py                 # 基准测试入口和端到端流程编排
├── data.py                 # SpreadsheetBench 与 SheetCopilot 数据加载器
├── evaluator.py            # 基准测试评估和汇总指标
├── llm.py                  # OpenAI 兼容的 LLM 客户端及用量统计
├── run_code.py             # 生成代码的执行与错误摘要
├── model/
│   ├── tables.py           # 表格检测与模式抽取
│   ├── graph.py            # 结构图构建与语义增强
│   ├── questions.py        # 指令分解与图结构定位
│   ├── codegen.py          # 代码生成与修复
│   └── pipeline.py         # 从关系图到可执行代码的流程
├── prompt/                 # 提示词与针对不同基准的少样本示例
├── run_bench.sh            # SpreadsheetBench 启动脚本
└── run_sc.sh               # SheetCopilot 启动脚本
```

## 安装

### 1. 克隆仓库

```bash
git clone https://github.com/PanJingHe/sheetcompass.git
cd sheetcompass
```

### 2. 创建运行环境

推荐使用 Python 3.10 或更高版本。

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
pip install openai openpyxl pyyaml python-dotenv tqdm
```

### 3. 配置大语言模型接口

在仓库根目录创建 `.env` 文件：

```dotenv
API_KEY=your_api_key
# 仅在使用 OpenAI 兼容的自定义接口时设置：
# BASE_URL=https://your-endpoint.example.com/v1
```

也可以通过 `--api_key` 和 `--base_url` 参数直接传入接口信息。客户端使用与 OpenAI Chat Completions 兼容的调用方式，因此模型名称必须得到所配置接口的支持。


## 准备评测数据

SheetCompass 仓库不重新分发评测数据。请从以下官方仓库下载数据集，并遵循其许可证和数据准备说明：

- [SpreadsheetBench](https://github.com/RUCKBReasoning/SpreadsheetBench)
- [SheetCopilot](https://github.com/BraveGroup/SheetCopilot)

数据加载器需要以下关键文件和目录：

```text
SpreadsheetBench/data/
└── sample_data_200/
    ├── dataset.json
    └── spreadsheet/...

SheetCopilot/dataset/
├── dataset.xlsx
├── task_sheets/
└── task_sheet_answers_v2/
```

如果下载版本的目录名称不同，请通过命令行参数传入对应的数据路径或真值版本。

## 快速开始

建议先运行一个样本，检查环境、模型接口和数据目录是否正确。

### SpreadsheetBench

```bash
python main.py \
  --benchmark spreadsheetbench \
  --data_root /path/to/SpreadsheetBench/data \
  --dataset_name sample_data_200 \
  --num_samples 1 \
  --model gpt-4o-mini \
  --output_dir outputs
```

### SheetCopilot

```bash
python main.py \
  --benchmark sheetcopilot \
  --data_root /path/to/SheetCopilot/dataset \
  --sheetcopilot_gt_version task_sheet_answers_v2 \
  --num_samples 1 \
  --model gpt-4o-mini \
  --output_dir outputs
```

上述命令使用 `gpt-4o-mini` 进行低成本的环境检查。

## 运行实验

仓库提供的 Shell 脚本包含作者环境中的默认数据路径。使用时请传入本地数据目录进行覆盖：

```bash
# SpreadsheetBench
bash run_bench.sh \
  --data_root /path/to/SpreadsheetBench/data \
  --num_samples 50

# SheetCopilot
bash run_sc.sh \
  --data_root /path/to/SheetCopilot/dataset \
  --num_samples 50
```

常用参数：

| 参数 | 说明 |
|---|---|
| `--benchmark` | 选择 `spreadsheetbench` 或 `sheetcopilot` |
| `--data_root` | 基准测试数据根目录 |
| `--dataset_name` | SpreadsheetBench 子集，默认为 `sample_data_200` |
| `--sheetcopilot_gt_version` | SheetCopilot 真值文件夹名称 |
| `--sample_id` | 仅运行指定样本 |
| `--num_samples` | 最大运行样本数；`-1` 表示全部样本 |
| `--preview_rows` | 每个检测表格保留的预览行数 |
| `--model` | 发送至模型接口的模型标识符 |
| `--temperature` | 模型采样温度 |
| `--max_tokens` | 单次大语言模型调用的最大输出 Token 数 |
| `--output_dir` | 输出文件和日志的根目录 |
| `--force-rerun` | 即使已有输出文件也重新生成 |
| `--eval-only` | 不调用大语言模型，仅评测已有输出工作簿 |

如需评测已经生成的文件，请保持数据集、模型和输出路径参数不变，并添加 `--eval-only`：

```bash
python main.py \
  --benchmark spreadsheetbench \
  --data_root /path/to/SpreadsheetBench/data \
  --dataset_name sample_data_200 \
  --model gpt-4o-mini \
  --output_dir outputs \
  --eval-only
```

## 输出文件

每次运行会在 `outputs/<model>/<benchmark>/` 下保存以下内容：

```text
outputs/<model>/<benchmark>/
├── conv_single.jsonl           # 样本级运行摘要
├── metrics.json                # 汇总指标与 LLM 用量
├── code/
│   └── <sample_id>.py          # 生成的程序
└── spreadsheets/
    └── <sample_id>/
        ├── *_output.xlsx       # 生成的工作簿
        └── sample_io_log.json  # 图、提示词、定位、执行和修复轨迹
```

生成的代码会在本地执行，并可能修改工作簿副本。请仅使用可信数据，在隔离环境中运行项目，检查生成的程序，并保留原始工作簿备份。

## 复现说明

- 论文使用 GPT-5（medium reasoning effort）和温度为 0.0 的 Qwen3.5-Plus；以上快速开始命令仅用于检查运行环境。
- 论文中的最大单次生成长度为 4,096 Token，报告结果均由三次独立运行取平均值得到。
- 不同数据集的评测指标不可直接互换：SCB 与 SheetRM 报告 Exec@1/Pass@1，SpreadsheetBench 报告 Soft/Hard Restriction 分数。
- 大语言模型接口、模型快照、数据集版本和电子表格引擎行为均可能影响最终结果。建议为每次实验记录模型版本、接口、数据集版本、运行参数和代码提交哈希。

## 引用

如果 SheetCompass 对你的研究有所帮助，请引用：

```bibtex
@inproceedings{he2027sheetcompass,
  title     = {SheetCompass: Hierarchical Relational Graphs for Agentic Spreadsheet Reasoning},
  author    = {He, Panjing and Cheng, Mingyue and Luo, Yucong and Li, Li and Zhang, Xiaohan},
  booktitle = {Proceedings of the Twentieth ACM International Conference on Web Search and Data Mining},
  year      = {2027},
  publisher = {ACM},
  doi       = {10.1145/3862322.3863471},
  url       = {https://doi.org/10.1145/3862322.3863471}
}
```

## 致谢

本研究得到国家自然科学基金、中央高校基本科研业务费专项资金以及“梦想启航”鲲鹏昇腾种子计划的支持。

## 许可证与联系方式

当前仓库尚未包含软件许可证。在添加许可证之前，仓库内容适用默认版权规则。第三方评测数据仍受其原始许可证约束。

如对论文或代码有疑问，请提交 GitHub Issue。
