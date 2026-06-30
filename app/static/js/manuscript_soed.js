//路徑(./app/static/js/manuscript_soed.js)
//版本 v0.8 (Rich-Text Formatting with Focus Guard + Word import event binding)
//更版時間 20260421-1445
// inner comment: 嚴格保留 v0.5 全量代碼與防呆邏輯。將 Mermaid 語法攔截並委託給 ManuImage 引擎處理視覺化與實體轉檔。
// CHANGE_PLAN_STUDY_FLOWB_2026-04-20: MVP prototype - wire direct .docx importer event to UI handler.

class ManuSoed {
    constructor(app) {
        this.app = app;
        this.activeJobId = null;
        this.typingTimeoutHandle = null;
        this.typingWarnMs = 120000;
    }

    // =========================================================================
    // 畫布內容注入與複製邏輯 (Canvas Content Injection)
    // =========================================================================
    importToEditor(content) {
        if (!content || content.includes('bi-arrow-left') || content.includes('恢復 2A')) {
            console.error("[logic violation] prevented copying non-academic metadata.");
            alert("錯誤：系統攔截了非草稿內容。請點擊草稿桌布（黃底區塊）中的藍色按鈕。");
            return;
        }
        const currentSection = this.app.drafterTargetSection.value;
        this.app.selectedSections = new Set([currentSection]);
        this.app.ui.updateDropdownLabel();

        // 專注模式：如果傳入的是實體圖片標籤，保留並附加；若是文字則清空舊畫布
        if (content.includes('<img src=')) {
            this.insertEditorCard(content, currentSection);
        } else {
            this.app.editorCanvas.innerHTML = '';
            this.app.editorCanvas.focus();
            this.insertEditorCard(content, currentSection);
        }
    }

    insertEditorCard(content, sectionId = 'general') {
        const sectionLabel = this.app.sections.find(s => s.id === sectionId)?.label || sectionId;
        const cardHtml = `
            <div class="editor-card animate__animated animate__fadeInUp" data-section="${sectionId}" contenteditable="false">
                <div class="badge bg-secondary mb-2">${sectionLabel}</div>
                <div class="card-actions">
                    <button onclick="wsApp.cardActionPushToFusion(this)" title="Push to Fusor 2C"><i class="bi bi-arrow-left-circle-fill text-primary"></i></button>
                    <button onclick="wsApp.cardActionSave(this)" title="Save Block"><i class="bi bi-floppy"></i></button>
                    <button onclick="wsApp.cardActionContext(this)" title="Quote to Chat"><i class="bi bi-chat-quote"></i></button>
                </div>
                <div class="card-content" contenteditable="true">${content}</div>
            </div><p><br></p>
        `;
        if (document.queryCommandSupported('insertHTML')) {
            document.execCommand('insertHTML', false, cardHtml);
        } else {
            this.app.editorCanvas.innerHTML += cardHtml;
        }
        this.updateWordCount();
    }

    formatText(command, val = null, target = 'editor') {
        const canvas = target === 'fusion' ? this.app.fusionCanvas : this.app.editorCanvas;
        if (!canvas) return;

        const selection = window.getSelection();
        let hasRange = selection && selection.rangeCount > 0;
        let inTargetCanvas = false;
        if (hasRange) {
            const range = selection.getRangeAt(0);
            inTargetCanvas = canvas.contains(range.commonAncestorContainer);
        }

        canvas.focus();
        if (!inTargetCanvas) {
            const range = document.createRange();
            range.selectNodeContents(canvas);
            range.collapse(false);
            selection.removeAllRanges();
            selection.addRange(range);
        }

        let cmd = command;
        let cmdVal = val;
        if (command === 'h1') {
            cmd = 'formatBlock';
            cmdVal = '<h1>';
        } else if (command === 'h2') {
            cmd = 'formatBlock';
            cmdVal = '<h2>';
        } else if (command === 'list') {
            cmd = 'insertUnorderedList';
        }

        try {
            document.execCommand(cmd, false, cmdVal);
        } catch (err) {
            console.warn('[ManuscriptWS] formatText failed:', err);
            if (this.app.saveStatus) {
                this.app.saveStatus.innerText = `Formatting failed: ${command}`;
            }
        }

        if (target === 'editor') {
            this.updateWordCount();
        }
    }

