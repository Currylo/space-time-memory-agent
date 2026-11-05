# 接着当前进度继续开发

2026-11-05：源码与视频交接分开版本管理。视频、录像、观测结果和演示 JSON 放在本仓库的 [handoff-20261105 Release](https://github.com/Currylo/space-time-memory-agent/releases/tag/handoff-20261105)，通过脚本恢复到原相对路径；不需要 Git LFS。

## 新电脑直接开始

```bash
git clone https://github.com/Currylo/space-time-memory-agent.git
cd space-time-memory-agent
python3 scripts/restore_handoff.py
python3 scripts/init_handoff_ledger.py
python3 demo/serve.py --review-ledger data/a2/adapter/ledger.handoff.sqlite --review-log data/a2/adapter/reviews.handoff.jsonl
```

以上恢复和播放只需 Python 3.12 标准库，不需要 GPU、模型权重或原作者数据库。完整下载约 1.71 GB，解压和本地缓存另需磁盘空间。网络不通时，从 Release 手动下载全部四个 `tar.gz` 到同一个目录，再执行：

```bash
python3 scripts/restore_handoff.py --archive-dir /path/to/downloads
```

脚本对压缩包和每个文件检查大小与 SHA256；不覆盖内容不同的本地文件，不从归档恢复绝对路径、符号链接或未登记文件。恢复后可只做校验：

```bash
python3 scripts/restore_handoff.py --verify-only
```

## 两个明确分开的进度点

| 入口 | 运行与结果 | 录像证据 |
| --- | --- | --- |
| http://127.0.0.1:8765/showcase.html | 当前增强版：`unified-mot20-05-coverage1280-rulesv3-20261105-003`，停留88、摔倒0、人数变化0 | `coverage-platform-20261105-r1`，88段录像 |
| http://127.0.0.1:8765/showcase.html?profile=baseline | 基线：`unified-mot20-05-detect-pose-v2-001`，停留8、摔倒0、人数变化10 | `unified-platform-20261104T1507Z`，18段录像 |

两者都使用同一段132.6秒、3315帧、25 FPS的MOT20-05样例。当前增强版以H.264原视频加网页Canvas叠加展示检测与轨迹；基线保留已有分析叠加视频。两版候选都是模型观测派生结果，不是独立人工真值。增强版0条人数变化与观测状态规则修正有关，不应为了对齐旧数量而补造候选。

## 附件内容

| 附件 | 大小（十进制MB，约） | 内容 |
| --- | --- | --- |
| `handoff-20261105-demo.tar.gz` | 185 | 原序列、H.264版本、基线分析叠加视频、预览图、4份主展示JSON和来源许可说明 |
| `handoff-20261105-baseline-evidence.tar.gz` | 497 | 18段平台录像、时间对齐图与元数据 |
| `handoff-20261105-coverage-evidence.tar.gz` | 1017 | 88段平台录像、时间对齐图与元数据 |
| `handoff-20261105-continuation.tar.gz` | 13 | 4个分析运行的配置/结果/观测、对照报告和106条候选事件来源种子 |

文件清单和校验值见 [manifest.json](../handoff/manifest.json) 与 [SHA256SUMS](../handoff/SHA256SUMS)。原片通过恢复脚本同时放回 `data/a2/unified/MOT20-05-raw.mp4`，可以继续分析，不只用于浏览器播放。

## 复核与平台状态

初始化脚本从公开演示事件的来源种子创建**新的** `ledger.handoff.sqlite`，106条候选全部 `pending_review`，0条人工复核记录。它不复制原SQLite数据库，不发送HTTP平台事件，也不导入虚构的网络请求记录。若目标数据库已存在则拒绝覆盖，避免丢失你之后的复核历史。

使用上面的启动命令后，可以回看录像、查看依据、提交你自己的复核并保存历史。原平台事件ID仅说明交接快照的来源：新电脑没有原Frigate服务，不能据此声称本机已部署平台或已创建这些事件。要继续平台实验，需另行准备Frigate配置和视频输入、建立新的运行编号与回放身份。不要把旧事件ID作为新平台产生的事件。

浏览器流程验收时可选择新数据库路径；正式开发中的结论由实际操作者提交，真实复核数据库仍不上传。需要测试依赖时：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.test.txt
.venv/bin/python -m unittest discover -s tests -v
```

## 模型与旧演示范围

模型权重、GPU环境、第三方源码、密钥没有包含在媒体包中。继续检测/姿态推理前，按 `components.lock.json` 和运行配置中的模型SHA256准备 `models/yolov8n.pt`、`models/yolov8n-pose.pt`，并建立目标机器的GPU环境；仅调整规则时可以使用已交接的观测与结果。

本次交接覆盖当前MOT20主展示及其全部106段平台证据。旧 `index.html`、`rules.html`、`showcase_cases.html` 和三分案例页所用的Roboflow、MOT17、GMD视频不在这个包中；它们的分发范围还需单独核对。因此旧E00/E03/E04样例测试仍可能跳过，`/healthz` 仍检查旧E00资产，可能返回503；判断当前主展示以本交接校验和页面实际播放为准。

## 接下来做什么

1. 师弟按上述命令独立恢复，记录操作系统、Python、提交SHA、播放结果与缺失项。
2. 完成两版主展示的播放、拖动、候选定位、时间同步、录像证据和独立复核验收；先复现再修复问题。
3. 在Issue #1/#2记录验收证据并通过PR提交修复；源码导入和本次媒体交接不等于这两项已全部验收。
4. 与负责人对齐业务定义后，再推进独立标注与评价、现场接入、阈值校准和困难样本回归。细则见 [ROADMAP.md](ROADMAP.md)。

## 来源与许可

样例来源：[MOTChallenge MOT20官方页面](https://motchallenge.net/data/MOT20/)，序列MOT20-05。视频及其转码、分析叠加、录像切片与证据图按 **CC BY-NC-SA 3.0** 保留署名、非商业与相同方式共享条件；官方页面还提示可能适用第三方来源条件。详见恢复后的 `handoff/MEDIA_NOTICE.txt` 和 [许可链接](https://creativecommons.org/licenses/by-nc-sa/3.0/)。此说明不替整个项目或第三方模型重新声明许可证。

## 本次交接验证

- 在独立源码目录从四个附件恢复1193个资产，逐文件SHA256匹配。
- 解码108个播放视频的首尾帧：2个主播放视频和106段平台录像；H.264原画为3315帧、25 FPS。
- 两个profile的接口分别返回18/88条候选；视频与录像Range请求返回206。
- 106条候选全部通过现有服务端来源、事件身份、时间窗口、录像覆盖及文件哈希校验。
- 两个profile各完成一次临时数据库复核保存检查；原数据库和交接种子没有修改。
- 源码测试发现131项：110项通过，21项因旧E03/E04资产缺失跳过。新增归档路径、覆盖保护和独立ledger测试4项通过。

以上不替代师弟在第二台电脑的独立复现，也不表示已验证新机GPU推理、现场效果或完整投屏交互。
