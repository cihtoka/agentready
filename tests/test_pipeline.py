import json
from pathlib import Path

import pytest

from agentready import config
from agentready.agent import Agent, validate_plan
from agentready.checks import CHECKS, analyze
from agentready.llm import MockLLM, ReplayLLM, RecordingLLM, parse_json, prompt_key
from agentready.patcher import ACTIONS, CHECK_TO_ACTION
from agentready.rag import Guidelines
from agentready.simulator import simulate
from agentready.site import Site


@pytest.fixture(scope="module")
def guidelines():
    return Guidelines(config.GUIDE_DIR)


def load(name):
    return Site.from_dir(config.SITES_DIR / name)


def test_weights_sum_to_100_per_group():
    for g in ("core", "ext"):
        assert sum(w for _, _, w, _, grp in CHECKS if grp == g) == 100
    assert len([1 for *_, g in CHECKS if g == "core"]) == 10 and len([1 for *_, g in CHECKS if g == "ext"]) == 6


def test_score_ordering():
    m, n, g = (analyze(load(x)).total for x in ("messy", "normal", "good"))
    assert m < n < g
    assert g >= 90


def test_core_checks_have_actions_and_ext_manual_flags():
    for cid, _, _, _, grp in CHECKS:
        if grp == "core":
            assert cid in CHECK_TO_ACTION and CHECK_TO_ACTION[cid] in ACTIONS
    r = analyze(load("messy")).by_id()
    assert [c for c in r if r[c].manual] == ["ssr", "policy", "reviews", "overlays", "friction"]  # 자동 수정 도구가 없는 항목
    assert r["consistency"].manual is False


def test_simulator_messy_blocked_good_completes():
    messy, good = simulate(load("messy")), simulate(load("good"))
    assert messy.steps[0].status == "fail" and messy.completion == 0
    assert messy.blocked_at == "검색창 찾기"
    assert all(s.status == "ok" for s in good.steps)


def test_rag_retrieves_matching_section(guidelines):
    for cid, name, *_ in CHECKS:
        assert guidelines.search(f"{name} {cid}", k=1)[0]["check"] == cid


@pytest.mark.parametrize("name", ["messy", "normal", "good"])
def test_agent_improves_score(name, guidelines):
    res = Agent(MockLLM(), guidelines).run(load(name))
    assert res.after.total >= res.before.total
    assert res.sim_after.completion >= res.sim_before.completion
    if name != "good":
        assert res.after.total - res.before.total > 20
        assert len(res.iterations) >= 2  # 한 번에 끝나지 않고 판단-검증 루프를 돈다


def test_messy_sim_recovers(guidelines):
    res = Agent(MockLLM(), guidelines).run(load("messy"))
    assert res.sim_before.completion == 0
    assert res.sim_after.completion == 1


def test_high_risk_actions_held_without_approval(guidelines):
    res = Agent(MockLLM(), guidelines, auto_approve=False).run(load("messy"))
    held = {p["action"] for p in res.pending}
    assert {"semantic_clickables", "add_jsonld"} <= held
    assert res.after.total < 100
    assert "onclick" in res.patched.pages["index.html"]  # 승인 전에는 DOM 이 그대로


def test_patch_does_not_mutate_original(guidelines):
    site = load("messy")
    snapshot = dict(site.pages)
    Agent(MockLLM(), guidelines).run(site)
    assert site.pages == snapshot


def test_jsonld_is_valid_json(guidelines):
    res = Agent(MockLLM(), guidelines).run(load("messy"))
    from agentready import htmlutil as H

    found = H.find_product(H.parse(res.patched.pages["product.html"]))
    assert found is not None
    prod = found[2]
    assert prod["name"] == "무선 청소기 A200"
    assert prod["offers"]["price"] == "129000"


class BrokenLLM(MockLLM):
    name = "broken"

    def generate_json(self, task, prompt, context):
        return {"oops": True}


def test_invalid_llm_output_falls_back(guidelines):
    res = Agent(BrokenLLM(), guidelines).run(load("messy"))
    assert res.after.total > res.before.total
    assert any(e.kind == "error" for e in res.trace)


