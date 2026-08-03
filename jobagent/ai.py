"""DeepSeek 封装：岗位匹配打分 / 个性化打招呼语 / 按 JD 定制简历。

三个能力的生成依据：
- match_score: 求职要求(preferences) + 母版简历 + JD
- greeting / tailor_resume: 母版简历 + 个人知识库(knowledge) + JD

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
    def match_score(self, resume_md: str, job: dict, preferences: str = "") -> tuple[int, str]:
        """综合求职要求与简历，对岗位打分。返回 (0-100 分, 一句话理由)。"""
        system = (
            "你是资深技术招聘顾问，为候选人把关筛选岗位。评分规则（求职要求优先于匹配度）：\n"
            "1. 岗位命中候选人【坚决排除】项：直接给 0-20 分\n"
            "2. 不满足【硬性要求】：最高只能给 40 分\n"
            "3. 以上都通过后，按简历匹配度评分：技能栈重合(40%) + 经验年限(30%) + 行业业务契合(20%) + 加分项(10%)\n"
            "4. 命中【优先加分】项可加 5-10 分\n"
            '只输出JSON：{"score": <0-100整数>, "reason": "<一句话理由，说明关键加减分因素>"}'
        )
        user = (
            f"【候选人求职要求】\n{preferences.strip() or '（未填写，仅按简历匹配度评分）'}\n\n"
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
    def greeting(self, resume_md: str, job: dict, knowledge: str = "") -> str:
        """生成发给招聘者的第一句打招呼语（以知识库为事实依据）。"""
        system = (
            "你是求职者本人，正在Boss直聘上给招聘者发第一条消息。要求：\n"
            "1. 80-120字，自然口语化，不油腻不卑微，不用敬语套话\n"
            "2. 开头一句点明你与该岗位最匹配的1-2个硬技能或经历，优先引用【个人知识库】中的具体项目成果和数字，其次才是简历\n"
            "3. 结尾表达希望进一步沟通\n"
            "4. 严禁编造简历和知识库中都没有的经历\n"
            "只输出消息正文，不要任何解释或引号。"
        )
        user = (
            f"【我的简历】\n{resume_md}\n\n"
            f"【个人知识库】\n{knowledge.strip() or '（无）'}\n\n"
            f"【目标岗位】{job['title']} | {job.get('company', '')}\n"
            f"【JD】\n{job['jd']}"
        )
        return self._chat(system, user, temperature=0.8)

    # ------------------------------------------------------------------
    def tailor_resume(self, resume_md: str, job: dict, knowledge: str = "") -> str:
        """根据 JD 生成定制版简历（Markdown），可用知识库补充真实细节。"""
        system = (
            "你是专业简历顾问。根据目标岗位JD，把候选人的母版简历改写为针对该岗位的定制版：\n"
            "1. 重排并突出与JD最相关的技能与项目经历，弱化无关内容\n"
            "2. 可从【个人知识库】中补充简历没写但真实存在的项目细节与量化成果\n"
            "3. 用JD中的关键词改写描述（利于HR搜索与ATS筛选），但严禁编造事实\n"
            "4. 每段经历用动词+成果+数字的表达\n"
            "5. 开头加一段3行以内的核心匹配点摘要\n"
            "输出完整Markdown简历，不要解释。"
        )
        user = (
            f"【母版简历】\n{resume_md}\n\n"
            f"【个人知识库】\n{knowledge.strip() or '（无）'}\n\n"
            f"【目标岗位】{job['title']} | {job.get('company', '')}\n"
            f"【JD】\n{job['jd']}"
        )
        return self._chat(system, user, temperature=0.5)
