const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const model = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../omarchy/agent-usage/Model.js'), 'utf8'), model);

function snapshot() {
  return {
    generated_at: '2026-10-13T00:00:00+00:00',
    rows: [
      {provider: 'codex', label: 'Codex (Gpt)', used_percent: 0, reset_at: '2026-10-13T05:00:00+00:00', window_seconds: 18000, error: null},
      {provider: 'copilot', label: 'Copilot', used_percent: null, reset_at: null, window_seconds: null, error: 'http 401'},
    ],
  };
}

test('accepts zero usage, partial failures, and over-quota values without conflating them', () => {
  const data = snapshot();
  let parsed = model.parseSnapshot(JSON.stringify(data));
  assert.equal(parsed.rows[0].used_percent, 0);
  assert.equal(parsed.rows[1].used_percent, null);
  data.rows[0].used_percent = 125.5;
  parsed = model.parseSnapshot(JSON.stringify(data));
  assert.equal(parsed.rows[0].used_percent, 125.5);
});

test('rejects malformed command output and missing snapshot fields', () => {
  for (const text of ['', 'not json', '{}', 'null', '{"rows":[]}']) {
    assert.throws(() => model.parseSnapshot(text));
  }
});

test('rejects missing quotas rather than coercing null to zero', () => {
  for (const [key, value] of [['used_percent', null], ['used_percent', '4'], ['used_percent', -1], ['reset_at', 'bad date'], ['window_seconds', 0], ['error', '']]) {
    const data = snapshot();
    data.rows[0][key] = value;
    assert.throws(() => model.parseSnapshot(JSON.stringify(data)));
  }
  const data = snapshot();
  data.rows[1].used_percent = 0;
  assert.throws(() => model.parseSnapshot(JSON.stringify(data)));
});

test('distinguishes short and long windows and counts down to now', () => {
  assert.equal(model.duration(18000), '5h');
  assert.equal(model.duration(604800), '7d');
  assert.equal(model.duration(30 * 86400), '30d');
  assert.equal(model.duration(3660), '1h 1m');
  const now = Date.parse('2026-10-13T00:00:00Z');
  assert.equal(model.countdown('2026-10-13T05:00:00Z', now), '5h');
  assert.equal(model.countdown('2026-10-13T02:00:00+02:00', now), 'now');
  assert.equal(model.countdown('2026-10-12T00:00:00Z', now), 'now');
  assert.equal(model.countdown('2026-10-13T00:00:01Z', now), '1m');
});

function paceAt(elapsedFraction, usedPercent, nowOffset = 0) {
  const start = Date.parse('2026-10-01T00:00:00Z');
  const windowSeconds = 20 * 86400;
  const observed = start + elapsedFraction * windowSeconds * 1000;
  const row = {
    provider: 'cursor', label: 'Cursor (*)', used_percent: usedPercent,
    reset_at: new Date(start + windowSeconds * 1000).toISOString(),
    window_seconds: windowSeconds, error: null,
  };
  return model.usagePace(row, new Date(observed).toISOString(), observed + nowOffset);
}

test('pace uses projected exhaustion within the full window, not percentage used', () => {
  assert.equal(paceAt(0.10, 50).level, 'red'); // Exhaustion at 20% of the period.
  assert.equal(paceAt(0.20, 50).level, 'orange');
  assert.equal(paceAt(0.30, 50).level, 'yellow');
  assert.equal(paceAt(0.40, 50).level, 'normal');
  assert.equal(paceAt(0.95, 95).level, 'normal');
  assert.equal(paceAt(0.01, 10).level, 'red');
  assert.match(paceAt(0.10, 50).hint, /20\.0% into this window/);
});

test('pace thresholds are inclusive and select the most severe matching color', () => {
  for (const [elapsed, color] of [
    [0.125, 'red'], [0.12501, 'orange'],
    [0.25, 'orange'], [0.25001, 'yellow'],
    [0.375, 'yellow'], [0.37501, 'normal'],
  ]) {
    assert.equal(paceAt(elapsed, 50).level, color, String(elapsed));
  }
});

test('unused and unknown pace stay neutral; exhausted active quotas stay red', () => {
  assert.equal(paceAt(0.2, 0).level, 'normal');
  assert.equal(paceAt(0, 5).level, 'normal');
  assert.equal(paceAt(-0.1, 5).level, 'normal');
  assert.equal(paceAt(0.8, 100).level, 'red');
  assert.equal(paceAt(0.8, 120).level, 'red');
  const data = snapshot();
  assert.equal(model.usagePace(data.rows[1], data.generated_at, Date.parse(data.generated_at)).level, 'normal');
  assert.equal(model.usagePace(data.rows[0], 'not a date', Date.now()).level, 'normal');
});

test('cached pace stays fixed until refresh; passed resets clear the old warning', () => {
  const day = 86400 * 1000;
  assert.equal(paceAt(0.10, 50).level, 'red');
  assert.equal(paceAt(0.10, 50, 10 * day).level, 'red');
  assert.equal(paceAt(0.10, 50, 18 * day).level, 'normal');
  assert.match(paceAt(0.10, 50, 18 * day).hint, /reset has passed/);
  assert.equal(paceAt(0.8, 100, 4 * day).level, 'normal');
});
