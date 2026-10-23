// 目的：讓作者選用教授／團隊協作；使用 textContent，邀請碼不進 URL、日誌或瀏覽器儲存。
// 上下游：collaboration.html → CSRF 保護的邀請 API；授權與歷史由伺服器驗證。
// 驗證：node --check app/static/js/collaboration.js；test_sai_invitations.py 與合成瀏覽器。
(() => {
    'use strict';
    const el = id => document.getElementById(id);
    const message = value => { el('collaboration-message').textContent = value; };
    const labels = {pending:'待接受',accepted:'已接受',revoked:'已撤銷',declined:'已婉拒'};
    async function api(path, body) {
        const response = await fetch(path, {method:body === undefined ? 'GET':'POST',
            headers:{'Content-Type':'application/json','X-CSRFToken':el('collaboration-csrf').value},
            ...(body === undefined ? {} : {body:JSON.stringify(body)})});
        const result = await response.json();
        if (!response.ok || result.success === false) throw new Error(result.message || result.error || `請求失敗 ${response.status}`);
        return result;
    }
    function button(parent, text, fn) {
        const node = document.createElement('button');
        node.type = 'button'; node.className = 'btn btn-sm btn-outline-primary me-2 mt-2'; node.textContent = text;
        node.addEventListener('click', async () => {
            node.disabled = true;
            try { await fn(); } catch (error) { message(error.message); } finally { node.disabled = false; }
        });
        parent.append(node);
    }
    function render(target, rows, inbox) {
        target.replaceChildren();
        if (!rows.length) { target.textContent = '目前沒有邀請。獨立研究可以照常進行。'; return; }
        for (const row of rows) {
            const card = document.createElement('div'); card.className = 'border rounded p-3 mb-2';
            const title = document.createElement('p');
            title.textContent = `${row.pid} · ${row.invited_email} · ${row.kind === 'professor'?'教授審閱':'團隊協作'} · ${row.role}${row.sections.length?' / '+row.sections.join(', '):''} · ${labels[row.status] || row.status}${row.expired && row.status === 'pending'?'（邀請碼期限已過）':''}`;
            card.append(title);
            const history = document.createElement('p'); history.className = 'small text-muted';
            history.textContent = row.history.map(event => `${event.action} ${event.created_at}${event.detail.reason?'：'+event.detail.reason:''}`).join(' → ');
            card.append(history);
            if (inbox && row.status === 'pending' && !row.expired) {
                const code = document.createElement('input'); code.type = 'password'; code.autocomplete = 'off'; code.className = 'form-control'; code.placeholder = '邀請者另行交付的邀請碼'; code.setAttribute('aria-label','一次性邀請碼'); card.append(code);
                button(card,'接受邀請',async () => { await api(`/api/collaboration/invitations/${row.id}/accept`,{token:code.value}); message('已接受。可進入此專案；研究者仍可自行完成研究。'); await loadInbox(); });
                button(card,'婉拒',async () => { await api(`/api/collaboration/invitations/${row.id}/decline`,{}); await loadInbox(); });
            }
            if (!inbox && ['pending','accepted'].includes(row.status)) {
                const reason = document.createElement('input'); reason.className = 'form-control'; reason.maxLength = 1000; reason.placeholder = '撤銷理由'; reason.setAttribute('aria-label','撤銷理由'); card.append(reason);
                button(card,'撤銷邀請／移除存取',async () => { await api(`/api/collaboration/invitations/${row.id}/revoke`,{reason:reason.value}); message('已撤銷存取。研究檔案與審閱歷史保留。'); await loadProject(); });
            }
            target.append(card);
        }
    }
    const loadInbox = async () => render(el('invitation-inbox'),(await api('/api/collaboration/invitations')).invitations,true);
    const loadProject = async () => render(el('project-invitations'),(await api(`/api/collaboration/project/${encodeURIComponent(el('collaboration-pid').value.trim())}/invitations`)).invitations,false);
    el('academic-profile').addEventListener('submit', async event => {
        event.preventDefault();
        try { await api('/api/collaboration/profile',{academic_role:el('academic-role').value,affiliation:el('academic-affiliation').value}); message('學術背景已保存。職銜不授予專案或管理權限。'); } catch(error) { message(error.message); }
    });
    el('invitation-project').addEventListener('submit',async event => { event.preventDefault(); try { await loadProject(); } catch(error) { message(error.message); } });
    function syncKind() {
        const professor = el('invite-kind').value === 'professor';
        if (professor) el('invite-role').value = 'viewer';
        el('invite-role').disabled = professor;
        el('invite-sections').disabled = professor || el('invite-role').value !== 'coauthor';
    }
    el('invite-kind').addEventListener('change',syncKind); el('invite-role').addEventListener('change',syncKind); syncKind();
    el('create-invitation').addEventListener('submit',async event => {
        event.preventDefault(); el('invitation-code-area').hidden = true; el('invitation-code').value = '';
        try {
            const result = await api(`/api/collaboration/project/${encodeURIComponent(el('collaboration-pid').value.trim())}/invitations`,{
                email:el('invite-email').value,kind:el('invite-kind').value,role:el('invite-role').value,
                sections:el('invite-role').value === 'coauthor' ? el('invite-sections').value.split(',').map(v=>v.trim()).filter(Boolean):[]});
            el('invitation-code').value = result.token; el('invitation-code-area').hidden = false;
            message('邀請已建立，尚未授權。對方接受後才生效。'); await loadProject();
        } catch(error) { message(error.message); }
    });
    el('collaboration-pid').value = new URL(location.href).searchParams.get('pid') || '';
    Promise.all([loadInbox(),api('/api/collaboration/profile').then(result => {
        el('academic-role').value = result.profile.academic_role; el('academic-affiliation').value = result.profile.affiliation;
    })]).catch(error => message(error.message));
})();
