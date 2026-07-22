"""比對兩次 golden eval 結果，列出退步/進步的題目。

用法：
    python scripts/eval_diff.py eval/results/golden_A.json eval/results/golden_B.json
    python scripts/eval_diff.py --latest   # 自動取 eval/results 最新兩份

退步定義：law_hit_rate 或 keyword_coverage 任一項下降。
Exit code: 有退步題目 → 1（可供 CI/腳本判斷），否則 0。
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_report(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {q["id"]: q for q in data["per_question"]}, data


def fmt(v):
    return "-" if v is None else f"{v:.0%}"


def main():
    parser = argparse.ArgumentParser(description="Diff two golden eval reports")
    parser.add_argument("old", nargs="?", help="舊報告路徑")
    parser.add_argument("new", nargs="?", help="新報告路徑")
    parser.add_argument("--latest", action="store_true", help="取 eval/results 最新兩份")
    args = parser.parse_args()

    if args.latest:
        reports = sorted((ROOT / "eval" / "results").glob("golden_*.json"))
        if len(reports) < 2:
            print("eval/results 內不足兩份報告")
            return 1
        old_path, new_path = reports[-2], reports[-1]
    elif args.old and args.new:
        old_path, new_path = args.old, args.new
    else:
        parser.error("需提供 old new 兩個路徑，或使用 --latest")

    old_q, old_data = load_report(old_path)
    new_q, new_data = load_report(new_path)

    print(f"OLD: {old_path} ({old_data['run_at']})")
    print(f"NEW: {new_path} ({new_data['run_at']})")
    print()
    print("Summary:")
    for key in ("avg_law_hit_rate", "questions_all_laws_hit", "avg_keyword_coverage"):
        ov, nv = old_data["summary"].get(key), new_data["summary"].get(key)
        arrow = "→"
        if isinstance(ov, (int, float)) and isinstance(nv, (int, float)):
            arrow = "↑" if nv > ov else ("↓" if nv < ov else "→")
        print(f"  {key}: {ov} {arrow} {nv}")

    regressed, improved = [], []
    for qid in sorted(set(old_q) & set(new_q)):
        o, n = old_q[qid], new_q[qid]
        for metric in ("law_hit_rate", "keyword_coverage"):
            ov, nv = o.get(metric), n.get(metric)
            if ov is None or nv is None:
                continue
            if nv < ov:
                regressed.append((qid, metric, ov, nv))
            elif nv > ov:
                improved.append((qid, metric, ov, nv))

    only_old = sorted(set(old_q) - set(new_q))
    only_new = sorted(set(new_q) - set(old_q))

    if regressed:
        print("\n退步題目:")
        for qid, metric, ov, nv in regressed:
            print(f"  [REGRESS] {qid} {metric}: {fmt(ov)} → {fmt(nv)}")
    if improved:
        print("\n進步題目:")
        for qid, metric, ov, nv in improved:
            print(f"  [IMPROVE] {qid} {metric}: {fmt(ov)} → {fmt(nv)}")
    if only_old:
        print(f"\n僅存在於舊報告: {', '.join(only_old)}")
    if only_new:
        print(f"\n僅存在於新報告: {', '.join(only_new)}")
    if not regressed and not improved:
        print("\n兩次結果無差異")

    return 1 if regressed else 0


if __name__ == "__main__":
    sys.exit(main())
