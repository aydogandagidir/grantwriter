# 08 — Frontend Spesifikasyonu

## 1. Stack ve Yapı Kararı

Next.js 15 App Router'ı seçtik çünkü (a) Server Components ile bundle size düşüyor — bu uygulamada frontend ağır (TipTap editor 400KB+), (b) Vercel'de zero-config deploy, (c) Streaming ve Suspense ile SSE entegrasyonu native. Pages Router'ı reddettik — App Router artık stabil ve gelecek tüm özellikler oraya gidiyor.

State yönetimi için ikili yaklaşım: **server state TanStack Query'de**, **client state Zustand'da**. Redux Toolkit reddedildi — proje boyutuna göre overkill, boilerplate fazla. Zustand ~3KB, hook tabanlı, uygulamamızda 4-5 store yeterli (auth, editor, generation, ui).

Form yönetimi React Hook Form + Zod. Reasoning: Brief formları program-spesifik ve dinamik (BaseProgramModule.get_brief_schema'dan geliyor). RHF dinamik form rendering için en olgun çözüm; Zod validation server schema ile aynı (Pydantic ile uyumlu — `zod-to-pydantic` ya da paylaşılan TypeScript types).

Editör için TipTap seçtik. ProseMirror tabanlı, extensions yazımı kolay. Bizim için kritik 3 extension yazılacak: `AIAttribution` (provenance markers), `CitationMark` (renkli citation rozetleri), `ProvenanceMarker` (sentence-level metadata). Quill ve Slate değerlendirildi — Quill modüler değil, Slate API hala değişken.

i18n için **next-intl**. App Router native, server component destekli, statik render uyumlu. next-i18next App Router'da hala olgunlaşmadı.

---

## 2. Sayfa Hiyerarşisi

```
apps/web/src/app/
├── [locale]/                                # /tr/... veya /en/...
│   ├── (marketing)/                         # public, marketing
│   │   ├── page.tsx                         # / — ana sayfa
│   │   ├── pricing/page.tsx
│   │   ├── features/page.tsx
│   │   └── login/page.tsx
│   │
│   ├── (app)/                               # auth required, RLS active
│   │   ├── layout.tsx                       # Sidebar + topbar layout
│   │   ├── dashboard/page.tsx
│   │   ├── calls/
│   │   │   ├── page.tsx                     # Çağrı listesi (filtre + arama)
│   │   │   └── [id]/page.tsx                # Çağrı detayı + eligibility check
│   │   ├── proposals/
│   │   │   ├── page.tsx                     # Başvurularım listesi
│   │   │   ├── new/page.tsx                 # Yeni başvuru başlat (program seç)
│   │   │   └── [id]/
│   │   │       ├── layout.tsx               # Stepper layout (Brief→Edit→Validate→Export)
│   │   │       ├── brief/page.tsx           # Brief formu (program-spesifik)
│   │   │       ├── editor/page.tsx          # Ana editör (TipTap)
│   │   │       ├── compliance/page.tsx      # Validation & quality
│   │   │       └── export/page.tsx          # DOCX/PDF/XLSX
│   │   └── settings/
│   │       ├── page.tsx                     # Profil
│   │       ├── tenant/page.tsx              # Şirket ayarları (admin)
│   │       ├── members/page.tsx             # Ekip yönetimi
│   │       ├── llm-config/page.tsx          # BYOK
│   │       ├── billing/page.tsx             # Plan + ödeme
│   │       └── usage/page.tsx               # Kullanım raporu
│   │
│   └── api/
│       └── (Next.js API routes — proxy/edge functions only)
│
├── components/                              # paylaşılan
│   ├── ui/                                  # shadcn/ui
│   ├── editor/                              # TipTap özel
│   │   ├── Editor.tsx
│   │   ├── extensions/
│   │   │   ├── AIAttribution.ts
│   │   │   ├── CitationMark.ts
│   │   │   └── ProvenanceMarker.ts
│   │   ├── Toolbar.tsx
│   │   └── CitationSidebar.tsx
│   ├── brief-forms/
│   │   ├── DynamicBriefForm.tsx             # generic, schema-driven
│   │   └── fields/                          # field type renderers
│   ├── workflow/
│   │   ├── GenerationProgress.tsx           # SSE-driven progress UI
│   │   └── AgentStep.tsx
│   └── shared/
│       ├── Sidebar.tsx
│       ├── TopBar.tsx
│       └── ProposalStatusBadge.tsx
│
├── lib/
│   ├── api.ts                               # API client (typed via openapi-codegen)
│   ├── auth.ts                              # Supabase session helpers
│   ├── stores/                              # Zustand stores
│   │   ├── editorStore.ts
│   │   ├── generationStore.ts
│   │   └── uiStore.ts
│   └── utils/
│
├── i18n/
│   ├── config.ts
│   └── messages/
│       ├── tr.json
│       └── en.json
│
└── middleware.ts                            # locale, auth, RLS
```

---

## 3. Kritik Sayfa Detayları

### 3.1 `/calls` — Çağrı Listesi

Server Component (SSR). Filtre paneli sol, sonuç listesi sağda. Filtre değiştiğinde URL search params güncellenir, sayfa yeniden render olur. **Niye Server Component?** Çağrı listesi (50-200 kayıt) statik gibi davranıyor, SEO'ya da yardımı oluyor (marketing'e eklenirse).

