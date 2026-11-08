# NBS 国家数据导出落地目录（A 单任务 2.7）

`data.stats.gov.cn/easyquery.htm` 在本机各出口均返回
`403 reason:UrlACL`（实测 IPv6 `2408:8256:…` 与 IPv4 `163.125.162.244` 都被拦，
与请求头 / 代理无关）。浏览器侧可正常打开，故指标树由人工导出后放这里。

## 导出步骤

1. 打开 <https://data.stats.gov.cn/easyquery.htm?cn=C01>（年度数据）
2. 左侧树展开「农业」，选中一级节点（会带全部子指标）
3. 右上角「下载」→ Excel(.xls)，存到本目录

## 文件命名（便于脚本识别，不必强制）

    nbs-agri-annual-<导出日期>.xls

## 已有文件

（等待导出）

## 拿到后由谁处理

`fd-industry-data/scripts/` 侧解析 → 建 `nbs-stats` 农业年度概念 → 期数门 + 命名门 →
与 gta-panel / akshare 年度值交叉核对（不用年鉴数覆盖其他车道）。
