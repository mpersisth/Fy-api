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

import React from 'react';
import { Table, Typography } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

const { Text } = Typography;

// Fy-api overlay: show fixed wan2.6 video RMB-per-second pricing by resolution.
const VIDEO_PRICING = {
  'wan2.6-i2v': { '720P': 0.3, '1080P': 0.5 },
  'wan2.6-r2v': { '720P': 0.3, '1080P': 0.5 },
};

const VideoPricingDisplay = ({
  modelName,
  groupRatioValue = 1,
  title = 'Fixed Price (RMB)',
}) => {
  const { t } = useTranslation();
  const pricing = VIDEO_PRICING[modelName];
  if (!pricing) return null;

  const data = Object.entries(pricing).map(([res, price]) => ({
    key: res,
    resolution: res,
    pricePerSec: (price * groupRatioValue).toFixed(2),
  }));

  const columns = [
    { title: t('分辨率'), dataIndex: 'resolution', key: 'resolution' },
    {
      title: t('单价'),
      dataIndex: 'pricePerSec',
      key: 'pricePerSec',
      render: (val) => <Text>{`¥${val}/${t('秒')}`}</Text>,
    },
  ];

  return (
    <div>
      <Text type='tertiary' size='small' className='block mb-2'>
        {title}
      </Text>
      <Table
        columns={columns}
        dataSource={data}
        pagination={false}
        size='small'
      />
    </div>
  );
};

export default VideoPricingDisplay;
export { VIDEO_PRICING };
