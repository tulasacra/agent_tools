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

test('accepts an explicitly unknown window while retaining quota and reset data', () => {
  const data = snapshot();
  data.rows[0].window_seconds = null;
  const row = model.parseSnapshot(JSON.stringify(data)).rows[0];
  assert.equal(row.window_seconds, null);
  assert.equal(row.used_percent, 0);
  assert.equal(row.reset_at, data.rows[0].reset_at);
  assert.equal(row.error, null);
});

test('manual reset balances preserve zero and normalize missing or invalid values to unknown', () => {
  for (const value of [3, 0, undefined, null, -1, 1.5, '3', true, false, [], {}, Infinity, NaN]) {
    const data = snapshot();
    data.rows[0].manual_resets_available = value;
    const parsed = model.parseSnapshot(JSON.stringify(data));
    const expected = value === 3 || value === 0 ? value : null;
    assert.equal(parsed.rows[0].manual_resets_available, expected);
    assert.equal(parsed.rows[0].used_percent, 0);
    assert.equal(parsed.rows[0].error, null);
    assert.equal(parsed.rows[1].manual_resets_available, null);
    assert.equal(parsed.rows[0].manual_reset_expirations, null);
    assert.equal(model.manualResetSummary(parsed.rows, 0),
      `Manual resets available: ${expected === null ? '—' : expected}${expected === 0 ? '' : ' · Next expiry: —'}`);
  }
});

test('manual reset summary appears once after the final Codex window only', () => {
  const data = snapshot();
  data.rows[0].manual_resets_available = 3;
  data.rows.splice(1, 0, {...data.rows[0], window_seconds: 604800});
  const {rows} = model.parseSnapshot(JSON.stringify(data));
  assert.equal(model.manualResetSummary(rows, 0), '');
  assert.equal(model.manualResetSummary(rows, 1), 'Manual resets available: 3 · Next expiry: —');
  assert.equal(model.manualResetSummary(rows, 2), '');
  rows.pop(); // The final provider also gets its summary.
  assert.equal(model.manualResetSummary(rows, 1), 'Manual resets available: 3 · Next expiry: —');
});

test('unavailable Codex balances stay unknown alongside the provider error', () => {
  const data = snapshot();
  data.rows = [{...data.rows[1], provider: 'codex', label: 'Codex', manual_resets_available: null}];
  const parsed = model.parseSnapshot(JSON.stringify(data));
  assert.equal(parsed.rows[0].error, 'http 401');
  assert.equal(model.manualResetSummary(parsed.rows, 0), 'Manual resets available: — · Next expiry: —');
});

const earlyExpiry = '2026-10-22T21:13:23.818Z';
const lateExpiry = '2026-10-29T19:44:01.802Z';
const expiryNow = Date.parse('2026-10-14T00:00:00Z');
// The widget supplies Qt's local formatter; test the model independently of Qt.
const formatExpiry = (value, includeZone) => `${value}${includeZone ? ' (local zone)' : ''}`;

function withExpirations(count, expirations) {
  const data = snapshot();
  Object.assign(data.rows[0], {manual_resets_available: count, manual_reset_expirations: expirations});
  return model.parseSnapshot(JSON.stringify(data)).rows;
}

test('expiration details normalize offsets, sort by instant, and preserve duplicates and unknowns', () => {
  const rows = withExpirations(4, [lateExpiry, null, '2026-10-22T23:13:23.818918+02:00', earlyExpiry]);
  assert.deepEqual(Array.from(rows[0].manual_reset_expirations), [earlyExpiry, earlyExpiry, lateExpiry, null]);
  assert.equal(model.manualResetSummary(rows, 0, expiryNow, formatExpiry),
    `Manual resets available: 4 · Earliest known expiry: ${earlyExpiry}`);
  assert.equal(model.manualResetTooltip(rows[0], expiryNow, formatExpiry), [
    'Reset expirations (local time)', `${earlyExpiry} (local zone)`, `${earlyExpiry} (local zone)`,
    `${lateExpiry} (local zone)`, 'Expiration unknown',
  ].join('\n'));
});

test('complete expiry details show the soonest date inline and all dates in the tooltip', () => {
  const rows = withExpirations(2, [lateExpiry, earlyExpiry]);
  assert.equal(model.manualResetSummary(rows, 0, expiryNow, formatExpiry),
    `Manual resets available: 2 · Next expiry: ${earlyExpiry}`);
  assert.equal(model.manualResetTooltip(rows[0], expiryNow, formatExpiry),
    `Reset expirations (local time)\n${earlyExpiry} (local zone)\n${lateExpiry} (local zone)`);
  rows.splice(1, 0, {...rows[0], window_seconds: 604800});
  assert.equal(model.manualResetSummary(rows, 0, expiryNow, formatExpiry), '');
  assert.match(model.manualResetSummary(rows, 1, expiryNow, formatExpiry), /Next expiry:/);
  rows.splice(1, 0, {...rows[0], provider: 'cursor'});
  assert.equal(model.manualResetSummary(rows, 0, expiryNow, formatExpiry), '');
  assert.equal(model.manualResetSummary(rows, 1, expiryNow, formatExpiry), '');
  assert.match(model.manualResetSummary(rows, 2, expiryNow, formatExpiry), /Next expiry:/);
});

