# 写库前门禁 (sql-write-gate)

## 定位

Agent 要对 DuckDB 数仓做 `INSERT` / 写操作时，**不能靠模型自己判断能不能写**。本仓库提供一层确定性预写门禁（wrapper）：

- **新鲜度**：写入过期分区（`dt` 早于截止日期）→ 拒绝，并给出 rule id + 原因
- **Schema**：未知列 / 类型不匹配 → 拒绝，并给出证据
- **PII**：向 `email` / `phone` 等 PII 列写入 → 拒绝，并给出证据
- **只读**：单条 `SELECT` 绕过写规则，直接放行
- **合法写入**：新鲜分区 + 非 PII 列 → 放行，并由唯一写工具执行

策略引擎是纯规则（sqlglot AST + `seed/catalog.json`），**不调用 LLM，不需要 API Key**。

## 无 Key 的 make demo

```bash
cd sql-write-gate
make demo
```

会依次：创建本地 venv → 安装依赖 → 幂等重建 `seed/warehouse.duckdb` → 用 Python 脚本打三条用例。全程离线，没有模型、没有网络 Key。

单独跑：

```bash
make seed    # 幂等重建 CSV + DuckDB
make test    # pytest -q
python -m write_gate check "SELECT 1"
python -m write_gate exec "INSERT INTO orders (order_id, user_id, amount, dt, status) VALUES (900001, 42, 18.5, '2026-09-01', 'paid')"
```

仓库文件：`seed/warehouse.duckdb`。目录：`seed/catalog.json`（可写表、允许列、PII 列、新鲜度 cutoff）。

## 三条用例

`make demo` 打印的三个固定场景（日期锚定 `as_of=2026-09-02`，超过 7 天的分区视为过期，即 `dt < 2026-08-26`）：

| # | 场景 | 期望 | `rule_id` |
|---|------|------|-----------|
| 1 | 合法写入：新鲜分区 `dt='2026-09-01'`，只写 `order_id,user_id,amount,dt,status` | ALLOWED | `ok` |
| 2 | 过期分区：`dt='2026-08-01'` | BLOCKED | `expired_partition` |
| 3 | PII 写入：INSERT 带 `email` | BLOCKED | `pii_column` |

证据对象形状：

```json
{
  "allowed": false,
  "rule_id": "pii_column",
  "message": "...",
  "sql": "..."
}
```

`rule_id` 取值：`ok` | `pii_column` | `expired_partition` | `schema_mismatch`。

示例表 `orders` 列：`order_id, user_id, amount, dt, email, phone, status`。种子约 120 行，一半过期分区、一半新鲜分区，并带有 email/phone。

**唯一写入口**：`WriteGate.execute(sql)`。脚本与测试不得绕过 wrapper 直接调用 DuckDB 写 API（种子脚本 `scripts/gen_seed.py` 除外，它只负责重建仓库）。

## 非目标

- 企业级 DQ / 数据质量平台、血缘 lineage
- ChatBI、SSO、多租户、计费
- LangGraph / CrewAI / 远程 MCP / 在线模型
- spark-retail-dw 克隆、Spark 数仓、海量数据

本仓库是可演示的本地 MVP：一个 wrapper、一份 catalog、一个 DuckDB 文件、一套 pytest。

## 许可

MIT。见 [LICENSE](LICENSE)。
