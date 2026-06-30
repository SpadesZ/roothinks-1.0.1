/* 路徑(./app/static/js/dashboard.js) #版本 v1.8 #更版時間 20260312-2230 */

document.addEventListener('DOMContentLoaded', () => {
    const params = new URLSearchParams(window.location.search);
    const initialStatus = params.get('status') === 'formal' ? 'formal' : 'temp';
    switchDashboardView(initialStatus);
});

let currentStatus = 'temp';
let allProjectsCache = []; 

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// ==========================================
// 1. 視圖切換 (View Switching)
// ==========================================
async function switchDashboardView(status) {
    currentStatus = status;

    document.querySelectorAll('#dashboardTabs .nav-link').forEach(el => el.classList.remove('active'));
    document.getElementById(`tab-${status}`).classList.add('active');

    document.getElementById('view-temp').classList.add('d-none');
    document.getElementById('view-formal').classList.add('d-none');
    document.getElementById(`view-${status}`).classList.remove('d-none');

    // 完全獨立控制兩個按鈕區塊的顯示
    if (status === 'temp') {
        document.getElementById('btn-group-temp').classList.remove('d-none');
        document.getElementById('btn-group-formal').classList.add('d-none');
    } else {
        document.getElementById('btn-group-temp').classList.add('d-none');
        document.getElementById('btn-group-formal').classList.remove('d-none');
    }

    await fetchProjects(status);
}

// ==========================================
// 1.1 View State Helper
// ==========================================
function showViewState(status, state, errorMsg = '') {
    const viewPrefix = status === 'temp' ? 'temp' : 'formal';
    const loadingEl = document.getElementById(`loading-${viewPrefix}`);
    const containerEl = document.getElementById(`container-${viewPrefix}`);
    const emptyEl = document.getElementById(`empty-${viewPrefix}`);

    if (loadingEl) loadingEl.classList.add('d-none');
    if (containerEl) containerEl.classList.add('d-none');
    if (emptyEl) emptyEl.classList.add('d-none');

    if (state === 'loading' && loadingEl) {
        loadingEl.classList.remove('d-none');
    } else if (state === 'content' && containerEl) {
        containerEl.classList.remove('d-none');
    } else if (state === 'empty' && emptyEl) {
        emptyEl.classList.remove('d-none');
    } else if (state === 'error' && containerEl) {
        containerEl.classList.remove('d-none');
        containerEl.innerHTML = `
            <div class="col-12 text-center py-5 text-danger">
                <i class="bi bi-exclamation-triangle-fill display-4 d-block mb-2"></i>
                <p class="fw-bold">載入失敗 (Load Failed)</p>
                <p class="text-muted small">${escapeHtml(errorMsg)}</p>
            </div>
        `;
    }
}

// ==========================================
// 2. 資料獲取與渲染 (Fetching & Rendering)
// ==========================================
async function fetchProjects(status) {
    showViewState(status, 'loading');

    try {
        let projects = [];
        
        if (status === 'temp') {
            const [resTemp, resRO] = await Promise.all([
                fetch(`/api/project/list?status=temp`),
                fetch(`/api/project/list?status=readonly`)
            ]);
            const dataTemp = await resTemp.json();
            const dataRO = await resRO.json();

            if (dataTemp.success && dataTemp.projects) projects.push(...dataTemp.projects);
            if (dataRO.success && dataRO.projects) projects.push(...dataRO.projects);

            projects.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
        } else {
            const res = await fetch(`/api/project/list?status=${status}`);
            const data = await res.json();
            if (data.success && data.projects) projects = data.projects;
        }

        allProjectsCache = projects; 
        
        if (projects.length === 0) {
            showViewState(status, 'empty');
        } else {
            showViewState(status, 'content');
            if (status === 'temp') {
                renderTempCards(projects);
            } else {
                renderFormalCards(projects);
            }
        }
    } catch (e) {
        console.error(e);
        showViewState(status, 'error', 'Network Error: ' + (e.message || 'Connection Failed'));
    }
}

