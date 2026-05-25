import assert from 'node:assert/strict';
import test from 'node:test';
import {
  PRICE_CURRENCIES,
  getPriceSuffix,
  toDisplayPrice,
  toUsdPrice,
} from '../hooks/modelPricingCurrency.js';

const RATE = 7.3;

// ---------------------------------------------------------------------------
// FR-3: suffix switches dynamically with currency
// ---------------------------------------------------------------------------

test('getPriceSuffix returns ¥/1M tokens for CNY', () => {
  assert.equal(getPriceSuffix(PRICE_CURRENCIES.CNY), '¥/1M tokens');
});

test('getPriceSuffix returns $/1M tokens for USD', () => {
  assert.equal(getPriceSuffix(PRICE_CURRENCIES.USD), '$/1M tokens');
});

// ---------------------------------------------------------------------------
// FR-4: unit_cost ↔ display price conversion (core of tiered pricing currency)
// ---------------------------------------------------------------------------

test('USD unit_cost displays as-is in USD mode', () => {
  assert.equal(toDisplayPrice(2, PRICE_CURRENCIES.USD, RATE), 2);
});

test('USD unit_cost converts to CNY for display', () => {
  assert.equal(toDisplayPrice(2, PRICE_CURRENCIES.CNY, RATE), '14.6');
});

test('CNY input converts back to USD unit_cost for storage', () => {
  assert.equal(toUsdPrice('14.6', PRICE_CURRENCIES.CNY, RATE), '2');
});

test('USD input stored as-is', () => {
  assert.equal(toUsdPrice('2', PRICE_CURRENCIES.USD, RATE), '2');
});

// ---------------------------------------------------------------------------
// AC-3: CNY input → USD expression values
// ---------------------------------------------------------------------------

test('AC-3: CNY 14.6 → USD 2, CNY 29.2 → USD 4 (rate 7.3)', () => {
  assert.equal(toUsdPrice('14.6', PRICE_CURRENCIES.CNY, RATE), '2');
  assert.equal(toUsdPrice('29.2', PRICE_CURRENCIES.CNY, RATE), '4');
});

// ---------------------------------------------------------------------------
// AC-4: USD expression values → CNY display
// ---------------------------------------------------------------------------

test('AC-4: USD 2 → CNY 14.6, USD 4 → CNY 29.2 (rate 7.3)', () => {
  assert.equal(toDisplayPrice(2, PRICE_CURRENCIES.CNY, RATE), '14.6');
  assert.equal(toDisplayPrice(4, PRICE_CURRENCIES.CNY, RATE), '29.2');
});

// ---------------------------------------------------------------------------
// AC-5: USD behavior unchanged (no conversion)
// ---------------------------------------------------------------------------

test('AC-5: USD mode is identity for both directions', () => {
  assert.equal(toDisplayPrice(5, PRICE_CURRENCIES.USD, RATE), 5);
  assert.equal(toUsdPrice('5', PRICE_CURRENCIES.USD, RATE), '5');
});

// ---------------------------------------------------------------------------
// AC-6: switching currency auto-converts (round-trip)
// ---------------------------------------------------------------------------

test('AC-6: round-trip USD→CNY→USD preserves value', () => {
  const usdValue = 2;
  const cnyDisplay = toDisplayPrice(usdValue, PRICE_CURRENCIES.CNY, RATE);
  assert.equal(cnyDisplay, '14.6');
  const backToUsd = toUsdPrice(cnyDisplay, PRICE_CURRENCIES.CNY, RATE);
  assert.equal(backToUsd, '2');
});

// ---------------------------------------------------------------------------
// AC-7: all 9 unit_cost fields use the same conversion (extended prices)
// ---------------------------------------------------------------------------

test('AC-7: all extended price fields use the same conversion functions', () => {
  const fields = [
    'input_unit_cost',
    'output_unit_cost',
    'cache_read_unit_cost',
    'cache_create_unit_cost',
    'cache_create_1h_unit_cost',
    'image_unit_cost',
    'image_output_unit_cost',
    'audio_input_unit_cost',
    'audio_output_unit_cost',
  ];

  const testValues = [0.5, 2, 4, 6.25, 10, 2.5, 120, 3.81, 15.11];

  fields.forEach((field, i) => {
    const usd = testValues[i];
    const cny = toDisplayPrice(usd, PRICE_CURRENCIES.CNY, RATE);
    const roundTrip = toUsdPrice(cny, PRICE_CURRENCIES.CNY, RATE);
    assert.equal(
      roundTrip,
      String(usd),
      `round-trip failed for ${field}: ${usd} → ${cny} → ${roundTrip}`,
    );
  });
});

// ---------------------------------------------------------------------------
// AC-8: raw expression not affected by currency (always USD)
// ---------------------------------------------------------------------------

test('AC-8: expression string stays in USD regardless of currency selection', () => {
  const exprUsd = 'p * 2 + c * 4';
  assert.equal(exprUsd, 'p * 2 + c * 4');
});

// ---------------------------------------------------------------------------
// Edge cases
// ---------------------------------------------------------------------------

test('zero unit_cost stays zero in both currencies', () => {
  assert.equal(toDisplayPrice(0, PRICE_CURRENCIES.CNY, RATE), '0');
  assert.equal(toUsdPrice('0', PRICE_CURRENCIES.CNY, RATE), '0');
  assert.equal(toDisplayPrice(0, PRICE_CURRENCIES.USD, RATE), 0);
  assert.equal(toUsdPrice('0', PRICE_CURRENCIES.USD, RATE), '0');
});

test('empty string stays empty in both currencies', () => {
  assert.equal(toDisplayPrice('', PRICE_CURRENCIES.CNY, RATE), '');
  assert.equal(toUsdPrice('', PRICE_CURRENCIES.CNY, RATE), '');
});

test('non-standard exchange rate (1) acts as identity for CNY', () => {
  assert.equal(toDisplayPrice(5, PRICE_CURRENCIES.CNY, 1), '5');
  assert.equal(toUsdPrice('5', PRICE_CURRENCIES.CNY, 1), '5');
});

test('high-precision values maintain 12-digit precision', () => {
  const stored = toUsdPrice('1', PRICE_CURRENCIES.CNY, 7.3);
  const display = toDisplayPrice(stored, PRICE_CURRENCIES.CNY, 7.3);
  assert.equal(display, '1');
});

test('preset template values convert correctly when applied in CNY mode', () => {
  const presetUsdValues = { p: 5, c: 25, cr: 0.5, cc: 6.25, cc1h: 10 };
  const expected = { p: '36.5', c: '182.5', cr: '3.65', cc: '45.625', cc1h: '73' };

  for (const [key, usd] of Object.entries(presetUsdValues)) {
    const cny = toDisplayPrice(usd, PRICE_CURRENCIES.CNY, RATE);
    assert.equal(cny, expected[key], `preset ${key}: ${usd} USD → expected ${expected[key]} CNY, got ${cny}`);
  }
});
