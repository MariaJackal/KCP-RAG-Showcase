"""Golden set 評估執行器：對每題跑真實 RAG pipeline，計算 3-1 指標。

用法（需 .env 與 GCP 憑證）：
    python scripts/run_golden_eval.py                       # 全部題目
    python scripts/run_golden_eval.py --category 酒駕       # 只跑某分類
    python scripts/run_golden_eval.py --limit 5             # 只跑前 5 題（煙霧測試）

結果寫入 eval/results/golden_<YYYYMMDD_HHMM>.json，供 eval_diff.py 比對。
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from golden_eval import aggregate, evaluate_answer, load_golden_set
from run_eval import load_dotenv_file

from config import load_settings
from personas import get_persona
from services.cache_store import TTLCache
from services.client_factory import build_search_client
from services.model_factory import build_answer_client, build_rewriter_client
from services.pipeline import run_rag_pipeline

EVAL_PERSONA_ID = "traffic_officer"


def run_case(case, settings, rewriter_model, answer_model, search_client, persona):
    # 每題獨立快取，避免題目間互相污染
    search_cache = TTLCache(ttl_seconds=300, max_entries=64)
    answer_cache = TTLCache(ttl_seconds=300, max_entries=64)

    started = time.perf_counter()
    result = run_rag_pipeline(
        question=case["question"],
        persona=persona,
        recent_messages=[],
        rewriter_model=rewriter_model,
        answer_model=answer_model,
        search_client=search_client,
        settings=settings,
        search_cache=search_cache,
        answer_cache=answer_cache,
        persona_id=EVAL_PERSONA_ID,
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)

    sources = list(getattr(result, "sources", ()) or ())
    metrics = evaluate_answer(result.answer or "", case, sources=sources)
    metrics.update(
        question=case["question"],
        intent=result.intent,
        answer_chars=len(result.answer or ""),
        answer=result.answer or "",  # 存全文：miss 分析需要完整法規依據段
        source_titles=[s.get("title", "") for s in sources],  # 檢索了什麼（titles 夠 debug）
        elapsed_ms=elapsed_ms,
        error=result.error,
    )
    return metrics


def main():
    parser = argparse.ArgumentParser(description="Run golden set eval (3-1 metrics)")
    parser.add_argument("--golden", default="eval/golden_set.jsonl")
    parser.add_argument("--output-dir", default="eval/results")
    parser.add_argument("--category", default=None, help="只跑指定分類")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 題")
    args = parser.parse_args()

    load_dotenv_file(ROOT / ".env")
    cases = load_golden_set(ROOT / args.golden)
    if args.category:
        cases = [c for c in cases if c["category"] == args.category]
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        print("沒有符合條件的題目")
        return 1

    settings = load_settings()
    if settings.model_provider == "vertexai_legacy":
        import vertexai

        vertexai.init(project=settings.project_id, location=settings.vertex_init_location)

    rewriter_model = build_rewriter_client(settings)
    answer_model = build_answer_client(settings)
    search_client = build_search_client(settings)
    persona = get_persona(EVAL_PERSONA_ID)

    per_question = []
    for idx, case in enumerate(cases, start=1):
        print(f"[{idx}/{len(cases)}] {case['id']}", flush=True)
        per_question.append(
            run_case(case, settings, rewriter_model, answer_model, search_client, persona)
        )

    summary = aggregate(per_question)
    report = {
        "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "golden_set": args.golden,
        "filter": {"category": args.category, "limit": args.limit},
        "summary": summary,
        "per_question": per_question,
    }

    out_dir = ROOT / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"golden_{datetime.now():%Y%m%d_%H%M}.json"
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("\nGolden Eval Summary")
    for k, v in summary.items():
        print(f"- {k}: {v}")
    print(f"- report: {out_path}")

    low = [
        q for q in per_question
        if q.get("law_hit_rate") is not None and q["law_hit_rate"] < 1.0
    ]
    if low:
        print("\n未全數命中法條的題目:")
        for q in low:
            missed = [f"{r['law']}§{r['article']}" for r in q["laws"] if not r["hit"]]
            print(f"  [{q['law_hit_rate']:.0%}] {q['id']} 缺: {', '.join(missed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
