> 源码导入说明：本文件保留原本地实验记录；媒体、演示 JSON、报告和运行结果未随源码上传。当前启动与验证边界见 [源码导入记录](../docs/SOURCE_IMPORT.md)。

# E00-D / E04 公开样例可视化演示

## 最新主展示：同一原序列视频联合分析

**http://127.0.0.1:8765/showcase.html**（同页副本 `unified.html`）。一段连续 MOT20-05 原序列视频 132.6 秒、25 FPS；三类规则共享 5 FPS 观测、轨迹编号、观察区与视频时钟。一个播放器可切换原画/分析叠加，右侧停留/姿态摔倒/人数变化随相同时刻更新，曲线和候选均可定位同一视频。该官方视频版带序列名及帧号水印，无 GT 框、无重复拼接。

**展示主线：发现线索 → 查看依据 → 回看录像 → 人工确认。** 首页增加四步流程与分类数量，点击线索后定位共同视频并展开依据、关联录像和复核表单；时钟残差、平台记录、规则窗口、方案 A 进展默认折叠。仍明确标注“离线分析回放”，复核结论不预填。

本次真实结果：停留 8 条、摔倒 0 条、人数变化 10 条；姿态不足不显示为已确认正常，不补入其他视频的正例。**当前已完成同一源视频到 Frigate 的受控回放闭环：** 新摄像头 `a2_unified_mot20_05`、回放身份 `unified-mot20-05-20261104T1507Z-r1`、18 条候选事件（8 停留 / 0 摔倒 / 10 人数变化）、18 个平台录像全部下载并核对完整证据窗口；复核默认 pending，真实复核待人。旧平台单例和 `review.html` 继续保留。

同源平台闭环要点：

- 时钟：开始/中段/后段 14 个录像段、3 个指定对齐点、逐秒代表片段对齐；最终漂移约 0 ppm、RMSE 0.000 秒。首两条候选因首个录像段尚未落盘而使用 `latest.jpg` 模型，映射残差约 +0.31/+0.36 秒；平台录像仍完整覆盖证据窗口（起点余量 1.31/1.22 秒）。
- 录像：停留事件覆盖 10.00 秒观测窗口；人数变化事件覆盖精确 60.00 秒窗口（例如 33.40—93.40 秒，条件时刻 93.60 秒，持续 2 秒最小计数 7）。
- 幂等：同一 18 条候选再次提交全部为 ledger `duplicate`，平台手动事件 18→18、ledger attempts 36→36；并发/崩溃/超时恢复使用独立 mock 与临时 ledger 验证。
- 浏览器：本次页面独立 51 项 Chrome 验收通过（桌面与 390×844、缺失媒体、平台离线、对齐失败、测试 ledger 复核流程），录屏 1 分 23 秒；真实 ledger 未被测试复核污染。R1—R3 修正与服务端负例见 `reports/同源平台闭环修正报告-R1R3.md`；F1/F2 收尾见 `reports/同源平台闭环复审修正-F1F2.md`。
- 状态：受控回放结束后摄像头保持配置但 `enabled: false`，不会自动循环重开；18 个手动事件和录像保留。平台原生对象事件为独立 `has_clip=false` 历史，不计入候选。

详见 [同源平台闭环执行报告](../reports/同源平台闭环执行报告.md) 与时间戳证据目录 `reports/unified-platform-20261104T1507Z/`。

### SSH 远程访问

页面和 Frigate 都只监听回环地址；远程使用时在本机终端建立转发，不要把未鉴权平台直接开放到公网：

```bash
ssh -L 8765:127.0.0.1:8765 -L 15000:127.0.0.1:15000 <user>@<host>
```

然后在本机浏览器打开 `http://127.0.0.1:8765/showcase.html`；平台事件链接使用 `http://127.0.0.1:15000/explore?event_id=...`。8765 页面中的录像和证据图使用相对路径，不依赖服务器绝对磁盘路径。

构建使用宿主 GPU 上的现有 `.venv-gpu`；模型、源视频、参数和结果独立保存：

