import {datasets, metrics} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {MetricStatus} from '../constants/enums.js';
import {esc, fmt, date, notify} from '../utils/dom.js';

const canEdit = () => session.user.role === 'admin' || session.user.role === 'analyst';
const canPublish = () => session.user.role === 'admin';

function chainList(chain, {draftLabel = false} = {}) {
  if (!chain?.length) return '<p class="empty">该口径不依赖其他子口径。</p>';
  return '<ol class="dependency-chain">' + chain.map((node, index) => '<li>' +
    '<span class="chain-index">' + String(index + 1).padStart(2, '0') + '</span>' +
    '<div><strong>' + esc(node.name || node.code) + '</strong> <code>' + esc(node.code) + '</code>' +
    (node.version_no ? ' <span class="version-tag">v' + node.version_no + '</span>' : ' <span class="version-tag draft">未发布草稿</span>') +
    (node.unit ? ' <small>' + esc(node.unit) + '</small>' : '') +
    '<p>' + esc(node.expression || '') + '</p>' +
    (node.path?.length > 1 ? '<small>路径 ' + esc(node.path.join(' → ')) + '</small>' : '') +
    '</div></li>').join('') + '</ol>';
}

function validationBanner(validation) {
  if (!validation) return '';
  const ignored = (validation.ignored_drafts || []).map(item =>
    '<p class="warning">' + esc(item.message) + '；子口径草稿不会进入本次发布。</p>').join('');
  if (validation.ok) {
    return '<p class="validation ok">校验通过 · 结果单位 ' + esc(validation.unit) + '。发布时会重新校验并固定整条依赖的已发布版本。</p>' + ignored;
  }
  return '<div class="validation error"><strong>' + esc(validation.code === 'circular_dependency' ? '循环引用，拒绝发布' : validation.code === 'unit_conflict' ? '单位冲突，拒绝发布' : validation.code === 'missing_reference' ? '缺少引用，拒绝发布' : '公式无效，拒绝发布') + '</strong>' +
    '<p>' + esc(validation.message) + '</p>' +
    (validation.chain_text ? '<p class="chain-text">依赖链：' + esc(validation.chain_text) + '</p>' : '') + '</div>' + ignored;
}

