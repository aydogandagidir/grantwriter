# 06 — Agent Mimarisi

## 1. Tasarım Felsefesi

### 1.1 Niye Custom Orchestrator?

ag2/AutoGen ve LangGraph değerlendirildi, reddedildi:
- **ag2:** Çok ağır, agent-to-agent conversation gereksiz (bizim flow deterministic)
- **LangGraph:** Yeni, ekosistem küçük, debug zor

**Karar:** Saga pattern + Celery + Redis pub/sub.

### 1.2 Agent Sayısı: 7 (sabit)

Her agent net bir sorumluluğa sahip:
1. **Call Analyst** — çağrı metnini parse eder
2. **Excellence Writer** — Excellence section
3. **Impact Writer** — Impact section
4. **Implementation Writer** — Implementation section + budget
5. **Compliance Reviewer** — formal kurallar (page limits, AI disclosure, DNSH)
6. **Hallucination Hunter** — citation grounding final check
7. **Distinctiveness Scorer** — anti-generic check (CORDIS comparison)

---

## 2. Conversation Flow

```
                    User Brief + Call Selection
                              │
                              ▼
                ┌──────────────────────────────┐
                │   1. CALL ANALYST            │
                │   (5-10 sec, Claude Opus)    │
                │   Output: structured metadata │
                └──────────┬───────────────────┘
                           │
                           ▼
              ┌────────────┴────────────┐
              │                         │
              ▼                         ▼
   ┌──────────────────┐      ┌──────────────────┐
   │ 2a. EXCELLENCE   │      │ 2b. IMPACT       │
   │  WRITER (parallel)│     │  WRITER (parallel)│
   │  ~30 sec, Opus   │      │  ~30 sec, Opus   │
   └──────────┬───────┘      └────────┬─────────┘
              │                       │
              └───────────┬───────────┘
                          ▼
                ┌──────────────────────┐
                │ 3. IMPLEMENTATION    │
                │    WRITER            │
                │  ~45 sec, Opus       │
                │  (needs Excellence + │
                │   Impact done)       │
                └──────────┬───────────┘
                           │
                ┌──────────┴──────────┐
                │                     │
                ▼                     ▼
   ┌──────────────────┐    ┌──────────────────┐
   │ 4. COMPLIANCE    │    │ 5. DISTINCTIVENESS│
   │    REVIEWER      │    │    SCORER         │
   │  ~10 sec, Sonnet │    │  ~5 sec, embed   │
   └──────────┬───────┘    └────────┬─────────┘
              │                     │
              └──────────┬──────────┘
                         ▼
                ┌──────────────────────┐
                │ 6. HALLUCINATION     │
                │    HUNTER (final)    │
                │  ~60 sec (citation   │
                │   batch verify)      │
                └──────────┬───────────┘
                           │
                           ▼
                  Draft Complete
                  (status='draft_complete')
```

**Toplam tipik süre:** 3-5 dk LLM + 1 dk citation verification = ~6 dk
**Worst case:** HE (uzun call text + 50+ citations) = ~15 dk
**Maliyet:** Bluedev managed Claude → $2-15/draft

---

## 3. Base Agent Interface

```python
# apps/api/src/agents/base.py

from abc import ABC, abstractmethod
from typing import AsyncIterator
from pydantic import BaseModel
from uuid import UUID

class AgentInput(BaseModel):
    proposal_id: UUID
    tenant_id: UUID
    programme_id: str
    language: str
    brief: dict
    call: dict
    previous_outputs: dict     # outputs from earlier agents

class AgentOutput(BaseModel):
    agent_id: str
    status: str                # 'completed', 'failed', 'skipped'
    output: dict               # agent-specific output
    citations_extracted: list[dict] = []
    metadata: dict = {}
    duration_ms: int
    cost_usd: float
    tokens_used: dict          # {input, output, cached}

class BaseAgent(ABC):
    agent_id: str
    name: str
    description: str
    version: str
    requires_rag: bool
    estimated_duration_seconds: int

    @abstractmethod
    async def run(self, input: AgentInput) -> AgentOutput: ...

    @abstractmethod
    async def stream(self, input: AgentInput) -> AsyncIterator[str]: ...
```

