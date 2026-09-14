"""统一岗位身份、操作资格与发送状态分类（2026-09-14 批量投递落地）。

三件事集中在这里，避免列表、详情、前端各自复制规则：

1. ``resolve_job``：以 ``platform + job_id`` 为全链路身份，面向正式岗位库
   （``job_details``）校验岗位是否存在、是否平台歧义。审阅样本只控制展示
   范围，不作为保存意向或准备投递包的前提。
2. ``classify_send_state``：把发送账本聚合成一个明确状态
   （none/sent/partial/unknown/paused/running/failed）。绝不把任意
   ``send_status`` 或 ``send_blocked`` 都统计成“已投递”。
3. ``compute_capabilities``：集中返回每一项操作的资格与原因，前端不再自行
   复制规则。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# 明确排除的公司：已真实投递或用户点名排除。排除必须在最终发送入口生效，
# 包括历史已授权的任务（见 outreach_send._load_ready_package）。
EXCLUDED_COMPANIES = {"云起未来", "云起科技", "比特鹰"}

SEND_STATE_LABELS = {
    "none": "未发送",
    "sent": "已投递",
    "partial": "部分发送",
    "unknown": "结果未知",
    "paused": "已暂停待核对",
    "running": "发送中",
    "failed": "发送失败",
}

# 资格键，前端按固定顺序读取。
CAPABILITY_KEYS = ("view", "note", "prepare", "interest", "send")

# 发送状态对应的行动禁令原因。
_SEND_BLOCK_REASONS = {
    "sent": "该岗位已完成发送，禁止重复发送",
    "partial": "存在部分发送（招呼语或长图未全部送达），需人工核对后再处理",
    "unknown": "上次发送结果未知，禁止自动重试，请人工核对 Boss 聊天窗",
    "paused": "发送已暂停，等待人工核对后由本人决定",
}


class JobLookupError(ValueError):
    """岗位身份无法唯一定位。"""


class JobAmbiguous(JobLookupError):
    """同一岗位编号存在于多个平台。"""


class JobNotFound(JobLookupError):
    """岗位在正式岗位库中不存在。"""


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(Path(db_path)))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _latest_snapshot(connection: sqlite3.Connection) -> sqlite3.Row | None:
    if not _table_exists(connection, "job_match_analyses"):
        return None
    return connection.execute(
        """
        SELECT analysis_version, profile_id, profile_version
          FROM job_match_analyses
         ORDER BY analyzed_at DESC, rowid DESC
         LIMIT 1
        """
    ).fetchone()


def resolve_job(db_path: str | Path, job_id: str, platform: str | None = None) -> dict:
    """按 ``platform + job_id`` 从正式岗位库读取岗位。

    - 未给 platform 时若同编号命中多平台，抛 ``JobAmbiguous``，要求显式指定，
      不做“取第一条”的宽泛回退。
    - 岗位不存在抛 ``JobNotFound``。
    - 返回字典统一携带 ``jd`` 字段与 ``decision``、匹配结论、在架状态。
    """
    job_id = str(job_id or "").strip()
    if not job_id:
        raise JobNotFound("岗位编号为空")
    conn = _connect(db_path)
    try:
        return _resolve_job(conn, job_id, platform)
    finally:
        conn.close()


def _resolve_job(connection: sqlite3.Connection, job_id: str, platform: str | None) -> dict:
    if not _table_exists(connection, "job_details"):
        raise JobNotFound("岗位库不存在")
    query = "SELECT * FROM job_details WHERE job_id = ?"
    params: list = [job_id]
    if platform:
        query += " AND platform = ?"
        params.append(platform)
    rows = connection.execute(query, params).fetchall()
    if len(rows) > 1:
        raise JobAmbiguous("同一岗位编号存在于多个平台，请指定平台")
    if not rows:
        raise JobNotFound("岗位不存在")
    job = dict(rows[0])

    decision = connection.execute(
        "SELECT * FROM job_decisions WHERE platform = ? AND job_id = ?",
        (job["platform"], job_id),
    ).fetchone()
    job["decision"] = dict(decision) if decision else None
    job["interest"] = (job["decision"] or {}).get("interest")

    state_row = None
    if _table_exists(connection, "job_freshness"):
        state_row = connection.execute(
            "SELECT evidence_state, last_seen_at FROM job_freshness "
            "WHERE platform = ? AND job_id = ?",
            (job["platform"], job_id),
        ).fetchone()
    job["evidence_state"] = state_row["evidence_state"] if state_row else ""
    job["last_seen_at"] = state_row["last_seen_at"] if state_row else ""

    snapshot = _latest_snapshot(connection)
    if snapshot is not None:
        match = connection.execute(
            """
            SELECT recommendation, score, information_status, track,
                   analyzed_at, jd_content_hash, hard_requirements_json,
                   strengths_json, gaps_json, uncertainties_json
              FROM job_match_analyses
             WHERE analysis_version = ? AND profile_id = ? AND profile_version = ?
               AND platform = ? AND job_id = ?
            """,
            (
                snapshot["analysis_version"],
                snapshot["profile_id"],
                snapshot["profile_version"],
                job["platform"],
                job_id,
            ),
        ).fetchone()
        if match is not None:
            job["recommendation"] = match["recommendation"]
            job["score"] = int(match["score"] or 0)
            job["information_status"] = match["information_status"]
            job["track"] = match["track"]
            job["analyzed_at"] = match["analyzed_at"]
            for source, target in (
                ("hard_requirements_json", "hard_requirements"),
                ("strengths_json", "strengths"),
                ("gaps_json", "gaps"),
                ("uncertainties_json", "uncertainties"),
            ):
                job[target] = _json_list(match[source])
    job.setdefault("recommendation", "unknown")
    job.setdefault("score", 0)
    job.setdefault("information_status", "unknown")
    job["jd"] = str(job.get("jd_text") or job.get("jd") or "")
    return job


def _json_list(value: object) -> list:
    import json

    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def is_excluded_company(company: object) -> bool:
    return str(company or "").strip() in EXCLUDED_COMPANIES


def classify_send_state(
    db_path: str | Path, platform: str, job_id: str
) -> dict:
    """把发送账本聚合为一个明确状态。

    只要有任何一次任务真正送达（招呼语与长图都已确认），就是 sent；
    只送达一部分是 partial；有未知结果优先按 unknown 处理，绝不冒充已投递。
    """
    return classify_send_states(db_path).get(
        (str(platform), str(job_id)),
        {"state": "none", "label": SEND_STATE_LABELS["none"], "task_id": None},
    )


def _aggregate_state(task_states: list[str]) -> str:
    """由任务状态与动作状态推导出的状态优先级。"""
    for want in ("sent", "partial"):
        if want in task_states:
            return want
    if "unknown" in task_states:
        return "unknown"
    if any(item in {"queued", "running"} for item in task_states):
        return "running"
    if "paused" in task_states:
        return "paused"
    if "failed" in task_states:
        return "failed"
    return "none"


def classify_send_states(db_path: str | Path) -> dict[tuple[str, str], dict]:
    """一次性聚合全库发送状态，避免逐岗位开连接。"""
    conn = _connect(db_path)
    try:
        if not _table_exists(conn, "outreach_send_tasks"):
            return {}
        tasks = conn.execute(
            "SELECT * FROM outreach_send_tasks ORDER BY created_at DESC, rowid DESC"
        ).fetchall()
        if not tasks:
            return {}
        action_rows = []
        if _table_exists(conn, "outreach_send_actions"):
            action_rows = conn.execute(
                "SELECT task_id, kind, status FROM outreach_send_actions"
            ).fetchall()
    finally:
        conn.close()

    by_task: dict[str, dict[str, str]] = {}
    for row in action_rows:
        by_task.setdefault(row["task_id"], {})[row["kind"]] = row["status"]

    grouped: dict[tuple[str, str], list[tuple[str, dict]]] = {}
    for task in tasks:
        grouped.setdefault((task["platform"], task["job_id"]), []).append((task, by_task.get(task["task_id"], {})))

    result: dict[tuple[str, str], dict] = {}
    for key, entries in grouped.items():
        states: list[str] = []
        for task, actions in entries:
            values = set(actions.values())
            if values and values == {"confirmed"}:
                states.append("sent")
            elif "confirmed" in values:
                states.append("partial")
            elif "unknown" in values:
                states.append("unknown")
            states.append(task["status"])
        state = _aggregate_state(states)
        result[key] = {
            "state": state,
            "label": SEND_STATE_LABELS[state],
            "task_id": entries[0][0]["task_id"],
        }
    return result



def compute_capabilities(
    job: dict | None,
    *,
    send_state: dict | None = None,
    package: dict | None = None,
    excluded: bool | None = None,
) -> dict:
    """集中返回每项操作的资格与原因。前端只读结果，不再复制规则。"""
    send_state = send_state or {"state": "none", "label": SEND_STATE_LABELS["none"]}
    state = str(send_state.get("state") or "none")
    label = SEND_STATE_LABELS.get(state, state)

    def item(allowed: bool, reason: str = "") -> dict:
        return {"allowed": bool(allowed), "reason": "" if allowed else reason}

    if job is None:
        return {
            "exists": False,
            "send_state": state,
            "send_state_label": label,
            "capabilities": {key: item(False, "岗位不存在") for key in CAPABILITY_KEYS},
        }

    closed = str(job.get("evidence_state") or "") in {"closed", "archived"}
    jd = str(job.get("jd") or job.get("jd_text") or "").strip()
    if excluded is None:
        excluded = is_excluded_company(job.get("company"))

    prepare_allowed = bool(jd) and not closed
    if not jd:
        prepare_reason = "岗位缺少 JD 正文，无法准备投递包"
    elif closed:
        prepare_reason = "岗位已关闭，不能准备投递包"
    else:
        prepare_reason = ""

    if excluded:
        send_allowed, send_reason = False, "该公司已投递或明确排除，禁止发送"
    elif state in _SEND_BLOCK_REASONS:
        send_allowed, send_reason = False, _SEND_BLOCK_REASONS[state]
    elif package is None or not package.get("ready"):
        send_allowed, send_reason = False, "投递包尚未就绪：招呼语与简历长图必须分别确认"
    elif package.get("stale"):
        send_allowed, send_reason = False, "投递包已失效，请重新生成后再发送"
    else:
        send_allowed, send_reason = True, ""

    capabilities = {
        "view": item(True),
        "note": item(True),
        "prepare": item(prepare_allowed, prepare_reason),
        "interest": item(True),
        "send": item(send_allowed, send_reason),
    }
    return {
        "exists": True,
        "excluded": bool(excluded),
        "closed": closed,
        "send_state": state,
        "send_state_label": label,
        "capabilities": capabilities,
    }
