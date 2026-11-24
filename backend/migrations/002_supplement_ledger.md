# 002 补传巡检台账与加查清单版本

应用启动时由 SQLAlchemy `create_all` 补建以下新表，已有表不受影响：

- `ingest_lock`：单行补传串行锁，保证两批补传交错到达时按顺序完整处理。
- `supplement_batches`：补传批次，`idempotency_key` 唯一，重跑成功批次不重复出单；
  记录归档行数、整份替换数、幂等跳过数、变动路段数与关联清单版本。
- `inspection_ledgers`：晚到记录按（步道、路段、巡检日期、版本）归档；
  同日更正只让更高版本整份替换，被替换记录 `is_active=false` 并留痕。
- `segment_states`：每个（步道、路段）当前生效巡检，增量试算只重读变动路段。
- `checklist_versions`：加查清单版本，含规则快照、参数、分母统计与变动路段；
  同一时刻只有一个 `active` 版本，发布失败时旧版保持 `active`。
- `checklist_items`：清单逐段加查项及 `evidence` 命中依据，随版本原子增删。

原有的 `datasets`、`runs`、`deliveries` 链路保持不变，整批运行仍可独立复跑。
