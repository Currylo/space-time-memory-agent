# A1：Frigate 单路公开样例部署包

状态（2026-11-04 收尾后）：**Docker 已安装，Frigate 0.18.2 已运行；长片输入无 `-stream_loop`；新 H.264 录像 VOD/浏览器 Review 回放通过；10 秒门控实测 10.102 秒（累计语义）；持久 SQLite ledger/跨次幂等/复核历史完成；HTTP 手动事件、clip 关联、Explore 播放通过。另新增 `a2_fall_demo` 摄像头（GMDCSA-24 S1_Fall_01）用于摔倒单例，复核结论 `uncertain`。旧 mp4v 历史段仍 400。逐帧轨迹导出仍缺失；手动 API 事件不生成 Review。详见 [A1 验收报告](../../reports/A1-Frigate部署与回放验收报告.md)、[接入层记录](../../reports/A2外接适配器接入层实施记录.md) 与 [门控/外接报告](../../reports/A2-门控核验与外接候选闭环.md)。**

**重启注意：** compose 的 restart 已从 `"no"` 改为 `unless-stopped`；配置变更后使用 `sudo docker compose -f deploy/frigate/compose.yaml up -d --force-recreate frigate`，不要用 `/api/restart` 代替 compose 启停。

该包优先补老师方案 A 的 A1，以及后续平台侧 A2。固定镜像 tag 为 `ghcr.io/blakeblackshear/frigate:0.18.2`，拉取后还需记录实际 RepoDigest。原方案完整六服务栈与 Frigate 是两种实现，部署报告必须明确是“替代平台验证”。

## 1. 准备（普通用户，无 GPU/网络要求）

从 `st_memory_experiments/` 执行：

```bash
python3 scripts/a1_prepare_frigate.py
```

默认读取已下载的 `data/a2/mot17/videos/MOT17-04-FRCNN.mp4`，复制到独立 `runtime/input/`，核对 SHA256，并生成 `runtime/config/config.yml` 和 `runtime/source_manifest.json`。配置内容使用合法 YAML 的 JSON 形式。存在旧配置或输入时拒绝覆盖；修改实例前须另行保存版本。

本次工作区已经完成初始准备；`runtime/source_manifest.json` 中的 `prepared_not_deployed` 是准备时点记录，不能替代当前 `/api/config` 与运行证据。17:41 重启后实际 API 和 FFmpeg 命令行均已切换为 H.264 长片；当前磁盘配置、API 配置和进程一致。

**2026-11-04 回放排障更新：**默认 MOT17 视频原来为 `mp4v`，仅能解码不足以证明适用于平台 VOD；准备脚本现会检查 FourCC 并拒绝不兼容输入。现有配置已在磁盘上改为独立 H.264 长片，但是否生效必须读取 `/api/config`，不能只检查磁盘文件。旧 mp4v 录像仍保留，新 H.264 录像已实测能通过 HLS 初始化段、媒体分片与解码；旧时段/混合时段不能用作新配置的回放验收。详见 `reports/A1-Frigate回放故障定位.md`。

默认单路、CPU TFLite 检测、3 分析 FPS、640×360、只检测 person、演示观察区停留 10 秒后才进入 required zone 告警链路。参数是 A1 功能起点，尚未实测。CPU 路线用于先打通平台，与现有 YOLOv8n 对照不是同一检测器，也不代表 GPU 部署或性能通过。

## 2. 管理员安装容器运行时（宿主终端）

本机 Ubuntu 24.04 的 apt 索引提供 `docker.io` 与 `docker-compose-v2`。由有 sudo 权限的人在本机终端输入密码，**无需向模型提供密码**：

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2
sudo systemctl enable --now docker
sudo docker version
sudo docker compose version
```

如果系统已经有容器运行时，先核对现有安装，不重复安装或替换。Docker 当前已经安装，本节安装命令仅保留为部署参考；目前的权限限制是模型会话不能非交互 sudo 重启容器。Codex 的宿主检查与默认沙箱状态需分开记录。

## 3. 校验与启动（项目目录）

```bash
# 先校验 Compose；这一步成功不等于 Frigate 配置有效
sudo docker compose -f deploy/frigate/compose.yaml config --quiet

