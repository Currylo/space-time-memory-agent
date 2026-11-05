(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const video = $('sceneVideo'), canvas = $('overlay'), ctx = canvas.getContext('2d');
  const profile = new URLSearchParams(location.search).get('profile')==='baseline'?'baseline':'coverage';
  const state = {data:null,platform:null,platformMap:new Map(),platformError:null,mode:'analysis',filter:'all',selected:null,selectedTrack:null,ready:false,frame:0,raf:null,generation:0};
  const names = {stay:'区域停留',fall:'摔倒提示',crowd:'人数变化'};
  const fmt = x => Number(x).toFixed(2);
  const REVIEW_LABELS = {pending_review:'待复核',confirmed:'证据支持',rejected:'证据不支持',uncertain:'不能确认'};
  function error(message) { $('error').hidden = false; $('error').textContent = message; }
  function disabled(value) { ['play','restart','back','forward','seek','fullscreen'].forEach(id=>{$(id).disabled=value;}); }
  function frameAt(time) {
    let lo=0,hi=state.data.frames.length-1;
    while(lo<hi){const mid=Math.ceil((lo+hi)/2);if(state.data.frames[mid].time<=time+1e-6)lo=mid;else hi=mid-1;}
    return state.data.frames[lo];
  }
  function chart(time) {
    const c=$('countChart'),p=c.getContext('2d'),w=c.width,h=c.height,frames=state.data.frames;
    p.clearRect(0,0,w,h);const max=Math.max(1,...frames.map(f=>f.count));
    p.fillStyle='#f0f6f8';p.fillRect(0,0,w,h);
    p.strokeStyle='#278983';p.lineWidth=2;p.beginPath();
    frames.forEach((f,i)=>{const x=f.time/state.data.source.duration*w,y=h-12-f.count/max*(h-25);i?p.lineTo(x,y):p.moveTo(x,y);});p.stroke();
    p.strokeStyle='#9eafba';p.setLineDash([4,5]);p.beginPath();const sixty=60/state.data.source.duration*w;p.moveTo(sixty,0);p.lineTo(sixty,h);p.stroke();p.setLineDash([]);
    p.fillStyle='#637c8c';p.font='12px sans-serif';p.fillText('60 s',sixty+5,14);p.fillText(`峰值 ${max}`,8,14);
    p.strokeStyle='#203f54';p.lineWidth=2;p.beginPath();p.moveTo(time/state.data.source.duration*w,0);p.lineTo(time/state.data.source.duration*w,h);p.stroke();
  }
  function drawOverlay(row) {
    ctx.clearRect(0,0,canvas.width,canvas.height);
    if(state.mode==='original')return;
    const box=p=>[p.box[0]*canvas.width,p.box[1]*canvas.height,(p.box[2]-p.box[0])*canvas.width,(p.box[3]-p.box[1])*canvas.height];
    if(state.data.overlay_mode==='canvas'){
      const [x1,y1,x2,y2]=state.data.config.roi;
      ctx.strokeStyle='#57b8c4';ctx.lineWidth=1.5;ctx.strokeRect(x1*canvas.width,y1*canvas.height,(x2-x1)*canvas.width,(y2-y1)*canvas.height);
      (row.detections||[]).filter(d=>d.track_id==null).forEach(d=>{
        ctx.setLineDash([3,3]);ctx.lineWidth=1;ctx.strokeStyle='#b3bac3';ctx.strokeRect(...box(d));
      });ctx.setLineDash([]);
      row.people.forEach(p=>{
        const selected=p.id===state.selectedTrack||p.id===state.selected?.track_id;
        const rect=box(p);ctx.strokeStyle=selected?'#6bffff':p.inside?(p.dwell>=10?'#ffcf62':'#76d99b'):'#abb6c2';
        ctx.lineWidth=selected?3:1;ctx.strokeRect(...rect);
        if(selected||p.dwell>=10){
          const label=`#${p.id} ${p.inside?p.dwell.toFixed(1)+'s':'区外'}`;
          ctx.font='11px sans-serif';const w=ctx.measureText(label).width+6;
          ctx.fillStyle='rgba(7,24,32,.85)';ctx.fillRect(rect[0],Math.max(0,rect[1]-15),w,15);
          ctx.fillStyle=ctx.strokeStyle;ctx.fillText(label,rect[0]+3,Math.max(11,rect[1]-4));
        }
      });
    }
    // The accepted baseline replay already has burned-in boxes.
    if(state.selected?.track_id!=null){
      const p=row.people.find(p=>p.id===state.selected.track_id);
      if(p){ctx.strokeStyle='#6bffff';ctx.lineWidth=4;ctx.strokeRect(...box(p));}
    }
  }
  function renderPeople(row) {
    if(!$('personList'))return;
    $('detected').textContent=row.detected ?? '未记录';
    $('coverageBreakdown').textContent=row.detected==null
      ?'旧版未保存检测器原始输出，无法拆分未建轨目标。可切换增强版查看。'
      :`检测 ${row.detected} · 已建轨 ${row.tracked} · 区内计时 ${row.count} · 区外 ${row.outside_zone} · 未关联/待建轨 ${row.pending_tracks}`;
    const selected=row.people.find(p=>p.id===state.selectedTrack);
    $('personSummary').textContent=selected
      ?`目标 #${selected.id}：${selected.inside?'区域内已观测 '+selected.dwell.toFixed(1)+' 秒':'当前在区域外，不累计区域停留'}。编号只在本次运行内有效。`
      :state.selectedTrack!=null?`目标 #${state.selectedTrack} 当前未观测到；不补点、不替它继续计时。`:'点击画面中的目标框或下方编号，查看该目标的停留计时。';
    // Refresh the list once per sampled observation, not every animation frame.
    if(state.lastPeopleTime===row.time)return;
    state.lastPeopleTime=row.time;
    const list=$('personList');list.replaceChildren();
    row.people.slice().sort((a,b)=>Number(b.inside)-Number(a.inside)||b.dwell-a.dwell||a.id-b.id).forEach(p=>{
      const button=document.createElement('button');button.type='button';button.dataset.track=p.id;
      button.classList.toggle('active',p.id===state.selectedTrack);
      button.textContent=`#${p.id} · ${p.inside?p.dwell.toFixed(1)+' 秒':'区外'}`;
      button.addEventListener('click',()=>{state.selectedTrack=p.id;state.lastPeopleTime=null;render();});list.append(button);
    });
    if(!row.people.length)list.textContent='当前尚无已关联轨迹。灰色虚线框是检测到但尚未关联轨迹的目标。';
  }
  function render() {
    if(!state.data)return;
    const t=Math.max(0,Math.min(video.currentTime,state.data.source.duration)),row=frameAt(t);
    $('clock').textContent=`${fmt(t)} / ${fmt(state.data.source.duration)} 秒`;$('seek').value=t;
    $('tracked').textContent=row.tracked;$('zoneCount').textContent=row.count;$('dwell').textContent=`${row.max_dwell.toFixed(1)} 秒`;
    $('poseCount').textContent=`${row.pose_valid} / ${row.tracked}`;
    renderPeople(row);
    const past=state.data.candidates.filter(e=>e.condition_met_video_time<=t+1e-6);
    ['stay','fall','crowd'].forEach(kind=>{
      const n=past.filter(e=>e.kind===kind).length;$(kind+'Count').textContent=`到此 ${n} 条线索`;
    });
    $('stayStatus').textContent=row.max_dwell>=state.data.config.stay_seconds?'当前达到停留阈值':'已运行 · 当前未达阈值';
    $('stayMeter').firstElementChild.style.width=`${Math.min(100,row.max_dwell/state.data.config.stay_seconds*100)}%`;
    const recentFall=past.some(e=>e.kind==='fall'&&t-e.condition_met_video_time<=2);
    $('fallStatus').textContent=recentFall?'规则触发 · 待人工复核':(row.pose_valid?'已运行 · 当前未触发':'已运行 · 当前姿态不足');
    if(!recentFall && row.pose_valid>0 && row.pose_valid<row.tracked)$('fallStatus').textContent=`部分目标姿态不足（有效 ${row.pose_valid}/${row.tracked}）`;
    $('crowdStatus').textContent=row.crowd_ready?(past.some(e=>e.kind==='crowd'&&t-e.condition_met_video_time<=.4)?'规则触发 · 待人工复核':'完整 60 秒窗口 · 当前未触发'):`观察未满 60 秒（${Math.floor(t)} / 60）`;
    if(row.crowd_ready && row.count_window_observable===false)$('crowdStatus').textContent='窗口含未建轨时刻 · 暂不判断人数变化';
    $('crowdMeter').firstElementChild.style.width=`${Math.min(100,t/60*100)}%`;
    $('play').textContent=video.paused?(t>=state.data.source.duration-.01?'重播':'播放'):'暂停';
    drawOverlay(row);chart(t);
  }
  function seek(time) {
    if(!state.ready)return;
    video.pause();video.currentTime=Math.max(0,Math.min(state.data.source.duration,time));render();
  }
  async function play() {
    if(!state.ready)return;
    try{await video.play();}catch(e){error(`播放未能开始：${e.message}。可以再次点击播放。`);}
  }
  function animate(){render();if(!video.paused)state.raf=requestAnimationFrame(animate);}
  function platformFor(event){return event?state.platformMap.get(event.candidate_id):null;}
  function platformStatusInfo(event) {
    if(!state.platform) return {label:'平台未接入',cls:'unknown',usable:false};
    if(state.platform.clock && state.platform.clock.status!=='verified') return {label:'时间对齐待核验',cls:'unknown',usable:false};
    const p=platformFor(event);
    if(!p) return {label:'平台未写入',cls:'missing',usable:false};
    if(p.event_id && p.clip?.coverage?.status==='verified') return {label:'已写入 · 录像完整',cls:'ok',usable:true};
    if(p.event_id) return {label:'已写入 · 录像待核验',cls:'unknown',usable:false};
    return {label:'平台未写入',cls:'missing',usable:false};
  }
  function reviewStatusText(p) {
    if(!p) return '待复核';
    if(p.reviews && p.reviews.length) {
      const latest=p.reviews[p.reviews.length-1];
      return `${REVIEW_LABELS[latest.decision]||latest.decision} · ${latest.reviewer}`;
    }
    return REVIEW_LABELS[p.review_status]||p.review_status||'待复核';
  }
  function renderCandidates() {
    const list=$('candidateList');list.replaceChildren();
    const candidates=state.data.candidates.filter(e=>state.filter==='all'||e.kind===state.filter);
    if(!candidates.length){
      const div=document.createElement('div');div.className='empty';
      const b=document.createElement('b');b.textContent=state.filter==='all'?'本次视频没有规则线索':`本次视频没有${names[state.filter]}线索`;
      const p=document.createElement('p');p.textContent='该规则已实际执行；结果为空，不补充其他视频的事件。';
      div.append(b,p);list.append(div);return;
    }
    candidates.forEach(e=>{
      const button=document.createElement('button');button.type='button';button.dataset.id=e.candidate_id;
      const b=document.createElement('b');b.textContent=`${names[e.kind]} · ${fmt(e.condition_met_video_time)} 秒`;
      const s=document.createElement('small');
      const p=platformFor(e);
      const ps=platformStatusInfo(e);
      s.textContent=`${reviewStatusText(p)} · ${ps.usable?'录像可回看':ps.label}`;
      button.append(b,s);button.classList.toggle('active',state.selected?.candidate_id===e.candidate_id);button.setAttribute('aria-pressed',String(state.selected?.candidate_id===e.candidate_id));
      button.addEventListener('click',()=>selectCandidate(e));list.append(button);
    });
  }
  function ruleText(event) {
    const f=event.features||{};
    if(event.kind==='stay')return `连续可观测停留 ${Number(f.observed_dwell_seconds).toFixed(2)} 秒；阈值 ${f.threshold_seconds} 秒；入口为首次观测到在区域内，未确认真实进入。`;
    if(event.kind==='fall')return `躯干角 ${f.torso_angle}°；框宽高比 ${f.aspect}；垂直位移比 ${f.vertical_drop_ratio}；姿态持续时间 ${f.observed_posture_seconds} 秒。`;
    return `60 秒窗口内区域观测数从少于 ${f.from_below ?? state.data.config.crowd.from_below} 到至少 ${f.to_at_least ?? state.data.config.crowd.to_at_least}，并持续 ${f.sustain_seconds} 秒；这是人数变化提示，不是已确认空间聚集。`;
  }
  function windowText(event, p) {
    const f=event.features||{};
    const start=p?.rule_window_start ?? event.rule_window_start ?? event.evidence_start;
    const end=p?.rule_window_end ?? event.rule_window_end ?? event.evidence_end;
    if(event.kind==='crowd') {
      return `60 秒比较窗口 ${fmt(start)}—${fmt(end)} 秒；观测数 ${f.from_count} → ${f.to_count}；最后观测样本覆盖至 ${fmt(event.condition_met_video_time)} 秒（条件时刻）；覆盖核验要求 ${Number(p?.required_coverage_seconds ?? 60.2).toFixed(2)} 秒。`;
    }
    if(event.kind==='stay') {
      const valid=p?.observed_valid_seconds ?? event.causal_window?.observed_valid_seconds;
      const span=p?.observed_span_seconds ?? event.causal_window?.observed_span_seconds;
      return `因果样本 ${fmt(start)}—${fmt(end)} 秒；有效观测 ${Number(valid).toFixed(2)} 秒；实际跨度 ${Number(span).toFixed(2)} 秒；覆盖要求至 ${fmt(event.condition_met_video_time)} 秒。`;
      const gap=p?.missing_gap_seconds ?? event.missing_gap_seconds;
      const requiredEnd=p?.required_coverage_end ?? event.required_coverage_end ?? event.condition_met_video_time;
      const ledgerStart=p?.ledger_identity_evidence_start ?? event.ledger_identity_evidence_start;
      const ledgerEnd=p?.ledger_identity_evidence_end ?? event.ledger_identity_evidence_end;
      let text=`因果样本窗口 ${fmt(start)}—${fmt(end)} 秒；最后样本覆盖至 ${fmt(requiredEnd)} 秒（条件时刻，下一采样帧起点）；有效观测 ${Number(valid).toFixed(2)} 秒（跨度至条件 ${Number(span).toFixed(2)} 秒，缺帧 ${Number(gap).toFixed(2)} 秒）；覆盖要求终点 ${fmt(requiredEnd)} 秒。`;
      if(ledgerStart!=null)text+=` ledger 身份窗口 ${fmt(ledgerStart)}—${fmt(ledgerEnd)} 秒（保留旧键，未改事件）。`;
      return text;
    }
    return `源范围 ${fmt(start)}—${fmt(end)} 秒；条件时刻 ${fmt(event.condition_met_video_time)} 秒。`;
  }
  function renderReviewHistory(p) {
    const container=$('reviewHistory');container.replaceChildren();
    if(!p || !p.reviews || !p.reviews.length){
      const note=document.createElement('p');note.className='caption';
      note.textContent='尚无提交记录；真实复核待人，不预填操作者或结论。';
      container.append(note);return;
    }
    p.reviews.slice().reverse().forEach(review=>{
      const article=document.createElement('article');
      const head=document.createElement('b');head.textContent=`${REVIEW_LABELS[review.decision]||review.decision} · ${review.reviewer}`;
      const meta=document.createElement('small');meta.textContent=` ${review.reviewed_at}`;
      const note=document.createElement('p');note.textContent=review.note;
      article.append(head,meta,note);container.append(article);
    });
  }
  function resetReviewForm() {
    $('reviewer').value='';$('decision').value='';$('note').value='';
    $('reviewMessage').textContent='默认待复核，由实际操作者填写。';
  }
  function renderPlatformDetail(event) {
    const panel=$('platformDetail');panel.hidden=false;
    const p=platformFor(event);
    const ps=platformStatusInfo(event);
    $('evidenceReason').textContent=ruleText(event);
    $('platformCandidateTitle').textContent=`${names[event.kind]} · ${fmt(event.condition_met_video_time)} 秒`;
    $('platformStatusBadge').textContent=ps.label;
    $('platformStatusBadge').className=`badge ${ps.cls}`;
    $('platformRule').textContent=ruleText(event) + ((event.kind==='stay' && p?.window_version)?` 证据窗口已按缺帧观测更正（${p.window_version}）；登记窗口仅作 ledger 身份保留。`:'');
    $('platformTimes').textContent=`证据帧 ${fmt(event.evidence_frame_video_time)} 秒 / 条件 ${fmt(event.condition_met_video_time)} 秒 · 回放 ${p?.replay_id||'—'} · 第 ${p?.loop_index ?? '—'} 轮`;
    $('platformWindow').textContent=windowText(event,p);
    $('platformReviewStatus').textContent=reviewStatusText(p);
    $('platformClipStatus').textContent='—';
    $('reviewHistory').replaceChildren();
    resetReviewForm();
    $('evidenceMessage').hidden=true;$('evidenceMessage').classList.remove('error');
    $('evidenceVideo').hidden=true;$('evidenceVideo').removeAttribute('src');$('evidenceVideo').load();
    $('windowChart').hidden=true;$('windowChart').removeAttribute('src');
    $('evidenceImage').hidden=true;$('evidenceImage').removeAttribute('src');
    $('windowChartCaption').textContent='—';
    $('playWindow').disabled=true;
    $('playWindow').textContent='在共同视频中定位规则窗口';
    $('clipLink').href='#';$('clipLink').textContent='打开录像文件 ↗';
    $('eventLink').href='#';$('eventLink').textContent='平台未写入';
    $('submitReview').disabled=true;
    if(!state.platform && state.data.overlay_mode==='canvas'){
      $('evidenceMessage').hidden=false;
      $('evidenceMessage').textContent='这是本次增强分析的新线索，尚未写入平台。可在上方同源视频中回看完整依据；旧版平台事件保留在“旧版平台闭环”中。';
      $('playWindow').disabled=false;$('playWindow').textContent='在同源视频中回看规则依据';
      $('platformClipStatus').textContent='同源完整原画可回看；本次未创建平台事件或独立 clip。';
      $('reviewMessage').textContent='新运行尚未接入平台，复核提交保持禁用。';
      $('platformFootnote').textContent='增强版与旧版共享原视频，候选、轨迹编号及平台身份分开；不复用旧版的事件或人工结论。';
      renderReviewHistory(null);return;
    }
    if(!state.platform){
      $('evidenceMessage').hidden=false;$('evidenceMessage').classList.add('error');
      $('evidenceMessage').textContent='平台未接入或离线：可以查看同源规则依据，但没有本次平台事件、录像和复核。';
      $('reviewMessage').textContent='平台离线，暂不能提交复核。';
      renderReviewHistory(null);
      return;
    }
    if(!p){
      $('evidenceMessage').hidden=false;$('evidenceMessage').classList.add('error');
      $('evidenceMessage').textContent='平台未写入：该候选没有本次回放的 ledger 记录，不能伪造事件或录像。';
      $('reviewMessage').textContent='平台未写入，暂不能提交复核。';
      renderReviewHistory(null);
      return;
    }
    $('eventLink').href=p.platform_event_url||'#';
    $('eventLink').textContent=p.event_id?`${p.event_id} ↗`:'平台未写入';
    const clip=p.clip||{};
    const coverage=clip.coverage||{};
    if(clip.http_status===200 && clip.decoded) {
      const observedValid=p?.observed_valid_seconds;
      const comparison=coverage.comparison_window_seconds;
      $('platformClipStatus').textContent=`已解码 ${Number(clip.duration).toFixed(2)} 秒；覆盖要求 ${Number(coverage.required_seconds||0).toFixed(2)} 秒${comparison!=null?`（比较窗口 ${Number(comparison).toFixed(2)} 秒）`:''}${observedValid!=null?`；有效观测 ${Number(observedValid).toFixed(2)} 秒`:''}；${coverage.status==='verified'?'完整':'不完整'}`;
      $('evidenceVideo').src=clip.url;$('evidenceVideo').hidden=false;
      $('clipLink').href=clip.url;
      $('clipLink').textContent='打开平台录像文件 ↗';
    } else {
      $('platformClipStatus').textContent=clip.error||'录像缺失';
      $('evidenceMessage').hidden=false;$('evidenceMessage').classList.add('error');
      $('evidenceMessage').textContent='录像缺失或未就绪：无效播放已禁用，页面不会用旧片或其他视频替代。';
    }
    if(coverage.status==='verified' && p.rule_window_start!=null) {
      $('playWindow').disabled=false;
      $('playWindow').textContent=(event.kind==='crowd')?'在共同视频中播放完整 60 秒窗口':'在共同视频中播放停留证据窗口';
    }
    if(p.window_chart_url){$('windowChart').src=p.window_chart_url;$('windowChart').hidden=false;}
    if(p.evidence_image_url){$('evidenceImage').src=p.evidence_image_url;$('evidenceImage').hidden=false;}
    const w=p.evidence?.window||{};
    if(w.kind==='crowd')$('windowChartCaption').textContent=`60 秒窗口 ${fmt(w.window_start)}—${fmt(w.window_end)} 秒；持续段最小计数 ${w.sustain_min_count}（阈值 ≥${w.thresholds?.to_at_least}）。`;
    else if(w.kind==='stay')$('windowChartCaption').textContent=`轨迹 ${w.track_id} 有效观测 ${Number(w.valid_seconds).toFixed(2)} 秒 / 实际跨度 ${Number(w.span_seconds).toFixed(2)} 秒 / 缺帧 ${Number(w.missing_gap_seconds).toFixed(2)} 秒；最后样本覆盖至 ${fmt(w.required_coverage_end ?? w.condition_met_video_time)} 秒（条件时刻，下一采样帧起点）。`;
    else $('windowChartCaption').textContent='该候选没有窗口依据图。';
    const reviewable = !!( (p.key||p.ledger_key) && p.event_id && ['created','completed','reconciled'].includes(p.submit_state) && coverage.status==='verified' && p.evidence?.status==='verified' );
    $('submitReview').disabled=!reviewable;
    if($('submitReview').disabled)$('reviewMessage').textContent='平台事件或 ledger 键缺失，暂不能提交复核。';
    if(state.platform.clock && state.platform.clock.status!=='verified'){
      $('evidenceMessage').hidden=false;$('evidenceMessage').classList.add('error');
      $('evidenceMessage').textContent=`时间对齐${state.platform.clock.status==='failed'?'失败':'待核验'}：不能把该片段当作已验证证据；请先核对 clock_alignment.json。`;
      $('evidenceVideo').hidden=true;$('evidenceVideo').removeAttribute('src');$('evidenceVideo').load();
      $('playWindow').disabled=true;
      $('submitReview').disabled=true;
      $('reviewMessage').textContent='时间对齐待核验，暂不能提交复核。';
    }
    renderReviewHistory(p);
    $('platformFootnote').textContent=`本次回放 ${state.platform.replay.replay_id} · 摄像头 ${state.platform.replay.camera} · 手动候选事件 ${state.platform.summary.manual_api_events} 条 · 平台原生对象事件 ${state.platform.summary.native_object_events} 条（无 clip，不计入候选）。`;
  }
  function selectCandidate(event) {
    state.selected=event;
    state.selectedTrack=event.track_id ?? null;state.lastPeopleTime=null;
    document.querySelectorAll('.candidate-list button').forEach(b=>{const selected=b.dataset.id===event.candidate_id;b.classList.toggle('active',selected);b.setAttribute('aria-pressed',String(selected));});
    $('candidateDetail').hidden=false;
    const features=event.features;let why='';
    if(event.kind==='stay')why=`有效观测停留 ${features.observed_dwell_seconds.toFixed(2)} 秒；阈值 ${features.threshold_seconds} 秒`;
    if(event.kind==='fall')why=`躯干角 ${features.torso_angle}°；框宽高比 ${features.aspect}；垂直位移比 ${features.vertical_drop_ratio}`;
    if(event.kind==='crowd')why=`窗口 ${fmt(features.window_start)}—${fmt(features.window_end)} 秒；观测数 ${features.from_count} → ${features.to_count}；持续 ${features.sustain_seconds} 秒`;
    $('candidateDetail').textContent=`已定位到 ${fmt(event.evidence_frame_video_time)} 秒的证据画面。${why}。`;
    renderPlatformDetail(event);
    seek(event.evidence_frame_video_time);
    $('platformCandidateTitle').focus({preventScroll:true});
    $('platformDetail').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});
  }
  function setMode(mode) {
    if(!state.data||state.mode===mode)return;
    state.mode=mode;const t=video.currentTime,playing=!video.paused,generation=++state.generation;
    state.ready=false;video.pause();disabled(true);$('mediaMessage').hidden=false;
    $('mediaMessage').textContent='正在切换同一视频的画面模式…';
    ['analysis','original'].forEach(name=>{const b=$(name+'Mode');b.classList.toggle('active',name===mode);b.setAttribute('aria-pressed',String(name===mode));});
    video.addEventListener('loadedmetadata',()=>{
      if(generation!==state.generation)return;
      state.ready=true;disabled(false);$('mediaMessage').hidden=true;video.currentTime=Math.min(t,video.duration);
      video.playbackRate=Number($('speed').value);if(playing)play();render();
    },{once:true});
    $('frameLabel').textContent=mode==='analysis'?'同源分析叠加':'同源原画（H.264 派生）';
    video.src=mode==='analysis'?state.data.files.analysis.url:(state.data.files.original_h264?.url||state.data.files.original.url);
  }
  function setPlatformBanner() {
    const summary=$('platformSummary'),clockText=$('platformClockText');
    if(!state.platform){
      if(state.data.overlay_mode==='canvas'){
        summary.textContent='增强分析 · 新候选尚未接入平台 · 可直接回看同源视频依据';
        summary.className='platform-tag';clockText.textContent='已验收的旧版 18 个事件、录像及账本保持原样，切换“旧版平台闭环”查看。';return;
      }
      summary.textContent='平台未接入/离线 · 仅显示同源规则线索';
      summary.className='platform-tag offline';
      clockText.textContent=state.platformError?`读取失败：${state.platformError}`:'';
      return;
    }
    const clock=state.platform.clock||{},summaryData=state.platform.summary||{};
    if(state.platform.test_only){
      summary.textContent='测试模式 · 独立测试 ledger · 浏览器流程验证，不是真实复核';
      summary.className='platform-tag offline';
      clockText.textContent=state.platform.test_notice||'测试记录不进入真实 ledger。';
      return;
    }
    if(clock.status && clock.status!=='verified'){
      summary.textContent='平台已接入 · 时间对齐待核验/失败 · 证据播放与复核已禁用';
      summary.className='platform-tag offline';
      clockText.textContent=`时钟 ${clock.samples||0} 点 · RMSE ${Number(clock.rmse_seconds||0).toFixed(3)} 秒 · ${clock.status==='failed'?'对齐失败':'对齐待核验'}；请先核对 clock_alignment.json`;
      return;
    }
    summary.textContent=`本次 ${summaryData.manual_api_events} 条平台候选事件 · ${state.platform.candidates.filter(c=>c.clip?.coverage?.status==='verified').length} 条录像窗口已核验 · 结论以人工复核为准`;
    summary.className='platform-tag'+(clock.status==='verified'?'':' offline');
    const holdout=clock.holdout_check?.numeric_max_abs_residual_seconds;
    clockText.textContent=`时钟 ${clock.samples||0} 端点 · 拟合数值残差 ${Number(clock.rmse_seconds||0).toFixed(3)} 秒 · ${holdout==null?'留出检查未提供':`留出最大残差 ${Number(holdout).toFixed(3)} 秒`} · 帧周期 ${Number(clock.frame_period_seconds||0).toFixed(2)} 秒 · latest.jpg ${Number(clock.latency_seconds||0).toFixed(3)} 秒（注入用 ${Number(clock.latency_seconds_used_in_injection ?? clock.latency_seconds ?? 0).toFixed(3)}） · ${clock.status==='verified'?'对齐已核验':'对齐待核验'}`;
  }
  $('play').addEventListener('click',()=>{if(!video.paused)video.pause();else{if(video.currentTime>=state.data.source.duration-.01)seek(0);play();}});
  $('restart').addEventListener('click',()=>{seek(0);play();});
  $('back').addEventListener('click',()=>seek(video.currentTime-1));$('forward').addEventListener('click',()=>seek(video.currentTime+1));
  $('seek').addEventListener('input',()=>seek(Number($('seek').value)));
  $('speed').addEventListener('change',()=>{video.playbackRate=Number($('speed').value);});
  $('analysisMode').addEventListener('click',()=>setMode('analysis'));$('originalMode').addEventListener('click',()=>setMode('original'));
  $('profileSelect')?.addEventListener('change',e=>{
    const next=new URL(location.href);next.searchParams.set('profile',e.target.value);next.searchParams.set('t',fmt(video.currentTime));location.assign(next);
  });
  canvas.addEventListener('click',e=>{
    if(!state.data||state.mode==='original')return;
    const bounds=canvas.getBoundingClientRect(),x=(e.clientX-bounds.left)/bounds.width,y=(e.clientY-bounds.top)/bounds.height;
    const hits=frameAt(video.currentTime).people.filter(p=>x>=p.box[0]&&x<=p.box[2]&&y>=p.box[1]&&y<=p.box[3]);
    hits.sort((a,b)=>(a.box[2]-a.box[0])*(a.box[3]-a.box[1])-(b.box[2]-b.box[0])*(b.box[3]-b.box[1]));
    if(hits.length){state.selectedTrack=hits[0].id;state.lastPeopleTime=null;render();}
  });
  $('countChart').addEventListener('click',e=>{if(!state.data)return;const r=e.currentTarget.getBoundingClientRect();seek((e.clientX-r.left)/r.width*state.data.source.duration);});
  $('fullscreen').addEventListener('click',async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await $('stage').requestFullscreen();}catch(e){error(`全屏未能打开：${e.message}`);}});
  $('filters').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{
    state.filter=b.dataset.kind;if(state.selected&&state.filter!=='all'&&state.selected.kind!==state.filter){state.selected=null;$('evidenceVideo').pause();$('platformDetail').hidden=true;$('candidateDetail').hidden=true;render();}$('filters').querySelectorAll('button').forEach(x=>{x.classList.toggle('active',x===b);x.setAttribute('aria-pressed',String(x===b));});renderCandidates();
  }));
  $('playWindow').addEventListener('click',()=>{
    const p=platformFor(state.selected)||state.selected;if(!p||p.rule_window_start==null)return;
    $('candidateDetail').textContent=`已在共同视频中定位到规则窗口起点 ${fmt(p.rule_window_start)} 秒；当前为同源视频回看。`;
    seek(p.rule_window_start);play();
  });
  $('reviewForm').addEventListener('submit',async event=>{
    event.preventDefault();
    const p=platformFor(state.selected);
    if(!state.selected||!p){$('reviewMessage').textContent='保存失败：没有当前候选的平台记录。';return;}
    const reviewer=$('reviewer').value.trim(),decision=$('decision').value,note=$('note').value.trim();
    if(!reviewer||!decision||!note){$('reviewMessage').textContent='请填写复核者、结论和说明。';return;}
    $('submitReview').disabled=true;$('reviewMessage').textContent='正在保存到 ledger…';
    try{
      const response=await fetch(`/api/review/submit?profile=${profile}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:state.selected.candidate_id,key:p.key||p.ledger_key,reviewer,decision,note})});
      const data=await response.json();
      if(!response.ok||!data.success)throw new Error(data.message||`HTTP ${response.status}`);
      p.review_status=data.candidate.review_status;
      p.reviews=data.candidate.reviews||[];
      $('reviewMessage').textContent=`已保存：${REVIEW_LABELS[data.record.decision]||data.record.decision}（${data.record.reviewed_at}）`;
      $('decision').value='';$('note').value='';
      $('platformReviewStatus').textContent=reviewStatusText(p);
      renderReviewHistory(p);renderCandidates();renderPlatformDetail(state.selected);
      $('reviewMessage').textContent=`已保存：${REVIEW_LABELS[data.record.decision]||data.record.decision}（${data.record.reviewed_at}）`;
    }catch(e){$('reviewMessage').textContent=`保存失败：${e.message}`;}
    finally{$('submitReview').disabled=!(p && (p.key||p.ledger_key) && p.event_id && ['created','completed','reconciled'].includes(p.submit_state));}
  });
  $('evidenceVideo').addEventListener('loadedmetadata',()=>{
    const evidence=$('evidenceVideo');
    try{if(evidence.duration>0.1)evidence.currentTime=Math.min(0.5,evidence.duration/2);}catch(e){}
  });
  $('evidenceVideo').addEventListener('error',()=>{
    const evidence=$('evidenceVideo');
    if(evidence.hidden)return;
    evidence.hidden=true;
    $('platformClipStatus').textContent='平台录像实际加载失败（缺失或不可解码）';
    $('evidenceMessage').hidden=false;
    $('evidenceMessage').classList.add('error');
    $('evidenceMessage').textContent='平台录像缺失或无法解码：已禁用无效播放，不用旧片或其他视频替代。';
  });
  video.addEventListener('play',()=>{cancelAnimationFrame(state.raf);animate();});  video.addEventListener('pause',()=>{cancelAnimationFrame(state.raf);render();});
  video.addEventListener('timeupdate',render);video.addEventListener('seeked',render);video.addEventListener('ended',render);
  video.addEventListener('error',()=>{state.ready=false;disabled(true);$('mediaMessage').hidden=false;$('mediaMessage').textContent='视频缺失或无法解码，请检查本地文件。';error(`无法加载 ${video.currentSrc||'同源分析视频'}。页面不使用占位视频。`);});
  async function loadPlatform() {
    try{
      const response=await fetch(`/api/unified/platform?profile=${profile}`,{cache:'no-store'});
      if(!response.ok)throw new Error(`HTTP ${response.status}`);
      const data=await response.json();
      if(data.schema!=='same-source-platform-v1')throw new Error('平台数据结构不符');
      if(data.source?.sha256!==state.data.source.sha256 || (data.candidates||[]).some(c=>c.run_id!==state.data.run_id)){
        state.platformError='平台证据属于旧运行，未绑定到本次增强分析。';return;
      }
      state.platform=data;
      (data.candidates||[]).forEach(c=>state.platformMap.set(c.candidate_id,c));
    }catch(e){state.platform=null;state.platformError=e.message;}
  }
  async function init() {
    try{
      const response=await fetch(profile==='baseline'?'/data/unified.json':'/data/unified_coverage1280.json',{cache:'no-store'});if(!response.ok)throw new Error(`同源数据 HTTP ${response.status}`);
      const data=await response.json();if(data.schema!=='same-source-three-rules-v1'||data.source.repeated)throw new Error('数据不是单段连续原序列结果');
      if(!data.frames.length||data.frames.some((f,i)=>i&&f.time<=data.frames[i-1].time))throw new Error('采样时间不连续递增');
      if(data.candidates.some(e=>e.run_id!==data.run_id||e.source_sha256!==data.source.sha256))throw new Error('候选与视频来源不一致');
      state.data=data;canvas.width=data.source.width;canvas.height=data.source.height;
      if($('profileSelect'))$('profileSelect').value=profile;
      if($('coverageNotice'))$('coverageNotice').textContent=profile==='baseline'
        ?'旧版平台闭环：可查看已验收的 18 个平台事件。人员检测覆盖较低；旧轨迹编号与增强版不互通。'
        :'当前使用人员覆盖增强版。绿框为区域内轨迹，灰色虚线为检测到但未建轨目标；仍可能漏检，不代表真实总人数。新候选未借用旧平台事件。';
      await loadPlatform();setPlatformBanner();
      $('stage').style.aspectRatio=`${data.source.width}/${data.source.height}`;
      $('sourceName').textContent=data.source.name;$('sourceSpec').textContent=`${fmt(data.source.duration)} 秒 · 连续原序列视频`;
      $('sampleFps').textContent=data.config.analysis_fps;$('seek').max=data.source.duration;$('seek').step=1/data.source.fps;
      video.poster=data.files.preview.url;
      const provenance=$('provenance');const p=document.createElement('p');p.textContent='当前为同源离线联合分析：同一源视频、同一时间基准、同一 ROI。规则候选是模型派生结果，不是 GT；摔倒 0 条只表示本次规则未触发。增强版为独立新运行，未绑定旧平台事件；旧版平台证据可单独查看。原画使用帧号一致的 H.264 派生副本，原始文件保留。';
      const a=document.createElement('a');a.href=data.source.url;a.target='_blank';a.rel='noopener';a.textContent='官方视频与许可来源 ↗';
      const pre=document.createElement('pre');
      const platformInfo=state.platform?`\n平台回放：${state.platform.replay.replay_id} · ${state.platform.replay.camera}\n平台事件：${state.platform.summary.manual_api_events} 条候选 / ${state.platform.summary.native_object_events} 条原生对象事件（无 clip）\n时钟：${state.platform.clock.samples} 点 · RMSE ${Number(state.platform.clock.rmse_seconds).toFixed(3)} 秒 · 漂移 ${Number(state.platform.clock.drift_ppm).toFixed(2)} ppm · 对齐 ${state.platform.clock.status}\n平台版本：${JSON.stringify(state.platform.platform.version)}`:'\n平台：未接入/离线';
      pre.textContent=`运行：${data.run_id}\n视频 SHA256：${data.source.sha256}\n规则配置 SHA256：${data.config_sha256}\n许可：${data.source.license}\n样本帧：${data.sample_count}\n本次完整候选：停留 ${data.candidate_counts.stay} / 摔倒 ${data.candidate_counts.fall} / 人数变化 ${data.candidate_counts.crowd}\n姿态分支：${data.config.pose_adaptation}\n候选为模型观测派生，尚无独立人工 GT。${platformInfo}`;
      provenance.replaceChildren(p,a,pre);
      const counts={all:data.candidates.length,...data.candidate_counts};
      $('clueOverview').textContent=`停留 ${counts.stay} 条 · 人数变化 ${counts.crowd} 条 · 摔倒 ${counts.fall} 条`;
      $('filters').querySelectorAll('button').forEach(b=>{b.textContent=`${b.dataset.kind==='all'?'全部':names[b.dataset.kind]} ${counts[b.dataset.kind]}`;b.setAttribute('aria-pressed',String(b.dataset.kind===state.filter));});
      renderCandidates();render();
      video.addEventListener('loadedmetadata',()=>{
        if(video.duration+1/data.source.fps<data.source.duration){
          state.ready=false;disabled(true);error('回放时长不足，不能展示完整规则覆盖范围。');return;
        }
        state.ready=true;disabled(false);$('mediaMessage').hidden=true;render();
        const requested=Number(new URLSearchParams(location.search).get('t'));
        if(Number.isFinite(requested)&&requested>0)seek(requested);
      },{once:true});
      video.src=data.files.analysis.url;
    }catch(e){disabled(true);$('mediaMessage').textContent='同源数据不可用';error(`联合演示读取失败：${e.message}`);}
  }
  init();
})();
