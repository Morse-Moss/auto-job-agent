"""集中式岗位身份、操作资格与发送状态分类测试（2026-09-14 批量投递落地）。"""

import sqlite3

import pytest

from jobagent.job_access import (
    JobAmbiguous,
    JobNotFound,
    classify_send_state,
    compute_capabilities,
    is_excluded_company,
    resolve_job,
)


def _db(tmp_path):
    path = tmp_path / "jobs.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE job_details (
          platform TEXT NOT NULL, job_id TEXT NOT NULL, title TEXT, company TEXT,
          jd_text TEXT, salary_raw TEXT, location TEXT, company_size TEXT, industry TEXT,
          source_url TEXT, detail_status TEXT, collected_at TEXT,
          PRIMARY KEY(platform, job_id)
        );
        CREATE TABLE job_decisions (
          platform TEXT NOT NULL, job_id TEXT NOT NULL, interest TEXT,
          rejection_reason TEXT DEFAULT '', note TEXT DEFAULT '',
          learning_intent INTEGER, decided_at TEXT, PRIMARY KEY(platform, job_id)
        );
        CREATE TABLE job_freshness (
          platform TEXT NOT NULL, job_id TEXT NOT NULL, evidence_state TEXT,
          last_seen_at TEXT, PRIMARY KEY(platform, job_id)
        );
        CREATE TABLE outreach_send_tasks (
          task_id TEXT PRIMARY KEY, platform TEXT, job_id TEXT, content_version INTEGER,
          greeting_hash TEXT, image_hash TEXT, jd_hash TEXT, resume_hash TEXT, status TEXT,
          stop_reason TEXT DEFAULT '', created_at TEXT, updated_at TEXT, finished_at TEXT
        );
        CREATE TABLE outreach_send_actions (
          task_id TEXT, kind TEXT, status TEXT, attempts INTEGER DEFAULT 0,
          evidence TEXT DEFAULT '', error TEXT DEFAULT '', updated_at TEXT,
          PRIMARY KEY(task_id, kind)
        );
        """
    )
    con.execute(
        "INSERT INTO job_details (platform, job_id, title, company, jd_text, salary_raw,"
        " location, company_size, industry, source_url, detail_status, collected_at)"
        " VALUES ('boss','job-1','AI Agent 工程师','产品公司','负责 Agent 落地。','20-30K',"
        " '广州','100-499人','企业服务','https://x','collected','2026-09-14T00:00:00+08:00')"
    )
    con.execute(
        "INSERT INTO job_details (platform, job_id, title, company, jd_text, salary_raw,"
        " location, company_size, industry, source_url, detail_status, collected_at)"
        " VALUES ('boss','job-2','FDE','云起未来','负责部署。','20-30K',"
        " '深圳','','', 'https://y','collected','2026-09-14T00:00:00+08:00')"
    )
    con.commit()
    con.close()
    return path


def _add_task(path, task_id, job_id, status, actions):
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO outreach_send_tasks (task_id, platform, job_id, content_version,"
        " greeting_hash, image_hash, jd_hash, resume_hash, status, created_at, updated_at)"
        " VALUES (?, 'boss', ?, 1, 'g', 'i', 'j', 'r', ?, '2026-09-14T00:00:00+08:00',"
        " '2026-09-14T00:00:00+08:00')",
        (task_id, job_id, status),
    )
    for kind, state in actions.items():
        con.execute(
            "INSERT INTO outreach_send_actions (task_id, kind, status, updated_at)"
            " VALUES (?, ?, ?, '2026-09-14T00:00:00+08:00')",
            (task_id, kind, state),
        )
    con.commit()
    con.close()


def test_resolve_job_returns_jd_and_decision(tmp_path):
    path = _db(tmp_path)
    job = resolve_job(path, "job-1")
    assert job["platform"] == "boss"
    assert job["jd"] == "负责 Agent 落地。"
    assert job["decision"] is None


def test_resolve_job_raises_when_missing(tmp_path):
    path = _db(tmp_path)
    with pytest.raises(JobNotFound):
        resolve_job(path, "missing")


def test_resolve_job_requires_platform_when_ambiguous(tmp_path):
    path = _db(tmp_path)
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO job_details (platform, job_id, title, jd_text, detail_status, collected_at)"
        " VALUES ('other','job-1','同编号','别的 JD','collected','2026-09-14T00:00:00+08:00')"
    )
    con.commit()
    con.close()
    with pytest.raises(JobAmbiguous):
        resolve_job(path, "job-1")
    # 显式指定平台后可以唯一定位
    assert resolve_job(path, "job-1", platform="other")["platform"] == "other"


def test_partial_is_not_reported_as_sent(tmp_path):
    path = _db(tmp_path)
    _add_task(path, "t1", "job-1", "partial", {"greeting": "confirmed", "image": "failed"})
    state = classify_send_state(path, "boss", "job-1")
    assert state["state"] == "partial"
    assert state["label"] == "部分发送"


def test_fully_delivered_is_sent(tmp_path):
    path = _db(tmp_path)
    _add_task(path, "t1", "job-1", "confirmed", {"greeting": "confirmed", "image": "confirmed"})
    assert classify_send_state(path, "boss", "job-1")["state"] == "sent"


def test_unknown_result_wins_over_queued(tmp_path):
    path = _db(tmp_path)
    _add_task(path, "t1", "job-1", "unknown", {"greeting": "unknown", "image": "pending"})
    assert classify_send_state(path, "boss", "job-1")["state"] == "unknown"


def test_no_task_means_none(tmp_path):
    path = _db(tmp_path)
    assert classify_send_state(path, "boss", "job-1")["state"] == "none"


def test_capabilities_allow_prepare_for_challenge_without_want(tmp_path):
    path = _db(tmp_path)
    job = resolve_job(path, "job-1")
    job["recommendation"] = "challenge"
    caps = compute_capabilities(job, send_state={"state": "none"}, package=None)
    assert caps["capabilities"]["prepare"]["allowed"] is True
    assert caps["capabilities"]["view"]["allowed"] is True
    assert caps["capabilities"]["note"]["allowed"] is True
    # 未就绪的投递包不能发送
    assert caps["capabilities"]["send"]["allowed"] is False


def test_capabilities_block_prepare_without_jd(tmp_path):
    path = _db(tmp_path)
    con = sqlite3.connect(path)
    con.execute("UPDATE job_details SET jd_text='' WHERE job_id='job-1'")
    con.commit()
    con.close()
    job = resolve_job(path, "job-1")
    caps = compute_capabilities(job, send_state={"state": "none"}, package=None)
    assert caps["capabilities"]["prepare"]["allowed"] is False
    assert "JD" in caps["capabilities"]["prepare"]["reason"]


def test_capabilities_block_send_for_excluded_and_sent(tmp_path):
    path = _db(tmp_path)
    excluded = resolve_job(path, "job-2")
    ready = {"ready": True, "stale": False}
    caps = compute_capabilities(
        excluded, send_state={"state": "none"}, package=ready
    )
    assert caps["capabilities"]["send"]["allowed"] is False
    assert caps["excluded"] is True

    job = resolve_job(path, "job-1")
    caps = compute_capabilities(job, send_state={"state": "sent"}, package=ready)
    assert caps["capabilities"]["send"]["allowed"] is False
    assert "重复发送" in caps["capabilities"]["send"]["reason"]


def test_is_excluded_company():
    assert is_excluded_company("云起未来") is True
    assert is_excluded_company(" 比特鹰 ") is True
    assert is_excluded_company("产品公司") is False
