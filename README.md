# SQL Copilot Agent

AI 驱动的 SQL 自动补全与查询助手（MVP+AI）。

## Features

- SQL AST 解析（`sqlglot`）
- Schema 感知补全（SQLite introspection）
- Alias 识别（`users u` -> `u.id`）
- 规则补全 + LLM 语义补全（OpenAI，可自动降级）
- LangGraph 工作流（Parse -> Schema -> LLM -> Rank）
- Monaco 最小可用前端

## Project Structure

```text
sql-copilot-agent
├── backend
│   ├── main.py
│   ├── parser.py
│   ├── schema_manager.py
│   ├── context_analyzer.py
│   ├── autocomplete_engine.py
│   ├── llm.py
│   └── models.py
├── agent
│   └── graph.py
├── frontend
│   ├── index.html
│   └── monaco.js
├── db
│   └── init.sql
├── tests
├── scripts
│   └── demo.sh
├── requirements.txt
└── README.md
```

## Quick Start

```bash
conda create -n sql-copilot-agent python=3.11 -y
conda activate sql-copilot-agent
pip install -r requirements.txt
uvicorn backend.main:app --reload
```

打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)。

## Environment Variables

- `DB_PATH`：SQLite 数据文件路径（默认 `db/demo.db`）
- `INIT_SQL_PATH`：初始化脚本路径（默认 `db/init.sql`）
- `LLM_PROVIDER`：`openai` / `qwen`（默认 `openai`，`qwen` 预留）
- `OPENAI_API_KEY`：配置后启用 LLM 补全
- `OPENAI_MODEL`：默认 `gpt-4o-mini`

没有设置 `OPENAI_API_KEY` 时，`/autocomplete` 自动降级为规则补全并返回 `mode=rule_only`。

## API

### `GET /health`

```json
{
  "status": "ok",
  "llm_enabled": false
}
```

### `GET /schema/tables`

```json
{
  "tables": ["orders", "users"]
}
```

### `GET /schema/columns/{table}`

```json
{
  "table": "users",
  "columns": [
    {"name": "id", "type": "INTEGER", "notnull": false, "default": null, "pk": true}
  ]
}
```

### `POST /autocomplete`

Request:

```json
{
  "sql": "SELECT u. FROM users u",
  "cursor": 9,
  "max_suggestions": 10,
  "use_llm": true
}
```

Response:

```json
{
  "suggestions": ["u.id", "u.name", "u.email"],
  "mode": "rule_only",
  "debug": {
    "context": "select",
    "table": "users",
    "alias_map": {"users": "users", "u": "users"},
    "timings_ms": {
      "parse_ms": 0.3,
      "schema_ms": 0.7,
      "llm_ms": 0.0,
      "rank_ms": 0.1,
      "total_ms": 1.4
    },
    "errors": []
  }
}
```

## Run Tests

```bash
pytest -q
```

## Milestone Mapping

- 阶段1：后端规则补全 + schema 接口 + SQLite 初始化
- 阶段2：OpenAI provider + 前端 Monaco + 混合补全与降级
- 阶段3：LangGraph DAG + 耗时/错误可观测信息
- 阶段4：README + demo 脚本 + 测试验收
