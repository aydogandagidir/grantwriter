'use client';

import { Loader2, Sparkles } from 'lucide-react';
import { useTranslations } from 'next-intl';

import { Button } from '@/components/ui/button';
import { useToast } from '@/components/ui/use-toast';

import { useInlineRewrite } from './use-inline-rewrite';

import type { InlineCommand } from '@/lib/api/queries';
import type { Editor } from '@tiptap/react';
import type { ProposalSection } from './editor';

interface InlineAIMenuProps {
  editor: Editor;
  proposalId: string;
  section: ProposalSection;
}

const COMMANDS: Array<{ command: InlineCommand; labelKey: string }> = [
  { command: 'rewrite', labelKey: 'rewrite' },
  { command: 'shorter', labelKey: 'shorter' },
  { command: 'longer', labelKey: 'longer' },
  { command: 'translate_en', labelKey: 'translateEn' },
  { command: 'translate_tr', labelKey: 'translateTr' },
];

/**
 * Floating bubble menu that appears when the user selects text inside
 * a TipTap section editor. Exposes the five slash commands the
 * `inline_rewrite` LLM task supports.
 *
 * On click → calls /inline-edit, replaces the selection in-place,
 * surfaces a toast on error so the operator notices when the LLM
 * route falls back or the rate limit kicks in (10/60s).
 */
export function InlineAIMenu({ editor, proposalId, section }: InlineAIMenuProps) {
  const t = useTranslations('proposalEditor.ai');
  const { toast } = useToast();
  const { apply, isPending } = useInlineRewrite(proposalId, section);

  const run = async (command: InlineCommand) => {
    try {
      const result = await apply(editor, command);
      if (result === null) {
        // No selection — shouldn't happen since shouldShow gates this,
        // but be defensive in case the menu fires twice during a
        // transition.
        return;
      }
    } catch (err) {
      // ApiError has `.status` + `.message`; everything else falls
      // through as a generic string.
      const message =
        err && typeof err === 'object' && 'message' in err
          ? String((err as { message: unknown }).message)
          : 'unknown error';
      toast({
        title: t('errorTitle'),
        description: message,
        variant: 'destructive',
      });
    }
  };

  return (
    <div
      className="flex items-center gap-1 rounded-md border bg-popover p-1 shadow-md"
      data-testid="inline-ai-menu"
      role="toolbar"
      aria-label={t('toolbarLabel')}
    >
      <Sparkles className="ml-1 h-3.5 w-3.5 text-primary" aria-hidden="true" />
      {COMMANDS.map(({ command, labelKey }) => (
        <Button
          key={command}
          variant="ghost"
          size="sm"
          disabled={isPending}
          onClick={() => run(command)}
          data-testid={`inline-ai-${command}`}
          className="h-7 px-2 text-xs"
        >
          {isPending ? (
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
          ) : null}
          {t(labelKey)}
        </Button>
      ))}
    </div>
  );
}
