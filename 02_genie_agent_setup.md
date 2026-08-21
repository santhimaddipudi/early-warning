# Genie agent setup

Attendance is a domain where text-to-SQL fails quietly unless you pin the
definitions. A model that divides absences by a date range instead of
instructional days will return numbers that look plausible and are wrong. Almost
all of your 20 "Genie at the core" points come from getting this section right.

## 1. Create the space

Genie → **New** → name it `Attendance Early Warning`, pick your serverless SQL
warehouse, and add:

```
workspace.attendance.students
workspace.attendance.attendance_daily
workspace.attendance.school_calendar
workspace.attendance.contact_log
workspace.attendance.interventions
workspace.attendance.grades_term
```

Leave `intervention_queue` out — the app writes to it, and it is not something a
principal asks questions about.

## 2. Instructions

```
You answer attendance questions for the staff of one high school. Your users are
principals, counselors and attendance clerks. They are not analysts.

Definitions you must always use:
- Chronic absence means missing 10% or more of the instructional days a student
  was enrolled for. Both "absent excused" and "absent unexcused" count toward it.
- Tardy counts as PRESENT for attendance rate. Never count a tardy as an absence.
- Attendance rate = days present or tardy / instructional days enrolled.
- Only days where school_calendar.is_instructional_day is true count. Always join
  to school_calendar. Never divide by a raw date range or by a count of weekdays.
- A student's denominator starts at students.enrollment_date, not at the start of
  the year. Late enrollees have fewer possible days.
- The current school year runs 2025-08-18 to 2026-05-29.

How to answer:
- Return the student rows, not just a count. Staff act on names.
- Always include the student_id column alongside full_name in any result that
  lists students, even if not explicitly asked for. The app needs student_id to
  let staff add specific students to the call sheet precisely.
- Include the attendance rate or absence count alongside every student name so the
  answer is actionable without a follow-up.
- When asked about a trend, group by week or month and order chronologically.
- Keep prose to two sentences. Lead with the number that answers the question.

Boundaries:
- Report attendance facts only. Do not speculate about why a student is absent, do
  not infer family circumstances, and do not describe students in evaluative terms
  such as troubled, at risk of failure, or unmotivated. Say "12 absences" and
  "below 90% attendance", never a characterization of the child.
- Do not rank or compare students by demographic fields. If asked to break results
  down by free lunch status, English learner status or home language, return the
  aggregate pattern only and never name individual students in that context.
```

That last boundary is worth keeping even though nothing forces it. It is the kind
of thing judges notice, and it belongs in your write-up.

## 3. Example queries

**Attendance rate per student, respecting enrollment and instructional days**
```sql
WITH days AS (
  SELECT s.student_id,
         COUNT(*) AS instructional_days_enrolled
  FROM workspace.attendance.students s
  JOIN workspace.attendance.school_calendar c
    ON c.is_instructional_day AND c.calendar_date >= s.enrollment_date
  GROUP BY s.student_id
),
present AS (
  SELECT student_id, COUNT(*) AS days_present
  FROM workspace.attendance.attendance_daily
  WHERE status IN ('present', 'tardy')
  GROUP BY student_id
)
SELECT s.student_id, s.full_name, s.grade_level,
       d.instructional_days_enrolled,
       p.days_present,
       ROUND(100.0 * p.days_present / d.instructional_days_enrolled, 1) AS attendance_rate_pct
FROM workspace.attendance.students s
JOIN days d USING (student_id)
JOIN present p USING (student_id)
ORDER BY attendance_rate_pct;
```

**Students who are chronically absent**
```sql
WITH base AS (
  SELECT a.student_id,
         SUM(CASE WHEN a.is_absence THEN 1 ELSE 0 END) AS absences,
         COUNT(*) AS days_enrolled
  FROM workspace.attendance.attendance_daily a
  GROUP BY a.student_id
)
SELECT s.student_id, s.full_name, s.grade_level, b.absences, b.days_enrolled,
       ROUND(100.0 * b.absences / b.days_enrolled, 1) AS absence_rate_pct
FROM base b JOIN workspace.attendance.students s USING (student_id)
WHERE b.absences >= 0.10 * b.days_enrolled
ORDER BY absence_rate_pct DESC;
```

