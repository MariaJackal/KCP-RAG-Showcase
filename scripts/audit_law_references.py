#!/usr/bin/env python3
"""掃描專案中所有條號引用，輸出報告供人工 review。

不呼叫外部 API——純靜態掃描。若需查證條號是否存在，
輸出的條號清單可搭配 taiwan-legal-db MCP 手動核對。

使用方式：
    python scripts/audit_law_references.py
"""
import io
import json
import re
import sys
from pathlib import Path

# Force UTF-8 output so Chinese characters display correctly on Windows terminals.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent.parent

SCAN_TARGETS = [
    ROOT / "data" / "legal_synonyms.json",
    ROOT / "services" / "term_normalizer.py",
    ROOT / "services" / "rewrite_service.py",
    ROOT / "services" / "local_query_expand.py",
    ROOT / "services" / "pipeline.py",
]

# 匹配「第N條」或「§N」（N 可含 - 如 185-4）
_ARTICLE_PATTERN = re.compile(r"第\s*(\d[\d-]*)\s*條|§\s*(\d[\d-]*)")


def extract_articles(text: str) -> list[str]:
    hits = []
    for m in _ARTICLE_PATTERN.finditer(text):
        num = m.group(1) or m.group(2)
        hits.append(num)
    return hits


def scan_file(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        return {"error": str(e), "articles": []}
    articles = extract_articles(text)
    unique = sorted(set(articles), key=lambda x: (len(x), x))
    return {"articles": unique, "raw_count": len(articles)}


def load_synonyms_meta(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = []
    for rule in data.get("rules", []):
        rows.append({
            "category": rule.get("category", ""),
            "verified_at": rule.get("verified_at", "未查證"),
            "verified_against": rule.get("verified_against", ""),
            "articles": extract_articles(" ".join(rule.get("legal_terms", []))),
        })
    return rows


def main():
    print("=" * 60)
    print("法規條號 Audit 報告")
    print("=" * 60)

    all_articles: set[str] = set()

    for target in SCAN_TARGETS:
        result = scan_file(target)
        rel = target.relative_to(ROOT)
        if "error" in result:
            print(f"\n[ERROR] {rel}: {result['error']}")
            continue
        articles = result["articles"]
        all_articles.update(articles)
        print(f"\n[{rel}]")
        if articles:
            print(f"  條號（去重後共 {len(articles)} 個）：{', '.join('第' + a + '條' for a in articles)}")
        else:
            print("  （未找到條號）")

    print("\n" + "=" * 60)
    print("legal_synonyms.json 規則查證狀態")
    print("=" * 60)
    synonym_path = ROOT / "data" / "legal_synonyms.json"
    for row in load_synonyms_meta(synonym_path):
        status = "[OK]" if row["verified_at"] != "未查證" else "[!!] 未查證"
        print(f"  {status}  {row['category']}")
        print(f"          查證日期：{row['verified_at']}")
        if row["verified_against"]:
            print(f"          查證來源：{row['verified_against']}")
        if row["articles"]:
            print(f"          條號：{', '.join('第' + a + '條' for a in row['articles'])}")

    print("\n" + "=" * 60)
    print(f"全專案出現條號（去重）：{len(all_articles)} 個")
    for a in sorted(all_articles, key=lambda x: (len(x), x)):
        print(f"  第{a}條")

    print("\n注意：此腳本為靜態掃描，條號是否存在需用 taiwan-legal-db MCP 手動查證。")


if __name__ == "__main__":
    main()