function renderTempCards(projects) {
    const container = document.getElementById('container-temp');
    if (!container) return; 
    container.innerHTML = '';

    projects.forEach(p => {
        const projectId = String(p.project_id || '');
        const safePidJs = projectId.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
        const projectIdUrl = encodeURIComponent(projectId);

        let piName = 'Unknown';
        if (p.members && Array.isArray(p.members)) {
            const pi = p.members.find(m => m.role && m.role.includes('主持人'));
            if (pi) {
                if (pi.name && typeof pi.name === 'object' && pi.name.en) {
                    piName = `${pi.name.en.given || ''} ${pi.name.en.surname || ''}`.trim();
                } else if (typeof pi.name === 'string') {
                    piName = pi.name;
                }
            }
        }
        
        const motivationRaw = (p.context_background || p.background || '').substring(0, 100) + (p.context_background?.length > 100 ? '...' : '');
        const motivation = escapeHtml(motivationRaw || '無背景描述...');
        const safeName = escapeHtml(p.name || '');
        const safeAbbr = escapeHtml(p.abbreviation || '');
        const safePiName = escapeHtml(piName);
        let timeStr = '--';
        if (p.created_at) timeStr = p.created_at.substring(0, 10);
        const safeTime = escapeHtml(timeStr);

        const isReadonly = p.status === 'readonly';
        const cardStyle = isReadonly ? 'background-color: #f8f9fa; opacity: 0.95;' : 'background-color: #f0f8ff;';
        
        const badgeHtml = isReadonly 
            ? `<span class="badge bg-secondary bg-opacity-10 text-secondary border border-secondary border-opacity-25 rounded-pill px-2" title="此專案已轉正，僅供唯讀"><i class="bi bi-lock-fill"></i> ${escapeHtml(projectId)}</span>`
            : `<span class="badge bg-primary bg-opacity-10 text-primary border border-primary border-opacity-25 rounded-pill px-2">${escapeHtml(projectId)}</span>`;
            
        const actionMenu = isReadonly
            ? `<li><a class="dropdown-item" href="#" onclick="openCreateModal('${safePidJs}', true)"><i class="bi bi-eye me-2"></i>檢視 (View)</a></li>`
            : `<li><a class="dropdown-item" href="#" onclick="openCreateModal('${safePidJs}')"><i class="bi bi-pencil me-2"></i>編輯 (Edit)</a></li>`;
            
        const enterBtn = isReadonly
            ? `<a href="/paq?pid=${projectIdUrl}" class="btn btn-outline-secondary w-100 fw-bold"><i class="bi bi-lock-fill me-1"></i> 唯讀工作檯 (Read-Only)</a>`
            : `<a href="/paq?pid=${projectIdUrl}" class="btn btn-outline-primary w-100 fw-bold"><i class="bi bi-box-arrow-in-right me-1"></i> 進入 PAQ 工作檯</a>`;

        const col = document.createElement('div');
        col.className = 'col-md-6 col-lg-4';
        
        col.innerHTML = `
            <div class="card h-100 shadow-sm project-card" style="${cardStyle}">
                <div class="card-body">
                    <div class="d-flex justify-content-between align-items-start mb-2">
                        ${badgeHtml}
                        <div class="dropdown">
                            <button class="btn btn-link text-muted p-0" data-bs-toggle="dropdown">
                                <i class="bi bi-three-dots-vertical"></i>
                            </button>
                            <ul class="dropdown-menu dropdown-menu-end shadow">
                                ${actionMenu}
                                <li><hr class="dropdown-divider"></li>
                                <li><a class="dropdown-item text-danger" href="#" onclick="deleteProject(event, '${safePidJs}')"><i class="bi bi-trash me-2"></i>刪除 (Delete)</a></li>
                            </ul>
                        </div>
                    </div>
                    
                    <h5 class="card-title fw-bold ${isReadonly ? 'text-secondary' : 'text-primary'} mb-1 text-wrap text-break cursor-pointer" style="text-decoration: underline;" title="點擊檢視專案詳情" onclick="viewProjectReadOnly('${safePidJs}', 'temp')">
                        ${safeName}
                    </h5>
                    <p class="card-subtitle text-muted small mb-3">${safeAbbr}</p>
                    
                    <div class="mb-3">
                        <div class="d-flex align-items-center mb-1">
                            <i class="bi bi-person-circle text-secondary me-2"></i>
                            <span class="text-dark small fw-bold">${safePiName}</span>
                        </div>
                        <div class="d-flex align-items-center text-muted small">
                            <i class="bi bi-clock me-2"></i>
                            <span>${safeTime}</span>
                        </div>
                    </div>

                    <p class="card-text text-secondary small text-truncate-2" style="min-height: 40px;">
                        ${motivation}
                    </p>
                </div>
                <div class="card-footer bg-white border-top-0 pt-0 pb-3">
                    ${enterBtn}
                </div>
            </div>
        `;
        container.appendChild(col);
    });
}

