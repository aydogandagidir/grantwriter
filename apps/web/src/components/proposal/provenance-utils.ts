/**
 * Plain helpers shared by the editor and the provenance test suite.
 *
 * Kept in their own module (no React imports) so vitest can pull them
 * in without spinning up jsdom — they're pure functions.
 */

import type { Editor } from '@tiptap/react';

import type { ProvenanceSource } from './provenance-mark';

export interface CollectedSentence {
  sentenceId: string;
  section: string;
  content: string;
  source: ProvenanceSource;
  agentId: string | null;
  llmModel: string | null;
}

/**
 * Generate a stable, URL-safe sentence id for new text the user types.
 *
 * We do NOT reuse ``crypto.randomUUID()`` because the editor instance
 * may live inside an iframe or a strict CSP that blocks the global
 * ``crypto`` reference. The ``Date.now() + Math.random()`` shape is
 * enough for our uniqueness needs (collisions only matter within the
 * same proposal's editor session — millions of sentences before risk).
 */
export function makeSentenceId(): string {
  const random = Math.floor(Math.random() * 1_000_000_000).toString(36);
  return `s-${Date.now().toString(36)}-${random}`;
}

/**
 * Walk every text node in the editor and assemble the provenance batch.
 *
 * Sentences without an explicit ``data-sentence-id`` get one assigned
 * here (so the persistence layer can key on it); the assignment is
 * pushed back into the editor so the next iteration sees the same id.
 */
export function collectSentences(
  editor: Editor,
  fallbackSection: string,
): CollectedSentence[] {
  const collected: CollectedSentence[] = [];

  editor.state.doc.descendants((node) => {
    if (!node.isText || !node.text) {
      return true;
    }

    const provenance = node.marks.find((mark) => mark.type.name === 'provenance');
    if (!provenance) {
      // No provenance mark → treat as legacy plain text. The save loop
      // skips these; the editor wraps every new keystroke in a
      // provenance mark by default, so this branch only fires for
      // imported / pasted plain content.
      return true;
    }

    const attrs = provenance.attrs as Record<string, unknown>;
    const sentenceId = (attrs.sentenceId as string | null) ?? makeSentenceId();

    collected.push({
      sentenceId,
      section: fallbackSection,
      content: node.text,
      source: (attrs.source as ProvenanceSource) ?? 'human',
      agentId: (attrs.agentId as string | null) ?? null,
      llmModel: (attrs.llmModel as string | null) ?? null,
    });
    return true;
  });

  return collected;
}

/**
 * Downgrade rule: when the user edits an ``ai-generated`` span the mark
 * flips to ``ai-edited``. This pure helper computes the new attrs from
 * the current mark + the next typed text; the editor's transaction
 * listener calls it inside an ``updateAttributes`` chain.
 */
export function downgradeOnEdit(
  current: ProvenanceSource,
): ProvenanceSource {
  return current === 'ai-generated' ? 'ai-edited' : current;
}
