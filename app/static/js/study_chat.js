// Roothinks source maintenance contract
// 檔案路徑: app/static/js/study_chat.js
// 模組定位: 瀏覽器互動層；把頁面元件、目前 PID 與後端 API/Socket 狀態同步。
// 主要責任: 管理 Study tutor 對話、輸入送出、歷史渲染與目前 paper scope。
// 上下游: Jinja template 建立 DOM 與初始 PID；本檔呼叫 /api、Socket.IO 或鄰接前端 controller 後更新可見狀態。
// 維護邊界: 前端狀態不是授權來源；外部文字必須 escape/sanitize，非同步回應須核對目前 PID/使用者後才能套用。
// 驗證: node --check app/static/js/study_chat.js
// #路徑(./app/static/js/study_chat.js) #版本 v2.1-AutoCreateOnFirstSend #更版時間 20260420
/* [MVP+Prototype Handoff Header]
 * 本檔案目前定位為 MVP/Prototype 實作；非最終產品級設計。
 * 對應規劃檔：CHANGE_PLAN_STUDY_FLOWB_2026-04-20.md
 * Study Chat context/focus_pids 為迭代中策略，後續請由人類團隊接手做產品級行為校驗。
 */

/**
 * Study Chat Controller v2.0 (Persistent Conversations)
 * 職責：
 * 1. 管理對話歷史 (CRUD)
 * 2. 處理訊息發送與接收 (Task 7 API)
 * 3. 渲染聊天泡泡與對話列表
 */
class StudyChat {
    constructor() {
        // 同步 Core 的 PID 邏輯
        this.pid = '';
        const urlParams = new URLSearchParams(window.location.search);
        const urlPid = urlParams.get('pid');
        const pathParts = window.location.pathname.split('/').filter(Boolean);
        const pathPid = (pathParts.length >= 3 && pathParts[0] === 'study' && pathParts[1] === 'project')
            ? decodeURIComponent(pathParts[2])
            : null;
        if (urlPid) this.pid = urlPid;
        else if (pathPid) this.pid = pathPid;
        else this.pid = '';

        if (this.pid) {
            try { localStorage.setItem('activePid', this.pid); } catch (e) {}
        }

        this.currentContext = null; // { pid, dimension, content }
        this.currentConversation = null; // { conv_id, title, messages: [] }
        this.isSending = false;
        this._creatingConversationPromise = null;
        
        this.historyContainer = document.getElementById('chat-history');
        this.inputEl = document.getElementById('chat-input');
        
        this.init();
    }

    init() {
        console.log('[StudyChat.init] 開始初始化...');
        console.log('[StudyChat.init] this.inputEl:', this.inputEl);
        
        if (this.inputEl) {
            console.log('[StudyChat.init] ✓ 找到 #chat-input 元素');
            
            try {
                this.inputEl.addEventListener('keypress', (e) => {
                    console.log('[StudyChat] Keypress 事件觸發，按鍵:', e.key);
                    if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault(); // 防止預設行為（換行）
                        console.log('[StudyChat] Enter 鍵按下，呼叫 sendMessage()');
                        this.sendMessage();
                    }
                });
                console.log('[StudyChat.init] ✓ keypress 事件監聽器已附加');
            } catch (e) {
                console.error('[StudyChat.init] ❌ addEventListener 失敗:', e);
            }
        } else {
            console.error('[StudyChat.init] ❌ 無法找到 #chat-input 元素！');
        }

