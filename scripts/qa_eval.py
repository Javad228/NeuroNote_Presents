#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any
from urllib import error, parse, request


def _read_jsonl(path: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except Exception as exc:
                raise RuntimeError(f"Invalid JSON on line {line_no}: {exc}") from exc
            if not isinstance(item, dict):
                raise RuntimeError(f"JSONL row {line_no} is not an object.")
            rows.append(item)
            if limit is not None and len(rows) >= limit:
                break
    return rows


def _post_json(url: str, payload: dict[str, Any], *, timeout: float = 120.0) -> tuple[int, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            status = int(resp.status)
    except error.HTTPError as exc:
        body = exc.read()
        status = int(exc.code)
    except Exception as exc:
        return 0, {"error": str(exc)}

    try:
        parsed = json.loads(body.decode("utf-8"))
    except Exception:
        parsed = {"raw": body.decode("utf-8", errors="replace")}
    return status, parsed


def _pctl(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    sorted_vals = sorted(values)
    idx = (len(sorted_vals) - 1) * pct
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    if hi == lo:
        return sorted_vals[lo]
    frac = idx - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def _get_retrieval_unit_ids(result: dict[str, Any]) -> list[str]:
    debug = result.get("debug")
    if not isinstance(debug, dict):
        return []
    candidates = debug.get("retrieval_merged_candidates")
    if not isinstance(candidates, list):
        candidates = debug.get("retrieval_candidates")
    if not isinstance(candidates, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if not isinstance(item, dict):
            continue
        unit_id = item.get("unit_id")
        if not isinstance(unit_id, str) or not unit_id or unit_id in seen:
            continue
        seen.add(unit_id)
        out.append(unit_id)
    return out


def _compute_recall(pred_ids: list[str], gold_ids: list[str]) -> float | None:
    gold = [g for g in gold_ids if isinstance(g, str) and g]
    if not gold:
        return None
    pred_set = set(pred_ids)
    hits = sum(1 for g in gold if g in pred_set)
    return hits / len(gold)


def _normalize_reason_codes(result: dict[str, Any]) -> list[str]:
    raw = result.get("reason_codes")
    if not isinstance(raw, list):
        return []
    return [str(v) for v in raw if isinstance(v, str)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate NeuroNote QA responses against a JSONL benchmark.")
    parser.add_argument("--backend", default="http://127.0.0.1:8100", help="Backend origin (default: %(default)s)")
    parser.add_argument("--benchmark", required=True, help="Path to benchmark JSONL")
    parser.add_argument("--out", required=True, help="Output JSON report path")
    parser.add_argument("--debug", default="true", choices=["true", "false"], help="Send debug=true to QA endpoint")
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--max-selected-units", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--judge", choices=["none", "llm"], default="none", help="LLM judging not implemented yet")
    args = parser.parse_args()

    benchmark_path = Path(args.benchmark)
    out_path = Path(args.out)
    rows = _read_jsonl(benchmark_path, limit=args.limit)
    if not rows:
        raise RuntimeError("Benchmark is empty.")

    latencies_ms: list[float] = []
    recalls: list[float] = []
    per_example: list[dict[str, Any]] = []
    reason_code_counts: dict[str, int] = {}
    http_error_count = 0
    success_count = 0
    tp = fp = tn = fn = 0

    debug_flag = args.debug == "true"

    for idx, row in enumerate(rows):
        job_id = row.get("job_id")
        question = row.get("question")
        if not isinstance(job_id, str) or not job_id or not isinstance(question, str) or not question.strip():
            per_example.append(
                {"row_index": idx, "error": "invalid benchmark row: missing job_id/question", "input": row}
            )
            http_error_count += 1
            continue

        query_params = {"debug": "true" if debug_flag else "false"}
        url = f"{args.backend.rstrip('/')}/api/jobs/{parse.quote(job_id)}/qa/answer?{parse.urlencode(query_params)}"
        payload: dict[str, Any] = {"question": question}
        if args.top_k is not None:
            payload["top_k"] = args.top_k
        if args.max_selected_units is not None:
            payload["max_selected_units"] = args.max_selected_units

        started = time.perf_counter()
        status, result = _post_json(url, payload)
        latency_ms = (time.perf_counter() - started) * 1000.0
        latencies_ms.append(latency_ms)

        item_report: dict[str, Any] = {
            "row_index": idx,
            "job_id": job_id,
            "question": question,
            "status_code": status,
            "latency_ms": round(latency_ms, 2),
        }

        if status != 200 or not isinstance(result, dict):
            http_error_count += 1
            item_report["error"] = result
            per_example.append(item_report)
            continue

        success_count += 1
        item_report["pipeline_version"] = result.get("pipeline_version")
        item_report["answerable_pred"] = bool(result.get("answerable", True))
        item_report["answerable_gold"] = bool(row.get("answerable", True))
        item_report["reason_codes"] = _normalize_reason_codes(result)
        for code in item_report["reason_codes"]:
            reason_code_counts[code] = reason_code_counts.get(code, 0) + 1

        gold_unit_ids = row.get("gold_unit_ids")
        if not isinstance(gold_unit_ids, list):
            gold_unit_ids = []
        retrieved_unit_ids = _get_retrieval_unit_ids(result)
        recall = _compute_recall(retrieved_unit_ids, gold_unit_ids)
        item_report["retrieval_unit_count"] = len(retrieved_unit_ids)
        item_report["retrieval_recall"] = recall
        if recall is not None:
            recalls.append(recall)

        gold_answerable = item_report["answerable_gold"]
        pred_answerable = item_report["answerable_pred"]
        if gold_answerable and pred_answerable:
            tp += 1
        elif not gold_answerable and pred_answerable:
            fp += 1
        elif not gold_answerable and not pred_answerable:
            tn += 1
        else:
            fn += 1

        per_example.append(item_report)

    precision = (tp / (tp + fp)) if (tp + fp) else None
    recall_ans = (tp / (tp + fn)) if (tp + fn) else None
    f1 = None
    if precision is not None and recall_ans is not None and (precision + recall_ans) > 0:
        f1 = 2 * precision * recall_ans / (precision + recall_ans)

    report = {
        "benchmark_path": str(benchmark_path),
        "generated_at_epoch_s": int(time.time()),
        "backend": args.backend,
        "total_examples": len(rows),
        "success_count": success_count,
        "http_error_count": http_error_count,
        "latency_ms_p50": round(_pctl(latencies_ms, 0.50), 2) if latencies_ms else None,
        "latency_ms_p95": round(_pctl(latencies_ms, 0.95), 2) if latencies_ms else None,
        "latency_ms_mean": round(statistics.mean(latencies_ms), 2) if latencies_ms else None,
        "retrieval_recall_at_merged_cap": round(statistics.mean(recalls), 4) if recalls else None,
        "retrieval_recall_at_rerank_candidates": round(statistics.mean(recalls), 4) if recalls else None,
        "answerable_precision": round(precision, 4) if precision is not None else None,
        "answerable_recall": round(recall_ans, 4) if recall_ans is not None else None,
        "answerable_f1": round(f1, 4) if f1 is not None else None,
        "answerable_confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "reason_code_counts": reason_code_counts,
        "judge_mode": args.judge,
        "judge_note": "LLM judging is not implemented in this first version." if args.judge == "llm" else None,
        "per_example": per_example,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote report: {out_path}")
    print(f"Examples: {len(rows)}  Success: {success_count}  HTTP errors: {http_error_count}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        raise SystemExit(130)
