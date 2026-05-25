/*
Copyright (C) 2025 QuantumNous

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.

For commercial licensing, please contact support@quantumnous.com
*/

import assert from 'node:assert/strict';
import test from 'node:test';
import {
  PRICE_CURRENCIES,
  buildSummaryText,
  getPriceSuffix,
  getUsdExchangeRate,
  toDisplayPrice,
  toUsdPrice,
} from './modelPricingCurrency.js';

const t = (key) => key;

test('getPriceSuffix switches token and request units by currency', () => {
  assert.equal(getPriceSuffix(PRICE_CURRENCIES.USD), '$/1M tokens');
  assert.equal(getPriceSuffix(PRICE_CURRENCIES.CNY), '¥/1M tokens');
  assert.equal(getPriceSuffix(PRICE_CURRENCIES.USD, 'request'), '$/次');
  assert.equal(getPriceSuffix(PRICE_CURRENCIES.CNY, 'request'), '¥/次');
});

test('getUsdExchangeRate accepts positive finite rates and falls back to one', () => {
  assert.equal(getUsdExchangeRate('7.3'), 7.3);
  assert.equal(getUsdExchangeRate(0), 1);
  assert.equal(getUsdExchangeRate(''), 1);
});

test('price conversion keeps USD state as the source of truth', () => {
  assert.equal(toDisplayPrice('1', PRICE_CURRENCIES.USD, 7.3), '1');
  assert.equal(toUsdPrice('1.25', PRICE_CURRENCIES.USD, 7.3), '1.25');
  assert.equal(toDisplayPrice('1', PRICE_CURRENCIES.CNY, 7.3), '7.3');
  assert.equal(toUsdPrice('7.3', PRICE_CURRENCIES.CNY, 7.3), '1');
  assert.equal(toDisplayPrice(toUsdPrice('5', PRICE_CURRENCIES.CNY, 7.3), PRICE_CURRENCIES.CNY, 7.3), '5');
});

test('price conversion handles empty and zero values', () => {
  assert.equal(toDisplayPrice('', PRICE_CURRENCIES.CNY, 7.3), '');
  assert.equal(toUsdPrice('', PRICE_CURRENCIES.CNY, 7.3), '');
  assert.equal(toDisplayPrice(0, PRICE_CURRENCIES.CNY, 7.3), '0');
  assert.equal(toUsdPrice(0, PRICE_CURRENCIES.CNY, 7.3), '0');
});

test('buildSummaryText displays per-token prices in the selected currency', () => {
  const model = {
    billingMode: 'per-token',
    inputPrice: '1',
    completionPrice: '2',
    cachePrice: '',
    createCachePrice: '',
    imagePrice: '',
    audioInputPrice: '',
    audioOutputPrice: '',
  };

  assert.equal(
    buildSummaryText(model, t, PRICE_CURRENCIES.USD, 7.3),
    '输入 $1，额外价格项 1',
  );
  assert.equal(
    buildSummaryText(model, t, PRICE_CURRENCIES.CNY, 7.3),
    '输入 ¥7.3，额外价格项 1',
  );
});

test('buildSummaryText displays per-request prices in the selected currency', () => {
  const model = {
    billingMode: 'per-request',
    fixedPrice: '1.5',
  };

  assert.equal(
    buildSummaryText(model, t, PRICE_CURRENCIES.USD, 7.3),
    '按次 $1.5 / 次',
  );
  assert.equal(
    buildSummaryText(model, t, PRICE_CURRENCIES.CNY, 7.3),
    '按次 ¥10.95 / 次',
  );
});

test('buildSummaryText keeps tiered expression summaries independent of currency', () => {
  const model = {
    billingMode: 'tiered_expr',
    billingExpr: 'tier(p, 1000, 1, 2) + tier(c, 1000, 3, 4)',
    requestRuleExpr: 'if(request_count > 1, 1, 0)',
  };

  assert.equal(
    buildSummaryText(model, t, PRICE_CURRENCIES.CNY, 7.3),
    '阶梯计费 (2 档)，请求规则',
  );
});

test('tiered unit cost conversion uses shared USD source of truth helpers', () => {
  const storedUnitCost = toUsdPrice('7.3', PRICE_CURRENCIES.CNY, 7.3);

  assert.equal(storedUnitCost, '1');
  assert.equal(
    toDisplayPrice(storedUnitCost, PRICE_CURRENCIES.CNY, 7.3),
    '7.3',
  );
  assert.equal(
    toDisplayPrice(storedUnitCost, PRICE_CURRENCIES.USD, 7.3),
    '1',
  );
});
