import {Fragment, ReactNode, useEffect, useRef, useState} from 'react';
import {api,useData} from './api';
import {Login,Tasks,Approvals,Team,Settings,Audit,User} from './Access';

const num=(n:number|undefined)=>n==null?'—':n.toLocaleString(undefined,{maximumFractionDigits:2});
const money=(n:number|undefined)=>n==null?'—':n.toLocaleString('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0});
const pct=(n:number|undefined)=>n==null?'—':`${(n*100).toFixed(1)}%`;
const label=(s:string)=>s.replaceAll('_',' ');
function Icon({name}:{name:string}){return <i className={'ti ti-'+name} aria-hidden="true"/>}
function Badge({children,warn=false}:{children:ReactNode,warn?:boolean}){return <span className={'chip '+(warn?'c-warn':'c-ok')}>{children}</span>}
function Card({title,note,children}:{title:string,note?:string,children:ReactNode}){return <section className="card"><div className="card-h"><div><h3>{title}</h3>{note&&<p>{note}</p>}</div></div><div className="card-b">{children}</div></section>}
function Tiles({items}:{items:[string,string,string][]}){return <div className="tiles">{items.map(([title,value,note])=><div className="tile" key={title}><div className="l">{title}</div><div className="v">{value}</div><div className="d neutral">{note}</div></div>)}</div>}
function Status({error}:{error?:string}){return <div className="empty-state" role={error?'alert':'status'}>{error||'Loading the latest completed run…'}</div>}

type Series={label:string,values:number[],color:string};
function Chart({series,labels,band,title}:{series:Series[],labels:string[],band?:[number,number][],title:string}){
  const [hover,setHover]=useState<number|null>(null);
  if(!labels.length)return <p>No chart data available.</p>;
  const W=620,H=220,L=44,R=18,T=20,B=30;
  const max=Math.max(1,...series.flatMap(s=>s.values),...(band?.flat()||[]))*1.08;
  const x=(i:number)=>L+(W-L-R)*i/Math.max(1,labels.length-1),y=(v:number)=>T+(H-T-B)*(1-v/max);
  return <div className="chart-wrap"><svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title}>
    {[0,.5,1].map(f=><g key={f}><line x1={L} x2={W-R} y1={y(max*f)} y2={y(max*f)} stroke="var(--line)"/><text x={L-6} y={y(max*f)+4} textAnchor="end" fontSize="11" fill="var(--faint)">{num(max*f)}</text></g>)}
    {band&&<polygon points={[...band.map((v,i)=>`${x(i)},${y(v[1])}`),...band.map((v,i)=>`${x(i)},${y(v[0])}`).reverse()].join(' ')} fill="var(--brand-soft)"/>}
    {series.map(s=><g key={s.label}><polyline points={s.values.map((v,i)=>`${x(i)},${y(v)}`).join(' ')} fill="none" stroke={s.color} strokeWidth="2"/>{s.values.map((v,i)=><circle key={i} cx={x(i)} cy={y(v)} r="3" fill="var(--panel)" stroke={s.color} tabIndex={0} aria-label={`${labels[i]}: ${s.label} ${num(v)}`} onFocus={()=>setHover(i)} onBlur={()=>setHover(null)} onMouseEnter={()=>setHover(i)} onMouseLeave={()=>setHover(null)}><title>{labels[i]} · {s.label}: {num(v)}</title></circle>)}</g>)}
    <text x={L} y={H-5} fill="var(--faint)" fontSize="11">{labels[0]}</text><text x={W-R} y={H-5} fill="var(--faint)" fontSize="11" textAnchor="end">{labels.at(-1)}</text>
  </svg><div className="chart-readout" aria-live="polite">{hover!==null?`${labels[hover]} · ${series.map(s=>s.label+': '+num(s.values[hover])).join(' · ')}`:title}</div><div className="legend">{series.map(s=><span key={s.label}><i style={{background:s.color}}/>{s.label}</span>)}{band&&<span>P10–P90 interval</span>}</div></div>
}

function MathPanel({item}:{item:any}){
  const c=item.calculation,b=c.breakdown,fc=item.forecast||[],sales=new Map(item.sales.map((r:any)=>[r.date.slice(0,10),r.qty_sold]));
  return <div className="tech">
    <div className="tbox"><h4>Daily sales and held-out forecast</h4><Chart title="Recorded daily sales and forecast range" labels={fc.map((r:any)=>r.date.slice(5,10))} band={fc.map((r:any)=>[r.q10_cal,r.q90_cal])} series={[{label:'Sales',values:fc.map((r:any)=>Number(sales.get(r.date.slice(0,10))||0)),color:'var(--ink)'},{label:'Forecast P50',values:fc.map((r:any)=>r.q50),color:'var(--brand-2)'}]}/></div>
    <div className="tbox"><h4>Evidence and forecast drivers</h4><ul className="evid">{item.finding?.evidence.map((e:any,i:number)=><li key={i} title={e.source_ref}><Icon name="point-filled"/>{num(e.value)} {e.unit} · {label(e.source_tool)}</li>)}</ul>{item.drivers?.length?<ul>{item.drivers.map((d:any,i:number)=><li key={i}>{typeof d==='string'?d:`${d.label}: ${num(d.value)}`}</li>)}</ul>:<p className="muted">SHAP drivers are available after model artifacts are loaded.</p>}<div className="checks">{item.checks?.length?item.checks.map((check:any)=><Badge key={check.name} warn={check.status==='escalate'}>{label(check.name)}</Badge>):<Badge>No exception flags</Badge>}</div></div>
    <div className="tbox full"><h4>Order math · deterministic engine</h4><div className="omath">{[['Demand target',num(c.target_qty)],['Critical ratio',pct(b.cr)],['Book / estimated stock',`${num(b.book_on_hand)} / ${num(c.estimated_on_hand)}`],['Case pack',num(c.case_pack)],['Recommendation',`${num(c.order_units)} units · ${num(c.order_cases)} ${c.calc_type==='bake'?'bakes':'cases'}`]].map(([k,v])=><div key={k}><small>{k}</small><b>{v}</b></div>)}</div><p className="idl provenance">{c.calc_id}</p>{item.yield_plan&&<details><summary>Yield and surplus plan</summary><pre>{JSON.stringify(item.yield_plan,null,2)}</pre></details>}</div>
  </div>
}

