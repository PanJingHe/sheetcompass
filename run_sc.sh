#!/usr/bin/env bash
# SheetCopilot benchmark; extra args are forwarded to main.py.
set -e
cd "$(dirname "$0")"
exec python main.py \
  --benchmark sheetcopilot \
  --data_root ../SheetCopilot-main/dataset \
  --sheetcopilot_gt_version task_sheet_answers_v2 \
  --preview_rows 3 \
  --model gpt-4o-mini \
  --temperature 0.2 \
  --max_tokens 4096 \
  --output_dir outputs \
  --num_samples 50 \
  --force-rerun \
  "$@"