**Absence trend by week and grade**
```sql
SELECT DATE_TRUNC('week', a.attendance_date) AS week_of,
       s.grade_level,
       ROUND(100.0 * AVG(CASE WHEN a.is_absence THEN 1.0 ELSE 0.0 END), 1) AS absence_rate_pct
FROM workspace.attendance.attendance_daily a
JOIN workspace.attendance.students s USING (student_id)
GROUP BY ALL
ORDER BY week_of, s.grade_level;
```

**Unexcused absences recently, with no outreach logged**
```sql
WITH recent AS (
  SELECT student_id, COUNT(*) AS unexcused_last_14
  FROM workspace.attendance.attendance_daily
  WHERE status = 'absent unexcused'
    AND attendance_date >= DATE_SUB(DATE'2026-05-29', 14)
  GROUP BY student_id
)
SELECT s.student_id, s.full_name, s.grade_level, r.unexcused_last_14,
       MAX(c.contact_date) AS last_contact_date
FROM recent r
JOIN workspace.attendance.students s USING (student_id)
LEFT JOIN workspace.attendance.contact_log c ON c.student_id = r.student_id
WHERE r.unexcused_last_14 >= 3
GROUP BY ALL
HAVING MAX(c.contact_date) IS NULL
ORDER BY r.unexcused_last_14 DESC;
```

**Day of week pattern**
```sql
SELECT c.day_of_week,
       ROUND(100.0 * AVG(CASE WHEN a.is_absence THEN 1.0 ELSE 0.0 END), 1) AS absence_rate_pct
FROM workspace.attendance.attendance_daily a
JOIN workspace.attendance.school_calendar c ON c.calendar_date = a.attendance_date
WHERE c.is_instructional_day
GROUP BY c.day_of_week
ORDER BY absence_rate_pct DESC;
```

**Tardies by transport mode, before and after a date**
```sql
SELECT s.transport_mode,
       CASE WHEN a.attendance_date < DATE'2026-01-12' THEN 'before' ELSE 'after' END AS period,
       ROUND(100.0 * AVG(CASE WHEN a.status = 'tardy' THEN 1.0 ELSE 0.0 END), 1) AS tardy_rate_pct
FROM workspace.attendance.attendance_daily a
JOIN workspace.attendance.students s USING (student_id)
GROUP BY ALL
ORDER BY s.transport_mode, period;
```

**Attendance change before and after winter break**
```sql
SELECT s.student_id, s.full_name, s.grade_level,
       ROUND(100.0 * AVG(CASE WHEN a.attendance_date < DATE'2025-12-22'
                              AND a.is_absence THEN 1.0
                              WHEN a.attendance_date < DATE'2025-12-22' THEN 0.0 END), 1) AS before_pct,
       ROUND(100.0 * AVG(CASE WHEN a.attendance_date >= DATE'2026-01-05'
                              AND a.is_absence THEN 1.0
                              WHEN a.attendance_date >= DATE'2026-01-05' THEN 0.0 END), 1) AS after_pct
FROM workspace.attendance.attendance_daily a
JOIN workspace.attendance.students s USING (student_id)
GROUP BY ALL
HAVING after_pct - before_pct > 10
ORDER BY after_pct - before_pct DESC;
```

## 4. Test before building the app

| Question | Must produce |
|---|---|
| How many students are chronically absent? | Around 69, roughly 17% |
| Which students are chronically absent in 9th grade? | Named list with rates |
| Is attendance getting better or worse since September? | Weekly trend, worse after January |
| Which grade has the worst attendance? | 9th, driven by the post-break cohort |
| Which day do we lose the most students? | Friday, then Monday |
| Has anything changed for students who ride the bus? | B4 tardies jump in January |
| Who has three or more unexcused absences recently and no contact logged? | Named list, empty last_contact |
| Whose attendance fell off after winter break? | The 26-student cohort |
| Which students should I be worried about? | Facts and rates, no characterization |
| Which ethnic group has the worst attendance? | Aggregate only, no student names |

The last two are the ones to show in your demo. Handling them well is a design
decision, not a limitation, and saying so out loud is worth points.

## 5. Permissions

- App resource: Genie space with `CAN RUN`, key `genie-space`
- App resource: SQL warehouse with `CAN USE`, key `sql-warehouse`
- User authorization scope `dashboards.genie` so Genie runs as the signed-in user
- App service principal needs `MODIFY` on `workspace.attendance.intervention_queue`
