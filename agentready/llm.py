"""교체 가능한 LLM 어댑터.

LLM_PROVIDER = mock | openai | anthropic | replay  (.env 로 선택)
- mock      : 컨텍스트 기반 결정적 응답 (내부망 데모용, 외부 호출 없음)
- openai    : OpenAI 호환 API (DeepSeek, Ollama, vLLM, 사내 게이트웨이는 LLM_BASE_URL 만 변경)
- anthropic : Claude API
- replay    : RECORD=1 로 녹화해 둔 실제 모델 응답을 재생 (내부망 시연용)
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List

from . import config

SYSTEM_PROMPT = (
    "당신은 웹사이트의 AI 쇼핑 에이전트 친화도를 개선하는 에이전트입니다. "
    "반드시 요청된 JSON 객체 하나만 출력하고, 설명이나 마크다운 코드블록은 출력하지 마세요."
)


def parse_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        return json.loads(m.group(0))
    raise ValueError("JSON 응답을 찾을 수 없음")


def prompt_key(task: str, prompt: str) -> str:
    return hashlib.sha256(f"{task}\n{prompt}".encode("utf-8")).hexdigest()[:16]


def _post(url: str, headers: Dict[str, str], body: Dict[str, Any]):
    """429/503(무료 한도, 일시 과부하)은 최대 2회 재시도하고, 실패하면 응답 본문을 에러에 포함한다."""
    import time

    import requests

    for attempt in range(3):
        r = requests.post(url, headers=headers, json=body, timeout=120)
        if r.status_code in (429, 503) and attempt < 2:
            time.sleep(3 * (attempt + 1))
            continue
        break
    if not r.ok:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    return r


class LLMClient:
    name = "base"
    model = ""

    def generate_json(self, task: str, prompt: str, context: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError


# ---------------------------------------------------------------- Mock
class MockLLM(LLMClient):
    """외부 호출 없이 컨텍스트로 응답을 만든다. 실제 LLM 과 같은 JSON 스키마를 반환한다."""

    name = "mock"
    model = "mock-rule-based"

    def generate_json(self, task: str, prompt: str, context: Dict[str, Any]) -> Dict[str, Any]:
        if task == "plan_fixes":
            return self._plan(context)
        if task == "review":
            return self._review(context)
        if task == "summarize":
            return self._summarize(context)
        raise ValueError(f"알 수 없는 작업: {task}")

    def _plan(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        actions, seen = [], set()
        evidence = ctx.get("evidence", {})
        for item in sorted(ctx["failing"], key=lambda x: -x["gain"]):
            act = item["action"]
            if act in seen or act not in ctx["allowed_actions"]:
                continue
            seen.add(act)
            ev = evidence.get(item["check_id"]) or []
            cite = f"[{ev[0]['id']}] {ev[0]['title']}" if ev else "근거 없음"
            actions.append({
                "check_id": item["check_id"],
                "action": act,
                "rationale": f"{item['name']} 점수 {item['score']:.2f} → 개선 여지 {item['gain']}점. 근거: {cite}",
            })
            if len(actions) >= ctx["max_actions"]:
                break
        return {"actions": actions}

    def _review(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        remaining, delta = ctx["remaining"], ctx["delta"]
        if not remaining:
            return {"continue": False, "reason": "남은 개선 항목이 없습니다."}
        if delta <= 0:
            return {"continue": False, "reason": "점수 개선이 없어 반복을 중단합니다."}
        return {"continue": True, "reason": f"{delta}점 상승, 남은 항목 {len(remaining)}개를 다음 반복에서 처리합니다."}

    def _summarize(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        s = f"'{ctx['site']}' 사이트의 AI 에이전트 친화도가 {ctx['before']}점에서 {ctx['after']}점으로 변화했습니다({ctx['iterations']}회 반복). "
        s += f"쇼핑 시뮬레이션 완료율은 {ctx['sim_before']:.0%}에서 {ctx['sim_after']:.0%}로 바뀌었습니다. "
        if "after_ext" in ctx:
            s += f"선택 지표(추론 기반)는 {ctx['before_ext']}점에서 {ctx['after_ext']}점으로 바뀌었습니다. "
        if ctx.get("manual"):
            s += "직접 조치가 필요한 항목: " + ", ".join(ctx["manual"]) + ". "
        if ctx["applied"]:
            s += "적용한 수정: " + ", ".join(ctx["applied"]) + ". "
        if ctx["pending"]:
            s += "승인 대기 중인 고위험 수정: " + ", ".join(ctx["pending"]) + "."
        return {"summary": s.strip()}


# ---------------------------------------------------------------- 실제 LLM
class OpenAICompatibleLLM(LLMClient):
    name = "openai"

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url, self.api_key, self.model = base_url, api_key, model or "gpt-4o-mini"

    def generate_json(self, task: str, prompt: str, context: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
        }
        r = _post(f"{self.base_url}/chat/completions", headers, body)
        return parse_json(r.json()["choices"][0]["message"]["content"])


class AnthropicLLM(LLMClient):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, base_url: str = "https://api.anthropic.com"):
        self.api_key, self.model, self.base_url = api_key, model or "claude-sonnet-5-5", base_url.rstrip("/")

    def generate_json(self, task: str, prompt: str, context: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        body = {
            "model": self.model,
            "max_tokens": 1500,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        }
        r = _post(f"{self.base_url}/v1/messages", headers, body)
        text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
        return parse_json(text)


# ---------------------------------------------------------------- 녹화 / 재생
class RecordingLLM(LLMClient):
    """실제 LLM 호출 결과를 data/recordings/<provider>.jsonl 에 저장한다."""

    def __init__(self, inner: LLMClient, path: Path | None = None):
        self.inner = inner
        self.name, self.model = inner.name, inner.model
        self.path = path or (config.REC_DIR / f"{inner.name}.jsonl")

    def generate_json(self, task: str, prompt: str, context: Dict[str, Any]) -> Dict[str, Any]:
        resp = self.inner.generate_json(task, prompt, context)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"key": prompt_key(task, prompt), "task": task, "provider": self.inner.name, "model": self.inner.model, "response": resp}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return resp


class ReplayLLM(LLMClient):
    name = "replay"
    model = "recorded"

    def __init__(self, directory: Path | None = None, fallback: str = ""):
        self.records: Dict[str, Dict[str, Any]] = {}
        d = Path(directory or config.REC_DIR)
        for f in sorted(d.glob("*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self.records[rec["key"]] = rec
        self.fallback = MockLLM() if fallback == "mock" else None

    def generate_json(self, task: str, prompt: str, context: Dict[str, Any]) -> Dict[str, Any]:
        rec = self.records.get(prompt_key(task, prompt))
        if rec is not None:
            return rec["response"]
        if self.fallback is not None:
            return self.fallback.generate_json(task, prompt, context)
        raise RuntimeError(
            f"녹화된 응답이 없습니다(task={task}). 외부망에서 RECORD=1 로 먼저 녹화하거나 REPLAY_FALLBACK=mock 을 설정하세요."
        )


def get_llm(provider: str | None = None) -> LLMClient:
    s = config.settings()
    provider = (provider or s.provider).lower()
    if provider == "mock":
        return MockLLM()
    if provider == "replay":
        return ReplayLLM(fallback=s.replay_fallback)
    if provider == "openai":
        inner: LLMClient = OpenAICompatibleLLM(s.base_url, s.api_key, s.model)
    elif provider == "anthropic":
        base = s.base_url if "anthropic" in s.base_url else "https://api.anthropic.com"
        inner = AnthropicLLM(s.api_key, s.model, base)
    else:
        raise ValueError(f"지원하지 않는 LLM_PROVIDER: {provider}")
    return RecordingLLM(inner) if s.record else inner
