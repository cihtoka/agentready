"""쇼핑 에이전트 시뮬레이터 (규칙 기반, LLM 불필요).

목표: 검색 → 상품 선택 → 가격 확인 → 장바구니 담기 → 장바구니 도달.
사람이 아닌 에이전트가 표준 태그(form/a/button)와 구조화 데이터만으로 따라갈 수 있는지 확인한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from . import htmlutil as H
from .site import Site

SEARCH_HINT = re.compile(r"search|query|keyword|검색|^q$|^kw$", re.I)
PURCHASE_RE = re.compile(r"장바구니|담기|구매|주문|cart|buy", re.I)
GOAL = "상품 검색 → 상품 선택 → 가격 확인 → 장바구니 담기 → 장바구니 도달"


@dataclass
class StepResult:
    id: str
    name: str
    status: str  # ok | warn | fail | skipped | na
    message: str


@dataclass
class SimResult:
    goal: str
    steps: List[StepResult] = field(default_factory=list)

    @property
    def completion(self) -> float:
        if not self.steps:
            return 0.0
        scored = [s for s in self.steps if s.status != "na"]  # 해당 없음 단계는 완료율에서 제외
        if not scored:
            return 0.0
        pts = sum(1.0 if s.status == "ok" else 0.5 if s.status == "warn" else 0.0 for s in scored)
        return pts / len(scored)

    @property
    def blocked_at(self) -> Optional[str]:
        for s in self.steps:
            if s.status == "fail":
                return s.name
        return None


def _find_search_form(soup) -> Tuple[Optional[object], bool]:
    for form in soup.find_all("form"):
        inputs = [i for i in form.find_all("input") if (i.get("type") or "text").lower() in ("text", "search")]
        if not inputs:
            continue
        hint_src = [" ".join(filter(None, [i.get("name"), i.get("id"), i.get("placeholder"), i.get("type")])) for i in inputs]
        is_search = (
            form.get("role") == "search"
            or any(SEARCH_HINT.search(s) for s in hint_src)
            or SEARCH_HINT.search(H.class_id(form))
        )
        if is_search:
            has_submit = bool(form.find("button") or form.find("input", attrs={"type": re.compile("submit|image")}))
            return form, has_submit
    return None, False


def simulate(site: Site) -> SimResult:
    res = SimResult(GOAL)
    home = site.home()
    home_soup = H.parse(site.pages[home])
    state = {"product_page": None}
    single = len(site.pages) == 1  # 단일 페이지 분석(업로드/URL): 페이지 간 이동 단계는 평가하지 않음

    def step_search():
        form, has_submit = _find_search_form(home_soup)
        if form is None:
            return "fail", "검색창을 <form>으로 찾을 수 없음 (div/onclick 기반이거나 검색 UI 없음)"
        if not has_submit:
            return "warn", "검색 form 은 있으나 제출 버튼이 없음 (엔터 입력에 의존)"
        return "ok", "검색 form 과 제출 버튼 확인"

    def step_product():
        if single:
            state["product_page"] = home
            return "na", "단일 페이지 분석: 이 페이지를 상품 페이지로 간주 (링크 탐색은 평가하지 않음)"
        for a in home_soup.find_all("a", href=True):
            target = site.resolve(a["href"])
            if target and "product" in target.lower() and target != home:
                state["product_page"] = target
                return "ok", f"상품 링크 발견: '{a.get_text(' ', strip=True)[:20]}' → {target}"
        if "product" in home.lower():
            state["product_page"] = home
            return "ok", "현재 페이지가 상품 페이지"
        return "fail", "상품 페이지로 가는 <a href> 링크를 찾을 수 없음 (div/td onclick 기반)"

    def step_price():
        page = state["product_page"]
        info = H.price_info(H.parse(site.pages[page]))
        if info["kind"] == "structured":
            return "ok", f"구조화 가격 확인: {info['digits']}"
        if info["kind"] == "text":
            return "ok", f"본문에서 통화 단위 포함 가격 파싱: {info['digits']}원"
        if info["kind"] == "numeric":
            return "warn", f"숫자 {info['digits']} 만 있고 통화 단위가 불명확"
        return "fail", "가격 정보를 찾을 수 없음"

    def step_add_cart():
        soup = H.parse(site.pages[state["product_page"]])
        semantic = []
        for el in soup.find_all(["button", "input", "a"]):
            if el.name == "input" and (el.get("type") or "").lower() not in ("submit", "button", "image"):
                continue
            if el.name == "a" and not el.get("href"):
                continue
            text = el.get_text(" ", strip=True) if el.name != "input" else (el.get("value") or "")
            if PURCHASE_RE.search(text):
                semantic.append(el)
        if semantic:
            if any(el.find_parent("form") for el in semantic) or any(el.name == "a" for el in semantic):
                return "ok", "표준 버튼/링크로 장바구니 담기 가능"
            return "warn", "담기 버튼은 있으나 form 없이 JS 이벤트에만 의존"
        pseudo = [
            el for el in soup.find_all(True)
            if el.has_attr("onclick") and el.name not in ("button", "a", "input")
            and PURCHASE_RE.search(el.get_text(" ", strip=True))
        ]
        if pseudo:
            return "fail", f"담기 동작이 div/span 의 onclick 으로만 구현됨 ({len(pseudo)}개)"
        return "fail", "장바구니 담기 컨트롤을 찾을 수 없음"

    def step_cart():
        if single:
            return "na", "단일 페이지 분석: 장바구니 페이지가 없어 평가하지 않음"
        cart = site.cart_page()
        if cart is None:
            return "warn", "사이트에 장바구니 페이지가 없음 (단일 페이지 업로드 시 정상)"
        for p in (state["product_page"], home):
            soup = H.parse(site.pages[p])
            for a in soup.find_all("a", href=True):
                if site.resolve(a["href"]) == cart:
                    return "ok", f"장바구니 링크 확인 → {cart}"
            for f in soup.find_all("form", attrs={"action": True}):
                if site.resolve(f["action"]) == cart:
                    return "ok", f"장바구니 form 전송 경로 확인 → {cart}"
        return "fail", "장바구니 페이지로 가는 <a>/<form> 경로가 없음"

    steps = [
        ("search", "검색창 찾기", step_search),
        ("product", "상품 선택", step_product),
        ("price", "가격 확인", step_price),
        ("add_cart", "장바구니 담기", step_add_cart),
        ("cart", "장바구니 도달", step_cart),
    ]
    blocked = False
    for sid, name, fn in steps:
        if blocked:
            res.steps.append(StepResult(sid, name, "skipped", "이전 단계 실패로 진행 불가"))
            continue
        status, msg = fn()
        res.steps.append(StepResult(sid, name, status, msg))
        if status == "fail":
            blocked = True
    return res