```bash
.venv-gpu/bin/python scripts/build_unified_demo.py --run-id unified-mot20-05-detect-pose-v2-002
```

已有 run-id 不可覆盖。来源登记在 `data/a2/unified/source.json`；构建输出 `demo/data/unified.json`、`demo/media/unified/`。页面本身只播放已完成结果，不调用 GPU。新增验证与边界见 `reports/方案A对照审查与同源视频优化方案.md`。

## 历史分案例展示

下述旧版总览迁至 **http://127.0.0.1:8765/showcase_cases.html**；原页面与复核接口保留。旧页面的 Chrome 验收不代表新同源页面已经完成浏览器验收。

历史分案例入口：**http://127.0.0.1:8765/showcase_cases.html**。以下旧版说明中的 `showcase.html` 指本次备份前的分案例版本。

`showcase.html` 同屏展示已有分析回放、区域观测数、实际停留候选、触发依据和证据定位；下方展示公开数据评价，并链接 Frigate 已验收录像。Frigate 的 `15000/review` 本身是原生录像回放页，不包含自研轨迹与规则界面。

此入口整合展示已有结果；单候选 HTTP 写入、录像关联和平台播放已有案例，页面新增操作录屏、关联录像副本、具体 ROI 三例及平台事件直达链接。上方行人样例为 12 秒分析；平台回放为另一段 280 秒拼接公开视频，不能混为同一段分析。

持久 SQLite ledger、正常写入路径幂等去重和独立人工复核提交已补；旧 confirmed 字段仍标为生成脚本写入，不能作为真人复核证据。页面新增“区域停留 / 摔倒提示 / 人数变化”三类能力入口：停留 pending、摔倒 uncertain、人数变化 pending；人数变化使用 168 秒固定机位 PEDS1 demo（带叠加，仅流程检查）。演示页已通过 Chrome 13 项交互检查；接入层与三类单例见 `reports/A2外接适配器接入层实施记录.md`。

新增文件：`demo/showcase.html`、`demo/review.html`、`demo/assets/showcase.css`、`demo/assets/review.css`、`demo/assets/review.js`、`demo/data/showcase.json`、`demo/data/review_candidates.json`。统一复核页通过本地 `serve.py` 的 `/api/review/*` 接口写入持久 ledger；评价小批次在 `data/a2/eval_batch/`。复核结论不设配额。评价快照从现有机器可读结果构建：

```bash
python3 scripts/build_showcase.py
```

构建读取现有 JSON，复制已有案例录屏/ROI 图并保存哈希；关联 MP4 仅重封装为普通 faststart MP4，不重编码，源文件不改。可用 `--ffmpeg /path/to/ffmpeg` 指定工具。

已有服务无需重启即可访问新增静态页面。新入口的服务资源与数据检查记录见 `reports/统一演示入口实施记录.md`；浏览器人工验收状态单独记录，不能沿用旧页面的验收结果。

面向非技术观看者的本地中文演示页面。页面只读取已完成的 E00-A 运行结果和 E04 规则派生结果，不重新运行检测/跟踪，不调用视频理解模型，不使用 GPU，不联网。

演示包含两个独立入口：

- **轨迹演示（E00-D）**：`index.html`，原视频/分析回放同步、轨迹路径、目标证据回看；
- **区域统计与规则（E04）**：`rules.html`，区域轮廓、逐帧观测数、停留/折返规则候选、触发依据和视频证据。

![演示主视图](../reports/e00-d/02-对照播放.png)

## 快速启动

```bash
cd /path/to/space-time-memory-agent
python3 demo/serve.py
```

访问：

- 轨迹演示：**http://127.0.0.1:8765/**
- 区域统计与规则：**http://127.0.0.1:8765/rules.html**

停止：在服务终端按 **Ctrl+C**。
换端口：`python3 demo/serve.py --port 8766`。

服务只监听 `127.0.0.1`，不发布公网。页面没有外部 CDN、字体或推理请求；会议前启动即可，不依赖现场模型下载或重新推理。

## 页面内容