    saveAllBlocks() {
        const cards = this.app.editorCanvas.querySelectorAll('.editor-card');
        if(cards.length === 0) return;
        
        cards.forEach(card => {
            const btn = card.querySelector('button[title="Save Block"]');
            if (btn) this.cardActionSave(btn, true); 
        });
        if (!this.app.pendingOpenBlockModal) {
            alert(`已觸發該段落的存檔！(版號自動 +1.0)`);
        }
    }

    cardActionSave(btn, forceAutoIncrement = false) {
        const card = btn.closest('.editor-card');
        // [v0.6 修正] 改為 innerHTML，確保包含 <img> 標籤的內容能完整存檔
        const content = card.querySelector('.card-content').innerHTML;
        const section = card.getAttribute('data-section') || 'general';
        const icon = btn.querySelector('i');
        const originalClass = icon.className;
        icon.className = 'spinner-border spinner-border-sm text-primary';
        
        let currentSVer = this.app.sectionVersion ? this.app.sectionVersion.value.trim() : '0.0';
        if (forceAutoIncrement || !this.app.lastSavedSVer[section] || this.app.lastSavedSVer[section] === currentSVer) {
            let parts = currentSVer.split('.');
            let main = parseInt(parts[0]) || 0;
            let sub = parts.length > 1 ? parseInt(parts[1]) || 0 : 0;
            currentSVer = `${main + 1}.${sub}`;
            if(this.app.sectionVersion) this.app.sectionVersion.value = currentSVer;
        }
        this.app.lastSavedSVer[section] = currentSVer;

        const title = this.app.paperTitleInput ? this.app.paperTitleInput.value.trim() : "Untitled_Paper";

        this.app.socket.emit('cmd_save_block', {
            pid: this.app.pid,
            title: title, 
            section: section,
            content: content,
            s_ver: currentSVer
        });

        if(this.app.saveStatus) this.app.saveStatus.innerText = `Sending block to database: [${section}] @ v${currentSVer}`;

        setTimeout(() => {
            icon.className = 'bi bi-check-lg text-success';
            setTimeout(() => icon.className = originalClass, 1500);
        }, 800);
    }

    cardActionPushToFusion(btn) {
        const card = btn.closest('.editor-card');
        // [v0.6 修正] 改為 innerHTML，確保推入 2C 時圖片不會消失
        const content = card.querySelector('.card-content').innerHTML;
        const section = card.getAttribute('data-section') || 'general';
        const sectionLabel = this.app.sections.find(s => s.id === section)?.label || section;
        
        if(this.app.fusionCanvas) {
            // [v0.5 修正] 當推入實質內容時，自動打掃掉預設的佔位文字，保持畫面乾淨
            if (this.app.fusionCanvas.innerHTML.includes('來自 2B 的段落將會依序插入於此處')) {
                this.app.fusionCanvas.innerHTML = this.app.fusionCanvas.innerHTML.replace(/<p[^>]*>來自 2B 的段落將會依序插入於此處\.\.\.<\/p>/g, '');
                this.app.fusionCanvas.innerHTML = this.app.fusionCanvas.innerHTML.replace('來自 2B 的段落將會依序插入於此處...', '');
            }

            const fusionHtml = `
                <div class="fusion-block mb-4 border-start border-4 border-success ps-3 animate__animated animate__fadeInLeft">
                    <h5 class="text-success fw-bold"><i class="bi bi-check2-circle me-1"></i>${sectionLabel}</h5>
                    <div class="fusion-body">${content.replace(/\n/g, '<br>')}</div>
                </div>
            `;
            this.app.fusionCanvas.innerHTML += fusionHtml;
        }

        const icon = btn.querySelector('i');
        icon.classList.replace('bi-arrow-left-circle-fill', 'bi-check-circle-fill');
        icon.classList.replace('text-primary', 'text-success');
        setTimeout(() => {
            icon.classList.replace('bi-check-circle-fill', 'bi-arrow-left-circle-fill');
            icon.classList.replace('text-success', 'text-primary');
        }, 1200);
    }

    cardActionContext(btn) {
        const card = btn.closest('.editor-card');
        const content = card.querySelector('.card-content').innerText;
        const quote = `> Citated content: "${content.substring(0, 150)}..."\n\n`;
        this.app.chatInput.value = quote + this.app.chatInput.value;
        this.app.chatInput.focus();
        
        const icon = btn.querySelector('i');
        const originalClass = icon.className;
        icon.className = 'bi bi-arrow-right text-primary';
        setTimeout(() => icon.className = originalClass, 1000);
    }

