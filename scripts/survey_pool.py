"""今晚采集前的现状盘点（只读）。"""
import sqlite3
from collections import Counter

DB = r"E:\auto-job-agent\output\jobagent.db"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

ver = conn.execute("SELECT MAX(analysis_version) FROM job_match_analyses").fetchone()[0]
print("分析版本:", ver)

# 每岗最优一次分析
best = {}
for r in conn.execute(
    "SELECT platform, job_id, score, recommendation, track FROM job_match_analyses "
    "WHERE analysis_version=? ORDER BY score DESC",
    (ver,),
):
    key = (r["platform"], r["job_id"])
    if key not in best:
        best[key] = dict(r)
print("已分析岗位数(去重):", len(best))

decisions = {
    (r["platform"], r["job_id"]): r["interest"]
    for r in conn.execute("SELECT platform, job_id, interest FROM job_decisions")
}
state = {
    (r["platform"], r["job_id"]): r["evidence_state"]
    for r in conn.execute("SELECT platform, job_id, evidence_state FROM job_freshness")
}

print("\n== 决策覆盖 ==")
c = Counter(decisions.get(k, "(无决策)") for k in best)
for k, v in c.most_common():
    print(" ", k, v)

print("\n== 无决策 x active x 档位/赛道 ==")
c = Counter()
for k, m in best.items():
    if decisions.get(k) is None and state.get(k) == "active":
        c[(m["recommendation"], m["track"])] += 1
for (rec, track), n in c.most_common(15):
    print(" ", rec, "|", track, "=", n)

print("\n== 无决策 active 且 priority 的岗位（分数降序，前 25） ==")
rows = [
    (m["score"], k, m["track"])
    for k, m in best.items()
    if decisions.get(k) is None and state.get(k) == "active" and m["recommendation"] == "priority"
]
rows.sort(reverse=True)
print("  合计:", len(rows))
titles = {}
for r in conn.execute("SELECT platform, job_id, title, company, salary_raw FROM job_details"):
    titles[(r["platform"], r["job_id"])] = (r["title"], r["company"], r["salary_raw"])
for score, k, track in rows[:25]:
    t, comp, sal = titles.get(k, ("?", "?", ""))
    print(f"  {score}分 {track:12s} {t} | {comp} | {sal}")

print("\n== 无决策 active 且 challenge 的数量 by 赛道 ==")
c = Counter(
    m["track"]
    for k, m in best.items()
    if decisions.get(k) is None and state.get(k) == "active" and m["recommendation"] == "challenge"
)
for track, n in c.most_common():
    print(" ", track, n)

print("\n== 岗位来源分布（observations.source） ==")
for r in conn.execute(
    "SELECT source, COUNT(DISTINCT job_id) n FROM job_observations GROUP BY source ORDER BY n DESC"
):
    print(" ", r["source"], r["n"])

print("\n== 最近 7 天新采集的岗位数 ==")
r = conn.execute(
    "SELECT COUNT(DISTINCT job_id) n FROM job_observations WHERE collected_at >= date('now','-7 day')"
).fetchone()
print(" ", r["n"])
conn.close()