### 轨迹演示（E00-D）

- **首页**：项目用途、唯一公开样例、轨迹演示与区域规则两个入口、能力状态。
- **视频对照**：原视频与分析回放同步播放、暂停、前后 1 秒、进度跳转、全屏。
- **时间边界**：只展示 0—12.00 秒；原视频 13.64 秒，尾部 1.64 秒用灰色“未分析”块标出。
- **轨迹回溯**：82 个轨迹编号，选择后显示首次出现、最后观察、观察跨度、轨迹点；原视频叠加实际路径，回放高亮目标框。
- **证据回看**：点击“首次出现”“最后观察到”或“播放该目标片段”，两路视频同时定位。
- **真实摘要**：300 帧、9,599 个轨迹点、82 个累计轨迹编号；明确 82 不是独立人数。
- **能力状态**：已实现/待验证/未启用；明确“本页不含规则”“视频理解未接入”。
- **来源与版本**：运行编号、组件版本、结果目录、文件哈希，默认折叠。
- **缺失提示**：文件缺失时显示实际请求地址和期望路径，禁用播放；不使用占位视频。

### 区域统计与规则（E04）

- **区域轮廓**：在原视频和分析回放上绘制三个公开演示区域，标签实时显示本帧算法观测数。
- **逐帧统计**：三张区域卡片和折线图随播放时刻更新；明确“算法观测数、不是独立人数”。
- **规则开关**：长时间停留、反复折返、60 秒人数变化可独立显示/隐藏；规则引擎启停另由配置和测试验证。
- **规则候选**：本次 v2 实际 6 条停留候选，统一标为“规则条件触发，待人工复核”；折返规则如实显示 0 条；60 秒规则显示“观察时长不足”。数量不是规则目标。
- **触发依据**：点击候选显示规则版本、配置哈希、阈值、触发特征、证据帧时刻、模拟触发视频时刻、实际写出墙钟时间、证据帧和去重键。
- **视频证据**：点击候选或时间轴标记可回到触发时刻；可播放触发前后 4 秒原视频证据。

## 数据与文件

采用运行：`runs/e00-public-demo-005/`。

| 文件 | 作用 |
| --- | --- |
| `demo/index.html` | 页面结构：首页 + 演示主视图 |
| `demo/assets/app.css` | 投屏布局、视频、轨迹面板、全屏、缺失提示 |
| `demo/assets/app.js` | 双视频同步、Canvas 路径、时间定位、状态渲染 |
| `demo/build_demo.py` | 只读运行 JSONL 生成轨迹演示数据 |
| `demo/data/demo.json` | 82 条轨迹逐点数据与真实统计 |
| `demo/rules.html` | E04 区域统计与规则演示页 |
| `demo/assets/rules.css` | E04 规则演示布局与组件样式 |
| `demo/assets/rules.js` | 区域叠加、统计曲线、规则候选、证据定位 |
| `demo/data/rules.json` | E04 紧凑演示数据（区域统计、规则状态、候选） |
| `scripts/e04_build.py` | 只读 E00-A/E03 结果生成 `data/e04/` 与 `demo/data/rules.json` |
| `scripts/e04_verify.py` | E04 输出一致性、证据和因果性核验 |
| `demo/serve.py` | 本地静态服务、Range/206、`/healthz` |
| `demo/media/analysis_replay.mp4` | H.264 1440×810 派生回放 |
| `../data/raw/people-walking.mp4` | 原始视频，未修改 |
| `../runs/e00-public-demo-005/replay.mp4` | 原始分析回放，未修改 |

`analysis_replay.mp4` 是从原回放转码的独立派生文件，仅改变编码和分辨率以便浏览器播放；原视频、原回放、权重和运行 JSONL 均未改动。派生文件 SHA256：`085038544d738521fd87168b6a7d6c2853c053aad8d931629dcfb1ef18f3c02d`。

### 重新生成数据

