import tempfile
import json
import sqlite3
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request, urlopen

from docx import Document
from PIL import Image

from jobagent.outreach import (
    OutreachService,
    OutreachStore,
    OutreachValidationError,
    TailoredResumeRenderer,
    apply_controlled_resume_plan,
    build_resume_change_summary,
    build_resume_catalog,
    compare_resume_order,
)
from jobagent.review_server import create_server


class OutreachStoreTests(unittest.TestCase):
    def test_generation_can_be_edited_and_requires_two_independent_confirmations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = OutreachStore(Path(temp_dir) / "jobs.db")
            try:
                created = store.save_generation(
                    platform="boss",
                    job_id="job-1",
                    jd_hash="jd-v1",
                    resume_hash="resume-v1",
                    greeting_text="你好，我做过 Agent 和 RAG 项目，希望进一步沟通。",
                    tailored_md="# 定制简历",
                    docx_path="job-1.docx",
                    image_path="job-1.jpg",
                    docx_hash="docx-v1",
                    image_hash="image-v1",
                    image_width=1400,
                    image_height=3000,
                )
                self.assertEqual(created["status"], "generated")
                self.assertFalse(created["ready"])

                greeting_confirmed = store.confirm("boss", "job-1", "greeting")
                self.assertTrue(greeting_confirmed["greeting_confirmed"])
                self.assertFalse(greeting_confirmed["resume_confirmed"])
                self.assertFalse(greeting_confirmed["ready"])

                ready = store.confirm("boss", "job-1", "resume")
                self.assertEqual(ready["status"], "ready")
                self.assertTrue(ready["ready"])

                edited = store.update_greeting(
                    "boss", "job-1", "你好，我负责过 AI Agent 落地，希望聊聊岗位。"
                )
                self.assertFalse(edited["greeting_confirmed"])
                self.assertTrue(edited["resume_confirmed"])
                self.assertEqual(edited["status"], "edited")
                self.assertFalse(edited["ready"])
            finally:
                store.close()