    // =========================================================================
    // 畫布內容與字數統計
    // =========================================================================
    loadMultiSectionContent() {
        if (this.app.editorCanvas.innerText.trim().length < 10) {
            const activeId = Array.from(this.app.selectedSections)[0] || 'abstract';
            const label = this.app.sections.find(s => s.id === activeId)?.label || activeId;
            this.app.editorCanvas.innerHTML = `
                <div class="section-block mb-4" data-section="${activeId}">
                    <h2 class="text-primary border-bottom pb-2 h4">${label}</h2>
                    <div class="section-content"><p class="text-muted fst-italic">Awaiting content draft for ${label} section...</p></div>
                </div>`;
        }
        this.updateWordCount();
    }

    updateWordCount() {
        const text = this.app.editorCanvas.innerText || "";
        const count = text.trim().length === 0 ? 0 : text.trim().split(/\s+/).length;
        if(this.app.wordCountDisplay) this.app.wordCountDisplay.innerText = `Current Draft Words: ${count}`;
    }

    switchChatSection(sectionId) {
        if(!sectionId) return;
        this.app.chatContainer.innerHTML = ''; 
        this.addSystemMessage(`Synchronizing context for section: [${sectionId}]...`);
        this.app.socket.emit('cmd_load_chat', { pid: this.app.pid, section: sectionId });
    }

