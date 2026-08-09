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
        // [CONFIG] 請填入您的 Google Cloud 憑證資訊
        this.API_KEY = 'YOUR_API_KEY_HERE';
        this.CLIENT_ID = 'YOUR_CLIENT_ID_HERE';
        this.APP_ID = 'YOUR_PROJECT_NUMBER_HERE'; // Project Number (not ID)

        // Scopes: drive.file (只存取此 App 建立或使用者選取的檔案，較安全)
        this.SCOPES = 'https://www.googleapis.com/auth/drive.file https://www.googleapis.com/auth/drive.readonly';
        
        this.tokenClient = null;
        this.accessToken = null;
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
                await gapi.client.load('https://www.googleapis.com/discovery/v1/apis/drive/v3/rest');
                this.pickerInited = true;
                console.log("[DriveAdapter] GAPI Loaded.");
            });
        }
    }

    loadGis() {
        if(window.google) {
            this.tokenClient = google.accounts.oauth2.initTokenClient({
                client_id: this.CLIENT_ID,
                scope: this.SCOPES,
                callback: '', // defined at request time
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
        if (!this.API_KEY || this.API_KEY === 'YOUR_API_KEY_HERE') {
            alert("請先在 google_driveapi.js 設定 API Key 與 Client ID！");
            return;
        }

        this.onFileSelected = callback;

        // Check Auth
        if (this.accessToken) {
            this.createPicker();
        } else {
            // Request Auth
            this.tokenClient.callback = async (response) => {
                if (response.error !== undefined) {
                    throw (response);
                }
                this.accessToken = response.access_token;
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
            .setCallback(this.pickerCallback.bind(this))
            .build();
        
        picker.setVisible(true);
    }

    async pickerCallback(data) {
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
                
                if (this.onFileSelected) {
                    this.onFileSelected({
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
        let url = `https://www.googleapis.com/drive/v3/files/${fileId}?alt=media`;
        
        // Handle Google Native Formats (Export)
        if (mimeType === 'application/vnd.google-apps.document') {
            url = `https://www.googleapis.com/drive/v3/files/${fileId}/export?mimeType=text/plain`;
        } else if (mimeType === 'application/vnd.google-apps.spreadsheet') {
            url = `https://www.googleapis.com/drive/v3/files/${fileId}/export?mimeType=text/csv`;
        }

        const response = await fetch(url, {
            headers: { 'Authorization': 'Bearer ' + token }
        });

        if (!response.ok) throw new Error(`Fetch failed: ${response.statusText}`);

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
