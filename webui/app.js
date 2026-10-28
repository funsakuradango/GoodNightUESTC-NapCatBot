const $ = (id) => document.getElementById(id);
const base = '/api';
const labels = {check:'检查已有会话',login:'账号密码登录',status:'查询今日记录','manual-login':'学校浏览器验证',
  'wechat-authorize':'微信授权','wechat-ticket':'验证微信取票'};
const reasons = {session_restored:'已有学校会话有效',login_completed:'账号密码登录成功',manual_login_completed:'学校验证完成',
  credentials_required:'需要学校登录，请先保存账号密码或完成短信验证',verification_required:'需要在学校浏览器完成验证',
  login_paused:'登录已暂停，请先人工核对',login_unconfirmed:'登录未确认，请打开学校浏览器检查',
  wechat_ticket_verified_no_submission:'微信取票验证成功',wechat_authorization_required:'需要微信扫码授权',
  wechat_qr_expired:'二维码已过期或已取消，请重新授权',wechat_account_changed:'微信授权与学校账号不一致',
  school_identity_mismatch:'学校登录身份与配置不一致',wechat_identity_mismatch:'微信绑定身份与学校登录不一致',
  today_record_verified:'今日签到记录已核验',today_record_pending:'今日尚未签到',
  checkin_window_not_open:'今日签到时段尚未开放',checkin_window_closed:'今日签到时段已结束',
  exempt_record:'今日免签到',reported_not_a_checkin:'今日已填报情况，请核查',
  business_auth_required:'业务页面需要重新登录',operation_failed:'操作失败，请检查服务器诊断日志',
  wechat_school_not_bound:'请先在官方小程序绑定学校账号',manual_timeout:'学校浏览器验证已超时',cancelled:'管理操作已取消',
  task_or_page_timeout:'操作超时，请检查网络或重新验证',management_restarted:'管理服务重启，上次操作已中断',
  invalid_request:'配置无效。请检查输入；已有账号不支持直接更换，密码留空表示保留',
  task_already_running:'其他任务正在使用浏览器，请稍后重试',management_unavailable:'后端暂时不可用，请检查服务连接',
  unauthorized:'请使用 NapCat WebUI Token 登录管理台'};
let state = null;
let polling = false;
let qrShown = false;
let dirtyUsername = false;
let messageUntil = 0;
let needsLogin = false;

function showLogin() {
  needsLogin = true;
  $('login-panel').hidden = false;
  $('overview').hidden = true;
  $('workspace').hidden = true;
  $('logout').hidden = true;
  $('qr').removeAttribute('src');
  qrShown = false;
}

