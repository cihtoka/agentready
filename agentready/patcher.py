"""수정 액션(도구) 모음. 에이전트는 이 액션 이름만 선택할 수 있고, 실제 HTML 변경은 여기서 결정적으로 수행한다."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List

from bs4 import NavigableString, Tag

try:
    from bs4.element import Script
except Exception:  # pragma: no cover
    Script = NavigableString  # type: ignore

from . import htmlutil as H
from .site import Site


@dataclass
class Action:
    name: str
    risk: str  # low | high
    fixes: List[str]
    desc: str
    fn: Callable[[Site], List[str]]


ACTIONS: Dict[str, Action] = {}


def action(name: str, risk: str, fixes: List[str], desc: str):
    def deco(fn):
        ACTIONS[name] = Action(name, risk, fixes, desc, fn)
        return fn

    return deco


def _save(site: Site, page: str, soup) -> None:
    site.pages[page] = str(soup)


def _set_script_text(script, text: str) -> None:
    script.clear()
    script.append(Script(text))


# ---------------------------------------------------------------- JSON-LD
@action("add_jsonld", "high", ["jsonld", "availability"],
        "상품 페이지에 schema.org Product JSON-LD 추가/보완 (재고 상태는 본문 기준 추정 → 판매자 확인 필요)")
def add_jsonld(site: Site) -> List[str]:
    notes: List[str] = []
    for p in site.product_pages():
        soup = H.parse(site.pages[p])
        name = H.h1_text(soup)
        if not name:
            cand = _title_candidate(soup)
            name = cand.get_text(" ", strip=True) if cand is not None else ""
        if not name:
            t = soup.title.get_text(strip=True) if soup.title else ""
            name = t if t.lower() not in H.GENERIC_TITLES else p
        digits = H.price_info(soup)["digits"]
        vis = H.visible_text(soup)
        availability = (
            "https://schema.org/OutOfStock" if re.search(r"품절|sold\s*out", vis, re.I) else "https://schema.org/InStock"
        )
        img = soup.find("img")
        image = img.get("src") if img is not None and img.get("src") else None
        found = H.find_product(soup)
        if found:
            script, data, prod = found
            before = json.dumps(data, sort_keys=True, ensure_ascii=False)
            prod.setdefault("name", name)
            if image:
                prod.setdefault("image", image)
            off = H.offers_of(prod)
            if off is None:
                off = {"@type": "Offer"}
                prod["offers"] = off
            if digits:
                off.setdefault("price", digits)
            off.setdefault("priceCurrency", "KRW")
            off.setdefault("availability", availability)
            if json.dumps(data, sort_keys=True, ensure_ascii=False) != before:
                _set_script_text(script, json.dumps(data, ensure_ascii=False, indent=2))
                _save(site, p, soup)
                notes.append(f"{p}: 기존 Product JSON-LD 보완 (offers/availability)")
            continue
        obj = {"@context": "https://schema.org", "@type": "Product", "name": name}
        if image:
            obj["image"] = image
        offers = {"@type": "Offer", "priceCurrency": "KRW", "availability": availability}
        if digits:
            offers["price"] = digits
        obj["offers"] = offers
        script = soup.new_tag("script", attrs={"type": "application/ld+json"})
        _set_script_text(script, json.dumps(obj, ensure_ascii=False, indent=2))
        if soup.head is not None:
            soup.head.append(script)
        else:
            soup.insert(0, script)
        _save(site, p, soup)
        notes.append(f"{p}: Product JSON-LD 신규 추가 (name={name}, price={digits or '미확인'}, availability 추정)")
    return notes


# ---------------------------------------------------------------- 메타
@action("add_meta", "low", ["meta"], "title/description/Open Graph 메타 태그 보강")
def add_meta(site: Site) -> List[str]:
    notes: List[str] = []
    for p in list(site.pages):
        soup = H.parse(site.pages[p])
        changed = False
        head = soup.head
        if head is None:
            head = soup.new_tag("head")
            if soup.html is not None:
                soup.html.insert(0, head)
            else:
                soup.insert(0, head)
        h1 = H.h1_text(soup)
        if h1 == "":
            cand = _title_candidate(soup)
            h1 = cand.get_text(" ", strip=True) if cand is not None else ""
        title_tag = soup.title
        cur = title_tag.get_text(strip=True) if title_tag else ""
        if cur.lower() in H.GENERIC_TITLES or len(cur) < 4:
            new = f"{h1} | {site.display}" if h1 else site.display
            if title_tag is None:
                title_tag = soup.new_tag("title")
                head.insert(0, title_tag)
            title_tag.string = new
            cur = new
            changed = True
            notes.append(f"{p}: title → '{new}'")
        desc = soup.find("meta", attrs={"name": "description"})
        if desc is None or len((desc.get("content") or "").strip()) < 20:
            first_p = next((x.get_text(" ", strip=True) for x in soup.find_all("p") if len(x.get_text(strip=True)) >= 20), "")
            text = first_p or f"{h1 or cur} - {site.display} 상품 정보 페이지"
            if len(text) < 20:
                text += f" - {site.display} 쇼핑몰 안내"
            text = text[:150]
            if desc is None:
                head.append(soup.new_tag("meta", attrs={"name": "description", "content": text}))
            else:
                desc["content"] = text
            changed = True
            notes.append(f"{p}: meta description 추가")
            desc_text = text
        else:
            desc_text = desc["content"]
        if soup.find("meta", attrs={"property": "og:title"}) is None:
            head.append(soup.new_tag("meta", attrs={"property": "og:title", "content": cur}))
            changed = True
        if soup.find("meta", attrs={"property": "og:description"}) is None:
            head.append(soup.new_tag("meta", attrs={"property": "og:description", "content": desc_text}))
            changed = True
            notes.append(f"{p}: Open Graph 추가")
        if changed:
            _save(site, p, soup)
    return notes


# ---------------------------------------------------------------- 랜드마크
_LANDMARK_CLASSES = [
    ("footer", re.compile(r"(footer|foot|ftr)", re.I)),
    ("header", re.compile(r"(^|[\s_-])(header|hdr|head)([\s_-]|$)", re.I)),
    ("nav", re.compile(r"(gnb|nav|menu|lnb)", re.I)),
]


@action("add_landmarks", "low", ["landmarks"], "div.header/gnb/footer 를 header/nav/footer 로 바꾸고 본문을 <main> 으로 감쌈")
def add_landmarks(site: Site) -> List[str]:
    notes: List[str] = []
    for p in list(site.pages):
        soup = H.parse(site.pages[p])
        body = soup.body
        if body is None:
            continue
        container = body
        while True:
            kids = [c for c in container.find_all(recursive=False) if c.name not in ("script", "style")]
            if len(kids) == 1 and kids[0].name == "div":
                container = kids[0]
            else:
                break
        changed: List[str] = []
        for child in container.find_all(recursive=False):
            if child.name != "div":
                continue
            ident = H.class_id(child)
            for tag, pat in _LANDMARK_CLASSES:
                if soup.find(tag) is None and pat.search(ident):
                    child.name = tag
                    changed.append(tag)
                    break
        if soup.find("main") is None and soup.find(attrs={"role": "main"}) is None:
            rest = [
                c for c in container.contents
                if not (isinstance(c, Tag) and c.name in ("header", "nav", "footer", "script", "style"))
                and not (isinstance(c, NavigableString) and not str(c).strip())
            ]
            if rest:
                main = soup.new_tag("main")
                rest[0].insert_before(main)
                for c in rest:
                    main.append(c.extract())
                changed.append("main")
        if changed:
            _save(site, p, soup)
            notes.append(f"{p}: <{'>, <'.join(changed)}> 적용")
    return notes


# ---------------------------------------------------------------- 제목
_TITLE_CLS = re.compile(r"(^|[\s_-])(tit|title|tit\d|pname|big|prd[-_]?name|product[-_]?(name|title))([\s_-]|$)", re.I)


def _title_candidate(soup):
    for el in soup.find_all(["div", "span", "p", "font", "b", "strong"]):
        if el.find_parent(["header", "nav", "footer"]):
            continue
        text = el.get_text(" ", strip=True)
        if _TITLE_CLS.search(H.class_id(el)) and 2 <= len(text) <= 80:
            return el
    return None


@action("fix_headings", "low", ["headings"], "h1 을 페이지당 1개로 맞추고 제목 레벨 건너뜀을 보정")
def fix_headings(site: Site) -> List[str]:
    notes: List[str] = []
    for p in list(site.pages):
        soup = H.parse(site.pages[p])
        changed: List[str] = []
        h1s = soup.find_all("h1")
        if len(h1s) > 1:
            for h in h1s[1:]:
                h.name = "h2"
            changed.append("중복 h1 → h2")
        elif not h1s:
            cand = _title_candidate(soup)
            if cand is not None:
                cand.name = "h1"
                changed.append(f"'{cand.get_text(' ', strip=True)[:15]}' → h1")
        prev = 0
        for h in soup.find_all(re.compile(r"^h[1-6]$")):
            lvl = int(h.name[1])
            if prev and lvl > prev + 1:
                lvl = prev + 1
                h.name = f"h{lvl}"
                changed.append("제목 레벨 보정")
            prev = lvl
        if changed:
            _save(site, p, soup)
            notes.append(f"{p}: " + ", ".join(changed))
    return notes


# ---------------------------------------------------------------- 라벨
@action("add_labels", "low", ["labels"], "라벨 없는 입력 요소에 aria-label 추가")
def add_labels(site: Site) -> List[str]:
    notes: List[str] = []
    for p in list(site.pages):
        soup = H.parse(site.pages[p])
        count = 0
        for el in soup.find_all(["input", "select", "textarea"]):
            if el.name == "input" and (el.get("type") or "text").lower() in ("hidden", "submit", "button", "image", "reset"):
                continue
            if el.get("aria-label") or el.get("aria-labelledby") or el.get("title"):
                continue
            if el.find_parent("label") or (el.get("id") and soup.find("label", attrs={"for": el.get("id")})):
                continue
            label = el.get("placeholder") or ""
            if not label:
                hint = " ".join(filter(None, [el.get("name"), el.get("id")])).lower()
                if re.search(r"search|query|^q$|kw", hint) or el.get("type") == "search":
                    label = "상품 검색"
                elif re.search(r"qty|quantity|count|수량", hint):
                    label = "수량"
                elif el.name == "select":
                    label = "옵션 선택"
                elif el.get("size") == "2" or el.get("value") == "1":
                    label = "수량"
                else:
                    label = el.get("name") or "입력"
            el["aria-label"] = label
            count += 1
        if count:
            _save(site, p, soup)
            notes.append(f"{p}: aria-label {count}개 추가")
    return notes


# ---------------------------------------------------------------- 클릭 요소
_LOC = re.compile(r"""(?:location(?:\.href)?|window\.open|document\.location)\s*(?:=|\()\s*['"]([^'"]+)['"]""")
_SEARCH_INPUT = re.compile(r"search|query|keyword|검색|^q$|^kw$", re.I)


def _is_search_input(inp) -> bool:
    if (inp.get("type") or "text").lower() not in ("text", "search"):
        return False
    src = " ".join(filter(None, [inp.get("name"), inp.get("id"), inp.get("placeholder"), inp.get("type")]))
    parent_cls = H.class_id(inp.parent) if inp.parent is not None else ""
    return bool(_SEARCH_INPUT.search(src) or _SEARCH_INPUT.search(parent_cls) or "뭐 찾" in src)


@action("semantic_clickables", "high", ["clickables"],
        "div/span 의 onclick 을 a/button 으로 교체하고 검색·담기 UI 를 form 으로 감쌈 (DOM 구조 변경, 동작 확인 필요)")
def semantic_clickables(site: Site) -> List[str]:
    notes: List[str] = []
    cart = site.cart_page() or "cart.html"
    for p in list(site.pages):
        soup = H.parse(site.pages[p])
        counts = {"a": 0, "button": 0, "search_form": 0, "cart_form": 0}
        wrapped = set()
        for inp in soup.find_all("input"):
            if inp.find_parent("form") is None and _is_search_input(inp):
                box = inp.parent
                if box is None or box.name in ("body", "html", "[document]") or id(box) in wrapped:
                    continue
                form = soup.new_tag("form", attrs={"role": "search", "action": "search", "method": "get"})
                box.wrap(form)
                wrapped.add(id(box))
                counts["search_form"] += 1
        for el in list(soup.find_all(True)):
            if not el.has_attr("onclick"):
                continue
            oc = el["onclick"]
            text = el.get_text(" ", strip=True)
            loc = _LOC.search(oc)
            if el.name == "a":
                href = (el.get("href") or "").strip()
                if loc and (not href or href.startswith("#") or href.lower().startswith("javascript:")):
                    el["href"] = loc.group(1)
                    del el["onclick"]
                    counts["a"] += 1
                continue
            if el.name in ("button",) or (el.name == "input" and (el.get("type") or "").lower() in ("submit", "button", "image")):
                continue
            if loc:
                new = soup.new_tag("a", attrs={"href": loc.group(1)})
                kind = "a"
            elif "검색" in text or "search" in oc.lower():
                new = soup.new_tag("button", attrs={"type": "submit", "onclick": oc})
                kind = "button"
            elif re.search(r"담기|장바구니|구매|cart|buy", text + oc, re.I):
                new = soup.new_tag("button", attrs={"type": "submit", "onclick": oc})
                kind = "cart"
            else:
                new = soup.new_tag("button", attrs={"type": "button", "onclick": oc})
                kind = "button"
            for attr, val in el.attrs.items():  # hidden, aria-*, role, tabindex, data-*, title 등 원래 속성을 모두 보존
                if attr == "onclick" or attr in new.attrs:
                    continue
                new[attr] = val
            for child in list(el.contents):
                new.append(child.extract())
            el.replace_with(new)
            if kind == "cart":
                if new.find_parent("form") is None:
                    new.wrap(soup.new_tag("form", attrs={"action": cart, "method": "post"}))
                    counts["cart_form"] += 1
                counts["button"] += 1
            elif kind == "a":
                counts["a"] += 1
            else:
                counts["button"] += 1
        if any(counts.values()):
            _save(site, p, soup)
            notes.append(
                f"{p}: a {counts['a']}개, button {counts['button']}개, 검색 form {counts['search_form']}개, 담기 form {counts['cart_form']}개"
            )
    return notes


# ---------------------------------------------------------------- 가격
@action("price_markup", "low", ["price"], "가격 요소에 itemprop=price/content 를 달고 통화 단위(원)를 명시")
def price_markup(site: Site) -> List[str]:
    notes: List[str] = []
    for p in site.product_pages():
        soup = H.parse(site.pages[p])
        info = H.price_info(soup)
        if info["kind"] in (None, "structured") or not info["digits"]:
            continue
        el = info["element"]
        if el is not None:
            el["itemprop"] = "price"
            el["content"] = info["digits"]
            txt = el.get_text(strip=True)
            if "원" not in txt and "₩" not in txt:
                el.append("원")
        else:
            node = soup.find(string=H.CUR_RE)
            if node is None:
                continue
            m = H.CUR_RE.search(str(node))
            num_start, num_end = (m.start(1), m.end(1)) if m.group(1) else (m.start(2), m.end(2))
            text = str(node)
            span = soup.new_tag("span", attrs={"itemprop": "price", "content": info["digits"]})
            span.string = text[num_start:num_end]
            node.insert_before(text[:num_start])
            node.insert_before(span)
            node.insert_before(text[num_end:])
            node.extract()
        _save(site, p, soup)
        notes.append(f"{p}: itemprop=price content={info['digits']}")
    return notes


# ---------------------------------------------------------------- alt
@action("alt_text", "low", ["alt"], "alt 없는 이미지에 주변 텍스트/상품명 기반 대체 텍스트 추가")
def alt_text(site: Site) -> List[str]:
    notes: List[str] = []
    for p in list(site.pages):
        soup = H.parse(site.pages[p])
        h1 = H.h1_text(soup)
        count = 0
        for i, img in enumerate(soup.find_all("img"), 1):
            if (img.get("alt") or "").strip():
                continue
            near = img.parent.get_text(" ", strip=True) if img.parent is not None else ""
            near = re.sub(r"\s*[\d,]{3,}\s*(원)?$", "", near).strip()[:30]
            title = soup.title.get_text(strip=True) if soup.title else ""
            base = h1 or (title if title.lower() not in H.GENERIC_TITLES else "") or site.display
            if p in site.product_pages() and h1:
                alt = h1 if count == 0 else f"{h1} 이미지 {count + 1}"
            else:
                alt = near or f"{base} 이미지 {i}"  # 파일명이나 '이미지 N' 만으로는 의미가 없어 페이지 제목을 앞에 붙임
            img["alt"] = alt
            count += 1
        if count:
            _save(site, p, soup)
            notes.append(f"{p}: alt {count}개 추가")
    return notes


# ---------------------------------------------------------------- robots / llms
@action("add_machine_files", "high", ["machine_files"],
        "robots.txt(허용)와 llms.txt(사이트 안내)를 추가 — 크롤링 정책이 외부에 공개되므로 운영팀 확인 필요")
def add_machine_files(site: Site) -> List[str]:
    notes: List[str] = []
    robots = site.files.get("robots.txt")
    if robots is None or H.robots_blocks_all(robots):
        site.files["robots.txt"] = "User-agent: *\nAllow: /\n"
        notes.append("robots.txt 추가/수정 (전체 허용)")
    if "llms.txt" not in site.files:
        lines = [f"# {site.display}", f"> {site.display} 쇼핑몰 - AI 에이전트용 사이트 안내", "", "## 주요 페이지"]
        for p, html in site.pages.items():
            soup = H.parse(html)
            title = soup.title.get_text(strip=True) if soup.title else ""
            lines.append(f"- [{title or H.h1_text(soup) or p}]({p})")
        site.files["llms.txt"] = "\n".join(lines) + "\n"
        notes.append("llms.txt 추가")
    return notes


# ---------------------------------------------------------------- 가격 일치
@action("sync_structured_price", "high", ["consistency"],
        "JSON-LD 가격을 화면에 보이는 가격과 같게 맞춤 (어느 쪽이 맞는지 판매자 확인 필요)")
def sync_structured_price(site: Site) -> List[str]:
    notes: List[str] = []
    for p in site.product_pages():
        soup = H.parse(site.pages[p])
        found = H.find_product(soup)
        if not found:
            continue
        script, data, prod = found
        off = H.offers_of(prod)
        vis = H.primary_visible_price(soup)
        if not vis or not off or off.get("price") in (None, ""):
            continue
        if H.digits(off["price"]) == vis:
            continue
        old = off["price"]
        off["price"] = vis
        _set_script_text(script, json.dumps(data, ensure_ascii=False, indent=2))
        _save(site, p, soup)
        notes.append(f"{p}: JSON-LD 가격 {old} → {vis}")
    return notes


CHECK_TO_ACTION: Dict[str, str] = {cid: a.name for a in ACTIONS.values() for cid in a.fixes}


def apply_action(site: Site, name: str) -> List[str]:
    return ACTIONS[name].fn(site)
