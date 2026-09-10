"""Loopback-only HTTP server for the job positioning review workbench."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, unquote, urlsplit

from jobagent.resume_collection import collect_resume_market_data
from jobagent.ai import OpenAICompatibleClient
from jobagent.config import resolve_project_path
from jobagent.knowledge import create_provider
from jobagent.matching import refresh_stale_matches
from jobagent.jd_update import plan_from_database
from jobagent.job_updates import JobUpdateStore
from jobagent.outreach import (
    OutreachService,
    OutreachValidationError,
    TailoredResumeRenderer,
)
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
    ):
        super().__init__(address, handler)
        self.db_path = db_path
        self.ui_dir = ui_dir
        self.config = config or {}
        self.collection_runner = collection_runner
        self.analysis_output_dir = analysis_output_dir or db_path.parent / "analysis"
        self.outreach_service_factory = outreach_service_factory
        self.collection_lock = threading.Lock()
        self.calibration_operation_lock = threading.Lock()
        self.collection_state = {"status": "idle"}

    def outreach_service(self):
        if self.outreach_service_factory is None:
            raise OutreachValidationError("投递准备功能尚未配置")
        return self.outreach_service_factory()

    def collection_snapshot(self) -> dict:
        with self.collection_lock:
            return dict(self.collection_state)

    def job_update_store(self) -> JobUpdateStore:
        return JobUpdateStore(self.db_path, self.config)

    def start_resume_collection(self, payload: dict) -> dict:
        if not self.calibration_operation_lock.acquire(blocking=False):
            if self.collection_snapshot().get("status") == "running":
                raise ReviewValidationError("岗位采集正在运行，请等待当前任务结束")
            raise ReviewValidationError("简历校准正在运行，请等待当前任务结束")

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
        if path == "/api/freshness":
            self._freshness_dashboard()
            return
        if path == "/api/job-updates/plan":
            self._job_update_plan()
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
            if len(parts) == 4 and parts[3] in {"generate", "confirm"}:
                try:
                    payload = self._read_json(64_000)
                    platform = str(query.get("platform", ["boss"])[0])
                    if parts[3] == "generate":
                        self._outreach_call(
                            lambda service: {"outreach": service.generate(platform, parts[2])}
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
                        action = lambda service: {"outreach": service.update_greeting(
                            platform, parts[2], str(payload.get("text") or "")
                        )}
                    else:
                        action = lambda service: {"outreach": service.update_resume(
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
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", f'inline; filename="{path.name}"')
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
) -> ReviewHttpServer:
    database = migrate_review_database(db_path)
    if outreach_service_factory is None and config:
        def outreach_service_factory():
            resume_cfg = config.get("resume") or {}
            resume_path = resolve_project_path(
                resume_cfg.get("master_file", "resume/AI-Product-Builder-Resume.scoring.md")
            )
            template_path = resolve_project_path(
                resume_cfg.get("word_template", "resume/Ai-product-builder-v10.docx")
            )
            output_dir = resolve_project_path(resume_cfg.get("output_dir", "output/tailored"))
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
                ai_factory=lambda: OpenAICompatibleClient(config.get("ai") or {}),
                knowledge=create_provider(config.get("knowledge")),
                renderer=TailoredResumeRenderer(
                    template_path,
                    output_dir,
                    image_width=int(resume_cfg.get("image_width", 1400)),
                ),
            )

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
