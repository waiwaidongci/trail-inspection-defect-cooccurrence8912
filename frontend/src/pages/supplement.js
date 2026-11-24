import {BATCH, CHECKLIST} from '../constants/enums.js';
import {feedback} from '../components/common/fields.js';
import {empty, panel, pill} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {handler} from '../hooks/action.js';
import {session} from '../stores/session.js';
import {workspace} from '../stores/workspace.js';
import {button, el, p} from '../utils/dom.js';
import {date} from '../utils/format.js';

function evidenceText(item) {
  const e = item.evidence;
  return `${(e.antecedent || []).join('、')} → ${e.consequent}｜支持度 ${e.support_pct}% · 置信度 ${e.confidence_pct}% · 提升度 ${e.lift}`;
}

export async function supplementPage(route, refresh) {
  const error = feedback();
  const [batches, states, latest, versions, ledgers] = await Promise.all([
    workspace.supplementBatches(), workspace.segmentStates(), workspace.latestChecklist(),
    workspace.checklists(), workspace.ledgers(),
  ]);

  const downloadTemplate = button('下载补传模板（含版本号列）', {class: 'quiet'});
  downloadTemplate.addEventListener('click', handler(downloadTemplate, error,
    () => workspace.download('/supplement/template', '补传巡检模板.csv')));

  const submit = button('归档并重试算清单', {type: 'submit', class: 'primary', 'data-testid': 'supplement-submit'});
  const form = session.canWrite && el('form', {class: 'fields', onSubmit: handler(submit, error, async event => {
    const data = new FormData(event.target);
    const file = data.get('source');
    if (!file?.size) throw new Error('请选择一个补传 CSV 文件');
    if (file.size > 2_000_000) throw new Error('CSV 最多 2 MB');
    const idempotencyKey = (data.get('idempotency_key') || '').toString().trim();
    const result = await workspace.supplement({
      name: data.get('batch_name'), filename: file.name, csv_text: await file.text(),
      idempotency_key: idempotencyKey,
      params: {route_scope: data.get('route_scope') || '全部步道'},
    });
    if (result.status === 'failed') throw new Error(result.error || '补传失败，旧版清单已保留');
    await refresh();
  })},
    el('label', {class: 'field'}, el('span', {}, '补传批次名称'),
      el('input', {name: 'batch_name', required: true, maxlength: 120, placeholder: '例如 11-24 晚到补传'})),
    el('label', {class: 'field'}, el('span', {}, '幂等键（留空则按文件内容生成）'),
      el('input', {name: 'idempotency_key', maxlength: 80, placeholder: '重跑同一批请填相同键，避免重复出单'})),
    el('label', {class: 'field'}, el('span', {}, '试算步道范围'),
      el('input', {name: 'route_scope', value: '全部步道', placeholder: '全部步道或具体步道名称'})),
    el('label', {class: 'field'}, el('span', {}, '补传 CSV'),
      el('input', {name: 'source', type: 'file', accept: '.csv,text/csv', required: true})),
    submit, error);

  const batchTable = table([
    {key: 'name', label: '补传批次'},
    {label: '状态', render: row => pill(BATCH[row.status] || row.status, row.status)},
    {key: 'row_count', label: '行数'},
    {key: 'replaced_versions', label: '整份替换'},
    {key: 'skipped_versions', label: '跳过版本'},
    {key: 'changed_segments', label: '变动路段'},
    {label: '时间', render: row => date(row.created_at)},
    {label: '说明', render: row => row.error || (row.checklist_version_id ? `清单 v${row.checklist_version_id}` : '无变化未出新版本')},
  ], batches, {caption: '补传批次', empty: '还没有补传批次。', pageSize: 8});

  const checklistPanel = latest ? panel(
    `当前加查清单 v${latest.version_no}`,
    p(latest.summary, 'intro-copy'),
    pill(CHECKLIST[latest.status] || latest.status, latest.status),
    p(`范围 ${latest.scope}｜纳入路段 ${latest.total_segments}（无缺陷 ${latest.clean_segments}）｜达标规则 ${latest.rule_count}｜加查项 ${latest.item_count}`, 'subtle'),
    table([
      {key: 'route', label: '步道'},
      {key: 'segment_id', label: '路段'},
      {key: 'inspection_date', label: '最近巡检'},
      {key: 'consequent', label: '加查缺陷'},
      {key: 'action', label: '现场核验动作'},
      {label: '命中依据', render: evidenceText},
    ], latest.items, {caption: '逐段加查与命中依据', empty: '当前没有需要加查的路段，按原清单巡检。', pageSize: 10}),
  ) : panel('当前加查清单', empty('首次补传归档后生成第一版清单。'));

  const versionTable = table([
    {label: '版本', render: row => `v${row.version_no}`},
    {label: '状态', render: row => pill(CHECKLIST[row.status] || row.status, row.status)},
    {key: 'scope', label: '范围'},
    {key: 'rule_count', label: '规则数'},
    {key: 'item_count', label: '加查项'},
    {label: '生成时间', render: row => date(row.created_at)},
  ], versions, {caption: '清单版本（旧版整份保留）', empty: '暂无清单版本。', pageSize: 6});

  const stateTable = table([
    {key: 'route', label: '步道'},
    {key: 'segment_id', label: '路段'},
    {key: 'inspection_date', label: '生效巡检日期'},
    {key: 'version', label: '版本'},
    {label: '当前缺陷', render: row => (row.defects.length ? row.defects.join('、') : '无缺陷（计入分母）')},
  ], states, {caption: '路段当前生效状态（试算只重读变动路段）', empty: '暂无路段状态。', pageSize: 10});

  const ledgerTable = table([
    {key: 'route', label: '步道'},
    {key: 'segment_id', label: '路段'},
    {key: 'inspection_date', label: '巡检日期'},
    {key: 'version', label: '版本'},
    {label: '登记结果', render: row => (row.defects.length ? row.defects.join('、') : '无缺陷')},
    {label: '是否生效', render: row => (row.is_active ? pill('生效', 'active') : pill('已替换', 'superseded'))},
  ], ledgers, {caption: '晚到记录归档台账', empty: '暂无归档记录。', pageSize: 10});

  return el('div', {class: 'two-col'},
    el('div', {class: 'wide-panel'},
      checklistPanel,
      panel('补传批次与版本', batchTable, versionTable),
      panel('路段状态与归档', stateTable, ledgerTable)),
    el('aside', {class: 'sidebar-panel'},
      panel('补传巡检',
        p('晚到记录按巡检日期归档；同日更正用更高版本号整份替换。试算只重读有变动路段，清单与命中依据一起切换，失败保留整份旧版。', 'subtle'),
        downloadTemplate, form),
      panel('口径提示', el('ul', {class: 'method-list'}, [
        '同版本同内容重跑自动跳过，不重复出单；同版本内容不同会被拒绝，请提高版本号。',
        '无缺陷路段保留在支持度分母中，进出分母会带动其它路段规则达标与加查增撤。',
        '两批补传交错到达时按顺序串行处理，任一批失败都不影响已发布清单。',
      ].map(text => el('li', {}, text))))));
}
