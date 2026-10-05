"""AI 에이전트 친화도 점검 항목 10개와 점수 산정 (총 100점)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

from . import htmlutil as H
from .patcher import CHECK_TO_ACTION
from .site import Site


@dataclass
class CheckResult:
    id: str
    name: str
    weight: int
    score: float  # 0.0 ~ 1.0
    detail: str
    group: str = "core"  # core: 구조 점검(기본 100점) | ext: 선택 지표(추론 기반 100점)
    manual: bool = False  # 자동 수정 도구가 없는 항목(직접 조치 필요)
    fixable: bool = True  # False 면 다른 수정이 먼저 필요해서 지금은 계획 대상이 아님

    @property
    def gain(self) -> float:
        return self.weight * (1 - self.score)


@dataclass
class Report:
    site: str
    checks: List[CheckResult]

    @property
    def total(self) -> float:
        """기본 구조 점수(100점): 읽히고 조작되는가."""
        return round(sum(c.weight * c.score for c in self.checks if c.group == "core"), 1)

    @property
    def ext_total(self) -> float:
        """선택 지표 점수(100점, 추론 기반): 후보에 오르고 선택·완료까지 가는가."""
        return round(sum(c.weight * c.score for c in self.checks if c.group == "ext"), 1)

    @property
    def progress(self) -> float:
        return round(self.total + self.ext_total, 1)

    def failing(self, threshold: float = 0.95) -> List[CheckResult]:
        return sorted((c for c in self.checks if c.score < threshold), key=lambda c: -c.gain)

    def by_id(self) -> Dict[str, CheckResult]:
        return {c.id: c for c in self.checks}


Soups = Dict[str, object]
Result = Tuple[float, str]


def _avg(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def chk_jsonld(site: Site, soups: Soups) -> Result:
    scores, notes = [], []
    for p in site.product_pages():
        found = H.find_product(soups[p])
        if not found:
            scores.append(0.0)
            notes.append(f"{p}: Product JSON-LD 없음")
            continue
        prod = found[2]
        off = H.offers_of(prod)
        s = 0.4
        s += 0.2 if prod.get("name") else 0
        s += 0.2 if off and off.get("price") not in (None, "") else 0
        s += 0.2 if off and off.get("priceCurrency") else 0
        scores.append(s)
        if s < 1:
            missing = [k for k, ok in (
                ("name", prod.get("name")),
                ("offers.price", off and off.get("price") not in (None, "")),
                ("priceCurrency", off and off.get("priceCurrency")),
            ) if not ok]
            notes.append(f"{p}: JSON-LD 필드 누락({', '.join(missing)})")
    return _avg(scores), "; ".join(notes) or "상품 구조화 데이터 정상"


def chk_price(site: Site, soups: Soups) -> Result:
    table = {"structured": 1.0, "text": 0.6, "numeric": 0.3, None: 0.0}
    msg = {
        "structured": "가격이 구조화되어 있음",
        "text": "가격이 본문 텍스트로만 존재",
        "numeric": "가격이 통화 단위 없는 숫자로만 존재",
        None: "가격을 찾을 수 없음",
    }
    scores, notes = [], []
    for p in site.product_pages():
        kind = H.price_info(soups[p])["kind"]
        scores.append(table[kind])
        if kind != "structured":
            notes.append(f"{p}: {msg[kind]}")
    return _avg(scores), "; ".join(notes) or msg["structured"]


def chk_availability(site: Site, soups: Soups) -> Result:
    scores, notes = [], []
    for p in site.product_pages():
        soup = soups[p]
        found = H.find_product(soup)
        off = H.offers_of(found[2]) if found else None
        if (off and off.get("availability")) or soup.find(attrs={"itemprop": "availability"}):
            scores.append(1.0)
        elif H.AVAIL_RE.search(H.visible_text(soup)):
            scores.append(0.5)
            notes.append(f"{p}: 재고 정보가 본문 텍스트로만 존재")
        else:
            scores.append(0.0)
            notes.append(f"{p}: 재고/구매 가능 여부 정보 없음")
    return _avg(scores), "; ".join(notes) or "재고 상태가 구조화되어 있음"


def chk_clickables(site: Site, soups: Soups) -> Result:
    good = bad = 0
    bad_pages: Dict[str, int] = {}
    for p, soup in soups.items():
        for el in soup.find_all(True):
            if el.name == "a":
                href = (el.get("href") or "").strip()
                if href and not href.startswith("#") and not href.lower().startswith("javascript:"):
                    good += 1
                elif href or el.has_attr("onclick"):
                    bad += 1
                    bad_pages[p] = bad_pages.get(p, 0) + 1
            elif el.name == "button":
                good += 1
            elif el.name == "input" and (el.get("type") or "").lower() in ("submit", "button", "image"):
                good += 1
            elif el.has_attr("onclick"):
                bad += 1
                bad_pages[p] = bad_pages.get(p, 0) + 1
    total = good + bad
    if total == 0:
        return 0.0, "클릭 가능한 요소가 없음"
    detail = "모든 클릭 요소가 표준 태그" if not bad else (
        f"div/span 등에 onclick 만 있는 요소 {bad}개 (" + ", ".join(f"{k}:{v}" for k, v in bad_pages.items()) + ")"
    )
    return good / total, detail


def chk_labels(site: Site, soups: Soups) -> Result:
    scores: List[float] = []
    for p, soup in soups.items():
        for el in soup.find_all(["input", "select", "textarea"]):
            if el.name == "input" and (el.get("type") or "text").lower() in ("hidden", "submit", "button", "image", "reset"):
                continue
            if el.get("aria-label") or el.get("aria-labelledby") or el.get("title"):
                scores.append(1.0)
            elif el.find_parent("label") or (el.get("id") and soup.find("label", attrs={"for": el.get("id")})):
                scores.append(1.0)
            elif el.get("placeholder"):
                scores.append(0.5)
            else:
                scores.append(0.0)
    if not scores:
        return 1.0, "폼 입력 요소 없음"
    unl = sum(1 for s in scores if s < 1)
    return _avg(scores), (f"라벨이 불완전한 입력 요소 {unl}/{len(scores)}개" if unl else "모든 입력 요소에 라벨 있음")


def chk_landmarks(site: Site, soups: Soups) -> Result:
    parts = (("main", 0.5), ("header", 0.2), ("nav", 0.15), ("footer", 0.15))
    scores, notes = [], []
    for p, soup in soups.items():
        s, miss = 0.0, []
        for tag, w in parts:
            if soup.find(tag) or soup.find(attrs={"role": "main" if tag == "main" else tag}):
                s += w
            else:
                miss.append(tag)
        scores.append(s)
        if miss:
            notes.append(f"{p}: <{'>, <'.join(miss)}> 없음")
    return _avg(scores), "; ".join(notes) or "시맨틱 랜드마크 정상"


def chk_headings(site: Site, soups: Soups) -> Result:
    scores, notes = [], []
    for p, soup in soups.items():
        h1s = soup.find_all("h1")
        s = 0.6 if len(h1s) == 1 else (0.2 if len(h1s) > 1 else 0.0)
        levels = [int(h.name[1]) for h in soup.find_all(re.compile(r"^h[1-6]$"))]
        skipped = any(b - a > 1 for a, b in zip(levels, levels[1:]))
        if levels and not skipped:
            s += 0.4
        scores.append(s)
        if len(h1s) != 1:
            notes.append(f"{p}: h1 {len(h1s)}개")
        if skipped:
            notes.append(f"{p}: 제목 레벨 건너뜀")
    return _avg(scores), "; ".join(notes) or "제목 구조 정상"


def chk_meta(site: Site, soups: Soups) -> Result:
    scores, notes = [], []
    for p, soup in soups.items():
        title = soup.title.get_text(strip=True) if soup.title else ""
        desc = soup.find("meta", attrs={"name": "description"})
        og = soup.find("meta", attrs={"property": re.compile(r"^og:(title|description)$")})
        s, miss = 0.0, []
        if title.lower() not in H.GENERIC_TITLES and len(title) >= 4:
            s += 1 / 3
        else:
            miss.append("의미 있는 title")
        if desc is not None and len((desc.get("content") or "").strip()) >= 20:
            s += 1 / 3
        else:
            miss.append("meta description")
        if og is not None:
            s += 1 / 3
        else:
            miss.append("Open Graph")
        scores.append(s)
        if miss:
            notes.append(f"{p}: {', '.join(miss)} 부족")
    return _avg(scores), "; ".join(notes) or "메타 정보 정상"


def chk_alt(site: Site, soups: Soups) -> Result:
    total = bad = 0
    for soup in soups.values():
        for img in soup.find_all("img"):
            total += 1
            if not (img.get("alt") or "").strip():
                bad += 1
    if total == 0:
        return 1.0, "이미지 없음"
    return (total - bad) / total, (f"alt 없는 이미지 {bad}/{total}개" if bad else "모든 이미지에 alt 있음")


def chk_files(site: Site, soups: Soups) -> Result:
    s, notes = 0.0, []
    robots = site.files.get("robots.txt")
    if robots is None:
        notes.append("robots.txt 없음")
    elif H.robots_blocks_all(robots):
        notes.append("robots.txt 가 모든 접근을 차단")
    else:
        s += 0.5
    if "llms.txt" in site.files:
        s += 0.5
    else:
        notes.append("llms.txt 없음")
    return s, "; ".join(notes) or "robots.txt / llms.txt 정상"


# ======================= 선택 지표 (추론 기반, 벤치마크로 검증 예정) =======================
def chk_ssr(site: Site, soups: Soups) -> Result:
    scores, notes = [], []
    for p in site.product_pages():
        soup, s, miss = soups[p], 0.0, []
        found = H.find_product(soup)
        if H.h1_text(soup) or (found and found[2].get("name")) or soup.find(attrs={"itemprop": "name"}) is not None:
            s += 1 / 3
        else:
            miss.append("상품명")
        if H.price_info(soup)["kind"] is not None:
            s += 1 / 3
        else:
            miss.append("가격")
        shell = any(soup.find(id=i) is not None and not soup.find(id=i).get_text(strip=True) for i in ("root", "app", "__next"))
        if len(H.visible_text(soup)) >= 150 and not shell:
            s += 1 / 3
        else:
            miss.append("충분한 본문 텍스트(JS 렌더링 또는 이미지 위주 의심)")
        scores.append(s)
        if miss:
            notes.append(f"{p}: 정적 HTML에 {', '.join(miss)} 없음")
    return _avg(scores), "; ".join(notes) or "JS 없이도 상품 핵심 정보가 HTML에 있음"


def chk_policy(site: Site, soups: Soups) -> Result:
    scores, notes = [], []
    for p in site.product_pages():
        soup = soups[p]
        vis = H.visible_text(soup)
        found = H.find_product(soup)
        prod = found[2] if found else {}
        off = H.offers_of(prod) or {}
        ship = bool(prod.get("shippingDetails") or off.get("shippingDetails")) or bool(H.SHIP_RE.search(vis))
        ret = bool(prod.get("hasMerchantReturnPolicy") or off.get("hasMerchantReturnPolicy")) or bool(H.RETURN_RE.search(vis))
        scores.append(0.5 * ship + 0.5 * ret)
        miss = [n for n, ok in (("배송", ship), ("반품·교환", ret)) if not ok]
        if miss:
            notes.append(f"{p}: {', '.join(miss)} 정보 없음")
    return _avg(scores), "; ".join(notes) or "배송·반품 정보가 노출됨"


def chk_reviews(site: Site, soups: Soups) -> Result:
    scores, notes = [], []
    for p in site.product_pages():
        soup = soups[p]
        found = H.find_product(soup)
        rating = (found[2].get("aggregateRating") if found else None)
        if isinstance(rating, dict) and rating.get("ratingValue"):
            scores.append(1.0)
        elif H.REVIEW_TEXT_RE.search(H.visible_text(soup)):
            scores.append(0.5)
            notes.append(f"{p}: 리뷰·평점이 본문 텍스트로만 존재")
        else:
            scores.append(0.0)
            notes.append(f"{p}: 리뷰·평점 정보 없음")
    return _avg(scores), "; ".join(notes) or "리뷰·평점이 구조화되어 있음"


def chk_consistency(site: Site, soups: Soups):
    scores, notes, waiting = [], [], False
    for p in site.product_pages():
        soup = soups[p]
        found = H.find_product(soup)
        off = H.offers_of(found[2]) if found else None
        structured = H.digits(off.get("price")) if off and off.get("price") not in (None, "") else ""
        visible = H.visible_price_candidates(soup)
        if not visible:
            scores.append(0.0)
            notes.append(f"{p}: 화면에 가격이 없음")
        elif not structured:
            scores.append(0.5)
            waiting = True
            notes.append(f"{p}: 구조화 가격이 없어 일치 여부 확인 불가")
        elif structured in visible:
            scores.append(1.0)
        else:
            scores.append(0.0)
            notes.append(f"{p}: 화면 가격({', '.join(sorted(visible)[:3])})과 JSON-LD 가격({structured}) 불일치")
    mismatch = any("불일치" in n for n in notes)
    return _avg(scores), "; ".join(notes) or "화면 가격과 구조화 가격이 일치", (mismatch or not waiting)


def chk_overlays(site: Site, soups: Soups) -> Result:
    table = {0: 1.0, 1: 0.6, 2: 0.3}
    scores, notes = [], []
    for p, soup in soups.items():
        n = len(H.overlay_elements(soup))
        scores.append(table.get(n, 0.0))
        if n:
            notes.append(f"{p}: 팝업·배너 {n}개")
    return _avg(scores), "; ".join(notes) or "화면을 가리는 팝업·배너 없음"


def chk_friction(site: Site, soups: Soups) -> Result:
    pages = list(site.product_pages()) + ([site.cart_page()] if site.cart_page() else [])
    login = guest = captcha = False
    for p in pages:
        soup = soups[p]
        vis = H.visible_text(soup)
        login = login or soup.find("input", attrs={"type": "password"}) is not None or bool(H.LOGIN_RE.search(vis))
        guest = guest or bool(H.GUEST_RE.search(vis))
        captcha = captcha or H.has_captcha(soup)
    login = login and not guest
    notes = (["로그인/회원가입 후 구매 요구"] if login else []) + (["캡차 감지"] if captcha else [])
    return max(0.0, 1.0 - 0.5 * login - 0.5 * captcha), ", ".join(notes) or "로그인·캡차 장벽 없음"


CHECKS: List[Tuple[str, str, int, Callable, str]] = [
    ("jsonld", "상품 구조화 데이터(JSON-LD)", 16, chk_jsonld, "core"),
    ("price", "가격 마크업", 12, chk_price, "core"),
    ("availability", "재고 상태 표기", 8, chk_availability, "core"),
    ("clickables", "표준 클릭 요소(a/button)", 18, chk_clickables, "core"),
    ("labels", "폼 입력 라벨", 8, chk_labels, "core"),
    ("landmarks", "시맨틱 랜드마크", 8, chk_landmarks, "core"),
    ("headings", "제목 구조(h1~h6)", 8, chk_headings, "core"),
    ("meta", "title/description/OG 메타", 8, chk_meta, "core"),
    ("alt", "이미지 대체 텍스트", 8, chk_alt, "core"),
    ("machine_files", "robots.txt / llms.txt", 6, chk_files, "core"),
    ("ssr", "정적 HTML 상품 정보", 20, chk_ssr, "ext"),
    ("policy", "배송·반품 정보 노출", 20, chk_policy, "ext"),
    ("reviews", "리뷰·평점 노출", 15, chk_reviews, "ext"),
    ("consistency", "화면·구조화 가격 일치", 15, chk_consistency, "ext"),
    ("overlays", "팝업·배너 방해 요소", 15, chk_overlays, "ext"),
    ("friction", "로그인·캡차 구매 장벽", 15, chk_friction, "ext"),
]
for _g in ("core", "ext"):
    assert sum(w for _, _, w, _, g in CHECKS if g == _g) == 100, _g


def analyze(site: Site) -> Report:
    soups = {p: H.parse(html) for p, html in site.pages.items()}
    results = []
    for cid, name, weight, fn, group in CHECKS:
        out = fn(site, soups)
        score, detail = out[0], out[1]
        fixable = out[2] if len(out) > 2 else True
        results.append(CheckResult(cid, name, weight, round(max(0.0, min(1.0, score)), 3), detail,
                                   group=group, manual=cid not in CHECK_TO_ACTION, fixable=bool(fixable)))
    return Report(site.display, results)