function Today({summary,revision,go}:{summary:any,revision:number,go:(s:string,dept?:string)=>void}){
  const {data:depts}=useData('departments',revision),{data:feed}=useData('activity?limit=8',revision),{data:catalog}=useData('catalog/summary',revision);
  return <><div className="hero"><div><div className="kick"><Icon name="circle-check"/>Completed run · {summary.date}</div><h2>Your agents reviewed <em>{num(summary.items)} items</em> today. They handled <em>{num(summary.auto_approved)}</em> on their own, and <em>{num(summary.pending)} need</em> your call.</h2><div className="cta"><button className="btn-hero" onClick={()=>go('Decisions')}>Review {summary.pending} decisions <Icon name="arrow-right"/></button><button className="btn-ghost" onClick={()=>go('Agent review')}>See what they handled</button></div></div><div className="agents">{[['shopping-cart','Ordering agent',`${num(summary.items)} orders prepared`],['eye-search','Shelf check agent',`${num(summary.pending)} manager decisions waiting`],['list-check','Catalog agent',`${num(catalog?.auto_merged)} listings linked automatically`]].map(([icon,title,note])=><div className="agent-row" key={title}><div className="ic"><Icon name={icon}/></div><div><b>{title}</b><small>{note}</small></div><span className="st">Done</span></div>)}</div></div>
    <Tiles items={[["Lower waste rate",pct(summary.kpis.waste_reduction),'Relative to ordering baseline'],['Lost sales avoided',money(summary.kpis.lost_sales_avoided),'Synthetic 28-day backtest'],['Margin added',money(summary.kpis.margin_added),'Versus baseline over holdout'],['In-stock product days',pct(summary.kpis.in_stock),'Measured in the backtest']]}/>
    <div className="cols"><div className="stack"><Card title="Departments" note="Today's order status by department"><div className="depts">{depts?.map((d:any)=><button className="dept" key={d.dept_id} onClick={()=>go('Agent review',d.dept_id)}><div className="dept-top"><Icon name={deptIcon(d.dept_id)}/><Badge warn={d.pending>0}>{d.pending?`${d.pending} to review`:d.sent?'Sent':'Ready'}</Badge></div><b>{d.name}</b><div className="meta">{num(d.items)} items · {money(d.value)}</div><div className="meter"><span style={{width:`${(d.items-d.pending)/d.items*100}%`}}/></div></button>)}</div></Card><Card title="Food waste cost per week" note="Recorded synthetic history · all fresh departments"><Chart title="Historical weekly waste cost ($)" labels={summary.weekly_waste.map((r:any)=>r.date.slice(0,10))} series={[{label:'Waste cost',values:summary.weekly_waste.map((r:any)=>r.value),color:'var(--brand-2)'}]}/></Card></div><Card title="Agent activity" note="Recorded activity from the completed run"><ul className="feed">{feed?.map((r:any)=><li key={r.run_id}><div className="ic c-ok"><Icon name="check"/></div><div><b>{r.name}</b><p>{label(r.agent)} · {label(r.outcome)}</p></div></li>)}</ul></Card></div></>
}
function deptIcon(dept:string){return ({produce:'carrot',meat_seafood:'meat',bakery:'bread',deli:'cheese',dairy_eggs:'egg'} as Record<string,string>)[dept]||'box'}

const overrideReasons=['Shelf is already stocked','Forecast looks too high/low','Supplier issue','Discontinued','Other'];
function OverrideForm({cases,unit,onSave,onReject}:{cases:number,unit:string,onSave:(body:any)=>Promise<void>,onReject:(body:any)=>Promise<void>}){
  const [qty,setQty]=useState(cases),[reason,setReason]=useState(''),[note,setNote]=useState(''),[counted,setCounted]=useState(''),[busy,setBusy]=useState(false);
  const body=()=>({cases:qty,reason,note,counted_qty:counted===''?null:Number(counted)});
  const run=async(fn:(body:any)=>Promise<void>)=>{setBusy(true);try{await fn(body())}finally{setBusy(false)}};
  return <div className="manager-box"><b>Agent got this wrong?</b><div className="stepper"><button aria-label="Fewer cases" onClick={()=>setQty(Math.max(0,qty-1))}>−</button><output>{qty} {unit}</output><button aria-label="More cases" onClick={()=>setQty(qty+1)}>+</button></div><select aria-label="Override reason" value={reason} onChange={e=>setReason(e.target.value)}><option value="">Choose a reason…</option>{overrideReasons.map(r=><option key={r}>{r}</option>)}</select><input aria-label="Counted quantity" type="number" min="0" placeholder="Counted quantity (optional)" value={counted} onChange={e=>setCounted(e.target.value)}/><input aria-label="Override note" placeholder="Note (optional)" value={note} onChange={e=>setNote(e.target.value)}/><div className="acts"><button className="btn pri" disabled={!reason||busy} onClick={()=>run(onSave)}>Save change</button><button className="btn danger" disabled={!reason||busy} onClick={()=>run(onReject)}>Don’t order</button></div></div>
}