```bash
# 轨迹演示数据
python3 demo/build_demo.py

# E04 规则输出与规则演示数据（标准库）
python3 scripts/e04_build.py

# 带 Supervision 区域交叉核对（使用 .venv-gpu）
.venv-gpu/bin/python scripts/e04_build.py --crosscheck-supervision
```

`build_demo.py` 会核对 300 帧、9,599 点、82 条轨迹摘要与 `run.json` 一致，然后重写 `demo/data/demo.json`。`e04_build.py` 只读取 E00-A 轨迹、E01 区域和 E03 区域派生结果，不重跑 YOLO/ByteTrack。

### 重新转码派生回放

需要本机有 ffmpeg：

```bash
ffmpeg -y -i runs/e00-public-demo-005/replay.mp4 \
  -an -vf "scale=1440:810:flags=lanczos" \
  -c:v libx264 -profile:v high -pix_fmt yuv420p \
  -crf 21 -preset medium -g 25 -keyint_min 25 -sc_threshold 0 \
  -movflags +faststart demo/media/analysis_replay.mp4
```

## 验收

数据完整性单测（不需要浏览器、不需要 GPU）：

```bash
python3 -m unittest demo.tests.test_demo_data -v
python3 -m unittest tests.test_e04_rules -v
```

浏览器验收和截图（需要独立安装 Playwright，不写入项目依赖锁）：

```bash
python3 demo/tests/verify_demo.py --screenshots reports/e00-d --check-missing

python3 demo/tests/verify_rules.py --screenshots reports/e04 --check-missing
```

`--check-missing` 会临时改名对应数据文件，验证真实缺失提示后自动恢复。运行验收时不要同时向其他人演示。

实际结果：

- 轨迹演示 33 项浏览器检查全部通过；
- 区域规则演示 34 项浏览器检查全部通过；
- E04 输出核验 35 项通过；
- E04 单元测试 29 项通过，合计项目单测 51 项通过；
- Chrome 156 + Playwright 1.55，控制台无错误。

证据见 `reports/e00-d/`、`reports/e04/`、`reports/E04执行报告.md`。

重新录屏（可选）：

```bash
python3 demo/tests/record_demo.py
```

录屏输出 `reports/e00-d/E00-D演示录屏.webm`；MP4 版本已在 `reports/e00-d/E00-D演示录屏.mp4`。

## 已知限制

- 82 是累计局部轨迹编号，不是独立人数，也不代表身份。
- “最后观察到”不代表目标离场。
- 区域是公开演示区域，不是现场业务区域；没有相机标定，不报告米制距离。
- 只有一段公开视频，没有现场视频、人工 GT、事件正例或困难负样本。
- 规则阈值均为演示阈值，未经现场校准；候选必须人工复核，不等于危险事件。
- 折返规则在当前 12 秒片段上为 0 条候选；60 秒人数变化规则显示观察时长不足。
- 视频理解未接入；页面不会生成语义解释或告警。
- 单路离线结果，不代表实时、多路或平台集成能力。
- 原视频 13.64 秒，仅分析前 12.00 秒。
- 录屏无旁白，需配合 `reports/E00-D讲解脚本.md` 或现场讲解。
- 公开样例再分发许可未核定；当前仅本地展示。

## 常见问题

**页面提示“演示数据未加载”**
确认从 `st_memory_experiments` 目录执行 `python3 demo/serve.py`，并访问 `http://127.0.0.1:8765/` 或 `http://127.0.0.1:8765/rules.html`，不要直接双击 HTML 文件。

**页面提示“演示文件缺失”**
轨迹页按提示检查 `demo/data/demo.json`、`demo/media/analysis_replay.mp4`、`data/raw/people-walking.mp4` 和 `runs/e00-public-demo-005/preview.jpg`；规则页还需检查 `demo/data/rules.json` 和 `data/e04/`。恢复文件后刷新页面。

**视频能播放但拖动不流畅**
确认使用 `demo/serve.py` 而不是其他不支持 Range 的静态服务器；浏览器需要 206 响应来 seek。

**端口被占用**
`python3 demo/serve.py --port 8766`，然后访问 `http://127.0.0.1:8766/`。