    // =========================================================================
    // Socket 與 通訊事件處理
    // =========================================================================
    setupSocketEvents() {
        this.app.socket.on('connect', () => {
            console.log("[Socket] Connection established successfully.");
            this.addSystemMessage("Drafter Server Connection: Active.");
        });

        this.app.socket.on('disconnect', () => {
            this.addSystemMessage("Drafter Server disconnected. Reconnecting...");
        });

        this.app.socket.on('sys_msg', (data) => this.addSystemMessage(data.msg));
        this.app.socket.on('ai_response', (data) => this.handleAIResponse(data));
        this.app.socket.on('job_queued', (data) => {
            this.activeJobId = data.job_id;
            this.updateTypingIndicator(`Job queued (${data.job_id}). Waiting worker...`);
            this._armTypingTimeout();
        });
        this.app.socket.on('job_progress', (data) => {
            if (this.activeJobId && data.job_id !== this.activeJobId) return;
            const msg = data.message || data.stage || 'processing';
            const pct = typeof data.progress === 'number' ? ` ${data.progress}%` : '';
            this.updateTypingIndicator(`AI ${msg}${pct}`);
        });
        this.app.socket.on('job_done', (data) => {
            if (this.activeJobId && data.job_id !== this.activeJobId) return;
            this._clearTypingTimeout();
            this.activeJobId = null;
            this.removeTypingIndicator();
            if (data && typeof data.latency_ms === 'number') {
                this.addSystemMessage(`Job completed in ${(data.latency_ms / 1000).toFixed(1)}s`);
            }
        });
        this.app.socket.on('job_error', (data) => {
            if (this.activeJobId && data.job_id !== this.activeJobId) return;
            this._clearTypingTimeout();
            this.activeJobId = null;
            this.removeTypingIndicator();
            this.addSystemMessage(`Job error: ${data.message || 'unknown error'}`);
        });
        this.app.socket.on('job_cancelled', (data) => {
            if (this.activeJobId && data.job_id !== this.activeJobId) return;
            this._clearTypingTimeout();
            this.activeJobId = null;
            this.removeTypingIndicator();
            this.addSystemMessage(`Job cancelled: ${data.job_id}`);
        });
        
        this.app.socket.on('save_ack', (data) => {
            if (data.target === 'paper') {
                this.app.ui.flashButtonSuccess(this.app.btnSaveGlobal);
                if (this.app.pendingOpenPaperModal) {
                    this.app.pendingOpenPaperModal = false;
                    const title = this.app.paperTitleInput.value.trim() || "Untitled_Paper";
                    this.app.socket.emit('cmd_list_papers', { pid: this.app.pid, title: title });
                }
            } else if (data.target === 'block') {
                if(this.app.saveStatus) {
                    this.app.saveStatus.innerText = data.msg + " @ " + new Date().toLocaleTimeString();
                }
                if (this.app.pendingOpenBlockModal) {
                    this.app.pendingOpenBlockModal = false;
                    const sec = Array.from(this.app.selectedSections)[0] || 'abstract';
                    this.fetchOldBlocks(sec);
                    bootstrap.Modal.getOrCreateInstance(document.getElementById('oldBlockModal')).show();
                }
            }
        });

        this.app.socket.on('chat_history', (data) => {
            this.app.chatContainer.innerHTML = '';
            this.addSystemMessage(`Syncing history for section: ${data.section}`);
            if(data.history && data.history.length > 0) {
                data.history.forEach(msg => {
                    if(msg.type === 'draft') {
                        if(msg.chat_msg) this.addBubble('ai', msg.chat_msg);
                        this.addDraftCard(msg.content);
                    }
                    else this.addBubble(msg.role, msg.content);
                });
            } else {
                this.addSystemMessage("No previous chat threads found here.");
            }
            this.scrollToBottom();
        });

        this.app.socket.on('paper_list', (data) => {
            const container = document.getElementById('oldPaperListContainer');
            container.innerHTML = '';
            if (data.files && data.files.length > 0) {
                data.files.forEach(file => {
                    let vTag = file.split('_V')[1]?.split('.json')[0] || file.split('_v')[1]?.split('.json')[0] || 'Unknown';
                    const a = document.createElement('a');
                    a.className = "list-group-item list-group-item-action d-flex justify-content-between align-items-center";
                    a.href = "#";
                    a.innerHTML = `<span class="fw-bold">${file}</span> <span class="badge bg-primary rounded-pill">v${vTag}</span>`;
                    a.onclick = (e) => {
                        e.preventDefault();
                        this.app.socket.emit('cmd_load_paper', { pid: this.app.pid, filename: file });
                        bootstrap.Modal.getOrCreateInstance(document.getElementById('oldPaperModal')).hide();
                    };
                    container.appendChild(a);
                });
            } else {
                container.innerHTML = '<div class="text-center text-muted p-3">查無舊檔案。</div>';
            }
            bootstrap.Modal.getOrCreateInstance(document.getElementById('oldPaperModal')).show();
        });

        this.app.socket.on('paper_loaded', (data) => {
            if(data.ok && this.app.fusionCanvas) {
                this.app.fusionCanvas.innerHTML = data.content.content || '';
                this.app.globalVersion.value = data.content.g_ver || '1.0';
                this.app.lastSavedGVer = this.app.globalVersion.value; 
                this.addSystemMessage(`已成功還原 2C 全文版本: ${data.filename}`);
            }
        });

        this.app.socket.on('block_list', (data) => {
            const container = document.getElementById('oldBlockListContainer');
            container.innerHTML = '';
            if (data.files && data.files.length > 0) {
                data.files.forEach(file => {
                    let vTag = file.split('_V')[1]?.split('.json')[0] || file.split('_v')[1]?.split('.json')[0] || 'Unknown';
                    const a = document.createElement('a');
                    a.className = "list-group-item list-group-item-action d-flex justify-content-between align-items-center";
                    a.href = "#";
                    a.innerHTML = `<span class="fw-bold">${file}</span> <span class="badge bg-info rounded-pill">v${vTag}</span>`;
                    a.onclick = (e) => {
                        e.preventDefault();
                        this.app.socket.emit('cmd_load_block', { pid: this.app.pid, section: data.section, filename: file });
                        bootstrap.Modal.getOrCreateInstance(document.getElementById('oldBlockModal')).hide();
                    };
                    container.appendChild(a);
                });
            } else {
                container.innerHTML = `
                    <div class="text-center text-muted p-4">
                        <i class="bi bi-folder-x fs-1 text-secondary mb-2"></i><br>
                        查無舊檔案。<br>
                        <button class="btn btn-sm btn-outline-primary mt-3" onclick="wsApp.createNewSection('${data.section}')">
                            <i class="bi bi-file-earmark-plus me-1"></i>建立空白新草稿
                        </button>
                    </div>`;
            }
        });

        this.app.socket.on('block_loaded', (data) => {
            if(data.ok && this.app.editorCanvas) {
                this.app.selectedSections = new Set([data.section]);
                this.app.ui.updateDropdownLabel();
                this.app.editorCanvas.innerHTML = ''; 
                
                this.insertEditorCard(data.content.content, data.section);
                let vStr = data.content.version.replace('V', '').replace('v', '');
                this.app.sectionVersion.value = vStr;
                this.app.lastSavedSVer[data.section] = vStr; 
                this.addSystemMessage(`已成功插入 2B 段落版本: ${data.filename}`);
            }
        });
    }

