"""
Spreadsheet benchmark: table detection → structural graph → decompose → ground → codegen → execute → eval.
"""

import argparse
import json
import logging
import os
import sys

from dotenv import load_dotenv
from tqdm import tqdm

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from evaluator import RunningMetrics, evaluate_sample
from data import SheetCopilotDataLoader, SpreadsheetDataLoader
from llm import LLMClient
from run_code import adapt_code_for_test_case, execute_code, summarize_execution_error
from model import process_spreadsheet, repair_code
from model.pipeline import run_codegen
from model.sc_block import sheetcopilot_instruction_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)


def parse_args():
    p = argparse.ArgumentParser(description="Spreadsheet agent with Schema+Graph model")
    p.add_argument("--benchmark", type=str, default="spreadsheetbench", choices=["spreadsheetbench", "sheetcopilot"])
    p.add_argument("--data_root", type=str, default="/data/hepanjing/projects/Agents/SpreadsheetBench-main/data")
    p.add_argument("--dataset_name", type=str, default="sample_data_200")
    p.add_argument("--sheetcopilot_gt_version", type=str, default="task_sheet_answers_v2")
    p.add_argument("--sample_id", type=str, default=None)
    p.add_argument("--num_samples", type=int, default=-1)
    p.add_argument("--preview_rows", type=int, default=3)
    p.add_argument("--model", type=str, default="gpt-4o-mini")
    p.add_argument("--api_key", type=str, default=None)
    p.add_argument("--base_url", type=str, default=None)
    p.add_argument("--temperature", type=float, default=0.2)
    p.add_argument("--max_tokens", type=int, default=4096)
    p.add_argument("--output_dir", type=str, default="outputs")
    p.add_argument("--no-few-shot", type=bool, default=False)
    p.add_argument("--eval-only", action="store_true")
    p.add_argument("--force-rerun", action="store_true")
    return p.parse_args()


def get_llm_credentials(args):
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    api_key = args.api_key or os.getenv("API_KEY")
    base_url = args.base_url or os.getenv("BASE_URL")
    if not api_key:
        raise ValueError("Missing API key. Pass --api_key or set API_KEY in .env")
    return api_key, base_url


def find_test_case_files(spreadsheet_dir):
    all_files = sorted(os.listdir(spreadsheet_dir))
    inputs = [f for f in all_files if f.endswith("_input.xlsx")]
    answers = [f for f in all_files if f.endswith("_answer.xlsx")]
    return inputs, answers


def has_all_sample_outputs(sample, out_root):
    sid = str(sample["id"])
    d = os.path.join(out_root, "spreadsheets", sid)
    if sample.get("benchmark") == "sheetcopilot":
        expected = [os.path.join(d, f"{sid}_output.xlsx")]
    else:
        expected = [os.path.join(d, f"{i}_{sid}_output.xlsx") for i in range(1, 4)]
    return all(os.path.exists(x) for x in expected)


def maybe_repair(llm, instruction, code, exec_out, ok):
    """On failure, ask LLM for a fix. Returns (code, exec_out, ok, repair_log|None)."""
    if ok or llm is None:
        return code, exec_out, ok, None
    err = summarize_execution_error(code, exec_out)
    fixed, pr, resp = repair_code(
        instruction,
        code,
        err["error_summary"],
        err["code_context"],
        llm,
    )
    log = {
        "error_summary": err["error_summary"], "line_number": err["line_number"],
        "code_context": err["code_context"], "relevant_traceback": err["relevant_traceback"],
        "prompt": pr, "response": resp, "repaired_code": fixed, "attempted": bool(fixed),
    }
    if fixed:
        ex2, ok2 = execute_code(fixed)
        log["repair_exec_output"], log["repair_success"] = ex2, ok2
        return fixed, ex2, ok2, log
    return code, exec_out, ok, log


