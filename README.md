# Scholar Service

`scholar-service` 是一个独立、可测试的 FastAPI 微服务原型，为 PharmAgents
提供统一文献搜索和合法开放全文获取能力。它不会访问 Sci-Hub，也不会绕过付费墙。

## 能力与数据源

- Scholar Search：并行查询 OpenAlex、Europe PMC、PubMed、Semantic Scholar、
  bioRxiv、medRxiv 和 arXiv，
  统一字段后按 DOI、PMID、PMCID 和标题相似度去重，再进行规则排序和可选
  Qwen rerank。
- Scholar Fetch：优先 Europe PMC/PMC JATS XML，然后尝试 Unpaywall OA location
  和 Crossref 链接；格式优先 XML、HTML、PDF，失败时只返回真实 abstract。
  支持批量输入 DOI、PMID、PMCID、OpenAlex ID 或 title。
- Pipeline：保留为 demo/debug endpoint。正式产品流程建议是 `/scholar/search` →
  Agent 阅读 title/abstract 并选择论文 → `/scholar/fetch`，不要默认 fetch 搜索到的
  所有论文。

| 数据源 | Search | Fetch/resolve | 配置 |
|---|---:|---:|---|
| OpenAlex | 是 | content API 预留 | 搜索无需 key；content 需 key |
| Europe PMC | 是 | JATS XML | 无需 key |
| PubMed E-utilities | 是 | ID/metadata | 建议配置 `NCBI_EMAIL` |
| Semantic Scholar | 是 | OA PDF hint | key 可选 |
| bioRxiv | 是 | Europe PMC/JATS/TDM XML、HTML、PDF、abstract | 官方 metadata feed；预印本 |
| medRxiv | 是 | Europe PMC/JATS/TDM XML、HTML、PDF、abstract | 官方 metadata feed；预印本 |
| arXiv | 是 | abstract + OA PDF | 官方 Atom API；预印本 |
| Unpaywall | 否 | DOI → OA location | 需要真实邮箱 |
| Crossref | 否 | metadata/link fallback | 无需 key |

## 安装

需要 Python 3.10+：

```bash
cd scholar-service
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
```

建议始终通过当前 Python 启动 pip 和 pytest，避免 Conda、系统 Python 与虚拟环境混用：

```bash
python -m pip install -e '.[dev]'
python -m pytest -m "not live"
```

可用下面的命令确认三者指向同一个 `.venv`：

```bash
which python
python -m pip --version
python -m pytest --version
```

请至少把 `.env` 中的 `NCBI_EMAIL` 改成可联系邮箱。使用 Unpaywall 时还要设置
`UNPAYWALL_EMAIL`。`SEMANTIC_SCHOLAR_API_KEY` 和 `OPENALEX_API_KEY` 均可留空；
`ENABLE_OPENALEX_CONTENT` 默认关闭。

主要配置：

```text
HTTP_TIMEOUT_SECONDS=20
MAX_CONCURRENT_REQUESTS=8
CACHE_DIR=.cache/scholar
ENABLE_PDF_FETCH=true
RERANKER_TYPE=none
SOURCE_TOP_K_PER_PROVIDER=30
PREPRINT_SOURCE_TOP_K_PER_PROVIDER=20
MAX_CANDIDATES_AFTER_DEDUP=200
QWEN_RERANK_TOP_K=200
RERANK_TIMEOUT_SECONDS=20
QWEN_API_KEY=
QWEN_RERANK_BASE_URL=https://api.qingyuntop.top
QWEN_RERANK_PATH=/v1/rerank
QWEN_RERANK_MODEL=qwen3-rerank
QWEN_RERANK_INSTRUCT=Given a web search query, retrieve relevant passages that answer the query.
```

缓存使用本地 SQLite，不需要额外服务。GROBID 配置已预留，但 v0.1 不依赖它。

`RERANKER_TYPE=none` 时使用本地规则排序。设为 `qwen` 且提供
`QWEN_API_KEY` 后，Qwen reranker 会作为 `/scholar/search` 的主排序器。
`/scholar/search` 的 `max_results` 表示最终返回给 Agent 阅读的 ranked papers
数量，范围为 1-30；它不控制每个 provider 的召回数量。Search 会先让正式文献
provider 独立召回 `SOURCE_TOP_K_PER_PROVIDER` 篇，让 preprint
provider（bioRxiv、medRxiv、arXiv）独立召回
`PREPRINT_SOURCE_TOP_K_PER_PROVIDER` 篇，normalize + dedup 后做 basic eligibility
filtering，并用 `MAX_CANDIDATES_AFTER_DEDUP` 限制候选池大小。默认情况下，4 个
正式源 × 30 + 3 个 preprint 源 × 20，最大 raw candidates 为 180。
如果候选数超过 `QWEN_RERANK_TOP_K`，只做非常轻量的 safety trimming 后送入 Qwen，
不会先用复杂 local ranking 决定 top candidates。

