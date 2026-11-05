(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const dom = {
    homeView: $("homeView"),
    demoView: $("demoView"),
    startDemo: $("startDemo"),
    backHome: $("backHome"),
    homePreview: $("homePreview"),
    homeAnalysisSeconds: $("homeAnalysisSeconds"),
    homeFrameCount: $("homeFrameCount"),
    homePointCount: $("homePointCount"),
    homeTrackCount: $("homeTrackCount"),
    homeImplemented: $("homeImplemented"),
    homePending: $("homePending"),
    homeDisabled: $("homeDisabled"),
    homeRunId: $("homeRunId"),
    demoRunId: $("demoRunId"),
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
    originalBadge: $("originalBadge"),
    replayFrame: $("replayFrame"),
    replayVideo: $("replayVideo"),
    replayOverlay: $("replayOverlay"),
    replayError: $("replayError"),
    replayBadge: $("replayBadge"),
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
    trackSelect: $("trackSelect"),
    prevTrack: $("prevTrack"),
    nextTrack: $("nextTrack"),
    quickPicks: $("quickPicks"),
    trackDetail: $("trackDetail"),
    trackCountPill: $("trackCountPill"),
    allTrackCount: $("allTrackCount"),
    trackFilter: $("trackFilter"),
    allTracksBody: $("allTracksBody"),
    tableEmpty: $("tableEmpty"),
    statAnalysisSeconds: $("statAnalysisSeconds"),
    statOriginalSeconds: $("statOriginalSeconds"),
    statFrames: $("statFrames"),
    statPoints: $("statPoints"),
    statTracks: $("statTracks"),
    summaryText: $("summaryText"),
    capImplemented: $("capImplemented"),
    capPending: $("capPending"),
    capDisabled: $("capDisabled"),
    provenanceBody: $("provenanceBody"),
    provenanceNote: $("provenanceNote"),
    toast: $("toast"),
    fatalError: $("fatalError"),
    fatalTitle: $("fatalTitle"),
    fatalMessage: $("fatalMessage"),
    fatalDetail: $("fatalDetail"),
    fatalRetry: $("fatalRetry"),
  };

  const state = {
    data: null,
    tracks: [],
    byId: new Map(),
    analysisEnd: 12,
    originalDuration: 13.64,
    time: 0,
    selected: null,
    playing: false,
    clipEnd: null,
    assetsOk: false,
    missing: new Map(),
    dirty: true,
    scrubbing: false,
    syncOk: true,
    toastTimer: null,
    replayDurationMismatch: false,
  };

  const fmtInt = new Intl.NumberFormat("zh-CN");
  const FONT_STACK = '"Noto Sans SC", "Source Han Sans SC", "Microsoft YaHei", sans-serif';
  const PATHS = {
    original: "#f59e0b",
    current: "#fbbf24",
    start: "#22c55e",
    replay: "#2dd4bf",
  };

  let toastHideTimer = null;
  let lastLiveText = "";

  boot();

  async function boot() {
    bindEvents();
    dom.startDemo.disabled = true;
    dom.trackSelect.disabled = true;
    dom.playPause.disabled = true;

    try {
      const response = await fetch("/data/demo.json", { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`读取演示数据失败：HTTP ${response.status}`);
      }
      state.data = await response.json();
    } catch (error) {
      showFatal(error);
      return;
    }

    try {
      applyData();
      setupVideos();
      selectTrack(state.data.default_track_id, { seek: false, silent: true });
      seek(0, { fromUser: false, silent: true });
      showHome();
      checkAssets();
    } catch (error) {
      showFatal(error);
    }
  }

  function applyData() {
    const data = state.data;
    state.tracks = data.tracks.slice().sort((a, b) => a.id - b.id);
    state.byId = new Map(state.tracks.map((track) => [track.id, track]));
    state.analysisEnd = Number(data.sample.analysis_end) || 12;
    state.originalDuration = Number(data.sample.original_duration) || state.analysisEnd;

    document.title = `${data.project.name} · 公开样例演示`;
    dom.homePreview.src = data.files.preview_image;
    dom.homePreview.alt = `${data.sample.display_name}的分析回放预览`;
    dom.homeAnalysisSeconds.textContent = fmt(data.sample.analysis_end, 1);
    dom.homeFrameCount.textContent = fmtInt.format(data.sample.processed_frames);
    dom.homePointCount.textContent = fmtInt.format(data.sample.track_point_count);
    dom.homeTrackCount.textContent = fmtInt.format(data.sample.track_count);
    dom.homeRunId.textContent = data.run.run_id;
    dom.demoRunId.textContent = data.run.run_id;
    dom.trackCountPill.textContent = fmtInt.format(data.sample.track_count);
    dom.allTrackCount.textContent = fmtInt.format(data.sample.track_count);

    dom.statAnalysisSeconds.textContent = fmt(data.sample.analysis_end, 1);
    dom.statOriginalSeconds.textContent = fmt(data.sample.original_duration, 2);
    dom.statFrames.textContent = fmtInt.format(data.sample.processed_frames);
    dom.statPoints.textContent = fmtInt.format(data.sample.track_point_count);
    dom.statTracks.textContent = fmtInt.format(data.sample.track_count);
    dom.durationTime.textContent = fmt(state.analysisEnd);

    const sample = data.sample;
    dom.summaryText.innerHTML =
      `本次离线分析了前 <strong>${fmt(sample.analysis_end)} 秒</strong>、` +
      `<strong>${fmtInt.format(sample.processed_frames)} 帧</strong>画面，记录 ` +
      `<strong>${fmtInt.format(sample.track_point_count)} 个轨迹点</strong>，分配给 ` +
      `<strong>${fmtInt.format(sample.track_count)} 个累计轨迹编号</strong>。` +
      `平均每帧约 ${sample.average_tracked_boxes} 个跟踪框（算法框数量，不是人数），最高 ${sample.max_tracked_boxes} 个。` +
      `轨迹编号只代表本次运行中的连续观察，不是身份识别。事件规则和视频理解模型尚未接入，因此没有异常判断或自动解释。` +
      `原视频全长 ${fmt(sample.original_duration)} 秒，本次分析覆盖前 ${fmt(sample.analysis_end)} 秒，` +
      `剩余 ${fmt(sample.tail_seconds)} 秒未参与分析。`;

    renderCapabilities();
    renderProvenance();
    buildTrackSelect();
    buildQuickPicks();
    buildTrackTable();
    updateTimelineScale();

    dom.trackSelect.disabled = false;
    dom.startDemo.disabled = false;
  }

  function renderCapabilities() {
    const caps = state.data.capabilities;
    fillList(dom.homeImplemented, caps.implemented);
    fillList(dom.homePending, caps.pending);
    fillDisabledList(dom.homeDisabled, caps.disabled);
    fillList(dom.capImplemented, caps.implemented);
    fillList(dom.capPending, caps.pending);
    fillDisabledList(dom.capDisabled, caps.disabled);
  }

  function fillList(element, items) {
    element.innerHTML = items
      .map((item) => `<li>${escapeHtml(String(item))}</li>`)
      .join("");
  }

  function fillDisabledList(element, items) {
    element.innerHTML = items
      .map(
        (item) =>
          `<li><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.text)}</span></li>`
      )
      .join("");
  }

  function renderProvenance() {
    const data = state.data;
    const run = data.run;
    const provenance = data.provenance;
    const tracksHash = provenance.files?.["tracks.jsonl"]?.sha256 || "";
    const replayHash = provenance.files?.["replay.mp4"]?.sha256 || "";
    const rows = [
      ["运行编号", `<code>${escapeHtml(run.run_id)}</code>`],
      ["实验", "E00-A 感知复现 / E00-D 演示"],
      ["样例", `公开开发样例 · ${escapeHtml(data.sample.split)}`],
      ["来源", `<span class="prov-url">${escapeHtml(data.sample.source_url)}</span>`],
      ["使用范围", "本地流程复现；再分发许可未核定"],
      ["检测", escapeHtml(data.components.detector)],
      ["跟踪", escapeHtml(data.components.tracker)],
      ["时间口径", escapeHtml(provenance.timestamp_basis)],
      ["结果目录", `<code>${escapeHtml(run.result_dir)}</code>`],
      [
        "文件校验",
        `tracks.jsonl <code>${escapeHtml(shortHash(tracksHash))}</code><br>` +
          `replay.mp4 <code>${escapeHtml(shortHash(replayHash))}</code>`,
      ],
      [
        "离线耗时",
        `${fmt(run.wall_seconds, 2)} 秒 / ${fmt(run.processing_fps, 2)} FPS（仅离线短片）`,
      ],
      ["生成时间", escapeHtml(data.generated_at_utc)],
    ];
    dom.provenanceBody.innerHTML = rows
      .map(
        ([label, value]) =>
          `<tr><th scope="row">${escapeHtml(label)}</th><td>${value}</td></tr>`
      )
      .join("");
    dom.provenanceNote.textContent = provenance.notes.join(" ");
  }

  function buildTrackSelect() {
    dom.trackSelect.innerHTML = state.tracks
      .map(
        (track) =>
          `<option value="${track.id}">目标 ${pad3(track.id)} · ` +
          `${fmt(track.start)}–${fmt(track.last)} 秒 · ${fmtInt.format(track.point_count)} 点</option>`
      )
      .join("");
    dom.trackSelect.value = String(state.data.default_track_id);
  }

  function buildQuickPicks() {
    dom.quickPicks.innerHTML = state.data.quick_picks
      .map(
        (pick) =>
          `<button class="quick-pick" type="button" data-track-id="${pick.track_id}">` +
          `<span>目标 ${pad3(pick.track_id)} · ${escapeHtml(pick.label)}</span>` +
          `<small>${escapeHtml(pick.hint)}</small></button>`
      )
      .join("");
    updateQuickPickActive(state.data.default_track_id);
  }

  function buildTrackTable() {
    dom.allTracksBody.innerHTML = state.tracks
      .map(
        (track) =>
          `<tr data-track-id="${track.id}" tabindex="0">` +
          `<td>${pad3(track.id)}</td>` +
          `<td>${fmt(track.start)} 秒</td>` +
          `<td>${fmt(track.last)} 秒</td>` +
          `<td>${fmtInt.format(track.point_count)}</td></tr>`
      )
      .join("");
    updateTableSelected(state.data.default_track_id);
  }

  function updateTimelineScale() {
    const tail = Math.max(0, state.originalDuration - state.analysisEnd);
    const ratio =
      (state.analysisEnd / Math.max(state.originalDuration, state.analysisEnd, 0.001)) * 100;
    dom.timelineGrid.style.setProperty("--analysis-ratio", `${ratio.toFixed(2)}%`);
    dom.timelineGrid.style.gridTemplateColumns =
      tail > 0.05 ? "var(--analysis-ratio, 87.98%) minmax(46px, 1fr)" : "1fr";
    dom.timeline.max = String(state.analysisEnd);
    dom.durationTime.textContent = fmt(state.analysisEnd);
    dom.statOriginalSeconds.textContent = fmt(state.originalDuration, 2);
    dom.statAnalysisSeconds.textContent = fmt(state.analysisEnd, 1);
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
    updateTimeUi(state.time);
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
        updateTimelineScale();
      }
      applyTimeToVideos(state.time);
      state.dirty = true;
    });

    replay.addEventListener("loadedmetadata", () => {
      if (Number.isFinite(replay.duration) && replay.duration > 0) {
        if (Math.abs(replay.duration - state.analysisEnd) > 0.2) {
          state.replayDurationMismatch = true;
        }
        if (replay.duration + 0.05 < state.analysisEnd) {
          state.analysisEnd = replay.duration;
          updateTimelineScale();
        }
      }
      applyTimeToVideos(state.time);
      state.dirty = true;
    });

    original.addEventListener("play", () => {
      state.playing = true;
      updatePlayUi();
      updateSyncBadge();
      if (replay.paused) {
        replay.play().catch(() => {});
      }
    });
    original.addEventListener("pause", () => {
      state.playing = false;
      if (!replay.paused) replay.pause();
      updatePlayUi();
      state.syncOk = true;
      updateSyncBadge();
      state.dirty = true;
      updateSelectedLive();
    });
    original.addEventListener("ended", () => {
      state.playing = false;
      replay.pause();
      updatePlayUi();
    });
    original.addEventListener("seeked", () => {
      if (!state.scrubbing && state.playing) {
        updateTimeUi(original.currentTime);
      }
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
    if (dom.originalVideo.readyState >= 1) {
      dom.originalVideo.currentTime = mediaTime;
    }
    if (dom.replayVideo.readyState >= 1) {
      dom.replayVideo.currentTime = mediaTime;
    }
  }

  function clampMediaTime(time) {
    if (state.analysisEnd <= 0) return 0;
    const safeEnd = Math.max(0, state.analysisEnd - 0.04);
    return Math.max(0, Math.min(time, safeEnd));
  }

  async function play(options = {}) {
    if (!state.assetsOk) return;
    if (!options.keepClip) state.clipEnd = null;
    if (dom.originalVideo.readyState < 1 || dom.replayVideo.readyState < 1) {
      toast("视频还在加载，请稍候再播放。");
      return;
    }
    if (state.time >= state.analysisEnd - 0.03) {
      seek(0, { fromUser: false });
    }
    try {
      const results = await Promise.allSettled([
        dom.originalVideo.play(),
        dom.replayVideo.play(),
      ]);
      if (results[0].status === "rejected") {
        toast("浏览器未允许播放，请再点一次“播放”。");
      }
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
    if (state.playing || !dom.originalVideo.paused) {
      pause();
    } else {
      play();
    }
  }

  function seek(time, options = {}) {
    if (!state.assetsOk && options.fromUser) return;
    if (!options.keepClip) state.clipEnd = null;
    const next = Math.max(0, Math.min(state.analysisEnd, Number(time) || 0));
    state.time = next;
    applyTimeToVideos(next);
    updateTimeUi(next);
    updateSelectedLive();
    state.dirty = true;
    if (state.playing && next >= state.analysisEnd - 0.02) {
      pause();
    }
    if (options.fromUser && next >= state.analysisEnd - 0.005) {
      toast(`已到本次分析范围末尾（${fmt(state.analysisEnd)} 秒）。`);
    }
    drawOverlays();
  }

  function updateTimeUi(time) {
    state.time = time;
    dom.currentTime.textContent = fmt(time);
    if (!state.scrubbing) {
      dom.timeline.value = String(time);
    }
    const pct = state.analysisEnd > 0 ? (time / state.analysisEnd) * 100 : 0;
    dom.timeline.style.setProperty("--pct", `${Math.max(0, Math.min(100, pct))}%`);
    dom.timeline.setAttribute(
      "aria-valuetext",
      `${fmt(time)} 秒，共 ${fmt(state.analysisEnd)} 秒`
    );
    const atEnd = time >= state.analysisEnd - 0.02;
    if (!state.playing && atEnd) {
      dom.playLabel.textContent = "重播";
    } else if (!state.playing) {
      dom.playLabel.textContent = "播放";
    }
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
    if (state.data && !dom.demoView.hidden) {
      const original = dom.originalVideo;
      if (!original.paused) {
        const time = original.currentTime;
        if (state.clipEnd && time >= state.clipEnd - 0.02) {
          pause();
          seek(state.clipEnd, { fromUser: false });
          toast(`目标 ${pad3(state.selected?.id ?? 0)} 的片段播放结束。`);
        } else if (time >= state.analysisEnd - 0.02) {
          pause();
          seek(state.analysisEnd, { fromUser: false });
        } else {
          updateTimeUi(time);
          updateSelectedLive();
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

  function selectTrack(trackId, options = {}) {
    const track = state.byId.get(Number(trackId));
    if (!track) return;
    state.selected = track;
    state.clipEnd = null;
    dom.trackSelect.value = String(track.id);
    renderTrackDetail(track);
    updateQuickPickActive(track.id);
    updateTableSelected(track.id);
    updateSelectionBadges();
    updateTrackNav();
    if (options.seek !== false) {
      pause();
      seek(track.start, { fromUser: false });
    }
    updateSelectedLive();
    state.dirty = true;
    drawOverlays();
  }

  function renderTrackDetail(track) {
    dom.trackDetail.innerHTML = `
      <div class="selected-top">
        <div>
          <p class="selected-kicker">当前选择 · 算法轨迹</p>
          <p class="selected-id"><small>目标</small>${pad3(track.id)}</p>
        </div>
        <span class="selected-type">局部轨迹 ID</span>
      </div>
      <div class="selected-times">
        <button class="time-chip" type="button" data-seek="start" title="定位到首次出现时刻">
          <span>首次出现</span><b>${fmt(track.start)} 秒</b>
        </button>
        <button class="time-chip" type="button" data-seek="last" title="定位到最后观察时刻">
          <span>最后观察到</span><b>${fmt(track.last)} 秒</b>
        </button>
      </div>
      <div class="selected-meta">
        <span>轨迹点 <b>${fmtInt.format(track.point_count)}</b></span>
        <span>观察跨度 <b>${fmt(track.duration)} 秒</b></span>
        <span>帧 <b>${track.start_frame}–${track.last_frame}</b></span>
      </div>
      <div class="selected-actions">
        <button class="btn btn-primary" type="button" data-action="play-clip">播放该目标片段</button>
        <button class="btn btn-secondary" type="button" data-action="seek-start">回到首次出现</button>
      </div>
      <p class="selected-live" id="selectedLive">正在读取当前时刻…</p>
      <p class="selected-note">“最后观察到”只表示视频中最后一次记录，不代表目标离场；编号不是身份。</p>
    `;
    lastLiveText = "";
    updateSelectedLive();
  }

  function updateSelectedLive() {
    const live = $("selectedLive");
    if (!live) return;
    const track = state.selected;
    if (!track) {
      if (lastLiveText !== "尚未选择目标。") {
        live.textContent = "尚未选择目标。";
        lastLiveText = "尚未选择目标。";
      }
      return;
    }
    const time = state.time;
    let text;
    if (time < track.start - 0.02) {
      text = `当前 ${fmt(time)} 秒：该目标尚未出现（首次 ${fmt(track.start)} 秒）。`;
    } else if (time > track.last + 0.02) {
      text = `当前 ${fmt(time)} 秒：已超过最后观察时间（${fmt(track.last)} 秒）。`;
    } else {
      text = `当前 ${fmt(time)} 秒：目标在画面中，编号 ${pad3(track.id)} 的框已高亮。`;
    }
    if (text !== lastLiveText) {
      live.textContent = text;
      lastLiveText = text;
    }
  }

  function updateTrackNav() {
    if (!state.selected) {
      dom.prevTrack.disabled = true;
      dom.nextTrack.disabled = true;
      return;
    }
    const index = state.tracks.findIndex((track) => track.id === state.selected.id);
    dom.prevTrack.disabled = index <= 0;
    dom.nextTrack.disabled = index < 0 || index >= state.tracks.length - 1;
  }

  function updateQuickPickActive(trackId) {
    dom.quickPicks.querySelectorAll("[data-track-id]").forEach((button) => {
      button.classList.toggle("is-active", Number(button.dataset.trackId) === Number(trackId));
    });
  }

  function updateTableSelected(trackId) {
    dom.allTracksBody.querySelectorAll("tr[data-track-id]").forEach((row) => {
      row.classList.toggle("is-selected", Number(row.dataset.trackId) === Number(trackId));
    });
  }

  function updateSelectionBadges() {
    const track = state.selected;
    [dom.originalBadge, dom.replayBadge].forEach((badge) => {
      badge.hidden = !track;
      if (track) {
        badge.querySelector("b").textContent = pad3(track.id);
      }
    });
  }

  function playSelectedClip() {
    const track = state.selected;
    if (!track || !state.assetsOk) return;
    if (track.point_count < 2 || track.duration < 0.08) {
      pause();
      seek(track.start, { fromUser: false });
      toast(`目标 ${pad3(track.id)} 只被记录到很短时间，已定位到 ${fmt(track.start)} 秒。`);
      return;
    }
    pause();
    seek(track.start, { fromUser: false });
    state.clipEnd = Math.min(track.last + 0.45, state.analysisEnd);
    play({ keepClip: true });
    toast(`正在播放目标 ${pad3(track.id)}：${fmt(track.start)}–${fmt(track.last)} 秒。`);
  }

  function seekToEvidence(kind) {
    const track = state.selected;
    if (!track) return;
    const target = kind === "last" ? track.last : track.start;
    pause();
    seek(target, { fromUser: false });
    toast(
      `已定位到目标 ${pad3(track.id)} 的${kind === "last" ? "最后观察" : "首次出现"}时刻：${fmt(target)} 秒。`
    );
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

  function drawOverlays() {
    const originalCtx = dom.originalOverlay.getContext("2d");
    const replayCtx = dom.replayOverlay.getContext("2d");
    const width = dom.originalOverlay.width;
    const height = dom.originalOverlay.height;
    originalCtx.clearRect(0, 0, width, height);
    replayCtx.clearRect(0, 0, width, height);

    if (!state.assetsOk) return;
    const track = state.selected;
    if (!track || !track.points?.length) return;

    const points = track.points;
    const index = findPointIndex(points, state.time);
    const current = index >= 0 ? points[index] : points[0];
    const lineWidth = Math.max(4, Math.round(height * 0.0065));
    const markerRadius = Math.max(7, Math.round(height * 0.014));

    // Full path, faint.
    drawPath(originalCtx, points, points.length - 1, hexToRgba(PATHS.original, 0.26), lineWidth, false);
    // Already traversed path, bright.
    if (index >= 0) {
      drawPath(originalCtx, points, index, PATHS.original, lineWidth + 1, true);
    }

    const first = points[0];
    const last = points[points.length - 1];
    drawMarker(originalCtx, pointX(first), pointY(first), markerRadius * 0.8, PATHS.start, false);
    drawMarker(originalCtx, pointX(last), pointY(last), markerRadius * 0.72, "#94a3b8", false);

    let label = "首次出现";
    let markerColor = PATHS.start;
    let showBox = index >= 0;
    if (index >= 0) {
      label = state.time > track.last + 0.02 ? "最后观察" : "当前";
      markerColor = state.time > track.last + 0.02 ? "#94a3b8" : PATHS.current;
    }
    drawMarker(originalCtx, pointX(current), pointY(current), markerRadius, markerColor, true);
    if (showBox) {
      drawBox(originalCtx, current, markerColor, "", false);
    }
    drawTag(originalCtx, pointX(current), pointY(current), label, markerColor, "#fff");

    // On the replay, do not repaint the path; emphasize the selected box.
    if (index >= 0) {
      drawBox(replayCtx, current, PATHS.replay, "", true);
      drawMarker(replayCtx, pointX(current), pointY(current), markerRadius * 0.95, PATHS.replay, true);
    }
  }

  function findPointIndex(points, time) {
    let low = 0;
    let high = points.length - 1;
    let answer = -1;
    while (low <= high) {
      const mid = (low + high) >> 1;
      if (points[mid][0] <= time + 0.0001) {
        answer = mid;
        low = mid + 1;
      } else {
        high = mid - 1;
      }
    }
    return answer;
  }

  function drawPath(ctx, points, endIndex, color, lineWidth, glow) {
    if (endIndex < 1) return;
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(pointX(points[0]), pointY(points[0]));
    for (let i = 1; i <= endIndex; i += 1) {
      ctx.lineTo(pointX(points[i]), pointY(points[i]));
    }
    ctx.strokeStyle = color;
    ctx.lineWidth = lineWidth;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    if (glow) {
      ctx.shadowColor = hexToRgba(color, 0.55);
      ctx.shadowBlur = Math.round(lineWidth * 2.2);
    }
    ctx.stroke();
    ctx.restore();
  }

  function drawMarker(ctx, x, y, radius, color, halo) {
    ctx.save();
    if (halo) {
      ctx.beginPath();
      ctx.arc(x, y, radius * 1.9, 0, Math.PI * 2);
      ctx.fillStyle = hexToRgba(color, 0.22);
      ctx.fill();
    }
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.lineWidth = Math.max(2, radius * 0.22);
    ctx.strokeStyle = "#ffffff";
    ctx.stroke();
    ctx.restore();
  }

  function drawBox(ctx, point, color, label, glow) {
    const [x1, y1, x2, y2] = [point[1], point[2], point[3], point[4]];
    const radius = Math.max(5, Math.round((y2 - y1) * 0.04));
    ctx.save();
    ctx.beginPath();
    roundedRect(ctx, x1, y1, x2 - x1, y2 - y1, radius);
    ctx.strokeStyle = color;
    ctx.lineWidth = Math.max(3, Math.round(dom.originalOverlay.height * 0.0045));
    if (glow) {
      ctx.shadowColor = hexToRgba(color, 0.72);
      ctx.shadowBlur = 18;
    }
    ctx.stroke();
    ctx.restore();
    if (label) {
      drawTag(ctx, x1, y1, label, color, "#062a27");
    }
  }

  function drawTag(ctx, x, y, text, background, foreground) {
    if (!text) return;
    const height = dom.originalOverlay.height;
    const fontSize = Math.max(18, Math.round(height * 0.024));
    ctx.save();
    ctx.font = `700 ${fontSize}px ${FONT_STACK}`;
    const textWidth = ctx.measureText(text).width;
    const padX = Math.round(fontSize * 0.52);
    const padY = Math.round(fontSize * 0.3);
    const boxWidth = textWidth + padX * 2;
    const boxHeight = fontSize + padY * 2;
    let left = clamp(x - boxWidth / 2, 8, dom.originalOverlay.width - boxWidth - 8);
    let top = y - boxHeight - Math.round(fontSize * 0.55);
    if (top < 64) top = y + Math.round(fontSize * 0.55);
    ctx.beginPath();
    roundedRect(ctx, left, top, boxWidth, boxHeight, Math.round(fontSize * 0.35));
    ctx.fillStyle = background;
    ctx.shadowColor = "rgba(0,0,0,.28)";
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

  function pointX(point) {
    return (point[1] + point[3]) / 2;
  }

  function pointY(point) {
    return point[4];
  }

  async function checkAssets() {
    const checks = [
      ["original_video", "原视频"],
      ["analysis_video", "分析回放"],
      ["preview_image", "预览图"],
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
    const videoMissing = missing.some((item) => item.key !== "preview_image");
    state.assetsOk = !videoMissing;
    setPlaybackEnabled(state.assetsOk);
    if (state.assetsOk) {
      updateSyncBadge();
    }
  }

  function addMissing(item) {
    if (!item) return;
    state.missing.set(item.key, item);
    renderMissing();
    if (item.key !== "preview_image") {
      const isReplay = item.key === "analysis_video";
      const frame = isReplay ? dom.replayFrame : dom.originalFrame;
      const errorBox = isReplay ? dom.replayError : dom.originalError;
      frame.classList.add("is-error");
      errorBox.hidden = false;
      state.assetsOk = false;
      setPlaybackEnabled(false);
      state.dirty = true;
      drawOverlays();
    }
  }

  function renderMissing() {
    const items = [...state.missing.values()];
    dom.missingBanner.hidden = items.length === 0;
    if (!items.length) return;
    const videoMissing = items.some((item) => item.key !== "preview_image");
    dom.missingTitle.textContent = videoMissing
      ? "演示文件缺失，暂时无法播放"
      : "部分辅助文件缺失";
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
    const frame = isReplay ? dom.replayFrame : dom.originalFrame;
    const errorBox = isReplay ? dom.replayError : dom.originalError;
    frame.classList.add("is-error");
    errorBox.hidden = false;
    addMissing({
      key,
      label: isReplay ? "分析回放" : "原视频",
      url: isReplay ? state.data.files.analysis_video : state.data.files.original_video,
      path: isReplay ? state.data.expected_paths.analysis_video : state.data.expected_paths.original_video,
    });
  }

  function setPlaybackEnabled(enabled) {
    state.assetsOk = enabled;
    [dom.playPause, dom.back1, dom.forward1, dom.replayBtn, dom.fullscreenBtn, dom.timeline].forEach(
      (element) => {
        element.disabled = !enabled;
      }
    );
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
    dom.startDemo.disabled = true;
    dom.fatalError.hidden = false;
    dom.fatalTitle.textContent = "演示数据未加载";
    dom.fatalMessage.textContent =
      "无法读取演示数据。请通过本地演示服务打开页面，并确认运行结果完整。";
    dom.fatalDetail.textContent =
      `${location.origin}${location.pathname}\n` +
      `${error?.message || String(error)}\n\n` +
      "启动方式：在 st_memory_experiments 目录执行 python3 demo/serve.py\n" +
      "访问地址：http://127.0.0.1:8765/";
  }

  function bindEvents() {
    dom.startDemo.addEventListener("click", startDemo);
    dom.backHome.addEventListener("click", () => {
      pause();
      showHome();
    });
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

    dom.trackSelect.addEventListener("change", () => {
      selectTrack(Number(dom.trackSelect.value));
    });
    dom.prevTrack.addEventListener("click", () => stepTrack(-1));
    dom.nextTrack.addEventListener("click", () => stepTrack(1));
    dom.quickPicks.addEventListener("click", (event) => {
      const button = event.target.closest("[data-track-id]");
      if (button) selectTrack(Number(button.dataset.trackId));
    });

    dom.trackDetail.addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (!button) return;
      if (button.dataset.seek === "start" || button.dataset.action === "seek-start") {
        seekToEvidence("start");
      } else if (button.dataset.seek === "last") {
        seekToEvidence("last");
      } else if (button.dataset.action === "play-clip") {
        playSelectedClip();
      }
    });

    dom.allTracksBody.addEventListener("click", (event) => {
      const row = event.target.closest("tr[data-track-id]");
      if (row) selectFromTable(Number(row.dataset.trackId));
    });
    dom.allTracksBody.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const row = event.target.closest("tr[data-track-id]");
      if (row) {
        event.preventDefault();
        selectFromTable(Number(row.dataset.trackId));
      }
    });
    dom.trackFilter.addEventListener("input", filterTrackTable);
    dom.retryAssets.addEventListener("click", () => location.reload());
    dom.fatalRetry.addEventListener("click", () => location.reload());
    document.addEventListener("fullscreenchange", updateFullscreenUi);
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && document.fullscreenElement) {
        document.exitFullscreen().catch(() => {});
      }
    });
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && state.playing) {
        state.dirty = true;
        if (dom.replayVideo.readyState >= 2) {
          dom.replayVideo.currentTime = dom.originalVideo.currentTime;
        }
      }
    });
    window.addEventListener("beforeunload", () => {
      dom.originalVideo.pause();
      dom.replayVideo.pause();
    });
  }

  function startDemo() {
    if (!state.data) return;
    pause();
    state.clipEnd = null;
    showView("demo");
    selectTrack(state.data.default_track_id, { seek: false, silent: true });
    seek(0, { fromUser: false, silent: true });
    updatePlayUi();
    state.dirty = true;
    requestAnimationFrame(drawOverlays);
    if (!state.assetsOk) return;
    toast(
      state.replayDurationMismatch
        ? "提示：分析回放时长与运行记录不一致，请检查派生文件。"
        : `已预选目标 ${pad3(state.data.default_track_id)}。点击“播放”开始，或先选择其他编号。`,
      3600
    );
  }

  function showHome() {
    dom.demoView.hidden = true;
    dom.homeView.hidden = false;
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  function showView(name) {
    const demo = name === "demo";
    dom.homeView.hidden = demo;
    dom.demoView.hidden = !demo;
    window.scrollTo({ top: 0, behavior: "auto" });
    if (demo) {
      state.dirty = true;
      requestAnimationFrame(drawOverlays);
    }
  }

  function stepTrack(direction) {
    if (!state.selected) return;
    const index = state.tracks.findIndex((track) => track.id === state.selected.id);
    const next = state.tracks[index + direction];
    if (next) selectTrack(next.id);
  }

  function selectFromTable(trackId) {
    selectTrack(trackId);
    const details = document.querySelector(".all-tracks");
    if (details) details.open = false;
    const panel = document.querySelector(".track-panel");
    if (panel) panel.scrollTo({ top: 0, behavior: "smooth" });
  }

  function filterTrackTable() {
    const query = dom.trackFilter.value.trim();
    let visible = 0;
    dom.allTracksBody.querySelectorAll("tr[data-track-id]").forEach((row) => {
      const show = !query || row.dataset.trackId.includes(query);
      row.hidden = !show;
      if (show) visible += 1;
    });
    dom.tableEmpty.hidden = visible > 0;
  }

  function onKeyDown(event) {
    if (dom.demoView.hidden) return;
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
    }
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
    return value ? `${value.slice(0, 12)}…` : "—";
  }

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function hexToRgba(hex, alpha) {
    const normalized = hex.replace("#", "");
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