def process_sample(sample, llm, args, out_root, data_dir, use_few_shot):
    sid = str(sample["id"])
    sheet_dir = os.path.join(data_dir, sample["spreadsheet_path"])
    sample_out = os.path.join(out_root, "spreadsheets", sid)
    os.makedirs(sample_out, exist_ok=True)
    for f in os.listdir(sample_out):
        if f.endswith("_output.xlsx"):
            os.remove(os.path.join(sample_out, f))

    inputs, _ = find_test_case_files(sheet_dir)
    if not inputs:
        logger.warning(f"[{sid}] No input files found")
        return None

    io_log = {"sample_id": sid, "instruction": sample["instruction"], "instruction_type": sample.get("instruction_type", "")}
    in1 = os.path.join(sheet_dir, inputs[0])
    out1 = os.path.join(sample_out, f"1_{sid}_output.xlsx")
    try:
        schemas = process_spreadsheet(in1)
        logger.info(f"[{sid}] Detected {len(schemas)} table(s)")
    except Exception as e:
        logger.error(f"[{sid}] Table detection failed: {e}")
        schemas = []

    io_log.update(
        run_codegen(
            sample["instruction"],
            in1,
            out1,
            schemas,
            llm,
            use_few_shot,
            benchmark=sample.get("benchmark", "spreadsheetbench"),
        )[0],
    )
    code = io_log["code_generation"]["code"]

    ex, ok = execute_code(code)
    code, ex, ok, rlog = maybe_repair(llm, sample["instruction"], code, ex, ok)
    io_log["test_cases"] = [{"test_case": f"1_{sid}", "exec_output": ex, "success": ok}]
    if rlog is not None:
        io_log["code_repair"] = rlog
    logger.info(f"[{sid}] Test case 1 {'ok' if ok else 'fail'}: {ex[:200] if not ok else ''}")

    for fn in inputs[1:]:
        prefix = fn.replace("_input.xlsx", "")
        idx = prefix.split("_")[0]
        tin, tout = os.path.join(sheet_dir, fn), os.path.join(sample_out, f"{prefix}_output.xlsx")
        adapted = adapt_code_for_test_case(code, sid, "1", idx).replace(in1, tin).replace(out1, tout)
        tx, tok = execute_code(adapted)
        io_log["test_cases"].append({"test_case": prefix, "exec_output": tx, "success": tok, "reused_code": True})
        logger.info(f"[{sid}] case {idx} reuse {'ok' if tok else 'fail'}")

    code_dir = os.path.join(out_root, "code")
    os.makedirs(code_dir, exist_ok=True)
    code_path = os.path.join(code_dir, f"{sid}.py")
    with open(code_path, "w", encoding="utf-8") as fp:
        fp.write(code)
    with open(os.path.join(sample_out, "sample_io_log.json"), "w", encoding="utf-8") as fp:
        json.dump(io_log, fp, ensure_ascii=False, indent=2)

    return {"id": sample["id"], "instruction_type": sample.get("instruction_type", ""), "code": code, "success": ok, "code_path": code_path}


def process_sheetcopilot_sample(sample, llm, out_root, use_few_shot):
    sid = str(sample["id"])
    in_path, out_path = sample["workbook_path"], os.path.join(out_root, "spreadsheets", sid, f"{sid}_output.xlsx")
    sample_out = os.path.dirname(out_path)
    os.makedirs(sample_out, exist_ok=True)
    for f in os.listdir(sample_out):
        if f.endswith("_output.xlsx"):
            os.remove(os.path.join(sample_out, f))

    io_log = {
        "sample_id": sid, "instruction": sample["instruction"], "instruction_type": sample.get("instruction_type", ""),
        "benchmark": "sheetcopilot", "input_path": in_path, "output_path": out_path,
    }
    try:
        schemas = process_spreadsheet(in_path)
        logger.info(f"[{sid}] Detected {len(schemas)} table(s)")
    except Exception as e:
        logger.error(f"[{sid}] Table detection failed: {e}")
        schemas = []

    task_instruction = sheetcopilot_instruction_text(sample)
    io_log["task_instruction"] = task_instruction
    io_log.update(
        run_codegen(
            task_instruction,
            in_path,
            out_path,
            schemas,
            llm,
            use_few_shot,
            benchmark=sample.get("benchmark", "sheetcopilot"),
        )[0],
    )
    code = io_log["code_generation"]["code"]

    ex, ok = execute_code(code)
    code, ex, ok, rlog = maybe_repair(llm, task_instruction, code, ex, ok)
    io_log["success"], io_log["exec_output"] = ok, ex
    io_log["test_cases"] = [{"test_case": sid, "exec_output": ex, "success": ok}]
    if rlog is not None:
        io_log["code_repair"] = rlog

    code_dir = os.path.join(out_root, "code")
    os.makedirs(code_dir, exist_ok=True)
    code_path = os.path.join(code_dir, f"{sid}.py")
    with open(code_path, "w", encoding="utf-8") as fp:
        fp.write(code)
    with open(os.path.join(sample_out, "sample_io_log.json"), "w", encoding="utf-8") as fp:
        json.dump(io_log, fp, ensure_ascii=False, indent=2)

    return {"id": sample["id"], "instruction_type": sample.get("instruction_type", ""), "code": code, "success": ok, "code_path": code_path}


