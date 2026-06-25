'use client';

/**
 * Real TipTap editor — replaces the long-standing placeholder card in
 * :file:`editor-shell.tsx`. The editor is intentionally lean for the
 * first iteration:
 *
 *  - Starter kit gives us bold / italic / headings / lists / paragraphs.
 *  - The custom :mod:`ProvenanceMark` wraps every keystroke in a
 *    span with ``source = 'human'`` by default. Agent injections set
 *    ``source = 'ai-generated'`` (FE side, wired in a follow-up
 *    sprint).
 *  - A transaction listener downgrades ``ai-generated → ai-edited``
 *    whenever the user types inside a previously-AI span so the
 *    disclosure counts stay accurate.
 *  - A debounced save loop ships the dirty sentences to
 *    ``POST /provenance``. We persist the *structured* metadata, not
 *    the HTML — proposal_versions handles the editor body.
 *
 * The component takes ``proposalId`` + ``section`` props so the same
 * shell can host one editor per HE section without prop drilling
 * deeper down.
 */

import Placeholder from '@tiptap/extension-placeholder';
import StarterKit from '@tiptap/starter-kit';
import { EditorContent, useEditor } from '@tiptap/react';
import {
  Bold,
  Heading1,
  Heading2,
  Italic,
  List,
  ListOrdered,
} from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useCallback, useEffect, useMemo, useRef } from 'react';

import { useToast } from '@/components/ui/use-toast';
import { useProvenanceItems, useUpsertProvenance } from '@/lib/api/queries';

import { attachProvenance } from './provenance-attacher';
import { ProvenanceMark } from './provenance-mark';
import { collectSentences } from './provenance-utils';

const DEBOUNCE_MS = 1500;

interface ProposalEditorProps {
  proposalId: string;
  /**
   * Which proposal section this editor instance owns — the value flows
   * into every ``proposal_provenance`` row so the AI disclosure stats
   * can break down by section without parsing the HTML.
   */
  section: string;
  initialContent?: string;
  /**
   * Raw markdown for the section. When provided, the editor pre-fills
   * its document by splitting the markdown into sentences + matching
   * them against the saga's provenance rows, so AI-generated text
   * surfaces with the wavy underline from ``provenance-mark`` instead
   * of looking like the user wrote it. Source of truth for the markdown
   * is ``proposals.draft.<section>_md`` — wiring that fetch belongs to
   * the editor-shell, which decides whether the saga has run.
   */
  initialMarkdown?: string;
  placeholder?: string;
}

