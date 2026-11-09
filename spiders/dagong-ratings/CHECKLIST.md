# dagong-ratings 接入检查清单（批次六，2026-11-08）

- [x] 目录名 = manifest `name`（`dagong-ratings`；入口 `run_dagong_ratings`）
- [x] `spider.py` 提供 `run_dagong_ratings(limit=, topic=, pages=, page=) -> list[dict]`，纯标准库
- [x] **端点实读**：栏目页内联 Vue 找到 `POST /api/construction/openApi/getContentInfoPageList`
      （JSON body 的 `pageNum/size/channelCode/startTime/endTime/topic/content` 照抄页面源码）
- [x] **坑 1 留痕**：参数名是 `size` 不是 `pageSize`；用 `pageSize` 实测 HTTP 500（README 坑 1）
- [x] **坑 2 留痕**：表单编码实测 HTTP 415，必须 `application/json`（README 坑 2）
- [x] **坑 3 留痕**：`topic` 是报告名称关键字，不是行业字段；银行口径 = `channelCode=A009003002` + `topic=商业银行`（README 坑 3 + 过滤证据表）
- [x] **字段语义实测**：`a1` PDF 相对直链 / `a8` 主体等级 / `a9` 债项等级（可空、可多级别）/
      `a12` 展望（可空）/ `pubTime` 评级时间 / `topic` 报告名称 / `filePath` 详情页 / `infoId` 主键
- [x] **空值不伪造**：2018 及以前条目 `a8/a9/a12` 为空 → 输出空串；`a1` 缺失 → `pdf_url` 空
- [x] **topic/频道证据**：`""+商业银行=172` / `A009003002+""=6679` / `A009003002+商业银行=94`（与侦察簿 94 一致）
- [x] **分页**：请求 `pageNum`(1 起)/`size`(10)；响应 `total/size/current/pages`；94 条 10 页，末页 4 条
- [x] **四个参数均真实生效**：`limit=5→5 行`；`pages=2→20 行`；`page=5→2021-07-29..2019-07-29`；
      `topic=南洋商业银行→5 行`
- [x] **`page` 参数钉 golden**（增量参数，README 留痕理由）：`pages=1,page=5` 锚第 49 条
      南洋商业银行（中国）有限公司 2020-06-11 AAA/AAA/稳定
- [x] manifest 字段齐全；`columns` 12 键与输出行一一对应；四个声明参数全部可达
      （`declared-param-reachability` 通过）
- [x] `data_period: irregular` 有实测依据（94 条集中于 2014-2026 的 6-8 月批量发布）
- [x] 失败策略：单页失败留痕续跑、空页即到底、全失败 `RuntimeError`、缺标题丢条、相邻请求间隔 1s
- [x] 首爬实测：默认参数 **30 行 / 4.12s**（3 请求 + 2×1s 间隔）
- [x] golden schema+replay：`schema []`、`ok=True rows=10 diffs=[]`（首次即通过，1.35s）
- [x] `validate_manifests`：**145 manifests, 0 violations**
- [ ] `conformance_gate` 全仓 PASS：当前 **FAIL 1 violation**，唯一违规是
      `spiders/ratings-ccxi` 缺 `manifest.yaml`（并行单元，任务 6.1，非本单元；本单元零违规）。
      待该单元补 manifest 后复跑即应 `PASS … 0 violations`
- [ ] push 后 CI gate 绿——合入前由人工/CI 确认
- [ ] 点亮后试跑：验证 crawl_runs 记录与评级公告落库（默认口径对标 30 行/次）