        // 載入對話列表
        this.loadConversations();
    }

    /**
     * 載入對話列表
     */
    async loadConversations() {
        console.log('[StudyChat] Loading conversations for', this.pid);
        const container = document.getElementById('conversationListContainer');
        if (!this.pid) {
            if (container) {
                container.innerHTML = `<div class="text-warning small p-2"><i class="bi bi-exclamation-triangle"></i> 無有效 PID，請從 Dashboard 重新進入 Study。</div>`;
            }
            return;
        }
        
        try {
            const response = await fetch(`/api/study/${this.pid}/conversations`);
            const data = await response.json();
            
            if (!data.ok) {
                container.innerHTML = `<div class="text-danger small p-2"><i class="bi bi-exclamation-triangle"></i> ${this.escapeHtml(data.msg || '載入失敗')}</div>`;
                return;
            }
            
            const conversations = data.conversations || [];
            
            if (conversations.length === 0) {
                container.innerHTML = `
                    <div class="text-center text-secondary py-4">
                        <i class="bi bi-chat-dots display-4 opacity-25"></i>
                        <p class="small mt-2">尚無對話記錄</p>
                        <p class="small">點擊上方「新的對話」開始</p>
                    </div>`;
                return;
            }
            
            // 渲染對話列表 (類似 Gemini 風格)
            let html = '<div class="list-group list-group-flush">';
            
            conversations.forEach((conv, index) => {
                const isActive = this.currentConversation && this.currentConversation.conv_id === conv.conv_id;
                const activeClass = isActive ? 'active bg-primary text-white' : 'bg-dark text-light border-secondary';
                const hoverClass = isActive ? '' : 'list-group-item-action';
                const safeConvId = this.escapeJs(conv.conv_id);
                const safeCount = this.escapeHtml(conv.message_count);
                const safeUpdatedAt = this.escapeHtml(this.formatDate(conv.updated_at));
                // 相對時間（「4 分鐘前」）在回頭找某一次討論時沒有用 ——
                // 補上對話「開始」的絕對時間。API 本來就回 created_at，只是沒人用。
                const safeStartedAt = this.escapeHtml(this.formatStartStamp(conv.created_at));
                const safeStartedFull = this.escapeHtml(conv.created_at || '');
                
                html += `
                    <div class="list-group-item ${activeClass} ${hoverClass} border-0 px-2 py-2 mb-1 rounded cursor-pointer position-relative" 
                         onclick="window.studyChat.loadConversation('${safeConvId}')"
                         style="cursor: pointer;">
                        <div class="d-flex justify-content-between align-items-start">
                            <div class="flex-grow-1" style="min-width: 0;">
                                <h6 class="mb-1 small fw-bold text-truncate">${this.escapeHtml(conv.title)}</h6>
                                <p class="mb-0 text-truncate" style="font-size: 0.75rem; opacity: 0.7;"
                                   title="開始於 ${safeStartedFull}">
                                    ${safeCount} 則訊息 • ${safeUpdatedAt}
                                </p>
                                <p class="mb-0 text-truncate" style="font-size: 0.7rem; opacity: 0.55;"
                                   title="開始於 ${safeStartedFull}">
                                    起 ${safeStartedAt}
                                </p>
                            </div>
                            <div class="dropdown" onclick="event.stopPropagation();">
                                <button class="btn btn-sm btn-link ${isActive ? 'text-white' : 'text-secondary'} p-0" 
                                        data-bs-toggle="dropdown" aria-expanded="false">
                                    <i class="bi bi-three-dots-vertical"></i>
                                </button>
                                <ul class="dropdown-menu dropdown-menu-end shadow">
                                    <li><a class="dropdown-item" href="#" onclick="window.studyChat.renameConversation('${safeConvId}'); return false;">
                                        <i class="bi bi-pencil me-2"></i>重新命名
                                    </a></li>
                                    <li><hr class="dropdown-divider"></li>
                                    <li><a class="dropdown-item text-danger" href="#" onclick="window.studyChat.deleteConversation('${safeConvId}'); return false;">
                                        <i class="bi bi-trash me-2"></i>刪除對話
                                    </a></li>
                                </ul>
                            </div>
                        </div>
                    </div>`;
            });
            
            html += '</div>';
            container.innerHTML = html;

            this._restoreLastConversation(conversations);

        } catch (error) {
            console.error('[StudyChat] Error loading conversations:', error);
            container.innerHTML = `<div class="text-danger small p-2">載入錯誤: ${this.escapeHtml(error.message)}</div>`;
        }
    }

    /**
     * 創建新對話
     */
    async createNewConversation(options = {}) {
        const opts = options || {};
        const title = String(opts.title || '新的對話');
        const silent = Boolean(opts.silent);
        const clearHistory = opts.clearHistory !== false;
        const focusInput = opts.focusInput !== false;
        console.log('[StudyChat] Creating new conversation...');

        if (!this.pid) {
            if (!silent) {
                alert('無有效 PID，無法建立對話');
            }
            return null;
        }

        // 避免重複建立：如果已有正在建立中的請求，直接等待同一個 Promise。
        if (this._creatingConversationPromise) {
            await this._creatingConversationPromise;
            return this.currentConversation;
        }
        
        try {
            this._creatingConversationPromise = fetch('/api/study/conversations/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pid: this.pid, title })
            });
            const response = await this._creatingConversationPromise;
            const data = await response.json();
            
            if (!data.ok) {
                if (!silent) {
                    alert('創建對話失敗: ' + (data.msg || '未知錯誤'));
                }
                return null;
            }
            
            // 清空聊天區域
            if (clearHistory && this.historyContainer) {
                this.historyContainer.innerHTML = '';
            }
            
            // 設置為當前對話
            this.currentConversation = {
                conv_id: data.conv_id,
                title: data.title,
                messages: []
            };
            
            // 重新載入列表
            await this.loadConversations();
            
            // 聚焦輸入框
            if (focusInput && this.inputEl) {
                this.inputEl.focus();
                this.inputEl.placeholder = '開始對話...';
            }
            return this.currentConversation;
            
        } catch (error) {
            console.error('[StudyChat] Error creating conversation:', error);
            if (!silent) {
                alert('創建對話失敗: ' + error.message);
            }
            return null;
        } finally {
            this._creatingConversationPromise = null;
        }
    }

    /**
     * 載入指定對話
     */
    /** localStorage 的鍵。每個專案各自記住自己最後開的那則對話。 */
    _lastConvKey() {
        return `studyLastConv:${this.pid || 'nopid'}`;
    }

    /**
     * 進頁時自動還原上一次看的對話。
     *
     * 原本進 Study 一律是空的（Context: None），要自己再點一次才看得到內容；
     * 而使用者幾乎總是想接續上一次。優先用 localStorage 記住的那則，
     * 它若已被刪除就退回最新的一則（清單是依 updated_at 由新到舊）。
     *
     * loadConversation() 結尾會再呼叫 loadConversations() 重畫清單，
     * 所以這裡必須有 _autoRestored 旗標擋住遞迴。
     */
    _restoreLastConversation(conversations) {
        if (this._autoRestored) return;
        if (this.currentConversation) return;
        if (!Array.isArray(conversations) || conversations.length === 0) return;
        this._autoRestored = true;

        let target = null;
        try {
            const remembered = localStorage.getItem(this._lastConvKey());
            if (remembered) {
                target = conversations.find((c) => c.conv_id === remembered) || null;
            }
        } catch (e) {
            // localStorage 在某些隱私模式下會丟例外；退回最新一則即可，不影響功能。
        }
        if (!target) target = conversations[0];
        if (target && target.conv_id) {
            this.loadConversation(target.conv_id);
        }
    }

    async loadConversation(convId) {
        console.log('[StudyChat] Loading conversation:', convId);

        try {
            const response = await fetch(`/api/study/conversation/${convId}?pid=${this.pid}`);
            const data = await response.json();
            
            if (!data.ok) {
                alert('載入對話失敗: ' + (data.msg || '未知錯誤'));
                return;
            }
            
            this.currentConversation = data.conversation;
            this._autoRestored = true;   // 使用者已經有選擇，不要再自動蓋掉
            try {
                localStorage.setItem(this._lastConvKey(), convId);
            } catch (e) {
                // 隱私模式寫不進去；只是下次不會自動還原，不算故障。
            }

            // 渲染歷史訊息
            if (this.historyContainer) {
                this.historyContainer.innerHTML = '';
                
                const messages = data.conversation.messages || [];
                messages.forEach(msg => {
                    this.appendMessage(msg.role, msg.content, false); // false = 不滾動
                });
                
                // 完成後滾動到底部
                this.historyContainer.scrollTop = this.historyContainer.scrollHeight;
            }
            
            // 更新列表顯示
            this.loadConversations();
            
        } catch (error) {
            console.error('[StudyChat] Error loading conversation:', error);
            alert('載入對話失敗: ' + error.message);
        }
    }

    /**
     * 刪除對話
     */
    async deleteConversation(convId) {
        if (!confirm('確定要刪除這個對話嗎？此操作無法撤銷。')) {
            return;
        }
        
        try {
            const response = await fetch(`/api/study/conversation/${convId}/delete`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pid: this.pid })
            });
            
            const data = await response.json();
            
            if (!data.ok) {
                alert('刪除失敗: ' + (data.msg || '未知錯誤'));
                return;
            }
            
            // 如果刪除的是當前對話，清空顯示
            if (this.currentConversation && this.currentConversation.conv_id === convId) {
                this.currentConversation = null;
                if (this.historyContainer) {
                    this.historyContainer.innerHTML = '';
                }
            }
            
            // 重新載入列表
            this.loadConversations();
            
        } catch (error) {
            console.error('[StudyChat] Error deleting conversation:', error);
            alert('刪除失敗: ' + error.message);
        }
    }

    /**
     * 重新命名對話
     */
    async renameConversation(convId) {
        const newTitle = prompt('請輸入新的對話標題:');
        if (!newTitle || newTitle.trim() === '') {
            return;
        }
        
        try {
            const response = await fetch(`/api/study/conversation/${convId}/rename`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pid: this.pid, title: newTitle.trim() })
            });
            
            const data = await response.json();
            
            if (!data.ok) {
                alert('重新命名失敗: ' + (data.msg || '未知錯誤'));
                return;
            }
            
            // 更新當前對話標題
            if (this.currentConversation && this.currentConversation.conv_id === convId) {
                this.currentConversation.title = newTitle.trim();
            }
            
            // 重新載入列表
            this.loadConversations();
            
        } catch (error) {
            console.error('[StudyChat] Error renaming conversation:', error);
            alert('重新命名失敗: ' + error.message);
        }
    }

    /**
     * 接收來自 View 層的 Context 設定
     */
    setContext(ctx) {
        const incoming = (ctx && typeof ctx === 'object') ? ctx : {};
        const focus = Array.isArray(incoming.focus_pids)
            ? Array.from(new Set(incoming.focus_pids.map(v => String(v || '').trim()).filter(Boolean)))
            : [];
        this.currentContext = {
            ...incoming,
            focus_pids: focus
        };
        console.log("[StudyChat] Context updated:", ctx);
        const shorten = (value, maxLen = 28) => {
            const text = String(value || '');
            return text.length > maxLen ? text.slice(0, maxLen) + '...' : text;
        };
        
        // 只更新提示，不自動 focus，避免造成版面跳動。
        if(this.inputEl) {
            this.inputEl.placeholder = `針對 ${shorten(ctx.pid, 26)} / ${shorten(ctx.dimension, 12)} 提問...`;
            this.inputEl.title = `${ctx.pid} / ${ctx.dimension}`;
        }
    }

    _buildContextPayload() {
        const base = (this.currentContext && typeof this.currentContext === 'object')
            ? { ...this.currentContext }
            : {};

        let focus = Array.isArray(base.focus_pids) ? base.focus_pids : [];
        focus = Array.from(new Set(focus.map(v => String(v || '').trim()).filter(Boolean)));
        if (!focus.length && base.pid && String(base.pid).trim()) {
            focus = [String(base.pid).trim()];
        }

        const noteEl = document.getElementById('study-notes');
        const noteText = noteEl ? String(noteEl.value || '').trim() : '';

        return {
            ...base,
            focus_pids: focus,
            user_notes: noteText ? noteText.slice(0, 4000) : ''
        };
    }

    async sendMessage() {
        console.log('[StudyChat.sendMessage] ✓ 方法已呼叫');
        
        const text = this.inputEl.value.trim();
        console.log('[StudyChat.sendMessage] 輸入文本:', text);
        
        if (!text) {
            console.log('[StudyChat.sendMessage] 輸入為空，返回');
            return;
        }

        if (this.isSending) {
            console.log('[StudyChat.sendMessage] 正在等待上一則回覆，略過重複發送');
            return;
        }

        // 首次發送時自動創建對話，避免要求使用者先手動新增。
        if (!this.currentConversation) {
            const conv = await this.createNewConversation({
                silent: true,
                clearHistory: false,
                focusInput: false,
                title: '新的對話'
            });
            if (!conv || !conv.conv_id) {
                this.appendMessage('system', '目前無法自動建立對話，請稍後再試。');
                return;
            }
        }

        // 1. Render User Message
        this.appendMessage('user', text);
        this.inputEl.value = '';
        console.log('[StudyChat.sendMessage] 用戶訊息已渲染');

        // 2. Show Typing Indicator
        const loadingId = this.appendTyping();
        console.log('[StudyChat.sendMessage] 輸入指示器已顯示');

        // 3. API Payload (包含對話 ID)
        const payload = {
            pid: this.pid,
            question: text,
            context: this._buildContextPayload(),
            conv_id: this.currentConversation.conv_id  // [New] 傳遞對話 ID
        };
        console.log('[StudyChat.sendMessage] 準備發送 API 請求，payload:', payload);

        this.isSending = true;
        try {
            // 4. Send Request
            const response = await fetch('/api/study/chat', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            console.log('[StudyChat] ✓ 收到 HTTP 響應，狀態碼:', response.status, response.statusText);
            if (!response.ok) {
                console.error('[StudyChat] ❌ HTTP Error:', response.status, response.statusText);
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }

            const text = await response.text(); // 先讀為文本
            console.log('[StudyChat] ✓ 收到響應文本:', text);
            try {
                const res = JSON.parse(text);
                console.log('[StudyChat] ✓ JSON 解析成功:', res);
                console.log('[StudyChat] 診斷 - res.ok 類型:', typeof res.ok, '值:', res.ok);
                console.log('[StudyChat] 診斷 - res.answer 類型:', typeof res.answer, '值:', res.answer);
                
                this.removeMessage(loadingId);
                
                if (res.ok === true) {
                    console.log('[StudyChat] ✓ 條件 res.ok === true 成立，現在附加 AI 回應');
                    if (res.answer) {
                        console.log('[StudyChat] ✓ API 返回成功，答案長度:', res.answer.length);
                        this.appendMessage('ai', res.answer);
                    } else {
                        console.warn('[StudyChat] ⚠ res.answer 為空');
                        this.appendMessage('system', "Error: No answer returned from AI");
                    }
                } else {
                    console.warn('[StudyChat] ⚠ API 返回失敗，res.ok 不為 true:', res.msg || res.error);
                    this.appendMessage('system', "Error: " + (res.msg || res.error || "Unknown error"));
                }
            } catch (e) {
                this.removeMessage(loadingId);
                console.error('[StudyChat] ❌ JSON Parse Error:', e.message);
                console.error('[StudyChat] 原始響應文本:', text);
                this.appendMessage('system', "Server Error: Invalid response (check console)");
            }
        } catch (err) {
            this.removeMessage(loadingId);
            console.error('[StudyChat] ❌ Fetch 完全失敗:', err.message);
            console.error('[StudyChat] 完整錯誤堆棧:', err);
            this.appendMessage('system', "Net Error: " + err.message);
        } finally {
            this.isSending = false;
        }
    }

    appendMessage(role, text, shouldScroll = true) {
        if (!this.historyContainer) return;

        const div = document.createElement('div');
        div.className = `d-flex mb-3 ${role === 'user' ? 'justify-content-end' : 'justify-content-start'}`;
        
        let contentClass = role === 'user' ? 'bg-primary text-white' : 'bg-light text-dark border';
        if (role === 'system') contentClass = 'bg-danger-subtle text-danger small';

        // Markdown-like simple format
        // 將換行符轉為 <br>，處理簡單列表
        let formattedText = text
            .replace(/</g, "&lt;").replace(/>/g, "&gt;") // XSS protection
            .replace(/\n/g, '<br>');
        
        // 簡單加粗處理 **text**
        formattedText = formattedText.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');

        div.innerHTML = `
            <div class="p-3 rounded-3 shadow-sm" style="max-width: 85%; ${contentClass}">
                ${role === 'ai' || role === 'assistant' ? '<i class="bi bi-robot me-2 text-primary"></i>' : ''}
                <span>${formattedText}</span>
            </div>
        `;
        
        this.historyContainer.appendChild(div);
        if (shouldScroll) {
            this.scrollToBottom();
        }
        return div.id;
    }

    appendTyping() {
        const id = 'typing-' + Date.now();
        const div = document.createElement('div');
        div.id = id;
        div.className = 'd-flex mb-3 justify-content-start';
        div.innerHTML = `
            <div class="p-3 rounded-3 bg-light border text-secondary" style="max-width: 80%;">
                <div class="d-flex align-items-center gap-2">
                    <span class="spinner-grow spinner-grow-sm" role="status" style="animation-duration: 0.8s;"></span>
                    <span class="spinner-grow spinner-grow-sm" role="status" style="animation-duration: 0.8s; animation-delay: 0.2s;"></span>
                    <span class="spinner-grow spinner-grow-sm" role="status" style="animation-duration: 0.8s; animation-delay: 0.4s;"></span>
                </div>
            </div>`;
        this.historyContainer.appendChild(div);
        this.scrollToBottom();
        return id;
    }

    removeMessage(id) {
        const el = document.getElementById(id);
        if (el) el.remove();
    }

    scrollToBottom() {
        this.historyContainer.scrollTop = this.historyContainer.scrollHeight;
    }

    /**
     * HTML 轉義 (防止 XSS)
     */
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    escapeJs(text) {
        return String(text || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    }

    /**
     * 格式化日期
     */
    formatDate(dateStr) {
        if (!dateStr) return '';
        
        const date = new Date(dateStr);
        const now = new Date();
        const diffMs = now - date;
        const diffMins = Math.floor(diffMs / 60000);
        const diffHours = Math.floor(diffMs / 3600000);
        const diffDays = Math.floor(diffMs / 86400000);
        
        if (diffMins < 1) return '剛才';
        if (diffMins < 60) return `${diffMins} 分鐘前`;
        if (diffHours < 24) return `${diffHours} 小時前`;
        if (diffDays < 7) return `${diffDays} 天前`;
        
        // 超過一週顯示完整日期
        return date.toLocaleDateString('zh-TW', {
            year: 'numeric',
            month: 'short',
            day: 'numeric'
        });
    }

    /**
     * 對話「開始」時間，格式 DD:HH:MM（日:時:分，本地時區）。
     *
     * 為什麼要絕對時間：清單原本只有「4 分鐘前 / 1 天前」，要回頭找「上週三下午
     * 討論資料集的那一次」時完全沒有用。完整時間戳放在 title 屬性給游標停留看。
     */
    formatStartStamp(dateStr) {
        if (!dateStr) return '—';
        const d = new Date(dateStr);
        if (isNaN(d.getTime())) return '—';
        const p = (n) => String(n).padStart(2, '0');
        return `${p(d.getDate())}:${p(d.getHours())}:${p(d.getMinutes())}`;
    }
}