def main():
    args = parse_args()
    if args.benchmark == "sheetcopilot":
        loader = SheetCopilotDataLoader(
            args.data_root, args.num_samples, args.preview_rows, args.sample_id, args.sheetcopilot_gt_version,
        )
        data_dir = args.data_root
    else:
        loader = SpreadsheetDataLoader(args.data_root, args.dataset_name, args.num_samples, args.preview_rows, args.sample_id)
        data_dir = os.path.join(args.data_root, args.dataset_name)
    samples = loader.load_all_samples()
    if args.sample_id and not samples:
        raise ValueError(f"Sample id not found or missing files: {args.sample_id}")
    logger.info(f"Loaded {len(samples)} samples from benchmark={args.benchmark}")

    llm = None
    out_root = os.path.join(args.output_dir, args.model, args.benchmark)
    os.makedirs(out_root, exist_ok=True)
    use_few_shot = not args.no_few_shot
    metrics = RunningMetrics()
    metrics.benchmark = args.benchmark
    conv_path = os.path.join(out_root, "conv_single.jsonl")
    open(conv_path, "w", encoding="utf-8").close()

    for sample in tqdm(samples, desc="pipeline"):
        sid = str(sample["id"])
        exists = has_all_sample_outputs(sample, out_root)
        skip = exists and not args.force_rerun
        if args.eval_only:
            logger.info(f"[{sid}] Eval-only, skip run")
        elif skip:
            logger.info(f"[{sid}] Outputs exist, skip")
        else:
            if exists and args.force_rerun:
                logger.info(f"[{sid}] Force rerun")
            if llm is None:
                key, url = get_llm_credentials(args)
                llm = LLMClient(args.model, key, url, args.temperature, args.max_tokens)
            try:
                if args.benchmark == "sheetcopilot":
                    res = process_sheetcopilot_sample(sample, llm, out_root, use_few_shot)
                else:
                    res = process_sample(sample, llm, args, out_root, data_dir, use_few_shot)
            except Exception as e:
                logger.error(f"[{sid}] Pipeline error: {e}")
                res = None
            if res:
                with open(conv_path, "a", encoding="utf-8") as fp:
                    fp.write(json.dumps(res, ensure_ascii=False) + "\n")

        spread_out = os.path.join(out_root, "spreadsheets")
        ev = evaluate_sample(sample, spread_out, data_dir)
        metrics.update(ev)
        suf = f" matched={ev.matched_gt}" if ev.matched_gt else ""
        logger.info(
            f"[{sid}] exec={ev.exec_at_1} pass={ev.pass_at_1}{suf} | "
            f"Exec@1={metrics.exec_at_1:.2%}, Pass@1={metrics.pass_at_1:.2%}",
        )

    print(metrics.summary())
    usage = llm.usage_summary() if llm else {"total_input_tokens": 0, "total_output_tokens": 0, "total_cost_usd": 0.0}
    logger.info(f"LLM usage: {usage['total_input_tokens']} in / {usage['total_output_tokens']} out, ${usage['total_cost_usd']:.4f}")
    mp = os.path.join(out_root, "metrics.json")
    with open(mp, "w", encoding="utf-8") as fp:
        json.dump({**metrics.to_dict(), "llm_usage": usage}, fp, ensure_ascii=False, indent=2)
    logger.info(f"Metrics saved to {mp}")


if __name__ == "__main__":
    main()
