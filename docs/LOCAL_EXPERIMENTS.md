> 原工程本地实验说明，随 2026-11-05 源码导入归档。以下结论是历史记录，不代表此公开仓库已重现；报告、运行数据、媒体和第三方源码未上传。当前安装、验证和缺失资产边界以 [SOURCE_IMPORT.md](SOURCE_IMPORT.md) 为准。原文中的项目相对路径从仓库根目录解释。

# 时空记忆智能体实验代码

**2026-11-05 方案 A 继续推进：** 人员覆盖增强版 88 条停留候选现已绑定本次 88 个平台事件及完整证据录像，同页可复核；均 pending_review。旧版 18 条保持独立。新增 [A3 现场接收准备页](http://127.0.0.1:8765/field.html)、现场清单/检查工具与行为定义确认单；用户确认暂无现场数据，A3 仍待材料。127 项单测、30 项浏览器检查、7 项隔离复核与88项重复提交验证通过。详见 [本轮执行报告](reports/方案A-公开原型完善与A3准备-20261105.md)。


**2026-11-05 人员覆盖更新：** 主入口默认“人员覆盖增强版”，保持同一原视频，检测输入从 640 改为 1280；每帧平均已关联轨迹 8.36→48.98。页面区分检测/建轨/区内计时，可点击目标或展开当前轨迹查看停留秒数。新运行候选为 88/0/0（停留/摔倒/人数变化），本轮已接入88个独立平台事件；`?profile=baseline` 或版本下拉可访问上段已验收的旧版 18 个平台事件。增强版不复用旧事件、ID 或人工意见。仍可能漏检/断轨，未报告召回率或真实总人数。详见 [人员覆盖优化报告](reports/人员覆盖与停留统计优化-20261105.md)。117 项单测与 25 项新浏览器验收通过。

**旧版平台闭环基线：[查看已验收的 18 条事件](http://127.0.0.1:8765/showcase.html?profile=baseline)。** 同一段 MOT20-05 原序列视频（132.6 秒），共享时间轴和轨迹，实际同时运行停留、摔倒、人数变化；本次候选 8 / 0 / 10，非人工 GT。**同源候选已通过持久适配器写入 Frigate：** 摄像头 `a2_unified_mot20_05`、回放身份 `unified-mot20-05-20261104T1507Z-r1`、18 条候选事件、18 个完整证据录像；复核默认 pending，真实复核待人。旧分案例页保留为 `showcase_cases.html`，旧复核页为 `review.html`。同源平台执行报告见 [同源平台闭环执行报告](reports/同源平台闭环执行报告.md)（[HTML 同步阅读版](reports/同源平台闭环执行报告.html)），证据目录 `reports/unified-platform-20261104T1507Z/`。**2026-11-05 审核后 R1—R3 修正见 [同源平台闭环修正报告-R1R3](reports/同源平台闭环修正报告-R1R3.md)（[HTML](reports/同源平台闭环修正报告-R1R3.html)）；复审 F1/F2 收尾见 [同源平台闭环复审修正-F1F2](reports/同源平台闭环复审修正-F1F2.md)（[HTML](reports/同源平台闭环复审修正-F1F2.html)）。首次报告中的停留窗口和微秒级精度表述以修正报告为准。**

老师 A2 原文目标是确认平台功能链路，独立 GT/全面批量指标不作为延后 A3 的额外硬门槛；严谨效果评价仍须独立标注。当前主要未完成工作是业务定义对齐以及 A3—A5 的现场观察、区域校准、困难负样本。

## 同源平台闭环（P0，2026-11-04）

- **输入与回放**：原始 `data/a2/unified/MOT20-05-raw.mp4`（mp4v，SHA256 `e5d1099…bc8e`）保留不动；帧号一致的 H.264 派生输入 `deploy/frigate/runtime/input/a2_unified_mot20_05_h264.mp4`（3315 帧、25 FPS、132.6 秒）由脚本生成并记录哈希。
- **时间映射**：本次回放 `replay_id=unified-mot20-05-20261104T1507Z-r1`、`loop_index=0`；14 个录像段 + 3 个指定开始/中段/后段对齐点 + 逐秒代表片段（首/中/末）核对烧录帧号，最终漂移约 0 ppm、RMSE 0.000 秒。未使用旧 `LOOP_START`、墙钟取模或历史录像 `min(start_time)`。
- **事件映射**：18 条候选全部 `created`；停留事件覆盖 10.00 秒窗口，人数变化事件覆盖精确 60.00 秒窗口；平台事件、录像、证据窗口和复核状态分字段保存。`score=null`，类型为 `rule_boolean_not_a_probability`，不把规则满足值写成概率。
- **幂等/恢复**：真实 18 条候选再次提交全部 `duplicate`；平台手动事件 18→18、ledger attempts 36→36；并发、崩溃后对账、超时 unknown 保留使用独立 mock/临时 ledger 实测，未新增平台事件。
- **复核**：当前 18 条均为 `pending_review`，真实复核历史为空；没有填造 `user/demo_operator/confirmed`。浏览器复核流程在独立测试 ledger 上验证，并在报告中明确标注“测试记录，不是真实意见”。
- **浏览器验收**：本次页面独立 51 项检查通过（桌面 1440×1000、390×844、播放/暂停/拖动/变速/全屏、原画/分析切换、观察未满 60 秒、姿态不足、空结果、录像缺失、平台离线、对齐失败、测试复核提交/刷新/历史、旧页面可访问、控制台无错误）。录屏 1 分 23 秒；证据见 `reports/unified-platform-20261104T1507Z/browser/`；修正后新增平台播放器跨分片拖动/尾部播放、R1 更正窗口和服务端复核负例。
- **测试**：`.venv-gpu/bin/python -m unittest discover -s tests -v` 共 **111 项通过**（原 79 项基线 + 32 项新增）；`.venv/bin/python` 下 110 项通过、1 项因缺少 `sklearn` 导入失败，属于环境差异，报告未隐藏。
- **访问**：仅监听回环地址。远程使用 `ssh -L 8765:127.0.0.1:8765 -L 15000:127.0.0.1:15000 <user>@<host>`，然后打开 `http://127.0.0.1:8765/showcase.html`；不把未鉴权平台直接开放到公网。受控回放结束后摄像头保持配置但 `enabled: false`，不会自动循环重开；18 个手动事件和录像保留。

根据上级目录《时空记忆智能体-分阶段实验文档.md》从 E00 开始实施。当前主线是 **YOLOv8n 检测 → Supervision ByteTrack 单镜跟踪 → 带 ID 回放 / 轨迹 JSONL / 证据查询 → E04 区域统计与规则候选**，并已在 MOT17 公开基准上完成 E02 第一轮定量评价。所有视频、权重和结果保存在本地。

## 方案 A 当前状态（2026-11-04）

接入层补充完成：持久 SQLite ledger、跨次幂等、超时对账、独立人工复核历史；旧 confirmed 标为脚本写入。详见 [接入层实施记录](reports/A2外接适配器接入层实施记录.md)。总览已同步新增外接案例录屏、关联录像、ROI v2 和持久去重/复核状态。

**面向汇报的统一演示入口：[http://127.0.0.1:8765/showcase.html](http://127.0.0.1:8765/showcase.html)。** 同屏展示公开样例分析画面、区域观测数、停留候选、触发依据与证据定位，并提供公开数据评价和 Frigate 录像入口。`15000/review` 是原生录像页；单候选已通过 HTTP 写入平台并完成 Explore 播放；手动 API 事件仍不生成原生 Review。Chrome 13 项交互检查通过，详见 [实施记录](reports/统一演示入口实施记录.md)。

**当前优先级：A1 部署/回放/门控（已通过）→ A2 接入层/三类单例/评价小批次/统一复核页（已建立，A2 未完成）→ stay/crowd 人工起止、原始无叠加长视频、聚集定义、批量指标、更多摔倒复核 → A3 现场验证（与 A2 分开验收）→ A4 区域校准 → A5 困难负样本。** 公开数据离线实验作为已有模块和参考，新增算法消融列为后续项。完整要求与差距见 [方案 A 推进与验收清单](reports/方案A-推进与验收清单.md)。

Docker 已安装，Frigate `0.18.2-cfa08c5` 已运行。17:41 重启后实际输入为 `/input/public_demo_h264_long.mp4`、仅 `-re`，无 `-stream_loop`；新 H.264 录像 VOD 链和 Chrome Review 回放通过。10 秒门控实测 10.102 秒（累计语义）；持久 ledger/幂等/复核历史完成；摔倒单例 `S1_Fall_01` 进入平台并以 `uncertain` 复核；人数变化单例用 168 秒固定机位 PEDS1 demo 接入，复核 pending（带叠加，仅流程检查）。详见 [A1 验收报告](reports/A1-Frigate部署与回放验收报告.md)、[接入层记录](reports/A2外接适配器接入层实施记录.md)、[评价小批次/复核页](reports/A2评价小批次与统一复核实施记录.md)、[门控/外接/ROI 报告](reports/A2-门控核验与外接候选闭环.md) 与 [部署说明](deploy/frigate/README.md)。

- **A1 成熟平台：部署、回放、10 秒门控与单候选外接通过。** Frigate 已启动，API/录像/Review 可用；门控实测 10.102 秒；HTTP 手动事件、clip 关联、Explore 播放完成。逐帧轨迹导出仍缺失；手动 API 事件不生成 Review。详见 `reports/A1-Frigate部署与回放验收报告.md`。
- **A2 MOT17：七场景固定基线与严格 tracker-only 完成。** 5,316 帧、112,297 个行人 GT 框/546 ID；COMBINED HOTA 37.27、IDF1 44.09、MOTA 34.55。固定同一检测后，BoT-SORT HOTA 38.19/IDF1 45.20，高于 Ultralytics ByteTrack 36.37/42.89，但相对 Supervision 主基线只高 HOTA 0.92/IDF1 1.11，FP 多 1,490。详见 `reports/A2-MOT17多场景评价报告.md`。
- **A2 Loitering：轨迹级评价与泛化分析完成。** 19,737 条轨迹、3,860 条徘徊；5 折池化 RF F1 0.606/AP 0.530，LR 0.601/0.525；5 折均值 RF 0.603±0.104、LR 0.596±0.138，配对 t 检验 p=0.683（RF 优势不显著）；leave-one-folder-out RF 均值 F1 0.562±0.303。只有轨迹级标签，不能宣称事件起止定位。详见 `reports/A2-Loitering徘徊行为检测实验报告.md`。
- **A2 GMDCSA-24 摔倒：全 160 视频完成。** 79 Fall/81 ADL、78 个可解析事件区间；固定规则全数据视频级 F1 0.895、事件级 F1 0.803；S1+S2 开发→S3+S4 测试，视频 F1 0.976→0.800、事件 F1 0.860→0.732，跨 subject 泛化明显下降。详见 `reports/A2-GMDCSA24摔倒事件验证报告.md`。
- **A2 摔倒单例接入：完成，复核 uncertain。** 平台 `a2_fall_demo` 使用 S1_Fall_01；事件 `1793791187.189385-y9t9xj`，clip 8.88 秒，与源视频 1:1 对齐；正常负例 S1_ADL_08 未触发、未造事件。不是平台原生摔倒能力，不能写成确认通过。相对计划写出时刻的偏差 +0.000065 秒不是端到端告警延迟。详见 `reports/A2外接适配器接入层实施记录.md`。
- **A2 评价小批次与统一复核页：已建立。**`data/a2/eval_batch/` 覆盖停留/摔倒/人数变化正例和正常负例，配置冻结；stay/crowd 起止仍 pending_human。统一复核页 `http://127.0.0.1:8765/review.html` 支持选择候选、看依据、播放录像、提交复核并保存历史；复核不设配额。
- **A2 人数变化：单例接入完成，复核 pending。** 168 秒固定机位 PEDS1 crowd-counting demo，60 秒窗口 66—126 秒人数 2→6，平台事件 `1793792390.006841-an4d7q`，clip 12 秒，clip t=6 对应源 t=126（MSE 0.0）。视频带可视化叠加，只作流程检查；35 秒/280 秒重复仍被拒绝。详见 `reports/A2外接适配器接入层实施记录.md`。
- **A2 PAB：已移出视频事件验证。** PAB 是图像-文本行人异常检索数据集，没有视频/轨迹/事件起止；已改用 GMDCSA-24。核实见 `reports/A2-PAB姿态异常数据集核实报告.md`。
- **平台 vs 自研首轮对照：已完成，但不是准确率。** 连续口径：平台 122 条区域事件、自研 113 条 long-stay、匹配 108 对；累计口径：自研 147 条、匹配 119 对、平台侧未配对 3。门控实测 10.102 秒；平台是累计语义。无事件 GT，区域几乎覆盖整帧，不计算 P/R/F1。详见 `reports/A2-平台与自研同视频对照.md`。
- **门控/外接/ROI：已完成单案例闭环。** 自研 `track=1335/src=237.333s` 通过 HTTP 写入平台事件 `1793788055.850588-mx5nl8`，clip 26.67 秒 H.264；Explore 追踪详情完整播放；人工复核写入 description。平台无原生外部去重，适配器用 ledger；手动事件不生成 Review。ROI `a2-left-walkway-v1` 人工确认 3 例：穿行 1.333 秒、短停留 7.333 秒、超阈值 10.334 秒。详见 `reports/A2-门控核验与外接候选闭环.md`。
- 综合状态、验收核对和剩余缺口见 `reports/A2-方案A公开数据验证综合报告.md`。

## 当前状态

- GPU 诊断已通过：宿主 RTX 3090 / 24 GB，默认沙箱隐藏设备节点。详见 `reports/GPU诊断-2026-11-03.md`。
- 公开样例已跑通官方包 API 与本地输出适配器；结果与边界见 `reports/E00执行报告.md`。
- **E00-D 中文演示界面已实现并通过本地浏览器验收**：首页、原视频/分析回放同步、轨迹路径、证据回看、全屏和缺失文件提示；详见 `reports/E00-D执行报告.md`。页面截图、讲解脚本和演示录屏在 `reports/e00-d/` 与 `reports/E00-D讲解脚本.md`。
- **E00-B Holmes-VAU 已完成本机可运行性验证**：官方样例和公开行人视频均真实推理；固定仓库/模型/ATS 哈希，独立环境 `.venv-holmes`；峰值显存约 8.7 GB（整卡），有条件采用为 E07 候选。解释质量未评价，详见 `reports/E00-B执行报告.md`。
- **E01 公开数据基础已完成**：2 个公开视频清单与 SHA256、开发集划分、公开演示区域、轨迹/事件/困难负样本/质量标注格式与检查器；检查 0 错误、4 个实际缺口警告。现场视频、人工 GT、验证/测试集仍缺，E01 未完成。详见 `reports/E01执行报告.md`。
- **E02 公开基准定量评价第一轮已完成**：MOT17 训练集 `MOT17-02-FRCNN`，600 帧、30 FPS、官方行人 GT 18,581 框/62 ID；固定 YOLOv8n + ByteTrack；TrackEval 输出 IDF1 27.361、IDSW 36、Frag 62、HOTA 26.667、MOTA 17.001；保存预测、GT 适配、原始指标和错误案例。现场 E02 未开展，详见 `reports/E02公开基准执行报告.md`。
- **E03 公开样例工程验证已完成**：SQLite 派生存储、幂等导入、冲突检测、暂停/恢复、区域派生和证据查询；9,599 点/300 帧/82 轨迹，重复导入 0 新增，恢复与完整导入一致。现场 E02 和现场性能未通过，详见 `reports/E03执行报告.md`。
- **E04 公开样例规则功能验证已完成（v2 修正轮）**：300 帧 × 3 区域统计、可观测停留、停留/折返规则、60 秒规则“观察时长不足”、配置/证据/去重/因果性和独立规则演示页；本次 v2 实际 6 条停留候选、0 条折返候选。旧版输出保留在 `data/e04_legacy_v1/`。现场 E04 评价、E05 校准、现场 E02 仍未完成，详见 `reports/E04执行报告.md` 与 `reports/E04修正记录.md`。
- Frigate v0.18.2 已启动，新 H.264 录像的有界 VOD 链和浏览器 Review 回放已通过；A1 的保留项见验收报告。平台侧 A2 首轮同条件对照已完成。现场 E02、现场 E04、E05—E10 未宣称通过。

## E00-D 可视化演示

演示只读取已完成的 `runs/e00-public-demo-005` 结果，不重新运行模型、不调用 GPU、不联网。原视频保留在 `data/raw/`；浏览器兼容回放是独立派生文件 `demo/media/analysis_replay.mp4`，原回放未改动。

```bash
# 从本目录启动（默认仅监听 127.0.0.1:8765）
python3 demo/serve.py

# 浏览器访问
# http://127.0.0.1:8765/

# 停止：在服务终端按 Ctrl+C
```

演示页面上方会显示“公开样例 / 离线分析 / 单路视频”，轨迹页明确“本页不含规则”，视频理解标为未接入；区域统计与规则候选在独立 `rules.html` 展示。82 个编号统一显示为“累计轨迹编号”，不是独立人数。原视频 13.64 秒，只分析前 12.00 秒；时间轴灰色尾部表示未分析的 1.64 秒。

主要文件：

- `demo/index.html`、`demo/assets/`：界面代码；
- `demo/README.md`：演示专用启动、数据、验收和常见问题；
- `demo/build_demo.py`：从现有 JSONL 生成 `demo/data/demo.json`，不重跑感知；
- `demo/serve.py`：本地静态/Range 服务；
- `demo/media/analysis_replay.mp4`：H.264 1440×810 派生回放；
- `reports/e00-d/`：真实页面截图、录屏、33 项浏览器验收结果；
- `reports/E00-D讲解脚本.md`：3—5 分钟中文讲解脚本；
- `reports/E00-D执行报告.md`：实现、验证与限制。

## E04 区域统计与规则

公开样例上的规则功能验证已完成，不重新运行检测/跟踪。入口：

- 轨迹演示：`http://127.0.0.1:8765/`
- 区域统计与规则：`http://127.0.0.1:8765/rules.html`

```bash
# 生成规则输出和演示数据（只读 E00-A/E03）
python3 scripts/e04_build.py

# 带 Supervision 区域交叉核对（需要 .venv-gpu）
.venv-gpu/bin/python scripts/e04_build.py --crosscheck-supervision

# 输出核验（35 项）
python3 scripts/e04_verify.py

# E04 单测（29 项）
python3 -m unittest tests.test_e04_rules -v
```

实际结果：

- 9,599 点 / 300 帧 / 82 轨迹；6,942 条 E03 区域派生；
- 300 帧 × 3 区域逐帧观测数；
- 74 条区域停留片段；10.0 秒演示阈值下本次 v2 实际 6 条停留候选；
- 折返规则 0 条候选；目标 077 最接近（1 次有效反转）；
- 60 秒人数变化规则 `insufficient_observation`，未缩短窗口；
- 配置/版本/输入/区域哈希、仅含触发时及之前的触发特征、证据帧时刻、模拟触发视频时刻、实际写出墙钟时间、去重键和生命周期均已保存；
- 截断输入核验完整过去候选集合与触发特征一致（26 个过去候选签名）；
- 35 项输出核验、34 项浏览器验收、29 项 E04 单测、51 项项目单测、原 E00-D 33 项回归全部通过。

输出：`data/e04/`（`rule_run.json`、`candidates.jsonl`、`segments.jsonl`、`zone_stats.json`）、`demo/data/rules.json`。
配置：`configs/e04_demo_rules.json`。
证据：`reports/E04执行报告.md`、`reports/E04修正记录.md`、`reports/E04能力缺口清单.md`、`reports/E04实施工作记录.md`、`reports/E04讲解脚本.md`、`reports/e04/`、`data/e04_legacy_v1/`。

**边界：**区域是公开演示区域，不是现场业务区域；没有相机标定，距离单位为像素；规则阈值是演示阈值，未经现场校准；候选统一为“规则条件触发，待人工复核”；没有 GT，不计算检出率、误报率或现场准确率。

## E02 公开基准评价

MOT17 训练集单序列 `MOT17-02-FRCNN`，公开基准开发评估，不是现场 E02。

```bash
# 下载/登记官方单序列（HTTP Range，只取一个序列）
python3 scripts/e02_fetch_mot17_sequence.py --sequence MOT17-02-FRCNN --workers 8
python3 scripts/e02_register_sequence.py

# 冻结配置的 YOLOv8n + ByteTrack 推理
.venv-gpu/bin/python scripts/e02_mot17_infer.py

# TrackEval 准备与评价
python3 scripts/e02_prepare_trackeval.py
.venv-gpu/bin/python scripts/e02_run_trackeval.py

# 典型错误案例与完整核验
.venv-gpu/bin/python scripts/e02_error_cases.py
python3 scripts/e02_verify.py

# 低覆盖诊断与 640→1280 单因素对照核验
.venv-gpu/bin/python scripts/e02_diagnose.py
.venv-gpu/bin/python scripts/e02_diagnose.py \
  --detections runs/e02-mot17-02-frcnn-yolov8n-byte-img1280-001/detections.jsonl \
  --tracks runs/e02-mot17-02-frcnn-yolov8n-byte-img1280-001/predictions/MOT17-02-FRCNN.txt \
  --output reports/e02/diagnosis_img1280
python3 scripts/e02_diagnosis_verify.py
```

实际结果：

- 600 帧、30 FPS、1920×1080；官方 GT 18,581 行人框 / 62 ID；
- 预测 5,618 行 / 80 ID；TrackEval 预处理后 5,263 检测 / 79 ID；
- **IDF1 27.361、IDSW 36、Frag 62**；HOTA 26.667、MOTA 17.001、DetRe 19.368、DetPr 68.378、CLR_Re 22.760、CLR_Pr 80.353；
- 典型错误：GT 33 相邻帧 123→150 ID 切换；GT 3/335 是匹配竞争而非无检测；GT 36/120 真漏检、329—431 轨迹碎片；
- 30 项公开基准核验通过；诊断见 `reports/E02诊断执行报告.md`。
- 诊断单因素对照：检测输入 640→1280 提升覆盖/IDF1/HOTA，但 IDSW/Frag 和资源增加，不直接替换基线。

证据：`data/e02/`、`reports/E02公开基准执行报告.md`、`reports/E02公开基准实施工作记录.md`、`reports/E02公开基准指标.json`、`reports/E02诊断执行报告.md`、`reports/E02诊断实施工作记录.md`、`reports/E02诊断对照指标.json`、`reports/e02/`。
边界：只代表 1 个公开序列/1 个场景/1 个官方变体；不是现场 E02，不是未见测试集；未据结果调参，不用于事件规则准确率。

## E00-B Holmes-VAU

E00-B 使用独立环境 `.venv-holmes`（Python 3.9.23、torch 2.1.2+cu118、transformers 4.37.2、flash-attn 2.3.6），未修改 `.venv`、`.venv-gpu` 或 E00-A/E00-D 结果。模型权重 `models/HolmesVAU-2B/model.safetensors` 为 4.41 GB，SHA256 与官方 LFS 一致；ATS 权重在 `upstream/HolmesVAU/holmesvau/ATS/anomaly_scorer.pth`，SHA256 已记录。

实际结果：

- 官方样例 `examples/robbery.mp4` 成功，原始回答已保存；
- 公开行人视频 `data/raw/people-walking.mp4` 成功，原始回答已保存；
- 官方样例重复运行帧索引与回答相同；
- 峰值 `torch.cuda` allocated 约 7.13 GB、reserved 约 7.63 GB，`nvidia-smi` 整卡约 8.7 GB；
- 官方代码有两个单卡/短视频兼容性缺口（ATS checkpoint 的 CUDA device 2、回退索引 float64），已用最小补丁修复，diff 在 `reports/e00-b/e00b-ats-compatibility.patch`；
- 结论是“有条件采用为 E07 候选”，不是解释质量通过；未接入 E00-D，不宣称实时/多路/现场准确率。

```bash
# 查看报告和工作记录
cat reports/E00-B执行报告.md
cat reports/E00-B实施工作记录.md

# 重新运行官方样例（模型/ATS 哈希不匹配会失败）
PTX="$(realpath .venv-holmes/lib/python3.9/site-packages/triton/third_party/cuda/bin/ptxas)"
ln -sf "$PTX" /tmp/opencode/ptxas
TRITON_PTXAS_PATH=/tmp/opencode/ptxas \
.venv-holmes/bin/python scripts/run_holmesvau.py \
  --video upstream/HolmesVAU/examples/robbery.mp4 \
  --prompt "Could you specify the anomaly events present in the video?" \
  --out runs/e00b-holmesvau-001/official-robbery-003 \
  --label official-robbery-003 --select-frames 12 --use-ats
```

环境锁：`requirements.e00b.lock.txt`；源/许可核查：`reports/e00-b/来源与许可核查.md`；运行证据：`runs/e00b-holmesvau-001/`。

## E01 数据基础

E01 的公开数据准备工作已完成，现场数据尚未到位。目录：`data/e01/`。

- `videos.jsonl`：2 个已使用公开视频（Roboflow 行人样例、HolmesVAU 官方盗窃样例），含 SHA256、FPS、帧数、分辨率、时长、时间基准、用途和 development 集合；
- `splits.json`：开发集 2 个视频；验证集 `missing_data`、测试集 `not_established`，没有用公开样例冒充未见测试集；
- `zones/zones.json`：公开演示区域（3 个多边形）和官方盗窃样例的空区域配置；均明确 `is_field_business_zone=false`；
- `annotations/README.md` + `annotations/examples/`：轨迹、事件、困难负样本、质量四类标注规范与 6 行示例；全部 `synthetic_example`、不是 GT；
- `DATA_GAPS.md`：停留、折返、60 秒人数变化需要的视频和标注；
- `src/st_memory/e01_checks.py`、`scripts/check_e01_data.py`：清单/划分/区域/标注检查器；
- `reports/E01检查结果.json`：实际检查结果。

```bash
# 重新生成视频清单（只读视频）
.venv/bin/python scripts/build_e01_manifest.py

# 检查清单、划分、区域、标注
python3 scripts/check_e01_data.py

# 规则单测
python3 -m unittest discover -s tests -v
```

当前结果：**0 错误、4 警告**（验证集为空、测试集未建立、官方盗窃样例无区域、没有人工 GT）。这些是实际数据缺口，不是通过项。

公开演示区域预览：`reports/e01/public-walk-zones-preview.jpg`，明确写“PUBLIC DEMO ZONES — NOT FIELD BUSINESS ZONES”。

## E03 轨迹存储与证据查询

公开样例 `e00-public-demo-005` 的工程验证已完成。原始 `runs/e00-public-demo-005/` 未修改；`data/e03/track_store.sqlite` 是派生 SQLite，`data/e03/zone_assignments.jsonl` 是区域派生导出。

```bash
# 首次/重复导入（重复导入应显示 inserted=0）
python3 scripts/e03_import.py --run runs/e00-public-demo-005 --db data/e03/track_store.sqlite --batch-size 500

# 完整性
python3 scripts/e03_verify.py --run runs/e00-public-demo-005 --db data/e03/track_store.sqlite

# 查询（按运行+轨迹+时间）
python3 scripts/e03_query.py --run-id e00-public-demo-005 --track-id 7 --start 5.52 --end 5.52 --limit 1

# 区域
python3 scripts/e03_query.py --run-id e00-public-demo-005 --zone-id demo_central_walkway --start 2.0 --end 2.04 --limit 3

# 轨迹状态：最后观察、窗口、离场、缺口
python3 scripts/e03_query.py --run-id e00-public-demo-005 --track-id 7 --summary

# 暂停→恢复与完整导入对比
python3 scripts/e03_recovery_check.py
```

实际结果：

- 9,599 点 / 300 帧 / 82 轨迹；区域派生 6,942 条；
- 重复导入新增 0、跳过 9,599；
- 暂停 1,000 点后恢复，与完整导入的 points/frames/tracks/point_zones 完全一致；
- 5.52 秒 track 7 查询与 E00-D 证据点对齐；
- 22 项单测通过（E03 11 + E01 7 + E00 4）；E00-D 33 项浏览器回归通过。

证据：`reports/E03执行报告.md`、`reports/E03完整性检查.json`、`reports/E03恢复验证.json`、`reports/E03_E00D证据对齐.json`、`reports/E03查询示例.md`、`data/e03/field_mapping.json`。

**边界：这是公开样例上的工程验证，不是现场 E02 或现场性能通过。**

## 现有环境运行

下列命令从本目录执行。GPU 命令需要可访问 `/dev/nvidia*` 的宿主上下文；Codex 的默认沙箱不具备该条件，须使用获批的沙箱外执行。

```bash
# 记录执行环境并实测 CUDA 运算
.venv-gpu/bin/python scripts/doctor.py --context host --output reports/environment-host.json

# 只下载锁定的两个公开资产；已存在则仅检查校验值
python3 scripts/fetch_assets.py

# 独立上游 API 示例：前 60 帧
.venv-gpu/bin/python scripts/upstream_smoke.py

# 前 300 帧：默认自动生成独立运行编号，禁止覆盖已有运行目录
.venv-gpu/bin/python scripts/run_perception.py

# 查询与完整性检查无需 GPU，也不依赖 ML 包
python3 scripts/verify_run.py runs/e00-public-demo-005
python3 scripts/query_tracks.py runs/e00-public-demo-005 --camera-id public_walk_01 --track-id 1 --start 0 --end 2

# 固定版本重复运行的一致性报告
python3 scripts/compare_runs.py runs/e00-public-demo-004 runs/e00-public-demo-005
python3 -m unittest discover -s tests -v
```

配置见 `configs/e00_public_demo.json`。样例为 `public_demo / development`，源视频同组管理；没有伪造现场采集日期，没有建立现场测试集。时间单位为源视频相对秒，位置为图像像素、框底部中心点。暂未定义真实业务区域，`zone_id=null`；质量未评价，不把检测置信度作为遮挡质量。

## 输出与适配边界

每次运行目录包含 `replay.mp4`、`preview.jpg`、`detections.jsonl`、`tracks.jsonl`、`frames.jsonl`、`trace_summaries.jsonl`、`manifest.json`、`config.json` 和 `run.json`。保留原视频定位、局部 ID 与由场站/摄像头/运行/局部 ID 组成的 `trace_id`。

E00/E02/E04 主链路中 YOLO 只使用 `predict`，不调用其 `track`；关联器为 Supervision 集成 ByteTrack，不等同于已经复现 FoundationVision 原版。原始检测单独保留，以便以后固定检测结果做跟踪参数对照。A2 的 BoT-SORT 仅作为参考对照，使用 Ultralytics `model.track`（检测与关联耦合），不是严格 tracker-only 实验；结果和限制见 `reports/A2-MOT17多场景评价报告.md`。

轨迹点逐行写出，逐帧记录含零目标帧。汇总只表示本次观察窗口关闭，不能据最后一次观测推断真实轨迹已结束。E00-A 原运行未实现 SQLite、重复导入或断点恢复；E03 已在公开样例上新增派生 SQLite、幂等导入/恢复/查询，但现场规模、多路和平台集成未验证。E04 已在公开样例上新增区域统计、停留/折返规则、60 秒规则状态和证据链，但现场规则评价与阈值校准未完成。异常退出会记录失败，完整性脚本核对计数、重复、时间范围和文件校验值。未关闭 JSONL 行造成解析失败也应按失败排查。

## 环境复现与限制

`components.lock.json` 固定组件、权重和样例哈希；`requirements.runtime.lock.txt` 保存当前实际使用的主要包版本，`requirements.txt` 是感知核心依赖。尚未验证从空环境全量重建。

当前 `.venv-gpu` 使用 `/home/ubuntu/miniforge3/envs/tep-gpu` 的 `--system-site-packages`，并经 `project_local_dependencies.pth` 读取本项目 `.venv` 中的部分包；新增覆盖包只在 `.venv-gpu` 内。**保留这两个项目环境**，未修改已有 conda 环境。独立迁移应使用 runtime lock 建立全新环境，再重跑 CUDA、`pip check` 和样例核对。

Ultralytics 8.3.40 的旧加载器与当前 PyTorch 默认权重加载行为不同。两个运行入口设置 `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1`，仅用于本地已核对官方来源与哈希的 YOLO checkpoint，不改上游源代码。正式适配器会先核对视频、权重与依赖版本；不要以未知权重替换当前资产。

原始媒体、权重、缓存及运行目录默认不提交公共仓库。公开样例的再分发许可未核定，当前仅用于本地复现。现场媒体来源、保留周期、访问和回传范围仍待确认。

E00-D 中文可视化演示界面已完成第一版并通过浏览器验收；E00-B Holmes-VAU 已完成本机可运行性验证并形成有条件采用结论；E01 已完成公开数据清单、划分、区域、标注格式与检查器；E02 已完成 MOT17 单序列公开基准第一轮定量评价；E03 已完成公开样例存储/查询/恢复工程验证；E04 已完成公开样例区域统计与规则功能验证；A1 已完成 Frigate 部署与浏览器回放验收；A2 已完成 MOT17 七场景评价、严格 tracker-only 对照、Loitering 轨迹级评价与泛化分析、GMDCSA-24 摔倒事件评价，以及区域停留/摔倒/人数变化三类平台单例接入（复核分别为 pending/uncertain/pending）。近期主线为：用原始连续长视频和具体 ROI/人工 GT 做批量对照，补更多摔倒复核和“聚集”定义；现场视频/区域/人工 GT 到位后进入现场 E02/E04 与 E05；E07 在固定证据与人工评审条件具备后开展。当前 13.64 秒公开片段不足以验证长时间停留或现场误报；人数变化单例使用 168 秒带叠加 PEDS1 demo，仅作流程检查；视频理解仍未接入演示。A2 实验环境版本另见 `requirements.a2.lock.txt`（含 BoT-SORT 所需的 `lapx==0.13.0`）。
