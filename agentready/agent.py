"""에이전트 루프: 진단 → (RAG 근거 검색) → LLM 계획 → 승인 → 도구 실행 → 재진단 → 검토 → 반복."""
from __future__ import annotations

import difflib
import time
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from . import config
from .checks import Report, analyze
from .llm import LLMClient
from .patcher import ACTIONS, CHECK_TO_ACTION, apply_action
from .rag import Guidelines
from .simulator import SimResult, simulate
from .site import Site


@dataclass
class TraceEvent:
    kind: str  # observe | think | tool | decide | approval | error | done
    title: str
    detail: str = ""


@dataclass
class IterationLog:
    index: int
    plan: List[Dict[str, Any]]
    applied: List[Dict[str, Any]]
    pending: List[Dict[str, Any]]
    score_before: float
    score_after: float
    review: Dict[str, Any]


@dataclass
class RunResult:
    site: str
    provider: str
    before: Report
    after: Report
    sim_before: SimResult
    sim_after: SimResult
    iterations: List[IterationLog]
    pending: List[Dict[str, Any]]
    diffs: Dict[str, str]
    trace: List[TraceEvent]
    summary: str
    patched: Site
    evidence: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    manual: List[Dict[str, Any]] = field(default_factory=list)  # 직접 조치가 필요한 항목(자동 수정 도구 없음)


def render_prompt(task: str, ctx: Dict[str, Any]) -> str:
    tpl = (config.PROMPT_DIR / f"{task}.txt").read_text(encoding="utf-8")
    return tpl.replace("{context_json}", json.dumps(ctx, ensure_ascii=False, indent=2, sort_keys=True))


# ---------------------------------------------------------------- 응답 검증
def validate_plan(raw: Any, valid_actions: set) -> Optional[List[Dict[str, Any]]]:
    acts = raw.get("actions") if isinstance(raw, dict) else None
    if not isinstance(acts, list):
        return None
    out, seen = [], set()
    for a in acts:
        if not isinstance(a, dict):
            continue
        name = a.get("action")
        if name not in valid_actions or name in seen:
            continue
        seen.add(name)
        out.append({"check_id": str(a.get("check_id", "")), "action": name, "rationale": str(a.get("rationale", ""))[:300]})
    return out or None


def validate_review(raw: Any) -> Optional[Dict[str, Any]]:
    if isinstance(raw, dict) and isinstance(raw.get("continue"), bool):
        return {"continue": raw["continue"], "reason": str(raw.get("reason", ""))[:300]}
    return None


def validate_summary(raw: Any) -> Optional[str]:
    if isinstance(raw, dict) and isinstance(raw.get("summary"), str) and raw["summary"].strip():
        return raw["summary"].strip()
    return None


