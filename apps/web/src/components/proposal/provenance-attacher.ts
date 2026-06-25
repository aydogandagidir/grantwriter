/**
 * Markdown → TipTap doc reconstruction with provenance overlay.
 *
 * The saga (``src/orchestrator/provenance_recorder.py``) splits each
 * writer's markdown into sentences + records one ``proposal_provenance``
 * row per sentence, source = ``ai-generated``. When the user opens the
 * editor we re-attach those marks to the persisted draft so the wavy
 * underlines from ``provenance-mark.ts`` actually surface.
 *
 * Strategy:
 * 1. Split the markdown into sentences using the same heuristics as
 *    the Python splitter (heading lines / bullet lines / terminal
 *    punctuation regex).
 * 2. Match each sentence's text to a provenance item by exact content
 *    equality — same splitter on both sides means the text strings
 *    line up. Unmatched sentences fall back to ``human`` with no
 *    agent / model attribution.
 * 3. Build the TipTap JSON doc structure (doc → paragraph → text-with-marks)
 *    so the editor's ``setContent`` lands the marks in one transaction.
 *
 * Pure & testable: no TipTap runtime imports here, just the JSON
 * shape — vitest exercises it without spinning the editor up.
 */

import type { ProvenanceItem } from '@bluedev/shared-types';

import type { ProvenanceSource } from './provenance-mark';

// Regex parity with the Python recorder's ``_SENTENCE_TERMINATOR``.
// Splits on terminal punctuation followed by whitespace + uppercase
// or digit start. TR diacritics are kept in the lookahead so the
// "İlk cümle. İkinci cümle." pattern segments correctly.
const SENTENCE_TERMINATOR = /(?<=[.!?])\s+(?=[A-ZĞÜŞİÖÇ0-9])/;
const WHITESPACE = /\s+/g;

function normalise(text: string): string {
  return text.replace(WHITESPACE, ' ').trim();
}

function isBulletLine(line: string): boolean {
  const stripped = line.replace(/^\s+/, '');
  return (
    stripped.startsWith('- ') ||
    stripped.startsWith('* ') ||
    stripped.startsWith('+ ')
  );
}

/**
 * Split markdown into a flat list of sentences in document order. The
 * paragraph / heading / bullet boundaries are preserved as separate
 * sentences but the structural metadata is lost — callers that need
 * to rebuild the paragraph layout consult :func:`splitIntoBlocks`.
 */
export function splitIntoSentences(markdown: string): string[] {
  if (!markdown) return [];
  const paragraphs = markdown
    .split(/\n\n+/)
    .map((p) => p.trim())
    .filter((p) => p.length > 0);
  const out: string[] = [];
  for (const paragraph of paragraphs) {
    out.push(...sentencesInParagraph(paragraph));
  }
  return out;
}

function sentencesInParagraph(paragraph: string): string[] {
  if (paragraph.trimStart().startsWith('#')) {
    return [normalise(paragraph)];
  }
  const lines = paragraph.split('\n');
  if (lines.every(isBulletLine)) {
    return lines
      .map((line) => normalise(line.replace(/^[\s\-*+]+/, '')))
      .filter((s) => s.length > 0);
  }
  const joined = lines.map((line) => line.trim()).join(' ');
  return joined
    .split(SENTENCE_TERMINATOR)
    .map((part) => normalise(part))
    .filter((s) => s.length > 0);
}

/**
 * The TipTap JSON shape we'll feed into ``editor.commands.setContent``.
 * Keep the type narrow (just what we emit) — TipTap's own ``JSONContent``
 * is broader and would require importing the editor package here.
 */
export interface TipTapNode {
  type: string;
  attrs?: Record<string, unknown>;
  content?: TipTapNode[];
  text?: string;
  marks?: TipTapMark[];
}
export interface TipTapMark {
  type: string;
  attrs?: Record<string, unknown>;
}

interface ParagraphBlock {
  /** Block kind — heading lines get rendered with a ``heading`` node. */
  kind: 'heading' | 'paragraph' | 'bullet_list';
  /** Heading levels 1-3 are supported by StarterKit; rest default to 2. */
  headingLevel?: number;
  /**
   * Display text in document order. For ``heading`` the leading
   * ``#`` is stripped; for ``bullet_list`` each item is one entry.
   */
  sentences: string[];
  /**
   * Lookup keys parallel to ``sentences`` — same length, same order —
   * but matching the form the saga's splitter would have saved. For
   * headings that means we keep the ``#`` so ``items[].content`` lines
   * up; for everything else this mirrors ``sentences``.
   */
  matchKeys: string[];
}

