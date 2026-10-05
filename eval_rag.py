"""RAG 검색 성능 측정: 자연어 질의 → 정답 점검 항목. (자체 제작 평가셋 30문항)
사용법: python eval_rag.py
"""
import json

from agentready import config
from agentready.rag import Guidelines, get_embedder


def evaluate(g: Guidelines, mode: str, rows):
    r1 = r3 = 0
    for row in rows:
        hits = g.search(row["query"], k=3, mode=mode)
        checks = [h["check"] for h in hits]
        r1 += bool(checks) and checks[0] == row["check"]
        r3 += row["check"] in checks
    return r1 / len(rows), r3 / len(rows)


def main():
    rows = json.loads((config.DATA_DIR / "rag_eval_queries.json").read_text(encoding="utf-8"))
    g = Guidelines(config.GUIDE_DIR, embedder=get_embedder())
    modes = ["tfidf", "bm25", "hybrid"] + (["dense"] if g._dense is not None else [])
    lines = [f"# RAG 검색 성능 (질의 {len(rows)}개, 청크 {len(g.chunks)}개)", "",
             "| 검색 방식 | Recall@1 | Recall@3 |", "|---|---|---|"]
    for m in modes:
        a, b = evaluate(g, m, rows)
        lines.append(f"| {m} | {a:.0%} | {b:.0%} |")
    lines += ["", f"현재 하이브리드 구성: {g.mode}", "",
              "※ 평가셋은 자체 제작한 소규모 질의이며, 정답은 '검색 1·3순위 청크가 올바른 점검 항목에 속하는가'로 판정한다."]
    text = "\n".join(lines) + "\n"
    print(text)
    (config.ROOT / "docs" / "rag_eval.md").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