---

## 4. Agent Detay

### 4.1 Call Analyst

**Sorumluluk:** Çağrı dokümanını parse edip yapılandırılmış metadata çıkarır.

**Input:**
- `call.call_text` (full document, can be 20-50 pages)
- `brief.summary` (user's project idea)

**Output schema:**
```json
{
  "eligibility": {
    "eligible_countries": ["EU MS", "Associated Countries", "Türkiye"],
    "eligible_entities": ["SME", "research organization"],
    "min_partners": 3,
    "min_countries": 3,
    "trl_range": [4, 6]
  },
  "scope_summary": "...",
  "expected_outcomes": ["..."],
  "expected_impacts": ["..."],
  "evaluation_criteria": [
    {"criterion": "Excellence", "weight": 0.5, "sub_criteria": [...]}
  ],
  "page_limit": 45,
  "language_required": "en",
  "user_eligible": true,
  "user_eligibility_issues": [],
  "key_terms_to_use": ["circular economy", "TRL 5", "..."],
  "deadlines": {"submission": "2026-09-15", "ga_signature": "2027-03-01"}
}
```

**Prompt:** `apps/api/src/agents/prompts/_shared/call_analyst/v1.md`

```markdown
You are an expert EU/Turkish grant funding analyst.

Your task: parse the provided call document and extract structured metadata.

Be precise. If something is ambiguous, mark it as "unclear" rather than guessing.

User's project context (for eligibility check):
{brief_summary}

Call document:
<call_document>
{call_text}
</call_document>

Output a JSON object matching this schema:
<schema>
{json_schema}
</schema>

Critical rules:
- Page limits: extract exact number from document, not estimate
- Eligibility: list exact text from document, don't paraphrase
- Evaluation criteria: include weights if specified
- key_terms_to_use: extract 10-15 terms the writers should weave into their text
- user_eligibility_issues: if user is not clearly eligible, list specific issues
```

**Model:** Claude Opus 4.7 (long context, complex extraction)
**Caching:** Call text is cached (5 min TTL), so subsequent runs hit cache.

### 4.2 Excellence Writer

**Sorumluluk:** Horizon Europe Section 1 (Excellence) veya TÜBİTAK eşdeğer bölümünü yazar.

**Input:**
- All from Call Analyst
- `brief.problem_statement`, `brief.proposed_solution`, `brief.team_expertise`
- RAG: top 5 successful Excellence sections from same programme/topic

**Output:**
```json
{
  "excellence_md": "## 1.1 Objectives and ambition\n\n...",
  "subsections": {
    "1.1_objectives_and_ambition": "...",
    "1.2_methodology": "...",
    "1.3_state_of_the_art": "...",
    "1.4_open_science": "..."
  },
  "citations_used": [
    {"raw_text": "Smith et al. 2023...", "doi": "10.xxxx/...", "claim": "..."}
  ],
  "key_terms_used": ["circular economy", ...],
  "word_count": 4200,
  "estimated_page_count": 11
}
```

**Prompt:** `apps/api/src/agents/prompts/horizon_eu/excellence_writer/v1.md`

```markdown
You are an expert grant writer specializing in Horizon Europe RIA/IA proposals.

Your task: write a complete Excellence section (Section 1) for the user's project,
following the official Horizon Europe Standard Application Form template.

# Project Context

User Brief:
{brief}

Call Analysis:
{call_metadata}

# Retrieved Examples (Successful Past Proposals — Same Topic Cluster)

<retrieved_context>
{rag_chunks}
</retrieved_context>

# Citation Rules (CRITICAL)

YOU MUST FOLLOW THESE RULES:
1. ONLY cite sources provided in <retrieved_context> above. Do NOT invent citations.
2. If you don't have a source for a claim, omit the citation. Better no citation
   than fabricated.
3. Use [author year] inline format, e.g., [Smith 2023].
4. At end of section, list all citations in a "References" subsection with
   complete metadata (title, authors, year, journal, DOI).
5. The verification pipeline WILL check every citation. Fabricated citations
   block this proposal from being exported.

# Writing Rules

1. Write in {language} (en or tr).
2. Match the tone, depth, and structure of the retrieved examples.
3. Be specific to the user's project — never generic boilerplate.
4. Weave in these key terms from the call: {key_terms_to_use}
5. Subsections: 1.1 Objectives and ambition, 1.2 Methodology,
   1.3 State of the art, 1.4 Open science (where applicable)
6. Page budget: ~10 pages (~4000 words). Don't exceed.
7. Use proper Markdown headings (## for subsections).
8. Methodology MUST include a clear Theory of Change or logical chain.
9. State of the art MUST cite at least 5 sources.

# Anti-Generic Rules (CRITICAL — distinctiveness scoring active)

Recent research (Nature 2026) shows AI-written proposals tend to cluster around
the same patterns and get rejected for "lower semantic distinctiveness." To avoid
this:
- Use the user's specific terminology and naming
- Reference the user's specific industry context
- Avoid phrases like "cutting-edge", "state-of-the-art breakthrough",
  "paradigm shift", "transformative impact"
- Be technically specific: name actual algorithms, datasets, metrics

Now write the Excellence section.
```

**Model:** Claude Opus 4.7
**Streaming:** Yes (SSE) — user sees text appearing live

### 4.3 Impact Writer

Similar structure to Excellence Writer but for Section 2 (Impact).

**Subsections:**
- 2.1 Project's pathways towards impact
- 2.2 Measures to maximise impact (dissemination, exploitation, communication)
- 2.3 Summary canvas (key impact pathways table)

**Special focus:**
- KIPs (Key Impact Pathways) — quantitative targets
- Open Science strategy
- IP strategy (esp. for IA — innovation actions)

**Prompt similar to Excellence, but specialized for Impact section.**

### 4.4 Implementation Writer

**Sorumluluk:** Section 3 (Implementation) — work packages, Gantt, budget, consortium.

**Special outputs:**
- WP table (number, title, lead partner, person-months, deliverables)
- Gantt chart (Mermaid markdown for now, dedicated tool Faz 2)
- Budget table (lump sum format for HE, fatura-bazlı for TÜBİTAK)
- Risk register

**Output:**
```json
{
  "implementation_md": "...",
  "work_packages": [
    {
      "wp_number": 1,
      "title": "Requirements analysis",
      "lead_partner": "Coordinator",
      "person_months_per_partner": {...},
      "duration_months": 6,
      "deliverables": [...],
      "milestones": [...]
    }
  ],
  "budget": {
    "type": "lump_sum",
    "total_eur": 4500000,
    "by_partner": {...},
    "by_wp": {...}
  },
  "risks": [
    {"risk": "...", "probability": "medium", "mitigation": "..."}
  ]
}
```

### 4.5 Compliance Reviewer

**Sorumluluk:** Hard rules check.

**Checks:**
1. **Page limits**: per section, per programme (HE 45p, EIC 20p, TÜBİTAK no strict limit but recommended)
2. **AI disclosure** (HE only): generates page 32 text from provenance log
3. **DNSH** (HE only): hybrid rule-based + LLM check vs 6 environmental objectives
4. **Gender Dimension** (HE only): is research design gender-aware?
5. **Open Science**: DMP mentioned? FAIR principles?
6. **Required sections**: are all subsections present?
7. **Required terms**: did writers weave in `key_terms_to_use`?

**Output:**
```json
{
  "passed": false,
  "issues": [
    {
      "severity": "blocker",
      "type": "page_limit",
      "section": "excellence",
      "current": 12,
      "limit": 10,
      "suggestion": "Trim subsection 1.3 by ~800 words"
    },
    {
      "severity": "warning",
      "type": "missing_dnsh",
      "message": "DNSH assessment not found"
    }
  ],
  "ai_disclosure_text": "...",
  "compliance_score": 0.85
}
```

**Model:** Claude Sonnet 4.6 (cheaper, good for rule-following)

### 4.6 Hallucination Hunter

**Sorumluluk:** Final pass — citation verification.

Detail in `04-rag-strategy.md` §3.

**Output:**
```json
{
  "total_citations": 47,
  "verified": 44,
  "partial": 2,
  "fabricated": 1,
  "verification_rate": 0.94,
  "blocking": true,
  "fabricated_citations": [
    {"id": "uuid", "raw_text": "...", "reason": "DOI returns 404"}
  ],
  "claim_verification_sample": {
    "checked": 10,
    "passed": 9,
    "issues": [...]
  }
}
```

### 4.7 Distinctiveness Scorer

Detail in `04-rag-strategy.md` §4.

---

## 5. Orchestrator Implementation

```python
# apps/api/src/orchestrator/draft_generator.py

class DraftGenerator:
    """
    Saga pattern: each agent step has a compensation action on failure.
    """
    def __init__(self, proposal_id: UUID):
        self.proposal_id = proposal_id
        self.publisher = SSEPublisher(proposal_id)

    async def run(self) -> None:
        try:
            await self._update_status("generating")

            # Step 1: Call Analyst (must succeed)
            call_meta = await self._run_agent(CallAnalyst())

            # Step 2: Excellence + Impact in parallel
            excellence_task = asyncio.create_task(
                self._run_agent(ExcellenceWriter(), call_meta)
            )
            impact_task = asyncio.create_task(
                self._run_agent(ImpactWriter(), call_meta)
            )
            excellence, impact = await asyncio.gather(
                excellence_task, impact_task, return_exceptions=True
            )
            self._handle_partial_failure(excellence, impact)

            # Step 3: Implementation (depends on Excellence + Impact)
            implementation = await self._run_agent(
                ImplementationWriter(),
                {"call_meta": call_meta, "excellence": excellence, "impact": impact}
            )

            # Step 4: Compliance + Distinctiveness in parallel
            compliance_task = asyncio.create_task(self._run_agent(ComplianceReviewer()))
            distinctiveness_task = asyncio.create_task(
                self._run_agent(DistinctivenessScorer())
            )
            await asyncio.gather(compliance_task, distinctiveness_task)

            # Step 5: Hallucination Hunter (final)
            hunt_report = await self._run_agent(HallucinationHunter())

            # Final
            if hunt_report.blocking:
                await self._update_status("draft_complete_with_issues")
            else:
                await self._update_status("draft_complete")

            await self.publisher.publish("completed", {"proposal_id": self.proposal_id})

        except RecoverableError as e:
            await self._update_status("failed_recoverable")
            await self.publisher.publish("error", {"error": str(e), "recoverable": True})
        except UnrecoverableError as e:
            await self._update_status("failed")
            await self.publisher.publish("error", {"error": str(e), "recoverable": False})
```

---

## 6. Prompt Versioning

Directory structure:
```
apps/api/src/agents/prompts/
├── _shared/
│   ├── call_analyst/
│   │   ├── v1.md
│   │   └── v2.md
│   └── compliance_reviewer/
│       └── v1.md
├── horizon_eu/
│   ├── excellence_writer/
│   │   ├── v1.md
│   │   └── v2.md
│   ├── impact_writer/
│   ├── implementation_writer/
│   └── _key_terms.json
├── tubitak_1501/
│   └── ...
├── kosgeb_arge/
│   └── ...
└── cascade_funding/
    └── ...
```

**Config:**
```yaml
# apps/api/src/config/prompts.yaml
default:
  call_analyst: v1
  excellence_writer:
    horizon_eu: v1
    tubitak_1501: v1
    kosgeb_arge: v1
  ...

experiments:
  excellence_writer_v2:
    enabled_for_tenants: ["bluedev_internal"]
    rollout_percentage: 0
```

---

## 7. Cost Optimization

### 7.1 Prompt Caching

Anthropic prompt caching: system prompt block (~5K tokens) cached for 5 min.

```python
system_blocks = [
    {
        "type": "text",
        "text": load_prompt("excellence_writer/v1"),
        "cache_control": {"type": "ephemeral"}
    },
    {
        "type": "text",
        "text": rag_context,  # variable per proposal
    }
]
```

**Saving:** Re-runs (e.g., regenerate just Excellence) hit cache → 90% input cost reduction.

### 7.2 Model Routing

| Agent | Primary | Fallback |
|---|---|---|
| Call Analyst | Opus 4.7 (complex extraction) | GPT-4o |
| Excellence Writer | Opus 4.7 (writing quality) | GPT-4o |
| Impact Writer | Opus 4.7 | GPT-4o |
| Implementation Writer | Opus 4.7 | GPT-4o |
| Compliance Reviewer | Sonnet 4.6 (rule-following, faster) | GPT-4o-mini |
| Hallucination Hunter | Sonnet 4.6 (just verification) | GPT-4o-mini |
| Distinctiveness | (no LLM, embedding only) | — |

### 7.3 Token Budget per Draft

| Agent | Avg Input Tokens | Avg Output Tokens | Cost (Opus) |
|---|---|---|---|
| Call Analyst | 30K | 2K | $0.60 |
| Excellence Writer | 8K + 4K RAG | 4K | $1.05 |
| Impact Writer | 8K + 3K RAG | 3K | $0.85 |
| Implementation Writer | 12K | 4K | $1.20 |
| Compliance Reviewer | 15K | 1K | $0.20 (Sonnet) |
| Hallucination Hunter | 10K | 500 | $0.10 (Sonnet) |
| **Total per HE draft** | | | **~$4** |

For TÜBİTAK 1501 (smaller call text, shorter sections): ~$2/draft.

---

## 8. Testing Strategy

### 8.1 Agent Unit Tests

```python
# apps/api/tests/agents/test_excellence_writer.py
@pytest.mark.asyncio
async def test_excellence_writer_basic(snapshot):
    agent = ExcellenceWriter()
    input = AgentInput(
        proposal_id=test_uuid,
        brief={"problem_statement": "...", ...},
        call={"topic": "AI", ...},
        ...
    )
    output = await agent.run(input)
    assert output.status == "completed"
    assert "## 1.1" in output.output["excellence_md"]
    assert len(output.output["citations_used"]) >= 5
    snapshot.assert_match(output.output["subsections"]["1.1_objectives_and_ambition"])
```

### 8.2 LLM Output Stability

- Set `temperature=0.3` for writer agents (less variance, still creative)
- Set `temperature=0.0` for analysts (deterministic)
- Snapshot tests with diff threshold (allow minor wording changes)
- A/B test: production v1 vs experiment v2 → win rate measured

### 8.3 Integration Tests

```python
# apps/api/tests/integration/test_full_flow.py
@pytest.mark.slow
async def test_full_draft_generation_horizon_eu():
    proposal = await create_test_proposal(programme="horizon_eu_ria")
    job = await trigger_generation(proposal.id)
    await wait_for_completion(job.id, timeout=600)

    final = await get_proposal(proposal.id)
    assert final.status == "draft_complete"
    assert len(final.draft["excellence_md"]) > 5000
    assert final.distinctiveness_score is not None
    assert all(c.status == "verified" for c in final.bibliography)
```

---

## 9. Observability

Every agent run produces:
- Sentry trace (full distributed trace)
- PostHog event (`agent_completed`, `{agent_id, duration_ms, cost_usd}`)
- Database row in `tenant_usage_log`
- Log line in Logtail with structured fields

Dashboard (PostHog/Grafana):
- p50, p95, p99 latency per agent
- Success rate per agent
- Cost trend per programme
- Citation verification rate over time

---

## 10. Future (Faz 2+)

- **Reviewer Simulation Agent**: predicts evaluator scoring before submission
- **Consortium Builder Agent**: suggests partners from CORDIS+LinkedIn
- **Resubmission Coach**: parses ESR, recommends specific section rewrites
- **Multi-language QA Agent**: TR ↔ EN translation quality check

---

**Sonraki dosya:** `07-program-modules.md` — 5 program plugin detayı.