Qwen 调用青云专用 rerank endpoint：
`QWEN_RERANK_BASE_URL.rstrip("/") + QWEN_RERANK_PATH`，默认是
`https://api.qingyuntop.top/v1/rerank`。请求体使用
`model`、`documents`、`query`、`top_n` 和 `instruct`，其中 `documents` 只包含候选
论文的 title、abstract 和少量 metadata，不传全文。API key 只从环境变量读取，不会
写入日志；reranker 超时、限流、5xx、网络错误或返回格式异常时，服务会在
`warnings` 中记录 `Qwen reranker failed`，并自动 fallback 到 local ranking，不影响
`/scholar/search` 主流程。

## 启动

```bash
uvicorn app.main:app --reload --port 8000
```

健康检查：

```bash
curl http://localhost:8000/health
```

搜索：

```bash
curl -X POST http://localhost:8000/scholar/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "KRAS G12C sotorasib resistance mechanisms",
    "max_results": 10,
    "sources": [
      "openalex", "europepmc", "semantic_scholar", "pubmed",
      "biorxiv", "medrxiv", "arxiv"
    ],
    "from_year": 2020,
    "to_year": 2026
  }'
```

`/scholar/search` 内部流程：

```text
multi-source recall
> metadata normalization
> conservative dedup/merge
> basic eligibility filtering
> Qwen rerank as primary sorter if enabled
> local ranking fallback if Qwen is disabled or fails
```

默认返回已经排好序的 papers。正式响应不暴露 `rerank_score` 或 `ranking_debug`；
Agent 主要读取 `title`、`abstract`、`year`、`journal`、`authors`、`doi`、`pmid`、
`pmcid`、`openalex_id`、`semantic_scholar_id`、`citation_count`、
`is_open_access`、`has_full_text` 等字段。

只查询预印本：

```bash
curl -X POST http://localhost:8000/scholar/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "latest protein foundation model",
    "max_results": 20,
    "sources": ["biorxiv", "medrxiv", "arxiv"]
  }'
```

预印本结果会显式返回：

```json
{
  "is_preprint": true,
  "peer_reviewed": false,
  "review_status": "preprint",
  "server": "biorxiv",
  "category": "bioinformatics",
  "landing_url": "https://...",
  "pdf_url": "https://..."
}
```

bioRxiv/medRxiv 官方 API 没有与 arXiv 等价的服务端关键词搜索端点。本服务从
官方日期/最近批次 metadata feed 获取候选，再在本地按标题和摘要过滤。因此，
未设置年份时主要覆盖最近 30 天；历史检索建议设置 `from_year` 和 `to_year`。

bioRxiv/medRxiv 全文获取顺序固定为：

```text
Europe PMC fullTextXML（DOI 能映射到 PMCID 时）
> bioRxiv/medRxiv API 提供的 JATS XML 或 TDM XML
> full-text HTML
> PDF parsing
> abstract only
```

并非所有预印本都能通过简单 API 或稳定 URL 获取 XML。Provider 只使用官方
metadata 中实际返回的 `jatsxml`、`tdmxml` 等路径；XML 不存在或解析失败时才继续
尝试 HTML 和 PDF。

Fetch routing 会严格验证标识符：PMCID 必须匹配 `^PMC\d+$`；arXiv ID 必须是
`2601.01234v2` 或旧式 `hep-th/9901001v1` 等正式格式。OpenAPI 示例中的
`string`/`STRING` 会被忽略，不会触发 arXiv 或 Europe PMC 请求。

bioRxiv/medRxiv XML URL 优先来自官方 metadata 或 landing page 中实际暴露的
`citation_xml_url`、XML link。服务会清理路径中的重复斜杠，但不会凭发布日期和
DOI 猜测 `early/...source.xml` 路径。

抓取 Europe PMC 全文：

```bash
curl -X POST http://localhost:8000/scholar/fetch \
  -H "Content-Type: application/json" \
  -d '{"pmcid":"PMC9715446","prefer_formats":["xml","html","pdf"],"max_chars":50000}'
```

批量抓取多篇论文：

```bash
curl -X POST http://localhost:8000/scholar/fetch \
  -H "Content-Type: application/json" \
  -d '{
    "papers": [
      {"doi": "10.xxxx/xxxx"},
      {"pmcid": "PMC9715446"},
      {"pmid": "36315377"}
    ],
    "prefer_formats": ["xml", "html", "pdf"],
    "allow_pdf": true,
    "max_chars_per_paper": 50000
  }'
```

批量模式每篇 paper 独立执行 fetch；单篇失败不会影响其他 paper。外层返回
`total`、`succeeded`、`failed` 和 `results`。旧的单篇输入格式继续可用，并返回
单篇对象。

Fetch 输出统一为 `content`：

```json
{
  "paper": {"title": "...", "doi": "...", "has_full_text": true},
  "full_text_status": "success",
  "retrieval": {
    "source": "europepmc",
    "format": "xml",
    "url": "https://..."
  },
  "content": "Abstract\n...\n\nIntroduction\n...\n\nMethods\n...",
  "warnings": []
}
```

