# OpsMesh Linux 插件管理服务

核对日期：2026-11-20。状态：服务、部署模板和安装脚本已实现；真实 Linux/systemd 测试已加入 CI，不以 Windows 单测代替部署验收。服务名为 `opsmesh`。只安装一个命令：`opsmesh serve` 运行唯一常驻进程；其余在线管理命令只连接它，不再初始化框架。无 `opsmeshd`、`init` 或插件子进程。

## 1. 从本地 wheel 安装

目标机需要 Linux、systemd、Python 3.11+（含 venv/pip）、systemd-sysusers、flock、logrotate。正式部署从同一个 GitHub Release 获取 `opsmesh` 和 `opsmesh-cli` 的 wheel、`opsmesh-deploy-<tag>.tar.gz` 及 `SHA256SUMS`。校验下载的文件，解开部署包；在同平台、同 Python 版本的受信环境里用这两个 wheel 下载宿主的第三方依赖。发布包不内置第三方依赖，离线安装前需准备完整 wheelhouse。无需源码或业务插件。

开发时可从仓库根目录构建 wheelhouse 并安装：

```bash
python3 -m pip wheel --wheel-dir ./wheelhouse . ./src/opsmesh-cli
sudo bash deploy/install.sh ./wheelhouse /usr/bin/python3
systemctl status opsmesh.service
sudo opsmesh service status
```

[安装脚本](../deploy/install.sh) 要求目录含一个 `opsmesh_cli` wheel、同版本框架 wheel 及全部依赖。离线安装到 root 所有的 `/opt/opsmesh/environments/`，校验依赖与命令后切换 `current`；`/usr/local/bin/opsmesh` 与 systemd 使用同一环境。保留已有 `/etc/opsmesh/opsmesh.toml`；拒绝接管同名无关命令或正在运行的实例。不要让非管理员提供或修改安装脚本、解释器或 wheelhouse。

可选第三参数 `RUNTIME_REQUIREMENTS` 用于从同一离线 wheelhouse 安装已审核的第三方运行库（例如向量库的 SQLAlchemy、Agents SDK 和 CPU 模型库）。不要把由管理器接管的 OpsMesh 插件本身写进该文件；插件 wheel 单独通过管理命令安装。向量库使用独立数据库、预置模型和受保护的 systemd 环境文件，详见[插件部署说明](../packages/knowledge/README.md)。

unit 创建账户的运行、状态和日志目录，以 `opsmesh` 身份前台启动，支持就绪通知、受控重启及停机超时。管理员可将可信用户加入 `opsmesh-admin`，重新登录后即可使用 socket；成员有权加载同一服务账户下执行的 Python 代码，**不是沙箱**。投递区由 root 写、服务只读。普通安装只配置默认路径；更换路径须同时调整目录属主、unit 写入权限和 logrotate 模板。默认 `~none` Driver 不监听任何业务 TCP 端口；换用其他 Driver 要由部署层提供其依赖并重新验证就绪和停机。

## 2. 模块与所有权

```text
管理员 → opsmesh → socket → manager → database / filesystem
                                  └→ 受控重启 → runtime → 框架 Driver 与插件
业务输入 → Adapter → Event → Matcher → 业务处理与回复（不经过管理 socket）

src/opsmesh-cli/
├── pyproject.toml                  # 独立发行包，唯一入口 opsmesh_cli.main:main
└── src/                            # 源码直接平铺，构建后才映射到 opsmesh_cli
    ├── main.py / command.py        # 进程装配、生命周期；命令解析、等待及输出
    ├── config.py / socket.py       # 服务 TOML；本机协议与对端身份
    ├── log.py / maintenance.py     # 运行日志；独占离线备份、校验与恢复
    ├── plugin/
    │   ├── manager.py              # 预检、变更、提交、恢复状态机
    │   ├── dependencies.py         # 宿主依赖校验与插件拓扑排序
    │   ├── package.py              # wheel、RECORD、清单、受限解包
    │   └── runtime.py              # 唯一装配框架的模块、加载归属校验与就绪
    └── storage/
        ├── database.py             # SQLite 连接、事务、revision 与操作记录
        └── filesystem.py           # 受管目录、同步落盘、发布与清理
deploy/{install.sh,opsmesh.service,opsmesh.toml,opsmesh.sysusers,opsmesh.logrotate}
tests/unit/test_cli_management.py
tests/integration/test_linux_service.py
```

服务仅依赖仓库内的框架，不要求业务插件依赖服务。Python 包映射仅用于导入，不是第二个进程。不要直接执行 `src/main.py` 或把服务源码目录作为 `PYTHONPATH`，否则可能遮蔽标准库 `socket`。框架 `load_plugin` 可返回 None；Matcher 的 enabled 位不等于连接已停止。服务因此只在干净的运行实例中验证启动/换版，详见[框架架构](framework.md)与[网关流程](gateway.md)。实现使用固定框架版本的 Lifespan ready 钩子；框架升级需要复验该接点。

## 3. 命令与插件包