Filtreler: programme (multi-select), deadline range, budget range, TRL range, search (full-text). Backend `GET /api/v1/calls?...` cursor-based pagination.

```tsx
// app/[locale]/(app)/calls/page.tsx
export default async function CallsPage({ searchParams }: PageProps) {
  const filters = parseFilters(searchParams);
  const calls = await api.calls.list(filters);  // SSR fetch
  return (
    <div className="grid grid-cols-12 gap-6">
      <CallFilters className="col-span-3" defaultValues={filters} />
      <CallList className="col-span-9" calls={calls} />
    </div>
  );
}
```

### 3.2 `/proposals/[id]/brief` — Brief Formu

Client Component. Program-spesifik form. Schema backend'den `GET /api/v1/programmes/{id}/brief-schema` ile gelir, `DynamicBriefForm` component'i bu schema'yı render eder.

Auto-save: debounced (1.5s), her değişiklik PATCH request. Optimistic UI — kullanıcı input ettiği anda görür, server response sonrası status iconu değişir.

"Generate Draft" butonu enabled olur eğer:
- Tüm `required` alanlar dolu
- Brief minimum kelime sayısını geçti (her field için backend validation)
- Tenant'ın aylık quota'sı dolmadı

Quota dolmuşsa buton devre dışı, tooltip'te "Pro plana yükseltin" linki.

### 3.3 `/proposals/[id]/editor` — Ana Editör

En karmaşık sayfa. Üç kolon:

```
┌─────────────────────────────────────────────────────────────────────┐
│ TopBar: [← Brief] [Acronym] [Stepper] [Save status] [Export]       │
├──────────────┬──────────────────────────────────┬───────────────────┤
│              │                                  │                   │
│ Section      │   TipTap Editor (Markdown)       │ Citations Sidebar │
│ Navigator    │                                  │                   │
│              │   - Provenance markers           │ - All citations   │
│ □ Excellence │     (color-coded background)     │ - Verification    │
│   ├ 1.1 ...  │   - Citation badges inline       │   status          │
│   ├ 1.2 ...  │   - AI agent attribution         │ - "Add citation"  │
│ □ Impact     │     gutter icon                  │ - DOI lookup      │
│ □ Implement  │   - Auto-save indicator          │                   │
│              │                                  │ Word count: 4,210 │
│ Page count:  │                                  │ Page count: ~11   │
│ ~38 / 45     │                                  │                   │
└──────────────┴──────────────────────────────────┴───────────────────┘
```

Editör state Zustand'da (`editorStore`). TipTap content değişikliği → debounced save (1.5s). Konflict'ler için optimistic concurrency: backend `updated_at` timestamp döner, client güncel mi kontrol eder, eski ise merge UI gösterir.

#### Provenance gösterimi

Her cümle TipTap'te `<span data-provenance="ai-generated" data-agent="excellence_writer">...</span>` olarak render edilir. CSS:
- `[data-provenance="human"]` — varsayılan (siyah, arkaplan yok)
- `[data-provenance="ai-generated"]` — sol kenarda mavi şerit (3px)
- `[data-provenance="ai-edited"]` — sol kenarda turuncu şerit
- `[data-provenance="rag-retrieved"]` — sol kenarda mor şerit

Görsel olarak yazar AI hangi cümleyi yazdığını anında görür, manuel düzeltme yaparsa otomatik `ai-edited` olur.

#### Citation rozetleri