function notice(message, error = false, sticky = true) {
  $('notice').textContent = message;
  $('notice').classList.toggle('error', error);
  if (sticky) messageUntil = Date.now() + 6000;
}
async function api(path, body) {
  const response = await fetch(base + path, {
    method: body === undefined ? 'GET' : 'POST', cache:'no-store',
    headers:{'Content-Type':'application/json'},
    ...(body === undefined ? {} : {body:JSON.stringify(body)}),
    signal:AbortSignal.timeout(12000),
  });
  const data = await response.json();
  // NapCat can reject authorization with an application error in HTTP 200.
  if (!response.ok || (data.code !== undefined && data.code !== 0)) {
    if (response.status === 401) showLogin();
    const reason = response.status === 401 || String(data.message || '').includes('Unauthorized') ? 'unauthorized' : data.error;
    throw new Error(reasons[reason] || '请求失败，请检查 NapCat 登录和后端连接');
  }
  return data;
}
function render(s) {
  needsLogin = false;
  $('login-panel').hidden = true;
  $('overview').hidden = false;
  $('workspace').hidden = false;
  $('logout').hidden = false;
  state = s;
  if (!dirtyUsername && document.activeElement !== $('username')) $('username').value = s.username || '';
  $('password-hint').textContent = s.password_set ? '已设置，留空保留' : '尚未设置';
  $('enabled').checked = s.checkin_enabled;
  $('schedule').textContent = s.checkin_enabled ? s.schedule : '已暂停';
  $('date').textContent = s.date + ' · 北京时间';
  const statusNames = {COMPLETED:'签到完成',PENDING:'尚未签到',UNKNOWN:'结果待核查',SUBMITTING:'提交待核查',EXEMPT:'今日免签到',REPORTED:'已填报情况'};
  $('today').textContent = statusNames[s.today?.status] || (s.scheduled_date === s.date ? '已调度，暂无完成记录' : '暂无记录');
  $('paused').hidden = !s.auth_paused;
  $('school-status').textContent = s.auth_paused ? '需要人工处理' : '点击检查会话';
  $('wechat-status').textContent = s.wechat_saved ? '已保存授权' : '需要扫码授权';
  const job = s.job;
  const busy = Boolean(job?.running);
  document.querySelectorAll('[data-task]').forEach((b) => { b.disabled = busy; });
  $('cancel').hidden = !busy;
  $('browser-link').hidden = !(busy && job.action === 'manual-login');
  if (job) {
    $('job-label').textContent = labels[job.action] + ' · ' + new Date(job.started_at).toLocaleString();
    const result = job.result;
    $('job-result').textContent = busy ? (s.qr_ready ? '等待微信扫码确认…' : '正在执行…') :
      (reasons[result?.reason] || `${result?.status || ''} / ${result?.reason || ''}`);
    if (!busy && result?.status === 'AUTHENTICATED') {
      $('school-status').textContent = '最近验证成功';
      if (job.action.startsWith('wechat-')) $('wechat-status').textContent = '最近取票成功';
    }
    if (!busy && (result?.status === 'AUTH_REQUIRED' || result?.status === 'MANUAL_ACTION_REQUIRED')) $('school-status').textContent = '需要检查授权';
  }
  if (!s.qr_ready) {
    $('qr-area').hidden = true;
    $('qr').removeAttribute('src');
    qrShown = false;
  }
  if (Date.now() > messageUntil) notice(busy ? '管理操作执行中，请等待结果。' : '服务已连接 · 登录和授权操作不会提交签到', false, false);
}
async function refresh() {
  if (polling || needsLogin) return;
  polling = true;
  try {
    const s = await api('/status');
    render(s);
    if (s.qr_ready && !qrShown) {
      const qr = await api('/qr');
      if (typeof qr.image === 'string' && qr.image.startsWith('data:image/png;base64,')) {
        $('qr').src = qr.image;
        $('qr-area').hidden = false;
        qrShown = true;
      }
    }
  } catch (error) { notice(error.message, true); }
  finally { polling = false; }
}
async function action(name, resume = false) {
  try {
    await api('/jobs', {action:name, resume});
    notice('已启动：' + labels[name]);
    if (name === 'manual-login') notice('验证浏览器正在启动，请稍后点击“打开验证浏览器”。');
    await refresh();
  } catch (error) { notice(error.message, true); }
}
$('username').addEventListener('input', () => { dirtyUsername = true; });
$('credentials').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await api('/settings', {username:$('username').value.trim(),password:$('password').value});
    $('password').value = '';
    dirtyUsername = false;
    notice('账号配置已保存，下一次登录立即使用。');
    await refresh();
  } catch (error) { notice(error.message, true); }
});
document.querySelectorAll('[data-action]').forEach((button) => button.addEventListener('click', () => action(button.dataset.action)));
$('manual-login').addEventListener('click', () => action('manual-login'));
$('resume-login').addEventListener('click', () => action('login', true));
$('enabled').addEventListener('change', async () => {
  try { await api('/settings', {checkin_enabled:$('enabled').checked}); notice('调度设置已更新。'); }
  catch (error) { notice(error.message, true); }
  await refresh();
});
$('cancel').addEventListener('click', async () => {
  try { await api('/cancel', {}); notice('正在结束当前管理操作…'); }
  catch (error) { notice(error.message, true); }
  await refresh();
});
$('refresh').addEventListener('click', refresh);
$('admin-login').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const response = await fetch('/session', {method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({token:$('admin-token').value,totpCode:$('totp').value}),signal:AbortSignal.timeout(12000)});
    if (!response.ok) throw new Error(response.status === 429 ? '尝试次数过多，请一分钟后重试' : '登录失败，请核对 NapCat WebUI Token 和动态验证码');
    $('admin-token').value = '';
    $('totp').value = '';
    needsLogin = false;
    notice('管理登录成功。');
    await refresh();
  } catch (error) { notice(error.message, true); }
});
$('logout').addEventListener('click', async () => {
  try { await api('/logout', {}); showLogin(); notice('已退出管理登录。'); }
  catch (error) { notice(error.message, true); }
});
setInterval(() => { if (!document.hidden) refresh(); }, 2500);
refresh();