def test_validate_plan_rejects_unknown_action():
    assert validate_plan({"actions": [{"action": "rm_rf"}]}, {"add_meta"}) is None
    ok = validate_plan({"actions": [{"action": "add_meta", "check_id": "meta", "rationale": "x"}]}, {"add_meta"})
    assert ok[0]["action"] == "add_meta"


def test_parse_json_handles_code_fence():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('설명입니다 {"a": 2} 끝') == {"a": 2}


def test_record_and_replay_roundtrip(tmp_path, guidelines):
    rec_path = tmp_path / "mock.jsonl"
    rec = RecordingLLM(MockLLM(), rec_path)
    live = Agent(rec, guidelines).run(load("messy"))
    assert rec_path.exists() and rec_path.read_text(encoding="utf-8").strip()
    replay = Agent(ReplayLLM(tmp_path), guidelines).run(load("messy"))
    assert replay.after.total == live.after.total
    assert [a["action"] for it in replay.iterations for a in it.applied] == \
           [a["action"] for it in live.iterations for a in it.applied]


def test_replay_without_recording_raises(tmp_path):
    with pytest.raises(RuntimeError):
        ReplayLLM(tmp_path).generate_json("plan_fixes", "x", {})


def test_single_page_upload_marks_navigation_steps_na():
    html = (config.SITES_DIR / "good" / "product.html").read_text(encoding="utf-8")
    sim = simulate(Site.from_html("p", html, "page.html"))
    status = {s.id: s.status for s in sim.steps}
    assert status["product"] == "na" and status["cart"] == "na"
    assert status["search"] == "ok" and status["price"] == "ok" and status["add_cart"] == "ok"
    assert sim.completion == 1.0


def test_server_blocks_local_urls():
    import server

    for bad in ("http://127.0.0.1:8000", "http://localhost/", "file:///etc/passwd", "ftp://example.com"):
        with pytest.raises(ValueError):
            server.fetch(bad)


def test_with_base_injects_into_head():
    import server

    out = server.with_base("<html><head><title>x</title></head><body></body></html>", "https://shop.example/p/1")
    assert '<head><base href="https://shop.example/p/1">' in out


def _fake_openai_server(responses):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    calls = {"n": 0, "auth": None, "model": None}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls["auth"], calls["model"] = self.headers.get("Authorization"), body.get("model")
            code, payload = responses[min(calls["n"], len(responses) - 1)]
            calls["n"] += 1
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, calls


def test_openai_adapter_retries_429_and_parses_fenced_json(monkeypatch):
    from agentready.llm import OpenAICompatibleLLM

    monkeypatch.setattr("time.sleep", lambda s: None)
    ok = {"choices": [{"message": {"content": "```json\n{\"continue\": true, \"reason\": \"ok\"}\n```"}}]}
    srv, calls = _fake_openai_server([(429, {"error": "quota"}), (200, ok)])
    llm = OpenAICompatibleLLM(f"http://127.0.0.1:{srv.server_port}/v1beta/openai", "KEY123", "gemini-x")
    assert llm.generate_json("review", "p", {}) == {"continue": True, "reason": "ok"}
    assert calls["n"] == 2 and calls["auth"] == "Bearer KEY123" and calls["model"] == "gemini-x"
    srv.shutdown()


def test_openai_adapter_error_includes_status_and_body():
    from agentready.llm import OpenAICompatibleLLM

    srv, _ = _fake_openai_server([(401, {"error": {"message": "API key not valid"}})])
    llm = OpenAICompatibleLLM(f"http://127.0.0.1:{srv.server_port}", "bad", "m")
    with pytest.raises(RuntimeError) as e:
        llm.generate_json("review", "p", {})
    assert "401" in str(e.value) and "API key not valid" in str(e.value)
    srv.shutdown()


def test_agent_trace_shows_source_and_fallback(guidelines):
    ok = Agent(MockLLM(), guidelines).run(load("messy"))
    assert any("mock/mock-rule-based" in e.title for e in ok.trace)
    bad = Agent(BrokenLLM(), guidelines).run(load("messy"))
    assert any("규칙 기반 대체" in e.title for e in bad.trace)


def test_llm_test_endpoint_logic_for_mock():
    import server

    r = server.llm_test()
    assert r["ok"] and r["provider"] == "mock"


