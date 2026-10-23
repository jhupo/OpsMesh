# 工程脚本

此目录只保留当前源码使用的工程入口。主机部署实现位于 `deploy/cli`，容器启动脚本
位于 `deploy/images/entrypoint.sh`，插件开发示例位于 `sdk/python/examples`。
正式部署只有 Docker（Compose）和本机安装（systemd）两种；构建脚本、wheel 和源码包是开发与发行输入。

| 脚本 | 职责 |
| --- | --- |
| `audit_app_layout.py` | 当前源码职责、依赖和 ORM 所有者检查 |
| `export_compute_requirements.py` | 从锁文件生成计算镜像依赖 |
| `ci_tests.py`、`ci_command.py` | 按受影响边界选测试、运行 CI 命令 |
| `build_standalone.py`、`cli_entry.py`、`server_entry.py`、`freeze_hooks/` | 构建原生 CLI 和服务端发行包 |
| `check_standalone.py`、`check_python_package.py` | 验证迁址后的二进制与隔离 wheel |
| `check_runtime_image.py` | 真实 Docker 沙箱与 stdio MCP 验收 |
| `check_candidate.py`、`check_managed_delivery.py` | CI 候选镜像和独立部署、更新、恢复验收 |
| `release.py`、`publish_release.py`、`check_release.py` | 发行版本及清单、GitHub 发布和资产核验 |

带外部效果的验收脚本需使用其声明的隔离环境配置；普通静态检查不替代产品验收。

`check_standalone.py` 和 `check_managed_delivery.py` 使用当前 workflow 实例接口；
它们验证发行包、等待节点及恢复，不代表完整 Agent/MCP/审批产品验收。
当前覆盖缺口见 [tests/README.md](../tests/README.md)。只有用户明确要求时才在本地运行测试或验收脚本。
