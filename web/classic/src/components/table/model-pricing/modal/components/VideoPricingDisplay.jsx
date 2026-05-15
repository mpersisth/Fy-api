import React from 'react';
import { Table, Typography } from '@douyinfe/semi-ui';
import { useTranslation } from 'react-i18next';

const { Text } = Typography;

const VIDEO_PRICING = {
  'wan2.6-i2v': { '720P': 0.3, '1080P': 0.5 },
  'wan2.6-r2v': { '720P': 0.3, '1080P': 0.5 },
};

const VideoPricingDisplay = ({ modelName, groupRatioValue = 1 }) => {
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
    <Table
      columns={columns}
      dataSource={data}
      pagination={false}
      size="small"
    />
  );
};

export default VideoPricingDisplay;
export { VIDEO_PRICING };
