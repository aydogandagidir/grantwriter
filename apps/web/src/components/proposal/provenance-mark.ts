/**
 * Custom TipTap mark carrying per-sentence provenance metadata.
 *
 * Every text node the user types or an agent inserts is wrapped in a
 * ``<span data-provenance-source="..." data-sentence-id="...">``. The
 * source attribute follows the docs/09 §2.2 vocabulary:
 *
 *  - ``human``         user typed the text directly
 *  - ``ai-generated``  came verbatim from an agent insertion
 *  - ``ai-edited``     was ``ai-generated`` until the user modified it
 *  - ``imported``      pasted in from the brief / external doc
 *  - ``rag-retrieved`` adapted from a RAG passage
 *
 * The transaction listener in :file:`proposal-editor.tsx` downgrades
 * ``ai-generated → ai-edited`` whenever the user edits inside an
 * AI-generated range, keeping the AI disclosure counts accurate.
 */

import { Mark, mergeAttributes, type CommandProps } from '@tiptap/core';

export type ProvenanceSource =
  | 'human'
  | 'ai-generated'
  | 'ai-edited'
  | 'imported'
  | 'rag-retrieved';

export interface ProvenanceMarkAttrs {
  source: ProvenanceSource;
  sentenceId: string | null;
  agentId: string | null;
  llmModel: string | null;
}

declare module '@tiptap/core' {
  // eslint-disable-next-line @typescript-eslint/consistent-type-definitions
  interface Commands<ReturnType> {
    provenance: {
      setProvenance: (attrs: ProvenanceMarkAttrs) => ReturnType;
      unsetProvenance: () => ReturnType;
    };
  }
}

/**
 * Use one mark with attributes rather than five marks (one per source).
 * Attribute-style means an edit + reset uses one ``updateAttributes``
 * call instead of a chained unset+set pair, keeping the editor's
 * undo history coherent.
 */
export const ProvenanceMark = Mark.create<{
  defaultSource: ProvenanceSource;
}>({
  name: 'provenance',

  // Marks can't be excluded from selection-spanning operations cleanly,
  // so we let them mix with the starter-kit's marks (bold, italic).
  inclusive: true,

  addOptions() {
    return {
      defaultSource: 'human',
    };
  },

  addAttributes() {
    return {
      source: {
        default: 'human',
        parseHTML: (element: HTMLElement) =>
          element.getAttribute('data-provenance-source') ?? 'human',
        renderHTML: (attrs: Record<string, unknown>) => ({
          'data-provenance-source': attrs.source as ProvenanceSource,
        }),
      },
      sentenceId: {
        default: null,
        parseHTML: (element: HTMLElement) =>
          element.getAttribute('data-sentence-id'),
        renderHTML: (attrs: Record<string, unknown>) =>
          attrs.sentenceId
            ? { 'data-sentence-id': attrs.sentenceId as string }
            : {},
      },
      agentId: {
        default: null,
        parseHTML: (element: HTMLElement) =>
          element.getAttribute('data-agent-id'),
        renderHTML: (attrs: Record<string, unknown>) =>
          attrs.agentId
            ? { 'data-agent-id': attrs.agentId as string }
            : {},
      },
      llmModel: {
        default: null,
        parseHTML: (element: HTMLElement) =>
          element.getAttribute('data-llm-model'),
        renderHTML: (attrs: Record<string, unknown>) =>
          attrs.llmModel
            ? { 'data-llm-model': attrs.llmModel as string }
            : {},
      },
    };
  },

  parseHTML() {
    return [
      {
        tag: 'span[data-provenance-source]',
      },
    ];
  },

  renderHTML({ HTMLAttributes }: { HTMLAttributes: Record<string, unknown> }) {
    // Compose a styling-friendly class + hover tooltip from the data
    // attrs collected above. The class hooks into CSS rules in
    // ``globals.css`` so each source gets a subtle visual treatment
    // (underline colour); the title attr is the native hover tooltip
    // — no Floating UI dep needed for the 80/20 case.
    const source = String(
      HTMLAttributes['data-provenance-source'] ?? 'human',
    ) as ProvenanceSource;
    const agentId = HTMLAttributes['data-agent-id'];
    const llmModel = HTMLAttributes['data-llm-model'];
    const titleParts = [
      `Source: ${source}`,
      agentId ? `Agent: ${String(agentId)}` : null,
      llmModel ? `Model: ${String(llmModel)}` : null,
    ].filter((part): part is string => part !== null);
    const styled = mergeAttributes(HTMLAttributes, {
      class: `provenance provenance--${source}`,
      title: titleParts.join(' · '),
    });
    return ['span', styled, 0];
  },

  addCommands() {
    return {
      setProvenance:
        (attrs: ProvenanceMarkAttrs) =>
        ({ commands }: CommandProps) =>
          commands.setMark(this.name, attrs),
      unsetProvenance:
        () =>
        ({ commands }: CommandProps) =>
          commands.unsetMark(this.name),
    };
  },
});