Inline rozetler `[Smith 2023]` görünümünde, renk verification status'a göre:
- Yeşil ✓ — verified
- Sarı ⚠ — partial_match (kullanıcı incelemeli)
- Kırmızı ✗ — fabricated (export'u bloklar)
- Gri ⋯ — verifying (animasyon)

Rozete hover → tooltip: title, authors, year, match score, "Open DOI" linki.
Tıklama → sağ sidebar'da o citation'a scroll.

#### SSE generation progress

İlk taslak üretilirken (`/proposals/[id]/editor` ilk açıldığında status `generating`), editör read-only, üzerine overlay:

```tsx
function GenerationProgress({ proposalId }) {
  const { agents, currentAgent, completed } = useGenerationStream(proposalId);
  return (
    <Overlay>
      <Stepper>
        {AGENT_FLOW.map(agent => (
          <AgentStep
            key={agent.id}
            status={statusFor(agent, agents)}
            duration={agent.estimated_seconds}
            label={agent.name_tr}
          />
        ))}
      </Stepper>
      <p>Tahmini süre kaldı: {estimatedRemaining}</p>
    </Overlay>
  );
}
```

`useGenerationStream` hook'u `EventSource` wrapper'ı, reconnection ve last-event-id yönetimi yapar. Tab kapansa bile worker arka planda çalışır, kullanıcı geri döndüğünde stream'i resumelar.

### 3.4 `/proposals/[id]/compliance` — Validation

Validation report:

```
┌──────────────────────────────────────────────────────────────┐
│ Compliance Report                                            │
├──────────────────────────────────────────────────────────────┤
│ ❌ 1 blocker                                                 │
│   • Page limit exceeded (Excellence: 12 / 10 pages)          │
│     [Trim suggestions →]                                     │
│                                                              │
│ ⚠ 3 warnings                                                 │
│   • Distinctiveness 0.91 (similar to GREENBOT)               │
│   • Gender dimension not addressed                           │
│   • DNSH not mentioned                                       │
│                                                              │
│ Citations: 44 verified, 2 partial, 1 fabricated ❌           │
│   [Review fabricated →]                                      │
│                                                              │
│ AI Disclosure: Auto-generated ✓ [Preview →]                  │
│                                                              │
│ [Re-validate] [Export blocked until issues resolved]         │
└──────────────────────────────────────────────────────────────┘
```

"Trim suggestions" tıklandığında editöre dönüş + AI agent ("ImpactCondenser" Faz 2, MVP'de manuel) çağrısı.

### 3.5 `/proposals/[id]/export`

Sade. Format seçimi (DOCX, PDF, XLSX bütçe), download butonu. Backend export job kuyruklar, status'u poll eder, hazır olunca signed URL döner. PDF için weasyprint, DOCX→PDF dönüşümünden kaçınıyoruz çünkü kalite kaybı oluyor; DOCX direkt python-docx ile, PDF ise weasyprint ile HTML→PDF (markdown→HTML→PDF).

### 3.6 `/settings/llm-config` — BYOK

```
┌──────────────────────────────────────────────────────────────┐
│ LLM Configuration                                            │
├──────────────────────────────────────────────────────────────┤
│ ◉ Use Bluedev managed keys (Pro & Agency only)               │
│   Cost: included in your plan                                │
│                                                              │
│ ○ Bring Your Own Key                                         │
│   Anthropic API Key:  [sk-ant-***************] [Test] [Save] │
│   OpenAI API Key:     [Not set]                  [Add]       │
│                                                              │
│ Preferred provider: ( • Claude  ○ OpenAI  ○ Auto )           │
│                                                              │
│ Monthly budget alert: [200] USD                              │
│ Current usage: $42.18 (Mayıs)                                │
└──────────────────────────────────────────────────────────────┘
```

Anahtarlar plaintext olarak frontend'de tutulmaz — input → backend `PUT /api/v1/tenant/llm-config` → server-side pgcrypto ile encrypt edilip DB'ye yazılır. UI sadece "set/not set" boolean gösterir.

"Test" butonu: backend ucuz bir test çağrı yapar (`claude-sonnet-4-6` 5 token), 200 dönerse "valid" gösterir.

---

## 4. Editör Extensions (TipTap)

### 4.1 ProvenanceMarker Extension

```typescript
// components/editor/extensions/ProvenanceMarker.ts
import { Mark, mergeAttributes } from '@tiptap/core';

export const ProvenanceMarker = Mark.create({
  name: 'provenance',

  addAttributes() {
    return {
      source: {
        default: 'human',
        parseHTML: el => el.getAttribute('data-provenance'),
        renderHTML: attrs => ({ 'data-provenance': attrs.source }),
      },
      agent: {
        default: null,
        parseHTML: el => el.getAttribute('data-agent'),
      },
      timestamp: { default: null },
      sentenceId: { default: null },
    };
  },

  parseHTML() {
    return [{ tag: 'span[data-provenance]' }];
  },

  renderHTML({ HTMLAttributes }) {
    return ['span', mergeAttributes(HTMLAttributes), 0];
  },

  addCommands() {
    return {
      markAsAIGenerated: (agent: string) => ({ commands }) =>
        commands.setMark('provenance', {
          source: 'ai-generated',
          agent,
          timestamp: Date.now(),
          sentenceId: generateUuid(),
        }),
      markAsAIEdited: () => ({ commands }) =>
        commands.updateAttributes('provenance', { source: 'ai-edited' }),
    };
  },
});
```

Otomatik `ai-edited` tetikleyicisi: editör change event'ini dinleyen Zustand selector, eğer değişen text bir `ai-generated` mark içeriyorsa → mark'ı `ai-edited`'e dönüştürür.

### 4.2 CitationMark Extension

```typescript
export const CitationMark = Mark.create({
  name: 'citation',
  addAttributes() {
    return {
      citationId: { default: null },
      status: { default: 'unverified' },
      shortLabel: { default: '' },
    };
  },
  renderHTML({ HTMLAttributes }) {
    return ['span', { ...HTMLAttributes, class: 'citation-badge' }, 0];
  },
});
```

CSS ile renk durumu render edilir.

---

## 5. State Stores (Zustand)

### 5.1 editorStore

```typescript
interface EditorStore {
  proposalId: string | null;
  draft: Draft | null;
  isDirty: boolean;
  saveStatus: 'idle' | 'saving' | 'saved' | 'error';
  loadProposal: (id: string) => Promise<void>;
  updateDraft: (section: string, content: string) => void;
  save: () => Promise<void>;
  saveDebounced: () => void;  // 1.5s debounce
}
```

### 5.2 generationStore

```typescript
interface GenerationStore {
  jobId: string | null;
  status: 'idle' | 'queued' | 'running' | 'completed' | 'failed';
  agents: Record<AgentId, AgentStatus>;
  currentAgent: AgentId | null;
  estimatedRemainingSeconds: number;
  startGeneration: (proposalId: string) => Promise<void>;
  subscribeStream: (proposalId: string) => () => void;  // returns unsubscribe
}
```

### 5.3 uiStore

Sidebar açık/kapalı, tema (system/light/dark), toast notifications, modal state.

---

## 6. i18n Stratejisi

### 6.1 Lokalizasyon kapsamı

- UI metinleri: %100 (tr.json + en.json)
- Validation mesajları: %100 (backend'den `message_tr` + `message_en` olarak gelir)
- Hata mesajları: %100
- Brief field labels: backend BriefField'da `label_tr` ve `label_en` ayrı

### 6.2 Locale routing

`app/[locale]/...` segment. Middleware ilk request'te:
1. Cookie'den locale oku → varsa kullan
2. Yoksa Accept-Language header parse et → tr/en map et
3. Yoksa `tr` (varsayılan, Türkiye odaklı pazar)

Kullanıcı login olduktan sonra `users.preferred_language` öncelik kazanır.

### 6.3 Mesaj formatı

next-intl + ICU MessageFormat. Çoğul, tarih, sayı formatlama native.

```json
// i18n/messages/tr.json
{
  "calls.list.title": "Açık Çağrılar",
  "calls.list.results": "{count, plural, =0 {Sonuç bulunamadı} one {# çağrı} other {# çağrı}} bulundu",
  "proposals.quota.warning": "Aylık {limit} başvuru limitinin {used}'inı kullandınız."
}
```

---

## 7. Erişilebilirlik (a11y)

WCAG 2.1 AA hedefliyoruz çünkü AB pazarına çıkıyoruz ve EAA (European Accessibility Act) Haziran 2025'te yürürlüğe girdi. Anahtar noktalar:

- shadcn/ui zaten Radix tabanlı, çoğu primitive a11y-compliant
- Renk kodlu citation rozetleri **sadece renkle ayrım yapmaz** — ikon (✓ ⚠ ✗) ve aria-label da var
- Editör keyboard-navigable (TipTap built-in)
- Form errors `aria-describedby` ile bağlanmış
- Focus management: modal kapandığında trigger'a dön
- CI'da `axe-core` ile her sayfa otomatik test (Sprint 4 sonu)

---

## 8. Performans Hedefleri

| Metrik | Hedef | Ölçüm |
|---|---|---|
| LCP | <2.0s | Vercel Speed Insights |
| FID/INP | <200ms | Web Vitals |
| Bundle size (initial) | <200KB gzip | webpack-bundle-analyzer |
| Editor first paint | <500ms | Custom marker |

TipTap büyük olduğu için editör sadece `/proposals/[id]/editor` rotasında lazy load edilir (`dynamic(() => import('./Editor'), { ssr: false })`).

---

## 9. Test Stratejisi

- **Unit:** Vitest + Testing Library, business logic için
- **Component:** Storybook + Chromatic (visual regression)
- **E2E:** Playwright, kritik flow'lar (login, brief, generate, export)
- **A11y:** axe-core CI integration

E2E test öncelik sırası:
1. Login → create proposal → fill brief → generate → wait → export DOCX
2. BYOK setup → verify test passes
3. Multi-tenant izolasyon (UI seviyesinde — başka tenant'ın URL'ini açmaya çalış, 403)

---

**Sonraki dosya:** `09-security-compliance.md`