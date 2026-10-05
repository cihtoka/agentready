"""AgentReady - Streamlit 데모 UI.  실행: streamlit run app.py"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from agentready import config
from agentready.agent import Agent
from agentready.checks import analyze
from agentready.llm import get_llm
from agentready.rag import Guidelines
from agentready.simulator import simulate
from agentready.site import Site

ICONS = {"observe": "🔍", "think": "🧠", "tool": "🛠️", "decide": "⚖️", "approval": "🙋", "error": "⚠️", "done": "✅"}
STATUS = {"ok": "✅ 성공", "warn": "⚠️ 주의", "fail": "❌ 실패", "skipped": "⏭️ 건너뜀", "na": "➖ 해당 없음"}

st.set_page_config(page_title="AgentReady", page_icon="🛒", layout="wide")


@st.cache_resource
def load_guidelines() -> Guidelines:
    return Guidelines(config.GUIDE_DIR)


def show_html(html: str, height: int = 420) -> None:
    """번들 샘플 HTML 미리보기 (Streamlit 버전에 따라 iframe API 선택)."""
    if hasattr(st, "iframe"):
        st.iframe(html, height=height)
    else:  # 구버전 호환
        import streamlit.components.v1 as components

        components.html(html, height=height, scrolling=True)


def sample_sites() -> dict:
    return {d.name: d for d in sorted(config.SITES_DIR.iterdir()) if d.is_dir()}


st.title("🛒 AgentReady")
st.caption("AI 쇼핑 에이전트가 쓰기 좋은 사이트인지 진단하고, 스스로 판단해 고치는 에이전트형 RAG 데모")

# ---------------------------------------------------------------- 사이드바
samples = sample_sites()
with st.sidebar:
    st.header("설정")
    source = st.radio("분석 대상", ["샘플 사이트", "HTML 파일 업로드"])
    site = None
    if source == "샘플 사이트":
        choice = st.selectbox("샘플 선택", list(samples), format_func=lambda k: Site.from_dir(samples[k]).display)
        site = Site.from_dir(samples[choice])
    else:
        up = st.file_uploader("저장한 HTML 파일(.html)", type=["html", "htm"])
        if up is not None:
            site = Site.from_html(up.name, up.getvalue().decode("utf-8", errors="replace"))
    max_iter = st.slider("최대 반복 횟수", 1, 6, config.settings().max_iterations)
    max_actions = st.slider("반복당 최대 수정 수", 1, 5, config.settings().max_actions)
    auto = st.checkbox("고위험 수정 자동 승인", value=True,
                       help="끄면 DOM 구조 변경, 재고 추정, 크롤링 정책 공개 같은 수정은 '승인 대기'로 남깁니다.")
    llm = get_llm()
    st.info(f"LLM 제공자: **{llm.name}** ({llm.model or '-'})")
    run = st.button("에이전트 실행", type="primary", disabled=site is None)

if site is None:
    st.info("왼쪽에서 분석할 사이트를 선택하세요.")
    st.stop()

# ---------------------------------------------------------------- 실행
if run:
    events = []
    box = st.status("에이전트 실행 중...", expanded=True)

    def on_event(ev):
        events.append(ev)
        box.write(f"{ICONS.get(ev.kind, '•')} **{ev.title}**")

    agent = Agent(llm, load_guidelines(), max_iterations=max_iter, max_actions=max_actions,
                  auto_approve=auto, on_event=on_event)
    st.session_state["result"] = agent.run(site)
    box.update(label="완료", state="complete", expanded=False)

result = st.session_state.get("result")
if result is None or result.site != site.display:
    pre = analyze(site)
    pre_sim = simulate(site)
    c1, c2 = st.columns(2)
    c1.metric("현재 AI 에이전트 친화도", f"{pre.total}점")
    c2.metric("쇼핑 시뮬레이션 완료율", f"{pre_sim.completion:.0%}")
    st.caption("'에이전트 실행'을 누르면 진단 → 계획 → 수정 → 재진단 루프가 시작됩니다.")
    st.stop()

# ---------------------------------------------------------------- 결과
st.subheader(result.site)
m1, m2, m3, m4 = st.columns(4)
m1.metric("구조 점수 / 선택 지표", f"{result.after.total} / {result.after.ext_total}", f"{result.after.total - result.before.total:+.1f} / {result.after.ext_total - result.before.ext_total:+.1f}")
m2.metric("시뮬레이션 완료율", f"{result.sim_after.completion:.0%}",
          f"{(result.sim_after.completion - result.sim_before.completion) * 100:+.0f}%p")
m3.metric("반복 횟수", len(result.iterations))
m4.metric("승인 대기", len(result.pending))
st.write(result.summary)

tab_score, tab_sim, tab_trace, tab_diff, tab_rag, tab_prev = st.tabs(
    ["점수", "쇼핑 시뮬레이션", "에이전트 트레이스", "수정 내역(diff)", "가이드라인 근거(RAG)", "미리보기"]
)

with tab_score:
    b, a = result.before.by_id(), result.after.by_id()
    df = pd.DataFrame(
        [{"항목": b[k].name, "가중치": b[k].weight, "개선 전": round(b[k].score * b[k].weight, 1),
          "개선 후": round(a[k].score * a[k].weight, 1), "진단(개선 전)": b[k].detail} for k in b]
    )
    st.bar_chart(df.set_index("항목")[["개선 전", "개선 후"]])
    st.dataframe(df, hide_index=True)

with tab_sim:
    cols = st.columns(2)
    for col, title, sim in ((cols[0], "개선 전", result.sim_before), (cols[1], "개선 후", result.sim_after)):
        with col:
            st.markdown(f"**{title}** · 완료율 {sim.completion:.0%}")
            st.dataframe(
                pd.DataFrame([{"단계": s.name, "결과": STATUS[s.status], "설명": s.message} for s in sim.steps]),
                hide_index=True,
            )

with tab_trace:
    for ev in result.trace:
        with st.expander(f"{ICONS.get(ev.kind, '•')} {ev.title}", expanded=ev.kind in ("decide", "done")):
            st.text(ev.detail or "-")
    if result.pending:
        st.warning("승인 대기 중인 고위험 수정: " + ", ".join(p["action"] for p in result.pending)
                   + " — 사이드바에서 '고위험 수정 자동 승인'을 켜고 다시 실행하면 적용됩니다.")

with tab_diff:
    if not result.diffs:
        st.write("변경 사항이 없습니다.")
    for name, d in result.diffs.items():
        with st.expander(name):
            st.code(d or "(변경 없음)", language="diff")

with tab_rag:
    for cid, ev in result.evidence.items():
        top = ev["hits"][0] if ev["hits"] else None
        label = f"{cid} → [{top['id']}] (유사도 {top['score']})" if top else f"{cid} → 검색 결과 없음"
        with st.expander(label + (" · 재검색" if ev["retries"] else "")):
            st.caption("검색어: " + " → ".join(ev["queries"]))
            for h in ev["hits"]:
                st.markdown(f"**[{h['id']}] {h['title']}** · 유사도 {h['score']}")
                st.write(h["text"])

with tab_prev:
    if source != "샘플 사이트":
        st.info("업로드한 HTML 은 보안상 앱 안에서 실행하지 않습니다. 변경 내용은 'diff' 탭에서 확인하세요.")
    else:
        page = st.selectbox("페이지", list(result.patched.pages))
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**개선 전**")
            show_html(site.pages.get(page, ""))
        with c2:
            st.markdown("**개선 후**")
            show_html(result.patched.pages[page])
