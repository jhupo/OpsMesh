## 变更说明

<!-- 请简述本次 PR 解决的问题、核心改动及设计考量 -->

## 关联 Issue

Closes #

## 变更类型

- [ ] Bug 修复（`fix`）
- [ ] 新特性（`feat`）
- [ ] 重构 / 性能优化（`refactor` / `perf`）
- [ ] 文档与示例（`docs`）
- [ ] 测试与 CI / 安全工程（`test` / `chore` / `ci`）

## 自检清单

- [ ] 已在本地通过 `ruff format --check src tests examples`
- [ ] 已在本地通过 `ruff check src tests examples`（含 Bandit `S` 安全规则）
- [ ] 已在本地通过 `pytest -v` 并为新逻辑/修复补充了对应单元测试
- [ ] 若改动公共 API 或配置项，已同步更新文档与 `CHANGELOG.md`
- [ ] 若改动 `src/opsmesh/plugin/on.py`，已同步更新 `src/opsmesh/plugin/on.pyi`
