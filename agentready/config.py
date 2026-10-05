"""환경설정. .env 를 읽고, 호출 시점의 환경변수 값을 반환한다."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:  # python-dotenv 는 선택 사항
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SITES_DIR = DATA_DIR / "sites"
GUIDE_DIR = DATA_DIR / "guidelines"
REC_DIR = DATA_DIR / "recordings"
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


@dataclass
class Settings:
    provider: str
    model: str
    base_url: str
    api_key: str
    record: bool
    replay_fallback: str
    max_iterations: int
    max_actions: int


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


_override: dict = {}  # 웹 UI 에서 입력한 설정. 서버 메모리에만 유지되고 파일로 저장하지 않는다.


def set_override(values: dict) -> None:
    _override.update(values)


def clear_override() -> None:
    _override.clear()


def _get(key: str, env_name: str, default: str = "") -> str:
    if key in _override:
        return str(_override[key])
    return os.getenv(env_name) or default


def settings() -> Settings:
    rec = _override["record"] if "record" in _override else (os.getenv("RECORD") or "0").strip() == "1"
    return Settings(
        provider=_get("provider", "LLM_PROVIDER", "mock").strip().lower(),
        model=_get("model", "LLM_MODEL").strip(),
        base_url=_get("base_url", "LLM_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/"),
        api_key=_get("api_key", "LLM_API_KEY").strip(),
        record=bool(rec),
        replay_fallback=(os.getenv("REPLAY_FALLBACK") or "").strip().lower(),
        max_iterations=_int("MAX_ITERATIONS", 4),
        max_actions=_int("MAX_ACTIONS_PER_ITER", 3),
    )
