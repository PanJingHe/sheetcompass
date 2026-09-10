#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

# Use project conda env (has openpyxl, etc.); adjust name if needed.
if [ -f "/data/hepanjing/anaconda3/etc/profile.d/conda.sh" ]; then
  # shellcheck source=/dev/null
  source "/data/hepanjing/anaconda3/etc/profile.d/conda.sh"
  conda activate agent
fi

python main.py \
  --data_root /data/hepanjing/projects/Agents/SpreadsheetBench-main/data \
  --dataset_name sample_data_200 \
  --num_samples 50 \
  --preview_rows 3 \
  --model gpt-4o-mini \
  --temperature 0.2 \
  --max_tokens 4096 \
  --output_dir outputs \
  --force-rerun \
  "$@"