`opsmesh --help` 和每组子命令的 `--help` 是参数清单。`--config PATH`、`--json` 是命令前的全局参数。参考 nb-cli 按插件动词组织操作；这里的命令不会运行 pip 改变在线环境。典型操作：

```bash
# 管理员将受信 wheel 投递进 /var/lib/opsmesh-inbox，使用发布方确认的摘要。
sudo opsmesh plugin install report-1.0-py3-none-any.whl --sha256 "$SHA256"
sudo opsmesh plugin enable report --now --plan
sudo opsmesh plugin enable report --now --allow-restart
sudo opsmesh plugin status report
# 若要在运行中换版，先由管理员在服务配置中设置 restart_safe = ["report"] 并重启服务
sudo opsmesh plugin upgrade report report-1.1-py3-none-any.whl --sha256 "$NEW_SHA256" --allow-restart
sudo opsmesh plugin disable report --now --allow-restart
sudo opsmesh plugin uninstall report             # 保留用户配置、资源和数据
sudo opsmesh plugin delete report --confirm report # 彻底删除本机保留数据
```

| 操作 | 行为 |
| --- | --- |
| `plugin list/status` | 展示安装版本、本次实例的运行版本、自启和状态。没有业务探针，health 为 unknown；服务不可达是错误，不把插件报成已停止。 |
| `plugin start/stop` | 只改变本次运行集；`enable/disable` 只改普通开机集合，`--now` 同时改两者。需要换进程时必须允许整服务中断。 |
| `service status/version/logs`、`operation show UUID` | 状态含实例号、运行集、revision、未决操作；version 查询服务和框架，`--version` 离线查询本地命令。日志支持 `-f` 和 `--plugin ID`。 |

插件以普通 Python wheel 分发。不要求 OpsMesh 清单或 entry point。服务读取标准 WHEEL、METADATA 和 RECORD，验证投递的 SHA-256、文件摘要和 Python 版本约束。服务仅接受**一个含 __init__.py 的顶层纯 Python 包**，以导入名作为管理 ID；发行名可不同，如发行包 acme-report 安装后管理的是 report。不支持 namespace、多顶层包、原生扩展、.data 安装脚本或联网安装。Requires-Dist 可指向已安装的受管插件（按发行名匹配，检查版本和启用顺序）或部署环境预装的库；不解析 URL 或依赖 extras。

例如用 setuptools 打包目录 report/，包含 __init__.py 及 defaults.toml：

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "acme-report"
version = "1.0"
dependencies = ["opsmesh>=0.5,<1"]

