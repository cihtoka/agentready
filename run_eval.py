"""샘플 사이트 전체에 에이전트를 돌려 전/후 점수 표를 만든다 (문서/발표용).

사용법:  python run_eval.py            # .env 의 LLM_PROVIDER 사용 (기본 mock)
         python run_eval.py --provider replay
         RECORD=1 LLM_PROVIDER=openai python run_eval.py   # 실제 응답 녹화
"""
from __future__ import annotations

import argparse
import json
import time

from agentready import config
from agentready.agent import Agent
from agentready.llm import get_llm
from agentready.rag import Guidelines, get_embedder
from agentready.site import Site


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default=None)
    ap.add_argument("--no-approve", action="store_true", help="고위험 수정을 승인 대기로 남김")
    args = ap.parse_args()

    llm = get_llm(args.provider)
    guidelines = Guidelines(config.GUIDE_DIR, embedder=get_embedder())
    rows, dump = [], []
    for d in sorted(p for p in config.SITES_DIR.iterdir() if p.is_dir()):
        site = Site.from_dir(d)
        t0 = time.time()
        res = Agent(llm, guidelines, auto_approve=not args.no_approve).run(site)
        sec = time.time() - t0
        rows.append((site.display, res.before.total, res.after.total, res.before.ext_total, res.after.ext_total,
                     res.sim_before.completion, res.sim_after.completion, len(res.iterations), len(res.pending),
                     len(res.manual), sec))
        dump.append({"site": site.display, "before": res.before.total, "after": res.after.total,
                     "ext_before": res.before.ext_total, "ext_after": res.after.ext_total, "manual": [m["check_id"] for m in res.manual],
                     "sim_before": res.sim_before.completion, "sim_after": res.sim_after.completion,
                     "iterations": len(res.iterations), "pending": [p["action"] for p in res.pending]})

    lines = [
        f"# 평가 결과 (provider={llm.name}, model={llm.model})", "",
        "| 사이트 | 구조 점수(전→후) | 선택 지표(전→후) | 시뮬레이션 완료율(전→후) | 반복 | 승인 대기 | 직접 조치 | 소요(초) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, b, a, xb, xa, sb, sa, it, pe, mn, sec in rows:
        lines.append(f"| {name} | {b} → {a} | {xb} → {xa} | {sb:.0%} → {sa:.0%} | {it} | {pe} | {mn}건 | {sec:.2f} |")
    text = "\n".join(lines) + "\n"
    print(text)
    (config.ROOT / "docs").mkdir(exist_ok=True)
    (config.ROOT / "docs" / "eval_results.md").write_text(text, encoding="utf-8")
    (config.ROOT / "output").mkdir(exist_ok=True)
    (config.ROOT / "output" / "eval_results.json").write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
