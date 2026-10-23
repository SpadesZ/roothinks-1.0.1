// 目的：顯示依賴變更，讓作者／原審閱者明確追加重核；不改原稿，不代替研究判斷。
// 邊界：用文字節點呈現不可信來源，確認綁載入快照；失敗保留輸入。驗證：SAI API 與合成瀏覽器。
(() => {
    'use strict';
    const el = id => document.getElementById(id);
    const labels = {current:'已確認的依賴仍一致',needs_reconfirmation:'需要重新核對',superseded:'已由後續決策接續',
        untracked:'尚未完整追蹤',reconfirmed:'已明確重新核對',partially_reconfirmed:'已重核已知依賴；仍有未追蹤章節'};
    let report = null, editing = null, editingSnapshot = null, choices = [], generation = 0;
    const message = value => { el('impact-message').textContent = value; };
    async function api(path,body) {
        const response = await fetch(path,{method:body === undefined?'GET':'POST',headers:{'Content-Type':'application/json',
            'X-CSRFToken':el('impact-csrf').value},...(body === undefined?{}:{body:JSON.stringify(body)})});
        const data = await response.json();
        if (!response.ok || data.success === false) throw new Error(data.message || '無法完成，原輸入保留。');
        return data;
    }
    function text(parent,value,tag='p') { const node=document.createElement(tag); node.textContent=value; parent.append(node); return node; }
    function card(parent,title,details) {
        const node=document.createElement('article'); node.className='border rounded p-3 mb-2';
        text(node,title,'h3').className='h6'; if(details) text(node,details); parent.append(node); return node;
    }
    function preview() {
        const source=choices[Number(el('impact-evidence').value)];
        el('impact-evidence-preview').textContent=source ? `${source.title}\n定位：${source.locator}\n採用理由：${source.selection_reason}\n${source.text}`:'沒有可用證據；請由有權限讀來源的人先核對。';
    }
    async function editClaim(claim) {
        const token=++generation, pid=report.pid;
        const data=await api(`/manuscript/api/paragraph/context?pid=${encodeURIComponent(pid)}&section=${encodeURIComponent(claim.section)}`);
        if(token!==generation || report.pid!==pid) return;
        editing=claim; editingSnapshot=report.snapshot; choices=data.sources;
        for(const key of ['paragraph','purpose','claim','conditions','forbidden']) {
            el('impact-'+key).value=claim.context[key === 'paragraph'?'paragraph_text':key] || '';
        }
        el('impact-contradictions').value=''; el('impact-reason').value=''; el('impact-confirmed').checked=false;
        el('impact-edit-section').textContent=`${claim.section} · 原確認 ${claim.version}；請核對欲保存版本的實際段落。`;
        el('impact-version').replaceChildren(...data.versions.map(version => { const option=new Option(`章節 ${version.ver}`,version.ver); return option; }));
        el('impact-evidence').replaceChildren(...choices.map((source,index)=>new Option(source.title+' · '+source.evidence_id,String(index))));
        const match=choices.findIndex(s=>s.entry_id===claim.context.evidence.entry_id && s.evidence_id===claim.context.evidence.id);
        if(match>=0) el('impact-evidence').value=String(match);
        preview(); el('impact-editor').hidden=false; el('impact-editor').scrollIntoView({block:'start'});
    }
    function outcomeForm(node,target) {
        const snapshot=report.snapshot, pid=report.pid;
        const form=document.createElement('form');
        const label=text(form,'已重新核對的作者主張（選取能覆蓋本版本依賴的項目）','label'); label.className='d-block';
        const select=document.createElement('select'); select.multiple=true; select.required=true; select.className='form-select'; label.append(select);
        report.claims.filter(c=>c.status==='current' && target.sections[c.section]===c.version && target.present_claim_ids.includes(c.decision_id)).forEach(c=>select.add(new Option(c.context.claim,c.decision_id)));
        const reasonLabel=text(form,'重新核對理由','label'); reasonLabel.className='d-block';
        const reason=document.createElement('textarea'); reason.required=true; reason.maxLength=1000; reason.className='form-control'; reasonLabel.append(reason);
        const confirmedLabel=document.createElement('label'); confirmedLabel.className='d-block my-2';
        const confirmed=document.createElement('input'); confirmed.type='checkbox'; confirmed.required=true; confirmedLabel.append(confirmed,document.createTextNode(' 我已核對此版本的主張、證據與適用界線')); form.append(confirmedLabel);
        const button=document.createElement('button'); button.className='btn btn-outline-primary btn-sm'; button.textContent=target.kind==='paper'?'追加稿件重新確認':'原審閱者追加重新確認'; form.append(button);
        form.addEventListener('submit',async event=>{
            event.preventDefault(); button.disabled=true;
            try {
                await api('/manuscript/api/change-impact/reconfirm',{pid,kind:target.kind,target_id:target.id,
                    confirmed:confirmed.checked,reason:reason.value,claim_ids:Array.from(select.selectedOptions,o=>o.value),impact_snapshot:snapshot});
                if(report.pid!==pid) return;
                message('重新核對已追加；原稿與原審閱保留。'); await loadReport();
            } catch(error) { message(error.message); } finally { button.disabled=false; }
        }); node.append(form);
    }
    async function loadReport() {
        const pid=el('impact-project').value, token=++generation;
        if(!pid) return;
        const data=await api(`/manuscript/api/change-impact?pid=${encodeURIComponent(pid)}`);
        if(token!==generation) return;
        report=data;
        if(editing && editingSnapshot!==report.snapshot) message('變更清單已有更新，原輸入保留。請重新載入證據並核對，再勾選確認。');
        for(const id of ['impact-claims','impact-papers','impact-reviews']) el(id).replaceChildren();
        for(const claim of report.claims) {
            const node=card(el('impact-claims'),`${claim.section} · ${claim.version} · ${labels[claim.status]}`,claim.context.claim);
            claim.issues.forEach(issue=>text(node,issue.message));
            text(node,`適用：${claim.context.conditions}；不能聲稱：${claim.context.forbidden}`);
            if(claim.can_reconfirm && claim.status!=='superseded') {
                const button=document.createElement('button'); button.className='btn btn-sm btn-outline-primary'; button.textContent='檢視來源並重新確認本段';
                button.addEventListener('click',()=>editClaim(claim).catch(error=>message(error.message))); node.append(button);
            }
        }
        for(const target of [...report.papers,...report.reviews]) {
            const node=card(el(target.kind==='paper'?'impact-papers':'impact-reviews'),`${target.kind==='paper'?'稿件':'審閱'} ${target.version || '未標版本'} · ${labels[target.status]}`,
                target.dependency_basis==='historical_section_version_inference'?'舊版本僅能推定可能依賴，未改寫原稿。':'保留原版本與依賴關係。');
            target.issues.forEach(issue=>text(node,issue.message));
            if(target.untracked_sections.length) text(node,'未追蹤章節：'+target.untracked_sections.join('、'));
            if(target.last_reconfirmation) text(node,'上次重新核對：'+target.last_reconfirmation.detail.reason);
            if((target.kind==='paper' && report.can_confirm_paper) || target.can_reconfirm) outcomeForm(node,target);
        }
        if(!report.claims.length) text(el('impact-claims'),'尚未建立段落主張與證據連結；可先寫作並保存，之後補上作者判斷。');
        if(!report.reviews.length) text(el('impact-reviews'),'目前沒有審閱；教授是選用流程。');
        el('impact-coverage').textContent=`只涵蓋可見且已建立的依賴。尚有 ${report.untracked.legacy_citations} 筆舊引用未綁段落契約；科學正確性未由本清單判定。`;
    }
    el('impact-project-form').addEventListener('submit',event=>{event.preventDefault();loadReport().catch(error=>message(error.message));});
    el('impact-project').addEventListener('change',()=>{ editing=null; el('impact-editor').hidden=true; loadReport().catch(error=>message(error.message)); });
    el('impact-evidence').addEventListener('change',preview);
    el('impact-reload-evidence').addEventListener('click',async()=>{
        if(!editing || !report) return;
        const pid=report.pid, decisionId=editing.decision_id;
        try {
            await loadReport();
            if(report.pid!==pid || editing?.decision_id!==decisionId) return;
            const snapshot=report.snapshot;
            const data=await api(`/manuscript/api/paragraph/context?pid=${encodeURIComponent(pid)}&section=${encodeURIComponent(editing.section)}`);
            if(report.pid!==pid || editing?.decision_id!==decisionId) return;
            choices=data.sources; editingSnapshot=snapshot;
            const version=el('impact-version').value;
            el('impact-version').replaceChildren(...data.versions.map(v=>new Option(`章節 ${v.ver}`,v.ver)));
            if(data.versions.some(v=>v.ver===version)) el('impact-version').value=version;
            el('impact-evidence').replaceChildren(...choices.map((source,index)=>new Option(source.title+' · '+source.evidence_id,String(index))));
            preview(); el('impact-confirmed').checked=false; message('證據已重新載入。請閱讀後核對你的主張與條件，再確認。');
        } catch(error) { message(error.message); }
    });
    el('impact-claim-form').addEventListener('submit',async event=>{
        event.preventDefault(); if(!editing || !report) return;
        const source=choices[Number(el('impact-evidence').value)]; if(!source) return message('缺少可用證據；原輸入保留。');
        const pid=report.pid, decisionId=editing.decision_id;
        const data={pid,section:editing.section,s_ver:el('impact-version').value,entry_id:source.entry_id,evidence_id:source.evidence_id,
            confirmed:el('impact-confirmed').checked,change_reason:el('impact-reason').value,impact_snapshot:editingSnapshot,contradictions:el('impact-contradictions').value};
        for(const key of ['paragraph','purpose','claim','conditions','forbidden']) data[key==='paragraph'?'paragraph_text':key]=el('impact-'+key).value;
        try { await api(`/manuscript/api/paragraph/${encodeURIComponent(decisionId)}/reconfirm`,data); if(report.pid!==pid || editing?.decision_id!==decisionId) return; editing=null; el('impact-editor').hidden=true; message('新確認已追加。受影響稿件與審閱仍需各自核對。'); await loadReport(); }
        catch(error) { message(error.message); }
    });
    Promise.all([api('/api/project/list?status=formal'),api('/api/project/list?status=temp')]).then(async lists=>{
        const selected=new URL(location.href).searchParams.get('pid');
        const projects=new Map(lists.flatMap(result=>result.projects).map(p=>[p.project_id,p]));
        for(const project of projects.values()) el('impact-project').add(new Option(project.name,project.project_id));
        if(selected && projects.has(selected)) el('impact-project').value=selected;
        if(el('impact-project').value) await loadReport();
    }).catch(error=>message(error.message));
})();