def test_runtime_config_override_and_key_privacy():
    import server
    from agentready.llm import OpenAICompatibleLLM, get_llm

    try:
        state = server.apply_llm_config({"provider": "openai", "base_url": "https://example.test/v1/",
                                         "model": "m1", "api_key": "SECRET-KEY"})
        assert "SECRET-KEY" not in json.dumps(state) and state["has_key"] is True
        llm = get_llm()
        assert isinstance(llm, OpenAICompatibleLLM) and llm.model == "m1" and llm.api_key == "SECRET-KEY"
        assert llm.base_url == "https://example.test/v1"
        # 키를 비우면 같은 제공자에서는 기존 키 유지, 제공자를 바꾸면 이어받지 않음
        server.apply_llm_config({"provider": "openai", "base_url": "https://example.test/v1", "model": "m2"})
        assert get_llm().api_key == "SECRET-KEY"
        server.apply_llm_config({"provider": "anthropic", "model": "c1"})
        assert get_llm().api_key == ""
        with pytest.raises(ValueError):
            server.apply_llm_config({"provider": "evil"})
        with pytest.raises(ValueError):
            server.apply_llm_config({"provider": "openai", "base_url": "ftp://x"})
    finally:
        config.clear_override()


def test_server_rejects_foreign_host_and_missing_header():
    import http.client
    import threading
    from http.server import ThreadingHTTPServer

    import server

    srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def call(method, path, headers=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", srv.server_port)
        c.request(method, path, body=body, headers=headers or {})
        return c.getresponse().status

    try:
        assert call("GET", "/api/sites") == 200
        assert call("GET", "/api/sites", {"Host": "evil.example"}) == 403
        assert call("POST", "/api/llm_config", {"Content-Type": "text/plain"}, "{}") == 403  # 사전요청 없는 교차 사이트 POST
        assert call("POST", "/api/llm_config", {"X-AgentReady": "1"}, json.dumps({"provider": "mock"})) == 200
    finally:
        srv.shutdown()
        config.clear_override()


def test_scan_and_sample_summaries():
    import server

    sums = server.sample_summaries()
    assert [x["id"] for x in sums] == ["messy", "normal", "good"]  # 점수 낮은 순
    assert sums[0]["blocked_at"] == "검색창 찾기" and sums[2]["blocked_at"] is None
    r = server.scan({"site": "messy"})
    assert r["before"]["total"] == sums[0]["score"] and "index.html" in r["pages_before"]


def _rag_rows():
    return json.loads((config.DATA_DIR / "rag_eval_queries.json").read_text(encoding="utf-8"))


def test_rag_hybrid_recall_on_natural_queries(guidelines):
    rows = _rag_rows()
    assert len(guidelines.chunks) >= 30 and len(rows) >= 30

    def recall(mode, k):
        hit = 0
        for r in rows:
            checks = [h["check"] for h in guidelines.search(r["query"], k=k, mode=mode)]
            hit += (r["check"] in checks) if k > 1 else (bool(checks) and checks[0] == r["check"])
        return hit / len(rows)

    assert recall("hybrid", 1) >= 0.9
    assert recall("hybrid", 3) >= 0.95
    assert recall("hybrid", 1) >= recall("bm25", 1)


def test_corrective_retrieval_records_evidence_and_plan_cites_it(guidelines):
    res = Agent(MockLLM(), guidelines).run(load("messy"))
    assert set(res.evidence) >= {"clickables", "jsonld", "price"}
    for cid, ev in res.evidence.items():
        assert ev["queries"] and ev["hits"] and ev["graded"] in ("ok", "weak")
        assert ev["hits"][0]["check"] == cid
    assert any("[clickables." in a["rationale"] for it in res.iterations for a in it.plan)


def test_dense_embeddings_fuse_and_fall_back():
    import numpy as np

    from agentready.rag import Guidelines as G

    class FakeEmb:
        signature = "fake|v1"

        def embed(self, texts):
            out = np.zeros((len(texts), 64))
            for i, t in enumerate(texts):
                for ch in t:
                    out[i, ord(ch) % 64] += 1
            return out

    g = G(config.GUIDE_DIR, embedder=FakeEmb())
    assert g.dense_error is None and "임베딩" in g.mode
    hits = g.search("div onclick 버튼으로 인식 못 해요", k=3)
    assert hits and "dense" in hits[0]["scores"]

    class BrokenEmb(FakeEmb):
        signature = "broken|v1"

        def embed(self, texts):
            raise RuntimeError("HTTP 401")

    g2 = G(config.GUIDE_DIR, embedder=BrokenEmb())
    assert g2.dense_error and "401" in g2.dense_error and "임베딩" not in g2.mode
    assert g2.search("onclick 버튼", k=1)


def test_server_run_returns_rag_evidence():
    import server

    r = server.run({"site": "messy", "auto_approve": True})
    assert r["rag"]["mode"].startswith("하이브리드") and r["evidence"]
    assert {"check_name", "queries", "hits"} <= set(r["evidence"][0])


def test_corrective_retrieval_retries_with_rewritten_query():
    class Stub:
        mode = "stub"
        calls = []

        def search(self, query, k=3, mode="hybrid"):
            self.calls.append(query)
            if len(self.calls) > 1:  # 첫 질의는 엉뚱한 항목, 재검색 질의에서만 정답
                return [{"id": "labels.fix", "check": "labels", "score": 0.5, "title": "t", "text": "x", "source": "s"}]
            return [{"id": "clickables.fix", "check": "clickables", "score": 0.6, "title": "t", "text": "x", "source": "s"}]

    class C:  # 점검 결과 흉내
        id, name, detail = "labels", "폼 입력 라벨", "index.html: 라벨 없음"

    stub = Stub()
    ev = Agent(MockLLM(), stub)._retrieve(C())
    assert ev["retries"] == 1 and ev["graded"] == "ok" and ev["hits"][0]["check"] == "labels"
    assert ".html" not in stub.calls[0]  # 파일명 제거
    assert len(ev["queries"]) == 2 and ev["queries"][1] != ev["queries"][0]


# ---------------------------------------------------------------- 선택 지표(추론 기반) 확장
def _prod(html_body, head=""):
    return Site.from_html("t", f"<html><head><title>상품 상세 페이지</title>{head}</head><body>{html_body}</body></html>", "product.html")


def _score(site, cid):
    return analyze(site).by_id()[cid]


def test_base_scores_unchanged_by_extension():
    assert [analyze(load(x)).total for x in ("messy", "normal", "good")] == [5.6, 62.7, 97.0]


def test_ext_scores_order_and_ranges():
    m, n, g = (analyze(load(x)).ext_total for x in ("messy", "normal", "good"))
    assert m < n < g and g == 100.0 and 0 <= m <= 100


def test_ssr_detects_empty_js_shell():
    shell = _prod('<div id="root"></div><script src="app.js"></script>')
    full = _prod("<h1>원목 테이블</h1><p>89,000원</p><p>" + "국산 오크 원목으로 만든 튼튼한 테이블입니다. " * 6 + "</p>")
    assert _score(shell, "ssr").score < 0.5 and "JS" in _score(shell, "ssr").detail
    assert _score(full, "ssr").score == 1.0


def test_policy_and_reviews_detection():
    plain = _prod("<h1>x</h1><p>멋진 상품</p>")
    text = _prod("<h1>x</h1><p>배송비 무료</p><p>7일 이내 반품 가능</p><p>★ 4.5 · 리뷰 10개</p>")
    ld = _prod("<h1>x</h1>", '<script type="application/ld+json">{"@type":"Product","name":"x","aggregateRating":{"ratingValue":"4.5","reviewCount":"3"},"hasMerchantReturnPolicy":{"merchantReturnDays":7},"offers":{"shippingDetails":{"x":1}}}</script>')
    assert _score(plain, "policy").score == 0 and _score(plain, "reviews").score == 0
    assert _score(text, "policy").score == 1.0 and _score(text, "reviews").score == 0.5
    assert _score(ld, "policy").score == 1.0 and _score(ld, "reviews").score == 1.0


def test_consistency_match_mismatch_and_waiting():
    ld = lambda price: f'<script type="application/ld+json">{{"@type":"Product","name":"x","offers":{{"price":"{price}"}}}}</script>'
    ok = _prod('<h1>x</h1><span class="price">24,900원</span><p>배송비 3,000원</p>', ld("24900"))
    bad = _prod('<h1>x</h1><span class="price">24,900원</span>', ld("29900"))
    none = _prod('<h1>x</h1><span class="price">24,900원</span>')
    assert _score(ok, "consistency").score == 1.0
    assert _score(bad, "consistency").score == 0 and "불일치" in _score(bad, "consistency").detail and _score(bad, "consistency").fixable
    waiting = _score(none, "consistency")
    assert waiting.score == 0.5 and waiting.fixable is False  # add_jsonld 가 먼저 필요 → 계획 대상 아님


def test_sync_structured_price_action():
    from agentready.patcher import apply_action

    site = _prod('<h1>x</h1><span class="price">24,900원</span>',
                 '<script type="application/ld+json">{"@type":"Product","name":"x","offers":{"price":"29900","priceCurrency":"KRW"}}</script>')
    notes = apply_action(site, "sync_structured_price")
    assert notes and "29900 → 24900" in notes[0]
    assert _score(site, "consistency").score == 1.0
    assert ACTIONS["sync_structured_price"].risk == "high"


def test_overlays_and_friction_detection():
    pop = _prod('<h1>x</h1><div class="popup" style="position:fixed">이벤트</div><div class="cookie" style="position:fixed;bottom:0">쿠키</div>')
    assert _score(pop, "overlays").score == 0.3
    assert _score(_prod("<h1>x</h1>"), "overlays").score == 1.0
    login = _prod("<h1>x</h1><p>로그인 후 주문하실 수 있습니다.</p>")
    guest = _prod("<h1>x</h1><p>로그인 후 주문하실 수 있습니다.</p><p>비회원 구매 가능</p>")
    cap = _prod('<h1>x</h1><div class="g-recaptcha" data-sitekey="k"></div>')
    assert _score(login, "friction").score == 0.5 and _score(guest, "friction").score == 1.0 and _score(cap, "friction").score == 0.5


def test_agent_reports_manual_items_and_never_plans_them(guidelines):
    res = Agent(MockLLM(), guidelines).run(load("messy"))
    manual_ids = {m["check_id"] for m in res.manual}
    assert {"reviews", "policy", "overlays", "friction"} <= manual_ids
    planned = {a["action"] for it in res.iterations for a in it.plan}
    assert planned <= set(ACTIONS) and all(CHECK_TO_ACTION.get(m) is None for m in manual_ids)
    assert all(m["guide"] and m["guide"]["id"].startswith(m["check_id"] + ".") for m in res.manual)  # RAG 조치 가이드
    assert res.after.ext_total > res.before.ext_total  # add_jsonld 가 가격 일치 등을 개선


def test_server_run_returns_manual_and_ext_scores():
    import server

    r = server.run({"site": "normal", "auto_approve": True})
    assert r["after"]["ext_total"] > r["before"]["ext_total"] and {m["check_id"] for m in r["manual"]} >= {"policy", "reviews"}
    assert {c["group"] for c in r["before"]["checks"]} == {"core", "ext"}
    assert server.sample_summaries()[0]["ext_score"] is not None


def test_clickable_conversion_preserves_original_attributes():
    from agentready.patcher import apply_action

    site = _prod('''<h1>x</h1><span class="price">470,000원</span>
<div hidden class="soldout" aria-label="품절 안내" data-x="1" onclick="soldOut()">품절</div>
<div class="buy" role="button" tabindex="0" data-id="9" title="구매" onclick="addCart()">장바구니 담기</div>''')
    apply_action(site, "semantic_clickables")
    html = site.pages["product.html"]
    assert 'hidden=""' in html and 'aria-label="품절 안내"' in html and 'data-x="1"' in html  # 숨김 요소는 계속 숨김
    assert 'tabindex="0"' in html and 'title="구매"' in html and 'data-id="9"' in html
    assert 'onclick="soldOut()"' in html and "<form" in html  # 동작과 cart form 은 유지


def test_alt_fallback_uses_page_title_not_generic_number():
    from agentready.patcher import apply_action

    site = Site.from_html("t", '<html><head><title>코듀로이 코트 (블랙)</title></head><body><div><img src="a.jpg"></div></body></html>', "page.html")
    apply_action(site, "alt_text")
    assert 'alt="코듀로이 코트 (블랙) 이미지 1"' in site.pages["page.html"]
