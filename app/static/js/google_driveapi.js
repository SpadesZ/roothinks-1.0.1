// Roothinks source maintenance contract
// 檔案路徑: app/static/js/google_driveapi.js
// 模組定位: 瀏覽器互動層；把頁面元件、目前 PID 與後端 API/Socket 狀態同步。
// 主要責任: 封裝 Google Drive picker/upload 前端流程，正規化檔名與回傳資源 identity。
// 上下游: Jinja template 建立 DOM 與初始 PID；本檔呼叫 /api、Socket.IO 或鄰接前端 controller 後更新可見狀態。
// 維護邊界: 前端狀態不是授權來源；外部文字必須 escape/sanitize，非同步回應須核對目前 PID/使用者後才能套用。
//路徑(./app/static/js/google_driveapi.js)
//版本 v0.2 (Google Drive Adapter + Word import mime)
//更版時間 20260421-1415
// CHANGE_PLAN_STUDY_FLOWB_2026-04-20: MVP prototype - enable Word (.docx/.doc) selection in Drive picker.

/**
 * Google Drive API Adapter
 * 負責處理 OAuth 驗證、呼叫 Picker UI 以及下載檔案內容。
 * * [Prerequisites]
 * 1. Go to Google Cloud Console (https://console.cloud.google.com/)
 * 2. Create Project & Enable APIs: "Google Drive API", "Google Picker API"
 * 3. Create Credentials: 
 * - API Key (Browser key)
 * - OAuth 2.0 Client ID (Web application, add Authorized Origins)
 */

class GoogleDriveAdapter {
    constructor() {
        // 由伺服器注入公開 browser 設定；OAuth token 只存本頁記憶體。
        let config = {};
        try { config = JSON.parse(document.getElementById('googleDriveConfig')?.textContent || '{}'); }
        catch (_) { /* 無效設定視同未配置，不能阻擋本機研究流程。 */ }
        this.API_KEY = String(config.apiKey || '');
        this.CLIENT_ID = String(config.clientId || '');
        this.APP_ID = String(config.appId || '');

        // Scopes: drive.file (只存取此 App 建立或使用者選取的檔案，較安全)
        this.SCOPES = 'https://www.googleapis.com/auth/drive.file';
        
        this.tokenClient = null;
        this.accessToken = null;
        this.tokenExpiresAt = 0;
        this.pickerInited = false;
        this.gisInited = false;
        
        // Callback function to return file data to main app
        this.onFileSelected = null; 
    }

    _isTextMime(mime) {
        const m = String(mime || '').toLowerCase().trim();
        if (!m) return false;
        if (m.startsWith('text/')) return true;
        if (m.includes('json') || m.includes('xml') || m.includes('csv') || m.includes('yaml')) return true;
        if (m.includes('javascript') || m.includes('markdown')) return true;
        return false;
    }

    _isTextExt(filename) {
        const name = String(filename || '').toLowerCase();
        return [
            '.txt', '.md', '.markdown', '.csv', '.json', '.ndjson', '.xml', '.yml', '.yaml',
            '.html', '.htm', '.log', '.py', '.js', '.ts', '.tsx', '.jsx', '.css', '.sql',
            '.sh', '.bat', '.ps1', '.ini', '.cfg', '.conf'
        ].some((ext) => name.endsWith(ext));
    }

    /**
     * 初始化 Google API (由 HTML onload 觸發或手動呼叫)
     */
    init() {
        this.loadGapi();
        this.loadGis();
    }

    loadGapi() {
        if(window.gapi) {
            gapi.load('client:picker', async () => {
                try {
                    await gapi.client.load('https://www.googleapis.com/discovery/v1/apis/drive/v3/rest');
                    this.pickerInited = true;
                } catch (_) { this.sdkUnavailable(); }
            });
        }
    }

    sdkUnavailable() {
        this.pickerInited = false;
        this.gisInited = false;
        this.accessToken = null;
        this.tokenExpiresAt = 0;
    }

    loadGis() {
        if(window.google?.accounts?.oauth2) {
            this.tokenClient = google.accounts.oauth2.initTokenClient({
                client_id: this.CLIENT_ID,
                scope: this.SCOPES,
                callback: () => {}, // defined at request time
                error_callback: () => { this.accessToken = null; this.tokenExpiresAt = 0; alert('Google 授權未完成；可稍後重試或使用本機匯入。'); },
            });
            this.gisInited = true;
            console.log("[DriveAdapter] GIS Loaded.");
        }
    }

