import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from jobagent.pilot import build_pilot_plan
import main as main_module


NOW = "2026-09-02T10:00:00+08:00"


class PilotPlanTests(unittest.TestCase):
    def _resume(self, modified_at: str):
        temp_dir = tempfile.TemporaryDirectory()
        path = Path(temp_dir.name) / "resume.md"
        path.write_text("# Resume", encoding="utf-8")
        timestamp = datetime.fromisoformat(modified_at).timestamp()
        import os

        os.utime(path, (timestamp, timestamp))
        self.addCleanup(temp_dir.cleanup)
        return path

    def _match(self, job_id="job-1", **overrides):
        value = {
            "platform": "boss",
            "job_id": job_id,
            "title": "AI Agent 工程师",
            "company": "产品公司",
            "recommendation": "priority",
            "interest": "want",
            "match_stale": False,
            "information_status": "sufficient",
            "jd_text": "RAG Agent 工作流",
            "source_url": "https://example.invalid/job",
        }
        value.update(overrides)
        return value

    def _freshness(self, job_id="job-1", **overrides):
        value = {
            "platform": "boss",
            "job_id": job_id,
            "evidence_state": "active",
            "refresh_due": False,
            "detail_status": "collected",
            "age_days": 1.0,
        }
        value.update(overrides)
        return value

    def test_stale_inputs_block_pilot_with_explicit_next_actions(self):
        resume = self._resume("2026-08-01T10:00:00+08:00")
        plan = build_pilot_plan(
            {"jobs": [self._match()]},
            {"jobs": [self._freshness(refresh_due=True, age_days=27.0)]},
            resume,
            now=NOW,
        )

        self.assertEqual(plan["candidates"], [])
        self.assertIn("resume_stale", plan["blockers"])
        self.assertIn("jd_refresh_due", plan["job_blocks"][0]["reasons"])
        self.assertEqual(plan["next_actions"], ["refresh_resume", "refresh_jd"])

    def test_only_fresh_wanted_priority_jobs_enter_bounded_pilot(self):
        resume = self._resume("2026-09-01T10:00:00+08:00")
        matches = [
            self._match("ready"),
            self._match("not-wanted", interest="maybe"),
            self._match("stale-match", match_stale=True),
        ]
        freshness = [
            self._freshness("ready"),
            self._freshness("not-wanted"),
            self._freshness("stale-match"),
        ]

        plan = build_pilot_plan(
            {"jobs": matches},
            {"jobs": freshness},
            resume,
            now=NOW,
            max_jobs=1,
        )

        self.assertEqual([job["job_id"] for job in plan["candidates"]], ["ready"])
        self.assertEqual(plan["limits"]["max_jobs"], 1)
        self.assertEqual(plan["status"], "ready_for_manual_confirmation")
        self.assertEqual(plan["next_actions"], ["confirm_each_job"])

    def test_cli_pilot_plan_is_read_only_and_does_not_construct_ai_or_browser(self):
        calls = []
        cfg = {
            "resume": {"master_file": "resume.md"},
            "collection": {"db_path": "jobs.db"},
            "matching": {},
        }
        store = type("Store", (), {
            "match_dashboard": lambda self: {"jobs": []},
            "freshness_dashboard": lambda self: {"jobs": []},
            "conn": object(),
            "close": lambda self: calls.append("close"),
        })()
        with (
            patch.object(main_module, "load_config", return_value=cfg),
            patch.object(main_module, "CollectionStore", return_value=store),
            patch.object(main_module, "ReviewStore", return_value=store),
            patch.object(main_module, "resolve_project_path", side_effect=lambda p: Path(p)),
            patch.object(main_module, "OpenAICompatibleClient", side_effect=AssertionError),
            patch.object(main_module, "BossPlatform", side_effect=AssertionError),
            patch.object(main_module, "build_pilot_plan", return_value={"status": "blocked"}),
            patch.object(main_module, "build_freshness_dashboard", return_value={"jobs": []}),
            patch("sys.argv", ["main.py", "--pilot-plan"]),
        ):
            main_module.main()
        self.assertEqual(calls, ["close", "close"])


if __name__ == "__main__":
    unittest.main()
