"""成品简历资产（三选一）测试：选择、推荐、复制与版本/确认语义。"""

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from jobagent.outreach import (
    OutreachService,
    OutreachValidationError,
    normalize_resume_assets,
)


def job(**overrides):
    base = {
        "platform": "boss", "job_id": "job-1",
        "title": "AI Agent 工程师", "company": "产品公司",
        "jd_text": "负责 Agent、RAG 和企业 AI 应用落地，要求交付经验。",
        "recommendation": "priority", "information_status": "sufficient",
        "match_stale": False, "decision": {"interest": "want"}, "score": 91,
    }
    base.update(overrides)
    return base


class FakeAI:
    def greeting(self, resume_md, job, knowledge=""):
        return "你好，我目前做 AI 应用工程，主要负责 Agent 与 RAG 落地。"

    def resume_plan(self, *args, **kwargs):  # pragma: no cover - 资产模式下不允许被调用
        raise AssertionError("成品资产模式下不应再请求简历重排计划")


def make_service(root: Path, assets: list[dict], job_loader=None) -> OutreachService:
    return OutreachService(
        root / "jobs.db",
        resume_path=root / "resume.md",
        job_loader=job_loader or (lambda platform, job_id: job()),
        candidate_loader=lambda: [],
        ai=FakeAI(),
        knowledge=None,
        renderer=type("R", (), {"output_dir": root / "tailored"})(),
        assets=assets,
    )


class NormalizeResumeAssetsTests(unittest.TestCase):
    def test_relative_paths_resolve_and_files_must_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.docx").write_bytes(b"d")
            (root / "a.jpg").write_bytes(b"i")
            assets = normalize_resume_assets(
                [{"id": "a", "label": "A", "match_keywords": ["FDE"],
                  "docx": "a.docx", "image": "a.jpg"}],
                root=root,
            )
            self.assertEqual(assets[0]["id"], "a")
            self.assertEqual(assets[0]["docx_path"], (root / "a.docx").resolve())
            with self.assertRaises(OutreachValidationError):
                normalize_resume_assets(
                    [{"id": "b", "docx": "missing.docx", "image": "a.jpg"}], root=root
                )

    def test_duplicate_ids_and_bad_shape_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.docx").write_bytes(b"d")
            (root / "a.jpg").write_bytes(b"i")
            spec = [{"id": "a", "docx": "a.docx", "image": "a.jpg"}]
            with self.assertRaises(OutreachValidationError):
                normalize_resume_assets(spec + [{"id": "a", "docx": "a.docx", "image": "a.jpg"}], root=root)
            with self.assertRaises(OutreachValidationError):
                normalize_resume_assets({"id": "a"}, root=root)
            self.assertEqual(normalize_resume_assets(None, root=root), [])


class ResumeAssetSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "resume.md").write_text("# AI 应用工程师\n\n- Agent 项目", encoding="utf-8")
        self.assets = []
        for index, asset_id in enumerate(("fde", "agent"), start=1):
            docx = self.root / f"{asset_id}.docx"
            image = self.root / f"{asset_id}.jpg"
            docx.write_bytes(f"docx-{asset_id}".encode("utf-8"))
            Image.new("RGB", (1400, 2000 + index), "white").save(image, "JPEG")
            self.assets.append(
                {"id": asset_id, "label": asset_id.upper(),
                 "keywords": ["交付"] if asset_id == "fde" else ["Agent", "RAG"],
                 "docx_path": docx, "image_path": image}
            )

    def tearDown(self):
        self.tmp.cleanup()

    def test_suggest_picks_asset_with_most_keyword_hits(self):
        service = make_service(self.root, self.assets)
        try:
            self.assertEqual(service._suggest_asset_id(job()), "agent")
            fde_job = job(title="FDE 工程师", jd_text="驻场交付与实施。")
            self.assertEqual(service._suggest_asset_id(fde_job), "fde")
            unknown = job(title="Java 后端", jd_text="CRUD 业务系统。")
            self.assertEqual(service._suggest_asset_id(unknown), "fde")  # 平局取第一个
        finally:
            service.close()

    def test_generate_uses_asset_copy_without_reorder_plan(self):
        service = make_service(self.root, self.assets)
        try:
            package = service.generate("boss", "job-1")
            self.assertEqual(package["resume_asset_id"], "agent")
            copied = Path(package["image_path"])
            self.assertTrue(copied.is_file())
            self.assertEqual(package["image_width"], 1400)
            self.assertEqual(package["image_height"], 2002)
            self.assertTrue(Path(package["docx_path"]).is_file())
            self.assertEqual(package["change_summary"]["changes"], [])  # 成品资产不做任何改写
            self.assertIn("Agent 项目", package["tailored_md"])  # 母版原文存档
            self.assertFalse(package["ready"])
        finally:
            service.close()

    def test_generate_with_explicit_asset_and_reselection_bumps_version(self):
        service = make_service(self.root, self.assets)
        try:
            first = service.generate("boss", "job-1", asset_id="agent")
            self.assertEqual(first["resume_asset_id"], "agent")
            self.assertEqual(first["content_version"], 1)

            service.store.confirm("boss", "job-1", "greeting")
            service.store.confirm("boss", "job-1", "resume")
            self.assertTrue(service.get("boss", "job-1")["ready"])

            switched = service.select_asset("boss", "job-1", "fde")
            self.assertEqual(switched["resume_asset_id"], "fde")
            self.assertEqual(switched["content_version"], 2)
            self.assertTrue(switched["greeting_confirmed"])  # 招呼语未变，确认保留
            self.assertFalse(switched["resume_confirmed"])
            self.assertFalse(switched["ready"])
            copied = Path(switched["image_path"])
            self.assertEqual(Image.open(copied).size, (1400, 2001))
        finally:
            service.close()

    def test_select_asset_requires_generated_package_and_known_id(self):
        service = make_service(self.root, self.assets)
        try:
            with self.assertRaises(OutreachValidationError):
                service.select_asset("boss", "job-1", "fde")
            service.generate("boss", "job-1")
            with self.assertRaises(OutreachValidationError):
                service.select_asset("boss", "job-1", "nope")
        finally:
            service.close()

    def test_detail_exposes_assets_with_suggestion(self):
        service = make_service(self.root, self.assets)
        try:
            detail = service.get("boss", "job-1")
            self.assertEqual(len(detail["resume_assets"]), 2)
            self.assertTrue(detail["resume_assets"][1]["suggested"])
            self.assertEqual(detail["suggested_asset_id"], "agent")
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()
