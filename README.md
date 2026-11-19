# 跨境包裹扫描补传与妥投时效核验

供跨境集运客服质控员核对原始记录、处理差异并封存证据。

## 启动

```sh
docker compose up --build -d
docker compose exec -T app pytest -q
```

打开 http://localhost:18247 。首次进入自动运行仓内脏样例。工具条可重跑、切换样例或上传CSV。SQLite保存在命名卷records中，重建服务保留批次。

## 模块

- 1. 扫描采信：`/scans`，`/api/scans`。
- 2. 节点映射：`/nodes`，`/api/nodes`。
- 3. 时区归整：`/timezones`，`/api/timezones`。
- 4. 路径裁决：`/paths`，`/api/paths`。
- 5. 时效计算：`/timeliness`，`/api/timeliness`。
- 6. 赔付对账：`/compensation`，`/api/compensation`。
- 7. 批次归档：`/archives`，`/api/archives`。

## 核心规则

以事件发生时刻重建路径，补传不改变既有妥投时间；承诺时钟按目的地节假日停表。

同一面单退运后重新启用时，按每次启用拆成独立运输实例连续追踪：旧签收只作历史证据，不并入也不结束新一票；每个面单只在最新实例上产生唯一正式妥投与一次正式赔付。补传或重试不会重复创建实例或重复计赔。

## 演示

1. 打开首页，核对当次计算、差异和隔离原因。
2. 切换模块查看源行及裁决依据；路径页顶部“履约实例裁决”说明采用哪个实例、旧签收为何不并入。
3. 重跑相同样例，批次号与行数保持一致；切到“面单重新启用批”查看拆分结果。
4. 复核后导出或发布一份证据；重复点击只沿用现有证据版本，发布失败时旧版保持可读。版本已变化的复核或发布返回409。

## 输入与输出

输入列见 `docs/DATA-DICTIONARY.md`。项目包含dirty.csv、clean.csv、legacy.csv，统一走实际导入路径。上传限制4MB，拒绝不带时区的时间。业务键按字符串保留，Decimal参与计算，空值不会当零。

`POST /api/runs` 接收sample或csv；`GET /api/runs`返回历史批次；`POST /api/runs/<id>/review`需要version与decision；`GET /api/runs/<id>/archive`返回唯一证据导出；`POST /api/runs/<id>/archive`发布证据版本；`GET /api/runs/<id>/archives`列出证据版本。

## 范围

样例均为虚构。单机复核工具不连接生产系统；不提供登录、远端数据库或自动支付。内存计算按4MB输入上限控制，SQLite事务保证结果完整写入。

## 目录

app为服务与规则，sample_data为输入，tests为测试，docs为定义与深度登记。
