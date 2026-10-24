// 目的：作者逐項讀主張／來源／條件，追加自己的回應；不改稿、不阻擋其他研究動作。
// 邊界：不可信文字只用 textContent；來源／快照／actor 與章節權限由後端判定。
// 驗證：科學建議 API 對抗測試及合成真瀏覽器。
(() => {
    'use strict';
    const el=id=>document.getElementById(id);
    let report=null,target=null,generation=0;
    const responseDrafts=new Map();
    async function api(url,body){
        const r=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
        const value=await r.json();if(!r.ok)throw new Error(value.message || '本次未保存；原輸入保留。');return value;
    }
    function text(parent,value,tag='p'){const n=document.createElement(tag);n.textContent=value;parent.append(n);return n;}
    function message(value){el('sciMessage').textContent=value;}
    function ensurePanel(){
        if(el('scientificPanel'))return;
        const panel=document.createElement('section');panel.id='scientificPanel';panel.className='offcanvas offcanvas-end';panel.tabIndex=-1;panel.style.width='min(780px,100vw)';panel.setAttribute('aria-labelledby','sciHeading');
        panel.innerHTML=`<div class="offcanvas-header"><h2 id="sciHeading" class="h5">科學證據建議</h2><button class="btn-close" data-bs-dismiss="offcanvas" aria-label="關閉科學建議"></button></div><div class="offcanvas-body">
        <p>比對只提供建議，不是科學證明。你可以保留假說、不同意，並繼續寫作、保存與匯出。</p><p id="sciTarget" class="small"></p><p id="sciMessage" role="status" aria-live="polite"></p>
        <form id="sciForm"><label class="d-block mb-2">要比對的原文<textarea id="sciText" class="form-control" rows="5" required maxlength="16000"></textarea></label>
        <label class="d-block mb-2">選擇證據（可不選，最多八份）<select id="sciEvidence" class="form-select" multiple size="4"></select></label>
        <label class="d-block mb-2">你要保留的條件（選填，以分號分隔）<textarea id="sciConditions" class="form-control" maxlength="4000"></textarea></label>
        <p class="small text-muted">比對會保存原文與建議，供本章有權限的人查看；不會保存或改動你的稿件。</p><button class="btn btn-primary mb-3">比對並保存建議</button></form>
        <label class="d-block mb-3">歷史建議<select id="sciHistory" class="form-select"><option value="">選擇歷史建議</option></select></label><div id="sciReport"></div></div>`;
        document.body.append(panel);
        el('sciForm').onsubmit=async event=>{
            event.preventDefault();const active=target,token=++generation;
            try{
                const choices=Array.from(el('sciEvidence').selectedOptions,o=>JSON.parse(o.value));
                const result=await api('/manuscript/api/scientific/assess',{pid:active.pid,section:active.section,text:el('sciText').value,evidence:choices,required_conditions:el('sciConditions').value});
                if(token!==generation || target!==active)return;render(result);message('建議已保存，稿件沒有改動。');await history(active);
            }catch(error){message(error.message);}
        };
        el('sciHistory').onchange=()=>{if(el('sciHistory').value)load(el('sciHistory').value).catch(e=>message(e.message));};
    }
    async function history(active){
        const result=await api(`/manuscript/api/scientific/reports?pid=${encodeURIComponent(active.pid)}&section=${encodeURIComponent(active.section)}`);
        if(target!==active)return;
        el('sciHistory').replaceChildren(new Option('選擇歷史建議',''),...result.reports.map(r=>new Option(`${r.created_at} · ${r.origin==='generated_draft'?'生成後建議':'作者比對'}`,r.report_id)));
        if(report)el('sciHistory').value=report.report_id;
    }
    async function load(id){
        const active=target,token=++generation;
        const result=await api(`/manuscript/api/scientific/reports/${encodeURIComponent(id)}?pid=${encodeURIComponent(active.pid)}`);
        if(target!==active || token!==generation)return;render(result);
    }
    function render(data){
        report=data;const container=el('sciReport');container.replaceChildren();
        text(container,data.stale?'來源已變更或不可用。以下是原快照的歷史建議，請重讀來源。':'原文及來源快照已保存；這不代表主張已被科學驗證。').className='alert '+(data.stale?'alert-warning':'alert-info');
        text(container,data.uncertainty,'p').className='small';
        const refresh=document.createElement('button');refresh.type='button';refresh.className='btn btn-outline-secondary mb-3';
        refresh.textContent='重新載入此報告（保留未送出的回應）';refresh.onclick=()=>load(data.report_id).catch(e=>message(e.message));container.append(refresh);
        for(const claim of data.claims){
            const card=document.createElement('article');card.className='border rounded p-3 mb-3';container.append(card);
            text(card,claim.text,'h3').className='h6';
            text(card,({text_aligned:'文字對齊；仍須核對科學支持',possible_contradiction:'可能矛盾，請人工核對',unknown:'尚未建立支持／未知'})[claim.relation]);
            for(const finding of claim.findings){text(card,finding.message);if(finding.missing)text(card,'本句未明示：'+finding.missing.join('；'));if(finding.unmatched)text(card,'未對應數值：'+finding.unmatched.join('；'));}
            for(const comparison of claim.comparisons){
                const details=document.createElement('details');details.open=true;card.append(details);
                text(details,`來源 ${comparison.source_id} · 定位 ${JSON.stringify(comparison.locator)} · 句 ${comparison.sentence_index+1}`,'summary');
                text(details,comparison.quote,'blockquote').className='border-start ps-2 my-2';
                if(comparison.source_conditions.length)text(details,'此來源的條件：'+comparison.source_conditions.join('；'));
            }
            for(const response of data.responses.filter(r=>r.claim_id===claim.id))text(card,`作者回應：${({retain_hypothesis:'保留為假說',revise:'準備修訂',disagree:'不同意此建議',need_evidence:'需要更多證據'})[response.stance]} · ${response.reason}${response.stale?'（針對舊來源快照）':''}`);
            const form=document.createElement('form'),stance=document.createElement('select');stance.className='form-select';stance.setAttribute('aria-label','本主張的處理方式');
            for(const [value,label] of Object.entries({retain_hypothesis:'保留為假說',revise:'準備修訂',disagree:'不同意此建議',need_evidence:'需要更多證據'}))stance.add(new Option(label,value));form.append(stance);
            const reason=document.createElement('textarea');reason.className='form-control my-2';reason.required=true;reason.maxLength=4000;reason.setAttribute('aria-label','作者回應理由');form.append(reason);
            const draftKey=data.report_id+':'+claim.id,draft=responseDrafts.get(draftKey);
            if(draft){stance.value=draft.stance;reason.value=draft.reason;}
            form.addEventListener('input',()=>responseDrafts.set(draftKey,{stance:stance.value,reason:reason.value}));
            const button=document.createElement('button');button.className='btn btn-outline-primary';button.textContent='追加我的回應';form.append(button);
            const active=target,snapshot=data.view_snapshot;
            form.onsubmit=async event=>{
                event.preventDefault();button.disabled=true;
                try{await api(`/manuscript/api/scientific/reports/${encodeURIComponent(data.report_id)}/responses`,{pid:active.pid,claim_id:claim.id,stance:stance.value,reason:reason.value,view_snapshot:snapshot});
                    responseDrafts.delete(draftKey);if(target!==active)return;await load(data.report_id);message('你的回應已追加；不代表系統核准科學結論。');
                }catch(error){message(error.message);}finally{button.disabled=false;}
            };card.append(form);
        }
    }
    async function open(id){
        ensurePanel();const app=window.wsApp,section=app.collab.currentSection();
        if(!target || target.pid!==app.pid || target.section!==section){
            target={pid:app.pid,section};report=null;el('sciReport').replaceChildren();el('sciConditions').value='';
            el('sciText').value=String(window.getSelection() || '').trim() || Array.from(app.editorCanvas.querySelectorAll('.card-content'),n=>n.innerText).join('\n');
        }
        const active=target,token=++generation;el('sciTarget').textContent=`${active.pid} · ${active.section} · 可比對尚未保存的想法`;
        bootstrap.Offcanvas.getOrCreateInstance(el('scientificPanel')).show();message('載入可用來源…');
        try{const choices=await api(`/manuscript/api/paragraph/context?pid=${encodeURIComponent(active.pid)}&section=${encodeURIComponent(section)}`);
            if(target!==active || token!==generation)return;
            el('sciEvidence').replaceChildren(...choices.sources.map(s=>new Option(s.title+' · '+s.evidence_id,JSON.stringify({entry_id:s.entry_id,evidence_id:s.evidence_id}))));
            await history(active);message('未選證據時會明示未知；你仍可繼續研究。');if(id)await load(id);
        }catch(error){message(error.message);}
    }
    document.addEventListener('DOMContentLoaded',()=>{
        el('btnScientificReview')?.addEventListener('click',()=>open());
        window.wsApp?.socket?.on('ai_response',data=>{
            const advisory=data.meta?.scientific_advisory;if(!advisory)return;
            const status=el('scientificDraftStatus');status.replaceChildren();
            if(advisory.status==='unavailable'){status.textContent=advisory.message;return;}
            const button=document.createElement('button');button.type='button';button.className='btn btn-sm btn-outline-secondary';
            button.textContent=`本次草稿：${advisory.claims.length} 項主張的證據建議（可繼續編輯）`;button.onclick=()=>open(advisory.report_id);status.append(button);
        });
    });
})();