    createNewSection(secId) {
        this.app.selectedSections = new Set([secId]);
        this.app.ui.updateDropdownLabel();
        this.app.editorCanvas.innerHTML = '';
        this.app.sectionVersion.value = '0.0';
        this.app.lastSavedSVer[secId] = '0.0';
        this.loadMultiSectionContent();
        
        const modalEl = document.getElementById('oldBlockModal');
        if (modalEl) {
            const instance = bootstrap.Modal.getOrCreateInstance(modalEl);
            if(instance) instance.hide();
        }
    }

    // [v0.5 修復] 開啟舊檔流程 (2C) - 升級判斷邏輯
    openOldPaperFlow() {
        const rawText = this.app.fusionCanvas.innerText.trim();
        // 剔除佔位文字後計算真實長度
        const cleanText = rawText.replace(/來自 2B 的段落將會依序插入於此處\.\.\./g, '').trim();
        
        if (cleanText.length < 10) {
            const title = this.app.paperTitleInput.value.trim() || "Untitled_Paper";
            this.app.socket.emit('cmd_list_papers', { pid: this.app.pid, title: title });
            return;
        }

        if(confirm("即將開啟舊版檔案！系統會先自動將目前 2C 的內容強制備份 (G.Ver + 1.0)。確定繼續嗎？")) {
            this.app.pendingOpenPaperModal = true;
            this.app.lastSavedGVer = '0.0'; 
            this.app.btnSaveGlobal.click();
        }
    }

    openOldBlockFlow() {
        const cards = this.app.editorCanvas.querySelectorAll('.editor-card');
        const rawText = this.app.editorCanvas.innerText.trim();
        
        if (cards.length === 0 || rawText.includes("Awaiting content draft for") || rawText.includes("Select a section above")) {
            this.renderOldBlockSelect();
            this.fetchOldBlocks();
            bootstrap.Modal.getOrCreateInstance(document.getElementById('oldBlockModal')).show();
            return;
        }

        if(confirm("即將開啟舊版檔案(或切換章節)！系統會先自動將目前 2B 畫布上的內容強制備份 (S.Ver + 1.0)。確定繼續嗎？")) {
            this.app.pendingOpenBlockModal = true;
            this.saveAllBlocks();
            this.renderOldBlockSelect();
        }
    }

    renderOldBlockSelect() {
        const sel = document.getElementById('oldBlockSectionSelect');
        sel.innerHTML = '';
        this.app.sections.forEach(sec => {
            const opt = document.createElement('option');
            opt.value = sec.id;
            opt.innerText = sec.label;
            sel.appendChild(opt);
        });
        const currentActive = Array.from(this.app.selectedSections)[0] || 'abstract';
        sel.value = currentActive;
    }

    fetchOldBlocks(targetSec = null) {
        const sec = targetSec || document.getElementById('oldBlockSectionSelect').value;
        this.app.socket.emit('cmd_list_blocks', { pid: this.app.pid, section: sec });
    }

