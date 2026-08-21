# Databricks notebook source
# MAGIC %md
# MAGIC # Early Warning — attendance data generator
# MAGIC
# MAGIC Run once in a Free Edition notebook. Creates `school.attendance` with six
# MAGIC tables covering one academic year at a single fictional school.
# MAGIC
# MAGIC All students, names and records are synthetic. Two real signals are planted
# MAGIC so the app has something true to find:
# MAGIC
# MAGIC 1. **Post-break cliff** — a group of 9th graders whose attendance never
# MAGIC    recovers after winter break.
# MAGIC 2. **Bus route B4** — a tardy spike starting the week the route changed.

# COMMAND ----------

CATALOG = "workspace"        # change if your Free Edition catalog differs
SCHEMA = "attendance"
SEED = 7

# COMMAND ----------

import numpy as np
import pandas as pd
from datetime import date, timedelta

rng = np.random.default_rng(SEED)

try:
    spark
except NameError:
    from databricks.connect import DatabricksSession
    spark = DatabricksSession.builder.getOrCreate()

spark.sql(f"CREATE CATALOG IF NOT EXISTS {CATALOG}")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")

# COMMAND ----------
# MAGIC %md ## School calendar

# COMMAND ----------

YEAR_START = date(2025, 8, 18)
YEAR_END = date(2026, 5, 29)

closures = {
    **{date(2025, 9, 1): "Labor Day"},
    **{date(2025, 11, 24) + timedelta(d): "Thanksgiving break" for d in range(5)},
    **{date(2025, 12, 22) + timedelta(d): "Winter break"
       for d in range(21) if (date(2025, 12, 22) + timedelta(d)).weekday() < 5},
    **{date(2026, 1, 19): "Staff development day"},
    **{date(2026, 3, 16) + timedelta(d): "Spring break" for d in range(5)},
    **{date(2026, 2, 3): "Weather closure"},
}
testing_days = {date(2026, 4, 14), date(2026, 4, 15), date(2026, 4, 16)}

cal_rows = []
d = YEAR_START
while d <= YEAR_END:
    if d.weekday() < 5:
        closed = d in closures
        cal_rows.append({
            "calendar_date": d,
            "is_instructional_day": not closed,
            "day_of_week": d.strftime("%A"),
            "term": 1 if d < date(2025, 11, 1) else (2 if d < date(2026, 2, 15) else 3),
            "day_type": ("state testing" if d in testing_days
                         else closures.get(d, "regular instruction")),
        })
    d += timedelta(days=1)

school_calendar = pd.DataFrame(cal_rows)
instructional = school_calendar.loc[school_calendar.is_instructional_day, "calendar_date"].tolist()
print(len(instructional), "instructional days")

# COMMAND ----------
# MAGIC %md ## Students

# COMMAND ----------

FIRST = ["Amara","Noah","Sofia","Liam","Priya","Diego","Aisha","Mateo","Zoe","Ibrahim",
         "Elena","Kai","Nadia","Owen","Leila","Marcus","Ruth","Tomas","Hana","Julian",
         "Isabel","Andre","Mei","Samuel","Farida","Lucas","Naomi","Ezra","Camila","Yusuf"]
LAST = ["Okafor","Ramirez","Chen","Novak","Patel","Silva","Hassan","Torres","Lindqvist",
        "Ali","Moreau","Nguyen","Bekele","Walsh","Kowalski","Reyes","Adeyemi","Weber",
        "Sato","Ferreira","Vargas","Osei","Kim","Brennan","Haddad","Costa","Mbeki"]
ROUTES = ["B1","B2","B3","B4","B5","walker","family drive"]

N = 400
students = pd.DataFrame({
    "student_id": ["S%04d" % i for i in range(1, N + 1)],
    "first_name": rng.choice(FIRST, N),
    "last_name": rng.choice(LAST, N),
    "grade_level": rng.choice([9, 10, 11, 12], N, p=[0.28, 0.26, 0.24, 0.22]),
    "enrollment_date": [YEAR_START] * N,
    "home_language": rng.choice(["English","Spanish","Arabic","Vietnamese","Amharic"],
                                N, p=[0.62, 0.22, 0.07, 0.05, 0.04]),
    "transport_mode": rng.choice(ROUTES, N, p=[0.14,0.14,0.13,0.15,0.12,0.18,0.14]),
    "receives_free_lunch": rng.random(N) < 0.41,
    "is_english_learner": rng.random(N) < 0.16,
})
students["full_name"] = students.first_name + " " + students.last_name

