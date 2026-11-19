# 接口

模块GET使用仓内样例，模块POST可传csv或rows与context，直接计算当前规则。/api/runs使用完整流水线并原子保存。每个阶段返回rows、quarantine、metrics；后续阶段还返回累计隔离。/api/upload接收multipart文件file。

## 运输实例字段

路径及之后各阶段的每条记录是**一个运输实例**，不再是一条面单：

- `instance_id`：`面单#启用序号`，实例唯一键，也是结算键（`settlement_id`）。
- `instance_no`、`chain`：本面单的启用序号与全部实例顺序。
- `instance_status`：运输中 / 已妥投 / 已退运 / 已妥投并退运。
- `current`：是否为该面单的当前实例；只有当前实例产生正式妥投与正式赔付。
- `prior_delivery_at`、`prior_delivery_scan_id`、`prior_delivery_sources`、`prior_instance_id`：链上一实例的签收证据，只作历史，`prior_merged` 恒为 false。
- `segment_reason`：采用本实例的裁决依据，说明旧签收为何不并入。

时效阶段增加 `settlement_role`（正式/历史）、`expected`（历史实例为 0.00）、`reference_expected`/`reference_reported`（历史参考值）、`settlement_note`。对账阶段 `status` 对历史实例固定为“历史”，`status_detail` 保留参考结论；指标区分 `official_expected`/`official_reported`/`official_expected_total` 与全量合计。

## 归档

- `GET /api/runs/<id>/archive`：返回当前唯一证据导出，必要时原子发布一个新证据版本；响应头 `X-Archive-Version` 给出证据版本号。重复请求不新增版本。
- `POST /api/runs/<id>/archive`：body 需 `{"version": 批次版本}`；成功后返回证据 `version`、`sha256`、`reused`。批次版本已变化返回409，旧证据版本保持可读。
- `GET /api/runs/<id>/archives`：列出各证据版本与状态（published/superseded）。
- `GET /api/runs/<id>/archives/<version>`：取回指定证据版本。
