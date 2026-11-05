/* Read-only presentation of completed results. No inference or event injection. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const video = $('video');
  const canvas = $('overlay');
  const ctx = canvas.getContext('2d');
  const colors = ['#58c8c1', '#f3bf62', '#9ebcff'];
  const state = {rules: null, tracks: null, selected: null, ready: false, clipEnd: null, animation: null};
  const fmt = value => Number(value).toFixed(2);
  const trackLabel = id => String(id).padStart(3, '0');

  function failure(message) {
    $('error').hidden = false;
    $('error').textContent = message;
  }
  function disableMedia(message) {
    state.ready = false;
    video.pause();
    ['play', 'restart', 'seek', 'evidencePlay'].forEach(id => { $(id).disabled = true; });
    $('loading').hidden = false;
    $('loading').textContent = message;
    failure(message);
  }
  async function readJSON(path) {
    const response = await fetch(path, {cache: 'no-store'});
    if (!response.ok) throw new Error(`${path}：HTTP ${response.status}`);
    return response.json();
  }
  function fact(label, value) {
    const div = document.createElement('div');
    const small = document.createElement('small'); small.textContent = label;
    const b = document.createElement('b'); b.textContent = value;
    div.append(small, b);
    return div;
  }
  function currentFrame(time) {
    return Math.min(state.rules.sample.processed_frames - 1,
      Math.max(0, Math.floor((time + 0.00001) * state.rules.sample.fps)));
  }
  function render(time = video.currentTime) {
    if (!state.rules) return;
    const rules = state.rules;
    const end = rules.sample.analysis_end;
    const t = Math.max(0, Math.min(end, time));
    const frame = currentFrame(t);
    $('clock').textContent = `${fmt(t)} / ${fmt(end)} 秒`;
    $('seek').value = t;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    rules.zones.forEach((zone, i) => {
      ctx.beginPath();
      zone.polygon.forEach(([x,y], j) => j ? ctx.lineTo(x,y) : ctx.moveTo(x,y));
      ctx.closePath(); ctx.fillStyle = `${colors[i]}16`; ctx.fill();
      ctx.strokeStyle = colors[i]; ctx.lineWidth = 4; ctx.stroke();
      const [x,y] = zone.polygon[0];
      ctx.font = 'bold 25px sans-serif'; ctx.fillStyle = '#102b3be8';
      ctx.fillRect(x, y, 228, 43); ctx.fillStyle = colors[i];
      ctx.fillText(`观察区 ${String.fromCharCode(65+i)} · ${zone.counts[frame]}`, x+10, y+30);
      $(`zone-${i}`).textContent = zone.counts[frame];
    });
    const selected = state.selected;
    if (selected) {
      const track = state.tracks.tracks.find(tr => tr.id === selected.track_id);
      const points = track ? track.points : [];
      const past = points.filter(p => p[0] <= t + 0.00001);
      const recent = past.slice(-100);
      ctx.beginPath();
      recent.forEach((p, i) => {
        const x = (p[1]+p[3])/2, y = p[4];
        if (!i || p[0] - recent[i-1][0] > 1.5/rules.sample.fps) ctx.moveTo(x,y);
        else ctx.lineTo(x,y);
      });
      ctx.lineWidth = 5; ctx.strokeStyle = '#68ffff'; ctx.stroke();
      const point = past[past.length - 1];
      // Never hold a box across a missing observation or past the analyzed frame.
      if (point && Math.abs(point[0] - frame/rules.sample.fps) < 0.5/rules.sample.fps) {
        ctx.strokeStyle = '#68ffff'; ctx.lineWidth = 6;
        ctx.strokeRect(point[1],point[2],point[3]-point[1],point[4]-point[2]);
        ctx.fillStyle = '#102b3b'; ctx.fillRect(point[1],point[2]-43,165,40);
        ctx.fillStyle = '#68ffff'; ctx.font = 'bold 25px sans-serif';
        ctx.fillText(`目标 ${trackLabel(selected.track_id)}`,point[1]+6,point[2]-14);
      }
    }
  }
  function syncControls() {
    $('play').textContent = video.paused ? (video.currentTime >= (state.rules?.sample.analysis_end || 12) - 0.01 ? '重播分析回放' : '播放分析回放') : '暂停';
  }
  function setTime(time) {
    if (!state.ready) return;
    video.pause(); state.clipEnd = null;
    video.currentTime = Math.max(0, Math.min(state.rules.sample.analysis_end, time));
    render(video.currentTime);
  }
  async function playVideo() {
    if (!state.ready) return;
    try { await video.play(); }
    catch (error) { failure(`播放未能开始：${error.message}。请再次点击播放。`); }
  }
  function selectCandidate(candidate, seek = true) {
    state.selected = candidate;
    document.querySelectorAll('.candidate').forEach(button => {
      const active = button.dataset.id === candidate.candidate_id;
      button.classList.toggle('selected', active);
      button.setAttribute('aria-pressed', String(active));
    });
    $('evidenceTitle').textContent = `目标 ${trackLabel(candidate.track_id)} · ${candidate.zone_name}`;
    const features = candidate.trigger_features;
    $('evidenceFacts').replaceChildren(
      fact('连续可观测停留', `${fmt(features.dwell_observed_seconds)} 秒`),
      fact('演示阈值', `${fmt(features.threshold_seconds)} 秒`),
      fact('条件满足时刻', `${fmt(candidate.condition_met_video_time)} 秒`),
      fact('证据帧时刻', `${fmt(candidate.evidence_frame_video_time)} 秒`),
      fact('触发前有效观测', `${features.observed_points} 帧`)
    );
    $('evidenceNote').textContent = '证据帧标记帧起点，条件满足时刻取该帧覆盖结束。10 秒为演示阈值，候选待人工复核，尚未校准为现场告警。';
    if (seek) setTime(candidate.evidence_frame_video_time);
    else render();
  }
  function renderCandidates() {
    const {rules} = state;
    $('candidateList').replaceChildren(); $('markers').replaceChildren();
    rules.candidates.forEach(candidate => {
      const button = document.createElement('button'); button.className = 'candidate';
      button.type = 'button'; button.dataset.id = candidate.candidate_id;
      const copy = document.createElement('span');
      const title = document.createElement('b'); title.textContent = `目标 ${trackLabel(candidate.track_id)}`;
      const subtitle = document.createElement('small'); subtitle.textContent = candidate.zone_name;
      const time = document.createElement('time'); time.textContent = `${fmt(candidate.condition_met_video_time)} 秒 →`;
      copy.append(title,subtitle); button.append(copy,time);
      button.addEventListener('click', () => selectCandidate(candidate));
      $('candidateList').append(button);
      const marker = document.createElement('button'); marker.type = 'button';
      marker.style.left = `${100*candidate.condition_met_video_time/rules.sample.analysis_end}%`;
      marker.title = `目标 ${trackLabel(candidate.track_id)}，条件满足 ${fmt(candidate.condition_met_video_time)} 秒，定位证据帧 ${fmt(candidate.evidence_frame_video_time)} 秒`;
      marker.setAttribute('aria-label',marker.title);
      marker.addEventListener('click', () => selectCandidate(candidate));
      $('markers').append(marker);
    });
    if (!rules.candidates.length) $('candidateList').textContent = '本次分析没有规则候选。';
  }
  function renderBenchmarks(data) {
    const {tracking, loitering, fall} = data.benchmarks;
    const container = $('benchmarks'); container.replaceChildren();
    const cards = [
      ['目标跟踪', `${tracking.sequences} 个公开场景`, tracking.idf1.toFixed(2), 'IDF1',
        `HOTA ${tracking.hota.toFixed(2)} · MOTA ${tracking.mota.toFixed(2)}`,
        '衡量跟踪和编号保持能力。拥挤、小目标和遮挡仍是主要难点。'],
      ['徘徊识别', '按场景分组的 5 折评价', loitering.f1.toFixed(3), '轨迹级 F1',
        '基于已有轨迹特征的随机森林基线',
        '跨场景泛化仍有差距；未评价视频事件的起止定位，也未接入平台告警。'],
      ['摔倒识别', `${fall.videos} 个公开视频`, fall.test_event_f1.toFixed(3), '测试集事件 F1',
        'S1+S2 开发 → S3+S4 测试 · 时间 IoU ≥0.3',
        '测试结果低于开发集；床面、遮挡和部分身体可见仍易漏检。此能力未接入 Frigate。']
    ];
    cards.forEach(([title, scope, value, label, protocol, note], i) => {
      const card = document.createElement('article'); card.className = 'benchmark';
      const eyebrow = document.createElement('p'); eyebrow.className = 'eyebrow'; eyebrow.textContent = scope;
      const heading = document.createElement('h3'); heading.textContent = title;
      const metric = document.createElement('p'); metric.className = 'metric'; metric.textContent = value;
      const metricLabel = document.createElement('small'); metricLabel.textContent = label; metric.append(metricLabel);
      const method = document.createElement('p'); method.className = 'caption'; method.textContent = protocol;
      const text = document.createElement('p'); text.className = 'caption'; text.textContent = note;
      card.append(eyebrow,heading,metric,method,text);
      if (i === 2) {
        const bars = document.createElement('div'); bars.className = 'mini-bars';
        [['开发集',fall.development_event_f1],['测试集',fall.test_event_f1]].forEach(([label,v]) => {
          const bar = document.createElement('div'); bar.className = 'mini-bar';
          const name = document.createElement('span'); name.textContent = label;
          const fill = document.createElement('i'); fill.style.setProperty('--value',v);
          const number = document.createElement('b'); number.textContent = v.toFixed(3);
          bar.append(name,fill,number); bars.append(bar);
        });
        card.append(bars);
      }
      container.append(card);
    });
    const c = data.comparison;
    $('comparisonFacts').replaceChildren(fact('平台区域事件', c.platform_zone_events_total),
      fact('自研连续停留', c.self_long_stay_events), fact('连续口径匹配', c.temporal_matches));
    const cumulative = data.cumulative_comparison;
    $('cumulativeFacts').replaceChildren(fact('平台区域事件', cumulative.platform_zone_events),
      fact('自研累计停留', cumulative.self_cumulative_events), fact('累计口径匹配', cumulative.one_to_one_matches));
    $('platformReplay').href = data.platform.review_url;
    $('gateSeconds').textContent = `实测 ${data.platform.gate.gate_seconds.toFixed(3)} 秒`;
    $('externalEvent').href = data.platform.event_url;
    $('externalEvent').hidden = false;
    $('closureVideo').src = data.media.closure.url;
    $('externalClip').src = data.media.clip.url;
    const external = data.platform.external_case;
    $('externalFacts').textContent = `轨迹 ${external.candidate.track} · 源视频 ${external.candidate.source_start.toFixed(3)} 秒 · 记录停留 ${external.candidate.source_duration.toFixed(1)} 秒。平台事件 ${external.event_id}。复核状态 ${external.review_status}；持久 ledger 重复执行 ${external.duplicate_submit_state}。此录像为已保存副本，完整哈希见数据来源。`;
    const names = {normal_pass_through:'正常穿行',short_stay:'短暂停留',over_threshold_stay:'超过阈值停留'};
    $('roiFacts').replaceChildren(...data.roi.cases.map(c=>fact(`${names[c.type]} · 轨迹 ${c.track_id}`,`${c.observed_dwell_seconds.toFixed(3)} 秒 · ${c.review_status}`)));
    $('roiSheet').src = data.media.roi_sheet.url;
    $('sourcesText').textContent = Object.entries(data.sources)
      .map(([key,source]) => `${key}: ${source.path}\nSHA256: ${source.sha256}`).join('\n\n') + '\n\n' +
      Object.entries(data.media).map(([key,source])=>`${key}: ${source.source}\n源 SHA256: ${source.source_sha256}\n展示文件 SHA256: ${source.sha256}\n派生方式: ${source.transform}`).join('\n\n');
  }
  function tick() {
    if (!state.rules) return;
    const end = state.clipEnd ?? state.rules.sample.analysis_end;
    if (video.currentTime >= end - 0.00001) {
      video.pause();
      if (video.currentTime > end + 0.00001) video.currentTime = end;
      state.clipEnd = null;
    }
    render(); syncControls();
  }
  function animate() {
    tick();
    if (!video.paused) state.animation = requestAnimationFrame(animate);
  }
  video.addEventListener('play', () => {
    cancelAnimationFrame(state.animation); animate(); syncControls();
  });
  video.addEventListener('pause', () => { cancelAnimationFrame(state.animation); render(); syncControls(); });
  video.addEventListener('timeupdate', tick);
  video.addEventListener('seeked', () => { render(); syncControls(); });
  video.addEventListener('error', () => disableMedia('分析回放缺失或无法解码：/media/analysis_replay.mp4。请检查文件，页面不使用占位视频。'));
  $('play').addEventListener('click', () => {
    if (!video.paused) video.pause();
    else { if (video.currentTime >= state.rules.sample.analysis_end - 0.01) setTime(0); state.clipEnd = null; playVideo(); }
  });
  $('restart').addEventListener('click', () => { setTime(0); playVideo(); });
  $('seek').addEventListener('input', () => setTime(Number($('seek').value)));
  $('evidencePlay').addEventListener('click', () => {
    if (!state.selected || !state.ready) return;
    const t = state.selected.evidence_frame_video_time;
    setTime(Math.max(0,t-4)); state.clipEnd = Math.min(state.rules.sample.analysis_end,t+2); playVideo();
    $('analysis').scrollIntoView({block:'start',behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});
  });
  async function init() {
    try {
      const [rules,tracks] = await Promise.all([readJSON('/data/rules.json'),readJSON('/data/demo.json')]);
      if (rules.schema !== 'e04-rule-demo-v2' || rules.run.run_id !== tracks.run.run_id ||
          rules.provenance.source_video_sha256 !== tracks.provenance.source_sha256 ||
          rules.provenance.tracks_jsonl_sha256 !== tracks.provenance.files['tracks.jsonl'].sha256 ||
          rules.sample.analysis_end !== tracks.sample.analysis_end) {
        throw new Error('轨迹与规则的版本、运行、输入哈希或分析范围不一致，无法叠加展示。');
      }
      state.rules = rules; state.tracks = tracks;
      canvas.width = rules.sample.width; canvas.height = rules.sample.height;
      $('duration').textContent = `${fmt(rules.sample.analysis_end-rules.sample.analysis_start)} 秒`;
      $('trackCount').textContent = tracks.sample.track_count;
      $('zoneCount').textContent = rules.zones.length;
      $('candidateCount').textContent = rules.candidates.length;
      $('seek').max = rules.sample.analysis_end;
      $('reversal').textContent = `${rules.rules.find(r=>r.rule_id==='reversal').candidate_count} 条候选`;
      rules.zones.forEach((zone,i) => {
        const div = document.createElement('div'); div.className = 'zone-stat'; div.style.setProperty('--zone-color',colors[i]);
        const label = document.createElement('span'); label.textContent = zone.name;
        const count = document.createElement('strong'); count.id = `zone-${i}`; count.textContent = zone.counts[0];
        div.append(label,count); $('zoneStats').append(div);
      });
      renderCandidates();
      video.addEventListener('loadedmetadata', () => {
        if (video.duration < rules.sample.analysis_end - 1/rules.sample.fps) {
          disableMedia('分析回放短于规则覆盖范围，已停止展示。'); return;
        }
        state.ready = true; $('loading').hidden = true;
        ['play','restart','seek'].forEach(id=>{$(id).disabled=false;});
        $('evidencePlay').disabled = !rules.candidates.length;
        if (rules.candidates.length) selectCandidate(rules.candidates[0]);
      },{once:true});
      if (rules.candidates.length) selectCandidate(rules.candidates[0],false);
      else render(0);
      video.src = rules.files.analysis_video;
      const head = await fetch(rules.files.analysis_video,{method:'HEAD',cache:'no-store'});
      if (!head.ok) disableMedia(`分析回放不可用：${rules.files.analysis_video}（HTTP ${head.status}）。`);
    } catch (error) { disableMedia(`演示数据读取失败：${error.message}`); }
  }
  init();
  readJSON('/data/showcase.json').then(renderBenchmarks).catch(error => {
    $('benchmarks').textContent = `评价结果不可用：${error.message}`;
    failure(`评价快照读取失败：${error.message}。请运行 python3 scripts/build_showcase.py。`);
  });
})();