function classifyParagraph(paragraph: string): ParagraphBlock {
  const trimmed = paragraph.trimStart();
  if (trimmed.startsWith('#')) {
    const match = trimmed.match(/^(#{1,3})\s+(.*)$/);
    const level = match?.[1] ? match[1].length : 2;
    const display = match?.[2]
      ? normalise(match[2])
      : normalise(trimmed.replace(/^#+\s*/, ''));
    const matchKey = normalise(trimmed);
    return {
      kind: 'heading',
      headingLevel: level,
      sentences: [display],
      matchKeys: [matchKey],
    };
  }
  const lines = paragraph.split('\n');
  if (lines.every(isBulletLine)) {
    const items = lines
      .map((line) => normalise(line.replace(/^[\s\-*+]+/, '')))
      .filter((s) => s.length > 0);
    return { kind: 'bullet_list', sentences: items, matchKeys: items };
  }
  const sentences = sentencesInParagraph(paragraph);
  return { kind: 'paragraph', sentences, matchKeys: sentences };
}

function splitIntoBlocks(markdown: string): ParagraphBlock[] {
  if (!markdown) return [];
  const paragraphs = markdown
    .split(/\n\n+/)
    .map((p) => p.trim())
    .filter((p) => p.length > 0);
  return paragraphs.map(classifyParagraph);
}

// ── Matching + doc build ─────────────────────────────────────────────────

interface MatchOptions {
  /**
   * Only items whose ``section`` matches this name are eligible. The
   * editor instance is always section-scoped, so this avoids attaching
   * an Impact sentence to an Excellence paragraph.
   */
  section: string;
}

/**
 * Build a sentence → item lookup. Multiple items can map to the same
 * sentence text (rare, but possible after a saga rerun); we keep the
 * first one + log the rest for the dev console.
 */
function indexItemsByContent(
  items: ProvenanceItem[],
  options: MatchOptions,
): Map<string, ProvenanceItem> {
  const lookup = new Map<string, ProvenanceItem>();
  for (const item of items) {
    if (item.section !== options.section) continue;
    const key = normalise(item.content);
    if (lookup.has(key)) continue;
    lookup.set(key, item);
  }
  return lookup;
}

function markForItem(item: ProvenanceItem | null): TipTapMark | null {
  if (!item) return null;
  const source = (item.source as ProvenanceSource) ?? 'ai-generated';
  return {
    type: 'provenance',
    attrs: {
      source,
      sentenceId: item.sentence_id,
      agentId: item.agent_id,
      llmModel: item.llm_model,
    },
  };
}

function textNode(text: string, mark: TipTapMark | null): TipTapNode {
  if (mark === null) return { type: 'text', text };
  return { type: 'text', text, marks: [mark] };
}

function paragraphNode(children: TipTapNode[]): TipTapNode {
  return { type: 'paragraph', content: children };
}

function headingNode(level: number, children: TipTapNode[]): TipTapNode {
  return {
    type: 'heading',
    attrs: { level },
    content: children,
  };
}

function bulletItem(child: TipTapNode): TipTapNode {
  return {
    type: 'listItem',
    content: [paragraphNode([child])],
  };
}

/**
 * Top-level reconstruction. Returns a fully formed TipTap doc node
 * the editor can pass straight to ``setContent``.
 */
export function attachProvenance(
  markdown: string,
  items: ProvenanceItem[],
  options: MatchOptions,
): TipTapNode {
  const blocks = splitIntoBlocks(markdown);
  const lookup = indexItemsByContent(items, options);
  const content: TipTapNode[] = [];

  for (const block of blocks) {
    if (block.kind === 'heading') {
      const sentence = block.sentences[0] ?? '';
      const key = block.matchKeys[0] ?? sentence;
      const mark = markForItem(lookup.get(key) ?? null);
      content.push(headingNode(block.headingLevel ?? 2, [textNode(sentence, mark)]));
      continue;
    }
    if (block.kind === 'bullet_list') {
      content.push({
        type: 'bulletList',
        content: block.sentences.map((sentence, idx) => {
          const key = block.matchKeys[idx] ?? sentence;
          const mark = markForItem(lookup.get(key) ?? null);
          return bulletItem(textNode(sentence, mark));
        }),
      });
      continue;
    }
    // paragraph — multiple sentences joined by a single space, each
    // text node carrying its own provenance mark.
    const children: TipTapNode[] = [];
    block.sentences.forEach((sentence, idx) => {
      if (idx > 0) {
        children.push({ type: 'text', text: ' ' });
      }
      const key = block.matchKeys[idx] ?? sentence;
      const mark = markForItem(lookup.get(key) ?? null);
      children.push(textNode(sentence, mark));
    });
    if (children.length > 0) content.push(paragraphNode(children));
  }

  return { type: 'doc', content };
}

export { splitIntoBlocks };