对外响应不再返回 `sections` 或 `plain_text`。内部 XML/HTML/PDF parser 仍可以先解析
章节；最终会按 heading + text 合并成 `content`。HTML、PDF 和 abstract fallback
也统一返回 `content`，`retrieval.format` 继续明确标记为 `xml`、`html`、`pdf` 或
`abstract`。

Demo/debug pipeline：

```bash
curl -X POST http://localhost:8000/scholar/pipeline \
  -H "Content-Type: application/json" \
  -d '{
    "query": "KRAS G12C sotorasib resistance mechanisms",
    "max_search_results": 10,
    "fetch_top_n": 3,
    "require_full_text": true
  }'
```

也可在服务启动后运行：

```bash
python scripts/demo_search.py
python scripts/demo_fetch.py
```

## 测试

默认测试完全离线，provider HTTP 由 `respx` mock：

```bash
python -m pytest -m "not live"
```

真实 API 集成测试：

```bash
python -m pytest -m live
```

运行全部测试：

```bash
python -m pytest
```

## API 响应约定

单个搜索源超时或不可用时，其他数据源仍正常返回，问题记录在 `warnings`。
全文不可用不是 HTTP 错误：响应的 `full_text_status` 为 `abstract_only`，
`content` 只包含找到的真实摘要，绝不生成或猜测正文。

`/scholar/search` 对外只返回 Agent 选文献需要的精简 metadata，不返回
`full_text_candidates`、`source_hits`、`field_sources`、`metadata_conflicts`、
`score`、`query_relevance` 或 `biomedical_score`。这些字段仍可在服务内部用于
dedup、debug 和 ranking，但不会出现在 search response。

内部 metadata 调试信息形如：

```json
{
  "field_sources": {
    "title": ["openalex", "europepmc"],
    "doi": ["openalex", "europepmc"],
    "pmcid": ["europepmc"],
    "year": ["europepmc"]
  },
  "metadata_conflicts": [
    {
      "field": "year",
      "kept_value": "2024",
      "kept_source": "europepmc",
      "rejected_value": "2023",
      "rejected_source": "openalex"
    }
  ]
}
```

合并规则优先使用 Europe PMC、PubMed 等
生物医学权威来源；只要 DOI、PMID、PMCID 或 arXiv ID 存在明确冲突，就不会仅凭
相似标题合并。无强标识符时要求标题相似度至少 0.97、年份兼容并且作者有交集。
因此系统倾向于保留可能的重复项，而不是错误合并不同论文。

通过 PMCID 成功取得 Europe PMC XML 后，服务会从 Europe PMC metadata 和 JATS
front matter 补全 title、DOI、PMID、PMCID、作者、年份、期刊和预印本状态，同时
同步 `paper.has_full_text=true`、`paper.is_open_access=true`。

bioRxiv、medRxiv 和 arXiv 结果始终标记为预印本，不会被当作已经同行评审的正式
论文。Agent 在总结、比较或生成证据结论时必须显示其预印本身份，并降低证据等级。
当 `allow_pdf=true` 时服务可以尝试解析公开 PDF；关闭 PDF 或解析失败时，仍会返回
真实 abstract、`landing_url` 和 `pdf_url`，且 `retrieval.format` 为 `abstract`。
`retrieval.format` 始终明确为 `xml`、`html`、`pdf` 或 `abstract`。仅找到摘要和
链接时，`full_text_status` 保持 `abstract_only`，不会标记为 `success`。
若 XML 和 HTML 均失败而 PDF 成功，`retrieval.source` 会保持 `biorxiv` 或
`medrxiv`、`retrieval.format` 为 `pdf`，并在 `warnings` 中明确记录 PDF fallback。

排序默认对纯预印本施加轻微降权，避免其排在同等相关的正式论文之前。如果查询明确
包含 `latest`、`recent`、`preprint`、`new model`、`AI method`、`最新` 或
`预印本` 等时效性意图，会给予预印本适度加分，同时继续保留年份新近度评分。

## 当前限制

- 只支持公开且可合法访问的全文。
- Crossref full-text link 不保证实际可访问或确实开放。
- PDF 文本抽取不保留可靠的章节结构，扫描 PDF 也可能没有可提取文字。
- OpenAlex content API 需要 key，且服务条款/价格可能变化；本版仅预留配置。
- bioRxiv/medRxiv 关键词匹配在本地完成，超长历史区间需要后续增加分页抓取与索引。
- 预印本可能发生版本更新或最终发表；当前通过 published DOI、DOI 和标题相似度关联。
- 标题模糊匹配和规则排序适合原型，不等同于学习排序或系统评价。

## 下一步

- 接入 GROBID，改善 PDF 章节结构。
- 建立检索召回率、去重准确率和全文成功率 benchmark。
- 增加更多 biomedical 数据源与更细的许可证验证。
- 实现 PharmAgents tool interface 和结构化可观测性。
