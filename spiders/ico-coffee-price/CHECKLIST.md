# ico-coffee-price 接入检查清单（unified-source-onboarding）

- [x] 目录名 = manifest `name`（`ico-coffee-price`；入口 `run_ico_coffee_price`）
- [x] `spider.py` 提供 `run_ico_coffee_price(limit=N) -> list[dict]`，只依赖标准库
      `urllib/json/re`（无新依赖；不接 PDF 面——镜像未预装 PDF 解析库）
- [x] manifest 字段齐全：name/label/source_url/functions/columns/concepts/entities/fetch
- [x] `site` 未写（只访问 data.ico.org 公开 JSON API，2026-11-07 实测直连可达，走缺省执行位 tencent）
- [x] **未写 `schedule`**——静默合入，试跑验证后由人工门点亮
- [x] golden 样本 `golden/001-ico-coffee-price.json`（锚 2026-01-02 窗口起点深历史值：
      I-CIP=297.71，min_rows 3；不锚当月/最新交易日值——ICO 对当月值做日间修订）
- [x] 失败即红：主备两数据面（`/globe/icip` 宽表 + `/globe/icip/history` 列式）均失败
      或 0 数据行 → `RuntimeError`；缺失值格跳过，不 0 填、不造数
- [x] 历史深度如实声明：匿名窗口 2026-01-02 起（实测 217 个交易日）；数十年深历史需
      ICO 门户订阅（付费）或邮件 stats@ico.org 索取 Excel，不伪造更早历史
- [x] 本地自检：golden 样本经 `fd_industry_data.health.samples.validate_sample` 校验通过，
      锚经 `fd_industry_data.loader.load_spider_module` 重放命中（2026-11-07）
- [x] 仓级自检：`validate_manifests.py` / `check_manifest_commands.py` /
      `conformance_gate.py --roots .` 全绿
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`，红则修完再推）——合入前由人工/CI 确认
- [ ] 等一个 dispatcher tick（≤5 分钟），Console 源清单出现该源（crawl_sources）
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`，看 crawl_runs 记录与产出
- [ ] 点亮：manifest 补 `schedule` 提交（AppSet ~3 分钟内渲染 CronJob）——**人工门，spider-heal 不点亮**
- [ ] 深历史扩窗（可选，不阻塞）：拿到 ICO 门户凭据后按同一端点接入登录会话，把窗口
      从 2026-01 向前扩；另行评审，不随本单元合入
- [ ] 需要指标语义时，另行走统一指标注册目录治理（列→绑定→verified→标识符），
      不阻塞爬取本身
