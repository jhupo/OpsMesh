# workspaces

本模块拥有活动工作区、WorkspaceManifest、只读位、文件装配、制品、快照与恢复。`api.py` 是公开合同，`events.py` 是领域事件，`internal/` 保存实现；bootstrap 负责组装。

活动工作区始终是本地或共享挂载目录，路径仍为 `<data_directory>/storage/workspaces`。S3 仅替换不可变 blob 的存储；它不提供 POSIX 工作目录，也不替代 Worker 之间的活动文件共享。

## BlobStore

`infrastructure/storage/api.py` 定义异步 BlobStore：`put/get/stat/list/delete/close`。`LocalBlobStore` 默认使用现有 `storage/objects/<sha256>` 布局；可选 `S3BlobStore` 使用指定桶与 `<prefix>/<sha256>`。`LocalWorkspaceService(..., blob_store=...)` 组合文件工作区与 blob 提供商。

- 内容 ID 是实际字节的 SHA-256。get 总是有大小上限并验证完整摘要；不会信任 ETag 等于 SHA-256。
- put 幂等且不覆盖。Local 以完整临时文件和不可覆盖的硬链接发布；S3 使用 If-None-Match 条件创建，已有对象必须读取验证。遇损坏内容明确报错。
- list 分页返回大小、修改时间和条件删除版本；delete 要求 expected_version。S3 要求服务支持条件创建和 If-Match 删除，不会退化到无条件删除。
- 本地提供商需要支持原子硬链接的文件系统。S3 不会自动创建桶，桶/前缀应由部署管理并专供平台使用。
- 工作区读写、物化、快照及恢复使用数据库租约与 fence。blob 租约以存储 identity 定位，两个不同工作目录访问同一 S3 命名空间仍共享锁。
- 外部 I/O 持有 fence 到线程完成，任务取消不会让尚在进行的写入越过下一持有者。绕过服务直接改磁盘或对象的程序不受这些约束。

配置入口为 `OPSMESH_STORAGE_BACKEND=local|s3`、`OPSMESH_S3_BUCKET`、`OPSMESH_S3_ENDPOINT_URL`、`OPSMESH_S3_REGION_NAME`、`OPSMESH_S3_PREFIX`。凭据通过 `OPSMESH_S3_ACCESS_KEY_SECRET_REF`、`OPSMESH_S3_SECRET_KEY_SECRET_REF`、可选 `OPSMESH_S3_SESSION_TOKEN_SECRET_REF` 解析；也可使用 boto3 默认凭据链。不要把凭据写进 endpoint、Agent 定义或版本库。安装可选依赖使用 `uv sync --extra s3`。

切换提供商不会自动搬运旧对象。应停写、导出原存储、恢复到新存储并验证，随后将所有 API/Worker 配置切换为相同提供商与前缀。

## 引用与 GC

实际删除仍要求维护模式：停止所有写服务、Worker、受信文件写入程序和其他 GC。GC 使用存储命名空间的持久锁、每个 blob 的 fence、最小存活时间、二次数据库引用扫描及条件版本删除。一次 put 与业务引用落库不是跨 S3/SQL 的全局事务，因此不能把维护参数理解成自动阻止所有在线引用竞争。

数据库保守扫描包含模块记录和 Outbox 中的摘要；共享引用未删除就保留对象。直接持有但尚未落库的引用不能由扫描发现。读取不会改变 S3 对象的修改时间，最小年龄不是最后使用时间。

S3 开启桶版本控制时删除产生当前对象的删除标记，历史版本可能仍占空间；GC 返回的 reclaimed_bytes 是不再可见对象的逻辑字节，不代表已减少云存储账单。历史版本、删除标记和对象锁保留策略由桶生命周期管理；平台不会强行删除受保留保护的版本。

## 备份与恢复

`infrastructure/storage/backup.py` 提供已实现的离线制品接口：

