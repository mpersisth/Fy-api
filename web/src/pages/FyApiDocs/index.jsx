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
import React, { useState, useEffect } from 'react';
import NewMarkdownRenderer from '../../components/common/NewMarkdownRender/NewMarkdownRender.jsx'

// MD手册的服务器路径（域名+MD文件路径）
const MD_MANUAL_URL = '/docs/Fy-api.md';

export default function FyApiDocs() {
  const [mdContent, setMdContent] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // 组件挂载后，加载MD文件内容
  useEffect(() => {
    const loadMdManual = async () => {
      try {
        // 发送请求加载服务器上的MD文件
        const response = await fetch(MD_MANUAL_URL, {
          method: 'GET',
          headers: {
            'Content-Type': 'text/markdown'
          }
        });

        if (!response.ok) {
          throw new Error(`加载失败：${response.status} ${response.statusText}`);
        }

        // 读取MD文件的纯文本内容
        const content = await response.text();
        setMdContent(content);
      } catch (err) {
        setError(err.message);
        console.error('加载MD手册失败：', err);
      } finally {
        setLoading(false);
      }
    };

    loadMdManual();
  }, []);

  // 加载中/错误/渲染状态展示
  if (loading) {
    return (
      <div style={{ padding: '50px', textAlign: 'center' }}>
        <h3>正在加载 Fy-api 说明手册...</h3>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: '50px', color: '#f5222d' }}>
        <h3>手册加载失败</h3>
        <p>{error}</p>
        <p>请检查MD文件路径：{MD_MANUAL_URL}</p>
      </div>
    );
  }

  // 渲染MD手册（复用你的MarkdownRenderer，保持样式统一）
  return (
    <div style={{ 
      maxWidth: '1200px', 
      margin: '0 auto', 
      padding: '24px 16px',
      minHeight: '100vh',
      backgroundColor: '#fff'
    }}>
      <h1 style={{ 
        textAlign: 'center', 
        marginTop: '60px',
        fontSize: '28.8px',
        fontWeight: '600',
        fontFamily: 'Microsoft YaHei", sans-serif',
        color: 'rgb(31, 35, 41)'
      }}>Fy-api 说明手册</h1>
      <NewMarkdownRenderer
        content={mdContent}
        loading={false}
        fontSize={16}  // 手册字体稍大，提升可读性
        fontFamily="Microsoft YaHei, sans-serif"
        animated={false}
        style={{ lineHeight: '1.8' }}
      />
    </div>
  );
}