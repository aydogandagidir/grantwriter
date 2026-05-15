/**
 * Minimal markdown → HTML conversion for the TipTap editor's
 * initial-content path.
 *
 * The writer agents produce markdown (headings, bullets, paragraphs)
 * and we need to surface it inside TipTap on first render. A full
 * remark/marked dep would handle every edge case, but Faz 4 only
 * needs to cover the handful of constructs the writer prompts emit:
 *
 *   - `## H2` / `### H3` / `#### H4` — section sub-headings
 *   - `- item` / `* item` — unordered lists
 *   - `1. item` — ordered lists
 *   - `**bold**`, `*italic*`, `` `code` `` — basic inlines
 *   - blank-line-separated paragraphs
 *
 * Anything fancier round-trips as plain text — acceptable for v1.
 *
 * Safety: every text fragment is HTML-escaped before being wrapped
 * in tags, so a writer agent that emits `<script>` markup can't
 * inject HTML through this path.
 */

const ESCAPE: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
};

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => ESCAPE[c] ?? c);
}

function renderInlines(text: string): string {
  // Order matters: code first (preserves literal chars inside), then
  // bold/italic. We don't try to support nested bold-in-italic or
  // similar — writer prompts don't produce them.
  let s = escapeHtml(text);
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
  return s;
}

interface ListBlock {
  ordered: boolean;
  items: string[];
}

function flushList(out: string[], list: ListBlock | null): void {
  if (!list) return;
  const tag = list.ordered ? 'ol' : 'ul';
  out.push(`<${tag}>`);
  for (const item of list.items) {
    out.push(`<li>${renderInlines(item)}</li>`);
  }
  out.push(`</${tag}>`);
}

/**
 * Convert a markdown source string into HTML suitable for TipTap's
 * `content` prop. Returns an empty string for null/empty input.
 */
export function markdownToHTML(markdown: string | null | undefined): string {
  if (!markdown) return '';
  const lines = markdown.replace(/\r\n/g, '\n').split('\n');
  const out: string[] = [];
  let paragraph: string[] = [];
  let list: ListBlock | null = null;

  const flushParagraph = (): void => {
    if (paragraph.length === 0) return;
    out.push(`<p>${renderInlines(paragraph.join(' '))}</p>`);
    paragraph = [];
  };

  for (const raw of lines) {
    const line = raw.trim();

    if (!line) {
      flushParagraph();
      flushList(out, list);
      list = null;
      continue;
    }

    const headingMatch = line.match(/^(#{2,4})\s+(.+)$/);
    if (headingMatch) {
      flushParagraph();
      flushList(out, list);
      list = null;
      const level = headingMatch[1]!.length;
      out.push(`<h${level}>${renderInlines(headingMatch[2]!)}</h${level}>`);
      continue;
    }

    const ulMatch = line.match(/^[-*]\s+(.+)$/);
    if (ulMatch) {
      flushParagraph();
      if (!list || list.ordered) {
        flushList(out, list);
        list = { ordered: false, items: [] };
      }
      list.items.push(ulMatch[1]!);
      continue;
    }

    const olMatch = line.match(/^\d+\.\s+(.+)$/);
    if (olMatch) {
      flushParagraph();
      if (!list || !list.ordered) {
        flushList(out, list);
        list = { ordered: true, items: [] };
      }
      list.items.push(olMatch[1]!);
      continue;
    }

    // Regular line — accumulate into current paragraph. We trim
    // leading whitespace but keep the content as-is for renderInlines.
    paragraph.push(line);
  }

  flushParagraph();
  flushList(out, list);
  return out.join('');
}