function renderFormalCards(projects) {
    const container = document.getElementById('container-formal');
    if (!container) return;
    container.innerHTML = '';

    projects.forEach(p => {
        const projectId = String(p.project_id || '');
        const safePidJs = projectId.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
        const projectIdUrl = encodeURIComponent(projectId);
        let piName = 'Unknown';
        if (p.members && Array.isArray(p.members)) {
            const pi = p.members.find(m => m.role && m.role.includes('主持人'));
            if (pi) {
                if (pi.name && typeof pi.name === 'object' && pi.name.en) {
                    piName = `${pi.name.en.given || ''} ${pi.name.en.surname || ''}`.trim();
                } else if (typeof pi.name === 'string') {
                    piName = pi.name;
                }
            }
        }
        
        const summaryRaw = (p.ai_summary || '').substring(0, 100) + (p.ai_summary?.length > 100 ? '...' : '尚未生成 AI 摘要');
        const summary = escapeHtml(summaryRaw);
        const safeResearchTitle = escapeHtml(p.research_title || p.name || '');
        const safeAbbr = escapeHtml(p.abbreviation || '');
        const safePiName = escapeHtml(piName);
        let timeStr = '--';
        if (p.created_at) timeStr = p.created_at.substring(0, 10);
        const safeTime = escapeHtml(timeStr);

        const col = document.createElement('div');
        col.className = 'col-md-6 col-lg-4';
        
        col.innerHTML = `
            <div class="card h-100 shadow-sm project-card" style="background-color: #f5fff5; border-color: #c3e6cb;">
                <div class="card-body">
                    <div class="d-flex justify-content-between align-items-start mb-2">
                        <span class="badge bg-success bg-opacity-10 text-success border border-success border-opacity-25 rounded-pill px-2">
                            ${escapeHtml(projectId)}
                        </span>
                        <div class="dropdown">
                            <button class="btn btn-link text-muted p-0" data-bs-toggle="dropdown">
                                <i class="bi bi-three-dots-vertical"></i>
                            </button>
                            <ul class="dropdown-menu dropdown-menu-end shadow">
                                <li><a class="dropdown-item" href="#" onclick="openCreateFormalModal('${safePidJs}')"><i class="bi bi-pencil me-2"></i>編輯 (Edit)</a></li>
                                <li><hr class="dropdown-divider"></li>
                                <li><a class="dropdown-item text-danger" href="#" onclick="deleteProject(event, '${safePidJs}')"><i class="bi bi-trash me-2"></i>刪除 (Delete)</a></li>
                            </ul>
                        </div>
                    </div>
                    
                    <h5 class="card-title fw-bold text-success mb-1 text-wrap text-break cursor-pointer" style="text-decoration: underline;" title="點擊檢視專案詳情" onclick="viewProjectReadOnly('${safePidJs}', 'formal')">
                        ${safeResearchTitle}
                    </h5>
                    <p class="card-subtitle text-muted small mb-3">${safeAbbr}</p>
                    
                    <div class="mb-3">
                        <div class="d-flex align-items-center mb-1">
                            <i class="bi bi-person-circle text-secondary me-2"></i>
                            <span class="text-dark small fw-bold">${safePiName}</span>
                        </div>
                        <div class="d-flex align-items-center text-muted small">
                            <i class="bi bi-clock me-2"></i>
                            <span>${safeTime}</span>
                        </div>
                    </div>

                    <div class="card-text small text-truncate-2 p-2 bg-white rounded border border-success border-opacity-25" style="min-height: 50px;">
                        <i class="bi bi-robot text-success me-1"></i> ${summary}
                    </div>
                </div>
                <div class="card-footer bg-white border-top-0 pt-0 pb-3">
                    <div class="row g-2">
                        <div class="col-6">
                            <a href="/literature?pid=${projectIdUrl}" class="btn btn-success w-100 fw-bold">
                                <i class="bi bi-journal-check me-1"></i> 正式工作檯
                            </a>
                        </div>
                        <div class="col-6">
                            <a href="/manuscript?pid=${projectIdUrl}" class="btn btn-outline-warning w-100 fw-bold">
                                <i class="bi bi-pen me-1"></i> Manuscript
                            </a>
                        </div>
                    </div>
                </div>
            </div>
        `;
        container.appendChild(col);
    });
}

