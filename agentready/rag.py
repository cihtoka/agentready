"""가이드라인 RAG: 하이브리드 검색(TF-IDF + BM25 [+ 임베딩]) 과 RRF 결합.

- 기본 구성은 외부 호출 없이 오프라인으로 동작한다 (TF-IDF 문자 n-gram + BM25).
- 환경변수 RAG_EMBED_MODEL 을 설정하면 OpenAI 호환 /embeddings 로 임베딩 검색이 추가로 결합된다.
  임베딩 호출이 실패하면 자동으로 오프라인 검색만 사용한다.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

_TOKEN = re.compile(r"[0-9A-Za-z가-힣_]+")
_HANGUL = re.compile(r"[가-힣]")
_DENSE_CACHE: Dict[Any, np.ndarray] = {}


def tokenize(text: str) -> List[str]:
    """단어 + 한글 문자 bigram (형태소 분석기 없이 한국어 조사 변화에 강하게)."""
    toks: List[str] = []
    for w in _TOKEN.findall(text.lower()):
        toks.append(w)
        if len(w) >= 2 and _HANGUL.search(w):
            toks.extend(w[i:i + 2] for i in range(len(w) - 1))
    return toks


class BM25:
    def __init__(self, docs: List[List[str]], k1: float = 1.4, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avg = sum(self.len) / max(1, len(docs))
        df: Counter = Counter()
        for c in self.tf:
            df.update(c.keys())
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: List[str]) -> np.ndarray:
        out = np.zeros(len(self.tf))
        for t in set(query):
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i, c in enumerate(self.tf):
                f = c.get(t, 0)
                if f:
                    out[i] += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
        return out


class OpenAIEmbeddings:
    """OpenAI 호환 /embeddings (Gemini, OpenAI, Ollama 등)."""

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url, self.api_key, self.model = base_url.rstrip("/"), api_key, model
        self.signature = f"{self.base_url}|{model}"

    def embed(self, texts: List[str]) -> np.ndarray:
        from .llm import _post

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        r = _post(f"{self.base_url}/embeddings", headers, {"model": self.model, "input": texts})
        data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
        return np.array([d["embedding"] for d in data], dtype=float)


def get_embedder() -> Optional[OpenAIEmbeddings]:
    import os

    from . import config

    model = (os.getenv("RAG_EMBED_MODEL") or "").strip()
    s = config.settings()
    if model and s.provider == "openai":
        return OpenAIEmbeddings(s.base_url, s.api_key, model)
    return None


def _norm(a: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(a, axis=-1, keepdims=True)
    return a / np.where(n == 0, 1, n)


class Guidelines:
    def __init__(self, directory: Path, embedder: Optional[OpenAIEmbeddings] = None):
        self.chunks: List[Dict[str, Any]] = []
        for f in sorted(Path(directory).glob("*.md")):
            text = f.read_text(encoding="utf-8")
            for sec in re.split(r"(?m)^## ", text)[1:]:
                head, _, body = sec.partition("\n")
                cid = head.split(":")[0].strip()
                self.chunks.append({
                    "id": cid, "check": cid.split(".")[0], "topic": cid.split(".")[1] if "." in cid else "",
                    "title": head.split(":", 1)[-1].strip(), "text": body.strip(), "source": f.name,
                })
        if not self.chunks:
            raise ValueError(f"가이드라인 문서를 찾을 수 없음: {directory}")
        docs = [f"{c['title']} {c['text']}" for c in self.chunks]
        self._tfidf = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
        self._matrix = self._tfidf.fit_transform(docs)
        self._bm25 = BM25([tokenize(d) for d in docs])
        self._embedder, self._dense, self.dense_error = None, None, None
        if embedder is not None:
            try:
                key = (embedder.signature, hashlib.md5("".join(docs).encode("utf-8")).hexdigest())
                if key not in _DENSE_CACHE:
                    _DENSE_CACHE[key] = _norm(embedder.embed(docs))
                self._dense, self._embedder = _DENSE_CACHE[key], embedder
            except Exception as e:  # noqa: BLE001
                self.dense_error = f"{type(e).__name__}: {e}"

    @property
    def mode(self) -> str:
        return "하이브리드(임베딩+BM25+TF-IDF)" if self._dense is not None else "하이브리드(BM25+TF-IDF)"

    def search(self, query: str, k: int = 3, mode: str = "hybrid") -> List[Dict[str, Any]]:
        """mode: hybrid | tfidf | bm25 | dense.  score 는 TF-IDF 코사인(0~1) 으로 신뢰도 판단에 쓰고, 순위는 RRF 로 결정한다."""
        tfidf = cosine_similarity(self._tfidf.transform([query]), self._matrix)[0]
        rankers = {"tfidf": tfidf, "bm25": self._bm25.scores(tokenize(query))}
        if self._dense is not None and mode in ("hybrid", "dense"):
            try:
                rankers["dense"] = self._dense @ _norm(self._embedder.embed([query]))[0]
            except Exception as e:  # noqa: BLE001
                self.dense_error = f"{type(e).__name__}: {e}"
        use = [mode] if mode in rankers else list(rankers)
        fused = np.zeros(len(self.chunks))
        for name in use:
            for rank, i in enumerate(np.argsort(-rankers[name])):
                fused[i] += 1.0 / (60 + rank + 1)
        out = []
        for i in np.argsort(-fused)[:k]:
            if all(rankers[n][i] <= 0 for n in use):
                continue
            out.append({**self.chunks[i], "score": round(float(tfidf[i]), 3), "fused": round(float(fused[i]), 4),
                        "scores": {n: round(float(rankers[n][i]), 3) for n in rankers}})
        return out