# 18 students enrol late so rate maths must respect enrollment_date
late = rng.choice(N, 18, replace=False)
students.loc[late, "enrollment_date"] = [
    instructional[rng.integers(15, 60)] for _ in late
]

# per-student baseline propensities
students["_present_p"] = np.clip(rng.beta(44, 2.0, N), 0.62, 0.998)
students["_tardy_p"] = np.clip(rng.beta(1.6, 26, N), 0.0, 0.30)
students["_excused_share"] = rng.uniform(0.35, 0.85, N)

# planted signal 1: post-break cliff for a 9th grade cohort
g9 = students.index[students.grade_level == 9].to_numpy()
cliff = rng.choice(g9, 26, replace=False)
students["_cliff"] = False
students.loc[cliff, "_cliff"] = True

# planted signal 2: bus route B4 tardies after the route change
students["_b4"] = students.transport_mode == "B4"

ROUTE_CHANGE = date(2026, 1, 12)
BREAK_RETURN = date(2026, 1, 5)
FLU_START, FLU_END = date(2026, 2, 9), date(2026, 2, 20)

# COMMAND ----------
# MAGIC %md ## Daily attendance

# COMMAND ----------

recs = []
for i, s in students.iterrows():
    p_base, t_base = s._present_p, s._tardy_p
    for d in instructional:
        if d < s.enrollment_date:
            continue
        p, t = p_base, t_base
        if s._cliff and d >= BREAK_RETURN:
            p -= 0.16
        if s._b4 and d >= ROUTE_CHANGE:
            t = 0.24
        if FLU_START <= d <= FLU_END:
            p -= 0.07
        if d.weekday() == 4:            # Fridays run lighter everywhere
            p -= 0.030
        if d.weekday() == 0:
            p -= 0.02
        p = float(np.clip(p, 0.3, 0.999))

        if rng.random() < p:
            status = "tardy" if rng.random() < t else "present"
        else:
            status = "absent excused" if rng.random() < s._excused_share else "absent unexcused"

        recs.append((s.student_id, d, status))

attendance_daily = pd.DataFrame(recs, columns=["student_id", "attendance_date", "status"])
attendance_daily.insert(0, "record_id", ["A%06d" % i for i in range(1, len(attendance_daily) + 1)])
attendance_daily["is_absence"] = attendance_daily.status.str.startswith("absent")
attendance_daily["minutes_missed"] = np.where(
    attendance_daily.status == "tardy", rng.integers(5, 45, len(attendance_daily)),
    np.where(attendance_daily.is_absence, 420, 0))
print(len(attendance_daily), "attendance rows")

# COMMAND ----------
# MAGIC %md ## Contact log — with a deliberate coverage gap

# COMMAND ----------

absence_counts = (attendance_daily[attendance_daily.is_absence]
                  .groupby("student_id").size().rename("absences"))
chronic = absence_counts[absence_counts > 0.10 * len(instructional)].index.tolist()
print(len(chronic), "chronically absent students")

# only about half of them have any logged outreach — the gap the app surfaces
contacted = list(rng.choice(chronic, int(len(chronic) * 0.55), replace=False))

METHODS = ["phone call", "text message", "home visit", "letter mailed", "parent meeting"]
OUTCOMES = ["reached parent", "left voicemail", "no answer", "parent scheduled meeting",
            "wrong number on file"]
STAFF = ["A. Whitfield", "R. Delgado", "M. Osei", "K. Lindqvist"]

clog = []
for sid in contacted:
    for _ in range(int(rng.integers(1, 5))):
        d = instructional[int(rng.integers(20, len(instructional)))]
        clog.append({
            "contact_id": None,
            "student_id": sid,
            "contact_date": d,
            "staff_member": rng.choice(STAFF),
            "method": rng.choice(METHODS),
            "outcome": rng.choice(OUTCOMES),
        })
contact_log = pd.DataFrame(clog)
contact_log["contact_id"] = ["C%04d" % i for i in range(1, len(contact_log) + 1)]

# COMMAND ----------
# MAGIC %md ## Interventions and term grades

# COMMAND ----------

TYPES = ["attendance contract", "mentor check-in", "transport support",
         "counseling referral", "credit recovery"]