    /**
     * 開啟 Google Drive 選擇器
     * @param {Function} callback - (fileObj) => void
     */
    openPicker(callback) {
        if (![this.API_KEY, this.CLIENT_ID, this.APP_ID].every(value => value && !value.startsWith('YOUR_'))) {
            alert('Google Drive 尚未由管理者配置；可使用本機匯入。');
            return false;
        }
        if (!this.pickerInited || !this.gisInited || !this.tokenClient) {
            alert('Google SDK 尚未就緒或無法連線；可稍後重試或使用本機匯入。');
            return false;
        }

        this.onFileSelected = callback;

        // Check Auth
        if (this.accessToken && Date.now() < this.tokenExpiresAt) {
            this.createPicker();
        } else {
            this.accessToken = null;
            // Request Auth
            this.tokenClient.callback = async (response) => {
                if (!response || response.error !== undefined || !response.access_token || !(Number(response.expires_in) > 0)) {
                    this.accessToken = null;
                    this.tokenExpiresAt = 0;
                    alert('Google 授權未完成；未匯入任何內容。');
                    return;
                }
                this.accessToken = response.access_token;
                this.tokenExpiresAt = Date.now() + Number(response.expires_in) * 1000 - 5000;
                this.createPicker();
            };

            if (this.accessToken === null) {
                // Prompt the user to select a Google Account and ask for consent
                this.tokenClient.requestAccessToken({prompt: 'consent'});
            } else {
                // Skip display of account chooser and consent dialog for an existing session
                this.tokenClient.requestAccessToken({prompt: ''});
            }
        }
    }

    createPicker() {
        if (!this.pickerInited) {
            alert("Google API 尚未完全載入，請稍後再試。");
            return;
        }

        const callback = this.onFileSelected;
        const view = new google.picker.View(google.picker.ViewId.DOCS);
        view.setMimeTypes(
            "text/plain,application/pdf,image/png,image/jpeg," +
            "application/vnd.google-apps.document,application/vnd.google-apps.spreadsheet," +
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/msword"
        );

        const picker = new google.picker.PickerBuilder()
            .enableFeature(google.picker.Feature.NAV_HIDDEN)
            .enableFeature(google.picker.Feature.MULTISELECT_ENABLED)
            .setDeveloperKey(this.API_KEY)
            .setAppId(this.APP_ID)
            .setOAuthToken(this.accessToken)
            .addView(view)
            .addView(new google.picker.DocsUploadView())
            .setCallback(data => this.pickerCallback(data, callback))
            .build();
        
        picker.setVisible(true);
    }

    async pickerCallback(data, callback = this.onFileSelected) {
        if (data.action === google.picker.Action.PICKED) {
            const doc = data.docs[0];
            const fileId = doc.id;
            const fileName = doc.name;
            const mimeType = doc.mimeType;
            const oauthToken = this.accessToken;

            console.log(`[DriveAdapter] Picked: ${fileName} (${fileId})`);
            
            // 下載檔案內容
            try {
                const content = await this.downloadFile(fileId, mimeType, oauthToken, fileName);
                
                if (callback) {
                    callback({
                        name: fileName,
                        mime: mimeType,
                        content: content // Base64 or Text
                    });
                }
            } catch (err) {
                console.error("[DriveAdapter] Download Error:", err);
                alert("無法下載檔案內容，請確認權限或檔案類型。");
            }
        }
    }

    /**
     * 下載檔案內容
     * 若是 Google Doc/Sheet，需使用 export 介面轉換
     * 若是 一般檔案，使用 alt=media 下載
     */
    async downloadFile(fileId, mimeType, token, fileName = '') {
        if (!fileId || !token) throw new Error('Google 檔案或授權缺失');
        const encodedId = encodeURIComponent(String(fileId));
        let url = `https://www.googleapis.com/drive/v3/files/${encodedId}?alt=media`;
        
        // Handle Google Native Formats (Export)
        if (mimeType === 'application/vnd.google-apps.document') {
            url = `https://www.googleapis.com/drive/v3/files/${encodedId}/export?mimeType=text/plain`;
        } else if (mimeType === 'application/vnd.google-apps.spreadsheet') {
            url = `https://www.googleapis.com/drive/v3/files/${encodedId}/export?mimeType=text/csv`;
        }

        const response = await fetch(url, {
            headers: { 'Authorization': 'Bearer ' + token }
        });

        if (!response.ok) {
            if ([401, 403].includes(response.status) && this.accessToken === token) {
                this.accessToken = null;
                this.tokenExpiresAt = 0;
            }
            throw new Error(`Google 下載未完成 (${response.status})`);
        }

        const blob = await response.blob();
        const effectiveMime = String(blob.type || mimeType || '').toLowerCase();
        if (this._isTextMime(effectiveMime) || this._isTextExt(fileName)) {
            return await blob.text();
        }
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onloadend = () => resolve(reader.result);
            reader.onerror = reject;
            reader.readAsDataURL(blob);
        });
    }
}

// Export Instance
window.driveAdapter = new GoogleDriveAdapter();
