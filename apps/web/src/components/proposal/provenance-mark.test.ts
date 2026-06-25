import { Editor } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import { describe, expect, it } from 'vitest';

import { ProvenanceMark } from './provenance-mark';

/**
 * Spin up a minimal TipTap editor (no DOM mount) so we can drive the
 * mark via commands + read back the rendered HTML. We piggyback on
 * StarterKit for Document/Paragraph/Text — same path the real editor
 * takes — so the test stays representative.
 */
function makeEditor() {
  return new Editor({
    extensions: [StarterKit, ProvenanceMark],
    content: '<p>Lorem ipsum dolor sit amet.</p>',
  });
}

describe('ProvenanceMark.renderHTML', () => {
  it('emits a span with the source-specific class + data attrs', () => {
    const editor = makeEditor();
    editor
      .chain()
      .selectAll()
      .setProvenance({
        source: 'ai-generated',
        sentenceId: 'sent-1',
        agentId: 'excellence_writer',
        llmModel: 'anthropic/claude-opus-4-7',
      })
      .run();
    const html = editor.getHTML();
    expect(html).toContain('class="provenance provenance--ai-generated"');
    expect(html).toContain('data-provenance-source="ai-generated"');
    expect(html).toContain('data-sentence-id="sent-1"');
    expect(html).toContain('data-agent-id="excellence_writer"');
    expect(html).toContain('data-llm-model="anthropic/claude-opus-4-7"');
    editor.destroy();
  });

  it('composes the hover title from source + agent + model', () => {
    const editor = makeEditor();
    editor
      .chain()
      .selectAll()
      .setProvenance({
        source: 'ai-edited',
        sentenceId: null,
        agentId: 'impact_writer',
        llmModel: 'anthropic/claude-sonnet-4-6',
      })
      .run();
    const html = editor.getHTML();
    expect(html).toContain(
      'title="Source: ai-edited · Agent: impact_writer · Model: anthropic/claude-sonnet-4-6"',
    );
    editor.destroy();
  });

  it('omits agent + model from the title when those attrs are null', () => {
    const editor = makeEditor();
    editor
      .chain()
      .selectAll()
      .setProvenance({
        source: 'human',
        sentenceId: 'sent-x',
        agentId: null,
        llmModel: null,
      })
      .run();
    const html = editor.getHTML();
    expect(html).toContain('title="Source: human"');
    expect(html).not.toContain('Agent: null');
  });

  it('round-trips through parseHTML — pasted content keeps its source', () => {
    const editor = makeEditor();
    editor.commands.setContent(
      '<p><span data-provenance-source="imported" data-sentence-id="paste-1">Pasted text.</span></p>',
    );
    const html = editor.getHTML();
    // The mark survived parsing + got re-rendered with our class hook.
    expect(html).toContain('class="provenance provenance--imported"');
    expect(html).toContain('data-sentence-id="paste-1"');
    editor.destroy();
  });

  it('unsetProvenance strips the mark + its data attrs', () => {
    const editor = makeEditor();
    editor.chain().selectAll().setProvenance({
      source: 'ai-generated',
      sentenceId: 'sent-1',
      agentId: 'excellence_writer',
      llmModel: null,
    }).run();
    editor.chain().selectAll().unsetProvenance().run();
    const html = editor.getHTML();
    expect(html).not.toContain('data-provenance-source');
    expect(html).not.toContain('class="provenance');
    editor.destroy();
  });
});