// ==========================================
// 3. 專案建立與編輯 (兩條獨立路徑)
// ==========================================

let isEdit = false;
let editPid = null;

function viewProjectReadOnly(pid, type) {
    if (type === 'formal') {
        openCreateFormalModal(pid, true);
    } else {
        openCreateModal(pid, true);
    }
}

/**
 * 路徑一：獨立的「待擬研究」Modal 控制器
 */
function openCreateModal(pid = null, readOnly = false) {
    const form = document.getElementById('projectForm');
    if(form) form.reset();
    document.getElementById('c-personnel-container').innerHTML = '';
    
    document.getElementById('c_target_status').value = 'temp'; // 指定建立 temp

    const titleEl = document.getElementById('createModalLabel');
    const btnEl = document.getElementById('btn-submit-project');
    const headerBg = document.getElementById('modal-header-bg');
    const aiSummaryBlock = document.getElementById('ai-summary-block');
    
    // 待擬研究一律隱藏 AI 摘要
    aiSummaryBlock.style.display = 'none';
    headerBg.className = 'modal-header bg-primary text-white';

    if (pid) {
        const project = allProjectsCache.find(p => p.project_id === pid);
        isEdit = !readOnly;
        editPid = isEdit ? pid : null;
        
        titleEl.innerHTML = readOnly ? `<i class="bi bi-eye"></i> 檢視待擬研究 (View Project: #${pid})` : `<i class="bi bi-pencil-square"></i> 編輯待擬研究 (Edit Project: #${pid})`;
        
        if (btnEl) {
            btnEl.className = 'btn btn-primary px-4';
            btnEl.innerHTML = '<i class="bi bi-save"></i> 更新專案 (Update)';
            if (readOnly) btnEl.classList.add('d-none'); else btnEl.classList.remove('d-none');
        }

        document.getElementById('c_name').value = project.name || '';
        document.getElementById('c_abbr').value = project.abbreviation || '';
        document.getElementById('c_class').value = project.classification || '';
        document.getElementById('c_keywords').value = project.keywords || '';
        document.getElementById('c_context').value = project.context_background || project.background || '';

        if (project.members && project.members.length > 0) project.members.forEach(m => addMemberRow(m)); else addMemberRow();

    } else {
        isEdit = false;
        editPid = null;
        titleEl.innerHTML = '<i class="bi bi-plus-circle"></i> 新增待擬研究 (New Provisional)';
        if(btnEl) {
            btnEl.classList.remove('d-none');
            btnEl.className = 'btn btn-primary px-4';
            btnEl.innerHTML = '<i class="bi bi-rocket-takeoff"></i> 建立專案 (Create)';
        }
        addMemberRow();
    }
    
    applyReadOnlyState(form, readOnly);
    new bootstrap.Modal(document.getElementById('createModal')).show();
}

