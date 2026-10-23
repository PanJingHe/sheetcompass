<div align="center">

# SheetCompass: Hierarchical Relation Graphs for Agentic Spreadsheet Reasoning

[![English](https://img.shields.io/badge/Language-English-blue.svg)](#) 
[![简体中文](https://img.shields.io/badge/Language-简体中文-red.svg)](./README_CN.md)

</div>

---

**SheetCompass** is an open-source project focused on LLM-based intelligent reasoning and structural understanding for spreadsheets. Traditional large language models often lose spatial semantics when processing complex multi-table and multi-workbook spreadsheets due to serialization or flattening. To address this, SheetCompass introduces **Hierarchical Relation Graphs** and structured context management, significantly enhancing the agent's ability to reason about and manipulate both intra-sheet and cross-sheet relationships.

---

## 📂 Repository Structure

```text
sheetcompass/
├── __pycache__/      # Python cache files
├── model/            # Model configurations and weight interfaces
├── prompt/           # Agent prompt templates and structural definitions
├── data.py           # Data loading and spreadsheet parsing/processing
├── evaluator.py      # Evaluation metrics and benchmark scripts
├── llm.py            # LLM interaction wrapper (API / local model calls)
├── main.py           # Main program entry point
└── run_bench.sh      # SheetCompass core reasoning pipeline script
```

---

## 🚀 Quick Start

### 1. Environment Installation

Clone the repository and install dependencies:

```bash
git clone https://github.com/PanJingHe/sheetcompass.git
cd sheetcompass
```

### 2. Configure Environment Variables

Set your LLM API Key in your root environment:

```bash
export OPENAI_API_KEY="your-api-key-here"
```

---

## 💻 Usage Guide

### Run Benchmark

To evaluate model performance on spreadsheet reasoning tasks, run:

```bash
bash run_bench.sh
```

---

## 🛠️ Core Modules

- **`data.py`**: Responsible for reading spreadsheet files, preprocessing, and building graph structures.
- **`llm.py`**: Uniformly encapsulates interaction interfaces with LLMs to easily switch backends.
- **`prompt/`**: Stores meticulously designed prompts to guide agents through multi-step planning and tool calls.
- **`evaluator.py`**: Provides standardized evaluation metrics for automated scoring of experiment results.
- **`run_code.py`**: Manages the sandbox environment for executing generated code snippets and tool calls.