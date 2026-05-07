# @bluedev/shared-types

Web (Next.js) ile API (FastAPI) arasında paylaşılan TypeScript tipleri. Python tarafındaki Pydantic modellerinin TypeScript karşılıkları.

## Strateji

- **Source of truth:** `docs/05-api-contracts.md`
- **Senkronizasyon:** Python tarafında değişiklik olduğunda buradaki TS tipleri güncellenir (manuel — Faz 1 için yeterli, Faz 2'de openapi-typescript otomatize eder)

## Kullanım

```ts
import type { Proposal, Citation } from '@bluedev/shared-types';
```

Build adımı yok — Next.js `transpilePackages` ile direkt `.ts` dosyalarını yutar.