test('sub-millisecond expiry precision cannot round into the following minute', () => {
  const rows = withExpirations(1, ['2026-10-22T21:13:59.999999+00:00']);
  assert.deepEqual(Array.from(rows[0].manual_reset_expirations), ['2026-10-22T21:13:59.999Z']);
});

test('partial or mismatched expiry details never claim to establish the next expiration', () => {
  for (const [count, dates, note] of [
    [3, [lateExpiry], 'Details returned for 1 of 3 available resets.'],
    [1, [lateExpiry, earlyExpiry], 'Details and reset balance differ; refresh to check.'],
    [null, [lateExpiry], 'Total reset count unavailable.'],
  ]) {
    const rows = withExpirations(count, dates);
    assert.match(model.manualResetSummary(rows, 0, expiryNow, formatExpiry), /Earliest known expiry:/);
    assert.ok(model.manualResetTooltip(rows[0], expiryNow, formatExpiry).endsWith(note));
  }
});

test('missing and malformed optional details keep the balance and quotas usable', () => {
  for (const value of [undefined, null, 'bad', {}, 0, false]) {
    const rows = withExpirations(3, value);
    assert.equal(rows[0].manual_reset_expirations, null);
    assert.equal(rows[0].manual_resets_available, 3);
    assert.equal(rows[0].used_percent, 0);
    assert.equal(model.manualResetSummary(rows, 0, expiryNow, formatExpiry),
      'Manual resets available: 3 · Next expiry: —');
    assert.equal(model.manualResetTooltip(rows[0], expiryNow, formatExpiry), 'Expiration details unavailable.');
  }
  const rows = withExpirations(7, [null, 'bad', '3', 123, false, {}, '2026-10-22T99:00:00Z']);
  assert.deepEqual(Array.from(rows[0].manual_reset_expirations), Array(7).fill(null));
  assert.match(model.manualResetSummary(rows, 0, expiryNow, formatExpiry), /Next expiry: —$/);
  assert.equal(model.manualResetTooltip(rows[0], expiryNow, formatExpiry).split('Expiration unknown').length - 1, 7);
  const empty = withExpirations(3, []);
  assert.match(model.manualResetTooltip(empty[0], expiryNow, formatExpiry), /Details returned for 0 of 3/);
});

test('zero balances hide expirations and passed cached dates request refresh without changing the balance', () => {
  const zero = withExpirations(0, [earlyExpiry]);
  assert.equal(model.manualResetSummary(zero, 0, expiryNow, formatExpiry), 'Manual resets available: 0');
  assert.equal(model.manualResetTooltip(zero[0], expiryNow, formatExpiry), '');
  const rows = withExpirations(2, [earlyExpiry, lateExpiry]);
  const passed = Date.parse(earlyExpiry);
  assert.match(model.manualResetSummary(rows, 0, passed, formatExpiry), / \(passed; refresh\)$/);
  assert.equal(model.manualResetTooltip(rows[0], passed, formatExpiry).split('(passed; refresh)').length - 1, 1);
  assert.equal(rows[0].manual_resets_available, 2);
  assert.equal(rows[0].manual_reset_expirations.length, 2);
});

test('rejects malformed command output and missing snapshot fields', () => {
  for (const text of ['', 'not json', '{}', 'null', '{"rows":[]}']) {
    assert.throws(() => model.parseSnapshot(text));
  }
});

test('rejects missing quotas rather than coercing null to zero', () => {
  for (const [key, value] of [['used_percent', null], ['used_percent', '4'], ['used_percent', -1], ['reset_at', 'bad date'], ['window_seconds', 0], ['window_seconds', -1], ['window_seconds', '18000'], ['window_seconds', undefined], ['error', '']]) {
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

test('unknown windows disable projections but still flag an exhausted quota until reset', () => {
  const data = snapshot();
  const row = data.rows[0];
  const now = Date.parse(data.generated_at);
  row.window_seconds = null;
  row.used_percent = 75;
  const pace = model.usagePace(row, data.generated_at, now);
  assert.equal(pace.level, 'normal');
  assert.match(pace.hint, /window length is unknown/);
  assert.equal(model.countdown(row.reset_at, now), '5h');
  row.used_percent = 0;
  assert.match(model.usagePace(row, data.generated_at, now).hint, /No quota used/);
  row.used_percent = 100;
  assert.equal(model.usagePace(row, data.generated_at, now).level, 'red');
  const expired = model.usagePace(row, data.generated_at, Date.parse(row.reset_at));
  assert.equal(expired.level, 'normal');
  assert.match(expired.hint, /reset has passed/);
});

test('cached pace stays fixed until refresh; passed resets clear the old warning', () => {
  const day = 86400 * 1000;
  assert.equal(paceAt(0.10, 50).level, 'red');
  assert.equal(paceAt(0.10, 50, 10 * day).level, 'red');
  assert.equal(paceAt(0.10, 50, 18 * day).level, 'normal');
  assert.match(paceAt(0.10, 50, 18 * day).hint, /reset has passed/);
  assert.equal(paceAt(0.8, 100, 4 * day).level, 'normal');
});