function DecisionRejection({onReject}:{onReject:(body:any)=>Promise<void>}){
  const [reason,setReason]=useState(''),[note,setNote]=useState(''),[busy,setBusy]=useState(false);
  return <form className="decision-rejection adjust on" onSubmit={async e=>{
    e.preventDefault();if(!reason||busy)return;setBusy(true);
    try{await onReject({reason,note:note.trim()})}finally{setBusy(false)}
  }}>
    <fieldset disabled={busy}><legend>Why not? Your reason is logged as feedback.</legend>
      <div className="pick">{overrideReasons.map(r=><button type="button" key={r} className={reason===r?'on':''} aria-pressed={reason===r} onClick={()=>setReason(r)}>{r}</button>)}</div>
    </fieldset>
    <input className="rej-note" aria-label="Rejection note" placeholder="Note (optional)" value={note} onChange={e=>setNote(e.target.value)}/>
    <button className="btn warn" type="submit" disabled={!reason||busy}>Confirm, don’t order</button>
  </form>
}

function Decision({d,action,ask}:{d:any,action:(path:string,body?:any)=>Promise<void>,ask:(q:string,pid?:string)=>void}){
  const [editing,setEditing]=useState(false),[rejecting,setRejecting]=useState(false),[cases,setCases]=useState(d.cases),[reason,setReason]=useState(''),[busy,setBusy]=useState(false);
  const finding=d.finding,product=d.item.product,calc=d.item.calculation,unit=calc.calc_type==='bake'?'units':'cases';
  const sellUnit=product.sell_unit||'units';
  const outside=cases<d.recommended_cases*(d.override_band?.override_low??.6)||cases>d.recommended_cases*(d.override_band?.override_high??1.5);
  const confidence=({high:3,medium:2,low:1} as Record<string,number>)[finding.confidence]||0;
  const tag=({phantom_stock:'Shelf check needed',shrink_spike:'Waste needs a closer look',order_swing:'Order changed',low_confidence_match:'Check the product match',yield_plan_change:'Prep plan changed',bake_plan_change:'Bake plan changed'} as Record<string,string>)[finding.exception_type]||'Needs your review';
  const tint=({produce:'brand',meat_seafood:'tomato',bakery:'gold',deli:'plum',dairy_eggs:'sky'} as Record<string,string>)[product.dept_id]||'gold';
  const submit=async(kind:string,body:any={})=>{setBusy(true);try{await action(`decisions/${d.id}/${kind}`,body)}finally{setBusy(false)}};
  if(d.status==='approved'||d.status==='rejected'){
    const adjusted=d.cases!==d.recommended_cases;
    const amount=`${num(d.cases)} ${unit} (${num(d.cases*calc.case_pack)} ${sellUnit})`;
    const result=d.status==='rejected'
      ?`Not ordered · ${d.override_reason}${d.override_note?` · ${d.override_note}`:''}${d.counted_qty!=null?` · Counted ${num(d.counted_qty)} ${sellUnit}`:''} · logged as feedback for the agent`
      :adjusted?`Adjusted to ${amount} · your change is logged as feedback`:`Approved · ${amount} added to the order`;
    return <article className="dec"><div className="done-row"><Icon name={d.status==='rejected'?'circle-x':'circle-check'}/><div className="grow-title"><b>{product.name}</b><p>{result}</p></div><button className="btn" disabled={busy} onClick={()=>submit('undo')}><Icon name="arrow-back-up"/>Undo</button></div></article>;
  }
  return <article className="dec">
    <div className="dec-main">
      <div>
        <div className="dec-head"><div className={'thumb decision-tint-'+tint}><Icon name={deptIcon(product.dept_id)}/></div>
          <div><h3>{product.name}</h3><div className="sub">{label(product.dept_id)}</div><Badge warn>{tag}</Badge></div>
        </div>
        <p className="noticed">{finding.summary}</p>
        <ul className="reasons">{finding.reasons?.map((r:string,i:number)=><li key={i}><Icon name={['chart-line','package','clipboard-check'][i%3]}/>{r}</li>)}</ul>
      </div>
      <div className="rec">
        <small>Recommendation</small>
        <div className="big" title={calc.calc_id}>{num(d.recommended_cases)} {unit} <span>· {num(d.recommended_cases*calc.case_pack)} {sellUnit}</span></div>
        {finding.precondition&&<div className="pre"><Icon name="info-circle"/>{finding.precondition}</div>}
        <div className="impact">
          <span>{finding.estimated_impact_usd!=null?<><b>{money(finding.estimated_impact_usd)}</b> estimated impact</>:'Impact not estimated for this item'}</span>
          <span>Confidence <span className="conf" aria-hidden="true">{[1,2,3].map(n=><i key={n} className={n<=confidence?'f':''}/>)}</span> {finding.confidence?finding.confidence[0].toUpperCase()+finding.confidence.slice(1):'Unavailable'}</span>
        </div>
        {d.status==='needs_category_approval'&&<p className="guard bad">Pending admin approval. The requested quantity is held from sending. An admin can resolve it in Approvals.</p>}
        <div className="acts">
          <button className="btn pri" disabled={busy||d.status==='needs_category_approval'} onClick={()=>submit('approve')}><Icon name="check"/>Approve</button>
          <button className="btn" disabled={busy} aria-expanded={editing} onClick={()=>{setEditing(!editing);setRejecting(false)}}><Icon name="adjustments"/>Adjust</button>
          <button className="btn warn" disabled={busy} aria-expanded={rejecting} onClick={()=>{setRejecting(!rejecting);setEditing(false)}}><Icon name="x"/>Don’t order</button>
          <button className="btn" onClick={()=>ask(`Why is ${product.name} flagged?`,product.product_id)}><Icon name="message-question"/>Ask why</button>
        </div>
        {rejecting&&<DecisionRejection onReject={b=>submit('reject',b)}/>}
        {editing&&<div className="adjust on">
          <div className="stepper"><button aria-label="Fewer cases" disabled={busy} onClick={()=>setCases(Math.max(0,cases-1))}>−</button><output>{cases} {unit==='cases'?'cs':unit}</output><button aria-label="More cases" disabled={busy} onClick={()=>setCases(cases+1)}>+</button></div>
          <button className="btn pri" disabled={busy||(outside&&!reason.trim())} onClick={()=>submit('adjust',{cases,reason})}>Save</button>
          <input aria-label="Adjustment reason" value={reason} placeholder={outside?'Reason required':'Optional reason'} onChange={e=>setReason(e.target.value)}/>
          <p className={'guard '+(outside?'bad':'good')}>{outside?'Outside the policy band: requires a reason and admin approval.':'Within the policy band.'}</p>
        </div>}
      </div>
    </div>
    <details className="decision-math">
      <summary className="math-toggle"><span>Forecast, drivers and order math</span><span className="math-link">See the math <Icon name="chevron-down"/></span></summary>
      <MathPanel item={d.item}/>
    </details>
  </article>
}
function Decisions({revision,action,ask,target,go}:{revision:number,action:any,ask:any,target:string,go:(page:string)=>void}){
  const {data,error}=useData('decisions',revision),focused=useRef('');
  useEffect(()=>{
    if(!data||!target||focused.current===target)return;
    const card=document.getElementById('decision-'+target);
    if(card){card.focus({preventScroll:true});card.scrollIntoView({block:'center'});focused.current=target}
  },[data,target]);
  if(!data)return <Status error={error}/>;
  const pending=data.filter((d:any)=>['pending','needs_category_approval'].includes(d.status)).length;
  return <>
    <button className="btn" onClick={()=>go('Shelf checks')}>Assign or review shelf checks</button><div className="inbox-top decision-intro">
      <p>Your agents handled everything routine. These are the few calls where your eyes on the shelf make the difference. Each one explains what was noticed and why.</p>
      <div className="progress"><span>{data.length-pending} of {data.length} done</span><div className="meter" role="progressbar" aria-label="Decisions completed" aria-valuemin={0} aria-valuemax={data.length||1} aria-valuenow={data.length-pending}><span style={{width:`${data.length?(data.length-pending)/data.length*100:100}%`}}/></div></div>
    </div>
    <div className="decisions">
      {!pending&&<div className="empty-state"><Icon name="circle-check"/><h3>You’re all caught up</h3><p>Every decision is complete. Review and send your orders from Orders.</p><button className="btn pri go-orders" onClick={()=>go('Orders')}>Go to orders</button></div>}
      {data.map((d:any)=><div id={'decision-'+d.product_id} tabIndex={-1} key={d.id}><Decision key={d.status+':'+d.cases} d={d} action={action} ask={ask}/></div>)}
    </div>
  </>
}