class Agent:
    def __init__(
        self,
        llm: LLMClient,
        guidelines: Guidelines,
        max_iterations: Optional[int] = None,
        max_actions: Optional[int] = None,
        auto_approve: bool = True,
        on_event: Optional[Callable[[TraceEvent], None]] = None,
    ):
        s = config.settings()
        self.llm = llm
        self.guidelines = guidelines
        self.max_iterations = max_iterations or s.max_iterations
        self.max_actions = max_actions or s.max_actions
        self.auto_approve = auto_approve
        self.on_event = on_event
        self.trace: List[TraceEvent] = []
        self.last: Dict[str, Any] = {"ms": 0, "fallback": False}

    # ------------------------------------------------------------ 유틸
    def _log(self, kind: str, title: str, detail: str = "") -> None:
        ev = TraceEvent(kind, title, detail)
        self.trace.append(ev)
        if self.on_event:
            self.on_event(ev)

    def _ask(self, task: str, ctx: Dict[str, Any], validator: Callable[[Any], Any], fallback: Callable[[], Any]):
        """LLM 호출 → JSON 검증 → 실패 시 재시도(최대 2회) → 규칙 기반 대체."""
        prompt = render_prompt(task, ctx)
        t0 = time.time()
        for attempt in (1, 2):
            try:
                value = validator(self.llm.generate_json(task, prompt, ctx))
                if value is not None:
                    if attempt > 1:
                        self._log("think", f"{task}: 재시도 {attempt}회차에 유효한 응답 수신")
                    self.last = {"ms": int((time.time() - t0) * 1000), "fallback": False}
                    return value
                self._log("error", f"{task}: 응답 형식 오류", f"{attempt}회차 — 스키마 검증 실패")
            except Exception as e:  # noqa: BLE001
                self._log("error", f"{task}: LLM 호출 실패", f"{attempt}회차 — {type(e).__name__}: {e}")
        self._log("think", f"{task}: 규칙 기반 대체 응답 사용", "LLM 응답을 얻지 못해 안전한 기본 계획으로 진행")
        self.last = {"ms": 0, "fallback": True}
        return fallback()

    def _src(self) -> str:
        if self.last["fallback"]:
            return "규칙 기반 대체"
        return f"{self.llm.name}/{self.llm.model or '-'} · {self.last['ms'] / 1000:.1f}s"

    @staticmethod
    def _plannable(c, held) -> bool:
        """자동 수정 도구가 있고, 지금 계획할 수 있으며, 승인 대기 중이 아닌 항목."""
        return c.id in CHECK_TO_ACTION and c.fixable and CHECK_TO_ACTION[c.id] not in held

    @staticmethod
    def _failing_ctx(failing) -> List[Dict[str, Any]]:
        return [
            {
                "check_id": c.id,
                "name": c.name,
                "weight": c.weight,
                "score": round(c.score, 2),
                "detail": c.detail,
                "gain": round(c.gain, 1),
                "action": CHECK_TO_ACTION[c.id],
            }
            for c in failing
        ]

    def _retrieve(self, c) -> Dict[str, Any]:
        """진단 문장을 질의로 검색하고 결과를 채점한다. 1순위가 해당 점검 항목의 가이드가 아니면
        질의를 다듬어 한 번 더 찾고(교정형 검색), 그래도 아니면 '신뢰도 낮음'으로 표시한다."""

        def graded_ok(hs) -> bool:
            return bool(hs) and hs[0]["check"] == c.id

        detail = re.sub(r"[\w.-]+\.html\s*[:：]?", " ", c.detail)  # 페이지 파일명은 검색 잡음이 된다
        queries = [f"{detail} {c.name} 수정 방법".strip()]  # 수정이 목적이므로 수정 방법 조각이 앞서도록
        hits = self.guidelines.search(queries[0], k=3)
        retries = 0
        if not graded_ok(hits):
            queries.append(f"{c.name} 수정 방법")
            retries = 1
            retry_hits = self.guidelines.search(queries[1], k=3)
            if retry_hits:
                hits = retry_hits
        return {"check_id": c.id, "queries": queries, "retries": retries,
                "graded": "ok" if graded_ok(hits) else "weak", "hits": hits}

    # ------------------------------------------------------------ 실행
    def run(self, site: Site) -> RunResult:
        self.trace = []
        before = analyze(site)
        sim_before = simulate(site)
        self._log("observe", "초기 진단", f"구조 점수 {before.total}점 · 선택 지표 {before.ext_total}점 · 쇼핑 시뮬레이션 완료율 {sim_before.completion:.0%}"
                  + (f" · '{sim_before.blocked_at}' 단계에서 막힘" if sim_before.blocked_at else ""))

        cur = site.copy()
        held: Dict[str, str] = {}
        iterations: List[IterationLog] = []
        pending_all: List[Dict[str, Any]] = []
        evidence_all: Dict[str, Dict[str, Any]] = {}

        for it in range(1, self.max_iterations + 1):
            report = analyze(cur)
            failing = [c for c in report.failing() if self._plannable(c, held)]
            if not failing:
                self._log("decide", "개선할 항목이 없음", "남은 항목이 없거나 모두 승인 대기 상태입니다.")
                break

            # 1) RAG: 진단 내용으로 가이드라인 검색 → 결과 채점 → 약하면 질의를 바꿔 재검색 (교정형 검색)
            fresh = [c for c in report.failing() if c.id not in evidence_all and (c.manual or self._plannable(c, held))]
            for c in fresh:
                evidence_all[c.id] = self._retrieve(c)
            if fresh:
                self._log("tool", f"[{it}회차] 가이드라인 검색(RAG · {self.guidelines.mode})", "\n".join(
                    f"{c.id} → [{evidence_all[c.id]['hits'][0]['id']}] 유사도 {evidence_all[c.id]['hits'][0]['score']}"
                    f"{' · 재검색' if evidence_all[c.id]['retries'] else ''}{' · 신뢰도 낮음' if evidence_all[c.id]['graded'] == 'weak' else ''}"
                    for c in fresh if evidence_all[c.id]["hits"]))
            evidence = {
                c.id: [{"id": h["id"], "title": h["title"], "snippet": h["text"][:200]} for h in evidence_all[c.id]["hits"][:2]]
                for c in failing if evidence_all[c.id]["hits"]
            }

            # 2) LLM 계획
            valid = {CHECK_TO_ACTION[c.id] for c in failing}
            ctx = {
                "site": site.display,
                "iteration": it,
                "max_actions": self.max_actions,
                "failing": self._failing_ctx(failing),
                "evidence": evidence,
                "allowed_actions": {n: {"description": ACTIONS[n].desc, "fixes": ACTIONS[n].fixes} for n in sorted(valid)},
            }

            def fallback_plan() -> List[Dict[str, Any]]:
                out, seen = [], set()
                for item in ctx["failing"]:
                    if item["action"] in seen:
                        continue
                    seen.add(item["action"])
                    out.append({"check_id": item["check_id"], "action": item["action"], "rationale": "규칙 기반 기본 계획"})
                    if len(out) >= self.max_actions:
                        break
                return out

            plan = self._ask("plan_fixes", ctx, lambda raw: validate_plan(raw, valid), fallback_plan)[: self.max_actions]
            self._log("think", f"[{it}회차] 수정 계획 수립 · {self._src()}",
                      "\n".join(f"{p['action']}: {p['rationale']}" for p in plan))

            # 3) 승인 + 실행
            applied: List[Dict[str, Any]] = []
            pending: List[Dict[str, Any]] = []
            for item in plan:
                act = ACTIONS[item["action"]]
                if act.risk == "high":
                    if not self.auto_approve:
                        held[act.name] = item["rationale"]
                        pending.append({**item, "desc": act.desc})
                        self._log("approval", f"승인 대기: {act.name}", act.desc)
                        continue
                    self._log("approval", f"고위험 수정 자동 승인: {act.name}", act.desc)
                notes = apply_action(cur, act.name)
                applied.append({**item, "notes": notes})
                self._log("tool", f"{act.name} 실행", "\n".join(notes) or "변경 사항 없음")
            pending_all.extend(pending)

            # 4) 재진단 + 검토
            after = analyze(cur)
            delta = round(after.progress - report.progress, 1)  # 구조 + 선택 지표 합산 변화
            self._log("observe", f"[{it}회차] 재진단",
                      f"구조 {report.total}→{after.total}점 · 선택 지표 {report.ext_total}→{after.ext_total}점")
            remaining = [c for c in after.failing() if self._plannable(c, held)]
            rctx = {
                "iteration": it,
                "before": report.total,
                "after": after.total,
                "before_ext": report.ext_total,
                "after_ext": after.ext_total,
                "delta": delta,
                "remaining": self._failing_ctx(remaining),
            }
            review = self._ask(
                "review", rctx, validate_review,
                lambda: {"continue": bool(remaining) and delta > 0, "reason": "규칙 기반 판단"},
            )
            self._log("think", f"[{it}회차] 검토 · {self._src()}", review["reason"])
            iterations.append(IterationLog(it, plan, applied, pending, report.total, after.total, review))

            if not remaining:
                self._log("decide", "종료: 처리 가능한 항목을 모두 개선", review["reason"])
                break
            if delta <= 0:
                self._log("decide", "종료: 점수 개선 없음", "같은 방법을 반복해도 개선되지 않아 중단합니다.")
                break
            if not review["continue"]:
                self._log("decide", "종료: 검토 결과 중단", review["reason"])
                break
            self._log("decide", f"[{it}회차] 다음 반복 진행", review["reason"])
        else:
            self._log("decide", "종료: 최대 반복 횟수 도달")

        after_report = analyze(cur)
        sim_after = simulate(cur)

        # 자동 수정 도구가 없는 항목: 가이드라인(RAG)으로 조치 방법만 안내
        for c in after_report.failing():
            if c.manual and c.id not in evidence_all:
                evidence_all[c.id] = self._retrieve(c)
        manual = []
        for c in after_report.failing():
            if c.manual:
                hit = (evidence_all.get(c.id, {}).get("hits") or [None])[0]
                manual.append({"check_id": c.id, "name": c.name, "score": c.score, "weight": c.weight, "detail": c.detail,
                               "guide": {k: hit[k] for k in ("id", "title", "text")} if hit else None})
        if manual:
            self._log("decide", f"직접 조치가 필요한 항목 {len(manual)}건",
                      "\n".join(f"{m['name']}: {m['detail']}" for m in manual))
        self._log("observe", "최종 진단",
                  f"구조 {before.total}→{after_report.total}점 · 선택 지표 {before.ext_total}→{after_report.ext_total}점 · "
                  f"시뮬레이션 완료율 {sim_before.completion:.0%} → {sim_after.completion:.0%}")

        diffs = self._diffs(site, cur)
        sctx = {
            "site": site.display,
            "before": before.total,
            "after": after_report.total,
            "before_ext": before.ext_total,
            "after_ext": after_report.ext_total,
            "manual": [m["name"] for m in manual],
            "iterations": len(iterations),
            "sim_before": sim_before.completion,
            "sim_after": sim_after.completion,
            "applied": [a["action"] for it_ in iterations for a in it_.applied],
            "pending": [p["action"] for p in pending_all],
        }
        fallback_summary = lambda: (  # noqa: E731
            f"{before.total}점 → {after_report.total}점, 시뮬레이션 {sim_before.completion:.0%} → {sim_after.completion:.0%}"
        )
        summary = self._ask("summarize", sctx, validate_summary, fallback_summary)
        self._log("done", f"완료 · {self._src()}", summary)

        return RunResult(
            site=site.display, provider=self.llm.name, before=before, after=after_report,
            sim_before=sim_before, sim_after=sim_after, iterations=iterations, pending=pending_all,
            diffs=diffs, trace=list(self.trace), summary=summary, patched=cur, evidence=evidence_all, manual=manual,
        )

    @staticmethod
    def _diffs(old: Site, new: Site) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for p in new.pages:
            if old.pages.get(p) != new.pages[p]:
                out[p] = "".join(difflib.unified_diff(
                    old.pages.get(p, "").splitlines(keepends=True),
                    new.pages[p].splitlines(keepends=True),
                    fromfile=f"before/{p}", tofile=f"after/{p}", n=1,
                ))
        for f, text in new.files.items():
            if old.files.get(f) != text:
                out[f] = "".join(difflib.unified_diff(
                    old.files.get(f, "").splitlines(keepends=True),
                    text.splitlines(keepends=True),
                    fromfile=f"before/{f}", tofile=f"after/{f}", n=1,
                ))
        return out
