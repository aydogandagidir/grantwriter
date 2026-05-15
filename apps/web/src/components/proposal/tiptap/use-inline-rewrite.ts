'use client';

import { useCallback } from 'react';

import {
  type InlineCommand,
  type InlineEditResponse,
  useInlineEdit,
} from '@/lib/api/queries';

import type { Editor } from '@tiptap/react';

/**
 * Wraps the `useInlineEdit` mutation in a one-call helper that:
 *
 *   1. Reads the current selection from the TipTap editor.
 *   2. Builds the (selection, context_before, context_after) tuple the
 *      backend expects (≤ 500 chars each side).
 *   3. Posts the slash command to `/inline-edit`.
 *   4. Replaces the original selection with the response text and
 *      tags the new range with `data-ai="<command>"` so a downstream
 *      provenance extension can colour-code it.
 *
 * The hook returns the underlying mutation so callers can read
 * `isPending`, `error`, and the last response (the cost / model show
 * up in the AI disclosure surface).
 *
 * Section heuristic: callers pass the section label
 * (`'excellence' | 'impact' | 'implementation' | 'other'`) so the
 * server can register-match. The editor doesn't know what section it
 * is rendering — that's the parent component's job.
 */
export function useInlineRewrite(
  proposalId: string,
  section: 'excellence' | 'impact' | 'implementation' | 'other',
) {
  const mutation = useInlineEdit(proposalId);

  const apply = useCallback(
    async (editor: Editor, command: InlineCommand): Promise<InlineEditResponse | null> => {
      const { from, to } = editor.state.selection;
      if (from === to) return null;

      const selectionText = editor.state.doc.textBetween(from, to, '\n');
      if (!selectionText.trim()) return null;

      const docSize = editor.state.doc.content.size;
      // 500-char context windows — same cap the backend enforces. We
      // walk DOC positions, not raw string indices, so the slice
      // respects block boundaries.
      const beforeFrom = Math.max(0, from - 500);
      const afterTo = Math.min(docSize, to + 500);
      const contextBefore = editor.state.doc.textBetween(beforeFrom, from, '\n');
      const contextAfter = editor.state.doc.textBetween(to, afterTo, '\n');

      const response = await mutation.mutateAsync({
        command,
        section,
        selection_text: selectionText,
        context_before: contextBefore,
        context_after: contextAfter,
      });

      // Replace the selected range with the new text. We use
      // `insertContentAt` rather than `insertContent` so the cursor
      // lands at the end of the inserted text, not wherever the user
      // last clicked. The chain auto-closes the txn (single undo step).
      editor
        .chain()
        .focus()
        .deleteRange({ from, to })
        .insertContentAt(from, response.replacement_text)
        .run();

      return response;
    },
    [mutation, section],
  );

  return {
    apply,
    isPending: mutation.isPending,
    error: mutation.error,
    lastResponse: mutation.data,
  };
}
