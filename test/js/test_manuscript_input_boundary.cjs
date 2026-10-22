// Roothinks source maintenance contract
// 目的：真實 startup 事件接線僅接受正文編輯；復原選單不應觸發空白 autosave。
// 邊界：合成 DOM 及工作台依賴，執行既有 init()，不模擬其監聽邏輯。
// 驗證：node test/js/test_manuscript_input_boundary.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = vm.createContext({console, Blob, clearTimeout() {}, setTimeout() {}, document: {addEventListener() {}}});
vm.runInContext(fs.readFileSync('app/static/js/manuscript_ws.js', 'utf8'), context);
const Workspace = vm.runInContext('ManuscriptWorkspace', context);
const app = Object.create(Workspace.prototype);
let onInput, saved = 0;
const noop = () => {};
Object.assign(app, {pid: 'P-p', loadFormalProjects: async () => {}, setupUnloadGuard: noop,
    ui: {bootstrapWorkspace: async () => null, renderSectionDropdown: noop, setupToolbarActions: noop, setupResizer: noop},
    soed: {updateWordCount: noop, setupSocketEvents: noop, setupUIEvents: noop, addSystemMessage: noop, scheduleAutosave() {saved++;}},
    collab: {loadPermissions: noop}, progress: {load: noop},
    editorCanvas: {addEventListener: (name, handler) => {if (name === 'input') onInput=handler;}}});
(async () => {
    app.init();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(typeof onInput, 'function');
    onInput({target: {closest: selector => selector.includes('select') ? {} : null}});
    assert.equal(saved, 0, '復原下拉選單不可觸發正文 autosave');
    onInput({target: {closest: () => null}});
    assert.equal(saved, 1, '正文輸入仍須自動儲存');
    let beacons = 0;
    context.navigator = {sendBeacon() {beacons++; return true;}};
    Object.assign(app.soed, {_draftDirty: true, _draftConflicts: {intro:true}, _draftRevMap:{intro:2}, _rememberDraft() {}});
    app.editorCanvas.querySelectorAll = () => [{getAttribute: () => 'intro', querySelector: () => ({innerHTML:'stale local words'})}];
    assert.equal(app.flushDraftBeacon(), false, '尚未解決衝突時離頁不可自動提交');
    assert.equal(beacons, 0);
    console.log('manuscript input boundary: 3 scenarios passed');
})().catch(error => {console.error(error); process.exitCode=1;});