/**
 * 路徑二：完全獨立的「正式研究」Modal 控制器
 */
function openCreateFormalModal(pid = null, readOnly = false) {
    const form = document.getElementById('projectForm');
    if(form) form.reset();
    document.getElementById('c-personnel-container').innerHTML = '';
    
    document.getElementById('c_target_status').value = 'formal'; // 指定建立 formal

    const titleEl = document.getElementById('createModalLabel');
    const btnEl = document.getElementById('btn-submit-project');
    const headerBg = document.getElementById('modal-header-bg');
    const aiSummaryBlock = document.getElementById('ai-summary-block');
    
    // 1000000% 必須展示 AI 摘要
    aiSummaryBlock.style.display = 'block';
    headerBg.className = 'modal-header bg-success text-white';

    if (pid) {
        const project = allProjectsCache.find(p => p.project_id === pid);
        isEdit = !readOnly;
        editPid = isEdit ? pid : null;
        
        titleEl.innerHTML = readOnly ? `<i class="bi bi-eye"></i> 檢視正式研究 (View Formal: #${pid})` : `<i class="bi bi-pencil-square"></i> 編輯正式研究 (Edit Formal: #${pid})`;
        
        if (btnEl) {
            btnEl.className = 'btn btn-success px-4';
            btnEl.innerHTML = '<i class="bi bi-save"></i> 更新正式專案 (Update)';
            if (readOnly) btnEl.classList.add('d-none'); else btnEl.classList.remove('d-none');
        }

        document.getElementById('c_name').value = project.name || '';
        document.getElementById('c_abbr').value = project.abbreviation || '';
        document.getElementById('c_class').value = project.classification || '';
        document.getElementById('c_keywords').value = project.keywords || '';
        document.getElementById('c_context').value = project.context_background || project.background || '';
        document.getElementById('c_ai_summary').value = project.ai_summary || '等待系統執行摘要...';

        if (project.members && project.members.length > 0) project.members.forEach(m => addMemberRow(m)); else addMemberRow();

    } else {
        isEdit = false;
        editPid = null;
        titleEl.innerHTML = '<i class="bi bi-plus-circle"></i> 新增正式研究 (New Formal Project)';
        document.getElementById('c_ai_summary').value = '等待系統執行摘要...'; // 預設字串

        if(btnEl) {
            btnEl.classList.remove('d-none');
            btnEl.className = 'btn btn-success px-4';
            btnEl.innerHTML = '<i class="bi bi-rocket-takeoff"></i> 建立正式專案 (Create Formal)';
        }
        addMemberRow();
    }
    
    applyReadOnlyState(form, readOnly);
    new bootstrap.Modal(document.getElementById('createModal')).show();
}

function applyReadOnlyState(form, readOnly) {
    if (!form) return;
    const inputs = form.querySelectorAll('input, select, textarea:not(#c_ai_summary)');
    const actionBtns = form.querySelectorAll('.btn-outline-danger, .btn-add-email, .btn-remove-email');
    const addPersonBtn = document.querySelector('#createModal .card-header .btn-success');
    
    if (readOnly) {
        inputs.forEach(el => el.setAttribute('disabled', 'true'));
        actionBtns.forEach(el => el.style.display = 'none');
        if (addPersonBtn) addPersonBtn.classList.add('d-none');
    } else {
        inputs.forEach(el => el.removeAttribute('disabled'));
        actionBtns.forEach(el => el.style.display = '');
        if (addPersonBtn) addPersonBtn.classList.remove('d-none');
    }
}

