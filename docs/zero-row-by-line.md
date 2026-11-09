# 零行率分线口径（`scripts/zero_row_by_line.py`）

> change `harden-crawl-delivery` 任务 3.1。**全库零行率不能单独引用** —— 它把两条
> 语义完全不同的线混成一个数字。

## 为什么要分线

「成功但写 0 行」在两条线上的含义相反：

| 线 | 0 行的含义 | 正常范围 |
|---|---|---|
| 行业线（本仓 `spiders/*/manifest.yaml`） | 该周期上游确实没出新东西（快照源偶发空窗） | 1–2% |
| law/loader 线（跨仓成员，`config/health-scope.json` 的 `excluded_sources`） | 适配器没取到数 / 落库没落成 | 应在 0–5%，实测 **83%** |

合成一个数会同时造成两种误判：行业线看起来「有零行问题」（其实没有），
law 线的真问题被 21% 这个平均数稀释。

## 分线规则（机械可复算）

1. `spiders/<source>/manifest.yaml` 存在 → **industry**
2. 该源在 `config/health-scope.json` 的 `excluded_sources` 里 → **law-loader**
3. 其余 → **other**

顺序不能反：本仓有 manifest 就是行业线，哪怕它同时出现在跨仓排除表里。

## 复算

```bash
export FD_CRAWL_DB_URL=<中央库 DSN>
python3 scripts/zero_row_by_line.py --days 7          # 表
python3 scripts/zero_row_by_line.py --days 7 --json   # 机读
python3 scripts/zero_row_by_line.py --sql             # 只打印它跑的那条 SQL
```

脚本只读；`--sql` 保证数字不依赖本脚本也能复算：

```sql
select source,
       count(*) filter (where status = 'success')                        as ok_runs,
       count(*) filter (where status = 'success'
                          and coalesce(rows_written, 0) = 0)             as zero_runs
from crawl_runs
where started_at > now() - interval '{days} days'
group by source
order by zero_runs desc, ok_runs desc
```

## 实测（2026-11-10 复算，近 7 天）

```
line         sources   ok_runs    zero     pct
industry         115       708      11   1.55%
law-loader        14       228     190  83.33%
other              2         0       0    0.0%
TOTAL                      936     201  21.47%
```

与 2026-11-09 那次（行业 1.3% = 10/667、全库 21.3% = 200/938）的差别只是窗口
向前滑了一天，不是口径变化；两次都说明同一件事：**零行的绝对量几乎全在 law/loader 线**。

### 零行最多的源

```
industry:    portwatch-imf 3/8    ted-notices 2/8    eastmoney-data 1/11
             eastmoney-futures 1/8   cninfo-operating 1/5   dce 1/4
law-loader:  akshare 110/140      wbgapi 14/14       flk-law-embed 7/7
             mfa-treaty-crawl 7/7  mfa-treaty-load 7/7  party-regulation-crawl 7/7
             ccdi-supervisory-load 7/7                  ccdi-supervisory-crawl 7/7
```

`akshare` 一条就占全库零行的 **55%**（110/201）。先修它和 `wbgapi`，比在行业线
上做任何事都划算 —— 这也是 3.5 交接单的内容。

## 边界

- 「零行」只看 `rows_written = 0`，**不代表数据陈旧**：一个每天重写同一旧期的源
  行数漂亮、水位不动。新鲜度看 change 的 D5（数据期水位），不看这张表。
- `blocked-config` 与 `failed` 不在分母里（分母是 `status='success'`）。
- `other` 目前只有 2 个源（`example-stock`、`yfinance`），都没成功运行 —— 是
  样例/遗留源，不该被当成产品线。
