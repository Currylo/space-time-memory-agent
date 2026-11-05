(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const dom = {
    missingBanner: $("missingBanner"),
    missingTitle: $("missingTitle"),
    missingList: $("missingList"),
    retryAssets: $("retryAssets"),
    playerCard: $("playerCard"),
    syncBadge: $("syncBadge"),
    syncText: $("syncText"),
    originalFrame: $("originalFrame"),
    originalVideo: $("originalVideo"),
    originalOverlay: $("originalOverlay"),
    originalError: $("originalError"),
    replayFrame: $("replayFrame"),
    replayVideo: $("replayVideo"),
    replayOverlay: $("replayOverlay"),
    replayError: $("replayError"),
    playPause: $("playPause"),
    playLabel: $("playLabel"),
    playIcon: document.querySelector("#playPause .play-icon"),
    pauseIcon: document.querySelector("#playPause .pause-icon"),
    back1: $("back1"),
    forward1: $("forward1"),
    replayBtn: $("replayBtn"),
    fullscreenBtn: $("fullscreenBtn"),
    fullscreenLabel: $("fullscreenLabel"),
    fullscreenIcon: document.querySelector("#fullscreenBtn .fullscreen-icon"),
    exitFullscreenIcon: document.querySelector("#fullscreenBtn .exit-fullscreen-icon"),
    currentTime: $("currentTime"),
    durationTime: $("durationTime"),
    timeline: $("timeline"),
    timelineGrid: $("timelineGrid"),
    timelineTail: $("timelineTail"),
    tailNote: $("tailNote"),
    candidateMarkers: $("candidateMarkers"),
    candidateCount: $("candidateCount"),
    ruleList: $("ruleList"),
    candidateList: $("candidateList"),
    clearCandidate: $("clearCandidate"),
    zoneStatCards: $("zoneStatCards"),
    chartLegend: $("chartLegend"),
    zoneChart: $("zoneChart"),
    candidateDetailBody: $("candidateDetailBody"),
    provenanceNote: $("provenanceNote"),
    demoRunId: $("demoRunId"),
    toast: $("toast"),
    fatalError: $("fatalError"),
    fatalTitle: $("fatalTitle"),
    fatalMessage: $("fatalMessage"),
    fatalDetail: $("fatalDetail"),
    fatalRetry: $("fatalRetry"),
  };

  const ZONE_COLORS = {
    demo_central_walkway: "#0f766e",
    demo_left_walkway: "#b45309",
    demo_right_walkway: "#2563eb",
  };
  const FALLBACK_COLORS = ["#0f766e", "#b45309", "#2563eb", "#7c3aed"];
  const FONT_STACK = '"Noto Sans SC", "Source Han Sans SC", "Microsoft YaHei", sans-serif';

  const state = {
    data: null,
    tracksData: null,
    trackById: new Map(),
    zones: [],
    zoneById: new Map(),
    zoneStats: [],
    zoneTimes: [],
    rules: [],
    ruleById: new Map(),
    candidates: [],
    candidateById: new Map(),
    enabled: {},
    selected: null,
    time: 0,
    analysisEnd: 12,
    originalDuration: 13.64,
    playing: false,
    clipEnd: null,
    assetsOk: false,
    missing: new Map(),
    dirty: true,
    scrubbing: false,
    syncOk: true,
    toastTimer: null,
    chartGeometry: null,
    lastStatsIndex: -1,
  };

  const fmtInt = new Intl.NumberFormat("zh-CN");
  let toastHideTimer = null;

  boot();

  async function boot() {
    bindEvents();
    setPlaybackEnabled(false);
    try {
      const [rulesResponse, tracksResponse] = await Promise.all([
        fetch("/data/rules.json", { cache: "no-store" }),
        fetch("/data/demo.json", { cache: "no-store" }),
      ]);
      if (!rulesResponse.ok) {
        throw new Error(`读取规则演示数据失败：HTTP ${rulesResponse.status}`);
      }
      if (!tracksResponse.ok) {
        throw new Error(`读取轨迹演示数据失败：HTTP ${tracksResponse.status}`);
      }
      state.data = await rulesResponse.json();
      state.tracksData = await tracksResponse.json();
    } catch (error) {
      showFatal(error);
      return;
    }

    try {
      applyData();
      setupVideos();
      seek(0, { fromUser: false, silent: true });
      renderAtTime(0);
      checkAssets();
    } catch (error) {
      showFatal(error);
    }
  }

  function applyData() {
    const data = state.data;
    state.analysisEnd = Number(data.sample.analysis_end) || 12;
    state.originalDuration = Number(data.sample.original_duration) || state.analysisEnd;
    state.zones = (data.zones || []).slice();
    state.zoneById = new Map(state.zones.map((zone) => [zone.zone_id, zone]));
    state.zoneTimes = (data.zone_stats?.times || []).slice();
    state.zoneStats = state.zones.map((zone) => ({
      zone,
      counts: zone.counts || [],
      dwells: zone.max_continuous_dwell_seconds || [],
      peak: Number(zone.peak_count) || 0,
      peakTime: Number(zone.peak_time) || 0,
    }));
    state.rules = (data.rules || []).slice();
    state.ruleById = new Map(state.rules.map((rule) => [rule.rule_id, rule]));
    state.rules.forEach((rule) => {
      state.enabled[rule.rule_id] = Boolean(rule.enabled);
    });
    state.candidates = (data.candidates || []).slice();
    state.candidateById = new Map(
      state.candidates.map((candidate) => [candidate.candidate_id, candidate])
    );
    state.trackById = new Map(
      (state.tracksData.tracks || []).map((track) => [track.id, track])
    );

    document.title = `${data.project.name} · 区域统计与规则证据`;
    dom.demoRunId.textContent = data.run.run_id;
    dom.durationTime.textContent = fmt(state.analysisEnd);
    dom.timeline.max = String(state.analysisEnd);

    applyTimelineScale();
    renderZoneCards(0);
    renderChartLegend();
    renderRules();
    renderCandidates();
    renderMarkers();
    renderProvenance();
    setPlaybackEnabled(true);
    drawZoneChart();
  }

  function applyTimelineScale() {
    const tail = Math.max(0, state.originalDuration - state.analysisEnd);
    const ratio =
      (state.analysisEnd / Math.max(state.originalDuration, state.analysisEnd, 0.001)) * 100;
    dom.timelineGrid.style.setProperty("--analysis-ratio", `${ratio.toFixed(2)}%`);
    dom.timelineGrid.style.gridTemplateColumns =
      tail > 0.05 ? "var(--analysis-ratio, 87.98%) minmax(46px, 1fr)" : "1fr";
    dom.tailNote.textContent =
      `原视频全长 ${fmt(state.originalDuration)} 秒，本次分析覆盖前 ${fmt(state.analysisEnd)} 秒；` +
      (tail > 0.05
        ? `灰色部分为未分析的 ${fmt(tail)} 秒。`
        : "运行记录显示没有未分析的尾部。");
    dom.timelineTail.hidden = tail <= 0.05;
    if (tail > 0.05) {
      const label = `原视频后 ${fmt(tail)} 秒未参与分析`;
      dom.timelineTail.title = label;
      dom.timelineTail.setAttribute("aria-label", label);
    }
  }

  function renderProvenance() {
    const provenance = state.data.provenance || {};
    const notes = provenance.notes || [];
    const crosscheck = provenance.supervision_crosscheck;
    const crosscheckText =
      crosscheck && crosscheck.status === "completed"
        ? `Supervision 区域交叉核对：${crosscheck.checked_point_zone_pairs} 个点区对，` +
          `差异 ${crosscheck.mismatches} 个，均在多边形边界 1 px 内。`
        : "Supervision 区域交叉核对未运行。";
    dom.provenanceNote.textContent =
      `${notes.join(" ")} ${crosscheckText} ` +
      `规则配置哈希 ${shortHash(provenance.rule_config_sha256)}；` +
      `轨迹哈希 ${shortHash(provenance.tracks_jsonl_sha256)}；` +
      `区域派生哈希 ${shortHash(provenance.zone_assignments_sha256)}。`;
  }

  function renderChartLegend() {
    dom.chartLegend.innerHTML = state.zones
      .map(
        (zone, index) =>
          `<span><i style="background:${zoneColor(zone.zone_id, index)}"></i>${escapeHtml(
            shortZoneName(zone.name)
          )}</span>`
      )
      .join("");
  }

  function renderZoneCards(index) {
    const safeIndex = clampIndex(index);
    dom.zoneStatCards.innerHTML = state.zoneStats
      .map(({ zone, counts, dwells, peak }) => {
        const count = counts[safeIndex] ?? 0;
        const dwell = dwells[safeIndex] ?? 0;
        return (
          `<article class="zone-stat">` +
          `<div class="zone-stat-head"><i class="zone-dot" style="background:${zoneColor(
            zone.zone_id
          )}"></i>${escapeHtml(shortZoneName(zone.name))}</div>` +
          `<div class="zone-stat-value"><strong>${fmtInt.format(count)}</strong><small>个算法观测</small></div>` +
          `<span class="zone-stat-note">本帧最长连续可观测停留 ${fmt(dwell)} 秒<br>` +
          `全片观测峰值 ${fmtInt.format(peak)}（${fmt(zone.peak_time)} 秒）</span>` +
          `</article>`
        );
      })
      .join("");
  }

  function renderRules() {
    dom.ruleList.innerHTML = state.rules
      .map((rule) => {
        const enabled = Boolean(state.enabled[rule.rule_id]);
        const visibleCount = visibleCandidates().filter(
          (candidate) => candidate.rule_id === rule.rule_id
        ).length;
        const status = ruleStatusText(rule, enabled);
        const statusClass = !enabled
          ? "is-off"
          : rule.evaluation_status === "insufficient_observation"
            ? "is-warn"
            : "";
        const meta = ruleMeta(rule);
        return (
          `<article class="rule-item ${enabled ? "" : "is-off"}" data-rule-id="${escapeHtml(
            rule.rule_id
          )}">` +
          `<div class="rule-item-head">` +
          `<div class="rule-item-title"><svg class="icon" aria-hidden="true"><use href="#i-rule"></use></svg>` +
          `<div><h3>${escapeHtml(rule.label)}</h3><span class="rule-version">${escapeHtml(
            rule.version
          )}</span></div></div>` +
          `<label class="switch" title="${enabled ? "关闭该规则输出" : "打开该规则输出"}">` +
          `<input type="checkbox" data-rule-toggle="${escapeHtml(rule.rule_id)}" ${
            enabled ? "checked" : ""
          } aria-label="${escapeHtml(rule.label)}规则开关"><span class="switch-track"></span></label>` +
          `</div>` +
          `<p class="rule-status ${statusClass}">${escapeHtml(status)}</p>` +
          `<div class="rule-meta"><span>可见候选 <b>${visibleCount}</b> / 已生成 <b>${fmtInt.format(
            rule.candidate_count || 0
          )}</b></span>${meta}</div>` +
          `</article>`
        );
      })
      .join("");
  }

  function ruleStatusText(rule, enabled) {
    if (!enabled) {
      return "已关闭：本次演示中该规则输出已隐藏；规则引擎独立启停测试见执行报告。";
    }
    if (rule.evaluation_status === "disabled") {
      return "配置中已关闭，未运行。";
    }
    if (rule.evaluation_status === "insufficient_observation") {
      return rule.status_text || "观察时长不足，未评价。";
    }
    return rule.status_text || `规则已运行，${rule.candidate_count || 0} 条候选。`;
  }

  function ruleMeta(rule) {
    const params = rule.params || {};
    if (rule.rule_id === "long_stay") {
      return (
        `<span>阈值 ${fmt(params.min_continuous_observed_dwell_seconds, 1)} 秒</span>` +
        `<span>最大缺口 ${fmt(params.max_gap_seconds, 2)} 秒</span>` +
        `<span>边界容忍 ${fmt(params.zone_exit_tolerance_seconds, 2)} 秒</span>`
      );
    }
    if (rule.rule_id === "reversal") {
      const near = (rule.near_misses || [])[0];
      return (
        `<span>窗口 ${fmt(params.window_seconds, 1)} 秒</span>` +
        `<span>≥${params.min_effective_reversals} 次有效反转</span>` +
        `<span>效率 &lt; ${fmt(params.max_path_efficiency, 2)}</span>` +
        (near
          ? `<span>最接近：目标 ${pad3(near.track_id)} · ${near.max_effective_reversals} 次反转</span>`
          : "")
      );
    }
    if (rule.rule_id === "occupancy_change_60s") {
      return (
        `<span>窗口 ${fmt(params.window_seconds, 0)} 秒</span>` +
        `<span>&lt;${params.from_below} → ≥${params.to_at_least}</span>` +
        `<span>当前仅 ${fmt(state.analysisEnd, 2)} 秒</span>`
      );
    }
    return "";
  }

  function renderCandidates() {
    const visible = visibleCandidates();
    dom.candidateCount.textContent = fmtInt.format(visible.length);
    if (!visible.length) {
      const anyEnabled = Object.values(state.enabled).some(Boolean);
      dom.candidateList.innerHTML =
        `<div class="candidate-empty">` +
        (anyEnabled
          ? "当前没有可见候选。折返规则为 0 条；60 秒人数变化规则观察时长不足，未评价。"
          : "所有规则均已关闭。打开上方规则开关可查看对应输出。") +
        `</div>`;
      return;
    }
    dom.candidateList.innerHTML = visible
      .map((candidate) => {
        const featureText = candidateFeatureText(candidate);
        return (
          `<button class="candidate-card ${
            state.selected?.candidate_id === candidate.candidate_id ? "is-active" : ""
          }" type="button" data-candidate-id="${escapeHtml(candidate.candidate_id)}">` +
          `<div class="candidate-top"><span class="candidate-rule">${escapeHtml(
            candidate.rule_label
          )}</span><span class="candidate-time">${fmt(
            candidate.condition_met_video_time
          )} 秒</span></div>` +
          `<p class="candidate-title">${escapeHtml(candidate.display?.title || candidate.zone_name)}</p>` +
          `<span class="candidate-status">${escapeHtml(candidate.status_label)}</span>` +
          `<div class="candidate-features">${featureText}</div>` +
          `</button>`
        );
      })
      .join("");
  }

  function candidateFeatureText(candidate) {
    const f = candidate.trigger_features || {};
    if (candidate.rule_id === "long_stay") {
      return (
        `<span>可观测停留 ${fmt(f.dwell_observed_seconds)} 秒</span>` +
        `<span>观测点 ${fmtInt.format(f.observed_points || 0)}</span>` +
        (f.left_censored ? "<span>进入未观测</span>" : "<span>进入已观测</span>")
      );
    }
    if (candidate.rule_id === "reversal") {
      return (
        `<span>有效反转 ${fmtInt.format(f.effective_reversals || 0)} 次</span>` +
        `<span>路径效率 ${fmt(f.path_efficiency)}</span>` +
        `<span>净位移 ${fmt(f.net_displacement_px, 0)} px</span>`
      );
    }
    return `<span>窗口 ${fmt(f.window_seconds, 0)} 秒</span><span>人数变化 ${f.count_before} → ${f.count_at_trigger}</span>`;
  }

  function renderMarkers() {
    const visible = visibleCandidates();
    dom.candidateMarkers.innerHTML = visible
      .map((candidate) => {
        const ratio = clamp(candidate.condition_met_video_time / state.analysisEnd, 0, 1);
        return (
          `<button class="candidate-marker ${
            state.selected?.candidate_id === candidate.candidate_id ? "is-active" : ""
          }" type="button" data-candidate-id="${escapeHtml(candidate.candidate_id)}" ` +
          `style="left:${(ratio * 100).toFixed(3)}%" ` +
          `title="${escapeHtml(candidate.rule_label)} · 模拟触发 ${fmt(
            candidate.condition_met_video_time
          )} 秒" aria-label="${escapeHtml(candidate.rule_label)} 候选，模拟触发 ${fmt(
            candidate.condition_met_video_time
          )} 秒"></button>`
        );
      })
      .join("");
  }

  function visibleCandidates() {
    return state.candidates
      .filter((candidate) => state.enabled[candidate.rule_id])
      .sort(
        (a, b) =>
          Number(a.condition_met_video_time) - Number(b.condition_met_video_time) ||
          a.candidate_id.localeCompare(b.candidate_id)
      );
  }

  function selectCandidate(candidateId, options = {}) {
    const candidate = state.candidateById.get(candidateId);
    if (!candidate) return;
    if (!state.enabled[candidate.rule_id]) {
      state.enabled[candidate.rule_id] = true;
      renderRules();
      renderCandidates();
      renderMarkers();
    }
    state.selected = candidate;
    state.clipEnd = null;
    pause();
    const evidenceTime = Number(
      candidate.evidence_frame_video_time ?? candidate.condition_met_video_time
    );
    seek(evidenceTime, { fromUser: false });
    renderCandidateDetail(candidate);
    renderCandidates();
    renderMarkers();
    drawOverlays();
    if (options.play) playEvidence();
    else toast(`已定位到候选 ${candidate.candidate_id} 的证据帧：${fmt(evidenceTime)} 秒。`);
  }

  function clearCandidate() {
    state.selected = null;
    state.clipEnd = null;
    renderCandidateDetail(null);
    renderCandidates();
    renderMarkers();
    drawOverlays();
    toast("已清除候选选择。");
  }

  function renderCandidateDetail(candidate) {
    if (!candidate) {
      dom.candidateDetailBody.innerHTML =
        `<div class="detail-empty">` +
        `<svg class="icon" aria-hidden="true"><use href="#i-rule"></use></svg>` +
        `<strong>请选择左侧候选</strong>` +
        `<p>选择后显示规则版本、阈值、触发特征、条件满足时刻、输出时刻和原视频证据。</p>` +
        `</div>`;
      return;
    }
    const features = candidate.trigger_features || {};
    const thresholds = candidate.thresholds || {};
    const lifecycle = candidate.lifecycle || {};
    const intervals = (lifecycle.active_intervals || [])
      .map(
        (interval) =>
          `${fmt(interval.start_video_time)}–${fmt(interval.end_video_time)} 秒`
      )
      .join("；");
    const featureRows = featureRowsFor(candidate);
    const caveats = (candidate.caveats || []).join(" ");
    dom.candidateDetailBody.innerHTML =
      `<div class="detail-top">` +
      `<div><p class="detail-time">${fmt(candidate.condition_met_video_time)} <small>秒 · 模拟触发</small></p>` +
      `<p class="detail-title">${escapeHtml(candidate.rule_label)} · ${escapeHtml(
        candidate.zone_name
      )}${candidate.track_id != null ? ` · 目标 ${pad3(candidate.track_id)}` : ""}</p></div>` +
      `<span class="detail-status">${escapeHtml(candidate.status_label)}</span></div>` +
      `<div class="detail-actions">` +
      `<button class="btn btn-primary" type="button" data-detail-action="play-evidence">播放证据片段（前后 4 秒）</button>` +
      `<button class="btn btn-secondary" type="button" data-detail-action="seek-trigger">定位证据帧</button>` +
      `<button class="btn btn-secondary" type="button" data-detail-action="seek-clip-start">回到片段起点</button>` +
      `</div>` +
      `<div class="detail-grid">` +
      detailField("规则版本", candidate.rule_version) +
      detailField("规则配置哈希", shortHash(candidate.rule_config_sha256)) +
      detailField("区域版本", candidate.zone_version || "—") +
      detailField("轨迹编号", candidate.track_id != null ? pad3(candidate.track_id) : "区域级") +
      detailField("证据帧时刻", `${fmt(candidate.evidence_frame_video_time)} 秒`) +
      detailField("模拟触发视频时刻", `${fmt(candidate.condition_met_video_time)} 秒`) +
      detailField("帧覆盖区间", `[${fmt(candidate.frame_coverage_start)}, ${fmt(candidate.frame_coverage_end)}] 秒`) +
      detailField("证据帧", `#${candidate.condition_met_frame_index}`) +
      detailField("候选编号", candidate.candidate_id) +
      detailField("区域配置哈希", shortHash(state.data.provenance?.zone_config_sha256)) +
      detailField("轨迹文件哈希", shortHash(state.data.provenance?.tracks_jsonl_sha256)) +
      detailField("实际写出墙钟时间", candidate.emitted_at_utc || "—") +
      `</div>` +
      `<table class="detail-table"><thead><tr><th>项目</th><th>实际值</th></tr></thead><tbody>` +
      featureRows +
      `<tr><th>阈值</th><td>${escapeHtml(thresholdSummary(candidate, thresholds))}</td></tr>` +
      `<tr><th>证据框</th><td>${escapeHtml(
        `frame #${candidate.evidence?.frame_index ?? "—"} · 证据帧 ${fmt(
          candidate.evidence?.frame_video_time ?? candidate.evidence?.video_time
        )} 秒 · 覆盖至 ${fmt(candidate.evidence?.frame_coverage_end)} 秒 · ` +
        `[${(candidate.evidence?.bbox || []).map((value) => fmt(value, 1)).join(", ")}]`
      )}</td></tr>` +
      `<tr><th>证据视频</th><td><code>${escapeHtml(
        String(candidate.evidence?.video_path || "").split("/").pop() || "—"
      )}</code><br><small>原视频，未重新推理</small></td></tr>` +
      `<tr><th>持续区间</th><td>${escapeHtml(intervals || "—")}</td></tr>` +
      `<tr><th>结束语义</th><td>${escapeHtml(
        lifecycle.ongoing_at_analysis_end
          ? "观察窗口结束时条件仍在持续"
          : `结束原因：${lifecycle.end_reason || "—"}`
      )}</td></tr>` +
      `<tr><th>去重键</th><td><code>${escapeHtml(candidate.dedup_key)}</code></td></tr>` +
      `<tr><th>输出模式</th><td>${escapeHtml(candidate.output_mode || "—")}<br><small>${escapeHtml(
        candidate.latency_note || ""
      )}</small></td></tr>` +
      `</tbody></table>` +
      `<p class="detail-caveat">时间口径：证据帧时刻是帧起点；模拟触发视频时刻是触发帧覆盖结束；实际写出墙钟时间只表示本次批处理写盘，不是现场告警延迟。${escapeHtml(
        caveats
      )}</p>`;
  }

  function featureRowsFor(candidate) {
    const f = candidate.trigger_features || {};
    if (candidate.rule_id === "long_stay") {
      return [
        ["可观测停留", `${fmt(f.dwell_observed_seconds)} 秒`],
        ["覆盖区间时长", `${fmt(f.coverage_elapsed_seconds)} 秒`],
        ["覆盖区间缺口", `${fmt(f.gap_seconds)} 秒`],
        ["有效观测点", `${fmtInt.format(f.observed_points || 0)}`],
        ["进入时刻是否观测", f.entry_observed ? "是" : "否（时长是下界）"],
        ["触发前边界抖动次数", fmtInt.format(f.boundary_excursions || 0)],
        ["触发前最大缺口", `${fmt(f.max_gap_seconds)} 秒`],
        ["触发前缺口次数", fmtInt.format(f.gap_count || 0)],
      ]
        .map(
          ([label, value]) =>
            `<tr><th>${escapeHtml(label)}</th><td>${escapeHtml(String(value))}</td></tr>`
        )
        .join("");
    }
    if (candidate.rule_id === "reversal") {
      return [
        ["窗口内有效反转", `${fmtInt.format(f.effective_reversals || 0)} 次`],
        ["路径效率", f.path_efficiency != null ? fmt(f.path_efficiency) : "不可计算"],
        ["路径长度", `${fmt(f.path_length_px, 1)} px`],
        ["净位移", `${fmt(f.net_displacement_px, 1)} px`],
        ["有效运动段总长", `${fmt(f.valid_leg_length_px, 1)} px`],
        ["有效运动秒数", `${fmt(f.valid_motion_seconds)} 秒`],
        ["连续观测时长", `${fmt(f.observed_seconds)} 秒`],
        ["有效腿数", fmtInt.format(f.leg_count || 0)],
        ["平滑方式", f.smoothing || "—"],
      ]
        .map(
          ([label, value]) =>
            `<tr><th>${escapeHtml(label)}</th><td>${escapeHtml(String(value))}</td></tr>`
        )
        .join("");
    }
    return [
      ["窗口", `${fmt(f.window_seconds, 0)} 秒`],
      ["窗口前人数", fmtInt.format(f.count_before ?? 0)],
      ["触发时人数", fmtInt.format(f.count_at_trigger ?? 0)],
    ]
      .map(
        ([label, value]) =>
          `<tr><th>${escapeHtml(label)}</th><td>${escapeHtml(String(value))}</td></tr>`
      )
      .join("");
  }

  function thresholdSummary(candidate, thresholds) {
    if (candidate.rule_id === "long_stay") {
      return `连续可观测停留 ≥ ${fmt(
        thresholds.min_continuous_observed_dwell_seconds,
        1
      )} 秒；最大缺口 ${fmt(thresholds.max_gap_seconds, 2)} 秒；边界容忍 ${fmt(
        thresholds.zone_exit_tolerance_seconds,
        2
      )} 秒`;
    }
    if (candidate.rule_id === "reversal") {
      return `窗口 ${fmt(thresholds.window_seconds, 1)} 秒；有效反转 ≥ ${
        thresholds.min_effective_reversals
      }；路径效率 < ${fmt(thresholds.max_path_efficiency, 2)}；单腿位移 ≥ ${fmt(
        thresholds.min_leg_displacement_px,
        0
      )} px；转角 ≥ ${fmt(thresholds.min_turn_angle_degrees, 0)}°`;
    }
    return `窗口 ${fmt(thresholds.window_seconds, 0)} 秒；<${thresholds.from_below} → ≥${thresholds.to_at_least}`;
  }

  function detailField(label, value) {
    return (
      `<div class="detail-field"><span>${escapeHtml(label)}</span><b>${escapeHtml(
        String(value)
      )}</b></div>`
    );
  }

  function playEvidence() {
    const candidate = state.selected;
    if (!candidate || !state.assetsOk) return;
    const clip = candidate.evidence_clip || {};
    const evidenceTime = Number(
      candidate.evidence_frame_video_time ?? candidate.condition_met_video_time
    );
    const start = Number(clip.start_video_time ?? Math.max(0, evidenceTime - 2));
    const end = Number(clip.end_video_time ?? Math.min(state.analysisEnd, evidenceTime + 2));
    pause();
    seek(start, { fromUser: false });
    state.clipEnd = end;
    play({ keepClip: true });
    toast(`正在播放候选证据：${fmt(start)}–${fmt(end)} 秒。`);
  }

  function seekToTrigger() {
    if (!state.selected) return;
    const evidenceTime = Number(
      state.selected.evidence_frame_video_time ?? state.selected.condition_met_video_time
    );
    pause();
    seek(evidenceTime, { fromUser: false });
    toast(`已定位到证据帧 ${fmt(evidenceTime)} 秒；模拟触发时刻为 ${fmt(
      state.selected.condition_met_video_time
    )} 秒。`);
  }

  function seekToClipStart() {
    if (!state.selected) return;
    const evidenceTime = Number(
      state.selected.evidence_frame_video_time ?? state.selected.condition_met_video_time
    );
    const start = Number(
      state.selected.evidence_clip?.start_video_time ??
        Math.max(0, evidenceTime - 2)
    );
    pause();
    seek(start, { fromUser: false });
    toast(`已回到证据片段起点 ${fmt(start)} 秒。`);
  }

  function setupVideos() {
    const original = dom.originalVideo;
    const replay = dom.replayVideo;
    dom.originalOverlay.width = state.data.sample.width || 1920;
    dom.originalOverlay.height = state.data.sample.height || 1080;
    dom.replayOverlay.width = state.data.sample.width || 1920;
    dom.replayOverlay.height = state.data.sample.height || 1080;

    original.addEventListener("loadedmetadata", () => {
      if (Number.isFinite(original.duration) && original.duration > 0) {
        state.originalDuration = original.duration;
        applyTimelineScale();
      }
      applyTimeToVideos(state.time);
      state.dirty = true;
    });
    replay.addEventListener("loadedmetadata", () => {
      if (Number.isFinite(replay.duration) && replay.duration > 0) {
        if (Math.abs(replay.duration - state.analysisEnd) > 0.2) {
          toast("提示：分析回放时长与运行记录不一致，请检查派生文件。", 4200);
        }
        if (replay.duration + 0.05 < state.analysisEnd) {
          state.analysisEnd = replay.duration;
          applyTimelineScale();
        }
      }
      applyTimeToVideos(state.time);
      state.dirty = true;
    });

    original.addEventListener("play", () => {
      state.playing = true;
      updatePlayUi();
      updateSyncBadge();
      if (replay.paused) replay.play().catch(() => {});
    });
    original.addEventListener("pause", () => {
      state.playing = false;
      if (!replay.paused) replay.pause();
      updatePlayUi();
      state.syncOk = true;
      updateSyncBadge();
      state.dirty = true;
    });
    original.addEventListener("ended", () => {
      state.playing = false;
      replay.pause();
      updatePlayUi();
    });
    replay.addEventListener("ended", () => {
      if (!original.paused) {
        pause();
        seek(state.analysisEnd, { fromUser: false });
      }
    });
    original.addEventListener("error", () => markVideoError("original_video"));
    replay.addEventListener("error", () => markVideoError("analysis_video"));

    [original, replay].forEach((video) => {
      video.addEventListener("click", () => {
        if (state.assetsOk) togglePlay();
      });
      video.addEventListener("dblclick", (event) => {
        event.preventDefault();
        toggleFullscreen();
      });
    });

    updatePlayUi();
    updateSyncBadge();
    requestAnimationFrame(tick);
  }

  function applyTimeToVideos(time) {
    const mediaTime = clampMediaTime(time);
    if (dom.originalVideo.readyState >= 1) dom.originalVideo.currentTime = mediaTime;
    if (dom.replayVideo.readyState >= 1) dom.replayVideo.currentTime = mediaTime;
  }

  function clampMediaTime(time) {
    if (state.analysisEnd <= 0) return 0;
    return Math.max(0, Math.min(time, Math.max(0, state.analysisEnd - 0.04)));
  }

  async function play(options = {}) {
    if (!state.assetsOk) return;
    if (!options.keepClip) state.clipEnd = null;
    if (dom.originalVideo.readyState < 1 || dom.replayVideo.readyState < 1) {
      toast("视频还在加载，请稍候再播放。");
      return;
    }
    if (state.time >= state.analysisEnd - 0.03) seek(0, { fromUser: false });
    try {
      await Promise.allSettled([dom.originalVideo.play(), dom.replayVideo.play()]);
      state.playing = true;
      updatePlayUi();
    } catch (error) {
      toast("播放失败，请检查视频文件是否可读。");
    }
  }

  function pause() {
    dom.originalVideo.pause();
    dom.replayVideo.pause();
    state.playing = false;
    updatePlayUi();
    state.syncOk = true;
    updateSyncBadge();
    state.dirty = true;
  }

  function togglePlay() {
    if (state.playing || !dom.originalVideo.paused) pause();
    else play();
  }

  function seek(time, options = {}) {
    if (!options.keepClip) state.clipEnd = null;
    const next = Math.max(0, Math.min(state.analysisEnd, Number(time) || 0));
    state.time = next;
    applyTimeToVideos(next);
    updateTimeUi(next);
    renderAtTime(next);
    state.dirty = true;
    if (state.playing && next >= state.analysisEnd - 0.02) pause();
    if (options.fromUser && next >= state.analysisEnd - 0.005) {
      toast(`已到本次分析范围末尾（${fmt(state.analysisEnd)} 秒）。`);
    }
    drawOverlays();
  }

  function updateTimeUi(time) {
    state.time = time;
    dom.currentTime.textContent = fmt(time);
    if (!state.scrubbing) dom.timeline.value = String(time);
    const pct = state.analysisEnd > 0 ? (time / state.analysisEnd) * 100 : 0;
    dom.timeline.style.setProperty("--pct", `${clamp(pct, 0, 100)}%`);
    dom.timeline.setAttribute(
      "aria-valuetext",
      `${fmt(time)} 秒，共 ${fmt(state.analysisEnd)} 秒`
    );
    const atEnd = time >= state.analysisEnd - 0.02;
    if (!state.playing && atEnd) dom.playLabel.textContent = "重播";
    else if (!state.playing) dom.playLabel.textContent = "播放";
  }

  function updatePlayUi() {
    const playing = state.playing;
    const atEnd = state.time >= state.analysisEnd - 0.02;
    dom.playLabel.textContent = playing ? "暂停" : atEnd ? "重播" : "播放";
    dom.playPause.setAttribute("aria-label", playing ? "暂停" : atEnd ? "从头重播" : "播放");
    dom.playIcon.hidden = playing;
    dom.pauseIcon.hidden = !playing;
    dom.playPause.classList.toggle("is-playing", playing);
  }

  function updateSyncBadge() {
    if (!state.assetsOk) {
      dom.syncBadge.classList.add("is-off");
      dom.syncText.textContent = "无法同步";
      return;
    }
    dom.syncBadge.classList.toggle("is-off", !state.syncOk);
    dom.syncText.textContent = !state.playing
      ? "两路就绪"
      : state.syncOk
        ? "两路同步"
        : "正在校正";
  }

  function tick() {
    if (state.data) {
      const original = dom.originalVideo;
      if (!original.paused) {
        const time = original.currentTime;
        if (state.clipEnd && time >= state.clipEnd - 0.02) {
          pause();
          seek(state.clipEnd, { fromUser: false });
          toast("证据片段播放结束。");
        } else if (time >= state.analysisEnd - 0.02) {
          pause();
          seek(state.analysisEnd, { fromUser: false });
        } else {
          updateTimeUi(time);
          renderAtTime(time);
          const drift = Math.abs(dom.replayVideo.currentTime - time);
          const ok = drift <= 0.18;
          if (!ok && dom.replayVideo.readyState >= 2) {
            dom.replayVideo.currentTime = time;
          }
          if (ok !== state.syncOk) {
            state.syncOk = ok;
            updateSyncBadge();
          }
          state.dirty = true;
        }
      }
      if (state.dirty) {
        drawOverlays();
        state.dirty = false;
      }
    }
    requestAnimationFrame(tick);
  }

  function renderAtTime(time) {
    const index = indexAtTime(time);
    if (index === state.lastStatsIndex) return;
    state.lastStatsIndex = index;
    renderZoneCards(index);
    drawZoneChart();
  }

  function indexAtTime(time) {
    const times = state.zoneTimes;
    if (!times.length) return 0;
    let low = 0;
    let high = times.length - 1;
    let answer = 0;
    while (low <= high) {
      const mid = (low + high) >> 1;
      if (times[mid] <= time + 1e-6) {
        answer = mid;
        low = mid + 1;
      } else {
        high = mid - 1;
      }
    }
    return answer;
  }

  function drawZoneChart() {
    const canvas = dom.zoneChart;
    if (!canvas || !state.zoneStats.length) return;
    const ctx = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    const padding = { left: 42, right: 14, top: 18, bottom: 30 };
    const plotWidth = width - padding.left - padding.right;
    const plotHeight = height - padding.top - padding.bottom;
    const maxCount = Math.max(
      5,
      ...state.zoneStats.flatMap((zone) => zone.counts)
    );
    const yMax = Math.ceil(maxCount / 5) * 5;
    const xFor = (time) =>
      padding.left + (time / Math.max(state.analysisEnd, 0.001)) * plotWidth;
    const yFor = (count) =>
      padding.top + plotHeight - (count / Math.max(yMax, 1)) * plotHeight;

    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#fbfdfe";
    ctx.fillRect(0, 0, width, height);

    ctx.font = `500 12px ${FONT_STACK}`;
    ctx.textBaseline = "middle";
    ctx.strokeStyle = "#e4edf4";
    ctx.lineWidth = 1;
    for (let count = 0; count <= yMax; count += 5) {
      const y = yFor(count);
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(width - padding.right, y);
      ctx.stroke();
      ctx.fillStyle = "#7b8da0";
      ctx.textAlign = "right";
      ctx.fillText(String(count), padding.left - 8, y);
    }
    ctx.textAlign = "center";
    for (const time of [0, 3, 6, 9, 12]) {
      if (time > state.analysisEnd + 0.01) continue;
      ctx.fillStyle = "#7b8da0";
      ctx.fillText(`${time}s`, xFor(time), height - 11);
    }

    state.zoneStats.forEach(({ zone, counts }) => {
      const color = zoneColor(zone.zone_id);
      ctx.beginPath();
      counts.forEach((count, index) => {
        const time = state.zoneTimes[index] ?? 0;
        const x = xFor(time);
        const y = yFor(count);
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.strokeStyle = color;
      ctx.lineWidth = 2.6;
      ctx.lineJoin = "round";
      ctx.stroke();
    });

    const index = indexAtTime(state.time);
    const playheadX = xFor(state.zoneTimes[index] ?? state.time);
    ctx.beginPath();
    ctx.moveTo(playheadX, padding.top);
    ctx.lineTo(playheadX, height - padding.bottom);
    ctx.strokeStyle = "#f59e0b";
    ctx.lineWidth = 2;
    ctx.stroke();
    state.zoneStats.forEach(({ zone, counts }) => {
      const y = yFor(counts[index] ?? 0);
      ctx.beginPath();
      ctx.arc(playheadX, y, 4, 0, Math.PI * 2);
      ctx.fillStyle = zoneColor(zone.zone_id);
      ctx.fill();
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = 1.5;
      ctx.stroke();
    });

    state.chartGeometry = { padding, plotWidth, plotHeight, width, height };
  }

  function chartClick(event) {
    if (!state.chartGeometry) return;
    const rect = dom.zoneChart.getBoundingClientRect();
    const ratio = clamp((event.clientX - rect.left) / rect.width, 0, 1);
    const canvasX = ratio * dom.zoneChart.width;
    const { padding, plotWidth } = state.chartGeometry;
    const timeRatio = clamp((canvasX - padding.left) / plotWidth, 0, 1);
    pause();
    seek(timeRatio * state.analysisEnd, { fromUser: true });
  }

  function drawOverlays() {
    if (!state.data) return;
    const originalCtx = dom.originalOverlay.getContext("2d");
    const replayCtx = dom.replayOverlay.getContext("2d");
    const width = dom.originalOverlay.width;
    const height = dom.originalOverlay.height;
    originalCtx.clearRect(0, 0, width, height);
    replayCtx.clearRect(0, 0, width, height);
    if (!state.assetsOk) return;

    const index = indexAtTime(state.time);
    state.zoneStats.forEach(({ zone, counts }) => {
      drawZone(originalCtx, zone, counts[index] ?? 0, true);
      drawZone(replayCtx, zone, counts[index] ?? 0, false);
    });

    const candidate = state.selected;
    if (!candidate) return;
    const track = candidate.track_id != null ? state.trackById.get(candidate.track_id) : null;
    if (track?.points?.length) {
      const pointIndex = findPointIndex(track.points, state.time);
      const triggerPoint = findPointIndex(
        track.points,
        Number(candidate.condition_met_video_time)
      );
      if (triggerPoint >= 0) {
        const point = track.points[triggerPoint];
        drawBox(originalCtx, point, "#f59e0b", "候选触发点", true, width);
        drawBox(replayCtx, point, "#f59e0b", "候选触发点", true, width);
      }
      if (pointIndex >= 0) {
        drawPath(originalCtx, track.points, pointIndex, "#f59e0b", width);
        drawBox(originalCtx, track.points[pointIndex], "#2dd4bf", `目标 ${pad3(track.id)}`, true, width);
        drawBox(replayCtx, track.points[pointIndex], "#2dd4bf", `目标 ${pad3(track.id)}`, true, width);
      }
    }
    highlightSelectedZone(originalCtx, candidate.zone_id, width);
    highlightSelectedZone(replayCtx, candidate.zone_id, width);
  }

  function drawZone(ctx, zone, count, primary) {
    const color = zoneColor(zone.zone_id);
    ctx.save();
    ctx.beginPath();
    zone.polygon.forEach(([x, y], index) => {
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.closePath();
    ctx.fillStyle = hexToRgba(color, primary ? 0.1 : 0.05);
    ctx.fill();
    ctx.strokeStyle = hexToRgba(color, primary ? 0.95 : 0.55);
    ctx.lineWidth = primary ? 4 : 2.5;
    ctx.setLineDash(primary ? [] : [10, 8]);
    ctx.stroke();
    ctx.setLineDash([]);
    const centroid = polygonCentroid(zone.polygon);
    drawTag(
      ctx,
      centroid.x,
      centroid.y,
      `${shortZoneName(zone.name)} · ${count}`,
      color,
      "#fff",
      primary ? 28 : 20
    );
    ctx.restore();
  }

  function highlightSelectedZone(ctx, zoneId, width) {
    if (!zoneId) return;
    const zone = state.zoneById.get(zoneId);
    if (!zone) return;
    ctx.save();
    ctx.beginPath();
    zone.polygon.forEach(([x, y], index) => {
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.closePath();
    ctx.strokeStyle = "rgba(245, 158, 11, 0.95)";
    ctx.lineWidth = 7;
    ctx.setLineDash([]);
    ctx.stroke();
    ctx.restore();
  }

  function drawPath(ctx, points, endIndex, color, canvasWidth) {
    if (endIndex < 1) return;
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(pointX(points[0]), pointY(points[0]));
    for (let index = 1; index <= endIndex; index += 1) {
      ctx.lineTo(pointX(points[index]), pointY(points[index]));
    }
    ctx.strokeStyle = color;
    ctx.lineWidth = Math.max(5, Math.round(canvasWidth * 0.0032));
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.shadowColor = "rgba(245, 158, 11, 0.5)";
    ctx.shadowBlur = 16;
    ctx.stroke();
    ctx.restore();
  }

  function drawBox(ctx, point, color, label, glow, canvasWidth) {
    const [x1, y1, x2, y2] = [point[1], point[2], point[3], point[4]];
    ctx.save();
    ctx.beginPath();
    roundedRect(ctx, x1, y1, x2 - x1, y2 - y1, Math.max(5, Math.round((y2 - y1) * 0.04)));
    ctx.strokeStyle = color;
    ctx.lineWidth = Math.max(4, Math.round(canvasWidth * 0.0028));
    if (glow) {
      ctx.shadowColor = hexToRgba(color, 0.7);
      ctx.shadowBlur = 16;
    }
    ctx.stroke();
    ctx.restore();
    if (label) drawTag(ctx, (x1 + x2) / 2, y1, label, color, "#062a27", 24);
  }

  function drawTag(ctx, x, y, text, background, foreground, fontSize) {
    if (!text) return;
    const size = fontSize || 24;
    ctx.save();
    ctx.font = `800 ${size}px ${FONT_STACK}`;
    const textWidth = ctx.measureText(text).width;
    const padX = Math.round(size * 0.5);
    const padY = Math.round(size * 0.28);
    const boxWidth = textWidth + padX * 2;
    const boxHeight = size + padY * 2;
    let left = clamp(x - boxWidth / 2, 8, dom.originalOverlay.width - boxWidth - 8);
    let top = y - boxHeight - Math.round(size * 0.45);
    if (top < 52) top = y + Math.round(size * 0.45);
    ctx.beginPath();
    roundedRect(ctx, left, top, boxWidth, boxHeight, Math.round(size * 0.32));
    ctx.fillStyle = background;
    ctx.shadowColor = "rgba(0,0,0,.3)";
    ctx.shadowBlur = 10;
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.fillStyle = foreground;
    ctx.textBaseline = "middle";
    ctx.fillText(text, left + padX, top + boxHeight / 2);
    ctx.restore();
  }

  function roundedRect(ctx, x, y, width, height, radius) {
    const r = Math.min(radius, Math.abs(width) / 2, Math.abs(height) / 2);
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + width, y, x + width, y + height, r);
    ctx.arcTo(x + width, y + height, x, y + height, r);
    ctx.arcTo(x, y + height, x, y, r);
    ctx.arcTo(x, y, x + width, y, r);
    ctx.closePath();
  }

  function findPointIndex(points, time) {
    let low = 0;
    let high = points.length - 1;
    let answer = -1;
    while (low <= high) {
      const mid = (low + high) >> 1;
      if (points[mid][0] <= time + 1e-4) {
        answer = mid;
        low = mid + 1;
      } else {
        high = mid - 1;
      }
    }
    return answer;
  }

  function pointX(point) {
    return (point[1] + point[3]) / 2;
  }

  function pointY(point) {
    return point[4];
  }

  function polygonCentroid(polygon) {
    let x = 0;
    let y = 0;
    polygon.forEach(([px, py]) => {
      x += px;
      y += py;
    });
    return { x: x / polygon.length, y: y / polygon.length };
  }

  async function checkAssets() {
    const checks = [
      ["original_video", "原视频"],
      ["analysis_video", "分析回放"],
    ];
    const missing = [];
    await Promise.all(
      checks.map(async ([key, label]) => {
        const url = state.data.files[key];
        try {
          const response = await fetch(url, { method: "HEAD", cache: "no-store" });
          if (!response.ok) {
            missing.push({ key, label, url, path: state.data.expected_paths[key] });
          }
        } catch (error) {
          missing.push({ key, label, url, path: state.data.expected_paths[key] });
        }
      })
    );
    missing.forEach((item) => addMissing(item));
    const videoMissing = missing.length > 0;
    setPlaybackEnabled(!videoMissing);
    if (!videoMissing) updateSyncBadge();
  }

  function addMissing(item) {
    if (!item) return;
    state.missing.set(item.key, item);
    renderMissing();
    const isReplay = item.key === "analysis_video";
    const frame = isReplay ? dom.replayFrame : dom.originalFrame;
    const errorBox = isReplay ? dom.replayError : dom.originalError;
    frame.classList.add("is-error");
    errorBox.hidden = false;
    state.dirty = true;
    drawOverlays();
  }

  function renderMissing() {
    const items = [...state.missing.values()];
    dom.missingBanner.hidden = items.length === 0;
    if (!items.length) return;
    dom.missingTitle.textContent = "规则演示文件缺失，暂时无法播放";
    dom.missingList.innerHTML = items
      .map(
        (item) =>
          `<li><strong>${escapeHtml(item.label)}</strong>：${escapeHtml(item.url)}<br>` +
          `期望：<code>${escapeHtml(item.path)}</code></li>`
      )
      .join("");
  }

  function markVideoError(key) {
    const isReplay = key === "analysis_video";
    addMissing({
      key,
      label: isReplay ? "分析回放" : "原视频",
      url: isReplay ? state.data.files.analysis_video : state.data.files.original_video,
      path: isReplay ? state.data.expected_paths.analysis_video : state.data.expected_paths.original_video,
    });
  }

  function setPlaybackEnabled(enabled) {
    state.assetsOk = enabled;
    [
      dom.playPause,
      dom.back1,
      dom.forward1,
      dom.replayBtn,
      dom.fullscreenBtn,
      dom.timeline,
    ].forEach((element) => {
      element.disabled = !enabled;
    });
    if (!enabled) {
      pause();
      dom.syncBadge.classList.add("is-off");
      dom.syncText.textContent = "无法同步";
      state.dirty = true;
      drawOverlays();
    } else {
      state.dirty = true;
      requestAnimationFrame(drawOverlays);
    }
  }

  function showFatal(error) {
    dom.fatalError.hidden = false;
    dom.fatalTitle.textContent = "规则演示数据未加载";
    dom.fatalMessage.textContent =
      "无法读取规则演示数据。请通过本地演示服务打开页面，并确认 E04 输出完整。";
    dom.fatalDetail.textContent =
      `${location.origin}${location.pathname}\n` +
      `${error?.message || String(error)}\n\n` +
      "规则数据：st_memory_experiments/demo/data/rules.json\n" +
      "轨迹数据：st_memory_experiments/demo/data/demo.json\n\n" +
      "启动方式：在 st_memory_experiments 目录执行 python3 demo/serve.py\n" +
      "访问地址：http://127.0.0.1:8765/rules.html";
  }

  function bindEvents() {
    dom.playPause.addEventListener("click", togglePlay);
    dom.back1.addEventListener("click", () => seek(state.time - 1));
    dom.forward1.addEventListener("click", () => seek(state.time + 1));
    dom.replayBtn.addEventListener("click", () => {
      state.clipEnd = null;
      pause();
      seek(0, { fromUser: false });
      play();
    });
    dom.fullscreenBtn.addEventListener("click", toggleFullscreen);
    dom.timelineTail.addEventListener("click", () => {
      toast(
        `演示只覆盖前 ${fmt(state.analysisEnd)} 秒；原视频后 ${fmt(
          Math.max(0, state.originalDuration - state.analysisEnd)
        )} 秒未参与分析。`
      );
    });
    dom.timeline.addEventListener("pointerdown", () => {
      state.scrubbing = true;
      pause();
    });
    dom.timeline.addEventListener("input", () => {
      state.scrubbing = true;
      pause();
      seek(Number(dom.timeline.value), { fromUser: false });
    });
    ["change", "pointerup", "touchend", "blur"].forEach((eventName) => {
      dom.timeline.addEventListener(eventName, () => {
        if (!state.scrubbing) return;
        state.scrubbing = false;
        seek(Number(dom.timeline.value), { fromUser: true });
      });
    });
    dom.ruleList.addEventListener("change", (event) => {
      const input = event.target.closest("[data-rule-toggle]");
      if (!input) return;
      state.enabled[input.dataset.ruleToggle] = input.checked;
      if (state.selected && !state.enabled[state.selected.rule_id]) {
        state.selected = null;
        renderCandidateDetail(null);
      }
      renderRules();
      renderCandidates();
      renderMarkers();
      drawOverlays();
      if (!input.checked) toast("该规则输出已在本次演示中隐藏。");
    });
    dom.candidateList.addEventListener("click", (event) => {
      const card = event.target.closest("[data-candidate-id]");
      if (card) selectCandidate(card.dataset.candidateId);
    });
    dom.candidateMarkers.addEventListener("click", (event) => {
      const marker = event.target.closest("[data-candidate-id]");
      if (marker) selectCandidate(marker.dataset.candidateId);
    });
    dom.clearCandidate.addEventListener("click", clearCandidate);
    dom.candidateDetailBody.addEventListener("click", (event) => {
      const button = event.target.closest("[data-detail-action]");
      if (!button) return;
      if (button.dataset.detailAction === "play-evidence") playEvidence();
      else if (button.dataset.detailAction === "seek-trigger") seekToTrigger();
      else if (button.dataset.detailAction === "seek-clip-start") seekToClipStart();
    });
    dom.zoneChart.addEventListener("click", chartClick);
    dom.retryAssets.addEventListener("click", () => location.reload());
    dom.fatalRetry.addEventListener("click", () => location.reload());
    document.addEventListener("fullscreenchange", updateFullscreenUi);
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && state.playing) {
        state.dirty = true;
        if (dom.replayVideo.readyState >= 2) {
          dom.replayVideo.currentTime = dom.originalVideo.currentTime;
        }
      }
    });
  }

  function onKeyDown(event) {
    const tag = event.target.tagName;
    if (
      ["INPUT", "SELECT", "TEXTAREA", "BUTTON", "A", "SUMMARY"].includes(tag) ||
      event.target.isContentEditable
    ) {
      return;
    }
    if (event.key === " " || event.code === "Space") {
      event.preventDefault();
      if (state.assetsOk) togglePlay();
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      if (state.assetsOk) seek(state.time - 1);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      if (state.assetsOk) seek(state.time + 1);
    } else if (event.key === "Home") {
      event.preventDefault();
      if (state.assetsOk) seek(0);
    } else if (event.key === "End") {
      event.preventDefault();
      if (state.assetsOk) seek(state.analysisEnd);
    } else if (event.key === "f" || event.key === "F") {
      event.preventDefault();
      toggleFullscreen();
    } else if (event.key === "Escape" && document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
    }
  }

  function toggleFullscreen() {
    if (!document.fullscreenEnabled) {
      toast("当前浏览器不允许全屏，请按 F11 使用浏览器全屏。");
      return;
    }
    if (!document.fullscreenElement) {
      dom.playerCard.requestFullscreen().catch(() => {
        toast("未能进入全屏，请按 F11 使用浏览器全屏。");
      });
    } else {
      document.exitFullscreen().catch(() => {});
    }
  }

  function updateFullscreenUi() {
    const active = Boolean(document.fullscreenElement);
    dom.fullscreenLabel.textContent = active ? "退出全屏" : "全屏";
    dom.fullscreenIcon.hidden = active;
    dom.exitFullscreenIcon.hidden = !active;
  }

  function zoneColor(zoneId) {
    if (ZONE_COLORS[zoneId]) return ZONE_COLORS[zoneId];
    const index = state.zones.findIndex((zone) => zone.zone_id === zoneId);
    return FALLBACK_COLORS[Math.max(0, index) % FALLBACK_COLORS.length];
  }

  function shortZoneName(name) {
    return String(name || "")
      .replace("演示观察区 ", "区域 ")
      .replace("演示观察区", "区域");
  }

  function toast(message, duration = 3200) {
    window.clearTimeout(state.toastTimer);
    window.clearTimeout(toastHideTimer);
    dom.toast.textContent = message;
    dom.toast.hidden = false;
    requestAnimationFrame(() => dom.toast.classList.add("is-visible"));
    state.toastTimer = window.setTimeout(() => {
      dom.toast.classList.remove("is-visible");
      toastHideTimer = window.setTimeout(() => {
        dom.toast.hidden = true;
      }, 220);
    }, duration);
  }

  function fmt(value, digits = 2) {
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(digits) : "—";
  }

  function pad3(value) {
    return String(value).padStart(3, "0");
  }

  function shortHash(value) {
    return value ? `${String(value).slice(0, 12)}…` : "—";
  }

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function clampIndex(index) {
    if (!state.zoneTimes.length) return 0;
    return Math.max(0, Math.min(state.zoneTimes.length - 1, Number(index) || 0));
  }

  function hexToRgba(hex, alpha) {
    const normalized = String(hex).replace("#", "");
    const value = parseInt(normalized, 16);
    const red = (value >> 16) & 255;
    const green = (value >> 8) & 255;
    const blue = value & 255;
    return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }
})();
