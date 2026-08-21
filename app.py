"""Early Warning — a Genie-powered attendance app for school staff.

Three modes on one Genie conversation:
  Briefing — five curated questions answered on demand, as cards
  Ask      — free-form follow-ups that inherit the briefing's context
  Act      — students pulled from any answer onto a call sheet, written to Delta

The app holds no attendance logic. Every number on screen came from a Genie turn,
and every card shows the question that produced it.
"""

import os
import uuid
from datetime import datetime

import pandas as pd
import streamlit as st
from databricks.sdk import WorkspaceClient

GENIE_SPACE_ID = os.getenv("GENIE_SPACE_ID")
WAREHOUSE_ID = os.getenv("DATABRICKS_WAREHOUSE_ID")
QUEUE_TABLE = os.getenv("QUEUE_TABLE", "workspace.attendance.intervention_queue")

st.set_page_config(page_title="Early Warning", page_icon="◔", layout="wide")

# ------------------------------------------------------------------ appearance

st.markdown(
    """
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400;12..96,600&family=Public+Sans:wght@400;500&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {
  --page:#F7F8FA; --card:#FFFFFF; --ink:#1B2230; --muted:#63708A; --line:#E1E6EF;
  --accent:#3D4C8A; --warn:#B3701E; --ok:#1D7A63; --hot:#A63D3D;
}
.stApp { background: var(--page); color: var(--ink); }
html, body, [class*="css"] { font-family:'Public Sans', sans-serif; color: var(--ink); }
h1,h2,h3 { font-family:'Bricolage Grotesque', sans-serif !important; font-weight:600 !important;
  letter-spacing:-0.02em; color:var(--ink); }
.masthead { display:flex; justify-content:space-between; align-items:baseline;
  border-bottom:2px solid var(--ink); padding-bottom:10px; margin-bottom:6px; }
.masthead .name { font-family:'Bricolage Grotesque',sans-serif; font-size:26px; font-weight:600; }
.masthead .meta { font-family:'JetBrains Mono',monospace; font-size:11px; color:var(--muted);
  letter-spacing:0.08em; text-transform:uppercase; }
.card { background:var(--card); border:1px solid var(--line); border-left:4px solid var(--accent);
  border-radius:0 6px 6px 0; padding:16px 18px; margin-bottom:14px; }
.card.warn { border-left-color:var(--warn); }
.card.hot  { border-left-color:var(--hot); }
.card.ok   { border-left-color:var(--ok); }
.card .head { font-family:'Bricolage Grotesque',sans-serif; font-size:16px; font-weight:600;
  margin-bottom:6px; }
.card .body { font-size:15px; line-height:1.6; }
.prov { font-family:'JetBrains Mono',monospace; font-size:11px; color:var(--muted);
  margin-top:10px; padding-top:8px; border-top:1px dashed var(--line); }
.turn { background:var(--card); border:1px solid var(--line); border-radius:6px;
  padding:14px 16px; margin-bottom:12px; }
.turn .q { font-family:'JetBrains Mono',monospace; font-size:12px; color:var(--accent);
  margin-bottom:8px; }
.empty { color:var(--muted); font-size:14px; line-height:1.6; }
.stButton button { font-family:'Public Sans',sans-serif; font-weight:500; }
</style>
""",
    unsafe_allow_html=True,
)

# ------------------------------------------------------------------ content

BRIEFING = [
    {
        "head": "Who crossed the chronic absence line",
        "q": "Which students are chronically absent this year? Show name, grade and absence rate.",
        "tone": "hot",
    },
    {
        "head": "Where attendance is heading",
        "q": "Show the absence rate by week for the whole school this year, in date order.",
        "tone": "warn",
    },
    {
        "head": "Absences with no outreach behind them",
        "q": "Which students have three or more unexcused absences in the last two weeks and no contact logged in the contact log?",
        "tone": "hot",
    },
    {
        "head": "Which grade needs attention",
        "q": "What is the absence rate by grade level this year?",
        "tone": "warn",
    },
    {
        "head": "The weekly pattern",
        "q": "What is the absence rate by day of week across the year?",
        "tone": "ok",
    },
]

