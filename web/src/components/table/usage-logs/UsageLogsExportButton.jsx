/*
 * Fy-api overlay component — not from upstream new-api.
 *
 * Renders a "Export CSV" button next to the usage-logs statistics. When
 * clicked, the button reads the current filter values from `formApi` and
 * fetches a CSV from either
 *   /api/log/export            (admin view)
 * or
 *   /api/log/self/export       (non-admin view)
 * with the same query-string params used by the table. The fetch explicitly
 * attaches the `New-Api-User` header because the backend AdminAuth /
 * UserAuth middleware rejects requests without it — a plain
 * `window.location.assign` cannot attach custom headers, so we pull the
 * bytes into a Blob and trigger the download via a synthetic <a download>.
 * The backend implementation lives in controller/log_export.go and
 * model/log_export.go.
 */

import React, { useState } from 'react';
import { Button, Tooltip } from '@douyinfe/semi-ui';
import { IconDownload } from '@douyinfe/semi-icons';
import { getUserIdFromLocalStorage, showError } from '../../../helpers/utils';

const UsageLogsExportButton = ({ formApi, isAdminUser, t }) => {
  const [loading, setLoading] = useState(false);

  const onExport = async () => {
    const values = formApi ? formApi.getValues() || {} : {};

    // dateRange → start/end timestamp (seconds)
    let startTs = '';
    let endTs = '';
    if (
      values.dateRange &&
      Array.isArray(values.dateRange) &&
      values.dateRange.length === 2
    ) {
      startTs = String(
        Math.floor(Date.parse(values.dateRange[0]) / 1000) || '',
      );
      endTs = String(Math.floor(Date.parse(values.dateRange[1]) / 1000) || '');
    }

    const params = new URLSearchParams();
    params.set('type', String(values.logType ?? 0));
    if (startTs) params.set('start_timestamp', startTs);
    if (endTs) params.set('end_timestamp', endTs);
    if (values.token_name) params.set('token_name', values.token_name);
    if (values.model_name) params.set('model_name', values.model_name);
    if (values.group) params.set('group', values.group);
    if (values.request_id) params.set('request_id', values.request_id);

    if (isAdminUser) {
      if (values.username) params.set('username', values.username);
      if (values.channel) params.set('channel', String(values.channel));
    }

    const url = isAdminUser
      ? `/api/log/export?${params.toString()}`
      : `/api/log/self/export?${params.toString()}`;

    try {
      setLoading(true);
      // The backend AdminAuth/UserAuth middleware requires a `New-Api-User`
      // header alongside the session cookie. A plain window.location.assign
      // download cannot attach custom headers, so we do an authenticated
      // fetch and stream the response into a blob the browser then saves.
      const response = await fetch(url, {
        headers: {
          'New-API-User': getUserIdFromLocalStorage(),
        },
      });

      if (!response.ok) {
        const text = await response.text();
        throw new Error(
          text || response.statusText || `HTTP ${response.status}`,
        );
      }

      const blob = await response.blob();
      const objectUrl = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = objectUrl;
      link.download = 'logs.csv';
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(objectUrl);
    } catch (error) {
      showError(error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Tooltip content={t('导出当前筛选条件下的日志为 CSV（最多 5 万条）')}>
      <Button
        theme='light'
        type='tertiary'
        size='small'
        loading={loading}
        icon={<IconDownload />}
        onClick={onExport}
        className='!rounded-lg'
      >
        {t('导出 CSV')}
      </Button>
    </Tooltip>
  );
};

export default UsageLogsExportButton;