class TailoredResumeRendererTests(unittest.TestCase):
    def test_renderer_creates_openable_docx_and_cropped_long_image(self):
        template = Path(__file__).resolve().parent.parent / "resume" / "Ai-product-builder-v10.docx"
        self.assertTrue(template.is_file())
        markdown = """# AI Product Builder | AI Agent 应用工程师

## 教育背景

**佛山大学 | 计算机科学与技术 本科** | 2021.09 - 2025.06

## 工作经历

### 佛山拓效科技有限公司 | AI 应用工程师 | 2026.03 - 至今

- 搭建 Agent 系统并接入 10+ 图片与视频模型。
- 建设运营数据自动化平台，月处理数据 10,000+。

## 项目经历

### 数字摩斯 | 独立开发

- 构建 BGE + pgvector RAG，并提供来源引用。

## 专业技能

- Python、FastAPI、TypeScript、React、RAG、Agent
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            renderer = TailoredResumeRenderer(template, Path(temp_dir))
            result = renderer.render(
                {"company": "产品公司", "title": "AI Agent 工程师"},
                markdown,
                "job-1-v1",
            )

            document = Document(result.docx_path)
            body = "\n".join(paragraph.text for paragraph in document.paragraphs)
            self.assertIn("运营数据自动化平台", body)
            self.assertTrue(result.image_path.is_file())
            with Image.open(result.image_path) as image:
                self.assertEqual(image.format, "JPEG")
                self.assertEqual(image.width, 1400)
                self.assertLess(image.height, 3600)
            self.assertEqual(len(result.docx_hash), 64)
            self.assertEqual(len(result.image_hash), 64)


class OutreachServiceTests(unittest.TestCase):
    def _job(self, **overrides):
        job = {
            "platform": "boss",
            "job_id": "job-1",
            "title": "AI Agent 工程师",
            "company": "产品公司",
            "jd_text": "负责 Agent、RAG 和企业 AI 应用落地。",
            "recommendation": "priority",
            "information_status": "sufficient",
            "match_stale": False,
            "decision": {"interest": "want"},
            "score": 91,
        }
        job.update(overrides)
        return job

    def test_generate_creates_local_package_without_any_send_dependency(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            resume = root / "resume.md"
            resume.write_text("# AI 应用工程师\n\n- Agent 与 RAG 项目", encoding="utf-8")
            docx_path = root / "tailored.docx"
            image_path = root / "tailored.jpg"
            docx_path.write_bytes(b"docx")
            image_path.write_bytes(b"image")

            class FakeAI:
                def greeting(self, resume_md, job, knowledge=""):
                    return "你好，我做过 Agent 与 RAG 项目，希望进一步沟通。"

                def resume_plan(self, resume_md, job, catalog):
                    return {"project_priority": [], "bullet_priority": []}

            class FakeKnowledge:
                def for_job(self, job):
                    return ""

            class FakeRenderer:
                def render(self, job, markdown, artifact_key):
                    return SimpleNamespace(
                        docx_path=docx_path, image_path=image_path,
                        docx_hash="d" * 64, image_hash="i" * 64,
                        image_width=1400, image_height=2200,
                    )

            service = OutreachService(
                root / "jobs.db",
                resume_path=resume,
                job_loader=lambda platform, job_id: self._job(),
                candidate_loader=lambda: [self._job(decision=None, interest="want")],
                ai=FakeAI(),
                knowledge=FakeKnowledge(),
                renderer=FakeRenderer(),
            )
            try:
                package = service.generate("boss", "job-1")
                self.assertEqual(package["status"], "generated")
                self.assertEqual(package["greeting_text"], "你好，我做过 Agent 与 RAG 项目，希望进一步沟通。")
                self.assertEqual(package["tailored_md"], "# AI 应用工程师\n\n- Agent 与 RAG 项目")
                self.assertEqual(package["title"], "AI Agent 工程师")
                self.assertFalse(package["ready"])
            finally:
                service.close()

    def test_reading_candidates_does_not_initialize_ai(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            resume = root / "resume.md"
            resume.write_text("# AI 应用工程师", encoding="utf-8")
            ai_calls = []
            service = OutreachService(
                root / "jobs.db",
                resume_path=resume,
                job_loader=lambda platform, job_id: self._job(),
                candidate_loader=lambda: [self._job(decision=None, interest="want")],
                ai_factory=lambda: ai_calls.append("initialized"),
                knowledge=None,
                renderer=object(),
            )
            try:
                self.assertEqual(len(service.list_candidates()), 1)
                self.assertEqual(service.get("boss", "job-1")["status"], "not_generated")
                self.assertEqual(ai_calls, [])
            finally:
                service.close()

    def test_invalid_resume_plan_is_blocked_before_render_or_save(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            resume = root / "resume.md"
            resume.write_text(
                "# AI 应用工程师\n\n## 项目经历\n\n### 数字摩斯\n\n- 构建 RAG。",
                encoding="utf-8",
            )
            render_calls = []

            class InvalidPlanAI:
                def greeting(self, resume_md, job, knowledge=""):
                    return "你好，希望进一步沟通。"

                def resume_plan(self, resume_md, job, catalog):
                    return {"project_priority": ["project-99"], "bullet_priority": []}

            class Renderer:
                def render(self, job, markdown, artifact_key):
                    render_calls.append(markdown)

            service = OutreachService(
                root / "jobs.db",
                resume_path=resume,
                job_loader=lambda platform, job_id: self._job(),
                candidate_loader=lambda: [],
                ai=InvalidPlanAI(),
                knowledge=None,
                renderer=Renderer(),
            )
            try:
                with self.assertRaisesRegex(OutreachValidationError, "不存在"):
                    service.generate("boss", "job-1")
                self.assertEqual(render_calls, [])
                self.assertIsNone(service.store.get("boss", "job-1"))
            finally:
                service.close()

    def test_unparseable_resume_plan_is_reported_as_validation_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            resume = root / "resume.md"
            resume.write_text("# AI 应用工程师\n\n- 构建 Agent。", encoding="utf-8")

            class BrokenAI:
                def greeting(self, resume_md, job, knowledge=""):
                    return "你好，希望进一步沟通。"

                def resume_plan(self, resume_md, job, catalog):
                    raise ValueError("invalid json")

            service = OutreachService(
                root / "jobs.db",
                resume_path=resume,
                job_loader=lambda platform, job_id: self._job(),
                candidate_loader=lambda: [],
                ai=BrokenAI(),
                knowledge=None,
                renderer=object(),
            )
            try:
                with self.assertRaisesRegex(OutreachValidationError, "微调计划无法解析"):
                    service.generate("boss", "job-1")
            finally:
                service.close()


class ControlledResumePlanTests(unittest.TestCase):
    RESUME = """# AI 应用工程师

