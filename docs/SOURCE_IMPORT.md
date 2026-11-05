# 源码导入与验证记录

> **2026-11-05 接续交接更新：**源码已上传；当前主展示视频、106段平台证据和观测结果通过Release提供。请先看 [接续开发与恢复步骤](HANDOFF.md)。下文保留的“源码尚未导入”“媒体未上传”等描述属于此前交付时点。

更新：2026-11-05。本次在独立克隆目录，基于远程 `main` 的 `43ee6a51a81f8d95ac7baa095d3e5aa0b2dd1186` 导入现有工程源码，沿用原有历史；未强推、未替换远程历史。

远程原有 `README.md`、`docs/COLLABORATION.md`、`docs/DATA_AND_ENVIRONMENT.md`、`docs/ROADMAP.md`、`.gitattributes` 和 PR 模板逐字保留。`.gitignore` 原规则全部保留，仅追加缓存、模型及其他二进制产物规则。Issues #1—#7 不修改、不关闭。旧文档里的“源码尚未导入”描述的是导入前状态，本记录说明此次源码交付的实际边界。

## 导入范围

- `src/st_memory/`：轨迹存储、规则、平台映射、复核校验、覆盖分析等自有模块。
- `scripts/`：数据准备、分析、评价、演示构建、平台接入和验收脚本。
- `tests/`、`demo/tests/`：现有单元测试和需另备资产的集成/浏览器检查。
- `demo/`：HTML、CSS、JavaScript、SVG 和本地 HTTP 服务；不含生成数据与媒体。
- `configs/`、`manifests/`、`deploy/frigate/`：配置、来源登记、Compose 模板和部署脚本；不含实际运行目录。
- `components.lock.json`、依赖清单和 `patches/holmesvau-ats-compatibility.patch`：组件来源、版本、哈希和必要兼容补丁。
- [LOCAL_EXPERIMENTS.md](LOCAL_EXPERIMENTS.md)：原工程 README 的历史实验记录，独立归档，不覆盖远程项目首页。

未纳入：原始及派生视频、证据图片、模型、虚拟环境、缓存、`data/`、`runs/`、`demo/data/`、`demo/media/`、`upstream/`、运行数据库/日志、Frigate runtime、原始报告与真实复核记录。所有导入文件均为可审查文本；SVG 为页面图标。除去远程原文件，新增资产中只有 `tests/fixtures/clock_alignment.json` 是小型数值夹具，已移除录像路径、机器路径和无关运行字段，不含视频或数据库。

## 路径与依赖整理

1. `manifests/public_demo.json` 的旧挂载绝对路径改为 `data/raw/people-walking.mp4`。主要配置继续使用仓库相对路径；检测设备默认仍为 `0`，CPU 重跑需在复制的配置中显式选择 `cpu`。
2. 运行脚本不再从原机器 `/tmp/opencode` 注入 Python 包，也不再默认使用该目录下的 ffmpeg。ffmpeg 从 `PATH` 或现有 `--ffmpeg` 参数指定；HolmesVAU 的 ptxas 包装使用独立临时目录。浏览器检查仍需安装 Playwright/浏览器，部分历史脚本预期系统 Chrome 位于 `/usr/bin/google-chrome`。
3. `requirements.e02.lock.txt` 中 Python 版本和 TrackEval commit 改成注释，它们不是可通过 pip 安装的包。
4. 保留原实验环境快照，并明确分组。原快照同时记录了 `opencv-python` 与 `opencv-python-headless`；两者共享 `cv2`，新环境选择其中一种，不直接把所有快照合并安装。旧 CUDA、HolmesVAU 与感知环境不能混装。
5. 时间对齐回归测试使用专用数值夹具，不再读取未上传的运行报告。依赖实际 E00/E03/E04 数据的测试在资产缺失时明确跳过，资产齐全时仍执行原断言。

