# Kubernetes部署

此目录提供API/Worker双副本、显式数据库迁移Job、共享内容PVC、Service和API中断预算；没有创建外部PostgreSQL、Redis或公共Ingress。已在独立K3s v1.34.1集群验收逐Run Job完成、跨Pod审批恢复、取消、缺镜像deadline失败与日志回收。

镜像从包含wheel和锁定依赖清单的Linux发行包构建。`deploy/Dockerfile`不再接受源码仓库根目录作为构建上下文。在v0.3.2源码仓库中先生成并解压发行包，再使用包内的Dockerfile：

```sh
uv run python scripts/build_linux_release.py --output-dir dist/linux
(
  set -e
  cd dist/linux
  sha256sum --check opsmesh-0.3.2-linux.tar.gz.sha256
  tar -xzf opsmesh-0.3.2-linux.tar.gz
  cd opsmesh-0.3.2-linux
  sha256sum --check SHA256SUMS
  docker build --file deploy/Dockerfile --tag opsmesh:0.3.2 .
)
```

也可直接下载并校验v0.3.2的Linux发行包，从解压步骤开始。将生成的镜像推送到自己的镜像仓库，再用Kustomize overlay将`opsmesh:0.3.2`替换为固定digest；本示例标签不表示存在公开容器镜像。后续`kubectl`命令仍从源码仓库根目录执行，使用本目录中的部署清单。

共享PVC预先保证控制器与Run worker的UID/GID（默认10001）有读写权限；hostPath不会自动替宿主root创建的子目录修复权限。配置一个支持ReadWriteMany和POSIX一致读写的存储类；不能把不同Pod本地目录视为共享工作区。数据库使用外部PostgreSQL，Redis用于跨Gateway限流。

在`opsmesh`命名空间创建已有Secret `opsmesh-secrets`，至少提供`OPSMESH_DATABASE_URL`、`OPSMESH_REDIS_URL`和模型/提供商环境引用。模型密钥应另行设置`OPSMESH_RUN_WORKER_ENVIRONMENT_ALLOWLIST`才能传给子进程。启用OTLP时提供`OPSMESH_TRACING_ENDPOINT`，认证JSON放到被`OPSMESH_TRACING_HEADERS_SECRET_REF`引用的环境变量；不把凭据写进仓库。

先应用namespace、configuration、storage和migrate，等待迁移Job成功，再应用api/worker。API readiness查询数据库，liveness只检查服务存活。初始化本地管理员使用一次交互式`opsmesh init`，不将管理员密码写在Job定义中。后续版本迁移Job需新名字或显式重建；不要把已有Job成功状态当作新版本迁移已执行。

基础配置的Run Worker是Pod内独立Python进程。`job-mode/` overlay启用真正的逐Run Kubernetes Job执行器。Pod限制约束整台Worker，进程不等于独立安全沙箱；需要工具文件隔离时注册沙箱provider，或使用独立提供商。部署文件不挂载宿主Docker socket。

扩容Worker复用持久Run领取、Session占用和CAS容量租约。外部调用中断后结果不确定时仍需核实，不能依赖Pod重启自动重放副作用。执行`storage-gc --apply --maintenance`前停止所有写入服务和Worker，备份数据库及共享内容后再恢复服务。

## 逐Run Job模式

`kubectl kustomize deploy/kubernetes/job-mode`可检查完整overlay；先按前述步骤完成迁移，再应用API和Coordinator。将overlay中的`OPSMESH_RUN_WORKER_IMAGE`设置为包含同版本OpsMesh和可信插件的固定镜像digest。Kustomize的`images`替换不会自动改环境变量中的Job镜像，需同时配置这项。

部署方另行创建以下Secret，仓库不包含凭据：

- `opsmesh-run-settings`的`OPSMESH_WORKER_SETTINGS`键是完整`OpsMeshSettings` JSON，至少包含与控制器一致的`database_url`、`plugins`、`data_directory: "/var/lib/opsmesh"`及相关策略配置。推荐显式`worker_enabled: false`和`run_worker_mode: "inline"`；单Run入口也会强制关闭队列消费者。使用外部PostgreSQL；此模式拒绝SQLite。
- `opsmesh-model-secrets`保存被模型、存储、追踪配置引用的环境变量。`environment_secrets`可以换成其他明确选择的Secret；不会把控制器全部环境变量传入Job。