export function ProposalEditor({
  proposalId,
  section,
  initialContent,
  initialMarkdown,
  placeholder,
}: ProposalEditorProps) {
  const t = useTranslations('editor');
  const { toast } = useToast();
  const upsert = useUpsertProvenance(proposalId);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Items load lazily once the editor is up; the pre-fill effect waits
  // for them so a saga-completed proposal lights up with marks instead
  // of plain prose.
  const items = useProvenanceItems(proposalId, { section });
  // Guard so we only attach once per mount — re-attaching on every
  // items refetch would clobber the user's in-flight edits.
  const attachedRef = useRef(false);

  const editor = useEditor({
    immediatelyRender: false,
    extensions: [
      StarterKit.configure({
        // Heading depth — Sprint 3 templates only use h1/h2; deeper
        // levels are intentionally disabled to keep the export
        // grammar tight.
        heading: { levels: [1, 2] },
      }),
      ProvenanceMark.configure({ defaultSource: 'human' }),
      Placeholder.configure({ placeholder: placeholder ?? t('placeholder') }),
    ],
    content: initialContent ?? '',
    editorProps: {
      attributes: {
        // Tailwind ``prose`` keeps the long-form text readable on
        // wide screens; ``focus:outline-none`` hides the default
        // browser ring (we draw our own focus state on the wrapper).
        class:
          'prose prose-sm max-w-none min-h-[280px] focus:outline-none px-3 py-2',
      },
    },
  });

  // Sentence-level downgrade: when the user types inside an existing
  // ``ai-generated`` mark, swap the mark's ``source`` attribute to
  // ``ai-edited`` so the disclosure counts stop attributing the edited
  // text to the agent.
  useEffect(() => {
    if (!editor) {
      return;
    }
    const onTransaction = ({
      transaction,
    }: {
      transaction: { docChanged: boolean; steps: unknown[] };
    }) => {
      if (!transaction.docChanged) {
        return;
      }
      transaction.steps.forEach((stepRaw: unknown) => {
        const step = stepRaw as { from?: unknown; to?: unknown };
        if (typeof step.from !== 'number' || typeof step.to !== 'number') {
          return;
        }
        editor.state.doc.nodesBetween(step.from, step.to, (node) => {
          const provenance = node.marks?.find?.(
            (mark: { type: { name: string } }) => mark.type.name === 'provenance',
          );
          if (provenance && provenance.attrs.source === 'ai-generated') {
            editor.commands.updateAttributes('provenance', {
              source: 'ai-edited',
            });
          }
        });
      });
    };
    editor.on('transaction', onTransaction);
    return () => {
      editor.off('transaction', onTransaction);
    };
  }, [editor]);

  // Pre-fill: when both the editor instance + the provenance items are
  // ready, build the marked TipTap doc from ``initialMarkdown`` and
  // hand it to ``setContent``. The transaction listener flips
  // ``ai-generated`` → ``ai-edited`` the moment the user touches it,
  // so the marks degrade gracefully on edit.
  useEffect(() => {
    if (!editor || attachedRef.current) return;
    if (!initialMarkdown) return;
    if (items.isPending) return; // wait for the items round-trip
    const itemsData = items.data?.items ?? [];
    const doc = attachProvenance(initialMarkdown, itemsData, { section });
    // ``emitUpdate: false`` keeps the prefill out of the dirty-buffer —
    // we don't want the debounce timer to re-POST the saga's own rows.
    editor.commands.setContent(doc, false);
    attachedRef.current = true;
  }, [editor, initialMarkdown, items.isPending, items.data, section]);

  // Debounced provenance save loop. Every doc change resets the timer;
  // when it fires we collect + ship. Failed saves toast once so the
  // user knows their edits aren't durable, but the editor keeps going.
  const flush = useCallback(() => {
    if (!editor) {
      return;
    }
    const sentences = collectSentences(editor, section);
    if (sentences.length === 0) {
      return;
    }
    upsert.mutate(
      {
        sentences: sentences.map((s) => ({
          sentence_id: s.sentenceId,
          section: s.section,
          content: s.content,
          source: s.source,
          agent_id: s.agentId,
          llm_model: s.llmModel,
        })),
      },
      {
        onError: (err: unknown) => {
          toast({
            variant: 'destructive',
            title: t('saveFailed'),
            description: (err as Error).message,
          });
        },
      },
    );
  }, [editor, section, toast, upsert, t]);

  useEffect(() => {
    if (!editor) {
      return;
    }
    const scheduleSave = () => {
      if (saveTimer.current) {
        clearTimeout(saveTimer.current);
      }
      saveTimer.current = setTimeout(flush, DEBOUNCE_MS);
    };
    editor.on('update', scheduleSave);
    return () => {
      editor.off('update', scheduleSave);
      if (saveTimer.current) {
        clearTimeout(saveTimer.current);
        // Section switches (key={section} → unmount) would otherwise
        // lose any edits still inside the debounce window. Fire the
        // upsert synchronously — the mutation is fire-and-forget and
        // React Query keeps it alive after unmount.
        flush();
      }
    };
  }, [editor, flush]);

  const toolbarItems = useMemo(
    () => [
      {
        key: 'bold' as const,
        icon: Bold,
        label: t('bold'),
        action: () => editor?.chain().focus().toggleBold().run(),
        active: () => editor?.isActive('bold') ?? false,
      },
      {
        key: 'italic' as const,
        icon: Italic,
        label: t('italic'),
        action: () => editor?.chain().focus().toggleItalic().run(),
        active: () => editor?.isActive('italic') ?? false,
      },
      {
        key: 'h1' as const,
        icon: Heading1,
        label: t('h1'),
        action: () => editor?.chain().focus().toggleHeading({ level: 1 }).run(),
        active: () => editor?.isActive('heading', { level: 1 }) ?? false,
      },
      {
        key: 'h2' as const,
        icon: Heading2,
        label: t('h2'),
        action: () => editor?.chain().focus().toggleHeading({ level: 2 }).run(),
        active: () => editor?.isActive('heading', { level: 2 }) ?? false,
      },
      {
        key: 'bullet' as const,
        icon: List,
        label: t('bullet'),
        action: () => editor?.chain().focus().toggleBulletList().run(),
        active: () => editor?.isActive('bulletList') ?? false,
      },
      {
        key: 'ordered' as const,
        icon: ListOrdered,
        label: t('ordered'),
        action: () => editor?.chain().focus().toggleOrderedList().run(),
        active: () => editor?.isActive('orderedList') ?? false,
      },
    ],
    [editor, t],
  );

  return (
    <div
      className="rounded-md border bg-background focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-2"
      aria-label={t('regionLabel')}
    >
      <div className="flex items-center gap-1 border-b px-2 py-1">
        {toolbarItems.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={item.action}
            aria-pressed={item.active()}
            aria-label={item.label}
            title={item.label}
            className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground aria-pressed:bg-muted aria-pressed:text-foreground"
          >
            <item.icon className="h-4 w-4" />
          </button>
        ))}
        <span
          className="ml-auto text-xs text-muted-foreground"
          aria-live="polite"
        >
          {upsert.isPending ? t('saving') : t('saved')}
        </span>
      </div>
      <EditorContent editor={editor} />
    </div>
  );
}
