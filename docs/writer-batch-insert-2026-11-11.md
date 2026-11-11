# 落库路径的吞吐墙：`executemany` 逐行往返（2026-11-11）

> 起因：把 `spiders/jodi-oil` 的 `limit` 从 100 抬到全集 114,610 之后，
> 真跑**跑了 30 分钟没结束**。本文记录根因、实测数据、修法与它反过来约束了什么。

## 一、结论

`writer.py` 写 `crawl_items` 用的是 `cur.executemany("INSERT ... VALUES (%s,%s,%s)", [...])`。
psycopg2 对多行 `executemany` 是**逐行往返**（每行一次 network round trip），
于是落库吞吐只有 **17.6 rows/s**，与上游取数速度无关。

已改为批量写（`psycopg2.extras.execute_values`，1000 行/批）：
**520 rows/s，快 29.6 倍**；COPY 还能再快一倍（1,062 rows/s），但 `execute_values`
不需要手写转义、与现有参数化写法同构，故取它。

## 二、怎么发现的（判据链，可复现）

抬到 `limit: 130000` 后的一次性 Job（`kubectl create job --from=cronjob/...`）：

| 观察 | 值 | 说明 |
|---|---|---|
| pod `Status` | `Running` 30+ 分钟 | 卡住 |
| `/proc/1/io` | `rchar 14,889,147` / `wchar 9,495` | 读完了 11MB CSV，几乎没写出东西 |
| `/proc/1/wchan` | `do_poll`，fd 4 个，1 个 ESTABLISHED socket | 在等一个网络连接 |
| `pg_stat_activity` | `pid 61774, 'idle in transaction', 'Client', 'ClientRead', x_age=1455s, 'INSERT INTO crawl_items (run_id, idx, payload) VAL'` | **真身**：事务开着，等客户端发下一行 |

判据要点：`state='idle in transaction'` **不会**被 `state='active'` 的查询抓到。
我第一次用 `where state='active' and query like '%crawl_items%'` 查，返回 1 ——
那 1 条是**我自己那条诊断 SQL**（它的文本里也含 `crawl_items`）。
换成 `pid <> pg_backend_pid()` 后立刻变成 0，再放宽到 `state <> 'idle'` 才看到真相。

## 三、实测吞吐（5000 行，事务内跑完即 ROLLBACK，零污染）

在 `tencent`/`fd-industry` 用 runner 镜像，`FD_CRAWL_DB_URL` 指向 `fd-industry-db`：

| 写法 | 5000 行耗时 | 吞吐 | 114,610 行预计 |
|---|---|---|---|
| `executemany`（改动前） | 284.7 s | **17.6 rows/s** | **~1.8 小时** |
| `execute_values`（1000/批） | 9.6 s | **520 rows/s** | ~3.7 分钟 |
| `COPY` | 4.7 s | 1,062 rows/s | ~1.8 分钟 |

（第一次尝试用 `run_id=-1` 直接插 `crawl_items` 被
`crawl_items_run_id_fkey` 挡住 —— 改成在同一事务里先插一行 `crawl_runs` 拿真实 id，
再跑三段、每段 `rollback()`。）

## 四、修法（`fd_industry_data/writer.py`）

新增一个辅助函数，两个写入路径（`finish_run` 与 `report_run`）都改调它：

```python
_ITEM_BATCH = 1000

def _insert_items(cur, run_id: int, payload) -> int:
    if not payload:
        return 0
    from psycopg2.extras import execute_values
    written = 0
    for start in range(0, len(payload), _ITEM_BATCH):
        chunk = payload[start:start + _ITEM_BATCH]
        rows = [(run_id, start + offset,
                 json.dumps(it, ensure_ascii=False, default=str))
                for offset, it in enumerate(chunk)]
        execute_values(cur,
            "INSERT INTO crawl_items (run_id, idx, payload) VALUES %s",
            rows, page_size=len(rows))
        written += len(rows)
    return written
```

**语义不变**：同一事务、同一 `(run_id, idx)` 顺序（`start + offset` 保证跨块连续）、
同一 payload JSON。分块只为给单条语句一个上界（内存/包大小），不是性能调优点。

**回滚点**：`git revert` 这一个 commit 即回到逐行 executemany；无 schema 改动、
无数据迁移、无可观测语义变化，回滚不需要重放任何数据。

## 五、它反过来约束了什么（重要）

1. **`limit` 不能只按"全集多大"定，还要按"写得动多少"定。**
   我先前只量了全集（114,610）就把 `jodi-oil` 设成 130,000 —— 这是判断失误。
   按现状写法 1.8 小时贴着 `activeDeadlineSeconds=7200` 跑，一旦被杀：
   `finish_run` 不会执行 → 该行停在 `running` → single-flight 让这个源
   **再锁死 8.5 小时**（`FD_STALE_RUN_SECONDS` 默认 30600）。
   故现在把 `jodi-oil` 设成 **20000**（≈19 分钟），并把"抬到 130000"挂在新镜像上线之后。

2. **runner 日志里的 `in 46.6s` 不含写库时间。**
   `finish_run`（写库）在打印之前执行，但打印用的是写库**之前**取的 `finished`
   时间戳。所以 `jodi-gas -> success rows=16556 in 46.6s` 背后还有约 15 分钟的落库
   —— 只看这一行会以为 1.6 万行是几十秒写完的。判断一次运行的真实代价要看
   **pod 从 Started 到 Completed 的墙钟**，不是这行日志。

3. **这条墙对所有大 limit 源都成立**，不止 jodi-oil：
   `jodi-gas` 16,556 行 ≈ 15.7 分钟、`usaspending-awards` 6,183 行 ≈ 5.9 分钟、
   `mohurd-yearbook` 5,514 行 ≈ 5.2 分钟。所以这次改动不是为单个源做的。

4. **生效路径**：`fd_industry_data` 随**镜像**发布（运行时 checkout 的只有 `spiders/` 内容），
   故本次修复要在新 `sha-<commit>` 镜像经 AppSet 滚上生产后才生效
   （`k8s/release.yaml` 记录 tag，AppSet 定义在 fd-infra-deploy）。

## 六、验证

- `tests/test_writer_batch_insert.py`（新增 6 例）：空 payload 不触发 SQL、
  不出现 `cur.executemany` 调用、分块后 `(run_id, idx)` 全局连续、
  单批大小有上界且尾块正确、源码闸（两处调用点都是 `_insert_items`）。
- `tests/test_writer_cap.py` 仍绿（落库帽语义未变）。
- 离线全量：**1591 passed, 2 skipped**（排除 `tests/real` 与依赖跨仓
  `fd_open_data_mcp` 的 `test_pharma_facts.py`）。
- 端到端（新镜像上线后补）：`jodi-oil` 跑 `--limit 130000`，应在数分钟内完成且
  `crawl_items = 114,610`。

## 七、教训

- **"慢"要先分清是取数慢还是落库慢。** 取数阶段有明确超时与日志；落库阶段没有日志、
  没有进度，只有一个大开着的 `idle in transaction` 事务。查法是看
  `/proc/1/io` 的读写比 + `pg_stat_activity` 的 `state`（尤其
  `idle in transaction`），而不是只看 `state='active'`。
- **诊断 SQL 自己也会污染判据。** `query like '%crawl_items%'` 会命中查询者本人；
  一定要 `pid <> pg_backend_pid()`。
- **默认值再次是隐式契约**：`executemany` 是"最像 ORM 风格"的写法，读代码时看不出它会
  每行往返——只有压到万级行数才暴露。和 `--limit 100` 是同一类：默认值不会写在任何
  manifest 里，所以 review 看不见。
