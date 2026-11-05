(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const state = {candidates: [], selected: null};

  function statusLabel(value) {
    return value === 'pending_review' ? '待复核' : value;
  }
  function statusClass(value) {
    if (value === 'uncertain') return 'badge uncertain';
    if (value === 'rejected') return 'badge rejected';
    if (value === 'confirmed') return 'badge';
    return 'badge pending';
  }
  function setError(message) {
    $('error').hidden = !message;
    $('error').textContent = message || '';
  }
  function renderHistory(reviews) {
    const container = $('history');
    container.replaceChildren();
    if (!reviews || !reviews.length) {
      const p = document.createElement('p'); p.className = 'caption'; p.textContent = '尚无提交记录。';
      container.append(p); return;
    }
    reviews.slice().reverse().forEach(review => {
      const article = document.createElement('article');
      const head = document.createElement('b'); head.textContent = `${review.decision} · ${review.reviewer}`;
      const meta = document.createElement('small'); meta.textContent = ` ${review.reviewed_at}`;
      const note = document.createElement('p'); note.textContent = review.note;
      article.append(head, meta, note);
      container.append(article);
    });
  }
  function selectCandidate(candidate) {
    state.selected = candidate;
    document.querySelectorAll('.review-item').forEach(button => {
      button.classList.toggle('active', button.dataset.id === candidate.candidate_id);
      button.setAttribute('aria-pressed', String(button.dataset.id === candidate.candidate_id));
    });
    $('detailEyebrow').textContent = candidate.kind;
    $('candidateTitle').textContent = candidate.title;
    $('ruleText').textContent = candidate.rule;
    $('timeText').textContent = `源 ${Number(candidate.source_time).toFixed(3)} 秒 / 条件 ${Number(candidate.condition_time).toFixed(3)} 秒`;
    $('candidateNote').textContent = candidate.note;
    $('statusBadge').textContent = statusLabel(candidate.review_status);
    $('statusBadge').className = statusClass(candidate.review_status);
    $('eventLink').href = candidate.event_url;
    $('clipLink').href = candidate.clip_url;
    $('evidenceVideo').src = candidate.clip_url;
    $('reviewMessage').textContent = '';
    renderHistory(candidate.reviews || []);
  }
  function renderCandidates() {
    const list = $('candidateList');
    list.replaceChildren();
    if (!state.candidates.length) {
      list.textContent = '没有候选。'; return;
    }
    state.candidates.forEach(candidate => {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'review-item'; button.dataset.id = candidate.candidate_id;
      const title = document.createElement('b'); title.textContent = candidate.title;
      const meta = document.createElement('small'); meta.textContent = `${candidate.kind} · ${statusLabel(candidate.review_status)}`;
      button.append(title, meta);
      button.addEventListener('click', () => selectCandidate(candidate));
      list.append(button);
    });
    selectCandidate(state.candidates[0]);
  }
  async function loadCandidates() {
    const response = await fetch('/api/review/candidates', {cache: 'no-store'});
    if (!response.ok) throw new Error(`候选接口 HTTP ${response.status}`);
    const data = await response.json();
    state.candidates = data.candidates || [];
    renderCandidates();
  }
  async function submit(event) {
    event.preventDefault();
    if (!state.selected) return;
    const reviewer = $('reviewer').value.trim();
    const decision = $('decision').value;
    const note = $('note').value.trim();
    if (!reviewer || !decision || !note) {
      $('reviewMessage').textContent = '请填写复核者、结论和说明。'; return;
    }
    $('submitReview').disabled = true;
    $('reviewMessage').textContent = '正在保存…';
    try {
      const response = await fetch('/api/review/submit', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({candidate_id: state.selected.candidate_id, reviewer, decision, note})
      });
      const data = await response.json();
      if (!response.ok || !data.success) throw new Error(data.message || `HTTP ${response.status}`);
      const index = state.candidates.findIndex(item => item.candidate_id === state.selected.candidate_id);
      state.candidates[index] = data.candidate;
      selectCandidate(data.candidate);
      $('reviewMessage').textContent = `已保存：${data.record.decision}（${data.record.reviewed_at}）`;
      $('note').value = '';
      $('decision').value = '';
    } catch (error) {
      $('reviewMessage').textContent = `保存失败：${error.message}`;
    } finally {
      $('submitReview').disabled = false;
    }
  }
  $('reviewForm').addEventListener('submit', submit);
  loadCandidates().catch(error => setError(`读取复核候选失败：${error.message}`));
})();