# 拉取并记录实际镜像摘要
sudo docker compose -f deploy/frigate/compose.yaml pull
sudo docker image inspect ghcr.io/blakeblackshear/frigate:0.18.2 --format '{{json .RepoDigests}}'

# 启动后核对日志中的实际 Frigate 配置、模型和 FFmpeg 状态
sudo docker compose -f deploy/frigate/compose.yaml up -d
sudo docker compose -f deploy/frigate/compose.yaml ps
sudo docker compose -f deploy/frigate/compose.yaml logs --tail 100 frigate
```

本机 UI/API：`http://127.0.0.1:15000/`（内部无认证接口，仅绑定回环地址）；认证入口：`http://127.0.0.1:8971/`。平台首次生成的管理凭据由操作人在本地查看。不要将包含凭据的完整启动日志写入公共报告。

这两个地址均是**拟部署地址**，启动前不能写为可访问服务。既有 E00-D 的 8765 服务不受此包影响。

## 4. 采集真实证据

```bash
python3 scripts/a1_collect_frigate.py
# 进一步区分旧录像、新录像、混合窗口；包含 init+媒体片段解码
python3 scripts/a1_diagnose_vod.py --output reports/a1/frigate/my-vod-check
```

采集 `/api/version`、`stats`、实际 config、person events、review 和带框最新图，保存在每次独立的 `reports/a1/frigate/` 子目录。零事件、缺失或连接失败如实保留。脚本只采集证据，不自动宣告 A1/A2 通过。实际录制片段需在 UI 点开播放并截图，不能只凭 `has_clip` 标志宣告回放成功。

验收依次确认：平台版本/镜像摘要 → 实际视频解码/检测 → 目标框与对象记录 → 区域停留条件 → 告警/Review → 录像/证据可回放。逐帧轨迹导出能力单独验证，缺失时保留独立离线轨迹流水线。

停止（保留录像和数据库）：

```bash
sudo docker compose -f deploy/frigate/compose.yaml stop
```

## 5. 平台 A2 对照约束

- 当前文件循环回放只作 A1 烟测；每轮不是独立样本，跨循环的轨迹/停留不作行为结果。
- A2 另建一次性回放协议，实测源视频时间、回放轮次、平台时钟和证据偏移；未对齐时不算事件延迟或 tIoU。
- 相同视频/ROI/观察窗口/定义/匹配口径是基础。平台 stock detector 与自研 YOLO 不同的对照标为“整条流水线对照”，不能归因为规则或关联器增益。
- Frigate `loitering_time` 表示区域停留门控，不等于 RF/LR 轨迹级徘徊分类或有效折返；标签不支持的指标填 N/A。
- 摔倒沿用 GMDCSA-24 外部模块，但需真实接入平台事件与回放再验证。内置、外部接入、尚缺失分栏记录。
- 人群数量变化先实测平台原生能力，缺失时才补最小计数规则/适配；不因文档列出功能就假定已支持。
- Frigate 当前版本可能有其他增强能力；语义解释不在本次 A1 单路功能范围内。

## 固定版来源

- [Frigate v0.18.2](https://github.com/blakeblackshear/frigate/tree/v0.18.2)
- [输入参数](https://github.com/blakeblackshear/frigate/blob/v0.18.2/frigate/config/camera/ffmpeg.py)
- [区域/停留配置](https://github.com/blakeblackshear/frigate/blob/v0.18.2/frigate/config/camera/zone.py)
- [录像配置](https://github.com/blakeblackshear/frigate/blob/v0.18.2/frigate/config/camera/record.py)
- [Review 告警门控](https://github.com/blakeblackshear/frigate/blob/v0.18.2/frigate/config/camera/review.py)
- [CPU 插件](https://github.com/blakeblackshear/frigate/blob/v0.18.2/frigate/detectors/plugins/cpu_tfl.py)