function ReviewItemControls({item,action,openDecision}:{item:any,action:any,openDecision:(productId:string)=>void}){
  if(item.decision_id)return <div className="review-decision"><button className="btn pri" onClick={()=>openDecision(item.product_id)}>Open decision</button></div>;
  if(item.status==='sent')return <p className="method">This order has been sent. Recall it from Orders before making changes.</p>;
  const unit=item.detail.calculation.calc_type==='bake'?'units':'cases';
  if(item.override_action)return <div className="manager-box"><b>Manager changed this order</b><p><s>Agent: {item.original_cases} {unit}</s> → Manager: {item.manager_cases} {unit}</p><p>{item.override_reason}{item.override_note?` · ${item.override_note}`:''}</p><button className="btn" onClick={()=>action(`items/${item.product_id}/restore`,{})}>Restore agent’s order</button></div>;
  return <OverrideForm cases={item.cases} unit={unit} onSave={b=>action(`items/${item.product_id}/override`,b)} onReject={b=>action(`items/${item.product_id}/reject`,b)}/>;
}

function Review({revision,initialDept,summary,action,openDecision}:{revision:number,initialDept:string,summary:any,action:any,openDecision:(productId:string)=>void}){
  const [type,setType]=useState('all'),[dept,setDept]=useState(initialDept),[q,setQ]=useState(''),[page,setPage]=useState(1),[open,setOpen]=useState('');
  const {data,error}=useData(`items?type=${type}&dept=${dept}&q=${encodeURIComponent(q)}&page=${page}`,revision),{data:depts}=useData('departments',revision);
  const types=[['all','All items'],['needs','Needs you'],['adjusted','Adjusted'],['normal','Usual order'],['no_order','No order needed'],['data_fix','Data issues']];
  const count=(k:string)=>k==='all'?summary.items:k==='needs'?summary.pending:data?.counts.find((r:any)=>r.category===k)?.count||0;
  return <><div className="types">{types.map(([k,name])=><button className={'type '+(type===k?'on':'')} key={k} onClick={()=>{setType(k);setPage(1)}}><span className="t">{name}</span><span className="n">{num(count(k))}</span></button>)}</div><section className="card"><div className="toolbar"><input aria-label="Search items" placeholder="Search items, e.g. avocados" value={q} onChange={e=>{setQ(e.target.value);setPage(1)}}/><select aria-label="Department" value={dept} onChange={e=>{setDept(e.target.value);setPage(1)}}><option value="all">All departments</option>{depts?.map((d:any)=><option value={d.dept_id} key={d.dept_id}>{d.name}</option>)}</select><span className="cnt">{num(data?.total)} items</span></div>{!data?<Status error={error}/>:<><div className="tw"><table className="otable"><thead><tr><th>Item</th><th>Department</th><th>Agent decision</th><th>Order</th><th>Why</th></tr></thead><tbody>{data.items.map((r:any)=><Fragment key={r.product_id}><tr><td><button className="item-link" aria-expanded={open===r.product_id} onClick={()=>setOpen(open===r.product_id?'':r.product_id)}>{r.name}</button><span className="idl">{r.product_id}</span></td><td>{label(r.dept_id)}</td><td><Badge warn={r.status==='pending'}>{label(r.status==='pending'?'needs you':r.category)}</Badge></td><td title={r.detail.calculation.calc_id}>{r.override_action?<><s>{r.original_cases}</s> → <b>{r.manager_cases}</b></>:r.cases} {r.detail.calculation.calc_type==='bake'?'units':'cases'}</td><td className="why">{r.detail.reason}</td></tr>{open===r.product_id&&<tr className="row-exp"><td colSpan={5}><MathPanel item={r.detail}/><ReviewItemControls item={r} action={action} openDecision={openDecision}/></td></tr>}</Fragment>)}</tbody></table>{!data.items.length&&<p className="empty-state">No items match these filters.</p>}</div><div className="pager"><span>Page {page} · {num(data.total)} matching items</span><div className="acts"><button className="btn" disabled={page<=1} onClick={()=>setPage(page-1)}>Previous</button><button className="btn" disabled={page*data.page_size>=data.total} onClick={()=>setPage(page+1)}>Next</button></div></div></>}</section></>
}

