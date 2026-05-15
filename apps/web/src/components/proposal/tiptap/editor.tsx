'use client';

import { BubbleMenu, EditorContent, useEditor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Placeholder from '@tiptap/extension-placeholder';
import { useEffect } from 'react';

import { InlineAIMenu } from './inline-ai-menu';
import { markdownToHTML } from './markdown';

export type ProposalSection = 'excellence' | 'impact' | 'implementation' | 'other';

export interface ProposalSectionEditorProps {
  proposalId: string;
  section: ProposalSection;
  /** Markdown body — converted to HTML for TipTap on first render. */
  initialMarkdown: string;
  /** Placeholder shown when the section is empty. */
  placeholder?: string;
  /** Fires on every content change (debounced upstream if needed). */
  onChange?: (html: string) => void;
  /** Visually disables the editor (still focusable for selection). */
  readOnly?: boolean;
}

/**
 * TipTap-backed WYSIWYG editor for a single proposal section.
 *
 * MVP scope (Faz 4):
 *   - StarterKit (bold, italic, lists, headings, code, blockquote).
 *   - Floating bubble menu when text is selected → AI slash commands.
 *   - Placeholder when empty.
 *
 * The editor is markdown-aware in one direction only: it accepts
 * markdown on initial render and re-renders TipTap's serialised HTML
 * on every change. The proposal-save path persists HTML; markdown
 * round-tripping is a Faz 4.1 follow-up (we'd need a remark/marked
 * dep to go HTML→MD reliably). For now the source-of-truth flips to
 * HTML once the user edits.
 */
export function ProposalSectionEditor({
  proposalId,
  section,
  initialMarkdown,
  placeholder,
  onChange,
  readOnly = false,
}: ProposalSectionEditorProps) {
  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        // ProseMirror's default heading levels are 1-6; proposals
        // never use H1 (the section title is rendered outside the
        // editor) and rarely need H5/H6.
        heading: { levels: [2, 3, 4] },
      }),
      Placeholder.configure({
        placeholder: placeholder ?? '',
        emptyEditorClass:
          'is-editor-empty before:content-[attr(data-placeholder)] before:text-muted-foreground before:float-left before:h-0 before:pointer-events-none',
      }),
    ],
    content: markdownToHTML(initialMarkdown),
    editable: !readOnly,
    // Avoid TipTap's "SSR is meaningless, do not render content on
    // server" warning by setting `immediatelyRender: false`. Next.js
    // 15's App Router pre-renders pages; the editor mounts on the
    // client.
    immediatelyRender: false,
    onUpdate: ({ editor: e }) => {
      onChange?.(e.getHTML());
    },
  });

  // Re-sync the editor when the source markdown changes (e.g. the
  // saga finishes and the parent passes fresh content). We compare
  // against the current HTML to avoid clobbering local edits.
  useEffect(() => {
    if (!editor) return;
    const incoming = markdownToHTML(initialMarkdown);
    if (incoming !== editor.getHTML()) {
      editor.commands.setContent(incoming, false);
    }
    // The proposalId+section are part of the key the parent uses to
    // remount this component when switching tabs — we don't depend on
    // them here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialMarkdown]);

  if (!editor) return null;

  return (
    <div className="relative" data-testid={`tiptap-section-${section}`}>
      <BubbleMenu
        editor={editor}
        tippyOptions={{ duration: 100, placement: 'top' }}
        shouldShow={({ editor: e, from, to }) => {
          // Only show the AI menu when the user has a non-trivial
          // selection — single-cursor states get the formatting
          // buttons (bold/italic) inline-only.
          if (readOnly) return false;
          if (from === to) return false;
          const text = e.state.doc.textBetween(from, to, '\n').trim();
          return text.length >= 3;
        }}
      >
        <InlineAIMenu editor={editor} proposalId={proposalId} section={section} />
      </BubbleMenu>

      <EditorContent
        editor={editor}
        className={
          'prose prose-sm max-w-none rounded-md border bg-card p-4 text-sm ' +
          'leading-relaxed focus:outline-none [&_p]:my-2 [&_h2]:mt-4 ' +
          '[&_h2]:mb-2 [&_h3]:mt-3 [&_h3]:mb-1.5 [&_ul]:list-disc ' +
          '[&_ol]:list-decimal [&_li]:ml-5 ' +
          (readOnly ? 'opacity-90' : '')
        }
      />
    </div>
  );
}
