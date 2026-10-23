// Roothinks source maintenance contract
// 目的：作者明確確認段落條件，讀同一教授意見並追加版本回覆；純文字呈現來源。
// 邊界：草稿／確認 ID 不跨 PID 或章節；伺服器重新驗 ACL、版本、來源及預算。
// 驗證：node --check app/static/js/paragraph_review.js；SAI 真實瀏覽器流程。
(function () {
    const byId = id => document.getElementById(id);
    const esc = value => { const d=document.createElement('div'); d.textContent=String(value ?? ''); return d.innerHTML; };
    let openedPid='', section='', sources=[], decisions=[], authorReview=null, sequence=0;
    async function api(url,body) {
        const response=await fetch(url,body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {});
        const result=await response.json();
        if (!response.ok || result.success===false) throw new Error(result.message || '無法完成，內容尚未保存。');
        return result;
    }
    function status(message) { byId('prStatus').textContent=message; }
    function describe(context) {
        return `目的：${context.purpose}\n主張：${context.claim}\n條件：${context.conditions}\n不能聲稱：${context.forbidden}\n原段落 S.Ver ${context.s_ver}：${context.paragraph_text}\n證據：${context.evidence.source_id} · ${JSON.stringify(context.evidence.locator)}\n${context.evidence.text}`;
    }
    function ensurePanel() {
        if (byId('paragraphReviewPanel')) return;
        const panel=document.createElement('div');
        panel.id='paragraphReviewPanel'; panel.className='offcanvas offcanvas-end'; panel.tabIndex=-1;
        panel.style.width='min(680px,100vw)'; panel.setAttribute('aria-labelledby','prHeading');
        panel.innerHTML=`<div class="offcanvas-header"><h5 id="prHeading">段落主張與版本審閱</h5><button class="btn-close" data-bs-dismiss="offcanvas" aria-label="關閉"></button></div>
        <div class="offcanvas-body"><p id="prTarget" class="small"></p><div id="prStatus" role="status" class="small text-primary mb-2"></div>
        <details open><summary>作者的段落決策</summary><p class="small text-muted">先保存 2B，選取段落，再確認。確認保留作者意圖；證據是否足以支持主張仍需研究者判斷。</p>
        <form id="prForm">
        <label class="form-label w-100">已保存的本段文字<textarea id="prParagraph" class="form-control" rows="3" required maxlength="8000"></textarea></label>
        <label class="form-label w-100">本段目的<textarea id="prPurpose" class="form-control" required maxlength="4000"></textarea></label>
        <label class="form-label w-100">本段主張<textarea id="prClaim" class="form-control" required maxlength="4000"></textarea></label>
        <label class="form-label w-100">已確認納入的證據<select id="prEvidence" class="form-select" required></select></label>
        <button type="button" id="prFromNote" class="btn btn-sm btn-outline-secondary">從已確認閱讀筆記帶入主張</button>
        <pre id="prEvidencePreview" class="small border p-2 mt-2" style="white-space:pre-wrap;max-height:200px;overflow:auto"></pre>
        <label class="form-label w-100">適用條件<textarea id="prConditions" class="form-control" required maxlength="4000" placeholder="樣本、溫度、校正、比較基準等；未知請明寫"></textarea></label>
        <label class="form-label w-100">不能聲稱的事<textarea id="prForbidden" class="form-control" required maxlength="4000"></textarea></label>
        <label class="form-label w-100">仍有矛盾或缺少的脈絡<textarea id="prContradictions" class="form-control" maxlength="4000" placeholder="有未解矛盾時會停止確認；請先處理"></textarea></label>
        <label class="d-block"><input id="prConfirmed" type="checkbox" required> 我已核對主張、證據與適用界線</label>
        <button class="btn btn-primary my-2">確認並保存新決策</button></form>
        <label class="form-label w-100">本次生成使用的決策<select id="prDecision" class="form-select"><option value="">一般寫作（未套用段落決策）</option></select></label>
        <pre id="prDecisionPreview" class="small" style="white-space:pre-wrap"></pre></details>
        <hr><details open><summary>教授意見與我的回覆</summary><label class="form-label w-100">被審閱的全文版本<select id="prReviewVersion" class="form-select"></select></label><div id="prReviews"></div></details>
        </div>`;
        document.body.appendChild(panel);
        byId('prEvidence').onchange=previewSource;
        byId('prFromNote').onclick=()=>{const source=sources[Number(byId('prEvidence').value)]; if (source) { byId('prClaim').value=source.reading_note || source.selection_reason; byId('prConfirmed').checked=false; }};
        byId('prForm').onsubmit=save;
        byId('prDecision').onchange=()=>{
            const row=decisions.find(d=>d.decision_id===byId('prDecision').value);
            window.wsApp.paragraphDecision=row ? {id:row.decision_id,pid:openedPid,section} : null;
            byId('prDecisionPreview').textContent=row ? describe(row.paragraph_context) : '';
        };
        byId('prReviewVersion').onchange=loadReviews;
    }
    function previewSource() {
        const source=sources[Number(byId('prEvidence').value)];
        byId('prEvidencePreview').textContent=source ? `${source.title}\n來源定位：${source.locator || '缺少頁碼；請核對原文'}\n採用理由：${source.selection_reason}\n${source.text}` : '缺少已納入且可定位的證據；請先到 Literature 確認文獻並建立索引。';
    }
    async function loadContext(token) {
        const data=await api(`/manuscript/api/paragraph/context?pid=${encodeURIComponent(openedPid)}&section=${encodeURIComponent(section)}`);
        if (token!==sequence) return;
        sources=data.sources; decisions=data.decisions;
        byId('prEvidence').innerHTML=sources.map((s,i)=>`<option value="${i}">${esc(s.title)} · ${s.evidence_id}</option>`).join('');
        previewSource();
        byId('prDecision').innerHTML='<option value="">一般寫作（未套用段落決策）</option>'+decisions.map(d=>`<option value="${esc(d.decision_id)}" ${d.issues.length ? 'disabled' : ''}>${esc(d.paragraph_context.claim)} · S.Ver ${esc(d.paragraph_context.s_ver)}${d.issues.length ? ' · 需重核：'+esc(d.issues.join('；')) : ''}</option>`).join('');
        const selected=window.wsApp.paragraphDecision;
        if (selected?.pid===openedPid && selected.section===section && decisions.some(d=>d.decision_id===selected.id && !d.issues.length)) byId('prDecision').value=selected.id;
        else window.wsApp.paragraphDecision=null;
        byId('prDecision').onchange();
    }
    async function save(event) {
        event.preventDefault();
        if (window.wsApp.pid!==openedPid || window.wsApp.collab.currentSection()!==section) return status('工作範圍已變更，請關閉後重新開啟。');
        const data={pid:openedPid,section,s_ver:window.wsApp.collab.currentSVer(),confirmed:byId('prConfirmed').checked};
        for (const [key,id] of Object.entries({paragraph_text:'prParagraph',purpose:'prPurpose',claim:'prClaim',conditions:'prConditions',forbidden:'prForbidden',contradictions:'prContradictions'})) data[key]=byId(id).value;
        const source=sources[Number(byId('prEvidence').value)];
        if (!source) return status('缺少可用證據，尚未確認。');
        Object.assign(data,{entry_id:source.entry_id,evidence_id:source.evidence_id});
        try {
            const result=await api('/manuscript/api/paragraph/confirm',data);
            if (data.pid!==openedPid || data.section!==section) return;
            window.wsApp.paragraphDecision={id:result.decision.decision_id,pid:openedPid,section};
            await loadContext(sequence); status('決策已保存並選用。生成仍會重新核對來源及版本。');
        } catch (error) { status(error.message); }
    }
    async function loadReviews() {
        const token=sequence;
        try {
            const version=byId('prReviewVersion').value;
            const result=await api(`/api/mentor/my-reviews/${encodeURIComponent(openedPid)}${version ? '?version='+encodeURIComponent(version) : ''}`);
            if (token!==sequence) return;
            authorReview=result;
            if (!version) byId('prReviewVersion').innerHTML=result.versions.map(v=>`<option>${esc(v.g_ver)}</option>`).join('');
            const selected=byId('prReviewVersion').value;
            const items=result.items.filter(i=>i.paper_version===selected || !i.paper_version);
            byId('prReviews').innerHTML=items.length ? items.map(item=>{
                const c=item.comment;
                return `<article class="border rounded p-2 my-2"><strong>${esc(item.mentor)} · ${esc(item.paper_version || '未標版本（不能推定）')}</strong><p style="white-space:pre-wrap">${esc(item.body || item.title)}</p>
                ${item.reviewer_active === false ? '<p class="text-muted">教授目前無存取權；保留原意見、證據與回覆歷史。寫作與匯出可照常進行。</p>' : ''}
                ${item.context_issues?.length ? `<p class="text-warning-emphasis">需重核：${esc(item.context_issues.join('；'))}。以下保留原確認快照。</p>` : ''}
                ${item.download_url ? `<a href="${esc(item.download_url)}">下載教授附件</a>` : ''}
                ${item.url ? `<a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">開啟資源</a>` : ''}
                ${item.paragraph_decision ? `<details><summary>本段主張與證據</summary><pre style="white-space:pre-wrap">${esc(describe(item.paragraph_decision.paragraph_context))}</pre></details>` : ''}
                ${c ? `<p>狀態：${esc(c.state)} · 審閱版號 ${c.revision}</p>${c.events.map(e=>`<p class="small">${esc(e.action)} · ${esc(e.target_version)}<br>${esc(e.body)}<br>修訂位置：${esc(e.revision_location)}</p>`).join('')}
                <form data-thread="${c.id}" data-rev="${c.revision}"><fieldset ${item.reviewer_active === false ? 'disabled' : ''}><label class="w-100">回覆<textarea name="body" class="form-control" required maxlength="4000"></textarea></label>
                <label class="w-100">修訂全文版本<select name="version" class="form-select">${result.versions.map(v=>`<option>${esc(v.g_ver)}</option>`).join('')}</select></label>
                <label class="w-100">修訂位置（貼上該版本實際段落）<textarea name="location" class="form-control" required maxlength="4000"></textarea></label><button class="btn btn-sm btn-primary my-2">追加作者回覆</button></fieldset></form>` : '<p>舊意見尚未連結版本留言。</p>'}</article>`;
            }).join('') : '<p class="small">教授審閱為選用；此版本尚無意見。寫作、保存、修訂與匯出可照常進行。舊版意見請切換版本查看。</p>';
            byId('prReviews').querySelectorAll('form').forEach(form=>form.onsubmit=async event=>{
                event.preventDefault(); const project=openedPid;
                try { await api(`/manuscript/api/chapter/${encodeURIComponent(project)}/comments/${form.dataset.thread}/events`,{
                    action:'respond',base_rev:Number(form.dataset.rev),body:form.elements.body.value,
                    target_version:form.elements.version.value,revision_location:form.elements.location.value});
                    if (project===openedPid) { status('回覆已保存。原教授可稍後處理；寫作與匯出可照常進行。'); await loadReviews(); }
                } catch(error) {status(error.message);}
            });
        } catch(error) { status(error.message); }
    }
    window.paragraphReview={decisionFor:function (target) {
        const d=window.wsApp.paragraphDecision;
        return d?.pid===window.wsApp.pid && d.section===target ? d.id : undefined;
    },open:async function () {
        ensurePanel(); const app=window.wsApp; const token=++sequence;
        const sameTarget=openedPid===app.pid && section===app.collab.currentSection();
        openedPid=app.pid; section=app.collab.currentSection();
        byId('prTarget').textContent=`${openedPid} · ${section} · S.Ver ${app.collab.currentSVer() || '尚未保存'}`;
        const selection=String(window.getSelection() || '').trim();
        if (!sameTarget) { byId('prForm').reset(); byId('prParagraph').value=selection; }
        byId('prReviewVersion').innerHTML=''; byId('prDecisionPreview').textContent=''; status('載入中…');
        bootstrap.Offcanvas.getOrCreateInstance(byId('paragraphReviewPanel')).show();
        if (!app._paragraphManifestBound && app.socket) {
            app._paragraphManifestBound=true;
            app.socket.on('context_manifest',manifest=>{
                app.soed.addSystemMessage(`本次準備使用：${manifest.items.map(i=>i.source_type+':'+i.source_id).join('、')}。${manifest.notes.join('；')}`);
            });
        }
        try { await loadContext(token); if (token===sequence) {status('請核對來源、條件與目前版本。'); await loadReviews();} }
        catch(error) {status(error.message);}
    }};
})();
