const assert = require('node:assert/strict');
const {
    isPaqPrincipalInvestigator,
} = require('../../app/static/js/paq_project.js');

for (const role of [
    '主持人 (Principal Investigator)',
    'Principal Investigator',
    'PI',
    'Lead PI',
]) {
    assert.equal(isPaqPrincipalInvestigator(role), true, role);
}

for (const role of [
    '共同主持人 (Co-PI)',
    'Co-PI',
    'Co PI',
    'Co-Principal Investigator',
    '研究助理',
    '',
]) {
    assert.equal(isPaqPrincipalInvestigator(role), false, role);
}

console.log('PAQ PI role behavior: passed');