export async function metricsPage(id) {
  workspace.datasets = await datasets.list();
  const list = await metrics.list();
  workspace.metrics = list;
  const selected = (id && list.find(item => String(item.id) === String(id))) || list[0];
  const [detail, deps, versions, usage] = selected
    ? await Promise.all([metrics.get(selected.id), metrics.draftDependencies(selected.id), metrics.versions(selected.id), metrics.usage(selected.id)])
    : [null, null, null, null];
  workspace.selectedMetric = detail?.id || null;
  const draft = detail?.draft;
  const validation = detail?.draft_validation;
  const current = detail?.current_version;

  const registry = '<section class="panel metric-registry"><div class="section-title"><h2>口径登记簿</h2><span>' + list.length + ' 个口径 · 公式以代码引用子口径</span></div>' +
    '<div class="metric-list">' + list.map(item => '<a class="metric-item' + (detail && item.id === detail.id ? ' active' : '') + '" href="#/metrics/' + item.id + '">' +
      '<div><strong>' + esc(item.name) + '</strong> <code>' + esc(item.code) + '</code>' +
      '<span class="metric-state ' + item.status + '">' + esc(MetricStatus[item.status] || item.status) + '</span></div>' +
      '<small>' + (item.current_version ? '已发布 v' + item.current_version.version_no + ' · ' + esc(item.current_version.unit) : '尚未发布') +
      (item.draft?.differs_from_published ? ' · 草稿有改动' : '') + '</small></a>').join('') + '</div></section>';

  const createForm = '<section class="panel"><div class="section-title"><h2>新建口径</h2><span>' + (canEdit() ? '先用 ref(子口径代码) 组装' : '当前角色只读') + '</span></div>' +
    '<form id="metric-form">' +
    '<div class="parameter-grid"><label>名称<input name="name" required minlength="2" placeholder="例如：合作渠道净收入"></label>' +
    '<label>稳定代码<input name="code" required pattern="[a-z][a-z0-9_]{1,63}" placeholder="partner_net"></label></div>' +
    '<label>说明<input name="description" placeholder="一句话说明业务用途" maxlength="500"></label>' +
    '<label>公式（DSL）<textarea name="expression" rows="3" required placeholder="sum(gross)-ref(deductions)">sum(gross)-ref(deductions)</textarea></label>' +
    '<p class="hint">可直接引用已发布或草稿中的子口径：<code>ref(代码)</code>。支持 <code>sum(字段)</code>、<code>ratio(分子,分母)</code>、四则运算和常量。' +
    '保存草稿可随时试算，发布后整条依赖固定版本。</p>' +
    '<button type="submit" class="primary" ' + (canEdit() ? '' : 'disabled') + '>保存为新口径草稿</button></form></section>';

  if (!detail) {
    return heading('03 / METRIC REGISTRY', '口径登记簿', '口径可以引用口径。草稿可联动试算；循环引用、单位冲突或缺引用会给出依赖链并拒绝发布。') +
      '<div class="metric-grid"><div>' + registry + '</div><div>' + createForm + '</div></div>';
  }

  const editor = '<section class="panel"><div class="section-title"><h2>' + esc(detail.name) + ' <code>' + esc(detail.code) + '</code></h2>' +
    '<span class="metric-state ' + detail.status + '">' + esc(MetricStatus[detail.status] || detail.status) + '</span></div>' +
    '<p class="hint">' + esc(detail.description || '未填写说明') + (current ? ' · 当前发布 v' + current.version_no + ' · 单位 ' + esc(current.unit) : ' · 尚未发布') + '</p>' +
    '<form id="draft-form">' +
    '<label>草稿公式（编辑不影响已发布版本）<textarea name="expression" rows="3" required ' + (canEdit() ? '' : 'disabled') + '>' + esc(draft?.expression || '') + '</textarea></label>' +
    '<label>草稿备注<input name="note" maxlength="500" value="' + esc(draft?.note || '') + '" ' + (canEdit() ? '' : 'disabled') + '></label>' +
    '<input type="hidden" name="expected_revision" value="' + esc(draft?.revision ?? '') + '">' +
    '<div class="button-row">' +
    '<button type="submit" ' + (canEdit() ? '' : 'disabled') + '>保存草稿</button>' +
    '<button type="button" id="validate-metric" class="button" ' + (canEdit() ? '' : 'disabled') + '>校验依赖与单位</button>' +
    '<button type="button" id="publish-metric" class="primary" ' + (canPublish() ? '' : 'disabled') + '>发布并固定整条依赖</button>' +
    '</div></form>' +
    '<div id="validation-slot">' + validationBanner(validation) + '</div>' +
    '<p class="hint">草稿修订 r' + (draft?.revision ?? 0) + (draft?.differs_from_published ? ' · 与发布版不同' : ' · 与发布版一致') +
    '。发布需负责人操作；发布后子口径再改，不会改变已发布公式。</p>' +
    (detail.status === 'active'
      ? '<button class="button small" id="disable-metric" ' + (canPublish() ? '' : 'disabled') + '>停用口径（阻止新分析）</button>'
      : '<button class="button small" id="enable-metric" ' + (canPublish() ? '' : 'disabled') + '>重新启用口径</button>') +
    '</section>';

  const trialPanel = '<section class="panel"><div class="section-title"><h2>草稿联动试算</h2><span>沿草稿链取子口径最新草稿，不写入任务</span></div>' +
    '<form id="trial-form"><div class="parameter-grid">' +
    '<label>数据批次<select name="dataset_id" required>' + workspace.datasets.map(d => '<option value="' + d.id + '">' + esc(d.name) + ' · ' + d.rows + ' 条</option>').join('') + '</select></label>' +
    '<label>对账维度<select name="group_by"><option value="channel">按渠道</option><option value="week">按自然周</option></select></label>' +
    '<label>零分母策略<select name="zero_policy"><option value="reject">阻止计算并修复</option><option value="zero">明确记0并警告</option></select></label>' +
    '</div><button type="submit" class="primary" ' + (canEdit() ? '' : 'disabled') + '>用当前草稿试算 ↗</button></form>' +
    '<div id="trial-output"><p class="empty">保存草稿后试算，可看到引用到的子口径实际取用的是草稿还是已发布版本。</p></div></section>';

  const dependencyPanel = deps?.error
    ? '<section class="panel"><div class="section-title"><h2>依赖链</h2><span>当前草稿无法解析</span></div>' +
      '<div class="validation error"><strong>依赖链无法生成</strong><p>' + esc(deps.error.detail) + '</p>' +
      (deps.error.chain_text ? '<p class="chain-text">依赖链：' + esc(deps.error.chain_text) + '</p>' : '') + '</div></section>'
    : '<section class="panel"><div class="section-title"><h2>依赖链</h2><span>' + (deps?.provisional ? '当前草稿（含未发布子口径）' : '已发布版本') + '</span></div>' +
    '<p class="hint">根口径单位：' + esc(deps?.unit || '—') + '。下方顺序即发布时固定版本的顺序。</p>' +
    chainList(deps?.chain || []) +
    '</section>';

  const versionPanel = '<section class="panel"><div class="section-title"><h2>版本历史</h2><span>' + (versions?.versions?.length || 0) + ' 个发布版本</span></div>' +
    (!versions?.versions?.length ? '<p class="empty">尚未发布。发布后每个版本固定整条依赖的具体版本。</p>' :
      '<div class="version-list">' + versions.versions.map(v => '<article><div><strong>v' + v.version_no + '</strong> <small>' + date(v.published_at) + '</small>' +
        '<p><code>' + esc(v.expression) + '</code></p>' +
        (v.dependencies?.length ? '<small>固定依赖：' + v.dependencies.map(dep => esc(dep.name) + '（' + esc(dep.code) + '）v' + dep.version_no).join('；') + '</small>' : '<small>无子口径依赖</small>') +
        (v.note ? '<small>备注：' + esc(v.note) + '</small>' : '') +
        '</div><a class="text-button" href="#/workbench">用于分析 ↗</a></article>').join('') + '</div>') + '</section>';

  const usagePanel = '<section class="panel"><div class="section-title"><h2>引用与使用</h2><span>最近 50 条绑定此口径的分析</span></div>' +
    (!usage?.length ? '<p class="empty">还没有分析任务绑定此口径。到分析工作台选择它即可。</p>' :
      '<div class="usage-list">' + usage.map(job => '<div><strong>' + esc(job.name) + '</strong>' +
        '<small>' + esc(job.status) + ' · 固定 ' + (job.metric_version_no ? 'v' + job.metric_version_no : '（历史任务，无版本）') + ' · ' + date(job.created_at) + (job.approved ? ' · 已批准' : '') + '</small></div>').join('') + '</div>') + '</section>';

  return heading('03 / METRIC REGISTRY', '口径登记簿与版本链', '口径可以引用口径。草稿可联动试算；循环引用、单位冲突或缺引用会给出依赖链并拒绝发布。') +
    '<div class="metric-grid"><div>' + registry + createForm + editor + '</div><div>' + dependencyPanel + trialPanel + versionPanel + usagePanel + '</div></div>';
}

