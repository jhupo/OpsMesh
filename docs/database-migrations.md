# 数据库插件与迁移

`database` 提供 PostgreSQL 引擎、原生 SQLAlchemy 会话和 Alembic 迁移。插件用 `DatabaseSchema` 声明自己的模型与迁移，不手动调用登记函数。框架加载时消费声明、登记所属插件，并在业务启动前检查其数据库版本；加载失败或撤销插件时一起撤销声明。

## 数据库访问

```python
from typing import Annotated

from sqlalchemy import text

from opsmesh.plugins import require

database = require("database")


async def count_items(
    access: Annotated[database.Database, database.DatabaseAccess()],
) -> int:
    async with access.session() as session:
        return await session.scalar(text("SELECT count(*) FROM items"))
```

`DatabaseAccess()` 注入可共享的数据库句柄，不注入共享的可变 Session。每个并行任务分别打开 `session()` 或 `transaction()`；原子写操作在同一个事务作用域内顺序执行。`session()` 不自动提交；`transaction()` 正常退出提交，异常退出回滚。已移除 `DBSession`、`DBTransaction` 及公开的 `register_models`、`register_migrations` 等重复登记入口，不提供别名。

## 独立迁移历史

发行包拆分后的官方插件 ID 为 `opsmesh.plugins.database`、`opsmesh.plugins.execution` 等；功能插件如 `example_chat` 的 ID 不变。历史转换清单中的旧 `execution` owner 必须保持原值，不能随着模块重命名改成 `opsmesh.plugins.execution`。

每个插件的版本表为 `opsmesh_version_<stable-owner-sha256-first-40-hex>`，包括 `parent:child` 等框架插件标识。表名安全编码不改变插件身份，部署时不得更改已有插件 ID。

迁移目录只包含该插件的 Alembic 历史，声明一个确定的终点 `head`。不使用其他插件的 revision 作为 `down_revision` 或 `depends_on`；插件之间通过框架 `require()` 保证迁移执行顺序，数据库外键仍可引用已依赖插件的表。停用插件后其版本表和数据保留，不影响独立插件检查。

`commands.migrate` 在 PostgreSQL 事务中取得全局 advisory lock，按声明的依赖顺序升级。正常启动只检查版本，不自动执行 DDL。原生 Alembic 的脚本加载与版本解析仍负责迁移，未自建迁移执行器。

## 从原共享 alembic_version 显式转换

此操作只适用于已有共享版本表的部署，属于一次性运维迁移，不是运行时兼容层。新安装无旧历史时直接执行常规 `commands.migrate`。

1. 停止旧 OpsMesh 进程及任何可能迁移数据库的其他进程，保留数据库备份和旧发行包中的完整迁移资源。
2. 准备完整的 JSON 清单，包括已停用插件的历史目录。目录相对清单文件解析；迁移模块是受信任代码，只允许使用管理员确认的本地文件。
3. 加载新 database 插件，以相同数据库 DSN 运行下方命令。
4. 转换成功后运行常规迁移，再启动新应用。不要重新启动仍写共享版本表的旧版本。

```json
{
  "sources": [
    {
      "owner": "example_chat",
      "directory": "/var/lib/opsmesh/plugins/example_chat/migrations",
      "head": "0001_example_chat"
    },
    {
      "owner": "example_sandbox",
      "directory": "/var/lib/opsmesh/plugins/example_sandbox/migrations",
      "head": "0001_example_sandbox"
    },
    {
      "owner": "execution",
      "directory": "builtin:legacy-execution",
      "head": "0001_execution_observations"
    }
  ]
}
```

```bash
export OPSMESH_DATABASE_HISTORY_MANIFEST=/etc/opsmesh/database-history.json
opsmesh --manifest /etc/opsmesh/host.json \
  --command opsmesh.plugins.database.commands:convert_shared_history
opsmesh --manifest /etc/opsmesh/host.json \
  --command opsmesh.plugins.database.commands:migrate
```

清单示例仅展示格式，必须按实际旧数据库提供所有来源。即使插件已停用，也要从旧离线发行包保留完整迁移目录，不能只列当前启用插件。独立 database 插件包携带所需迁移资源；读取旧编译发行历史时支持相邻 `.pyc` 迁移文件。历史跨插件 revision 依赖不能自动拆分，命令会拒绝，须先评审专门的历史转换方案。

转换在同一个事务和 advisory lock 内完成：验证所有来源及 revision 归属 → 确认共享表中每个 revision 均可解析 → 将实际已应用的 revision 写入各插件版本表 → 保存 `opsmesh_migration_cutover` 审计记录。不会执行任何 `upgrade`/`downgrade`，不会修改业务表，不会删除旧 `alembic_version`。缺少来源、归属冲突、目标版本冲突或已经转换都会明确失败。转换前，正常检查和升级遇到共享历史会拒绝执行，防止重复建表。

`builtin:legacy-execution` 对应发行包中的 `opsmesh/plugins/database/_legacy_migrations/execution/0001_execution_observations.py`（或相邻字节码）。它是旧迁移的完整归档，仅供显式转换使用，不进入正常 schema 注册。旧 `execution_calls` 和 `execution_usage_observations` 数据原样保留，新的 execution 不再维护它们，删除或归档旧业务表须另行授权。

## 验证范围

定向测试使用原生 Alembic 与隔离 SQLite 引擎验证独立版本表、共享历史转换、未知历史拒绝、旧 execution 数据保留、插件停用与并发 DI 作用域。PostgreSQL 的 advisory lock 和 DDL 事务需在隔离 PostgreSQL 环境联调；单元测试不能替代该验证。
