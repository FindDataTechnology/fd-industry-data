# 新源接入检查清单（unified-source-onboarding）— spiders/nra-rpi/

生成单（spider-heal，ticket 20261107-nra-rpi-3a5952f4）从零新建；逐项状态如下。

- [x] 目录名 = manifest `name`（`nra-rpi` → `run_nra_rpi`）
- [x] `spider.py` 提供 `run_nra_rpi(limit=N) -> list[dict]`，零第三方新依赖：
      HTTP 走 urllib（标准库），文本层走平台共享层 `fd_industry_data.pdftext`，
      缺失时回退内嵌 stdlib 提取器（同契约，Flate/WinAnsi/Type0+ToUnicode/layout 行重建）
- [x] manifest 字段齐全：name/label/source_url/functions/columns（version "2"）
- [x] `site` 不写（直连 restaurant.org，无需离开 tencent）
- [x] **不写 schedule**——本 PR 仅静默合入；点亮与排期由平台人工决定
- [x] 本地自检：`python3 scripts/health_verify.py --ticket reports/health-tickets/20261107-nra-rpi-3a5952f4.yaml`
      （验证证据见 PR 描述；本地为标准库回退路径，镜像运行走平台 pdftext，两者契约一致）
- [ ] push 后 CI gate 绿（Jenkins `fd-industry-runner`，红则修完再推）
- [ ] dispatcher tick / Console 源清单出现该源
- [ ] 试跑：Console「立即触发」或 agent `platform_trigger`
- [ ] 点亮：人工确认后 manifest 补 `schedule` 提交（本 agent 不执行此步）
- [ ] 首爬回补知悉：本源无归档逐期 URL（CDN 目录年份 ≠ 报告期），入口未声明
      window_start/window_end —— 每次运行只产最新一期的近 5 个月 × 3 指数（≤15 行），
      回补窗口不生效，属已知并接受的语义
- [ ] 指标语义注册（列→绑定→verified）：不阻塞爬取，后置
