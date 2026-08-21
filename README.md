# Early Warning — Genie-Powered App Challenge, Track A

An attendance app for the people who run one school. Principals, counselors and
attendance clerks ask the same six questions every week and currently wait on a
district analyst to answer them. This app answers them in natural language and
then lets staff act on the answer without leaving the page.

**The gut check:** remove Genie and there is no briefing, no follow-ups, and no
evidence behind any name on the call sheet. Nothing on screen is a saved report.

```
early-warning/
├── 01_generate_attendance_data.py   run once — one school year of synthetic data
├── 02_genie_agent_setup.md          space config, instructions, example queries, tests
├── app.py                           the Streamlit app
├── app.yaml                         app runtime config (command, env, resource wiring)
├── requirements.txt

```

**Live deployment:** app at `https://early-warning-7474656426806277.aws.databricksapps.com`,
Genie space "Attendance Early Warning" (`01f19d86c98010f68fe9d25d99389dd2`), workspace
`https://dbc-296f5b92-f111.cloud.databricks.com`.

## Build order

**1. Data.** Run `01_generate_attendance_data.py` as a notebook. Change `CATALOG`
if yours is not `workspace`. It produces 400 students, 177 instructional days,
~70,000 attendance rows, and a 17% chronic absence rate — realistic for a high
school. Two signals are planted deliberately:

- 26 ninth graders whose absence rate goes from 5% to 23% after winter break
- Bus route B4, where tardies jump from 6% to 22% the week the route changed

Those are what make the demo land. The app does not know about either one; Genie
finds them when asked.

**2. Genie space.** Follow `02_genie_agent_setup.md`. The instructions block is the
important part — attendance has definitions a model cannot infer, and getting
chronic absence, tardies and instructional days wrong produces numbers that look
right. Run the ten test questions before writing any UI.

**3. App.** Compute → Apps → Create app → Custom, with two resources:

- **Genie space**, `CAN RUN`, key `genie-space`
- **SQL warehouse**, `CAN USE`, key `sql-warehouse`

Grant the app's service principal `USE CATALOG`/`USE SCHEMA` on `workspace.attendance`,
`SELECT` on all six attendance tables, and `SELECT, MODIFY` on `intervention_queue`.
Upload `app.py`, `app.yaml`, `requirements.txt` and deploy.

**On auth — read this before you copy the OBO approach.** The natural design has each
signed-in user's own Databricks identity flow through to Genie via on-behalf-of-user
OAuth (declare `genie`/`sql` in the app's `user_api_scopes`, forward
`x-forwarded-access-token`). In this workspace that consent flow never actually landed
the scopes on the forwarded token, even after the documented fix (add the scope,
restart the app, force a fresh sign-in) — Genie kept 403ing with
`Invalid scope, required scopes: genie`. The deployed app instead runs on a single
service PAT: created via `databricks tokens create`, stored as a secret (scope
`early-warning`, key `app-token`), wired in as an app **secret** resource, exposed as
env var `DATABRICKS_TOKEN`. `workspace_client()` in `app.py` pins `auth_type="pat"`
explicitly — a bare `WorkspaceClient()` errors with "more than one authorization
method configured: oauth and pat", because the Apps runtime *also* injects its own
OAuth client credentials into the same environment. If you're setting this up fresh,
try OBO first — Databricks may have fixed the consent flow since — but budget time to
fall back to the PAT approach if it 403s the same way.

## How it works

**Monday briefing.** Five curated questions fire against Genie on demand and render
as cards. Each card shows the question underneath the answer, so the provenance of
every number is visible — that is the design signature and it is worth pointing at
in your demo.

**Ask.** Free-form follow-ups on the same conversation ID, so "and just 9th grade"
resolves against the previous turn.

**Call sheet.** Any answer containing a student id and a name gets an inline picker.
Staff select students, attach a reason, and the app appends to a Delta table along
with the question that surfaced them. Open items can be checked off as addressed —
that flips a status column rather than deleting the row, so the record survives — and
addressed students collapse into their own expander, out of the way. Downloads as CSV.

That last mode is the part most Track A entries will not have. The rubric asks how
effectively the app "moves a user from a question to a data-backed answer inside the
app" — a call sheet where every row carries the question that produced it is a
literal answer to that.

## Implementation notes

- `ask_genie` handles both text and query attachments, and falls back across two
  SDK method names for fetching results, since versions differ.
- Trend answers are auto-charted when a column name contains week or month and the
  rest are numeric. Everything else renders as a table.
- `find_student_columns` locates the id and name columns in whatever shape Genie
  returns, which is what makes the call sheet work on arbitrary answers rather than
  a fixed query. It specifically needs a `student_id`-shaped column, not just a name
  — the Genie space instructions say to always return `student_id` alongside
  `full_name` for exactly this reason. Without that line, Genie's SQL often selects
  only `full_name` and the call-sheet picker silently doesn't appear.
- The app writes to `intervention_queue` through the SQL warehouse. That table is
  deliberately not in the Genie space — it is app state, not something a principal
  asks questions about.
- The data generator (`01_generate_attendance_data.py`) is a Databricks notebook, not
  a plain script — its `# MAGIC %md` section headers need a `# COMMAND ----------`
  line before the code that follows, or Databricks folds that code into the markdown
  cell and it silently never runs (the job still reports `SUCCESS`, since nothing
  threw). Check the exported notebook after any edit if tables come up empty.



## Demo script, three to four minutes

Open cold. Run the briefing — five cards fill in, one by one. Point out that each
card names the question that produced it. Switch to Ask, type "whose attendance
dropped after winter break", get the cohort. Follow up with "and are any of them
already in an intervention" to show conversation memory. Add four of them to the
call sheet with a reason. Open the call sheet tab, download the CSV. Close on the
guardrail: ask which demographic group has the worst attendance and show Genie
returning the aggregate without naming students.