function Orders({revision,action,go}:{revision:number,action:any,go:any}){
  const {data,error}=useData('orders',revision);if(!data)return <Status error={error}/>;
  const pending=data.departments.filter((d:any)=>d.auto_approve).reduce((n:number,d:any)=>n+d.pending,0),sent=data.departments.some((d:any)=>d.sent>0);
  const recallOpen=data.batch&&!data.batch.recalled_at&&new Date(data.batch.recall_until)>new Date();
  return <Card title="Today's orders" note="Prepared for the recorded delivery cycle"><div className="tw"><table className="otable"><thead><tr><th>Department</th><th>Items</th><th>Approved</th><th>Order value</th><th>Auto-approve</th><th>Status</th></tr></thead><tbody>{data.departments.map((d:any)=><tr key={d.dept_id}><td><button className="item-link" onClick={()=>go('Agent review',d.dept_id)}><Icon name={deptIcon(d.dept_id)}/> {d.name}</button></td><td>{num(d.items)}</td><td>{num(d.approved)}</td><td>{money(d.value)}</td><td><button role="switch" aria-checked={d.auto_approve} className={'switch '+(d.auto_approve?'on':'')} onClick={()=>action(`orders/departments/${d.dept_id}`,{auto_approve:!d.auto_approve})}>{d.auto_approve?'On':'Off'}</button></td><td><Badge warn={!d.auto_approve||d.pending>0}>{!d.auto_approve?'Paused · held back':d.pending?`${d.pending} pending`:d.sent?'Sent':'Ready to send'}</Badge></td></tr>)}</tbody></table></div><div className="send-bar"><span>{sent?(recallOpen?'Orders sent · recall available until 6 PM.':'Orders sent · recall window closed.'):pending?`Finish ${pending} active decisions before sending.`:'Everything active is ready. Paused departments will be held back.'}</span><div className="acts">{sent&&recallOpen&&<button className="btn danger" onClick={()=>action('orders/recall',{})}>Recall orders</button>}<button className="btn pri" disabled={pending>0||sent} onClick={()=>action('orders/send',{})}><Icon name="send"/>Send active orders</button></div></div></Card>
}

function Catalog({revision,action,readOnly=false}:{revision:number,action:any,readOnly?:boolean}){const [page,setPage]=useState(1),{data:summary,error}=useData('catalog/summary',revision),{data}=useData(`catalog/reviews?page=${page}`,revision);if(!summary||!data)return <Status error={error}/>;return <><Tiles items={[["Supplier listings",num(summary.listings),'Imported synthetic catalog'],['Products',num(summary.products),'Canonical product records'],['Auto-linked',num(summary.auto_merged),'High-confidence matches'],['Need your review',num(summary.pending),'Uncertain matches']]}/><Card title="Unsure matches" note="Review the recommendation before linking a supplier listing."><div className="tw"><table className="otable"><thead><tr><th>Supplier listing</th><th>Product</th><th>Confidence</th><th>Recommendation</th><th>Action</th></tr></thead><tbody>{data.reviews.map((r:any)=><tr key={r.id}><td className="mono">{r.review.listing_name}<span className="idl">{r.review.raw_pack}</span></td><td>{r.review.product_name}</td><td>{pct(r.review.score)}</td><td><Badge>{label(r.review.decision)}</Badge><p className="why">{r.review.reason}</p></td><td>{r.status==='pending'&&!readOnly?<div className="acts"><button className="btn pri" onClick={()=>action(`catalog/reviews/${r.id}`,{action:'accept'})}>Accept</button><button className="btn" onClick={()=>action(`catalog/reviews/${r.id}`,{action:'reject'})}>Reject</button></div>:<Badge>{r.status}</Badge>}</td></tr>)}</tbody></table></div><div className="pager"><span>Page {page} · {num(data.total)} matches</span><div className="acts"><button className="btn" disabled={page===1} onClick={()=>setPage(page-1)}>Previous</button><button className="btn" disabled={page*data.page_size>=data.total} onClick={()=>setPage(page+1)}>Next</button></div></div></Card></>}