SUGGESTED = [
    "Whose attendance dropped sharply after winter break?",
    "Has anything changed for students on a particular bus route?",
    "Which chronically absent students already have an active intervention?",
    "How does term GPA compare between students above and below 90% attendance?",
]

REASONS = [
    "Chronic absence, needs a call home",
    "No outreach logged yet",
    "Sharp recent drop",
    "Repeated unexcused absences",
    "Transport problem suspected",
    "Refer to counselor",
]

# ------------------------------------------------------------------ databricks

@st.cache_resource
def workspace_client():
    # On-behalf-of-user auth via x-forwarded-access-token requires OAuth scopes
    # (genie, sql) that need per-user re-consent and were unreliable to grant in
    # this workspace. The app instead runs as a single service identity backed
    # by DATABRICKS_TOKEN (a PAT stored as an app secret resource). The Apps
    # runtime also injects its own OAuth client credentials into the same
    # environment, so auth_type must be pinned explicitly or the SDK refuses to
    # pick between the two.
    token = os.getenv("DATABRICKS_TOKEN")
    if token:
        return WorkspaceClient(token=token, auth_type="pat")
    return WorkspaceClient()


def _attachment_id(att):
    return getattr(att, "attachment_id", None) or getattr(att, "id", None)


def _to_frame(result):
    resp = getattr(result, "statement_response", result)
    manifest, data = getattr(resp, "manifest", None), getattr(resp, "result", None)
    if manifest is None or data is None:
        return None
    cols = [c.name for c in manifest.schema.columns]
    return pd.DataFrame(getattr(data, "data_array", None) or [], columns=cols)


def ask_genie(question: str) -> dict:
    """One Genie turn on the shared conversation. Returns text, SQL and rows."""
    w = workspace_client()
    conv_id = st.session_state.get("conversation_id")
    try:
        if conv_id is None:
            msg = w.genie.start_conversation_and_wait(GENIE_SPACE_ID, question)
            st.session_state.conversation_id = msg.conversation_id
            conv_id = msg.conversation_id
        else:
            msg = w.genie.create_message_and_wait(GENIE_SPACE_ID, conv_id, question)
    except Exception as exc:
        return {"text": None, "sql": None, "frame": None,
                "error": f"Genie could not answer that. {exc}"}

    out = {"text": None, "sql": None, "frame": None, "error": None}
    for att in getattr(msg, "attachments", None) or []:
        if getattr(att, "text", None) is not None:
            out["text"] = att.text.content
        if getattr(att, "query", None) is not None:
            out["sql"] = att.query.query
            if not out["text"]:
                out["text"] = getattr(att.query, "description", None)
            try:
                res = w.genie.get_message_query_result_by_attachment(
                    GENIE_SPACE_ID, conv_id, msg.id, _attachment_id(att))
            except Exception:
                res = w.genie.get_message_query_result(GENIE_SPACE_ID, conv_id, msg.id)
            out["frame"] = _to_frame(res)

    if out["text"] is None and out["frame"] is None:
        out["error"] = "No answer came back. Try naming the grade or the date range."
    return out


def add_to_queue(rows: list, reason: str, source_question: str) -> int:
    """Append students to the call sheet table."""
    w = workspace_client()
    user = "app user"
    try:
        user = st.context.headers.get("x-forwarded-email") or user
    except Exception:
        pass
    now = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    values = ", ".join(
        "('{}', '{}', '{}', '{}', '{}', '{}', TIMESTAMP'{}', 'open')".format(
            uuid.uuid4().hex[:12], _esc(sid), _esc(name), _esc(reason),
            _esc(source_question[:300]), _esc(user), now)
        for sid, name in rows
    )
    w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=f"INSERT INTO {QUEUE_TABLE} VALUES {values}",
        wait_timeout="30s",
    )
    return len(rows)


def load_queue() -> pd.DataFrame:
    w = workspace_client()
    stmt = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=(f"SELECT queue_id, student_name, reason, source_question, added_at, status "
                   f"FROM {QUEUE_TABLE} ORDER BY added_at DESC LIMIT 200"),
        wait_timeout="30s",
    )
    return _to_frame(stmt) if stmt.result else pd.DataFrame()


