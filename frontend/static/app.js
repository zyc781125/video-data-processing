const $ = selector => document.querySelector(selector);
const player = $('#player');
const state = {videos: [], current: null, frame: 0, start: null, end: null, timestamps: [], scanId: null, scanTimer: null, projectId: null, selectionId: 0};
const statusNames = {pending:'待处理',editing:'编辑中',exporting:'导出中',completed:'已完成',failed:'失败',rejected:'已拒绝',no_segment:'无片段',needs_reexport:'需重新导出'};

async function api(url, options = {}) {
  const response = await fetch(url, {headers:{'Content-Type':'application/json'}, ...options});
  const data = await response.json();
  if (!response.ok) throw Error(data.error || '请求失败');
  return data;
}
function showMessage(message, error = false) { $('#message').textContent = message; $('#message').className = error ? 'message error' : 'message'; }
function renderCounts(counts) { $('#counts').innerHTML = ['pending','editing','completed','failed'].map(key => `<span><b>${counts[key] || 0}</b>${statusNames[key]}</span>`).join(''); }
function naturalCompare(a, b) { return a.source_path.localeCompare(b.source_path, undefined, {numeric:true, sensitivity:'base'}); }
function renderList() {
  const filter = $('#filter').value;
  const list = state.videos.filter(video => filter === 'all' || video.status === filter).sort(naturalCompare);
  const container = $('#videoList'); container.replaceChildren();
  if (!list.length) { const empty = document.createElement('div'); empty.className = 'empty'; empty.textContent = state.scanId ? '扫描中，视频即将显示…' : '没有匹配的视频'; container.append(empty); return; }
  for (const video of list) {
    const item = document.createElement('div'); item.className = 'videoItem' + (state.current?.source_id === video.source_id ? ' active' : '');
    const title = document.createElement('strong'); title.textContent = video.source_path;
    const info = document.createElement('small'); info.textContent = `${statusNames[video.status] || video.status} · ${video.segment_count || 0} 段 · ${video.frame_count ? video.frame_count + ' 帧' : '无法读取'}`;
    item.append(title, info); item.onclick = () => selectVideo(video.source_id); container.append(item);
  }
}
function applyProject(data) {
  state.projectId = data.project?.project_id || null;
  state.videos = data.videos || [];
  renderCounts(data.counts || {}); renderList();
  if (data.project) {
    $('#inputDir').value = data.project.input_dir;
    $('#outputDir').value = data.project.output_dir;
    $('#projectSummary').textContent = `${data.project.input_dir} → ${data.project.output_dir}`;
  }
}
function clearEditor() {
  state.selectionId++; state.current = null; state.timestamps = []; state.start = state.end = null; state.frame = 0;
  player.pause(); player.removeAttribute('src'); player.load();
  $('#videoName').textContent = '未选择视频'; $('#videoInfo').textContent = '';
  $('#statusBadge').textContent = '待处理'; $('#statusBadge').className = 'badge';
  $('#frameNo').textContent = $('#timestamp').textContent = $('#totalFrames').textContent = $('#fps').textContent = '-';
  renderSelection(); renderSegments();
}
function clearProjectView() {
  clearEditor(); state.videos = []; renderCounts({}); renderList();
  $('#projectSummary').textContent = '正在切换项目…';
}
async function refresh() {
  const data = await api('/api/status');
  if (data.project?.project_id !== state.projectId) clearEditor();
  applyProject(data);
  if (data.scan_id && data.scan_id !== state.scanId) {
    state.scanId = data.scan_id;
    if (state.scanTimer) clearInterval(state.scanTimer);
    state.scanTimer = setInterval(() => pollScan(data.scan_id), 700);
    $('#scanBtn').disabled = true;
    await pollScan(data.scan_id);
  }
}
function frameForTime(time) {
  const values = state.timestamps;
  if (!values.length) return null;
  let low = 0, high = values.length;
  while (low < high) { const mid = (low + high) >> 1; if (values[mid] <= time + 0.000001) low = mid + 1; else high = mid; }
  return Math.max(0, Math.min(values.length - 1, low - 1));
}
function displayFrame(index, timestamp) {
  state.frame = index; $('#frameNo').textContent = index;
  $('#timestamp').textContent = Number.isFinite(timestamp) ? `${timestamp.toFixed(4)} s` : '未知';
}
function syncPlayback() {
  if (!state.current || !Number.isFinite(player.currentTime)) return;
  const index = frameForTime(player.currentTime);
  if (index !== null) displayFrame(index, state.timestamps[index]);
  else if (state.current.fps) displayFrame(Math.min(Math.max(0, Math.floor(player.currentTime * state.current.fps)), Math.max(0, state.current.frame_count - 1)), player.currentTime);
}
async function selectVideo(id) {
  const selection = ++state.selectionId;
  try {
    const video = await api(`/api/videos/${id}`);
    if (selection !== state.selectionId) return;
    state.current = video; state.timestamps = []; state.frame = 0; state.start = video.draft_start; state.end = video.draft_end;
    $('#videoName').textContent = video.source_path;
    $('#videoInfo').textContent = `${video.width}×${video.height} · ${video.fps_info || '未知帧率'} · ${video.duration.toFixed(2)} 秒`;
    $('#statusBadge').textContent = statusNames[video.status] || video.status; $('#statusBadge').className = `badge ${video.status}`;
    player.src = `/api/videos/${id}/file`;
    $('#totalFrames').textContent = video.frame_count || '未知'; $('#fps').textContent = video.fps ? video.fps.toFixed(3) : '未知';
    renderSelection(); renderSegments(); renderList(); displayFrame(0, 0);
    const data = await api(`/api/videos/${id}/timestamps`);
    if (selection !== state.selectionId) return;
    state.timestamps = data.timestamps; syncPlayback(); renderSegments();
  } catch (error) { if (selection === state.selectionId) showMessage(error.message, true); }
}
async function stepFrame(delta) {
  if (!state.current || !player.paused) return;
  const target = Math.max(0, Math.min(state.timestamps.length ? state.timestamps.length - 1 : state.current.frame_count - 1, state.frame + delta));
  if (state.timestamps.length) { player.currentTime = state.timestamps[target]; displayFrame(target, state.timestamps[target]); }
  else { try { const data = await api(`/api/videos/${state.current.source_id}/frame/${target}`); player.currentTime = data.timestamp ?? target / state.current.fps; displayFrame(data.frame_index, data.timestamp); } catch (error) { showMessage(error.message, true); } }
}
function renderSelection() { $('#startValue').textContent = state.start == null ? '未设置' : `第 ${state.start} 帧`; $('#endValue').textContent = state.end == null ? '未设置' : `第 ${state.end} 帧`; }
function segmentDuration(segment) {
  if (segment.status === 'completed' && Number.isFinite(segment.output_duration)) return segment.output_duration;
  const timestamps = state.timestamps;
  if (timestamps.length > segment.end_frame && Number.isFinite(timestamps[segment.start_frame])) {
    let endBoundary = timestamps[segment.end_frame + 1];
    if (!Number.isFinite(endBoundary)) {
      let lastInterval = 0;
      for (let index = segment.end_frame; index > 0; index--) {
        lastInterval = timestamps[index] - timestamps[index - 1];
        if (lastInterval > 0) break;
      }
      endBoundary = timestamps[segment.end_frame] + (lastInterval > 0 ? lastInterval : 1 / (state.current.fps || 1));
    }
    return Math.max(0, endBoundary - timestamps[segment.start_frame]);
  }
  return state.current?.fps ? segment.selected_frame_count / state.current.fps : null;
}
function renderSegments() {
  const container = $('#segments'); container.replaceChildren();
  if (!state.current?.segments.length) { const empty = document.createElement('div'); empty.className = 'empty'; empty.textContent = '尚未添加片段'; container.append(empty); return; }
  for (const segment of state.current.segments) {
    const row = document.createElement('div'); row.className = 'segment';
    const duration = segmentDuration(segment);
    const label = document.createElement('span'); label.textContent = `片段 ${String(segment.segment_index).padStart(3,'0')} · 第 ${segment.start_frame}–${segment.end_frame} 帧 · ${segment.selected_frame_count} 帧 · ${duration == null ? '时长计算中' : `时长 ${duration.toFixed(2)} 秒`}${segment.status === 'completed' ? ' · 已导出' : segment.status === 'failed' ? ' · 失败' : ''}`;
    const button = document.createElement('button'); button.textContent = '删除';
    button.onclick = async () => { try { state.current = await api(`/api/videos/${state.current.source_id}/segments/${segment.id}`, {method:'DELETE'}); renderSegments(); await refresh(); } catch (error) { showMessage(error.message, true); } };
    row.append(label, button); container.append(row);
  }
}
async function setPoint(which) {
  if (!state.current) return;
  const id = state.current.source_id, time = player.currentTime;
  try {
    const confirmed = await api(`/api/videos/${id}/frame-at?timestamp=${encodeURIComponent(time)}`);
    if (state.current?.source_id !== id) return;
    state[which] = confirmed.frame_index; displayFrame(confirmed.frame_index, confirmed.timestamp); renderSelection();
    await api(`/api/videos/${id}/draft`, {method:'POST', body:JSON.stringify({start:state.start,end:state.end})});
  } catch (error) { showMessage(error.message, true); }
}
async function add() {
  if (!state.current) return;
  if (state.start == null || state.end == null) return showMessage('请先同时设置起点和终点', true);
  if (state.end < state.start) return showMessage('终点不能早于起点', true);
  try {
    const id = state.current.source_id;
    await api(`/api/videos/${id}/segments`, {method:'POST', body:JSON.stringify({start:state.start,end:state.end})});
    state.start = state.end = null; renderSelection();
    state.current = await api(`/api/videos/${id}`); renderSegments(); await refresh(); showMessage('片段已添加');
  } catch (error) { showMessage(error.message, true); }
}
async function exportCurrent() {
  if (!state.current) return;
  if (!state.current.segments.length) return showMessage('没有片段，请标记为拒绝或无片段', true);
  const id = state.current.source_id;
  try {
    await api(`/api/videos/${id}/export`, {method:'POST'}); showMessage('正在导出');
    const timer = setInterval(async () => {
      try {
        const video = await api(`/api/videos/${id}`);
        if (video.status === 'exporting') return;
        clearInterval(timer); if (state.current?.source_id !== id) return;
        state.current = video; renderSegments(); await refresh();
        if (video.status === 'completed') {
          const next = state.videos.find(item => ['pending','editing','needs_reexport'].includes(item.status));
          if (next) await selectVideo(next.source_id); else showMessage('导出完成');
        } else showMessage(video.error || '导出失败', true);
      } catch (error) { clearInterval(timer); showMessage(error.message, true); }
    }, 1000);
  } catch (error) { showMessage(error.message, true); }
}
async function decision(value) {
  if (!state.current) return;
  try { await api(`/api/videos/${state.current.source_id}/decision`, {method:'POST',body:JSON.stringify({decision:value})}); await refresh(); showMessage('已保存决定'); }
  catch (error) { showMessage(error.message, true); }
}
async function pollScan(scanId) {
  if (state.scanId !== scanId) return;
  try {
    const data = await api(`/api/scans/${scanId}`);
    if (state.scanId !== scanId || data.project.project_id !== state.projectId) return;
    applyProject(data);
    showMessage(`扫描 ${data.scanned} / ${data.total}`);
    if (data.status === 'scanning') return;
    clearInterval(state.scanTimer); state.scanTimer = null; state.scanId = null; $('#scanBtn').disabled = false;
    if (data.status === 'failed') throw Error(data.error || '扫描失败');
    showMessage(`扫描完成，共 ${data.videos.length} 个视频`);
  } catch (error) {
    clearInterval(state.scanTimer); state.scanTimer = null; state.scanId = null; $('#scanBtn').disabled = false;
    $('#setupError').textContent = error.message;
  }
}
$('#scanBtn').onclick = async () => {
  const input = $('#inputDir').value, output = $('#outputDir').value;
  $('#scanBtn').disabled = true; $('#setupError').textContent = '';
  try {
    const data = await api('/api/project', {method:'POST',body:JSON.stringify({input_dir:input,output_dir:output})});
    if (state.scanTimer) clearInterval(state.scanTimer);
    clearProjectView(); applyProject(data);
    state.scanId = data.scan_id; showMessage(`扫描 0 / ${data.total}`);
    state.scanTimer = setInterval(() => pollScan(data.scan_id), 700);
    await pollScan(data.scan_id);
  } catch (error) { $('#scanBtn').disabled = false; $('#setupError').textContent = error.message; }
};
$('#filter').onchange = renderList;
document.querySelectorAll('[data-action]').forEach(button => button.onclick = () => ({start:()=>setPoint('start'),end:()=>setPoint('end'),add,export:exportCurrent,reject:()=>decision('rejected'),empty:()=>decision('no_segment')}[button.dataset.action])());
document.addEventListener('keydown', event => {
  if (event.target.matches('input,textarea,select,button') || !state.current) return;
  if (event.key === ' ') { event.preventDefault(); player.paused ? player.play() : player.pause(); }
  else if (event.key === 'ArrowLeft' && player.paused) { event.preventDefault(); stepFrame(-1); }
  else if (event.key === 'ArrowRight' && player.paused) { event.preventDefault(); stepFrame(1); }
  else if (event.key === '1') setPoint('start');
  else if (event.key === '2') setPoint('end');
  else if (event.key.toLowerCase() === 'a') add();
  else if (event.key.toLowerCase() === 'r') { event.preventDefault(); player.currentTime = 0; player.play(); }
  else if (event.key === 'Enter') exportCurrent();
});
player.addEventListener('timeupdate', syncPlayback);
player.addEventListener('seeked', syncPlayback);
player.addEventListener('seeking', syncPlayback);
if ('requestVideoFrameCallback' in player) {
  function tick() { syncPlayback(); player.requestVideoFrameCallback(tick); }
  player.requestVideoFrameCallback(tick);
}
refresh().catch(error => showMessage(error.message, true));
