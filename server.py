"""AgentReady 웹 클라이언트 서버 (표준 라이브러리만 사용, 추가 설치 없음).
실행: python server.py [포트]   →   http://127.0.0.1:8000
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urljoin, urlparse

from agentready import config
from agentready.agent import Agent
from agentready.checks import analyze
from agentready.llm import get_llm
from agentready.rag import Guidelines, get_embedder
from agentready.simulator import simulate
from agentready.site import Site

INDEX = Path(__file__).parent / "web" / "index.html"


def guidelines() -> Guidelines:
    """요청마다 현재 설정으로 구성(임베딩은 RAG_EMBED_MODEL 설정 시에만, 결과는 캐시)."""
    return Guidelines(config.GUIDE_DIR, embedder=get_embedder())
MAX_BODY = 8_000_000


def sites() -> dict:
    return {d.name: Site.from_dir(d) for d in sorted(config.SITES_DIR.iterdir()) if d.is_dir()}


def report(r) -> dict:
    return {"total": r.total, "ext_total": r.ext_total, "checks": [
        {"id": c.id, "name": c.name, "weight": c.weight, "score": c.score, "detail": c.detail,
         "group": c.group, "manual": c.manual} for c in r.checks]}


def sim(s) -> dict:
    return {"completion": s.completion, "steps": [{"name": x.name, "status": x.status, "message": x.message} for x in s.steps]}


UA = {"User-Agent": "Mozilla/5.0 (AgentReady analyzer)", "Accept-Language": "ko,en;q=0.8"}
FETCH_LIMIT = 2_000_000


def _check_url(url: str) -> None:
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("http/https 주소만 입력할 수 있습니다")
    for info in socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80)):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("내부/로컬 주소는 분석할 수 없습니다")


def fetch(url: str):
    """(최종 URL, 본문 텍스트). 리디렉션마다 주소를 다시 검사한다."""
    import requests

    for _ in range(4):
        _check_url(url)
        r = requests.get(url, headers=UA, timeout=15, stream=True, allow_redirects=False)
        if r.status_code in (301, 302, 303, 307, 308):
            url = urljoin(url, r.headers.get("Location", ""))
            continue
        break
    else:
        raise ValueError("리디렉션이 너무 많습니다")
    r.raise_for_status()
    buf = b""
    for chunk in r.iter_content(65536):
        buf += chunk
        if len(buf) > FETCH_LIMIT:
            break
    m = re.search(r"charset=([\w-]+)", r.headers.get("Content-Type", ""), re.I)
    for enc in ([m.group(1)] if m and m.group(1).lower() != "iso-8859-1" else []) + ["utf-8", "cp949"]:
        try:
            return url, buf.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return url, buf.decode("utf-8", errors="replace")


def site_from_url(url: str):
    final, html = fetch(url)
    u = urlparse(final)
    site = Site.from_html(u.netloc, html, filename="page.html")
    for name in ("robots.txt", "llms.txt"):  # 사이트 단위 파일은 실제로 가져와서 점검에 반영
        try:
            _, txt = fetch(f"{u.scheme}://{u.netloc}/{name}")
            if txt.strip() and "<html" not in txt[:500].lower():
                site.files[name] = txt
        except Exception:  # noqa: BLE001
            pass
    return site, final


def with_base(html: str, url: str) -> str:
    """미리보기에서 이미지/CSS 상대경로가 원본 사이트를 가리키도록 <base> 삽입(표시용 사본에만 적용)."""
    tag = f'<base href="{url.replace(chr(34), "")}">'
    if re.search(r"<head[^>]*>", html, re.I):
        return re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + tag, html, count=1, flags=re.I)
    return tag + html


def load_site(payload: dict):
    """(Site, 미리보기용 base URL). 샘플 / URL / 붙여넣기·업로드 HTML 중 하나."""
    if payload.get("url"):
        return site_from_url(str(payload["url"]).strip())
    if payload.get("html"):
        return Site.from_html(str(payload.get("name") or "업로드한 페이지")[:60], str(payload["html"])), None
    return sites()[payload["site"]], None


def scan(payload: dict) -> dict:
    """에이전트 없이 현재 상태만 진단 (규칙 코드, LLM 호출 없음)."""
    site, base_url = load_site(payload)
    return {
        "site": site.display, "before": report(analyze(site)), "sim_before": sim(simulate(site)),
        "pages_before": {k: with_base(v, base_url) if base_url else v for k, v in site.pages.items()},
    }


def sample_summaries() -> list:
    out = []
    for k, v in sites().items():
        r, sm = analyze(v), simulate(v)
        out.append({"id": k, "label": v.display, "score": r.total, "ext_score": r.ext_total, "completion": sm.completion, "blocked_at": sm.blocked_at})
    return sorted(out, key=lambda x: x["score"])


def run(payload: dict) -> dict:
    site, base_url = load_site(payload)
    llm = get_llm()
    guide = guidelines()
    res = Agent(llm, guide, auto_approve=bool(payload.get("auto_approve", True))).run(site)
    names = res.before.by_id()
    return {
        "site": res.site, "provider": llm.name, "summary": res.summary,
        "before": report(res.before), "after": report(res.after),
        "sim_before": sim(res.sim_before), "sim_after": sim(res.sim_after),
        "trace": [{"kind": e.kind, "title": e.title, "detail": e.detail} for e in res.trace],
        "diffs": res.diffs, "pending": [p["action"] for p in res.pending],
        "rag": {"mode": guide.mode, "dense_error": guide.dense_error},
        "manual": res.manual,
        "evidence": [{"check_id": k, "check_name": names[k].name if k in names else k, "queries": v["queries"],
                      "retries": v["retries"], "graded": v["graded"],
                      "hits": [{x: h[x] for x in ("id", "title", "score", "text", "source")} for h in v["hits"]]}
                     for k, v in res.evidence.items()],
        "pages_before": {k: with_base(v, base_url) if base_url else v for k, v in site.pages.items()},
        "pages_after": {k: with_base(v, base_url) if base_url else v for k, v in res.patched.pages.items()},
    }


PROVIDERS = ("mock", "openai", "anthropic", "replay")


def llm_state() -> dict:
    """현재 LLM 설정(키 값은 절대 반환하지 않고 저장 여부만 알려줌)."""
    s = config.settings()
    return {"provider": s.provider, "model": s.model, "base_url": s.base_url, "has_key": bool(s.api_key), "record": s.record}


def apply_llm_config(p: dict) -> dict:
    prov = str(p.get("provider", "mock")).strip().lower()
    if prov not in PROVIDERS:
        raise ValueError(f"지원하지 않는 제공자입니다: {prov}")
    cur = config.settings()
    base = str(p.get("base_url", "")).strip()
    if prov == "openai" and not re.match(r"^https?://", base):
        raise ValueError("OpenAI 호환 주소는 http:// 또는 https:// 로 시작해야 합니다")
    key = str(p.get("api_key", "")).strip() or (cur.api_key if prov == cur.provider else "")
    config.set_override({
        "provider": prov, "base_url": base or cur.base_url,
        "model": str(p.get("model", "")).strip(), "api_key": key, "record": bool(p.get("record")),
    })
    return llm_state()


def llm_test() -> dict:
    """현재 .env 설정으로 LLM 에 아주 작은 요청을 보내 연결/키/모델명을 확인한다 (녹화 파일에는 남기지 않음)."""
    llm = get_llm()
    inner = getattr(llm, "inner", llm)
    info = {"provider": inner.name, "model": inner.model}
    if inner.name in ("mock", "replay"):
        return {**info, "ok": True, "ms": 0, "note": "외부 호출 없는 모드입니다(연결 테스트 불필요)"}
    t0 = time.time()
    try:
        out = inner.generate_json("review", '다음 JSON 만 그대로 출력하세요: {"continue": true, "reason": "연결 확인"}', {})
        return {**info, "ok": True, "ms": int((time.time() - t0) * 1000), "note": str(out.get("reason", ""))[:40]}
    except Exception as e:  # noqa: BLE001
        return {**info, "ok": False, "error": f"{type(e).__name__}: {e}"[:400]}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _guard(self, post: bool = False) -> bool:
        """DNS rebinding / 다른 웹사이트의 localhost 호출 차단."""
        host = self.headers.get("Host", "").rsplit(":", 1)[0].strip("[]").lower()
        if host not in ("127.0.0.1", "localhost"):
            self._json(403, {"error": "허용되지 않은 Host 입니다"})
            return False
        if post and self.headers.get("X-AgentReady") != "1":
            self._json(403, {"error": "잘못된 요청입니다(헤더 누락)"})
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        if not self._guard():
            return
        if self.path in ("/", "/index.html"):
            self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/sites":
            self._json(200, {"sites": sample_summaries(),
                             "provider": get_llm().name, "model": get_llm().model, "config": llm_state()})
        elif self.path == "/api/llm_test":
            self._json(200, llm_test())
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._guard(post=True):
            return
        if self.path not in ("/api/run", "/api/scan", "/api/llm_config"):
            return self._json(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if n > MAX_BODY:
                return self._json(413, {"error": "요청이 너무 큽니다(8MB 제한)"})
            payload = json.loads(self.rfile.read(n) or b"{}")
            handler = {"/api/llm_config": apply_llm_config, "/api/scan": scan, "/api/run": run}[self.path]
            self._json(200, handler(payload))
        except Exception as e:  # noqa: BLE001
            self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def log_message(self, *a) -> None:  # 콘솔 로그 최소화
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"AgentReady  →  http://127.0.0.1:{port}   (종료: Ctrl+C)")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