def mark_addressed(queue_ids: list) -> int:
    """Flip selected call-sheet rows to status = addressed. Kept, not deleted."""
    if not queue_ids:
        return 0
    w = workspace_client()
    ids = ", ".join(f"'{_esc(q)}'" for q in queue_ids)
    w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=f"UPDATE {QUEUE_TABLE} SET status = 'addressed' WHERE queue_id IN ({ids})",
        wait_timeout="30s",
    )
    return len(queue_ids)


def _esc(v) -> str:
    return str(v).replace("'", "''")


def find_student_columns(frame: pd.DataFrame):
    """Locate the id and name columns in whatever shape Genie returned."""
    if frame is None or frame.empty:
        return None, None
    lower = {c.lower(): c for c in frame.columns}
    id_col = next((lower[c] for c in lower if "student_id" in c), None)
    name_col = next((lower[c] for c in lower
                     if "full_name" in c or c in ("name", "student_name")), None)
    return id_col, name_col


# ------------------------------------------------------------------ state

for k, v in {"conversation_id": None, "briefing": {}, "turns": [],
             "flash": None, "input_error": None}.items():
    st.session_state.setdefault(k, v)

st.markdown(
    """<div class="masthead">
      <div class="name">Early Warning</div>
      <div class="meta">Meridian High · attendance · 2025–26</div>
    </div>""",
    unsafe_allow_html=True,
)

if not GENIE_SPACE_ID:
    st.error("GENIE_SPACE_ID is not set. Add the Genie space as an app resource, then redeploy.")
    st.stop()

if st.session_state.flash:
    st.success(st.session_state.flash)
    st.session_state.flash = None

briefing_tab, ask_tab, act_tab = st.tabs(["Monday briefing", "Ask", "Call sheet"])

# ------------------------------------------------------------------ briefing


def render_answer(res: dict, question: str, key: str):
    if res.get("error"):
        st.markdown(f"<div class='body' style='color:#A63D3D'>{res['error']}</div>",
                    unsafe_allow_html=True)
        return
    if res.get("text"):
        st.markdown(f"<div class='body'>{res['text']}</div>", unsafe_allow_html=True)

    frame = res.get("frame")
    if frame is not None and not frame.empty:
        numeric = [c for c in frame.columns
                   if pd.to_numeric(frame[c], errors="coerce").notna().all()]
        looks_like_trend = any("week" in c.lower() or "month" in c.lower()
                               for c in frame.columns)
        if looks_like_trend and numeric:
            idx = [c for c in frame.columns if c not in numeric][:1] or [frame.columns[0]]
            plot = frame.copy()
            for c in numeric:
                plot[c] = pd.to_numeric(plot[c], errors="coerce")
            st.line_chart(plot.set_index(idx[0])[numeric])
        st.dataframe(frame, use_container_width=True, hide_index=True,
                     height=min(340, 44 + 35 * len(frame)))

        id_col, name_col = find_student_columns(frame)
        if id_col and name_col:
            with st.expander(f"Add students from this answer to the call sheet"):
                picks = st.multiselect("Students", options=list(frame[name_col]),
                                       key=f"pick_{key}")
                reason = st.selectbox("Reason", REASONS, key=f"reason_{key}")
                if st.button("Add to call sheet", key=f"add_{key}"):
                    if not picks:
                        st.error("Choose at least one student first.")
                    else:
                        sel = frame[frame[name_col].isin(picks)][[id_col, name_col]]
                        n = add_to_queue(list(sel.itertuples(index=False, name=None)),
                                         reason, question)
                        st.session_state.flash = f"Added {n} students to the call sheet."
                        st.rerun()

    st.markdown(f"<div class='prov'>Genie answered: {question}</div>", unsafe_allow_html=True)
    if res.get("sql"):
        with st.expander("Query Genie ran"):
            st.code(res["sql"], language="sql")


