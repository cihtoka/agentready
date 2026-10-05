"""분석 대상 사이트(페이지 모음) 표현."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse


@dataclass
class Site:
    name: str
    pages: Dict[str, str]
    files: Dict[str, str] = field(default_factory=dict)  # robots.txt, llms.txt
    label: str = ""

    @property
    def display(self) -> str:
        return self.label or self.name

    @classmethod
    def from_dir(cls, path, name: Optional[str] = None) -> "Site":
        p = Path(path)
        pages = {f.name: f.read_text(encoding="utf-8") for f in sorted(p.glob("*.html"))}
        files = {
            n: (p / n).read_text(encoding="utf-8")
            for n in ("robots.txt", "llms.txt")
            if (p / n).exists()
        }
        label = ""
        meta = p / "site.json"
        if meta.exists():
            label = json.loads(meta.read_text(encoding="utf-8")).get("label", "")
        return cls(name or p.name, pages, files, label)

    @classmethod
    def from_html(cls, name: str, html: str, filename: str = "index.html") -> "Site":
        return cls(name, {filename: html}, {}, name)

    def home(self) -> str:
        if "index.html" in self.pages:
            return "index.html"
        return next(iter(self.pages))

    def product_pages(self) -> List[str]:
        prod = [p for p in self.pages if "product" in p.lower()]
        return prod or list(self.pages)

    def cart_page(self) -> Optional[str]:
        for p in self.pages:
            if "cart" in p.lower():
                return p
        return None

    def copy(self) -> "Site":
        return Site(self.name, dict(self.pages), dict(self.files), self.label)

    def resolve(self, href: Optional[str]) -> Optional[str]:
        """href 를 사이트 내 페이지 이름으로 해석 (평면 구조 가정)."""
        if not href:
            return None
        href = href.strip()
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            return None
        name = Path(urlparse(href).path).name
        return name if name in self.pages else None
