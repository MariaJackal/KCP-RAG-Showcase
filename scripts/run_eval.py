import argparse
import json
import time
import sys
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import load_settings
from personas import get_persona
from services.cache_store import TTLCache
from services.client_factory import build_search_client
from services.model_factory import build_answer_client, build_rewriter_client
from services.pipeline import run_rag_pipeline

BLOCK_REPLY = "抱歉，本系統僅提供法規查詢服務，無法回應其他問題。"
EVAL_PERSONA_ID = "traffic_officer"


def load_cases(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_dotenv_file(path):
    """Minimal .env loader to support direct script execution."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def evaluate_case(case, settings, rewriter_model, answer_model, search_client, persona):
    q = case["question"]
    expected_route = case.get("expected_route")
    expected_response_type = case.get("expected_response_type")
    must_include = case.get("must_include", [])
    must_not_include = case.get("must_not_include", [])
    min_answer_chars = case.get("min_answer_chars")

    # Fresh per-case caches so cases don't pollute each other
    search_cache = TTLCache(ttl_seconds=300, max_entries=64)
    answer_cache = TTLCache(ttl_seconds=300, max_entries=64)

    started = time.perf_counter()
    result = run_rag_pipeline(
        question=q,
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
    elapsed_ms = (time.perf_counter() - started) * 1000

    route = result.intent
    answer = result.answer

    checks = {}
    checks["route_match"] = expected_route is None or route == expected_route

    if expected_response_type == "block":
        checks["response_type_match"] = answer.strip() == BLOCK_REPLY
    elif expected_response_type == "answer":
        checks["response_type_match"] = (
            "發生錯誤" not in answer
            and ("**結論:**" in answer or "結論:" in answer)
            and ("**法規依據:**" in answer or "法規依據:" in answer)
            and ("**注意事項:**" in answer or "注意事項:" in answer)
        )
    else:
        checks["response_type_match"] = True

    checks["must_include_match"] = all(token in answer for token in must_include)
    checks["must_not_include_match"] = all(token not in answer for token in must_not_include)
    checks["min_chars_match"] = min_answer_chars is None or len(answer) >= min_answer_chars

    # Check pipeline telemetry events were actually triggered (verifies real pipeline ran)
    stage_latencies = result.stage_latency_ms or {}
    checks["pipeline_stages_ran"] = "term_normalize" in stage_latencies or route == "BLOCK"

    passed = all(checks.values())
    return {
        "id": case.get("id", "unknown"),
        "question": q,
        "route": route,
        "answer_preview": answer[:300] if answer else "",
        "answer_chars": len(answer),
        "elapsed_ms": round(elapsed_ms, 1),
        "stage_latency_ms": stage_latencies,
        "checks": checks,
        "passed": passed,
    }


def main():
    parser = argparse.ArgumentParser(description="Run end-to-end eval for Vertex RAG app")
    parser.add_argument("--cases", default="eval/eval_cases.json", help="Path to eval cases JSON")
    parser.add_argument("--output", default="eval/eval_report.json", help="Path to output report JSON")
    args = parser.parse_args()

    load_dotenv_file(ROOT / ".env")
    cases = load_cases(args.cases)
    settings = load_settings()

    if settings.model_provider == "vertexai_legacy":
        import vertexai
        vertexai.init(project=settings.project_id, location=settings.vertex_init_location)

    rewriter_model = build_rewriter_client(settings)
    answer_model = build_answer_client(settings)
    search_client = build_search_client(settings)
    persona = get_persona(EVAL_PERSONA_ID)

    results = []
    total_cases = len(cases)
    for idx, case in enumerate(cases, start=1):
        case_id = case.get("id", f"case_{idx}")
        print(f"Running case {idx}/{total_cases}: {case_id}", flush=True)
        results.append(
            evaluate_case(case, settings, rewriter_model, answer_model, search_client, persona)
        )

    pass_count = sum(1 for r in results if r["passed"])
    total = len(results)
    score = (pass_count / total * 100.0) if total else 0.0
    avg_ms = sum(r["elapsed_ms"] for r in results) / total if total else 0.0

    report = {
        "summary": {
            "total": total,
            "passed": pass_count,
            "failed": total - pass_count,
            "score_percent": round(score, 1),
            "avg_latency_ms": round(avg_ms, 1),
        },
        "results": results,
    }

    output_path = Path(args.output)
    if output_path.exists() and output_path.is_dir():
        output_path = output_path / "eval_report.json"
    elif str(args.output).endswith("/") or str(args.output).endswith("\\"):
        output_path = output_path / "eval_report.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("Eval Summary")
    print(f"- total: {report['summary']['total']}")
    print(f"- passed: {report['summary']['passed']}")
    print(f"- failed: {report['summary']['failed']}")
    print(f"- score: {report['summary']['score_percent']}%")
    print(f"- avg_latency_ms: {report['summary']['avg_latency_ms']}")
    print(f"- report: {output_path}")

    for item in results:
        status = "PASS" if item["passed"] else "FAIL"
        stages = item.get("stage_latency_ms", {})
        norm_ms = stages.get("term_normalize", "-")
        decomp_ms = stages.get("decompose", "-")
        print(
            f"  [{status}] {item['id']} | route={item['route']} | "
            f"{item['elapsed_ms']}ms | norm={norm_ms}ms decomp={decomp_ms}ms"
        )


if __name__ == "__main__":
    main()
