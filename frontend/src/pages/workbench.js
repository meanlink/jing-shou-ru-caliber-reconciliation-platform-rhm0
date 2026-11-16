import {datasets,jobs,metrics} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {table} from '../components/common/table.js';
import {esc,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
export async function workbench(){
 const [list,metricsList]=await Promise.all([datasets.list(),metrics.list()]);
 workspace.datasets=list;workspace.metrics=metricsList;
 const m=workspace.meta,active=metricsList.filter(x=>x.status==='active'&&x.current_version);
 return heading('04 / ANALYSIS LAB',m.workbench_title,'选择业务批次和分析口径。口径以代码引用子口径，任务创建时固定整条依赖的版本，后续修改不影响既有任务。')+'<div class="workbench-grid"><section class="panel"><form id="job-form"><label>分析任务名称<input name="name" required minlength="2" value="'+esc(m.name)+' · 复核试算"></label><label>数据批次<select name="dataset_id" required>'+workspace.datasets.map(d=>'<option value="'+d.id+'">'+esc(d.name)+' · '+d.rows+' 条</option>').join('')+'</select></label><label>版本化口径<select name="metric_id" id="metric-select" '+(!active.length?'disabled':'')+'>'+(!active.length?'<option value="">没有可用口径，请先到口径登记簿发布</option>':active.map(metric=>'<option value="'+metric.id+'">'+esc(metric.name)+'（'+esc(metric.code)+'）v'+metric.current_version.version_no+' · '+esc(metric.current_version.unit)+'</option>').join(''))+'</select></label><p class="hint" id="metric-hint">'+(active.length?'任务将固定该口径当前发布版本及其全部子口径版本。':'先到 <a href="#/metrics">口径登记簿</a> 创建并发布口径。')+'</p><div class="parameter-grid">'+m.parameters.filter(p=>p.key!=='expression').map(p=>'<label>'+esc(p.label)+(p.type==='select'?'<select name="param-'+esc(p.key)+'">'+p.options.map(o=>'<option value="'+esc(o.value)+'" '+(o.value==p.default?'selected':'')+'>'+esc(o.label)+'</option>').join('')+'</select>':'<input type="number" step="any" name="param-'+esc(p.key)+'" value="'+esc(p.default)+'" min="'+esc(p.min??'')+'" max="'+esc(p.max??'')+'" required>')+'</label>').join('')+'</div><div id="metric-dependency"><p class="empty">选择口径后显示整条依赖与固定版本。</p></div><button type="submit" class="primary" '+(session.user.role==='viewer'||!workspace.datasets.length||!active.length?'disabled':'')+'>'+esc(m.action_label||'执行分析')+' ↗</button><p class="hint">提交后由后台任务计算。观察员可查看参数和结果，无法提交。已停用的口径不会出现在上方列表。</p></form></section><section class="method-sheet"><p class="eyebrow">METHOD / BUSINESS RULE</p><h2>'+esc(m.method)+'</h2><p>'+esc(m.tagline)+'</p><h3>业务判读边界</h3><ul>'+m.limitations.map(v=>'<li>'+esc(v)+'</li>').join('')+'</ul><div class="method-trace"><span>输入</span><strong>'+esc(m.dataset_name)+'</strong><span>输出</span><strong>可复核结果 / 固定口径版本 / 分发归档</strong></div></section></div>';
}
function renderDependency(metric){
 const box=document.querySelector('#metric-dependency');if(!box)return;
 if(!metric){box.innerHTML='<p class="empty">暂无可用口径。</p>';return;}
 box.innerHTML='<p class="hint">'+esc(metric.name)+'（'+esc(metric.code)+'）v'+metric.current_version.version_no+' 将固定以下依赖：</p>'+
  (metric.current_version.dependencies?.length?'<ol class="dependency-chain compact">'+metric.current_version.dependencies.map((dep,i)=>'<li><span class="chain-index">'+String(i+1).padStart(2,'0')+'</span><div><strong>'+esc(dep.name)+'</strong> <code>'+esc(dep.code)+'</code> <span class="version-tag">v'+dep.version_no+'</span><p>'+esc(dep.expression)+'</p></div></li>').join('')+'</ol>':'<p class="empty">该口径没有子口径依赖。</p>');
}
export function bindWorkbench(){
 const select=document.querySelector('#metric-select');
 select?.addEventListener('change',()=>renderDependency(workspace.metrics.find(x=>String(x.id)===select.value)));
 if(select?.value)renderDependency(workspace.metrics.find(x=>String(x.id)===select.value));
 submit(document.querySelector('#job-form'),async f=>{const params={};for(const p of workspace.meta.parameters){if(p.key==='expression')continue;const value=f.get('param-'+p.key);if(value!==null)params[p.key]=p.type==='number'?Number(value):value;}const metricId=f.get('metric_id');if(metricId)params.metric_id=Number(metricId);const j=await jobs.create({name:f.get('name'),dataset_id:Number(f.get('dataset_id')),params});workspace.selectedJob=j.id;notify('分析任务已提交，已固定口径 v'+(j.metric_version?.version_no??'—'));location.hash='#/results/'+j.id;});}
