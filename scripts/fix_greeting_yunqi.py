"""一次性脚本：云起未来招呼语纠错后重发准备（写入文本 + 重确认 + 同步任务 hash）。"""
import hashlib
import json
import sqlite3
import urllib.request

BASE = "http://127.0.0.1:8765"
JOB = "47a2c61fe492fdd80nZy3Ny6GFJW"
TASK = "send-ecd6ac7fbd324a63821d86178c57d8cc"
DB = r"E:\auto-job-agent\output\jobagent.db"

TEXT = ("你好，我就是典型的 AI 控：日常靠 Claude Code、Codex 干活，最近的求职智能体项目从 JD 采集、"
        "匹配打分到自动沟通全流程都是我自己用 AI+代码实现的，也做过公司首个大模型统一接入，统一管多渠道的鉴权和额度。"
        "产品 0→1 我走完过完整闭环：需求拆解、方案、编码、上线、迭代，不甩锅不等待。"
        "看到你们要的是不靠研发直接把产品做出来的人，这就是我现在的日常工作方式，想聊聊你们正在做的产品方向。")


def call(method, path, payload=None):
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


r = call("PUT", f"/api/outreach/{JOB}/greeting?platform=boss", {"text": TEXT})
o = r.get("outreach", r)
print("step1 update_greeting: version=%s, confirmed=%s" % (
    o.get("content_version"), o.get("greeting_confirmed_at")))

r = call("POST", f"/api/outreach/{JOB}/confirm?platform=boss", {"kind": "greeting"})
o = r.get("outreach", r)
print("step2 confirm: greeting_confirmed=%s resume_confirmed=%s ready=%s" % (
    bool(o.get("greeting_confirmed_at")), bool(o.get("resume_confirmed_at")), o.get("ready")))

new_hash = hashlib.sha256(TEXT.encode("utf-8")).hexdigest()
conn = sqlite3.connect(DB)
cur = conn.execute(
    "UPDATE outreach_send_tasks SET greeting_hash=? "
    "WHERE task_id=? AND status IN ('partial','failed','queued')",
    (new_hash, TASK))
conn.commit()
print("step3 hash update rows:", cur.rowcount)
row = conn.execute(
    "SELECT status, greeting_hash FROM outreach_send_tasks WHERE task_id=?",
    (TASK,)).fetchone()
conn.close()
print("step3 task now:", row[0], "hash_ok:", row[1] == new_hash)