## 工作经历

### 示例公司 | AI 应用工程师 | 2026.03 - 至今

- 搭建内容创作 Agent 系统，接入 10+ 模型。
- 建设运营数据自动化平台，月处理 10,000+ 条数据。

## 项目经历

### 数字摩斯 | 独立开发

技术栈：Next.js、PostgreSQL / pgvector

- 构建 RAG 并提供来源引用。

### 内容创作 Agent 系统 | 项目负责人

技术栈：React、FastAPI、LangGraph

- 设计对话式图片和视频生成工作流。

## 专业技能

- Python、FastAPI、TypeScript、React
- RAG、Agent 工作流编排
"""

    def test_plan_only_reorders_existing_projects_and_bullets(self):
        catalog = build_resume_catalog(self.RESUME)
        tailored = apply_controlled_resume_plan(
            self.RESUME,
            {
                "project_priority": ["project-2"],
                "bullet_priority": ["bullet-2", "bullet-1"],
            },
            catalog,
        )

        self.assertLess(tailored.index("内容创作 Agent 系统 | 项目负责人"), tailored.index("数字摩斯 | 独立开发"))
        self.assertLess(tailored.index("建设运营数据自动化平台"), tailored.index("搭建内容创作 Agent 系统"))
        self.assertCountEqual(tailored.splitlines(), self.RESUME.splitlines())

    def test_plan_rejects_unknown_or_duplicate_ids(self):
        catalog = build_resume_catalog(self.RESUME)
        with self.assertRaisesRegex(OutreachValidationError, "不存在"):
            apply_controlled_resume_plan(
                self.RESUME,
                {"project_priority": ["project-99"], "bullet_priority": []},
                catalog,
            )
        with self.assertRaisesRegex(OutreachValidationError, "重复"):
            apply_controlled_resume_plan(
                self.RESUME,
                {"project_priority": [], "bullet_priority": ["bullet-1", "bullet-1"]},
                catalog,
            )

    def test_plan_limits_how_much_content_can_be_prioritized(self):
        resume = self.RESUME + "\n" + "\n".join(
            f"- 补充既有要点 {index}。" for index in range(5, 10)
        )
        catalog = build_resume_catalog(resume)
        with self.assertRaisesRegex(OutreachValidationError, "最多前置 8 条"):
            apply_controlled_resume_plan(
                resume,
                {
                    "project_priority": [],
                    "bullet_priority": [
                        f"bullet-{index}" for index in range(1, 10)
                    ],
                },
                catalog,
            )

    def test_change_summary_reports_deterministic_positions_and_bound_evidence(self):
        catalog = build_resume_catalog(self.RESUME)
        plan = {
            "project_priority": ["project-2"],
            "bullet_priority": ["bullet-2"],
            "explanations": [
                {
                    "id": "project-2",
                    "reason": "岗位强调对话式 Agent 工作流",
                    "jd_evidence": "负责 Agent 工作流落地",
                },
                {
                    "id": "bullet-2",
                    "reason": "岗位需要运营数据自动化经验",
                    "jd_evidence": "建设运营数据自动化平台",
                },
            ],
        }

        summary = build_resume_change_summary(
            self.RESUME,
            plan,
            catalog,
            jd_text="负责 Agent 工作流落地，并建设运营数据自动化平台。",
        )

        changes = {item["id"]: item for item in summary["changes"]}
        self.assertEqual(set(changes), {"project-2", "bullet-2"})
        self.assertEqual(changes["project-2"]["kind"], "project_moved")
        self.assertEqual((changes["project-2"]["from"], changes["project-2"]["to"]), (2, 1))
        self.assertEqual(changes["bullet-2"]["label"], "建设运营数据自动化平台，月处理 10,000+ 条数据。")
        self.assertEqual((changes["bullet-2"]["from"], changes["bullet-2"]["to"]), (2, 1))
        self.assertEqual(changes["bullet-2"]["jd_evidence"], "建设运营数据自动化平台")

    def test_change_summary_rejects_explanations_for_unknown_or_unselected_ids(self):
        catalog = build_resume_catalog(self.RESUME)
        for invalid_id in ("project-99", "bullet-1"):
            with self.subTest(invalid_id=invalid_id):
                with self.assertRaisesRegex(OutreachValidationError, "解释.*ID"):
                    build_resume_change_summary(
                        self.RESUME,
                        {
                            "project_priority": ["project-2"],
                            "bullet_priority": [],
                            "explanations": [{"id": invalid_id, "reason": "原因", "jd_evidence": "Agent"}],
                        },
                        catalog,
                        jd_text="负责 Agent 工作流落地。",
                    )

    def test_existing_tailored_resume_can_recover_position_changes_without_ai(self):
        tailored = apply_controlled_resume_plan(
            self.RESUME,
            {"project_priority": ["project-2"], "bullet_priority": ["bullet-2"]},
        )

        summary = compare_resume_order(self.RESUME, tailored)

        changes = {(item["kind"], item["label"]): item for item in summary["changes"]}
        self.assertEqual(len(changes), 2)
        project = changes[("project_moved", "内容创作 Agent 系统 | 项目负责人")]
        bullet = changes[("bullet_moved", "建设运营数据自动化平台，月处理 10,000+ 条数据。")]
        self.assertEqual((project["from"], project["to"]), (2, 1))
        self.assertEqual((bullet["from"], bullet["to"]), (2, 1))


class OutreachApiTests(unittest.TestCase):
    @staticmethod
    def _job(**overrides):
        job = {
            "platform": "boss", "job_id": "job-1",
            "title": "AI Agent 工程师", "company": "产品公司",
            "jd_text": "负责 Agent、RAG 和企业 AI 应用落地。",
            "recommendation": "priority", "information_status": "sufficient",
            "match_stale": False, "decision": {"interest": "want"}, "score": 91,
        }
        job.update(overrides)
        return job

    def test_api_exposes_generate_edit_confirm_and_artifact_download(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sqlite3.connect(root / "jobs.db").close()
            image = root / "resume.jpg"
            image.write_bytes(b"jpeg-data")

            class FakeService:
                def list_candidates(self):
                    return [{"platform": "boss", "job_id": "job-1", "status": "not_generated"}]

                def get(self, platform, job_id):
                    return {"platform": platform, "job_id": job_id, "status": "generated"}

                def generate(self, platform, job_id):
                    return {"platform": platform, "job_id": job_id, "status": "generated"}

                def update_greeting(self, platform, job_id, text):
                    return {"platform": platform, "job_id": job_id, "greeting_text": text, "status": "edited"}

                def update_resume(self, platform, job_id, markdown):
                    return {"platform": platform, "job_id": job_id, "tailored_md": markdown, "status": "edited"}

                def confirm(self, platform, job_id, kind):
                    return {"platform": platform, "job_id": job_id, f"{kind}_confirmed": True}

                def artifact(self, platform, job_id, kind):
                    return image

                def close(self):
                    pass

            server = create_server(
                root / "jobs.db", host="127.0.0.1", port=0,
                outreach_service_factory=lambda: FakeService(),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}/api/outreach"
            try:
                with urlopen(base) as response:
                    self.assertEqual(json.load(response)["jobs"][0]["job_id"], "job-1")
                generate = Request(
                    f"{base}/job-1/generate?platform=boss", data=b"{}",
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urlopen(generate) as response:
                    self.assertEqual(json.load(response)["outreach"]["status"], "generated")
                edit = Request(
                    f"{base}/job-1/greeting?platform=boss",
                    data=json.dumps({"text": "新的招呼语"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="PUT",
                )
                with urlopen(edit) as response:
                    self.assertEqual(json.load(response)["outreach"]["greeting_text"], "新的招呼语")
                confirm = Request(
                    f"{base}/job-1/confirm?platform=boss",
                    data=json.dumps({"kind": "resume"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urlopen(confirm) as response:
                    self.assertTrue(json.load(response)["outreach"]["resume_confirmed"])
                with urlopen(f"{base}/job-1/artifact/image?platform=boss") as response:
                    self.assertEqual(response.headers.get_content_type(), "image/jpeg")
                    self.assertEqual(response.read(), b"jpeg-data")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_review_ui_exposes_outreach_preparation_controls(self):
        root = Path(__file__).resolve().parent.parent
        html = (root / "review_ui" / "index.html").read_text(encoding="utf-8")
        script = (root / "review_ui" / "app.js").read_text(encoding="utf-8")

        self.assertIn('data-view-target="outreach"', html)
        for element_id in (
            "outreachJobList", "generateOutreach", "greetingEditor",
            "resumeEditor", "outreachResumeImage", "confirmGreeting",
            "confirmResume", "downloadOutreachDocx", "outreachJdText",
            "outreachChanges", "outreachStrengths", "outreachGaps",
            "outreachMobileTabs", "greetingQuality",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn("/api/outreach", script)
        self.assertIn("loadOutreachDashboard", script)
        self.assertIn("greetingDirty", script)
        self.assertIn("resumeDirty", script)
        self.assertIn("renderOutreachChanges", script)
        self.assertIn("这版仍有模板感", script)

        for marker in (
            'data-collapse-group="jd"',
            'data-collapse-group="evidence"',
            'data-collapse-group="changes"',
            'data-collapse-action="expand"',
            'data-collapse-action="collapse"',
        ):
            self.assertIn(marker, html)
        self.assertIn("outreach-collapsible", html)
        self.assertIn("setCollapsibleGroup", script)
        self.assertIn("data-collapse-default", script)

    def test_generate_rejects_job_without_explicit_want_decision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            resume = root / "resume.md"
            resume.write_text("# Resume", encoding="utf-8")
            service = OutreachService(
                root / "jobs.db",
                resume_path=resume,
                job_loader=lambda platform, job_id: self._job(
                    decision={"interest": "maybe"}
                ),
                candidate_loader=lambda: [],
                ai=object(),
                knowledge=object(),
                renderer=object(),
            )
            try:
                with self.assertRaisesRegex(OutreachValidationError, "想投"):
                    service.generate("boss", "job-1")
            finally:
                service.close()

    def test_new_generation_replaces_artifacts_and_invalidates_old_confirmations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = OutreachStore(Path(temp_dir) / "jobs.db")
            try:
                values = dict(
                    platform="boss",
                    job_id="job-1",
                    jd_hash="jd-v1",
                    resume_hash="resume-v1",
                    greeting_text="第一版招呼语",
                    tailored_md="# 第一版",
                    docx_path="v1.docx",
                    image_path="v1.jpg",
                    docx_hash="docx-v1",
                    image_hash="image-v1",
                    image_width=1400,
                    image_height=3000,
                )
                store.save_generation(**values)
                store.confirm("boss", "job-1", "greeting")
                store.confirm("boss", "job-1", "resume")

                replaced = store.save_generation(
                    **{
                        **values,
                        "jd_hash": "jd-v2",
                        "greeting_text": "第二版招呼语",
                        "tailored_md": "# 第二版",
                        "docx_path": "v2.docx",
                        "image_path": "v2.jpg",
                    }
                )
                self.assertEqual(replaced["content_version"], 2)
                self.assertFalse(replaced["greeting_confirmed"])
                self.assertFalse(replaced["resume_confirmed"])
                self.assertFalse(replaced["ready"])
            finally:
                store.close()

    def test_current_hashes_mark_saved_content_stale(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = OutreachStore(Path(temp_dir) / "jobs.db")
            try:
                store.save_generation(
                    platform="boss",
                    job_id="job-1",
                    jd_hash="jd-v1",
                    resume_hash="resume-v1",
                    greeting_text="招呼语",
                    tailored_md="# 简历",
                    docx_path="resume.docx",
                    image_path="resume.jpg",
                    docx_hash="docx",
                    image_hash="image",
                    image_width=1400,
                    image_height=3000,
                )
                current = store.get("boss", "job-1", jd_hash="jd-v2", resume_hash="resume-v1")
                self.assertTrue(current["stale"])
                self.assertEqual(current["stale_reasons"], ["jd_changed"])
                self.assertFalse(current["ready"])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