function Impact({revision}:{revision:number}){const {data,error}=useData('impact',revision);if(!data)return <Status error={error}/>;const g=data.evaluation.G5_waste_and_stockout_vs_baseline,fp=g.freshpilot,base=g.baseline,departments=data.diagnostic?.generated?.departments||[];return <><Tiles items={[["Lower waste rate",pct(1-fp.waste_pct/base.waste_pct),'Relative to baseline'],['Fewer stockouts',pct(1-fp.stockout_pct/base.stockout_pct),'Relative to baseline'],['Margin added',money(fp.margin_usd-base.margin_usd),'28-day synthetic backtest'],['Food / carbon conversion','Not measured','No unsupported conversions']]}/><div className="cols"><Card title="Waste rate by department" note="Both policies use the same 28-day holdout"><div className="bars">{departments.filter((r:any)=>r.policy==='baseline').map((r:any)=>{const other=departments.find((p:any)=>p.policy==='freshpilot'&&p.department===r.department);return <div className="barrow" key={r.department}><div className="lab"><b>{label(r.department)}</b></div>{[[r.waste_pct,'Baseline','var(--base)'],[other?.waste_pct,'FreshPilot','var(--brand-2)']].map(([v,l,c]:any)=><div className="bline" key={l}><div className="btrack"><span style={{width:`${Number(v)*100}%`,background:c}} title={`${l}: ${pct(v)}`}/></div><span>{pct(v)}</span></div>)}</div>})}</div></Card><Card title="Measured results" note="Backtest, not a claim of real-store savings"><table className="otable"><thead><tr><th>Metric</th><th>Baseline</th><th>FreshPilot</th></tr></thead><tbody><tr><td>Waste rate</td><td>{pct(base.waste_pct)}</td><td>{pct(fp.waste_pct)}</td></tr><tr><td>Stockout rate</td><td>{pct(base.stockout_pct)}</td><td>{pct(fp.stockout_pct)}</td></tr><tr><td>Lost sales</td><td>{money(base.lost_sales_usd)}</td><td>{money(fp.lost_sales_usd)}</td></tr><tr><td>Margin</td><td>{money(base.margin_usd)}</td><td>{money(fp.margin_usd)}</td></tr></tbody></table><p className="method">{data.methodology}</p><p className="idl">{data.source_ref}</p></Card></div></>}

function Trace({id}:{id:string}){const {data,error}=useData('obs/traces/'+encodeURIComponent(id));if(!data)return <Status error={error}/>;return <div className="trace-detail"><ul className="steps">{data.trace?.map((s:any,i:number)=><li key={i}><span className={'sdot s-'+(s.type==='rules'?'det':s.type)}>{i+1}</span><span><b>{s.name}</b> — {s.summary}</span><time>{Number(s.duration_s).toFixed(3)} s</time></li>)}</ul>{data.tool_outputs?.length>0&&<details><summary>Tool inputs and recorded outputs</summary>{data.tool_outputs.map((t:any,i:number)=><details key={i}><summary>{t.name} · {num(t.duration_s)} s</summary><pre>{JSON.stringify(t,null,2)}</pre></details>)}</details>}</div>}
function Observability({revision,action}:{revision:number,action:any}){
  const {data:p,error}=useData('obs/pipeline',revision),{data:traces}=useData('obs/traces',revision),{data:model}=useData('obs/model',revision),{data:events}=useData('obs/events',revision);const [open,setOpen]=useState(''),[limit,setLimit]=useState(25),[running,setRunning]=useState(false);
  if(!p||!traces||!model||!events)return <Status error={error}/>;
  const summary=JSON.parse(p.run.summary),total=p.stages.reduce((n:number,s:any)=>n+s.duration_s,0);let offset=0;
  return <><Tiles items={[["Pipeline duration",num(p.run.duration_s)+' s',`${p.stages.length} recorded stages`],['Items the LLM touched',num(summary.llm_touched),`${num(summary.items)} products in the run`],['LLM cost','$'+Number(summary.cost_usd).toFixed(4),'Measured tokens / reserved unknown usage'],['Guardrail interventions',num(summary.guardrail_events),'Recorded for this run']]}/><div className="inbox-top"><p>Local traces are always available. Model training is tracked in MLflow.</p><button className="btn" disabled={running} onClick={async()=>{setRunning(true);await action('pipeline/run',{date:summary.date});setRunning(false)}}>{running?'Running pipeline…':'Re-run pipeline'}</button></div><div className="cols"><div className="stack"><Card title="Daily pipeline" note={`${summary.date} · cached stages are labelled`}><div className="gantt">{p.stages.map((s:any)=>{const left=offset;offset+=s.duration_s;return <div className="grow" key={s.ordinal}><div className="nm">{s.name}<small>{s.executor} · {s.status} · {num(s.count)} outputs</small></div><div className="gtrack"><span className={'k-'+(s.executor==='rules'?'det':s.executor)} style={{left:`${left/total*100}%`,width:`${s.duration_s/total*100}%`}} title={s.details}/></div><span className="dur">{num(s.duration_s)} s</span></div>})}</div></Card><Card title="Agent traces" note="Open a trace to inspect the decisions and tool calls"><div className="tw"><table className="otable"><thead><tr><th>Run</th><th>Agent</th><th>Tokens</th><th>Cost</th><th>Outcome</th></tr></thead><tbody>{traces.slice(0,limit).map((r:any)=><Fragment key={r.run_id}><tr><td><button className="item-link trace-button" onClick={()=>setOpen(open===r.run_id?'':r.run_id)} aria-expanded={open===r.run_id}>{r.product_id}</button></td><td>{r.agent}</td><td>{num(r.tokens_in+r.tokens_out)}</td><td>${Number(r.cost_usd).toFixed(4)}</td><td>{label(r.outcome)}</td></tr>{open===r.run_id&&<tr className="row-exp"><td colSpan={5}><Trace id={r.run_id}/></td></tr>}</Fragment>)}</tbody></table></div>{limit<traces.length&&<button className="btn" onClick={()=>setLimit(limit+25)}>Show more traces</button>}</Card></div><div className="stack"><Card title="Forecast model health" note="Weekly held-out error and calibrated coverage"><Chart title="Weekly WAPE: lower is better" labels={model.weeks.map((w:any)=>w.date)} series={[{label:'Model WAPE',values:model.weeks.map((w:any)=>w.wape*100),color:'var(--brand-2)'},{label:'Baseline WAPE',values:model.weeks.map((w:any)=>w.baseline_wape*100),color:'var(--base)'}]}/><p className="method">Interval coverage: {model.weeks.map((w:any)=>pct(w.coverage)).join(' · ')}</p><table className="otable"><thead><tr><th>Input drift</th><th>PSI</th></tr></thead><tbody>{model.drift.map((d:any)=><tr key={d.feature}><td>{label(d.feature)}</td><td>{num(d.psi)}</td></tr>)}</tbody></table></Card><Card title="Guardrails & human feedback"><ul className="events">{events.guardrails.map((e:any,i:number)=><li key={'g'+i}><Icon name="shield-x"/><div><b>{label(e.guardrail_type)}</b><p>{e.details}</p></div></li>)}{events.human.map((e:any)=><li key={e.id}><Icon name="user-edit"/><div><b>{label(e.action)}</b><p>{e.reason||e.subject}</p></div></li>)}</ul>{!events.guardrails.length&&!events.human.length&&<p className="muted">No interventions or manager actions in this run.</p>}</Card></div></div></>
}

