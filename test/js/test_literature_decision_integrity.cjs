// Roothinks source maintenance contract
// 目的：文獻決策的確認回應不能刪除送出後的新文字；失敗保留原版號與副本。
// 邊界：合成 DOM/fetch/sessionStorage，真實操作另由 Playwright 驗收。
// 驗證：node test/js/test_literature_decision_integrity.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup() {
    const stored = new Map(), status = {textContent: ''};
    const controls = [
        {dataset: {field: 'screening_note'}, value: 'original reason'},
        {dataset: {field: 'screening_status'}, value: 'included'},
    ];
    const form = {dataset: {entry: 'e1', rev: '0'},
        querySelectorAll: () => controls, querySelector: () => status};
    const context = vm.createContext({console, URLSearchParams, alert() {},
        window: {literatureApp: {currentPid: 'P-p'}},
        document: {addEventListener() {}, getElementById: id => id === 'libraryPanel' ? {dataset: {owner: '17'}} : null},
        sessionStorage: {getItem: k => stored.get(k), setItem: (k,v) => stored.set(k,v), removeItem: k => stored.delete(k)}});
    const source = fs.readFileSync('app/static/js/literature_library.js', 'utf8')
        .replace('    window.literatureLibrary =', '    globalThis.check = {saveDecision, draftKey}; window.literatureLibrary =');
    vm.runInContext(source, context);
    return {context, controls, form, stored, status};
}

(async () => {
    {
        const {context, controls, form, stored, status} = setup();
        let resolve;
        context.fetch = () => new Promise(done => {resolve = done;});
        const saving = context.check.saveDecision(form, {});
        controls[0].value = 'new thought typed while saving';
        resolve({ok:true, json:async () => ({status:'success', entry:{entry_id:'e1', _rev:1}})});
        await saving;
        assert.ok([...stored.values()].some(v => v.includes('new thought typed while saving')));
        assert.notEqual(status.textContent, '已儲存');
        assert.equal(Number(form.dataset.rev), 1);
    }
    {
        const {context, form, stored, status} = setup();
        context.fetch = async () => ({ok:false, json:async () => ({status:'error', conflict:true, message:'conflict'})});
        await context.check.saveDecision(form, {});
        assert.equal(form.dataset.rev, '0');
        assert.ok([...stored.values()].some(v => v.includes('original reason')));
        assert.ok(status.textContent.includes('未儲存'));
    }
    console.log('literature decision integrity: 2 scenarios passed');
})().catch(error => {console.error(error); process.exitCode=1;});
