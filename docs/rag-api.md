# RAG 检索接口契约（knowledge.provider: rag）

auto-job-agent 不关心知识库的具体实现，只要求对方暴露一个符合本契约的 HTTP 接口。
任何系统（Morse 数字分身、自建向量库、Dify/FastGPT 等）实现该契约即可接入；
没有知识库的用户把 `knowledge.provider` 设为 `none` 即可，功能完全可选。

## 请求

```
POST {knowledge.rag.endpoint}
Content-Type: application/json
Authorization: Bearer <api_key>    # 可选，配置了 knowledge.rag.api_key 时携带

{
  "query": "<岗位名 公司名 + JD 前 1500 字>",
  "top_k": 6
}
```

## 响应

```
200 OK
{
  "results": [
    { "text": "知识片段正文", "source": "来源标识（可选）", "score": 0.87 }
  ]
}
```

- `results` 按相关度降序；`text` 必填，`source` / `score` 可选
- 非 200、超时或格式错误：agent 打印警告，该岗位降级为无知识模式，投递流程不中断

## 与 Morse (Self-Website) 对接建议

Morse 已有完整 RAG 链路（本地 BGE Embeddings + PostgreSQL/pgvector，检索范围为
`content/site-content.json` 审核后的公开知识）。建议在 Morse 侧新增一个内部检索路由：

- 路径：`app/api/internal/rag/search/route.ts`
- 实现：复用 `lib/server` 中现有的向量检索逻辑，只做「检索并返回片段」，
  不走对话工作流、不消耗访客额度、不产生会话
- 安全边界（遵守 Morse 既有设计）：
  - 仅本机/内网监听，或校验 Bearer token（token 走环境变量注入，不进仓库）
  - 检索范围仅限审核后的公开知识源；私密简历与 `content/drafts/` 不进入检索结果
  - 不在响应中暴露 Provider、表名、知识库规模等运维信息
- 私密补充材料（薪资期望、内部项目数据等）不要放进 Morse 公开内容源，
  应写入本地母版简历，或用 local 模式的 `knowledge/` 目录（已被 .gitignore 忽略）