[tool.setuptools.package-data]
report = ["defaults.toml"]
```

构建命令为 `python -m build --wheel`。包内默认值与部署配置是两条独立路径：插件可以用 `importlib.resources.files("report").joinpath("defaults.toml").read_text(encoding="utf-8")` 读取随包资源，用 `get_plugin_config(ReportConfig)` 读取框架环境配置。未声明 `config_file` 时，后者不自动读取包内 TOML。插件可另行声明 `config_file="settings.json"`，由框架在启动前按[配置契约](framework.md#6-插件配置与声明式连接)读取入口目录下的 JSON，以 `config` 配置自身、以 `dependencies.<插件 ID>` 为依赖插件提供配置。将可变的 `settings.json` 作为管理员创建的文件，不纳入 wheel 文件清单；wheel 可附带另名示例。这样升级不会覆盖配置，改动重启生效。凭据通过服务进程环境提供，不写在文件中。服务只管理包和生命周期，不注入路径，不另存插件 JSON，也不提供资源编辑、功能开关或清缓存命令。

管理员在服务设置中用 `protected` 指定不能独立停用、升级和移除的插件，用 `restart_safe` 明确允许哪些插件在已运行时换版（仍需 --allow-restart）。两者默认均为空；未确认数据兼容的插件先停机备份再升级。运行实例也尊重已加载插件的框架 protected 声明；若需加载前或跨重启保护，须设置 protected。

## 4. 数据与恢复边界

| 路径 | 内容 |
| --- | --- |
| `/etc/opsmesh/opsmesh.toml`、`/run/opsmesh/opsmesh.sock` | root 管理的设置；0660 的本机控制接口 |
| `/var/lib/opsmesh/state.sqlite3`、`maintenance.lock` | 唯一安装记录、操作日志与 revision；在线及离线互斥 |
| `/var/lib/opsmesh/plugins/<id>/` | 入口、包内资源、插件自建文件在同一目录，不强制 code/data 分层。`.opsmesh-metadata/` 由安装器保留，用于发现标准发行元数据。 |
| `/var/lib/opsmesh/staging/<op>/`、`trash/<op>/` | 同卷的临时操作目录，不是历史仓库；成功后删除 |
| `/var/log/opsmesh/opsmeshd.log` | 唯一运行日志，结构化 JSON，按日轮转保留 14 份。不设单独审计日志。无文件权限时不借 daemon 提权读日志；启动诊断另见 `journalctl -u opsmesh.service -b` |
| `/var/backups/opsmesh/<UUID>/` | 显式创建的离线备份，不随插件删除 |

`--plan` 返回 revision、变更前后运行集、版本和重启影响，不改状态。可用 `--expected-revision N` 防止计划过期。服务先查重、再校验版本/依赖/受保护项；同时检查已安装依赖、启用集合和本次运行集，不隐式启动、禁用或删除依赖。`--request-id UUID` 让同一 UID 重试同一请求，不能复用给不同载荷。安装与普通写在服务端完成预检/准备后返回结果；跨重启操作返回持久化 op_id。默认 CLI 等待终态，`--no-wait` 不等待重启结束，不表示跳过包校验。

1. **准备。** 验证归属、hash、依赖、RECORD 和包内文件，再解到私有 `new/`。安装不导入包。不改活动目录；准备后记录 `prepared`、目标记录、切换前后运行集以及摘要。
2. **替换。** 运行中先正常退出，由 systemd 拉起干净进程；未运行者无需中断其他插件。只按数据库中已验证的包文件清单替换：旧文件同卷移入本次操作的 `old/`，新文件逐个改名并同步父目录。预检发现受管文件被改或新文件与用户文件同名即拒绝升级。普通卸载保留未登记和已被用户修改的文件；彻底删除必须确认 ID。重启时根据操作记录、存在性和摘要补完未完成的改名。
3. **验证。** 试启动前将 `trying` 落盘。检查实际插件 ID、模块来源及 Driver/socket 就绪；运行中换版只有此时才能提交。停止的插件只完成安装验证，不伪称业务健康。未声明数据兼容的插件需先停机、备份、按其手册迁移。
4. **提交与清理。** SQLite 在一个事务内写当前记录、新 revision 和 `cleanup`。删除旧代码及本次工作区后才返回 `succeeded`。删除失败保持新版，后台重试清理并拒绝其他写；不建立可选择的历史版本。
5. **失败。** `trying` 后未能提交，下一干净进程恢复本次旧代码并按 before 启动。最多一次；回退失败则 `maintenance_required`，使用离线恢复。提交后宕机只清理，不回退。客户端断线不撤销已登记操作。代码恢复不能撤销已发生的外部写入。

## 5. 管理协议与运维

本机 Unix stream，每连接一请求/响应；四字节大端长度 + UTF-8 JSON，限 1 MiB。文件系统权限决定可连接者，内核 SO_PEERCRED 标识调用 UID。拒绝未知字段、方法、重复键和危险 ID。请求 `v=1,request_id,method,params[,expected_revision]`；成功返回 `ok/result`，失败 `ok=false,error:{code,message}`。读帧 5 秒；写单飞、无队列；客户端等待重启最长约 120 秒，超时用原 UUID 或操作号查证。退出码：0 成功或已接纳；1 操作失败；2 输入错误；3 未找到；4 权限不足；5 冲突/忙；6 不可达或结果未知。

```bash
sudo systemctl stop opsmesh.service
sudo -u opsmesh /usr/local/bin/opsmesh maintenance backup
sudo -u opsmesh /usr/local/bin/opsmesh maintenance check --snapshot "$SNAPSHOT_ID"
# 恢复是显式操作，不是每次升级的必经步骤：
sudo -u opsmesh /usr/local/bin/opsmesh maintenance restore "$SNAPSHOT_ID" --confirm "$SNAPSHOT_ID"
sudo systemctl start opsmesh.service
```

维护程序拒绝以 root 执行。备份 SQLite、整个插件目录（包括插件自建的配置、资源和缓存），校验快照内容摘要；服务日志、外部数据库与宿主凭据不包含。恢复时先校验副本，保存 `restore.pending`，逐项同卷替换；进程中断后用同一恢复命令继续，daemon 不得打开半恢复的根。原数据保存在 `previous-<UUID>/` 供救援。备份生成后由管理员另存到受保护的异机存储；本机同 UID 的插件不是备份信任边界。业务外库与服务配置另行备份。新版本服务使用同一安装脚本切换环境；先停止、备份和保存旧配置。本布局使用 schema 2。旧 schema 1 无文件归属清单，不能原地接管：用旧版停机备份，保留旧状态根，在新的状态根重装原始 wheel，再按插件说明复制业务文件并恢复启用状态。不要覆盖新版包文件或复制旧数据库。`maintenance migrate` 在 schema 已匹配时只校验，不尝试猜测旧格式。恢复旧软件需同时恢复匹配的数据与配置。

## 6. 验收

在已安装服务 wheel 的测试环境执行 `python -m pytest tests/unit/test_cli_management.py`。在专用可丢弃 Linux VM 中，再运行安装脚本和 `OPSMESH_DISPOSABLE_SYSTEMD_TEST=1 python -m pytest tests/integration/test_linux_service.py`；后者会真实安装测试插件、重启 unit、恢复数据并轮转日志，**禁止在生产机运行**。CI 单独作业构建 wheelhouse 并走这一路径。Linux 运行证据、外部系统的业务健康、不可逆业务迁移，均不能由纯 Python 单测替代。
