# AgentReady 🛒

AI 쇼핑 에이전트(Muse 같은 에이전트)가 쓰기 좋은 사이트인지 진단하고, **에이전트가 스스로 판단해 고치는** 에이전트형 RAG 데모 프로그램.

- 진단: **구조 점검 10항목(100점)** + **선택 지표 6항목(100점, 추론 기반)**
- 쇼핑 시뮬레이션: 검색 → 상품 선택 → 가격 확인 → 장바구니 담기 → 장바구니 도달 (규칙 기반, LLM 불필요)
- 에이전트 루프: RAG 근거 검색(채점·재검색) → LLM 계획 → (승인) → 수정 → 재진단 → 검토 → 반복
- 기본 구성은 **외부 호출 없이 오프라인으로 동작** (Mock LLM + TF-IDF RAG)

구조와 다이어그램은 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), 샘플 평가 결과는 [docs/eval_results.md](docs/eval_results.md) 참고.

## 빠른 시작

```bash
pip install -r requirements.txt
python server.py            # 웹 클라이언트 → http://127.0.0.1:8000 (추가 설치 없음)
streamlit run app.py        # Streamlit 데모 UI
python run_eval.py          # 샘플 3개 전/후 점수 표 생성
python -m pytest -q         # 테스트 41개
```

UI 에서 샘플 사이트를 고르고 **에이전트 실행**을 누르면 트레이스가 실시간으로 쌓이고, 점수 차트·시뮬레이션 전/후·diff·RAG 근거·미리보기가 탭으로 나옵니다.

## 실제 사이트 분석 (웹 클라이언트)
상단의 **URL 입력칸**에 상품 페이지 주소를 넣고 실행하면, 서버가 해당 페이지와 `robots.txt`/`llms.txt`를 가져와 분석합니다. URL 대신 **HTML 업로드**나 **HTML 붙여넣기**(개발자도구 → `<html>` 우클릭 → Copy → Copy outerHTML)도 됩니다. 붙여넣기는 파일이 생기지 않아 보안 프로그램에 지워지지 않습니다. 업로드/붙여넣기한 HTML 은 전송 전에 스크립트·스타일이 제거됩니다.
- 한 페이지만 분석하므로 "상품 링크 찾기/장바구니 페이지 도달"은 `해당 없음`으로 처리하고 완료율에서 제외합니다.
- 서버는 `127.0.0.1`에서만 열리며, 내부/로컬 주소(사설 IP, localhost)는 분석을 거부합니다.
- 큰 쇼핑몰은 봇을 차단하거나 JS 로 화면을 그려서 원본 HTML 에 상품 정보가 없을 수 있습니다. 이때는 브라우저에서 **Ctrl+S → "웹페이지, 전체"** 로 저장한 .html 을 업로드하세요.
- 본인 소유이거나 공개된 페이지를 개인 테스트 용도로만 사용하고, 특정 업체의 점수를 외부 문서에 싣는 것은 피하세요.

## 샘플 사이트 (`data/sites/`)
| 폴더 | 설명 |
|---|---|
| `messy` | 오늘마켓 — div/onclick 기반, 구조화 데이터 없음 (검색 단계에서 막힘) |
| `normal` | 리빙플러스 — 일부만 표준 준수 |
| `good` | 나무결 스토어 — 대부분 표준 준수 |

실제 쇼핑몰은 브라우저에서 **다른 이름으로 저장한 .html 파일**을 UI 의 "HTML 파일 업로드"로 넣어 분석할 수 있습니다. (보안상 업로드 HTML 은 앱에서 렌더링하지 않고 diff 로만 보여줍니다.)

## LLM 연동 (`.env`)

```bash
cp .env.example .env
```

| 설정 | 의미 |
|---|---|
| `LLM_PROVIDER=mock` | 기본값. 외부 호출 없음 (내부망 데모) |
| `LLM_PROVIDER=openai` | OpenAI 호환 API. DeepSeek/Ollama/vLLM/사내 게이트웨이는 `LLM_BASE_URL` 과 `LLM_MODEL` 만 변경 |
| `LLM_PROVIDER=anthropic` | Claude API (`LLM_API_KEY`, `LLM_MODEL`) |
| `LLM_PROVIDER=replay` | 녹화해 둔 실제 모델 응답을 재생 |

API 키는 `.env` 에만 두세요(`.gitignore` 에 포함되어 있습니다).

### 웹 화면에서 바로 설정하기
`.env` 를 건드리지 않고 화면 상단 **⚙ LLM 설정**에서 제공자(Gemini 등 프리셋), 모델명, API 키를 입력하고 **적용 + 연결 테스트**를 누르면 됩니다. 성공(✅)하면 바로 **에이전트 실행**을 누르세요.
- 키는 서버 메모리에만 유지되고 파일로 저장되거나 화면으로 다시 전송되지 않습니다. 서버를 껐다 켜면 다시 입력해야 합니다(고정하려면 `.env` 사용).
- 서버는 `127.0.0.1`/`localhost` Host 만 허용하고, 설정·실행 요청에는 전용 헤더를 요구해 다른 웹사이트가 호출하지 못하게 막습니다.

### 연결 확인
웹 클라이언트 상단의 **LLM 연결 테스트** 버튼으로 현재 `.env` 설정(키, 주소, 모델명)이 맞는지 바로 확인할 수 있습니다. 실패하면 HTTP 상태와 응답 본문(예: `401 API key not valid`, `404 model not found`)이 표시됩니다.
무료 한도 초과(429)와 일시 과부하(503)는 자동으로 최대 2회 재시도합니다. 트레이스에는 각 단계를 어떤 모델이 몇 초 만에 답했는지, 또는 규칙 기반으로 대체했는지가 표시됩니다.

