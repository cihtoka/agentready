"""HTML 파싱 공통 유틸: JSON-LD, 가격, 재고, 텍스트 추출."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup, Comment, Doctype

CUR_RE = re.compile(r"(\d[\d,]*)\s*원|₩\s*(\d[\d,]*)")
AVAIL_RE = re.compile(r"품절|재고|구매\s*가능|sold\s*out|in\s*stock|out\s*of\s*stock", re.I)
PRICE_ID_RE = re.compile(r"pri|prc|cost", re.I)
NUM_RE = re.compile(r"^[₩\s]*\d[\d,]{2,}\s*(원)?$")
GENERIC_TITLES = {"", "untitled", "untitled document", "제목없음", "무제", "index", "home", "document", "shop"}
SKIP_TAGS = {"script", "style", "head", "title", "html", "body", "meta", "link"}


def parse(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def visible_text(soup: BeautifulSoup) -> str:
    parts: List[str] = []
    for s in soup.find_all(string=True):
        if isinstance(s, (Comment, Doctype)):
            continue
        if s.parent is not None and s.parent.name in ("script", "style", "title"):
            continue
        t = str(s).strip()
        if t:
            parts.append(t)
    return " ".join(parts)


def h1_text(soup: BeautifulSoup) -> str:
    h = soup.find("h1")
    return h.get_text(" ", strip=True) if h else ""


def class_id(el) -> str:
    return " ".join(el.get("class", [])) + " " + (el.get("id") or "")


# ---------- JSON-LD ----------
def flatten(data: Any) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if isinstance(data, list):
        for d in data:
            items.extend(flatten(d))
    elif isinstance(data, dict):
        if "@graph" in data:
            items.extend(flatten(data["@graph"]))
        else:
            items.append(data)
    return items


def find_product(soup: BeautifulSoup) -> Optional[Tuple[Any, Any, Dict[str, Any]]]:
    """(script 태그, 파싱된 전체 data, Product dict) 또는 None."""
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except Exception:
            continue
        for obj in flatten(data):
            t = obj.get("@type")
            types = t if isinstance(t, list) else [t]
            if "Product" in types:
                return script, data, obj
    return None


def offers_of(prod: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not prod:
        return None
    off = prod.get("offers")
    if isinstance(off, list):
        off = off[0] if off else None
    return off if isinstance(off, dict) else None


# ---------- 가격 ----------
def _digits(s: Any) -> str:
    return re.sub(r"[^\d]", "", str(s or ""))


def price_info(soup: BeautifulSoup) -> Dict[str, Any]:
    """kind: structured | text | numeric | None"""
    found = find_product(soup)
    if found:
        off = offers_of(found[2])
        if off and off.get("price") not in (None, ""):
            return {"kind": "structured", "digits": _digits(off["price"]), "element": None}
    el = soup.find(attrs={"itemprop": "price"})
    if el is not None:
        return {"kind": "structured", "digits": _digits(el.get("content") or el.get_text()) or None, "element": el}
    meta = soup.find("meta", attrs={"property": re.compile(r"(product|og):price:amount")})
    if meta is not None and meta.get("content"):
        return {"kind": "structured", "digits": _digits(meta["content"]), "element": None}
    for el in soup.find_all(True):
        if el.name in SKIP_TAGS or el.find(True):
            continue
        if not PRICE_ID_RE.search(class_id(el)):
            continue
        txt = el.get_text(" ", strip=True)
        if NUM_RE.match(txt):
            kind = "text" if ("원" in txt or "₩" in txt) else "numeric"
            return {"kind": kind, "digits": _digits(txt), "element": el}
    m = CUR_RE.search(visible_text(soup))
    if m:
        return {"kind": "text", "digits": _digits(m.group(1) or m.group(2)), "element": None}
    return {"kind": None, "digits": None, "element": None}


# ---------- robots.txt ----------
def robots_blocks_all(text: str) -> bool:
    applies = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        low = line.lower()
        if low.startswith("user-agent:"):
            applies = low.split(":", 1)[1].strip() == "*"
        elif applies and low.replace(" ", "") == "disallow:/":
            return True
    return False


# ---------- 선택 지표(추론 기반) 보조 ----------
SHIP_RE = re.compile(r"배송|택배|무료배송|도착|출고|shipping|delivery", re.I)
RETURN_RE = re.compile(r"반품|교환|환불|returns?\b|refund", re.I)
REVIEW_TEXT_RE = re.compile(r"(리뷰|후기|상품평)\s*[\d,]+|평점\s*[\d.]+|★\s*[\d.]+|[\d.]+\s*/\s*5\b|rating\s*[\d.]+", re.I)
LOGIN_RE = re.compile(r"로그인\s*(후|이\s*필요|해야)|회원\s*가입\s*(후|해야)|login\s*required|sign\s*in\s*to", re.I)
GUEST_RE = re.compile(r"비회원|guest", re.I)
OVERLAY_NAME_RE = re.compile(r"popup|pop-up|modal|overlay|dimmed|lightbox|cookie|consent|newsletter|interstitial", re.I)
OVERLAY_STRONG_RE = re.compile(r"popup|pop-up|modal|overlay|dimmed|lightbox", re.I)
FIXED_RE = re.compile(r"position\s*:\s*(fixed|sticky)", re.I)
CAPTCHA_RE = re.compile(r"recaptcha|hcaptcha|turnstile|captcha|sitekey", re.I)


def digits(s: Any) -> str:
    return re.sub(r"[^\d]", "", str(s or ""))


def visible_price_candidates(soup: BeautifulSoup) -> set:
    """화면에 보이는 가격 후보(숫자 문자열). 가격 클래스 요소 + '…원' 표기 전부."""
    cands = set()
    for el in soup.find_all(True):
        if el.name in SKIP_TAGS or el.find(True):
            continue
        if PRICE_ID_RE.search(class_id(el)) or el.get("itemprop") == "price":
            txt = el.get_text(" ", strip=True)
            if NUM_RE.match(txt):
                cands.add(digits(txt))
    for m in CUR_RE.finditer(visible_text(soup)):
        cands.add(digits(m.group(1) or m.group(2)))
    return {c for c in cands if c}


def primary_visible_price(soup: BeautifulSoup) -> Optional[str]:
    """화면의 대표 가격: 가격 요소 우선, 없으면 본문에서 처음 나온 '…원'."""
    for el in soup.find_all(True):
        if el.name in SKIP_TAGS or el.find(True):
            continue
        if PRICE_ID_RE.search(class_id(el)) or el.get("itemprop") == "price":
            txt = el.get_text(" ", strip=True)
            if NUM_RE.match(txt):
                return digits(txt)
    m = CUR_RE.search(visible_text(soup))
    return digits(m.group(1) or m.group(2)) if m else None


def overlay_elements(soup: BeautifulSoup) -> list:
    """화면을 가리거나 흐름을 끊을 수 있는 팝업·모달·쿠키 배너 요소(최상위만)."""
    found: list = []
    ids: set = set()
    for el in soup.find_all(True):
        if el.name in SKIP_TAGS or el.name in ("script", "style"):
            continue
        ident, style = class_id(el), el.get("style") or ""
        dialog = el.get("role") in ("dialog", "alertdialog") or el.get("aria-modal") == "true" or (el.name == "dialog" and el.has_attr("open"))
        named_fixed = bool(OVERLAY_NAME_RE.search(ident) and FIXED_RE.search(style))
        strong = bool(OVERLAY_STRONG_RE.search(ident))
        if not (dialog or named_fixed or strong):
            continue
        if any(id(p) in ids for p in el.parents):
            continue
        ids.add(id(el))
        found.append(el)
    return found


def has_captcha(soup: BeautifulSoup) -> bool:
    for el in soup.find_all(True):
        blob = " ".join(str(v) for v in el.attrs.values())
        if CAPTCHA_RE.search(blob):
            return True
    return False