with briefing_tab:
    top = st.columns([3, 1])
    with top[0]:
        st.markdown("<div class='empty'>Five questions this school asks every week. "
                    "Each card is a live Genie answer, not a saved report.</div>",
                    unsafe_allow_html=True)
    with top[1]:
        if st.button("Run the briefing", type="primary", use_container_width=True):
            st.session_state.briefing = {}
            for item in BRIEFING:
                with st.spinner(item["head"].lower() + "…"):
                    st.session_state.briefing[item["head"]] = ask_genie(item["q"])
            st.rerun()

    if not st.session_state.briefing:
        st.markdown("<div class='empty'>Nothing has been pulled yet. "
                    "Run the briefing to see this week's picture.</div>",
                    unsafe_allow_html=True)

    for i, item in enumerate(BRIEFING):
        res = st.session_state.briefing.get(item["head"])
        if not res:
            continue
        st.markdown(f"<div class='card {item['tone']}'><div class='head'>{item['head']}</div>",
                    unsafe_allow_html=True)
        render_answer(res, item["q"], f"b{i}")
        st.markdown("</div>", unsafe_allow_html=True)

# ------------------------------------------------------------------ ask

with ask_tab:
    st.markdown("<div class='empty'>Ask anything the briefing did not cover. "
                "Follow-ups keep the context, so \"and just 9th grade\" works.</div>",
                unsafe_allow_html=True)

    cols = st.columns(2)
    for i, s in enumerate(SUGGESTED):
        if cols[i % 2].button(s, key=f"sug{i}", use_container_width=True):
            st.session_state.turns.insert(0, {"q": s, **ask_genie(s)})
            st.rerun()

    with st.form("ask_form", clear_on_submit=True):
        q = st.text_input("Question", label_visibility="collapsed",
                          placeholder="Which 10th graders missed more than five days this month?")
        submitted = st.form_submit_button("Ask", use_container_width=True)
    if submitted:
        if not q.strip():
            st.error("Type a question first.")
        else:
            with st.spinner("Checking the records…"):
                st.session_state.turns.insert(0, {"q": q.strip(), **ask_genie(q.strip())})
            st.rerun()

    for i, turn in enumerate(st.session_state.turns):
        st.markdown(f"<div class='turn'><div class='q'>{turn['q']}</div>",
                    unsafe_allow_html=True)
        render_answer(turn, turn["q"], f"t{i}")
        st.markdown("</div>", unsafe_allow_html=True)

# ------------------------------------------------------------------ act

with act_tab:
    st.markdown("<div class='empty'>Students staff added from an answer, with the "
                "question that surfaced them. This is what goes home tonight.</div>",
                unsafe_allow_html=True)
    if st.button("Refresh"):
        st.rerun()
    try:
        queue = load_queue()
    except Exception as exc:
        st.error(f"Could not read the call sheet table. {exc}")
        queue = pd.DataFrame()

    if queue is None or queue.empty:
        st.markdown("<div class='empty'>The call sheet is empty. Add students from "
                    "any briefing card or answer.</div>", unsafe_allow_html=True)
    else:
        open_rows = queue[queue["status"] == "open"].copy()
        addressed_rows = queue[queue["status"] != "open"]

        if open_rows.empty:
            st.markdown("<div class='empty'>Nothing open — everything on the call sheet "
                        "has been addressed.</div>", unsafe_allow_html=True)
        else:
            st.markdown(f"**{len(open_rows)} open**")
            open_rows.insert(0, "Addressed", False)
            edited = st.data_editor(
                open_rows.drop(columns=["queue_id", "status"]),
                hide_index=True, use_container_width=True, key="queue_editor",
                disabled=["student_name", "reason", "source_question", "added_at"],
            )
            checked = open_rows.loc[edited["Addressed"], "queue_id"].tolist()
            if st.button("Mark checked as addressed", disabled=not checked):
                n = mark_addressed(checked)
                st.session_state.flash = f"Marked {n} students as addressed."
                st.rerun()

        if not addressed_rows.empty:
            with st.expander(f"Addressed ({len(addressed_rows)})"):
                st.dataframe(addressed_rows.drop(columns=["queue_id"]),
                             use_container_width=True, hide_index=True)

        st.download_button("Download the call sheet",
                           queue.drop(columns=["queue_id"]).to_csv(index=False).encode(),
                           file_name="call_sheet.csv", mime="text/csv")
