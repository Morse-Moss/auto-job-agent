"""Loopback-only HTTP server for the job positioning review workbench."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, quote, unquote, urlsplit

from jobagent.resume_collection import collect_resume_market_data
from jobagent.ai import create_ai_client
from jobagent.config import resolve_project_path
from jobagent.knowledge import create_provider
from jobagent.matching import refresh_stale_matches
from jobagent.jd_update import plan_from_database
from jobagent.job_updates import JobUpdateStore
from jobagent.job_update_executor import BrowserLease, BrowserLeaseBusy, JobUpdateExecutor
from jobagent.outreach import (
    OutreachService,
    OutreachValidationError,
    TailoredResumeRenderer,
    normalize_resume_assets,
)
from jobagent.outreach_send import (
    OutreachSendService,
    SendBusy,
    SendValidationError,
)
from jobagent.platforms.boss_sender import BossSender
from jobagent.progress import ProgressStore, ProgressValidationError
from jobagent.review import (
    ReviewStore,
    ReviewValidationError,
    migrate_review_database,
)


UI_DIR = Path(__file__).resolve().parent.parent / "review_ui"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


class ReviewHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address,
        handler,
        db_path: Path,
        ui_dir: Path,
        config: dict | None = None,
        collection_runner: Callable = collect_resume_market_data,
        analysis_output_dir: Path | None = None,
        outreach_service_factory: Callable | None = None,
        send_service_factory: Callable | None = None,
        job_update_executor: JobUpdateExecutor | None = None,
    ):
        super().__init__(address, handler)
        self.db_path = db_path
        self.ui_dir = ui_dir
        self.config = config or {}
        self.collection_runner = collection_runner
        self.analysis_output_dir = analysis_output_dir or db_path.parent / "analysis"
        self.outreach_service_factory = outreach_service_factory
        self.send_service_factory = send_service_factory
        self.collection_lock = threading.Lock()
        self.calibration_operation_lock = threading.Lock()
        self.collection_state = {"status": "idle"}
        self.browser_lease = BrowserLease()
        self.job_update_executor = job_update_executor or JobUpdateExecutor(
            lease=self.browser_lease, config=self.config
        )
        recovery_store = JobUpdateStore(self.db_path, self.config)
        try:
            recovery_store.recover_interrupted()
        finally:
            recovery_store.close()

    def outreach_service(self):
        if self.outreach_service_factory is None:
            raise OutreachValidationError("投递准备功能尚未配置")
        return self.outreach_service_factory()

    def send_service(self):
        """A controlled-first-contact service bound to the shared browser lease."""
        if self.send_service_factory is None:
            raise SendValidationError("受控发送功能尚未配置")
        return self.send_service_factory(self.browser_lease)

    def collection_snapshot(self) -> dict:
        with self.collection_lock:
            return dict(self.collection_state)

    def job_update_store(self) -> JobUpdateStore:
        return JobUpdateStore(self.db_path, self.config)

    def progress_store(self) -> ProgressStore:
        """S6 沟通进展存储：与 job_update_store 一样按请求创建。"""
        return ProgressStore(self.db_path)

    def send_precheck(self) -> dict:
        """发送前浏览器预检：CDP 可达 + Boss 登录态，不导航不点击。"""
        sender = BossSender(self.config.get("boss") or {})
        return sender.precheck()

    def execute_job_update(self, run_id: str) -> dict:
        """Explicit external action. Never reachable from a page load or plan build."""
        store = self.job_update_store()
        try:
            task = store.get(run_id)
            if task is None:
                raise ReviewValidationError("更新任务不存在")
            return self.job_update_executor.run(task, store)
        finally:
            store.close()

    def start_resume_collection(self, payload: dict) -> dict:
        if not self.calibration_operation_lock.acquire(blocking=False):
            if self.collection_snapshot().get("status") == "running":
                raise ReviewValidationError("岗位采集正在运行，请等待当前任务结束")
            raise ReviewValidationError("简历校准正在运行，请等待当前任务结束")
        if not self.browser_lease.try_acquire("resume-collect"):
            self.calibration_operation_lock.release()
            raise ReviewValidationError("浏览器正在被更新任务占用，请等待当前任务结束")

        try:
            with self.collection_lock:
                started_at = datetime.now().astimezone().isoformat(timespec="seconds")
                started = {
                    "status": "running",
                    "started_at": started_at,
                    "queries": list(payload.get("queries") or []),
                }
                self.collection_state = started

            thread = threading.Thread(
                target=self._run_resume_collection,
                args=(payload,),
                daemon=True,
                name="resume-market-collection",
            )
            thread.start()
        except Exception:
            with self.collection_lock:
                self.collection_state = {"status": "idle"}
            self.browser_lease.release("resume-collect")
            self.calibration_operation_lock.release()
            raise
        return dict(started)

    def calibrate_resume(self, payload: dict) -> dict:
        if not self.calibration_operation_lock.acquire(blocking=False):
            if self.collection_snapshot().get("status") == "running":
                raise ReviewValidationError("岗位采集正在运行，请等待当前任务结束")
            raise ReviewValidationError("简历校准正在运行，请等待当前任务结束")
        try:
            store = ReviewStore(self.db_path)
            try:
                return store.calibrate_resume(
                    payload["resume_text"],
                    filename=payload["filename"],
                    candidate_id=payload["candidate_id"],
                    analysis_version=payload["analysis_version"],
                )
            finally:
                store.close()
        finally:
            self.calibration_operation_lock.release()

    def confirm_preferences(self, payload: dict) -> dict:
        if not self.calibration_operation_lock.acquire(blocking=False):
            if self.collection_snapshot().get("status") == "running":
                raise ReviewValidationError("岗位采集正在运行，请等待当前任务结束")
            raise ReviewValidationError("简历校准正在运行，请等待当前任务结束")
        try:
            store = ReviewStore(self.db_path)
            try:
                return store.confirm_preferences(
                    payload,
                    candidate_id=str(payload.get("candidate_id") or "local-candidate"),
                    analysis_version=str(
                        payload.get("analysis_version") or "resume-match-v1"
                    ),
                )
            finally:
                store.close()
        finally:
            self.calibration_operation_lock.release()

    def _run_resume_collection(self, payload: dict) -> None:
        try:
            dashboard = self.collection_runner(
                self.db_path,
                self.config,
                payload["resume_text"],
                filename=payload["filename"],
                candidate_id=payload["candidate_id"],
                analysis_version=payload["analysis_version"],
                analysis_output_dir=self.analysis_output_dir,
            )
            plan = dashboard.get("collection_plan") or {}
            completed = {
                "status": str(plan.get("external_collection") or "completed"),
                "started_at": self.collection_snapshot().get("started_at", ""),
                "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "collected_new_jobs": int(plan.get("collected_new_jobs") or 0),
                "local_jobs": int(plan.get("local_jobs") or 0),
                "queries": list(plan.get("queries") or []),
                "error": str(plan.get("external_collection_error") or ""),
            }
        except Exception as exc:
            completed = {
                "status": "stopped",
                "started_at": self.collection_snapshot().get("started_at", ""),
                "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "collected_new_jobs": 0,
                "error": f"{type(exc).__name__}: {exc}",
            }
        try:
            with self.collection_lock:
                self.collection_state = completed
        finally:
            self.browser_lease.release("resume-collect")
            self.calibration_operation_lock.release()


class ReviewRequestHandler(BaseHTTPRequestHandler):
    server: ReviewHttpServer

    def do_GET(self) -> None:
        request_url = urlsplit(self.path)
        path = request_url.path
        query = parse_qs(request_url.query)
        if path == "/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        if path == "/api/send/precheck":
            try:
                self._send_json(HTTPStatus.OK, {"precheck": self.server.send_precheck()})
            except Exception as exc:  # 预检失败也要给前端结构化结果，不能 500
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "precheck": {
                            "ok": False,
                            "cdp": False,
                            "zhipin_page": False,
                            "logged_in": False,
                            "detail": f"预检执行失败：{exc}",
                        }
                    },
                )
            return
        if path == "/api/learning":
            self._learning_dashboard()
            return
        if path == "/api/outreach":
            self._outreach_call(lambda service: {"jobs": service.list_candidates()})
            return
        if path.startswith("/api/outreach/"):
            parts = [unquote(value) for value in path.split("/") if value]
            if len(parts) == 3:
                self._outreach_call(
                    lambda service: {"outreach": service.get(
                        str(query.get("platform", ["boss"])[0]), parts[2]
                    )}
                )
                return
            if len(parts) == 5 and parts[3] == "artifact" and parts[4] in {"docx", "image"}:
                self._send_outreach_artifact(
                    parts[2], parts[4], str(query.get("platform", ["boss"])[0])
                )
                return
            if len(parts) == 4 and parts[3] == "send":
                self._outreach_send_status(
                    parts[2], str(query.get("platform", ["boss"])[0])
                )
                return
            if len(parts) == 4 and parts[3] == "progress":
                self._progress_read(parts[2], str(query.get("platform", ["boss"])[0]))
                return
        if path == "/api/freshness":
            self._freshness_dashboard()
            return
        if path == "/api/job-updates/plan":
            self._job_update_plan()
            return
        if path == "/api/job-updates/latest":
            store = self.server.job_update_store()
            try:
                task = store.latest()
            finally:
                store.close()
            self._send_json(HTTPStatus.OK, {"job_update": task})
            return
        if path.startswith("/api/job-updates/"):
            run_id = unquote(path.removeprefix("/api/job-updates/"))
            if run_id:
                self._job_update_status(run_id)
                return
        if path == "/api/boundary-calibration":
            self._boundary_dashboard()
            return
        if path == "/api/calibration":
            self._calibration_dashboard()
            return
        if path == "/api/calibration/collection-status":
            self._send_json(
                HTTPStatus.OK,
                {"collection": self.server.collection_snapshot()},
            )
            return
        if path == "/api/matches":
            self._match_dashboard()
            return
        if path.startswith("/api/matches/"):
            self._match_detail(
                unquote(path.removeprefix("/api/matches/")),
                str(query.get("platform", [""])[0]) or None,
            )
            return
        if path == "/api/jobs":
            self._list_jobs()
            return
        if path.startswith("/api/jobs/"):
            self._get_job(
                unquote(path.removeprefix("/api/jobs/")),
                str(query.get("sample_name", ["positioning-v1"])[0]),
                str(query.get("platform", [""])[0]) or None,
            )
            return
        if path in STATIC_FILES:
            self._send_static(path)
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "页面不存在"})

    def do_POST(self) -> None:
        request_url = urlsplit(self.path)
        path = request_url.path
        query = parse_qs(request_url.query)
        if path.startswith("/api/outreach/"):
            parts = [unquote(value) for value in path.split("/") if value]
            if len(parts) == 4 and parts[3] in {"generate", "confirm", "asset"}:
                try:
                    payload = self._read_json(64_000)
                    platform = str(query.get("platform", ["boss"])[0])
                    if parts[3] == "generate":
                        self._outreach_call(
                            lambda service: {"outreach": service.generate(
                                platform, parts[2],
                                asset_id=str(payload.get("asset_id") or "") or None,
                            )}
                        )
                    elif parts[3] == "asset":
                        self._outreach_call(
                            lambda service: {"outreach": service.select_asset(
                                platform, parts[2], str(payload.get("asset_id") or "")
                            )}
                        )
                    else:
                        self._outreach_call(
                            lambda service: {"outreach": service.confirm(
                                platform, parts[2], str(payload.get("kind") or "")
                            )}
                        )
                except (ValueError, json.JSONDecodeError, OutreachValidationError) as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            if len(parts) == 4 and parts[3] == "authorize":
                platform = str(query.get("platform", ["boss"])[0])
                self._send_call(
                    lambda service: {"send": service.authorize(platform, parts[2])}
                )
                return
            if len(parts) == 5 and parts[4] == "resolve":
                platform = str(query.get("platform", ["boss"])[0])
                try:
                    payload = self._read_json(64_000)
                    self._send_call(
                        lambda service: {"send": service.resolve_and_continue(
                            platform,
                            parts[2],
                            greeting_sent=bool(payload.get("greeting_sent")),
                            note=str(payload.get("note") or ""),
                        )}
                    )
                except SendBusy as exc:
                    self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})
                except (ValueError, json.JSONDecodeError, SendValidationError) as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            if len(parts) == 4 and parts[3] == "progress":
                try:
                    payload = self._read_json(64_000)
                    platform = str(query.get("platform", ["boss"])[0])
                    store = self.server.progress_store()
                    try:
                        event = store.add(
                            platform,
                            parts[2],
                            str(payload.get("event_type") or ""),
                            note=str(payload.get("note") or ""),
                            evidence=str(payload.get("evidence") or ""),
                            source=str(payload.get("source") or "manual"),
                        )
                    finally:
                        store.close()
                    self._send_json(HTTPStatus.CREATED, {"progress_event": event})
                except (ValueError, json.JSONDecodeError, ProgressValidationError) as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            if len(parts) == 5 and parts[3] == "progress" and parts[4] == "confirm":
                try:
                    payload = self._read_json(64_000)
                    store = self.server.progress_store()
                    try:
                        event = store.confirm(int(payload.get("event_id") or 0))
                    finally:
                        store.close()
                    self._send_json(HTTPStatus.OK, {"progress_event": event})
                except (ValueError, json.JSONDecodeError, ProgressValidationError) as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            if len(parts) == 5 and parts[2] == "send" and parts[4] == "execute":
                self._send_call(lambda service: {"send": service.execute(parts[3])})
                return
        if path == "/api/job-updates":
            try:
                payload = self._read_json(64_000)
                store = self.server.job_update_store()
                try:
                    task = store.start(payload)
                finally:
                    store.close()
                self._send_json(HTTPStatus.ACCEPTED, {"job_update": task})
            except (ValueError, json.JSONDecodeError, ReviewValidationError) as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if path.startswith("/api/job-updates/") and path.endswith("/execute"):
            run_id = unquote(path.removeprefix("/api/job-updates/").removesuffix("/execute"))
            try:
                if int(self.headers.get("Content-Length", "0") or 0):
                    self._read_json(64_000)
                task = self.server.execute_job_update(run_id)
                self._send_json(HTTPStatus.OK, {"job_update": task})
            except BrowserLeaseBusy as exc:
                self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})
            except (ValueError, json.JSONDecodeError, ReviewValidationError) as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if path.startswith("/api/job-updates/") and path.endswith("/stop"):
            run_id = unquote(path.removeprefix("/api/job-updates/").removesuffix("/stop"))
            try:
                store = self.server.job_update_store()
                try:
                    task = store.stop(run_id)
                finally:
                    store.close()
                if task is None:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "更新任务不存在"})
                else:
                    self._send_json(HTTPStatus.OK, {"job_update": task})
            except (ValueError, ReviewValidationError) as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if path == "/api/matches/refresh-stale":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length > 64_000:
                    raise ReviewValidationError("刷新范围设置过大")
                payload = json.loads(self.rfile.read(content_length) or b"{}")
                if not isinstance(payload, dict):
                    raise ReviewValidationError("提交内容格式不正确")
                limit = int(payload.get("limit") or 20)
                if limit < 1 or limit > 100:
                    raise ReviewValidationError("单次最多重算 100 条岗位")
                self._send_json(
                    HTTPStatus.OK,
                    refresh_stale_matches(self.server.db_path, limit=limit),
                )
            except (ValueError, json.JSONDecodeError, ReviewValidationError) as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if path not in {"/api/calibration", "/api/calibration/collect"}:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "接口不存在"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length > 1_000_000:
                raise ReviewValidationError("简历内容过大")
            payload = json.loads(self.rfile.read(content_length) or b"{}")
            if not isinstance(payload, dict):
                raise ReviewValidationError("提交内容格式不正确")
            resume_text = str(payload.get("resume_text") or "")
            filename = str(payload.get("filename") or "resume.md")
            suffix = Path(filename).suffix.casefold()
            if suffix not in {".md", ".txt", ".markdown"}:
                raise ReviewValidationError("当前支持 Markdown 或纯文本简历")
            candidate_id = str(payload.get("candidate_id") or "local-candidate")
            analysis_version = str(payload.get("analysis_version") or "resume-match-v1")
            if path == "/api/calibration/collect":
                started = self.server.start_resume_collection(
                    {
                        "resume_text": resume_text,
                        "filename": filename,
                        "candidate_id": candidate_id,
                        "analysis_version": analysis_version,
                    }
                )
                self._send_json(HTTPStatus.ACCEPTED, {"collection": started})
                return
            dashboard = self.server.calibrate_resume(
                {
                    "resume_text": resume_text,
                    "filename": filename,
                    "candidate_id": candidate_id,
                    "analysis_version": analysis_version,
                }
            )
            self._send_json(HTTPStatus.OK, dashboard)
        except (ValueError, json.JSONDecodeError, ReviewValidationError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def do_PUT(self) -> None:
        request_url = urlsplit(self.path)
        path = request_url.path
        query = parse_qs(request_url.query)
        if path.startswith("/api/outreach/"):
            parts = [unquote(value) for value in path.split("/") if value]
            if len(parts) == 4 and parts[3] in {"greeting", "resume"}:
                try:
                    payload = self._read_json(500_000)
                    platform = str(query.get("platform", ["boss"])[0])
                    if parts[3] == "greeting":
                        def action(service):
                            return {"outreach": service.update_greeting(
                                platform, parts[2], str(payload.get("text") or "")
                            )}
                    else:
                        def action(service):
                            return {"outreach": service.update_resume(
                                platform, parts[2], str(payload.get("markdown") or "")
                            )}
                    self._outreach_call(action)
                except (ValueError, json.JSONDecodeError, OutreachValidationError) as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
        if path == "/api/calibration/preferences":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length > 64_000:
                    raise ReviewValidationError("偏好设置内容过大")
                payload = json.loads(self.rfile.read(content_length) or b"{}")
                if not isinstance(payload, dict):
                    raise ReviewValidationError("提交内容格式不正确")
                dashboard = self.server.confirm_preferences(payload)
                self._send_json(HTTPStatus.OK, dashboard)
            except (ValueError, json.JSONDecodeError, ReviewValidationError) as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        sample_name = str(query.get("sample_name", ["positioning-v1"])[0])
        platform = str(query.get("platform", [""])[0]) or None
        if not path.startswith("/api/jobs/"):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "接口不存在"})
            return
        is_correction = path.endswith("/correction")
        job_path = path.removeprefix("/api/jobs/")
        if is_correction:
            job_path = job_path.removesuffix("/correction")
        job_id = unquote(job_path)
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(content_length) or b"{}")
            if not isinstance(payload, dict):
                raise ReviewValidationError("提交内容格式不正确")
        except (ValueError, json.JSONDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "提交内容不是有效 JSON"})
            return

        store = ReviewStore(self.server.db_path)
        try:
            if is_correction:
                correction = store.save_correction(
                    job_id,
                    payload,
                    sample_name=sample_name,
                    platform=platform,
                )
                self._send_json(HTTPStatus.OK, {"correction": correction})
            else:
                decision = store.save_decision(
                    job_id,
                    payload,
                    sample_name=sample_name,
                    platform=platform,
                )
                self._send_json(HTTPStatus.OK, {"decision": decision})
        except ReviewValidationError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        finally:
            store.close()

    def _list_jobs(self) -> None:
        store = ReviewStore(self.server.db_path)
        try:
            jobs = store.ensure_sample()
            self._send_json(
                HTTPStatus.OK,
                {"jobs": jobs, "stats": store.stats()},
            )
        except ReviewValidationError as exc:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})
        finally:
            store.close()

    def _get_job(
        self,
        job_id: str,
        sample_name: str = "positioning-v1",
        platform: str | None = None,
    ) -> None:
        store = ReviewStore(self.server.db_path)
        try:
            job = store.get_job(job_id, sample_name=sample_name, platform=platform)
            if job is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "岗位不在指定审阅样本中"})
                return
            self._send_json(HTTPStatus.OK, {"job": job})
        except ReviewValidationError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        finally:
            store.close()

    def _learning_dashboard(self) -> None:
        store = ReviewStore(self.server.db_path)
        try:
            self._send_json(HTTPStatus.OK, store.learning_dashboard())
        finally:
            store.close()

    def _freshness_dashboard(self) -> None:
        store = ReviewStore(self.server.db_path)
        try:
            self._send_json(HTTPStatus.OK, store.freshness_dashboard())
        finally:
            store.close()

    def _job_update_plan(self) -> None:
        try:
            self._send_json(HTTPStatus.OK, {"job_update": plan_from_database(self.server.db_path, self.server.config)})
        except (ValueError, OSError) as exc:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})

    def _job_update_status(self, run_id: str) -> None:
        store = self.server.job_update_store()
        try:
            task = store.get(run_id)
        finally:
            store.close()
        if task is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "更新任务不存在"})
        else:
            self._send_json(HTTPStatus.OK, {"job_update": task})

    def _boundary_dashboard(self) -> None:
        store = ReviewStore(self.server.db_path)
        try:
            self._send_json(HTTPStatus.OK, store.boundary_dashboard())
        finally:
            store.close()

    def _calibration_dashboard(self) -> None:
        store = ReviewStore(self.server.db_path)
        try:
            self._send_json(HTTPStatus.OK, store.calibration_dashboard())
        except ValueError as exc:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})
        finally:
            store.close()

    def _match_dashboard(self) -> None:
        store = ReviewStore(self.server.db_path)
        try:
            self._send_json(HTTPStatus.OK, store.match_dashboard())
        finally:
            store.close()

    def _match_detail(self, job_id: str, platform: str | None = None) -> None:
        store = ReviewStore(self.server.db_path)
        try:
            match = store.match_detail(job_id, platform=platform)
            if match is None:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error": "岗位不在当前匹配预览中，请先重新生成预览"},
                )
                return
            self._send_json(HTTPStatus.OK, {"match": match})
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        finally:
            store.close()

    def _send_static(self, path: str) -> None:
        filename, content_type = STATIC_FILES[path]
        file_path = self.server.ui_dir / filename
        if not file_path.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "界面资源缺失"})
            return
        body = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self, limit: int) -> dict:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length > limit:
            raise OutreachValidationError("提交内容过大")
        payload = json.loads(self.rfile.read(content_length) or b"{}")
        if not isinstance(payload, dict):
            raise OutreachValidationError("提交内容格式不正确")
        return payload

    def _outreach_call(self, action) -> None:
        service = None
        try:
            service = self.server.outreach_service()
            self._send_json(HTTPStatus.OK, action(service))
        except OutreachValidationError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"投递准备失败：{type(exc).__name__}: {exc}"},
            )
        finally:
            if service is not None:
                service.close()

    def _send_call(self, action) -> None:
        service = None
        try:
            service = self.server.send_service()
            self._send_json(HTTPStatus.OK, action(service))
        except SendValidationError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except SendBusy as exc:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})
        except Exception as exc:
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": f"受控发送失败：{type(exc).__name__}: {exc}"},
            )
        finally:
            if service is not None:
                service.close()

    def _outreach_send_status(self, job_id: str, platform: str) -> None:
        self._send_call(lambda service: {"send": service.status_for(platform, job_id)})

    def _progress_read(self, job_id: str, platform: str) -> None:
        store = self.server.progress_store()
        try:
            data = store.summary(platform, job_id)
        finally:
            store.close()
        self._send_json(HTTPStatus.OK, {"progress": data})

    def _send_outreach_artifact(self, job_id: str, kind: str, platform: str) -> None:
        service = None
        try:
            service = self.server.outreach_service()
            path = service.artifact(platform, job_id, kind)
            body = path.read_bytes()
            content_type = (
                "image/jpeg"
                if kind == "image"
                else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )
            # HTTP 头只能 latin-1；中文文件名必须按 RFC 5987 用 filename* 编码，
            # 否则 UnicodeEncodeError 会直接掐断连接，前端只剩破图占位。
            ascii_fallback = path.name.encode("ascii", "ignore").decode("ascii") or "artifact"
            utf8_name = quote(path.name, safe="")
            disposition = (
                f"inline; filename=\"{ascii_fallback}\"; filename*=UTF-8''{utf8_name}"
            )
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", disposition)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except OutreachValidationError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        finally:
            if service is not None:
                service.close()

    def _send_json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        return


def create_server(
    db_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    ui_dir: str | Path = UI_DIR,
    config: dict | None = None,
    collection_runner: Callable = collect_resume_market_data,
    analysis_output_dir: str | Path | None = None,
    outreach_service_factory: Callable | None = None,
    send_service_factory: Callable | None = None,
    job_update_executor: JobUpdateExecutor | None = None,
) -> ReviewHttpServer:
    database = migrate_review_database(db_path)
    build_outreach_service: Callable | None = None
    if config:
        def build_outreach_service():
            resume_cfg = config.get("resume") or {}
            resume_path = resolve_project_path(
                resume_cfg.get("master_file", "resume/AI-Product-Builder-Resume.scoring.md")
            )
            template_path = resolve_project_path(
                resume_cfg.get("word_template", "resume/Ai-product-builder-v10.docx")
            )
            output_dir = resolve_project_path(resume_cfg.get("output_dir", "output/tailored"))
            assets = normalize_resume_assets(resume_cfg.get("assets"))
            def load_job(platform, job_id):
                review_store = ReviewStore(database)
                try:
                    return review_store.match_detail(job_id, platform)
                finally:
                    review_store.close()

            def load_candidates():
                review_store = ReviewStore(database)
                try:
                    return review_store.match_dashboard().get("jobs", [])
                finally:
                    review_store.close()

            return OutreachService(
                database, resume_path=resume_path,
                job_loader=load_job,
                candidate_loader=load_candidates,
                ai_factory=lambda: create_ai_client(config.get("ai") or {}),
                knowledge=create_provider(config.get("knowledge")),
                renderer=TailoredResumeRenderer(
                    template_path,
                    output_dir,
                    image_width=int(resume_cfg.get("image_width", 1400)),
                ),
                assets=assets,
            )

    if outreach_service_factory is None and build_outreach_service is not None:
        outreach_service_factory = build_outreach_service

    if send_service_factory is None and build_outreach_service is not None:
        def _default_send_service_factory(lease):
            def package_provider(platform: str, job_id: str):
                outreach = build_outreach_service()
                try:
                    return outreach.get(platform, job_id)
                finally:
                    outreach.close()

            return OutreachSendService(
                database,
                package_provider=package_provider,
                sender=BossSender(config.get("boss") or {}),
                lease=lease,
            )

        send_service_factory = _default_send_service_factory

    return ReviewHttpServer(
        (host, port),
        ReviewRequestHandler,
        database,
        Path(ui_dir).resolve(),
        config=config,
        collection_runner=collection_runner,
        analysis_output_dir=(
            Path(analysis_output_dir).resolve() if analysis_output_dir else None
        ),
        outreach_service_factory=outreach_service_factory,
        send_service_factory=send_service_factory,
        job_update_executor=job_update_executor,
    )


def run_review_server(
    db_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    config: dict | None = None,
    analysis_output_dir: str | Path | None = None,
) -> None:
    server = create_server(
        db_path,
        host=host,
        port=port,
        config=config,
        analysis_output_dir=analysis_output_dir,
    )
    print(f"岗位定位审阅台：http://{host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
