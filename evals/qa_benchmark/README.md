# QA Benchmark JSONL Format

One JSON object per line.

Required fields:

- `job_id` (string)
- `question` (string)
- `answerable` (boolean)
- `gold_answer` (string)
- `gold_unit_ids` (array of strings)

Optional fields:

- `gold_region_ids` (array of strings)
- `question_type` (string)
- `notes` (string)

Example row:

```json
{"job_id":"022d9d2cfcf5","question":"What is the main function of xylem vessels?","answerable":true,"gold_answer":"They transport water and dissolved minerals through the plant.","gold_unit_ids":["chunk_001:page_003:s2"],"question_type":"fact"}
```

Run evaluator:

```bash
python3 scripts/qa_eval.py --benchmark evals/qa_benchmark/sample.jsonl --out evals/qa_benchmark/report.json
```