- `export_blobs(store, destination, digests=None, maintenance=False)` 导出新目录中的 `objects/` 与 `manifest.json`；指定 digests 时逐个确认引用存在，否则导出当前对象清单。
- 返回 `BlobBackupManifest`，其 `manifest_digest` 应另行保存在备份记录中。完成标志是最后写入的 manifest；失败导出可能留下无 manifest 的目录，不应把它当作可用备份。
- `verify_blob_backup(source, expected_manifest_digest=...)` 验证 manifest、每个对象的摘要和大小。
- `restore_blobs(store, source, expected_manifest_digest=..., maintenance=False)` 先验证整个制品再上传，重复恢复幂等；中途远端失败可重试，已有损坏对象不会被悄悄覆盖。

这只是 blob 备份，不是完整平台恢复。完整维护演练顺序为：停写并确认没有活跃外部任务 → 保存数据库一致性 dump、活动工作目录及 blob bundle → 单独保存 manifest 摘要和部署/插件版本 → 在隔离的新数据库、目录和 S3 前缀恢复 → 校验 schema、所有数据库引用、工作区文件/只读位、快照恢复及业务读取 → 再启用服务。数据加密密钥和凭据另按部署密钥管理保存。

数据库 dump 与活动目录必须由统一备份编排协调；只恢复 blob 无法重建账户、Run、审批或工作区元数据。S3 原生版本控制也不能单独代替这组一致性备份。

## 快照恢复的重试

平台管理接口为每个恢复目标保存所有者、快照 ID 和稳定操作 ID。相同主体针对相同快照/目标的重试继续原操作，其他主体、其他快照及普通 create 不能占用这个目标。旧版本留下的所有者预占，仅在实际工作区不存在时允许同一所有者接续恢复。

文件先写入 `storage/restore-staging` 的私有目录，完整写入后在同一文件系统原子重命名到活动工作区。数据库记录保留 staging/ready 状态，staging 状态不能被正常 get/list/read 当作完成的工作区使用。存储故障、取消或进程中断后，相同请求可以重试；若目录已发布但完成记录尚未提交，重试只补数据库状态，不覆盖文件。完成后重放也保留用户后续编辑。

底层 `WorkspaceService.restore(..., idempotency_key=...)` 支持受信调用方明确提供恢复身份；不传该参数时，可接续相同快照/目标的未完成恢复，但已完成目标仍报冲突。恢复和 create 分别受数据库租约及文件 fence 保护；部署时 staging 与活动目录必须在同一文件系统。未完成恢复的数据与预占保留用于重试，不通过无条件删除所有者来释放目标。

未完成的恢复记录及所有者预占会保留源快照引用，阻止源快照删除；完成后解除这一引用。staging 文件在相同操作重试时重新构建，成功后原子移动；放弃后长期未重试的 staging 数据目前保守保留，维护清理必须先停止写入并核对操作记录，不能直接按文件年龄删除。已标记 ready 的重试仍验证目标目录存在且未被替换为符号链接。

## 验证

默认测试：

```console
uv run pytest tests/infrastructure/test_blob_storage.py tests/infrastructure/test_s3_storage.py tests/resources/test_content_gc.py tests/resources/test_workspace_concurrency.py -q
```

S3 默认测试启动本地 HTTP 协议服务，使用真实 boto3 请求序列化和响应解析，覆盖条件写、重复写校验、分页、条件删除、快照、备份、恢复及跨实例 GC；不是把返回值直接假装成 S3 成功。

真实 MinIO/S3 验收使用专用测试桶，先设置 `OPSMESH_TEST_S3_INTEGRATION=1`、`OPSMESH_TEST_S3_BUCKET`，以及可选 endpoint、region、access key、secret key、session token 对应的 `OPSMESH_TEST_S3_*` 环境变量；也可提供 `OPSMESH_TEST_S3_DATABASE_URL` 使用专用 PostgreSQL 测试数据库。测试在随机前缀下创建对象并清理该前缀，不创建或删除整个桶。默认未启用时跳过。

```console
uv run pytest tests/infrastructure/test_s3_integration.py -q
```

准确配置名称见该测试；命令不包含凭据。真实环境结果由部署验收记录提供，本模块的本地协议测试不等同于生产对象锁、跨区域复制或故障恢复保证。
