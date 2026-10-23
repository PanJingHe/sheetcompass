<div align="center">

# SheetCompass: Hierarchical Relation Graphs for Agentic Spreadsheet Reasoning

[![English](https://img.shields.io/badge/Language-English-blue.svg)](./README.md) 
[![简体中文](https://img.shields.io/badge/Language-简体中文-red.svg)](#)

</div>

---

**SheetCompass** 是一个专注于大语言模型（LLM）电子表格智能推理与结构化理解的项目。针对传统大模型在处理复杂多表、多工作簿电子表格时容易因为序列化或扁平化而丢失空间语义的问题，SheetCompass 引入了**分层关系图（Hierarchical Relation Graphs）**与结构化上下文管理，显著提升了智能体（Agent）对表格内部及跨表关系的推理与操作能力。

---

## 📂 仓库目录结构

```text
sheetcompass/
├── __pycache__/      # Python 缓存文件
├── model/            # 模型相关配置与权重接口
├── prompt/           # Agent 提示词模板及结构化定义
├── data.py           # 数据加载与表格解析处理
├── evaluator.py      # 评估指标与评测脚本
├── llm.py            # 大模型交互封装（API/本地模型调用）
├── main.py           # 主程序入口
└── run_bench.sh      # SheetCompass 核心推理流程脚本
```

---

## 🚀 快速开始

### 1. 环境安装

克隆仓库并安装相关依赖：

```bash
git clone https://github.com/PanJingHe/sheetcompass.git
cd sheetcompass
```

### 2. 配置环境变量

在根目录下配置你的大模型 API Key（例如 OpenAI 或其他兼容接口）：

```bash
export OPENAI_API_KEY="your-api-key-here"
```

---

## 💻 使用指南

### 运行基准测试 (Benchmark)

若要评估模型在表格推理任务上的表现，可执行：

```bash
bash run_bench.sh
```

---

## 🛠️ 核心模块说明

- **`data.py`**: 负责电子表格文件的读取、预处理以及图结构的构建。
- **`llm.py`**: 统一封装了与大模型的交互接口，便于切换不同的 LLM 后端。
- **`prompt/`**: 存放了精心设计的提示词，用于引导 Agent 进行多步规划与工具调用。
- **`evaluator.py`**: 提供标准化的评测指标，方便对实验结果进行自动化打分。
- **`run_code.py`**: 负责执行生成的代码片段与工具调用的沙箱环境。