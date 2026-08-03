import re
from pathlib import Path


class ResumeManager:
    def __init__(self, cfg: dict):
        self.master_file = Path(cfg["master_file"])
        self.output_dir = Path(cfg.get("output_dir", "output/tailored"))
        if not self.master_file.exists():
            raise SystemExit(
                f"未找到母版简历 {self.master_file}。请先执行:\n"
                "  cp resume/master_resume.example.md resume/master_resume.md\n"
                "并填写你的真实简历。"
            )

    @property
    def master(self) -> str:
        return self.master_file.read_text(encoding="utf-8")

    def save_tailored(self, job: dict, tailored_md: str) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", f"{job.get('company', '')}-{job['title']}")[:60]
        path = self.output_dir / f"{safe}.md"
        path.write_text(tailored_md, encoding="utf-8")
        return path