iv_students = list(rng.choice(chronic, min(24, len(chronic)), replace=False))
interventions = pd.DataFrame([{
    "intervention_id": "I%03d" % (i + 1),
    "student_id": sid,
    "intervention_type": rng.choice(TYPES),
    "start_date": instructional[int(rng.integers(30, 120))],
    "status": rng.choice(["active", "completed", "lapsed"], p=[0.6, 0.2, 0.2]),
    "owner": rng.choice(STAFF),
} for i, sid in enumerate(iv_students)])

att_rate = (1 - absence_counts.reindex(students.student_id).fillna(0)
            / len(instructional))
grades = []
for term in (1, 2, 3):
    for sid, rate in att_rate.items():
        gpa = float(np.clip(1.1 + 2.9 * rate + rng.normal(0, 0.32), 0.0, 4.0))
        grades.append({"student_id": sid, "term": term, "term_gpa": round(gpa, 2),
                       "courses_failing": int(max(0, rng.poisson(max(0, 2.6 - 2.6 * rate))))})
grades_term = pd.DataFrame(grades)

# COMMAND ----------
# MAGIC %md ## Write tables

# COMMAND ----------

students_out = students.drop(columns=[c for c in students.columns if c.startswith("_")])

tables = {
    "students": students_out,
    "attendance_daily": attendance_daily,
    "school_calendar": school_calendar,
    "contact_log": contact_log,
    "interventions": interventions,
    "grades_term": grades_term,
}
for name, pdf in tables.items():
    full = f"{CATALOG}.{SCHEMA}.{name}"
    spark.createDataFrame(pdf).write.mode("overwrite") \
        .option("overwriteSchema", "true").saveAsTable(full)
    print("wrote", full, len(pdf), "rows")

# an empty table the app appends to when staff add a student to the call sheet
spark.sql(f"""
CREATE TABLE IF NOT EXISTS {CATALOG}.{SCHEMA}.intervention_queue (
  queue_id STRING, student_id STRING, student_name STRING, reason STRING,
  source_question STRING, added_by STRING, added_at TIMESTAMP, status STRING
)""")

# COMMAND ----------
# MAGIC %md ## Comments — Genie depends on these

# COMMAND ----------

table_comments = {
    "students": "One row per enrolled student. enrollment_date matters: students who enrolled late have fewer possible instructional days and their attendance rate must be calculated from their enrollment date onward.",
    "attendance_daily": "One row per student per instructional day. status is one of present, tardy, absent excused, absent unexcused. Tardy counts as present for attendance rate.",
    "school_calendar": "Every weekday in the academic year. Only rows with is_instructional_day = true count toward attendance rates. Holidays, breaks and closures are false.",
    "contact_log": "Outreach from staff to families about attendance. A student with no rows here has never been contacted.",
    "interventions": "Formal support plans. status is active, completed or lapsed.",
    "grades_term": "Term GPA and failing course count per student per term.",
    "intervention_queue": "Students added to the call sheet from the app, with the question that surfaced them.",
}
column_comments = {
    "students": {
        "grade_level": "9 through 12.",
        "enrollment_date": "First day this student was enrolled. Use as the start of their attendance denominator.",
        "transport_mode": "Bus route identifier such as B1 to B5, or walker, or family drive.",
    },
    "attendance_daily": {
        "status": "present, tardy, absent excused, or absent unexcused. Both absence types count toward chronic absence.",
        "is_absence": "True for absent excused and absent unexcused. False for present and tardy.",
        "minutes_missed": "Instructional minutes missed. A full day is 420.",
    },
    "school_calendar": {
        "is_instructional_day": "True only for days school was in session. Never divide by a plain date range; join to this table instead.",
        "term": "1, 2 or 3.",
        "day_type": "regular instruction, state testing, or the name of the holiday or closure.",
    },
    "contact_log": {"outcome": "What happened on the contact attempt."},
    "grades_term": {"courses_failing": "Number of courses the student is failing that term."},
}

for name, c in table_comments.items():
    spark.sql(f"COMMENT ON TABLE {CATALOG}.{SCHEMA}.{name} IS '{c}'")
for name, cols in column_comments.items():
    for col, c in cols.items():
        spark.sql(f"ALTER TABLE {CATALOG}.{SCHEMA}.{name} ALTER COLUMN {col} COMMENT '{c}'")

print("done. chronic absence rate:", round(100 * len(chronic) / N, 1), "%")
