'use strict';
const $ = id => document.getElementById(id);
const state = {overview:null, assets:[], selected:'NSE:RELIANCE', filter:'all', runs:[], forecast:null, series:[], view:'markets', interval:'5m', seriesRequest:0};
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = (value, digits=2) => value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('en-IN',{minimumFractionDigits:digits,maximumFractionDigits:digits});
const pct = (value, sign=false) => value == null ? '—' : `${sign && value>0?'+':''}${num(value*100)}%`;
const color = value => value == null || value===0 ? 'muted' : value>0?'positive':'negative';
const pretty = value => String(value ?? '').replaceAll('_',' ').toLowerCase();
const modelName = run => run?.model?.engine === 'timesfm_3.0' ? 'TimesFM 3.0' : 'Last-price baseline';
async function api(path, options={}) {
  const response = await fetch(path, options);
  const value = await response.json();
  if(!response.ok) throw new Error(typeof value.detail==='string'?value.detail:JSON.stringify(value.detail));
  return value;
}
function notice(message, error=false){$('notice').textContent=message;$('notice').classList.toggle('error',error);}
function setView(view){
  state.view=view;
  const content={markets:['Market explorer','A wider view of the market.','Explore the relationships. Test the forecasts.'],experiments:['Experiments','Turn a hypothesis into evidence.','Compare zero-shot forecasts on the same historical origins.'],coverage:['Data coverage','Know what the data can support.','Inspect observed coverage, timing and source limitations.']}[view];
  $('market-view').hidden=view!=='markets';$('experiment-view').hidden=view!=='experiments';$('coverage-view').hidden=view!=='coverage';
  $('view-name').textContent=content[0];$('page-title').textContent=content[1];$('page-subtitle').textContent=content[2];
  document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
  if(view==='markets') renderChart();
}
async function refresh(){
  $('refresh').disabled=true;
  try{
    state.overview=await api(`/api/overview?interval=${state.interval}`);state.assets=state.overview.assets;
    if(!state.assets.some(a=>a.asset_id===state.selected))state.selected=state.assets[0]?.asset_id;
    const demo=state.assets.some(a=>a.flags.includes('SYNTHETIC_DEMO'));
    notice(demo?'SYNTHETIC DEMO · All displayed prices and metrics are generated test data. They do not measure market forecasting accuracy.':`${state.interval} bars · ${state.overview.populated} / ${state.overview.registered} assets have observed history · ${state.overview.verified_timing} have fully verified publication timing. Public historical data is retrospective research; coverage and freshness vary by source.`);
    renderAssets();renderCards();renderCoverage();await loadRuns();await selectAsset(state.selected);
  }catch(error){notice(`Workspace could not load: ${error.message}`,true);}
  finally{$('refresh').disabled=false;}
}
function renderCards(){
  const ids=['NSE:NIFTY50','NSE:BANKNIFTY','COM:GOLD','COM:CRUDE'];
  $('market-cards').innerHTML=ids.map(id=>{
    const a=state.assets.find(a=>a.asset_id===id);if(!a)return '';
    return `<button class="market-card" data-asset="${esc(id)}"><span class="card-label">${esc(a.name.toUpperCase())} <span class="muted">${esc(a.currency)}</span></span><strong>${num(a.close)}</strong><span class="change ${color(a.change)}">${pct(a.change,true)} <span class="muted">last observed bar</span></span></button>`;
  }).join('');
}
function renderAssets(){
  const query=$('search').value.toLowerCase();
  const assets=state.assets.filter(a=>(state.filter==='all'||a.asset_class===state.filter||(state.filter==='indices'&&['india_index','global_index','fx'].includes(a.asset_class)))&&`${a.name} ${a.asset_id}`.toLowerCase().includes(query));
  $('asset-count').textContent=`${assets.length} assets`;
  $('asset-list').innerHTML=assets.length?assets.map(a=>`<button class="asset-row ${a.asset_id===state.selected?'selected':''}" data-asset="${esc(a.asset_id)}"><span><strong>${esc(a.asset_id.split(':')[1])}</strong><small>${esc(a.name)}</small></span><span class="asset-quote"><strong>${num(a.close)}</strong><span class="change ${color(a.change)}">${pct(a.change,true)}</span></span></button>`).join(''):'<p class="empty">No matching assets.</p>';
}
async function selectAsset(id){
  if(!id)return;state.selected=id;renderAssets();const request=++state.seriesRequest;
  try{const response=await api(`/api/series?asset_id=${encodeURIComponent(id)}&limit=${$('range').value}&interval=${state.interval}`);if(request!==state.seriesRequest)return;state.series=response.bars;renderChart();}
  catch(error){if(request===state.seriesRequest)$('chart').innerHTML=`<p class="empty">${esc(error.message)}</p>`;}
}
function renderChart(){
  const a=state.assets.find(a=>a.asset_id===state.selected);if(!a)return;
  const run=state.forecast;
  const predictions=run?.forecasts.filter(f=>f.asset_id===a.asset_id)??[];
  const bars=state.series.filter(b=>!predictions.length||Date.parse(b.available_at)<=Date.parse(run.cutoff));
  $('chart-meta').textContent=`${a.exchange} / ${pretty(a.asset_class)} / ${a.currency} per ${a.unit}${state.interval==='5m'?' / times in IST':''}`;
  const chartChange=bars.length>1&&bars.at(-2).close?bars.at(-1).close/bars.at(-2).close-1:null;
  $('chart-name').textContent=a.name;$('chart-price').textContent=num(bars.at(-1)?.close??a.close);$('chart-change').textContent=pct(chartChange,true);$('chart-change').className=`change ${color(chartChange)}`;
  $('chart-source').textContent=bars.length?`${bars.at(-1).source} · ${state.interval==='5m'?new Date(bars.at(-1).close_at).toLocaleString('en-GB',{timeZone:'Asia/Kolkata'}):bars.at(-1).session}`:'No observations collected';
  $('chart-forecast-note').textContent=predictions.length?`${modelName(run)} · T+${run.horizon} · ${run.lag_bars?'LAGGED '+run.lag_bars+' bars · ':''}${run.origin}`:'No forecast for this asset';
  if(!bars.length){$('chart').innerHTML='<p class="empty">No observed history for this asset. Check Data coverage for its source status.</p>';return;}
  const width=Math.max($('chart').clientWidth,340),height=Math.max($('chart').clientHeight,260);
  const pad={left:10,right:65,top:20,bottom:30};
  const futureSlots=predictions.length?Math.max(14,predictions.length+3):3;
  const dx=(width-pad.left-pad.right)/(bars.length+futureSlots);
  const all=bars.flatMap(b=>[b.low??b.close,b.high??b.close]).concat(predictions.flatMap(p=>[p.quantiles['0.1'],p.quantiles['0.9'],p.price]));
  const min=Math.min(...all),max=Math.max(...all),margin=Math.max((max-min)*.12,max*.003,0.01);
  const y=v=>pad.top+(max+margin-v)/(max-min+2*margin)*(height-pad.top-pad.bottom);
  const x=i=>pad.left+(i+.5)*dx;
  let svg=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(a.name)} historical candles and forecast intervals"><title>${esc(a.name)} prices in ${esc(a.currency)}</title>`;
  for(let i=0;i<5;i++){
    const value=min-margin+(max-min+2*margin)*i/4,cy=y(value);
    svg+=`<line x1="${pad.left}" x2="${width-pad.right}" y1="${cy}" y2="${cy}" stroke="#21303b" stroke-dasharray="3 5"/><text x="${width-pad.right+9}" y="${cy+3}" fill="#6f8293" font-size="9">${num(value,value<10?2:0)}</text>`;
  }
  const candleWidth=Math.max(1,dx*.65);
  bars.forEach((b,i)=>{
    const open=b.open??b.close,high=b.high??b.close,low=b.low??b.close,col=b.close>=open?'#78bfa7':'#d17d83';
    svg+=`<g data-candle="${i}"><line x1="${x(i)}" x2="${x(i)}" y1="${y(high)}" y2="${y(low)}" stroke="${col}"/><rect x="${x(i)-candleWidth/2}" y="${Math.min(y(open),y(b.close))}" width="${candleWidth}" height="${Math.max(1,Math.abs(y(open)-y(b.close)))}" fill="${col}"/><rect x="${x(i)-dx/2}" y="0" width="${dx}" height="${height-pad.bottom}" fill="transparent"/></g>`;
  });
  for(let i=0;i<bars.length-(predictions.length?Math.ceil(bars.length*.1):0);i+=Math.max(1,Math.floor(bars.length/(width<500?3:5))))svg+=`<text x="${x(i)}" y="${height-8}" fill="#6f8293" font-size="9">${esc(state.interval==='5m'?new Date(bars[i].close_at).toLocaleString('en-GB',{timeZone:'Asia/Kolkata',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}):bars[i].session.slice(5))}</text>`;
  if(predictions.length){
    const start=x(bars.length-1),last=bars.at(-1).close,end=width-pad.right-8;
    const fx=i=>start+(end-start)*(i+1)/predictions.length;
    const upper=[[start,y(last)],...predictions.map((p,i)=>[fx(i),y(p.quantiles['0.9'])])];
    const lower=predictions.map((p,i)=>[fx(i),y(p.quantiles['0.1'])]).reverse();
    svg+=`<polygon points="${[...upper,...lower].map(p=>p.join(',')).join(' ')}" fill="#729edb" opacity=".14"/>`;
    svg+=`<line x1="${start}" x2="${start}" y1="${pad.top}" y2="${height-pad.bottom}" stroke="#567496" stroke-dasharray="4 4"/>`;
    svg+=`<polyline points="${[[start,y(last)],...predictions.map((p,i)=>[fx(i),y(p.price)])].map(p=>p.join(',')).join(' ')}" fill="none" stroke="#91b9ef" stroke-width="2" stroke-dasharray="4 3"/>`;
    svg+=`<text x="${end-22}" y="${height-8}" fill="#91b9ef" font-size="9">T+${run.horizon}</text>`;
  }
  $('chart').innerHTML=svg+'</svg>';
  $('chart').onpointermove=event=>{const node=event.target.closest('[data-candle]');if(!node)return;const b=bars[Number(node.dataset.candle)];$('chart-tooltip').textContent=`${state.interval==='5m'?new Date(b.close_at).toLocaleString('en-GB',{timeZone:'Asia/Kolkata'}):b.session} · O ${num(b.open)} H ${num(b.high)} L ${num(b.low)} C ${num(b.close)} · Volume ${num(b.volume,0)}`;};
}
function renderRankings(){
  const run=state.forecast;if(!run){$('rankings').innerHTML='<p class="empty">Run a forecast to see relative stock returns.</p>';$('ranking-meta').textContent='No forecast yet';return;}
  $('ranking-meta').textContent=`${modelName(run)} · ${state.interval} · T+${run.horizon}${run.lag_bars?' · LAGGED '+run.lag_bars+' bars':''}`;
  $('rankings').innerHTML=`<table><thead><tr><th>RANK / STOCK</th><th>FORECAST</th><th>RETURN</th><th>10–90% RANGE</th></tr></thead><tbody>${run.ranking.map((r,i)=>`<tr><td><button class="text-button" data-asset="${esc(r.asset_id)}">${String(i+1).padStart(2,'0')} &nbsp; ${esc(r.asset_id.split(':')[1])}</button></td><td>${num(r.price)} <small>${esc(r.currency)}</small></td><td class="${color(r.return)}">${pct(r.return,true)}</td><td>${num(r.quantiles['0.1'])} – ${num(r.quantiles['0.9'])}</td></tr>`).join('')}</tbody></table>`;
}
function renderCoverage(){
  $('coverage-summary').textContent=`${state.interval} · ${state.overview.populated} populated / ${state.overview.registered} registered`;
  $('coverage-table').innerHTML=`<table><thead><tr><th>ASSET</th><th>SOURCE STATUS</th><th>OBSERVATIONS</th><th>OBSERVED SPAN</th><th>RECENT 128 GAPS / AGE</th><th>PROVENANCE / LIMITATIONS</th></tr></thead><tbody>${state.assets.map(a=>`<tr><td>${esc(a.name)}<small>${esc(a.asset_id)} · ${esc(a.currency)} / ${esc(a.unit)}</small></td><td>${esc(pretty(a.collection.status))}${a.collection.error?`<small>${esc(a.collection.error)}</small>`:''}</td><td>${num(a.observations,0)}</td><td>${esc(a.start??'—')}<small>to ${esc(a.end??'—')}</small></td><td>${a.missing_recent_128??'—'} missing<small>${a.age_hours==null?'—':`${num(a.age_hours,1)} h`}</small></td><td>${a.flags.length?a.flags.map(f=>`<span class="flag">${esc(pretty(f))}</span>`).join(''):'Verified timing'}${a.series_kind==='continuous_future'?'<small>USD futures proxy · not MCX</small>':''}</td></tr>`).join('')}</tbody></table>`;
}
async function loadRuns(){
  state.runs=await api(`/api/runs?interval=${state.interval}`);
  const latest=state.runs.find(r=>r.kind==='forecast');state.forecast=latest?await api(`/api/runs/${latest.run_id}`):null;
  renderRankings();
  $('run-list').innerHTML=state.runs.length?state.runs.map(r=>`<button class="run-row" data-run="${esc(r.run_id)}"><span class="badge subtle">${esc(r.kind)}</span><strong>${r.kind==='comparison'?'Controlled comparison':modelName(r)}</strong><p>${esc(pretty(r.experiment??'all experiments'))} · ${esc(r.data_mode)}<br>${esc(r.created_at.slice(0,19).replace('T',' '))} UTC${r.completed_folds!=null?` · ${r.completed_folds}/${r.scheduled_folds} folds`:''}</p></button>`).join(''):'<p class="empty">No runs yet. Start with the last-price baseline to check the workflow, then run TimesFM.</p>';
}
function metricCards(m){return `<div class="metric-grid">${[['Return MAE',pct(m.return_mae)],['Directional hit rate',pct(m.directional_hit_rate)],['Mean ranking IC',num(m.rank_ic,4)],['80% interval coverage',pct(m.interval_80_coverage)]].map(([label,value])=>`<div class="metric"><span>${label}</span><strong>${value}</strong></div>`).join('')}</div>`;}
async function showRun(id){
  try{
    const run=await api(`/api/runs/${id}`);$('result-title').textContent=run.kind==='comparison'?'Controlled comparison':`${modelName(run)} · ${pretty(run.experiment)}`;
    $('download-run').href=`/api/runs/${encodeURIComponent(id)}`;$('download-run').download=`${id}.json`;$('download-run').hidden=false;
    let html='';
    if(run.kind==='comparison'){
      html=`<p class="detail-note">${esc(run.interpretation)} ${run.common_origins.length} common origins.</p><div class="table-wrap"><table><thead><tr><th>MODEL / EXPERIMENT</th><th>RETURN MAE</th><th>DIRECTION</th><th>RANK IC</th><th>80% COVERAGE</th></tr></thead><tbody>${run.comparisons.map(c=>`<tr><td>${esc(c.model)}<small>${esc(pretty(c.experiment))}</small></td><td>${pct(c.metrics.return_mae)}</td><td>${pct(c.metrics.directional_hit_rate)}</td><td>${num(c.metrics.rank_ic,4)}</td><td>${pct(c.metrics.interval_80_coverage)}</td></tr>`).join('')}</tbody></table></div>`;
    }else if(run.kind==='backtest'){
      html=metricCards(run.metrics)+`<p class="detail-note">${run.completed_folds} completed folds · ${run.metrics.observations} equity outcomes at T+${run.horizon}. Reserved history: ${esc(run.holdout.start)} to ${esc(run.holdout.end)}; not evaluated in this run.</p>`;
      const ids=[...new Set(run.records.map(r=>r.asset_id))];
      if(ids.length)html+=`<label class="small">Actual vs predicted close · <select id="result-asset" aria-label="Evaluation chart asset">${ids.map(a=>`<option>${esc(a)}</option>`).join('')}</select></label><div id="result-chart" class="run-chart"></div>`;
      if(run.skipped.length)html+=`<p class="detail-note">Skipped folds: ${run.skipped.map(s=>`${esc(s.origin)}: ${esc(s.reason)}`).join('<br>')}</p>`;
    }else{
      state.forecast=run;renderRankings();renderChart();html=`<p class="detail-note">${run.targets.length} targets · ${run.covariates.length} past-only covariates · ${run.context} context bars · ${run.horizon} forecast bars (${run.interval??'1d'}).<br>Origin: ${esc(run.origin)} · Input data available by ${esc(run.cutoff)}. ${run.lag_bars?`Lagged inputs: ${run.lag_bars} observed bars behind the latest panel.`:''}</p><button class="text-button" id="view-forecast">View forecast charts and stock rankings →</button>`;
    }
    html+=`<div class="result-meta">Data mode: ${esc(run.data_mode)} · Research only · Promotion disabled${run.model?.revision?`<br>Checkpoint revision: <code>${esc(run.model.revision)}</code>`:''}${run.input_sha256?`<br>Input SHA-256: <code>${esc(run.input_sha256)}</code>`:''}</div><p class="detail-note">${(run.limitations??[]).map(esc).join('<br>')}</p>`;
    $('run-result').innerHTML=html;
    if($('view-forecast'))$('view-forecast').onclick=()=>setView('markets');
    if($('result-asset')){const render=()=>renderEvaluation(run,$('result-asset').value);$('result-asset').onchange=render;render();}
  }catch(error){$('run-result').innerHTML=`<p class="empty">${esc(error.message)}</p>`;}
}
function renderEvaluation(run,asset){
  const rows=run.records.filter(r=>r.asset_id===asset&&r.step===run.horizon),width=Math.max($('result-chart').clientWidth,300),height=230;
  const values=rows.flatMap(r=>[r.price,r.actual_price]),min=Math.min(...values),max=Math.max(...values),span=Math.max(max-min,1);
  const x=i=>50+i*(width-120)/Math.max(rows.length-1,1),y=v=>25+(max-v)/span*155;
  let svg=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Actual versus predicted ${esc(asset)} close"><text x="50" y="15" fill="#88d9ba" font-size="10">Actual</text><text x="110" y="15" fill="#91b9ef" font-size="10">Predicted · ${esc(rows[0]?.currency??'')}</text>`;
  for(const [key,col] of [['actual_price','#88d9ba'],['price','#91b9ef']]){
    svg+=`<polyline points="${rows.map((r,i)=>`${x(i)},${y(r[key])}`).join(' ')}" fill="none" stroke="${col}" stroke-width="2"/>`;
    rows.forEach((r,i)=>svg+=`<circle cx="${x(i)}" cy="${y(r[key])}" r="3" fill="${col}"><title>${esc(r.target_session)}: ${num(r[key])}</title></circle>`);
  }
  rows.forEach((r,i)=>{if(i%Math.max(1,Math.ceil(rows.length/6))===0)svg+=`<text x="${x(i)-20}" y="212" fill="#8295a7" font-size="9">${esc(r.target_session)}</text>`;});
  svg+=`<text x="${width-60}" y="30" fill="#8295a7" font-size="9">${num(max,0)}</text><text x="${width-60}" y="180" fill="#8295a7" font-size="9">${num(min,0)}</text>`;
  $('result-chart').innerHTML=svg+'</svg>';
}
async function submitRun(event){
  event.preventDefault();$('run-button').disabled=true;$('job-status').textContent='Checking data and starting the research job…';
  const request={interval:state.interval,latest_complete:$('latest-complete').checked,kind:$('run-kind').value,model:$('run-model').value,experiment:$('run-experiment').value,profile:$('run-profile').value,context:Number($('run-context').value),horizon:Number($('run-horizon').value),folds:Number($('run-folds').value),allow_retrospective:$('retrospective').checked};
  try{
    const job=await api('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)});
    localStorage.setItem('timesfm-job',job.job_id);pollJob(job.job_id);
  }catch(error){$('job-status').textContent=error.message;$('run-button').disabled=false;}
}
async function pollJob(id){
  try{
    const job=await api(`/api/jobs/${id}`);
    if(['QUEUED','RUNNING'].includes(job.status)){$('job-status').textContent=`${pretty(job.status)} · CPU inference can take a few minutes. You can keep browsing.`;$('run-button').disabled=true;setTimeout(()=>pollJob(id),2500);return;}
    localStorage.removeItem('timesfm-job');$('run-button').disabled=false;
    $('job-status').textContent=job.error??`${pretty(job.status)} · Results saved with source and checkpoint provenance.`;
    await loadRuns();if(job.run_id)await showRun(job.run_id);
  }catch(error){$('job-status').textContent=`Job status unavailable: ${error.message}`;$('run-button').disabled=false;localStorage.removeItem('timesfm-job');}
}
document.addEventListener('click',event=>{
  const asset=event.target.closest('[data-asset]');if(asset){selectAsset(asset.dataset.asset);setView('markets');}
  const view=event.target.closest('[data-view]');if(view)setView(view.dataset.view);
  const filter=event.target.closest('[data-filter]');if(filter){state.filter=filter.dataset.filter;document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('selected',b===filter));renderAssets();}
  const run=event.target.closest('[data-run]');if(run)showRun(run.dataset.run);
});
$('interval').onchange=()=>{state.interval=$('interval').value;state.forecast=null;state.series=[];$('chart-interval').textContent=state.interval;refresh();};
$('search').oninput=renderAssets;$('range').onchange=()=>selectAsset(state.selected);$('refresh').onclick=refresh;
$('open-experiments').onclick=()=>setView('experiments');$('refresh-runs').onclick=()=>loadRuns().catch(e=>notice(e.message,true));
$('run-form').onsubmit=submitRun;
$('run-model').onchange=()=>{if($('run-model').value==='naive'){$('run-experiment').value='stock_only';if($('run-kind').value==='compare')$('run-kind').value='backtest';}};
$('run-kind').onchange=()=>{if($('run-kind').value==='compare')$('run-model').value='timesfm';};
new ResizeObserver(()=>{if(state.view==='markets')renderChart();}).observe($('chart'));
refresh();const pendingJob=localStorage.getItem('timesfm-job');if(pendingJob)pollJob(pendingJob);