| 使用范围 | 清单 / 条件 | 本次验证 |
| --- | --- | --- |
| 页面 HTTP 服务 | Python 标准库；实测 Python 3.12.3 | 启动和缺失资产响应已检查 |
| 无媒体的源码测试 | `requirements.test.txt`：NumPy、SciPy、scikit-learn、OpenCV | 独立 Python 3.12 环境通过 |
| YOLO / ByteTrack 感知 | 原 `requirements.txt`；PyTorch/CUDA 按目标机器独立准备 | 未重新安装或运行 GPU 推理 |
| A2 分析、报表、WebSocket 辅助脚本 | `requirements.analysis.txt`，包括 pandas、matplotlib、Pillow、markdown-it-py、websocket-client 等 | 补齐直接导入依赖清单；未逐项运行 |
| 浏览器验收 | `requirements.browser.txt`，安装 Chromium 或使用脚本所需本机 Chrome | 本次未进行交互验收 |
| TrackEval | 在 `upstream/TrackEval` 检出配置所记 commit；依赖见 `requirements.e02.lock.txt` | 未下载第三方源码与评测集 |
| HolmesVAU | 单独 Python 3.9 / CUDA 11.8 环境；`requirements.e00b.lock.txt`、组件锁和兼容补丁 | 历史快照，不宣称新机已复现 |

`requirements.runtime.lock.txt`、`requirements.a2.lock.txt` 和 `requirements.e00b.lock.txt` 是历史环境记录，并非跨机器、跨平台的完整重建保证；依赖清单语法已解析检查，测试环境执行 `pip check` 未发现依赖冲突。没有对全部可选 GPU、数据与平台组件进行安装验证。

## 新克隆的最小验证

从仓库根目录运行，建议 Python 3.12：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.test.txt
.venv/bin/python -m pip check
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s demo/tests -p 'test_*.py' -v
```

本次隔离验证未使用原工程虚拟环境或运行数据：

- `tests/` 发现 127 项：106 项通过，21 项因未包含 E03/E04 运行资产跳过，0 失败。
- `demo/tests/test_demo_data.py` 的 7 项因未包含生成的演示数据跳过。
- 125 个 Python 文件可解析，11 个 JSON 可解析，Shell 脚本通过 `bash -n`。
- HTTP 检查：`/showcase.html`、页面 JS 为 200；缺少资产时 `/healthz` 为 503 且 `ok=false`；缺失 JSON、视频和平台数据接口为 404。检查未生成运行数据库。

以上是源码与缺失资产处理的验证，不代表 E03/E04 集成数据、新版页面交互、GPU 推理、平台录像或第二台电脑独立复现已验收。恢复资产后应重新运行被跳过的集成测试，并完成 Issues #1、#2 的相应验收。

## 启动与另行交接

只查看页面结构和缺失提示，无需模型或第三方包：

```bash
python3 demo/serve.py --port 8765
```

访问 `http://127.0.0.1:8765/showcase.html`。完整展示至少还需以下另行审查和交接的资产，不能把缺失资产状态当作可播放交付：

- 当前增强展示：`demo/data/unified_coverage1280.json`，以及该 JSON 引用的浏览器兼容原视频、分析叠加视频和其他资源。
- 基线展示：`demo/data/unified.json`、`demo/data/unified_platform.json`；增强平台数据为 `demo/data/unified_platform_coverage.json`。按数据内来源/路径恢复匹配录像，不复用不对应的结果。
- 旧 E00/E04 入口：`demo/data/demo.json`、`demo/data/rules.json`、`data/raw/people-walking.mp4`、对应 `runs/e00-public-demo-005/` 与 `data/e01/`、`data/e03/` 产物。
- 平台接入：另行准备 Frigate runtime 配置、输入与录像；真实 ledger 不放入公开仓库。复核服务在 ledger 缺失时明确不可提交，不自动迁移真实人工复核结论。测试可使用 `--review-ledger` 和 `--review-log` 指向各自独立路径。

`components.lock.json` 保留资产来源与 SHA256；`scripts/fetch_assets.py` 会遍历其中全部资产，包括大模型，并非只下载轻量演示。未执行该下载脚本。确认许可、用途和下载范围后再获取。MOT20、平台映射及完整展示资产的交接清单仍需完善，不以其他视频或伪造数据填补。

第三方源码不随本次导入分发。HolmesVAU 的固定 commit、权重 revision/hash 见组件锁；兼容补丁见 `patches/`，保留原项目来源与许可说明。本次不新增整个项目的开源许可证。
