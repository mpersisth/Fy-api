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

export const PRICE_CURRENCIES = {
  USD: 'USD',
  CNY: 'CNY',
};
export const PRICE_SUFFIX = '$/1M tokens';

export const hasValue = (value) =>
  value !== '' && value !== null && value !== undefined && value !== false;

const toNumberOrNull = (value) => {
  if (!hasValue(value) && value !== 0) {
    return null;
  }
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
};

const formatNumber = (value) => {
  const num = toNumberOrNull(value);
  if (num === null) {
    return '';
  }
  return parseFloat(num.toPrecision(12)).toString();
};

export const getPriceSuffix = (currency, unit = 'token') => {
  const symbol = currency === PRICE_CURRENCIES.CNY ? '¥' : '$';
  return unit === 'request' ? `${symbol}/次` : `${symbol}/1M tokens`;
};

export const getUsdExchangeRate = (value) => {
  const num = Number(value);
  if (Number.isFinite(num) && num > 0) {
    return num;
  }
  console.warn('Invalid usdExchangeRate, fallback to 1');
  return 1;
};

export const toDisplayPrice = (value, currency, usdExchangeRate) => {
  if (!hasValue(value) && value !== 0) {
    return '';
  }
  if (currency !== PRICE_CURRENCIES.CNY) {
    return value;
  }
  const num = toNumberOrNull(value);
  return num === null
    ? ''
    : formatNumber(num * getUsdExchangeRate(usdExchangeRate));
};

export const toUsdPrice = (value, currency, usdExchangeRate) => {
  if (!hasValue(value) && value !== 0) {
    return '';
  }
  if (currency !== PRICE_CURRENCIES.CNY) {
    return value;
  }
  const num = toNumberOrNull(value);
  return num === null
    ? ''
    : formatNumber(num / getUsdExchangeRate(usdExchangeRate));
};

export const buildSummaryText = (
  model,
  t,
  currency = PRICE_CURRENCIES.USD,
  usdExchangeRate = 1,
) => {
  const requestRuleSuffix =
    model.billingMode === 'tiered_expr' && model.requestRuleExpr
      ? `，${t('请求规则')}`
      : '';
  const symbol = currency === PRICE_CURRENCIES.CNY ? '¥' : '$';
  if (model.billingMode === 'tiered_expr') {
    const expr = model.billingExpr;
    if (!expr) return `${t('表达式计费')}${requestRuleSuffix}`;
    const tierCount = (expr.match(/tier\(/g) || []).length;
    if (tierCount === 0) {
      return `${t('表达式计费')}${requestRuleSuffix}`;
    }
    return `${t('阶梯计费')} (${tierCount} ${t('档')})${requestRuleSuffix}`;
  }

  if (model.billingMode === 'per-request' && hasValue(model.fixedPrice)) {
    return `${t('按次')} ${symbol}${toDisplayPrice(
      model.fixedPrice,
      currency,
      usdExchangeRate,
    )} / ${t('次')}${requestRuleSuffix}`;
  }

  if (hasValue(model.inputPrice)) {
    const extraCount = [
      model.completionPrice,
      model.cachePrice,
      model.createCachePrice,
      model.imagePrice,
      model.audioInputPrice,
      model.audioOutputPrice,
    ].filter(hasValue).length;
    const extraLabel =
      extraCount > 0 ? `，${t('额外价格项')} ${extraCount}` : '';
    return `${t('输入')} ${symbol}${toDisplayPrice(
      model.inputPrice,
      currency,
      usdExchangeRate,
    )}${extraLabel}${requestRuleSuffix}`;
  }

  return `${t('未设置价格')}${requestRuleSuffix}`;
};