function Chat({initial,close,staff=false}:{staff?:boolean,initial:{question:string,product_id?:string},close:()=>void}){
  const [messages,setMessages]=useState<{role:string,text:string,sources?:string[]}[]>([]),[q,setQ]=useState(''),[busy,setBusy]=useState(false);const dialog=useRef<HTMLElement>(null),input=useRef<HTMLInputElement>(null),started=useRef(false);
  async function ask(question:string){if(!question.trim()||busy)return;setMessages(m=>[...m,{role:'me',text:question},{role:'ai',text:''}]);setQ('');setBusy(true);try{const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json','X-FreshPilot':'1'},body:JSON.stringify({message:question,product_id:initial.product_id})});if(!r.ok)throw new Error('The answer is unavailable. Please try again.');const reader=r.body!.getReader(),decoder=new TextDecoder();let buffer='';while(true){const {done,value}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});let end;while((end=buffer.indexOf('\n\n'))>=0){const event=buffer.slice(0,end);buffer=buffer.slice(end+2);if(event.startsWith('data: ')){const data=JSON.parse(event.slice(6));setMessages(m=>{const copy=[...m],last={...copy[copy.length-1]};last.text+=data.text||'';last.sources=data.sources||last.sources;copy[copy.length-1]=last;return copy})}}}}catch(e){setMessages(m=>[...m,{role:'ai',text:(e as Error).message}])}finally{setBusy(false)}}
  useEffect(()=>{const prior=document.activeElement as HTMLElement;input.current?.focus();if(initial.question&&!started.current){started.current=true;void ask(initial.question)}const key=(e:KeyboardEvent)=>{if(e.key==='Escape')close();if(e.key==='Tab'){const focusable=dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input');if(!focusable?.length)return;const first=focusable[0],last=focusable[focusable.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}}};document.addEventListener('keydown',key);return()=>{document.removeEventListener('keydown',key);prior?.focus()}},[]);
  return <><div className="scrim on" onClick={close}/><aside ref={dialog} className="drawer on" role="dialog" aria-modal="true" aria-label="Ask FreshPilot"><div className="dr-h"><div className="mark"><Icon name="sparkles"/></div><div><b>Ask FreshPilot</b><small>{staff?'Answers about your assigned tasks':'Read-only answers from your recorded run'}</small></div><button className="x" aria-label="Close chat" onClick={close}><Icon name="x"/></button></div><div className="msgs" aria-live="polite">{!messages.length&&<div className="msg ai">{staff?'Ask about your assigned shelf checks.':'Ask about today’s decisions, orders, waste, or an item.'}</div>}{messages.map((m,i)=><div className={'msg '+m.role} key={i}>{m.text||'Checking the recorded evidence…'}{!!m.sources?.length&&<div className="src">Sources: {m.sources.join(', ')}</div>}</div>)}</div><div className="sugg">{(staff?['What are my assigned tasks?','How do I record damaged stock?']:['What did the agents do today?','Which items need a shelf check?','Where is our waste coming from?']).map(s=><button disabled={busy} key={s} onClick={()=>ask(s)}>{s}</button>)}</div><form className="composer" onSubmit={e=>{e.preventDefault();void ask(q)}}><input ref={input} aria-label="Your question" placeholder="Ask about your store…" value={q} onChange={e=>setQ(e.target.value)}/><button disabled={busy||!q.trim()} aria-label="Send question"><Icon name="arrow-up"/></button></form></aside></>
}

export default function App(){
  const [user,setUser]=useState<User|null>(null),[loading,setLoading]=useState(true);
  useEffect(()=>{api('auth/me').then(setUser).catch(()=>setUser(null)).finally(()=>setLoading(false));const expire=()=>setUser(null);window.addEventListener('session-expired',expire);return()=>window.removeEventListener('session-expired',expire)},[]);
  if(loading)return <Status/>;
  return user?<Workspace key={user.id} user={user} onLogout={()=>setUser(null)}/>:<Login onLogin={setUser}/>;
}
function Workspace({user,onLogout}:{user:User,onLogout:()=>void}){
  const staff=user.role==='staff',admin=user.role==='admin';
  const [targetDecision,setTargetDecision]=useState('');
  const [page,setPage]=useState(staff?'My tasks':admin?'Store overview':'Today'),[revision,setRevision]=useState(0),[dept,setDept]=useState('all'),[chat,setChat]=useState<{question:string,product_id?:string}|null>(null),[notice,setNotice]=useState(''),[theme,setTheme]=useState(localStorage.getItem('theme')||'light');
  const {data:summary,error}=useData(staff?'auth/me':'summary/today',revision);
  const nav=staff?[['My tasks','clipboard-check']]:[
    [admin?'Store overview':'Today','layout-dashboard'],['Decisions','sparkles'],['Shelf checks','clipboard-check'],['Agent review','list-search'],['Orders','truck-delivery'],['Catalog','tags'],['Impact','chart-line'],
    ...(admin?[['Approvals','shield-check'],['Team & access','users'],['Settings','settings'],['Audit history','history'],['Observability','activity-heartbeat']]:[])];
  useEffect(()=>{document.documentElement.dataset.theme=theme;localStorage.setItem('theme',theme)},[theme]);
  useEffect(()=>{const timer=setInterval(()=>setRevision(r=>r+1),30000);return()=>clearInterval(timer)},[]);
  const go=(next:string,department='all')=>{if(!nav.some(n=>n[0]===next))return;setTargetDecision('');setDept(department);setPage(next);window.scrollTo(0,0)},ask=(question='',product_id?:string)=>setChat({question,product_id});
  const openDecision=(productId:string)=>{setTargetDecision(productId);setPage('Decisions')};
  async function action(path:string,body:any={}){try{const result=await api(path,body);setRevision(r=>r+1);setNotice(result?.status==='needs_category_approval'?'Saved and held for admin approval.':'Saved. Your action has been recorded.')}catch(e){setNotice((e as Error).message)}}
  return <><a href="#content" className="skip-link">Skip to content</a><div className="app"><nav className="side" aria-label="Main"><div className="brand"><div className="mark"><Icon name="leaf"/></div><b>FreshPilot</b></div><div className="store"><Icon name="building-store"/><div><small>Assigned store</small><span>{summary?.store?.name||user.stores.join(', ')||'No store assigned'}</span></div></div>{nav.map(([name,icon])=><button key={name} className={'nav '+(page===name?'on':'')} aria-current={page===name?'page':undefined} onClick={()=>go(name)}><Icon name={icon}/>{name}{name==='Decisions'&&summary?.pending>0&&<span className="pill">{summary.pending}</span>}</button>)}<button className="nav" onClick={()=>ask()}><Icon name="message-chatbot"/>Ask FreshPilot</button><button className="nav" onClick={async()=>{try{await api('auth/logout',{});onLogout()}catch(e){setNotice((e as Error).message)}}}><Icon name="logout"/>Sign out</button><div className="side-foot"><div className="avatar">{user.name.split(' ').map(n=>n[0]).join('')}</div><div>{user.name}<small>{user.role}</small></div></div></nav><main id="content"><header className="top"><div><h1>{page==='Today'?'Good morning, '+user.name.split(' ')[0]:page}</h1><p>{staff?'Check your assigned items and send counts to your manager.':page==='Store overview'?'Store performance, operational exceptions, and team access.':'FreshPilot · '+(summary?.date||'Latest completed run')}</p></div><div className="top-r"><span className="chip c-ok">{user.role}</span><button className="btn theme" aria-label="Toggle color theme" onClick={()=>setTheme(theme==='light'?'dark':'light')}><Icon name={theme==='light'?'moon':'sun'}/></button><button className="ask-btn" onClick={()=>ask()}><Icon name="sparkles"/>Ask FreshPilot</button></div></header>{notice&&<div className="notice" role="status">{notice}<button aria-label="Dismiss notification" onClick={()=>setNotice('')}>×</button></div>}{!summary?<Status error={error}/>:<div className="view on" key={page+dept}>
    {(page==='Today'||page==='Store overview')&&<>{admin&&<div className="admin-shortcuts"><button className="btn pri" onClick={()=>go('Approvals')}>Review admin approvals</button><button className="btn" onClick={()=>go('Team & access')}>Manage team access</button></div>}<Today summary={summary} revision={revision} go={go}/></>}
    {(page==='My tasks'||page==='Shelf checks')&&<Tasks user={user} revision={revision} action={action}/>}
    {page==='Approvals'&&<Approvals revision={revision} action={action}/>}
    {page==='Team & access'&&<Team user={user} revision={revision} action={action}/>}
    {page==='Settings'&&<Settings revision={revision} action={action}/>}
    {page==='Audit history'&&<Audit revision={revision}/>}
    {page==='Decisions'&&<Decisions revision={revision} action={action} ask={ask} target={targetDecision} go={go}/>}
    {page==='Agent review'&&<Review revision={revision} initialDept={dept} summary={summary} action={action} openDecision={openDecision}/>}
    {page==='Orders'&&<Orders revision={revision} action={action} go={go}/>}
    {page==='Catalog'&&<Catalog revision={revision} action={action} readOnly={!admin}/>}
    {page==='Impact'&&<Impact revision={revision}/>}
    {page==='Observability'&&<Observability revision={revision} action={action}/>}
  </div>}<footer>Independent proof of concept · Fully synthetic data · No retailer affiliation.</footer></main></div>{chat&&<Chat initial={chat} staff={staff} close={()=>setChat(null)}/>}</>;
}