### 내부망 시연: 녹화 → 재생
1. 외부망 PC: `.env` 에 실제 LLM 을 설정하고 `RECORD=1` 로 `python run_eval.py` (또는 UI 실행) → `data/recordings/*.jsonl` 에 요청/응답 저장
2. 녹화 파일을 내부망으로 복사
3. 내부망: `LLM_PROVIDER=replay` 로 실행 → 같은 입력에 같은 응답을 재생

재생 모드로 시연할 때는 "실제 모델이 생성한 응답을 녹화해 재생하는 시연 모드"라고 밝히세요. 녹화본에 없는 입력은 오류가 나며, `REPLAY_FALLBACK=mock` 으로 Mock 대체를 허용할 수 있습니다.

### 내부망에 패키지 반입
```bash
# 외부망 PC
pip download -r requirements.txt -d wheels
# 내부망 PC (wheels 폴더 복사 후)
pip install --no-index --find-links wheels -r requirements.txt
```

## 점수 체계 (두 축)
| 축 | 항목 수 | 의미 | 근거 |
|---|---|---|---|
| 구조 점수 | 10 | 에이전트가 읽고 조작할 수 있는가 | 웹 표준과 쇼핑 흐름 시뮬레이션 |
| 선택 지표 | 6 | 후보에 오르고 선택·완료까지 가는가 | 에이전트 동작 방식과 업계 자료에서 **추론**한 권고(가중치는 실험으로 검증 전) |

선택 지표 6항목: 정적 HTML 상품 정보(JS 없이), 배송·반품 정보, 리뷰·평점, 화면·구조화 가격 일치, 팝업·배너 방해, 로그인·캡차 구매 장벽.
- **자동 수정 도구가 없는 항목은 ‘직접 조치’로 분류**합니다(배송·반품·리뷰 값은 실제 정책·데이터여야 하므로 프로그램이 만들어 넣지 않음). 에이전트는 이런 항목을 계획에 넣지 않고, 가이드라인(RAG)으로 조치 방법만 안내합니다.
- 자동 수정이 있는 것은 가격 일치(`sync_structured_price`, 고위험: 어느 쪽 가격이 맞는지 확인 필요)뿐이며, 구조화 가격이 없을 때는 `add_jsonld` 가 먼저 처리합니다.

## RAG
- **지식 베이스**: `data/guidelines/*.md` — 점검 항목마다 *이유 / 수정 방법 / 예시* 3조각, 총 50조각(`## 항목.주제: 제목` 형식으로 추가하면 자동 반영)
- **하이브리드 검색**: TF-IDF(문자 n-gram) + BM25(단어·한글 bigram) 순위를 RRF 로 결합. 오프라인 동작
- **교정형 검색**: 진단 문장(파일명 제거)을 질의로 검색 → 1순위가 해당 항목 가이드인지 채점 → 아니면 질의를 바꿔 재검색 → 그래도 아니면 *신뢰도 낮음* 표시. 화면의 **검색 근거** 탭에서 검색어·후보·유사도·재검색 여부 확인
- **계획에 근거 인용**: LLM 은 검색된 발췌를 받아 `[clickables.fix]` 처럼 근거 id 를 인용
- **임베딩(선택)**: `.env` 에 `RAG_EMBED_MODEL` 을 지정하면(OpenAI 호환 `/embeddings`, 제공자 openai) 임베딩 검색이 추가로 결합됩니다. 호출이 실패하면 자동으로 오프라인 검색만 사용
- **성능 측정**: `python eval_rag.py` → `docs/rag_eval.md` (자체 제작 48문항, Recall@1/@3). 평가셋은 직접 만든 소규모라 낙관적일 수 있음

## 프로젝트 구조
```
server.py               웹 클라이언트 서버(표준 라이브러리)
web/index.html          웹 클라이언트 화면
app.py                  Streamlit UI
run_eval.py             전/후 점수 표 생성
agentready/
  agent.py              에이전트 루프(계획·검증·승인·종료)
  checks.py             점검 10항목, 점수
  simulator.py          쇼핑 에이전트 시뮬레이터
  patcher.py            수정 액션(도구)과 위험도
  rag.py                하이브리드 검색(TF-IDF+BM25[+임베딩], RRF)
  llm.py                LLM 어댑터 + 녹화/재생
  prompts/              계획/검토/요약 프롬프트
data/
  sites/                샘플 사이트 3개
  guidelines/           RAG 가이드라인 문서
  recordings/           녹화본(jsonl)
tests/                  pytest 41개
docs/                   아키텍처, 평가 결과
```

## 범위와 한계
- 기본 구성의 LLM 은 **Mock** 입니다. 수정 계획·검토·요약은 같은 JSON 스키마를 따르는 규칙 기반 응답이며, 점검·시뮬레이션·RAG 검색·HTML 수정은 실제로 동작합니다.
- OpenAI 호환/Anthropic 어댑터는 코드가 포함되어 있으나, 실제 API 호출은 외부망에서 직접 확인해야 합니다.
- RAG 는 TF-IDF 입니다. 임베딩 모델로 바꾸려면 `rag.py` 의 벡터화/유사도 부분만 교체하면 됩니다.
- 점수 기준과 가중치는 데모용 휴리스틱이며 표준 지표가 아닙니다.
- add_jsonld 가 채우는 재고 상태는 본문 기준 추정입니다(고위험 액션으로 분류).

## Git 에 올리기
```bash
git init
git add .
git commit -m "init: agentready"
git remote add origin <저장소 URL>
git push -u origin main
```