function addMemberRow(data = null) {
    const container = document.getElementById('c-personnel-container');
    const div = document.createElement('div');
    div.className = 'member-card member-row card mb-3 border-start border-4 border-info shadow-sm';
    
    const safeGet = (obj, path, def = '') => {
        try {
            return path.split('.').reduce((o, k) => (o || {})[k], obj) || def;
        } catch (e) { return def; }
    };

    const role = data ? data.role : '主持人 (Principal Investigator)';
    const title = data ? data.title : '';
    const enSurname = data ? safeGet(data, 'name.en.surname') : '';
    const enGiven = data ? safeGet(data, 'name.en.given') : '';
    const enMiddle = data ? safeGet(data, 'name.en.middle') : '';
    const orgSurname = data ? safeGet(data, 'name.original.surname') : '';
    const orgGiven = data ? safeGet(data, 'name.original.given') : '';
    const orgMiddle = data ? safeGet(data, 'name.original.middle') : '';
    const orgL1 = data ? safeGet(data, 'organization.l1') : '';
    const orgL2 = data ? safeGet(data, 'organization.l2') : '';
    const orgL3 = data ? safeGet(data, 'organization.l3') : '';
    const address = data ? data.address : '';
    
    let emailHtml = '';
    const emails = (data && data.emails && Array.isArray(data.emails)) ? data.emails : ['']; 
    if (emails.length === 0) emails.push('');
    
    emails.forEach((email, idx) => {
        emailHtml += `
            <div class="email-row d-flex align-items-center mt-1">
                <input type="email" class="form-control form-control-sm email-input" placeholder="email@example.com" value="${email}">
                ${idx === 0 
                    ? `<i class="bi bi-plus-circle-fill ms-2 btn-add-email text-success cursor-pointer" onclick="addEmailField(this)" title="Add Email" style="${emails.length >=3 ? 'display:none':''}"></i>`
                    : `<i class="bi bi-dash-circle-fill ms-2 btn-remove-email text-danger cursor-pointer" onclick="removeEmailField(this)" title="Remove"></i>`
                }
            </div>
        `;
    });

    div.innerHTML = `
        <div class="card-body p-3">
            <div class="d-flex justify-content-between mb-2">
                <h6 class="fw-bold text-info"><i class="bi bi-person-vcard"></i> 成員資料 (Member Profile)</h6>
                <button type="button" class="btn btn-sm btn-outline-danger border-0" onclick="this.closest('.member-card').remove()">
                    <i class="bi bi-trash"></i> 移除
                </button>
            </div>
            
            <div class="row g-2 mb-2">
                <div class="col-md-6">
                    <label class="small text-muted">角色 (Role)</label>
                    <select class="form-select form-select-sm role-select">
                        <option value="主持人 (Principal Investigator)" ${role.includes('主持人') ? 'selected' : ''}>1. 主持人 (Principal Investigator)</option>
                        <option value="共同主持人 (Co-PI)" ${role.includes('共同主持人') ? 'selected' : ''}>2. 共同主持人 (Co-PI)</option>
                        <option value="主貢獻人員 (Principal Contributor)" ${role.includes('主貢獻') ? 'selected' : ''}>3. 主貢獻人員 (Principal Contributor)</option>
                        <option value="通信窗口 (Correspondent)" ${role.includes('通信') ? 'selected' : ''}>4. 通信窗口 (Correspondent)</option>
                        <option value="合作人員 (Collaborator)" ${role.includes('合作') ? 'selected' : ''}>5. 合作人員 (Collaborator)</option>
                        <option value="支援人員 (Supporter)" ${role.includes('支援') ? 'selected' : ''}>6. 支援人員 (Supporter)</option>
                    </select>
                </div>
                <div class="col-md-6">
                    <label class="small text-muted">職銜 (Title)</label>
                    <input type="text" class="form-control form-control-sm title-input" placeholder="e.g. Professor" value="${title}">
                </div>
            </div>

            <div class="row g-2 mb-2 bg-light p-1 rounded">
                <div class="col-12"><small class="fw-bold text-dark">英文姓名 (English Name - Primary)</small></div>
                <div class="col-md-4">
                    <input type="text" class="form-control form-control-sm en-surname" placeholder="Surname" value="${enSurname}">
                </div>
                <div class="col-md-4">
                    <input type="text" class="form-control form-control-sm en-given" placeholder="Given Name" value="${enGiven}">
                </div>
                <div class="col-md-4">
                    <input type="text" class="form-control form-control-sm en-middle" placeholder="Middle Name" value="${enMiddle}">
                </div>
            </div>

            <div class="row g-2 mb-2">
                <div class="col-12"><small class="fw-bold text-secondary">原文姓名 (Native/Original Name)</small></div>
                <div class="col-md-4">
                    <input type="text" class="form-control form-control-sm org-surname" placeholder="姓" value="${orgSurname}">
                </div>
                <div class="col-md-4">
                    <input type="text" class="form-control form-control-sm org-given" placeholder="名" value="${orgGiven}">
                </div>
                <div class="col-md-4">
                    <input type="text" class="form-control form-control-sm org-middle" placeholder="中間名" value="${orgMiddle}">
                </div>
            </div>

            <div class="row g-2 mb-2 border-top pt-2">
                <div class="col-12"><small class="fw-bold text-secondary">所屬單位 (Organization Hierarchy)</small></div>
                <div class="col-md-4">
                    <input type="text" class="form-control form-control-sm org-l1" placeholder="L1: Institution" value="${orgL1}">
                </div>
                <div class="col-md-4">
                    <input type="text" class="form-control form-control-sm org-l2" placeholder="L2: Dept/Div" value="${orgL2}">
                </div>
                <div class="col-md-4">
                    <input type="text" class="form-control form-control-sm org-l3" placeholder="L3: Lab/Unit" value="${orgL3}">
                </div>
            </div>

            <div class="row g-2 mb-2">
                <div class="col-12">
                    <label class="small text-muted">電子郵件 (Emails)</label>
                    <div class="email-container">
                        ${emailHtml}
                    </div>
                </div>
            </div>

            <div class="row g-2">
                <div class="col-12">
                    <label class="small text-muted">通訊地址 (Address)</label>
                    <input type="text" class="form-control form-control-sm address-input" placeholder="完整通訊地址" value="${address}">
                </div>
            </div>
        </div>
    `;
    container.appendChild(div);
}

