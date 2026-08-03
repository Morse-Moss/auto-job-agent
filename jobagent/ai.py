"""DeepSeek 封装：岗位匹配打分 / 个性化打招呼语 / 按 JD 定制简历。

DeepSeek API 与 OpenAI SDK 兼容，base_url 指向 https://api.deepseek.com 即可。
"""

import json

from openai import OpenAI


class DeepSeekClient:
    def __init__(self, cfg: dict):
        self.client = OpenAI(
            api_key=cfg["api_key"],
            base_url=cfg.get("base_url", "https://api.deepseek.com"),
        )
        self.model = cfg.get("model", "deepseek-chat")

    def _chat(self, system: str, user: str, temperature: float = 0.7) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
        )
        return (resp.choices[0].message.content or "").strip()

    # ------------------------------------------------------------------
    def match_score(self, resume_md: str, job: dict) -> tuple[int, str]:
        """对岗位与简历的匹配度打分。返回 (0-100 分, 一句话理由)。"""
        system = (
            "你是资深技术招聘顾问。根据候选人简历与岗位JD评估匹配度。"
            "评分标准：技能栈重合度(40%)、经验年限与岗位要求(30%)、行业/业务契合(20%)、加分项(10%)。"
            '只输出JSON，格式：{"score": <0-100整数>, "reason": "<一句话理由>"}'
        )
        user = (
            f"【候选人简历】\n{resume_md}\n\n"
            f"【岗位】{job['title']} | {job.get('company', '')} | {job.get('salary', '')}\n"
            f"【JD】\n{job['jd']}"
        )
        raw = self._chat(system, user, temperature=0.2)
        try:
            data = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])
            return max(0, min(100, int(data["score"]))), str(data.get("reason", ""))
        except (ValueError, KeyError, json.JSONDecodeError):
            return 0, f"AI输出无法解析: {raw[:120]}"

    # ------------------------------------------------------------------
    def greeting(self, resume_md: str, job: dict) -> str:
        """生成发给招聘者的第一句打招呼语。"""
        system = (
            "你是求职者本人，正在Boss直聘上给招聘者发第一条消息。要求：\n"
            "1. 80-120字，自然口语化，不油腻不卑微，不用'贵司'等书面腔\n"
            "2. 开头一句点明你与该岗位最匹配的1-2个硬技能/经历（用数字更佳）\n"
            "3. 结尾表达希望进一步沟通\n"
            "4. 不编造简历中没有的经历\n"
            "只输出消息正文，不要任何解释或引号。"
        )
        user = (
            f"【我的简历】\n{resume_md}\n\n"
            f"【目标岗位】{job['title']} | {job.get('company', '')}\n"
            f"【JD】\n{job['jd']}"
        )
        return self._chat(system, user, temperature=0.8)

    # ------------------------------------------------------------------
    def tailor_resume(self, resume_md: str, job: dict) -> str:
        """根据 JD 生成定制版简历（Markdown）。"""
        system = (
            "你是专业简历顾问。根据目标岗位JD，把候选人的母版简历改写为针对该岗位的定制版：\n"
            "1. 重排并突出与JD最相关的技能与项目经历，弱化无关内容\n"
            "2. 用JD中的关键词改写描述（利于HR搜索与ATS筛选），但严禁编造事实\n"
            "3. 每段经历用'动词+成果+数字'的表达\n"
            "4. 开头加一段3行以内的'核心匹配点'摘要\n"
            "输出完整Markdown简历，不要解释。"
        )
        user = (
            f"【母版简历】\n{resume_md}\n\n"
            f"【目标岗位】{job['title']} | {job.get('company', '')}\n"
            f"【JD】\n{job['jd']}"
        )
        return self._chat(system, user, temperature=0.5)