function renderTrial(output, payload) {
  const snapshot = payload.snapshot || {};
  const result = payload.result || {};
  const usedDrafts = (snapshot.chain || []).filter(node => node.is_draft || !node.version_id);
  output.innerHTML = '<div class="trial-snapshot">' +
    '<p class="validation ok">试算完成 · 单位 ' + esc(snapshot.unit || '') + '。' +
    (usedDrafts.length ? '本次取用了未发布草稿：' + usedDrafts.map(node => esc(node.name || node.code)).join('、') + '。' : '本次全部取用已发布版本。') + '</p>' +
    '<p>' + esc(result.summary || '') + '</p>' +
    '<div class="metrics">' + (result.metrics || []).map(m => '<div><span>' + esc(m.label) + '</span><strong>' + esc(fmt(m.value)) + '<small>' + esc(m.unit || '') + '</small></strong></div>').join('') + '</div>' +
    '<h3>试算依赖链</h3>' + chainList(snapshot.chain || []) +
    (result.warnings?.length ? result.warnings.map(w => '<p class="warning">' + esc(w) + '</p>').join('') : '') +
    '</div>';
}

function renderValidation(slot, payload) {
  slot.innerHTML = validationBanner(payload);
}

export function bindMetrics(refresh) {
  document.querySelector('#metric-form')?.addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = Object.fromEntries(new FormData(form));
    const button = form.querySelector('[type=submit]');
    button.disabled = true;
    try {
      const metric = await metrics.create(data);
      notify('口径草稿已建立，可先试算再发布');
      location.hash = '#/metrics/' + metric.id;
    } catch (error) {
      notify(error.chainText ? error.message + '（' + error.chainText + '）' : error.message, true);
    } finally {
      button.disabled = false;
    }
  });

  const draftForm = document.querySelector('#draft-form');
  const currentId = workspace.selectedMetric;
  const expressionOf = () => draftForm.querySelector('[name=expression]').value.trim();

  draftForm?.addEventListener('submit', async event => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(draftForm));
    try {
      await metrics.saveDraft(currentId, {
        expression: String(data.expression).trim(),
        note: data.note,
        expected_revision: Number(data.expected_revision),
      });
      notify('草稿已保存，可校验或试算');
      await refresh();
    } catch (error) {
      notify(error.chainText ? error.message + '（' + error.chainText + '）' : error.message, true);
    }
  });

  document.querySelector('#validate-metric')?.addEventListener('click', async () => {
    const slot = document.querySelector('#validation-slot');
    try {
      const payload = await metrics.validate(currentId, {expression: expressionOf(), note: ''});
      renderValidation(slot, payload);
      if (!payload.ok) notify(payload.chain_text ? payload.message + '（' + payload.chain_text + '）' : payload.message, true);
    } catch (error) {
      notify(error.message, true);
    }
  });

  document.querySelector('#publish-metric')?.addEventListener('click', async () => {
    const slot = document.querySelector('#validation-slot');
    try {
      await metrics.publish(currentId, {expected_revision: Number(draftForm?.querySelector('[name=expected_revision]').value)});
      notify('已发布，整条依赖固定到具体版本');
      await refresh();
    } catch (error) {
      if (slot) {
        slot.innerHTML = '<div class="validation error"><strong>发布被拒绝</strong><p>' + esc(error.message) + '</p>' +
          (error.chainText ? '<p class="chain-text">依赖链：' + esc(error.chainText) + '</p>' : '') + '</div>';
      }
      notify(error.chainText ? error.message + '（' + error.chainText + '）' : error.message, true);
    }
  });

  const disable = document.querySelector('#disable-metric');
  if (disable) disable.onclick = async () => {
    try {
      await metrics.setStatus(currentId, 'disabled');
      notify('口径已停用：阻止新分析，既有任务与分发保持可追溯');
      await refresh();
    } catch (error) { notify(error.message, true); }
  };
  const enable = document.querySelector('#enable-metric');
  if (enable) enable.onclick = async () => {
    try {
      await metrics.setStatus(currentId, 'active');
      notify('口径已重新启用');
      await refresh();
    } catch (error) { notify(error.message, true); }
  };

  const trialForm = document.querySelector('#trial-form');
  trialForm?.addEventListener('submit', async event => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(trialForm));
    const output = document.querySelector('#trial-output');
    output.innerHTML = '<p class="empty">正在按草稿依赖链试算…</p>';
    try {
      const payload = await metrics.trial(currentId, {
        dataset_id: Number(data.dataset_id),
        expression: expressionOf() || undefined,
        group_by: data.group_by,
        zero_policy: data.zero_policy,
      });
      renderTrial(output, payload);
    } catch (error) {
      output.innerHTML = '<div class="validation error"><strong>试算被拒绝</strong><p>' + esc(error.message) + '</p>' +
        (error.chainText ? '<p class="chain-text">依赖链：' + esc(error.chainText) + '</p>' : '') + '</div>';
      notify(error.message, true);
    }
  });
}