function addEmailField(btn) {
    const container = btn.closest('.email-container');
    const rows = container.querySelectorAll('.email-row');
    if (rows.length >= 3) {
        alert('最多只能新增 3 組 Email (Max 3 emails allowed)');
        return;
    }
    const newRow = document.createElement('div');
    newRow.className = 'email-row d-flex align-items-center mt-1';
    newRow.innerHTML = `
        <input type="email" class="form-control form-control-sm email-input" placeholder="Next email...">
        <i class="bi bi-dash-circle-fill ms-2 btn-remove-email text-danger cursor-pointer" onclick="removeEmailField(this)" title="Remove"></i>
    `;
    container.appendChild(newRow);
    
    if (container.querySelectorAll('.email-row').length >= 3) {
        const firstRowBtn = container.querySelector('.btn-add-email');
        if(firstRowBtn) firstRowBtn.style.display = 'none';
    }
}

function removeEmailField(btn) {
    const container = btn.closest('.email-container');
    btn.closest('.email-row').remove();
    
    if (container.querySelectorAll('.email-row').length < 3) {
        const firstRowBtn = container.querySelector('.btn-add-email');
        if(firstRowBtn) firstRowBtn.style.display = 'inline-block';
    }
}

async function submitCreate() {
    const name = document.getElementById('c_name').value.trim();
    const abbreviation = document.getElementById('c_abbr').value.trim(); 
    const classification = document.getElementById('c_class').value.trim();
    const keywords = document.getElementById('c_keywords').value.trim();
    const context = document.getElementById('c_context').value.trim();
    
    // 讀取隱藏欄位，確認是要建立 temp 還是 formal
    const statusVal = document.getElementById('c_target_status') ? document.getElementById('c_target_status').value : 'temp';
    
    // [v1.8 Update] 嚴格攔截 Table 1 基本資料缺漏
    if (!name || !abbreviation || !classification || !keywords || !context) {
        alert("請完整填寫 Table 1 的所有基本資訊欄位 (包含專案名稱、簡稱、分類、關鍵字與背景)！");
        return;
    }

    const members = [];
    const memberCards = document.querySelectorAll('.member-card');
    
    for (const card of memberCards) {
        const emails = [];
        card.querySelectorAll('.email-input').forEach(input => {
            if(input.value.trim()) emails.push(input.value.trim());
        });

        members.push({
            role: card.querySelector('.role-select').value,
            title: card.querySelector('.title-input').value.trim(),
            name: {
                en: {
                    surname: card.querySelector('.en-surname').value.trim(),
                    given: card.querySelector('.en-given').value.trim(),
                    middle: card.querySelector('.en-middle').value.trim()
                },
                original: {
                    surname: card.querySelector('.org-surname').value.trim(),
                    given: card.querySelector('.org-given').value.trim(),
                    middle: card.querySelector('.org-middle').value.trim()
                },
            },
            organization: {
                l1: card.querySelector('.org-l1').value.trim(),
                l2: card.querySelector('.org-l2').value.trim(),
                l3: card.querySelector('.org-l3').value.trim()
            },
            emails: emails,
            address: card.querySelector('.address-input').value.trim()
        });
    }

    // [v1.8 Update] 精確攔截主持人缺漏：若無主持人，或主持人姓名與單位空白，嚴格阻擋並給出唯一指定提示
    const pis = members.filter(m => m.role.includes('主持人'));
    if (pis.length === 0) {
        alert("請確實填寫主持人成員資料");
        return;
    } else if (pis.length !== 1) {
        alert("必須且只能有一位「主持人 (Principal Investigator)」 (Exactly one PI is required)");
        return;
    } else {
        const pi = pis[0];
        const hasEnName = pi.name.en.surname || pi.name.en.given;
        const hasOrgName = pi.name.original.surname || pi.name.original.given;
        const hasOrgL1 = pi.organization.l1;
        
        if (!(hasEnName || hasOrgName) || !hasOrgL1) {
            alert("請確實填寫主持人成員資料");
            return;
        }
    }

    const payload = { 
        name, abbreviation, context, classification, keywords, members,
        status: statusVal // 送交給後端判定
    };

    try {
        let url = '/api/project/create';
        if (isEdit) url = `/api/project/update/${editPid}`;

        const res = await fetch(url, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        const result = await res.json();
        
        if (result.success) {
            bootstrap.Modal.getInstance(document.getElementById('createModal')).hide();
            fetchProjects(currentStatus); 
            alert(isEdit ? '專案更新成功 (Updated)' : '專案建立成功 (Created)');
        } else {
            alert((isEdit ? '更新失敗: ' : '建立失敗: ') + result.message);
        }
    } catch(e) {
        console.error(e);
        alert('API Error: 無法連接伺服器');
    }
}

async function deleteProject(event, pid) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }

    if (!confirm(`確定要刪除專案 ${pid} 嗎？\n此動作僅刪除目前專案，不會連帶刪除配對專案。`)) return;

    try {
        const encodedPid = encodeURIComponent(pid);
        const res = await fetch(`/api/project/delete/${encodedPid}?scope=single`, {
            method: 'DELETE'
        });
        const result = await res.json();
        
        if (result.success) {
            fetchProjects(currentStatus);
        } else {
            alert('刪除失敗: ' + result.message);
        }
    } catch (e) {
        alert('系統錯誤: ' + e.message);
    }
}