    // =========================================================================
    // UI 事件綁定 - 【v0.5 核心修復：防堵空畫布強制存檔跳號】
    // =========================================================================
    setupUIEvents() {
        this.app.btnSend.onclick = () => this.sendUserMessage();
        this.app.chatInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') this.sendUserMessage();
        });
        this.app.fileInput.addEventListener('change', (e) => this.app.ui.handleFileSelect(e));
        if (this.app.wordImportInput) {
            this.app.wordImportInput.addEventListener('change', (e) => this.app.ui.handleWordImportFileSelect(e));
        }
        if (this.app.drafterTargetSection) {
            this.app.drafterTargetSection.addEventListener('change', (e) => {
                this.switchChatSection(e.target.value);
            });
        }
        
        if(this.app.btnSaveGlobal) {
            this.app.btnSaveGlobal.onclick = () => {
                const rawText = this.app.fusionCanvas ? this.app.fusionCanvas.innerText.trim() : "";
                
                // [v0.5 嚴格防呆] 剔除系統預設字串，確保只計算真正的文章內容
                const cleanText = rawText.replace(/來自 2B 的段落將會依序插入於此處\.\.\./g, '').trim();
                
                if (cleanText.length < 10) {
                    alert("畫布中沒有實質內容，無法執行存檔！");
                    return; // 強制中斷，不允許跳號存檔
                }

                const title = this.app.paperTitleInput.value.trim() || "Untitled_Paper";
                let currentVer = this.app.globalVersion.value.trim() || '0.0';
                if (this.app.lastSavedGVer === currentVer) {
                    let parts = currentVer.split('.');
                    let x = parseInt(parts[0]) || 0;
                    let y = parts.length > 1 ? parseInt(parts[1]) || 0 : 0;
                    x += 1; 
                    currentVer = `${x}.${y}`;
                    this.app.globalVersion.value = currentVer;
                }
                this.app.lastSavedGVer = currentVer;
                const fusionContent = this.app.fusionCanvas ? this.app.fusionCanvas.innerHTML : '';
                this.app.socket.emit('cmd_save_paper', { pid: this.app.pid, title: title, ver: currentVer, content: fusionContent });
                this.app.btnSaveGlobal.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
            };
        }
    }
    
    // =========================================================================
    // AI 聊天與意圖處理 
    // =========================================================================
    sendUserMessage() {
        const txt = this.app.chatInput.value.trim();
        if(!txt && !this.app.currentAttachment) return;
        if (this.activeJobId) {
            this.addSystemMessage('A job is still running. Please wait or cancel it first.');
            return;
        }
        let targetLang = this.app.targetLangSelect ? this.app.targetLangSelect.value : "English";
        const targetSection = this.app.drafterTargetSection.value; 
        let displayPrompt = txt;
        if (this.app.currentAttachment) { displayPrompt += `<br><small class="text-warning"><i class="bi bi-paperclip"></i> 附加檔案: ${this.app.currentAttachment.name}</small>`; }
        this.addBubble('user', displayPrompt);
        const payload = { msg: txt, context: this.app.editorCanvas.innerText.substring(0,3000), target_lang: targetLang, attachment: this.app.currentAttachment, import_type: this.app.currentImportType, pid: this.app.pid, title: this.app.paperTitleInput.value.trim(), section: targetSection, s_ver: '0.1' };
        this.app.socket.emit('chat_message', payload);
        this.app.chatInput.value = ''; 
        this.app.ui.clearFile(); 
        this.showTypingIndicator();
        this._armTypingTimeout();
    }

    triggerAutoDraft() {
        if (this.activeJobId) {
            this.addSystemMessage('A job is still running. Please wait or cancel it first.');
            return;
        }
        const targetSection = this.app.drafterTargetSection.value;
        const targetLabel = this.app.sections.find(s => s.id === targetSection)?.label || targetSection;
        let targetLang = this.app.targetLangSelect ? this.app.targetLangSelect.value : "English";
        const currentTitle = this.app.paperTitleInput.value.trim() || 'Untitled';
        const refineMsg = `請撰寫草稿：一篇論文 ${targetLabel}，title是『${currentTitle}』，${targetLabel}字數300字`;
        this.addBubble('user', `[Auto Draft Command] <br><span class="text-info">${refineMsg}</span>`);
        this.app.socket.emit('chat_message', { msg: refineMsg, context: this.app.editorCanvas.innerText.substring(0,3000), target_lang: targetLang, pid: this.app.pid, title: currentTitle, section: targetSection, s_ver: '0.1' });
        this.showTypingIndicator();
        this._armTypingTimeout();
    }
    
    handleAIResponse(d) {
        try {
            this.removeTypingIndicator();
            if(d.type === 'draft') {
                if(d.chat_msg) this.addBubble('ai', d.chat_msg);
                this.addDraftCard(d.content);
            } else if(d.type==='text') {
                this.addBubble('ai', d.content);
            } else if(d.type === 'image_mermaid') {
                // [v0.6 核心修復] 攔截 mermaid 語法，移交給獨立的 ManuImage 引擎處理前端渲染
                if (this.app.imgEngine) {
                    this.app.imgEngine.renderMermaidToChat(d.content);
                } else {
                    this.addCard(d.type, d.content, `Generated Graphic (Engine Missing)`);
                }
            } else if(d.type === 'sheet') {
                this.addCard(d.type, d.content, `Generated ${d.type}`);
            } else if(d.type==='error') {
                this.addSystemMessage(`Process Aborted: ${d.content}`);
            } else {
                this.addBubble('ai', typeof d.content === 'object' ? JSON.stringify(d.content) : d.content);
            }
        } catch (err) {
            console.error("處理 AI 回應時發生錯誤:", err);
            this.addSystemMessage("系統處理回應時發生異常錯誤。");
        } finally {
            this._clearTypingTimeout();
            this.activeJobId = null;
            this.removeTypingIndicator();
        }
    }

    cancelActiveJob() {
        if (!this.activeJobId) {
            this.addSystemMessage('No active job to cancel.');
            return;
        }
        this.app.socket.emit('cmd_cancel_job', { job_id: this.activeJobId });
    }
    
    addBubble(role, text) {
        const div = document.createElement('div'); 
        div.className = `message-bubble ${role}`;
        div.innerHTML = `<div class="bubble-content shadow-sm"><div class="chat-conversational-body">${text.replace(/\n/g, '<br>')}</div></div>`;
        this.app.chatContainer.appendChild(div);
        this.scrollToBottom();
    }

    addDraftCard(content) {
        const div = document.createElement('div'); 
        div.className = `message-bubble ai animate__animated animate__fadeIn`;
        const targetBodyId = 'academic_body_' + Math.random().toString(36).substr(2, 6);
        div.innerHTML = `<div class="bubble-content draft-card-wrapper shadow"><button class="btn btn-primary draft-card-btn" title="將此段落推入 2B 編輯區" onclick="wsApp.importToEditor(document.getElementById('${targetBodyId}').innerHTML)"><i class="bi bi-arrow-left-circle-fill fs-5"></i></button><div class="draft-card-text academic-content" id="${targetBodyId}">${content.replace(/\n/g, '<br>')}</div></div>`;
        this.app.chatContainer.appendChild(div);
        this.scrollToBottom();
    }
    
    addCard(type, data, label) { 
        // [保留行數防呆] 就算 image_mermaid 已經被上面的 imgEngine 接管，此處仍保留原結構確保程式碼行數不減
        let displayHtml = `<strong class="text-primary">${label}</strong><br>`;
        if (type === 'image_mermaid') { displayHtml += `<pre class="bg-dark text-light p-2 rounded mt-2" style="font-size: 0.8em;">${data}</pre>`; } 
        else if (type === 'sheet') { displayHtml += `<pre class="bg-light border p-2 rounded mt-2" style="font-size: 0.8em; overflow-x:auto;">${JSON.stringify(data, null, 2)}</pre>`; } 
        else { displayHtml += data; }
        this.addBubble('ai', displayHtml); 
    }

    scrollToBottom() { this.app.chatContainer.scrollTop = this.app.chatContainer.scrollHeight; }

    addSystemMessage(t) { 
        const d = document.createElement('div');
        d.className = "text-center text-muted small my-3 font-monospace animate__animated animate__fadeIn";
        d.innerHTML = `<i class="bi bi-cpu me-1"></i> ${t}`; 
        this.app.chatContainer.appendChild(d); 
        this.scrollToBottom(); 
    }

    showTypingIndicator() { 
        if(document.getElementById('typingIndicator')) return;
        const d = document.createElement('div');
        d.id = 'typingIndicator';
        d.className = 'message-bubble ai';
        d.innerHTML = `<div class="bubble-content text-muted"><span class="spinner-grow spinner-grow-sm me-1"></span> AI is analyzing and generating...</div>`;
        this.app.chatContainer.appendChild(d); 
        this.scrollToBottom(); 
    }

    updateTypingIndicator(msg) {
        const el = document.getElementById('typingIndicator');
        if (!el) {
            this.showTypingIndicator();
            return;
        }
        el.innerHTML = `<div class="bubble-content text-muted"><span class="spinner-grow spinner-grow-sm me-1"></span> ${msg}</div>`;
    }

    removeTypingIndicator() { 
        const el = document.getElementById('typingIndicator');
        if(el) el.remove(); 
    }

    _armTypingTimeout() {
        this._clearTypingTimeout();
        this.typingTimeoutHandle = setTimeout(() => {
            if (!this.activeJobId) return;
            this.addSystemMessage('Request is taking too long. You can wait or cancel and retry.');
        }, this.typingWarnMs);
    }

    _clearTypingTimeout() {
        if (this.typingTimeoutHandle) {
            clearTimeout(this.typingTimeoutHandle);
            this.typingTimeoutHandle = null;
        }
    }
}
