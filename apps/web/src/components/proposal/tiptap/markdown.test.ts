import { describe, expect, it } from 'vitest';

import { markdownToHTML } from './markdown';

describe('markdownToHTML', () => {
  it('returns empty string for null / undefined / empty input', () => {
    expect(markdownToHTML(null)).toBe('');
    expect(markdownToHTML(undefined)).toBe('');
    expect(markdownToHTML('')).toBe('');
  });

  it('wraps plain text in a single <p>', () => {
    expect(markdownToHTML('Hello world.')).toBe('<p>Hello world.</p>');
  });

  it('joins consecutive non-blank lines into one paragraph', () => {
    // Markdown convention: a hard-wrapped paragraph stays one <p>.
    expect(markdownToHTML('Line one.\nLine two.')).toBe(
      '<p>Line one. Line two.</p>',
    );
  });

  it('splits paragraphs on blank lines', () => {
    expect(markdownToHTML('Para one.\n\nPara two.')).toBe(
      '<p>Para one.</p><p>Para two.</p>',
    );
  });

  it('promotes ## to <h2>, ### to <h3>, #### to <h4>', () => {
    expect(markdownToHTML('## 1.1 Objectives')).toBe(
      '<h2>1.1 Objectives</h2>',
    );
    expect(markdownToHTML('### 1.1.1 Sub')).toBe('<h3>1.1.1 Sub</h3>');
    expect(markdownToHTML('#### Deeper')).toBe('<h4>Deeper</h4>');
  });

  it('renders unordered lists from "- item" or "* item"', () => {
    expect(markdownToHTML('- alpha\n- beta')).toBe(
      '<ul><li>alpha</li><li>beta</li></ul>',
    );
    expect(markdownToHTML('* alpha\n* beta')).toBe(
      '<ul><li>alpha</li><li>beta</li></ul>',
    );
  });

  it('renders ordered lists from "N. item"', () => {
    expect(markdownToHTML('1. first\n2. second')).toBe(
      '<ol><li>first</li><li>second</li></ol>',
    );
  });

  it('escapes HTML entities in plain text', () => {
    expect(markdownToHTML('a <script>tag</script>')).toBe(
      '<p>a &lt;script&gt;tag&lt;/script&gt;</p>',
    );
  });

  it('renders inline bold + italic + code', () => {
    expect(markdownToHTML('**bold** and *italic* and `code`')).toBe(
      '<p><strong>bold</strong> and <em>italic</em> and <code>code</code></p>',
    );
  });

  it('handles a mixed heading + paragraph + list block', () => {
    const md = [
      '## 2.1 Impact pathway',
      '',
      'The project will drive uptake.',
      '',
      '- Stakeholder one',
      '- Stakeholder two',
    ].join('\n');
    expect(markdownToHTML(md)).toBe(
      '<h2>2.1 Impact pathway</h2>' +
        '<p>The project will drive uptake.</p>' +
        '<ul><li>Stakeholder one</li><li>Stakeholder two</li></ul>',
    );
  });

  it('flushes a paragraph before starting a list block', () => {
    expect(markdownToHTML('Intro line.\n- item')).toBe(
      '<p>Intro line.</p><ul><li>item</li></ul>',
    );
  });

  it('handles CRLF line endings', () => {
    expect(markdownToHTML('## H\r\n\r\nText.')).toBe('<h2>H</h2><p>Text.</p>');
  });
});
