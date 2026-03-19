

**SQL Copilot Agent**

------

# SQL Copilot Agent

AI 驱动的 SQL 自动补全与查询助手

------

# 一、项目背景

在现代数据分析和数据开发中，SQL 是最核心的语言之一。但传统 SQL IDE（如 DBeaver、Navicat）的自动补全能力通常仅限于：

- SQL 关键字补全
- 表名补全
- 字段名补全

这些规则型补全无法理解 **语义上下文**，例如：

```
SELECT *
FROM orders
WHERE
```

IDE 只能补全字段名，而无法生成：

```
WHERE order_date > '2024-01-01'
```

因此，本项目实现一个 **AI 驱动的 SQL Copilot**，支持：

- SQL 智能补全
- Schema 感知补全
- SQL 自动生成
- SQL 优化建议

------

# 二、项目目标

构建一个 **AI SQL 开发助手**，实现以下能力：

| 功能         | 说明               |
| ------------ | ------------------ |
| SQL 自动补全 | 根据上下文补全 SQL |
| Schema 感知  | 识别数据库表结构   |
| Alias 解析   | 自动识别表别名     |
| AI SQL生成   | 自动生成 SQL       |
| SQL 优化     | 优化查询性能       |

------

# 三、系统架构

整体架构如下：

```
                ┌──────────────────┐
                │    Frontend IDE   │
                │  Monaco Editor    │
                └─────────┬────────┘
                          │
                          ▼
                 ┌────────────────┐
                 │  API Gateway    │
                 └───────┬────────┘
                         │
         ┌───────────────┼────────────────┐
         ▼               ▼                ▼
   SQL Parser      Schema Retriever     LLM Engine
   (sqlglot)        (MySQL)            (GPT/Qwen)
         │               │                │
         └───────────────┴────────────────┘
                         │
                         ▼
                 Autocomplete Engine
                         │
                         ▼
                  Suggestion Ranking
                         │
                         ▼
                     Response
```

------

# 四、技术选型

| 模块       | 技术           |
| ---------- | -------------- |
| Frontend   | Monaco Editor  |
| Backend    | FastAPI        |
| SQL Parser | sqlglot        |
| 数据库     | MySQL / SQLite |
| AI模型     | OpenAI / Qwen  |
| Agent框架  | LangGraph      |
| 向量检索   | FAISS          |
| Schema缓存 | Redis          |

------

# 五、系统模块设计

系统由五个核心模块组成：

```
1 SQL Parser
2 Schema Manager
3 Context Analyzer
4 Autocomplete Engine
5 LLM Copilot
```

------

# 六、SQL Parser

SQL Parser 负责解析 SQL AST。

工具：

```
sqlglot
```

示例：

```
SELECT u.id
FROM users u
```

解析结果：

```
table = users
alias = u
```

实现：

```python
import sqlglot

def parse_sql(sql):
    ast = sqlglot.parse_one(sql)
    return ast
```

------

# 七、Schema Manager

Schema Manager 负责加载数据库结构。

支持：

- tables
- columns
- index
- foreign keys

示例：

```
users
 ├─ id
 ├─ name
 ├─ email

orders
 ├─ id
 ├─ user_id
 ├─ price
```

SQL：

```sql
SHOW TABLES;
SHOW COLUMNS FROM users;
```

API：

```
GET /schema/tables
GET /schema/columns/{table}
```

------

# 八、Context Analyzer

Context Analyzer 用于识别 SQL 上下文。

示例：

输入：

```
SELECT u.
FROM users u
```

识别：

```
alias u -> users
```

逻辑：

```
cursor position
        ↓
SQL AST
        ↓
context type
```

Context 类型：

| 类型   | 说明     |
| ------ | -------- |
| SELECT | 字段补全 |
| FROM   | 表补全   |
| WHERE  | 条件补全 |

------

# 九、Autocomplete Engine

Autocomplete Engine 根据 context 生成候选补全。

规则：

### keyword补全

```
SEL -> SELECT
```

### table补全

```
SELECT * FROM
```

返回：

```
users
orders
products
```

### column补全

```
SELECT users.
```

返回：

```
users.id
users.name
users.email
```

实现：

```python
def suggest_columns(table):
    return schema[table]["columns"]
```

------

# 十、LLM SQL Copilot

LLM 用于生成语义补全。

Prompt：

```
You are a SQL copilot.

Database schema:

users(id, name, email)
orders(id, user_id, price, order_date)

Complete the SQL query:

SELECT * FROM orders WHERE
```

返回：

```
order_date > '2024-01-01'
```

------

# 十一、SQL Agent 架构

为了增强系统能力，引入 Agent。

Agent Workflow：

```
User SQL
   │
   ▼
Parse SQL
   │
   ▼
Retrieve Schema
   │
   ▼
LLM Generate
   │
   ▼
Return Suggestions
```

LangGraph DAG：

```
User Input
    │
    ▼
Parse Node
    │
    ▼
Schema Node
    │
    ▼
LLM Node
    │
    ▼
Output
```

------

# 十二、项目结构

```
sql-copilot-agent
│
├── backend
│   ├── main.py
│   ├── parser.py
│   ├── schema.py
│   ├── autocomplete.py
│   ├── llm.py
│
├── agent
│   ├── graph.py
│
├── frontend
│   ├── index.html
│   ├── monaco.js
│
├── db
│   ├── init.sql
│
├── requirements.txt
│
└── README.md
```

------

# 十三、API设计

## SQL补全

```
POST /autocomplete
```

请求：

```json
{
  "sql": "SELECT u.",
  "cursor": 9
}
```

返回：

```json
{
  "suggestions": [
    "u.id",
    "u.name",
    "u.email"
  ]
}
```

------

# 十四、核心代码示例

### autocomplete

```python
def autocomplete(sql):
    ast = parse_sql(sql)

    context = detect_context(ast)

    if context == "table":
        return schema.get_tables()

    if context == "column":
        table = extract_table(ast)
        return schema.get_columns(table)
```

------

# 十五、性能优化

优化方案：

### Schema缓存

避免频繁查询数据库。

```
Redis
```

### SQL AST缓存

```
LRU Cache
```

### LLM结果缓存

```
semantic cache
```

------

# 十六、未来扩展

可扩展功能：

### SQL Explain

自动解释 SQL。

```
SELECT * FROM users
```

解释：

```
Query all records from users table
```

------

### SQL优化

检测慢查询。

```
missing index
```

------

### SQL生成

输入：

```
查找最近7天订单
```

生成：

```
SELECT *
FROM orders
WHERE order_date >= NOW() - INTERVAL 7 DAY
```

------

# 十七、部署

运行：

```
uv python install 3.11
uv venv --python 3.11
source .venv/bin/activate
uv sync --dev
```

启动：

```
uv run uvicorn backend.main:app --reload
```

前端：

```
npm install
npm run dev
```

------

# 十八、Demo示例

输入：

```
SELECT u.
FROM users u
```

返回：

```
u.id
u.name
u.email
```

------

# 十九、项目价值

本项目展示了：

- SQL Parser 技术
- Agent 架构设计
- LLM Tool 调用
- Schema-aware AI
- Developer Copilot 系统设计

适合用于：

- AI Agent 面试
- LLM 工程能力展示
- 数据开发工具开发

------

# 二十、总结

SQL Copilot Agent 结合了：

- SQL AST解析
- Schema知识
- LLM语义补全
- Agent工作流

实现了一个 **AI 驱动的 SQL IDE 自动补全系统**。

------
