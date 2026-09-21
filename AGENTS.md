# wedoc

wedoc 是一个开源自托管的协作式多维表格（spreadsheet-database）平台的**后端服务**，用 Python 3.14 编写，目标是产出单一 Docker 镜像，对外提供 REST API、SSE 流式接口与实时协作通道，与现有 Web 前端契约完全兼容、可直接平替上线。

## 技术栈

- **运行时**：Python 3.14，[uv](https://docs.astral.sh/uv/) 管理依赖与虚拟环境（`pyproject.toml` + `uv.lock`）
- **HTTP**：FastAPI + Starlette + uvicorn（ASGI；REST、SSE、WebSocket 同端口）
- **数据校验**：Pydantic v2（请求/响应模型与公开 API 契约逐字段对齐）
- **数据库**：PostgreSQL（唯一支持的存储）。SQLAlchemy 2.0 async（asyncpg）映射既有元数据表；用户数据为**每表一张真实物理表**，DDL/DQL 由 `src/wedoc/db/provider/` 的 SQL 构造层生成
- **缓存/会话/队列**：Redis（redis.asyncio）；后台任务用 arq
- **认证**：session cookie + Bearer（PAT/JWT）多通道；bcrypt、PyJWT、cryptography（AES）
- **实时协作**：`src/wedoc/realtime/` 自研 SockJS + ShareDB 协议兼容层（json0 OT、presence、redis pubsub）
- **测试**：pytest + httpx AsyncClient

## 目录结构

```
src/wedoc/
├── main.py          # ASGI 装配：中间件 → 守卫 → 路由 → /socket → 前端反代兜底
├── config.py        # pydantic-settings，环境变量与部署契约同名同义
├── compat.py        # 协议必需但含品牌词的字面量，运行时拼接的唯一集中点
├── core/            # errors / security / permissions / cache / locks / events / mailer / storage / i18n
├── db/              # models_meta、models_data、migrator、provider（SQL 构造器）
├── formula/         # 公式解析/求值与 PG SQL 编译
├── realtime/        # sockjs、sharedb 消息协议、json0 OT、presence、pubsub、快照 adapter
├── modules/         # 业务模块（auth、space、base、table、field、view、record 等），
│                    # 每模块 router.py / schemas.py / service.py / repository.py
└── workers/         # arq 后台任务（导入导出、邮件、附件裁剪、归档等）
tests/               # pytest 单测与契约测试
docker/              # Dockerfile（uv 构建，python:3.14-slim）
scripts/             # 开发辅助脚本
docs/                # 设计文档与对齐台账（gitignored，内部资料）
```

## 常用命令

```bash
uv sync                          # 安装/同步依赖
uv run pytest                    # 运行测试
uv run wedoc                     # 启动服务（开发）
uv run python scripts/check_brand_terms.py   # 品牌词自检（必须通过）
```

## 硬性规范

1. **品牌纯净**：被 git 跟踪的文件中不得出现上游参考实现的产品名（不区分大小写）。协议层必须保留的兼容字面量一律在 `compat.py` 运行时拼接。提交前运行 `scripts/check_brand_terms.py`，零容忍。
2. **契约一致**：接口的请求参数、返回值、错误结构（`{message, status, code, data?}`）、HTTP 状态码必须与公开 API 契约完全一致；成功响应不做任何包装。
3. **数据库兼容**：不重设计表结构；schema 迁移由 `src/wedoc/db/migrator` 回放官方迁移 SQL 完成，与既有部署可互换。
4. **代码风格**：全量类型标注；ruff lint 通过；模块内分层 router → service → repository；不写解释"做了什么"的注释，只写非显然的"为什么"。
5. **版本控制**：未经用户明确要求，不执行 git commit / push。
