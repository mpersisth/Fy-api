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
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm'; // 支持表格、换行等GFM语法（适配Excel文本）

/**
 * @typedef {Object} MarkdownRendererProps
 * @property {string} content - Markdown文本内容（含Excel纯文本+图片）
 * @property {boolean} [loading=false] - 是否加载中
 * @property {number} [fontSize=16] - 正文字体大小（px）
 * @property {string} [fontFamily='-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'] - 字体
 * @property {React.CSSProperties} [style] - 自定义外层样式
 */

/**
 * 适配纯文本Excel+图片的Markdown渲染组件（无代码块）
 * @param {MarkdownRendererProps} props
 */
const NewMarkdownRenderer = ({
  content,
  loading = false,
  fontSize = 16,
  fontFamily = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
  style = {},
}) => {
  // 加载中状态
  if (loading) {
    return (
      <div style={{ 
        padding: '40px', 
        textAlign: 'center', 
        fontSize: 14, 
        color: '#666',
        ...style
      }}>
        文档加载中...
      </div>
    );
  }

  // 空内容处理
  if (!content || content.trim() === '') {
    return (
      <div style={{ 
        padding: '40px', 
        textAlign: 'center', 
        fontSize: 14, 
        color: '#999',
        ...style
      }}>
        暂无文档内容
      </div>
    );
  }

    // 通用自动换行样式（抽离复用）
  const wrapStyle = {
    wordBreak: 'break-all', // 强制换行：中英文都能在任意位置换行
    overflowWrap: 'break-word', // 单词内换行：避免长单词/URL超出容器
    whiteSpace: 'pre-line', // 保留换行符，同时自动换行
  };
  // 自定义组件（仅保留纯文本、表格、图片、列表等，删除code相关）
  const customComponents = {
    // 标题样式（适配Excel文档的层级）
    h1: ({ children }) => (
      <h1 style={{
        fontSize: `${fontSize * 1.8}px`,
        fontWeight: 600,
        color: '#1f2329',
        margin: '20px 0 10px',
        paddingBottom: '8px',
        fontFamily,
         ...wrapStyle,
      }}>
        {children}
      </h1>
    ),
    h2: ({ children }) => (
      <h2 style={{
        fontSize: `${fontSize * 1.5}px`,
        fontWeight: 600,
        color: '#1f2329',
        margin: '18px 0 8px',
        paddingBottom: '6px',
        fontFamily,
         ...wrapStyle,
      }}>
        {children}
      </h2>
    ),
    h3: ({ children }) => (
      <h3 style={{
        fontSize: `${fontSize * 1.3}px`,
        fontWeight: 600,
        color: '#1f2329',
        margin: '14px 0 6px',
        fontFamily,
         ...wrapStyle,
      }}>
        {children}
      </h3>
    ),
     h4: ({ children }) => (
      <h4 style={{
        fontSize: `${fontSize * 1}px`,
        fontWeight: 600,
        color: '#1f2329',
        margin: '10px 0 0px',
        fontFamily,
         ...wrapStyle,
      }}>
        {children}
      </h4>
     ),
    // 段落样式（优化Excel纯文本的换行/行高）
    p: ({ children }) => (
      <p style={{
        fontSize: `${fontSize}px`,
        lineHeight: 1.8,
        color: '#1f2329',
        margin: '6px 0',
        fontFamily,
         ...wrapStyle,
      }}>
        {children}
      </p>
    ),

    // 图片样式（强化Excel相关图片展示）
    img: ({ src, alt, title }) => (
      <div style={{
        textAlign: 'center',
        margin: '8px 0',
        padding: '4px',
        borderRadius: '6px',
      }}>
        <img
          src={src}
          alt={alt || title || ''}
          title={title}
          style={{
            maxWidth: '100%',
            height: 'auto',
            borderRadius: '4px',
          }}
          loading="lazy" // 懒加载优化
        />
        {alt && (
          <p style={{
            fontSize: `${fontSize * 0.85}px`,
            color: '#6e7781',
            marginTop: '4px',
            lineHeight: 1.5,
            marginBottom: 0,
          }}>
            {alt}
          </p>
        )}
      </div>
    ),
 // 表格样式（单元格自动换行，无横向滚动）
    table: ({ children }) => (
      <div style={{
        margin: '10px 0',
        overflowX: 'hidden', // 关闭表格横向滚动，强制单元格换行
        border: '1px solid #e5e7eb',
        borderRadius: '6px',
      }}>
        <table style={{
          width: '100%',
          borderCollapse: 'collapse',
          fontSize: `${fontSize}px`,
          fontFamily,
          tableLayout: 'fixed', // 表格列宽均分，强制换行
        }}>
          {children}
        </table>
      </div>
    ),
    th: ({ children }) => (
      <th style={{
        padding: '8px 12px',
        backgroundColor: '#f7f8fa',
        border: '1px solid #e5e7eb',
        fontWeight: 600,
        color: '#1f2329',
        textAlign: 'left',
        width: 'auto', // 列宽自适应
        ...wrapStyle, // 表头文本自动换行
      }}>
        {children}
      </th>
    ),
    td: ({ children }) => (
      <td style={{
        padding: '8px 12px',
        border: '1px solid #e5e7eb',
        color: '#1f2329',
        lineHeight: 1.6,
        width: 'auto',
        ...wrapStyle, // 单元格Excel文本自动换行
      }}>
        {children}
      </td>
    ),

    // 列表样式（列表项自动换行）
    ul: ({ children }) => (
      <ul style={{
        // margin: '12px 0',
        paddingLeft: '24px',
        fontSize: `${fontSize}px`,
        lineHeight: 1.8,
        color: '#1f2329',
        fontFamily,
        ...wrapStyle,
      }}>
        {children}
      </ul>
    ),
    ol: ({ children }) => (
      <ol style={{
        // margin: '12px 0',
        paddingLeft: '24px',
        fontSize: `${fontSize}px`,
        lineHeight: 1.8,
        color: '#1f2329',
        fontFamily,
        ...wrapStyle,
      }}>
        {children}
      </ol>
    ),
    li: ({ children }) => (
      <li style={{
        // margin: '4px 0',
        ...wrapStyle, // 列表项文本自动换行
      }}>
        {children}
      </li>
    ),
    // 引用块（适配Excel注释/说明）
    blockquote: ({ children }) => (
      <blockquote style={{
        margin: '8px 0',
        padding: '8px 12px',
        borderLeft: '4px solid #4299e1',
        backgroundColor: '#f5f8ff',
        fontSize: `${fontSize}px`,
        lineHeight: 1.8,
        color: '#4e5969',
        fontFamily,
         ...wrapStyle,
      }}>
        {children}
      </blockquote>
    ),

    // 链接样式（适配Excel中的超链接）
    a: ({ href, children }) => (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        style={{
          color: '#4299e1',
          textDecoration: 'none',
        }}
        onMouseOver={(e) => e.target.style.textDecoration = 'underline'}
        onMouseOut={(e) => e.target.style.textDecoration = 'none'}
      >
        {children}
      </a>
    ),

    // 加粗样式（适配Excel重点内容）
    strong: ({ children }) => (
      <strong style={{
        fontWeight: 600,
        color: '#1f2329',
      }}>
        {children}
      </strong>
    ),
  };

  return (
    <div style={{
      maxWidth: '900px', // 适配Excel表格的展示宽度
      margin: '0 auto',
      padding: '16px 16px',
      ...style,
    }}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]} // 关键：支持Excel文本的换行、表格、任务列表等
        components={customComponents}
        skipHtml={false} // 允许HTML标签（适配Excel导出的文本）
      >
        {content}
      </ReactMarkdown>
    </div>
  );
};

export default NewMarkdownRenderer;