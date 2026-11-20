# 东湾海堤巡检归档站

巡检站收取堤顶测点的整数振动原样。现场员停采后把完整原帧交给归档员。设备凭已提交的普通回执腾出物理槽，夜班用轮号或原回执在断源后核回原样和峰值。

## 运行

使用 Python 3.11 标准库。在项目根目录执行以下命令。控制台提供 `check_project.py`，它运行 `tests` 包中的完整回归并以退出码报告结果。演示使用自行清理的临时接收站。

```sh
python3 -B check_project.py
python3 -B -m patrol.demo
```

## 一天工作流

1. 现场员在 `PointRegistry` 登记堤顶测点 P07，再用 `open_run(points, runs, run_id, point_id, settings)` 开录 D01。`settings` 接受 `SamplingSettings` 或含 `unit`、`minimum`、`maximum`、`peak_limit` 的映射。开录保存独立的不可变口径，同轮同参复领当前记录。
2. 记录器用 `record_word(runs, slots, run_id, position, word)` 送入一基物理序和整数原样。第一字实收固定本帧出处。错序、非整数、越界和停采后的报文均拒收。越界的第二字可以用相同序位补交。
3. 现场员用 `freeze_frame(runs, slots, run_id)` 停采。空轮仍可继续录入。非空轮固定帧次、物理序、出处和口径。归档员可通过 `read_frozen_frame(slots, frame_key)` 重读当前冻结原帧。
4. 归档员用 `archive_frame(store, frame, volume_id)` 把原帧完整写到指定介质。介质净余量恰好等于帧长时可接收。成功返回已提交的 `WholeReceipt`，其中包含介质位置、原帧绑定和计量结果。同帧同内容重投复领首执并沿用原位置。
5. 设备用 `release_frame(slots, store, receipt_id)` 查已提交首执，并在同一槽状态更新中核对当前帧才清槽。旧帧首执、未知号及候选首执均不能清除当前帧。
6. 交班员用 `close_run(store, run_id, receipt_id)` 固化轮号到对应首执的清单。同轮同执复办取得原清单。错误引用不会改动它。
7. 夜班关闭已清槽的设备，重开接收站，用 `lookup_run_receipt(store, run_id)` 找到首执，再用 `lookup_receipt(store, receipt_id)` 取得 `AuditResult`。它含原样、原出处、原口径和以原件复算的计量结论。

### 计量与归档

物理序从 1 起连续递增。原样为冻结范围内的有符号整数，布尔值和浮点值均拒收。字宽按范围端点所需的最少整数字节选定，载荷以大端顺序保存。各字使用冻结的原生单位。质检比较有符号原值，峰值相等取首次物理序。限差为峰值减峰限，严格大于零才超限。演示帧 `[4,9,9,2]` 使用 mm/s 和峰限 9，峰值为 9，首次峰位为 2，限差为 0，判未超限。

`ArchiveStore.open(root, specs)` 首次初始化时接受三个名称不同的 `VolumeSpec`。介质名由小写 ASCII 字母、数字、下划线和连字符组成，开头为字母或数字，长度上限为 32。每块介质在 `media/<名称>` 有独立数据区，容量以原样字计。`station.sqlite3` 保存配置、对象目录、首执、计量和交班索引。档案完整原样位于其所指介质。元数据不扣除介质字数。

以相同配置重开会保留历史数据。配置冲突将拒绝。已初始化后只给 `root` 即可重开，缺失介质目录按离线处理。测试或演练可在 `open` 时传 `offline` 屏蔽单块介质，传 `before_media_write(volume_id, object_id)` 或 `before_archive_commit()` 在相应接点抛出 `OSError`。回执提交失败后查不到新档案，原帧可沿相同入口重试。接收站重开会清理能确认没有已提交引用的受管候选文件。

异常均可从 `patrol` 导入。非法输入、物理序及轮次状态分别使用 `InvalidInput`、`SequenceError` 和 `StateError`。已登记身份上的冲突使用 `Conflict`。容量不足使用 `CapacityError`，介质离线使用 `MediumUnavailable`，写介质及归档提交失败分别使用 `MediaWriteError` 和 `CommitError`。清槽和交班的凭据拒绝分别使用 `ReleaseDenied` 和 `CloseoutDenied`。核领未知号使用 `NotFound`，已知回执的原件、出处、内容或复算结论出现差异时使用 `AuditError`。介质不可读时，其他介质上的已提交原件仍可按各自首执领取。

公开入口位于 `patrol.runs.lifecycle`、`patrol.source.samples`、`patrol.source.freeze`、`patrol.source.release`、`patrol.archive.intake`、`patrol.handover.closeout` 和 `patrol.handover.lookup`。测点清册、轮次目录和设备槽是现场对象。接收工程能独立于它们重开。`SlotBank` 省略 `session_id` 时分配新的设备会话号。设备仿真可给定明确的会话号，每次独立设备会话使用不同值。