`OPSMESH_KUBERNETES_WORKER`接受`KubernetesWorkerConfiguration` JSON。默认通过Pod投射的ServiceAccount token和CA访问HTTPS API；每次请求重读token以支持轮换。`namespace`、`deployment_id`、`data_claim`、`settings_secret`属于部署配置，HTTP Run覆盖不能修改。多个Coordinator必须使用相同配置和Secret版本；同一Run代次发现不同Job规范时返回冲突，不替换正在执行的Job。

领取使用持久Run启动租约；Job名称和token由命名空间、deployment_id、Run及执行代次确定。创建响应丢失会查询原Job，多协调器竞争也只能创建同名Job。启动后由Run执行租约和CAS控制实际执行。`backoffLimit: 0`、`restartPolicy: Never`阻止Kubernetes自行重放模型调用。审批暂停结束本次Job，审批恢复创建新代次Job；失联执行沿用平台的未知结果恢复策略。

Coordinator每轮发现已有Job，续租尚未启动的任务；完成/失败先保存有界Pod日志及退出状态，再凭Job UID删除。HTTP取消写入Run后，Coordinator删除对应Job。重启Coordinator或关闭其API客户端不会取消其他正常Job，后继Coordinator继续接管；`activeDeadlineSeconds`和完成后TTL提供集群侧兜底回收。日志保存在共享内容目录`worker-logs/<Run哈希>/<代次>.log`，Job删除后仍可通过launcher读取。设置日志期限应结合实际审计与数据保留要求。

Job默认非root、只读根文件系统、禁止提权、丢弃全部Linux capabilities，带CPU/内存/临时空间限制；仅挂载共享PVC和`/tmp`。Run ServiceAccount没有Role，也不挂载Kubernetes token。Coordinator Role限定所在命名空间，仅允许管理Jobs、读取Pod状态及日志，无Secret读取、Pod exec或跨命名空间权限。能够创建Job的控制器仍属于该命名空间的受信任组件，应该使用专属命名空间。

API使用独立`opsmesh-observer`只读ServiceAccount；只有Job/Pod状态与Pod日志读取权限。`GET /api/v1/runs/{run_id}/worker`及`/worker/logs`要求`run:read`授权，状态不暴露启动token。日志端点只投影Worker的`started`、`finished`、错误code等结构化诊断，默认64KB输入窗口、最高1MB，任意插件stdout、模型原文、原始错误message不会返回，丢弃行数可见。原始stdout仍在受限运维文件中，不应把共享内容目录直接作为静态Web目录公开。API取消只写持久Run状态，实际删除由Coordinator执行，observer不需要删除权限。

Kubernetes API没有通用的逐Pod PID上限字段，Docker的`run_worker_pids_limit`不宣称在此模式生效；需要由集群kubelet的`podPidsLimit`策略配置。网络出口使用集群NetworkPolicy控制，不能把Docker的`run_worker_network`当作Pod网络策略。共享PVC支持多Pod并发访问，不能用不同节点各自的本地目录替代。

## 显式集群验收

`tests/runtime/test_kubernetes_integration.py`默认跳过。它使用真实Job和离线backend，不调用模型API，仅在专用测试命名空间、数据库和PVC中运行。准备测试镜像、上述最小权限Role/SA及Secret后，设置：

```text
OPSMESH_TEST_KUBERNETES_INTEGRATION=1
OPSMESH_TEST_KUBERNETES_SETTINGS=/path/to/private-test-settings.json
```

控制器配置文件为完整`OpsMeshSettings` JSON；`run_worker_mode: "kubernetes"`、`run_worker_image`、`kubernetes_worker`必填，并在`plugins`加入`opsmesh_kubernetes_probe`。其`data_directory`必须是测试PVC的同一内容（控制器宿主目录路径可不同于Pod的`/var/lib/opsmesh`）。测试将可信fixture写到该目录的`acceptance-plugins/`；Job的settings Secret也必须加载此插件，环境Secret设置`PYTHONPATH=/var/lib/opsmesh/acceptance-plugins`。控制器使用`opsmesh-coordinator`令牌，测试会断言读取Secret被403拒绝，不能用集群管理员凭据替代。

执行`uv run pytest tests/runtime/test_kubernetes_integration.py -q`。测试真实创建Pod、完成Run、跨Pod审批恢复、取消运行中Job，以及`imagePullPolicy: Never`下缺镜像的30秒deadline失败，检查日志和失败Run状态。每次只处理自己创建的Run/Job；不向已有业务Pod发送命令。配置文件和Secret均不提交仓库。
