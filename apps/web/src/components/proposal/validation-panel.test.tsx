import type { ValidationReport } from '@bluedev/shared-types';
import { describe, expect, it } from 'vitest';

import { isExportBlocked } from './validation-panel';

const buildReport = (overrides: Partial<ValidationReport> = {}): ValidationReport => ({
  compliance: {
    passed: true,
    issues: [],
    ai_disclosure_text: null,
    compliance_score: 1.0,
    ...(overrides.compliance ?? {}),
  },
  hallucination_hunter: {
    total_citations: 4,
    verified: 4,
    partial_match: 0,
    fabricated: 0,
    not_found: 0,
    errors: 0,
    verification_rate: 1.0,
    flagged_citations: [],
    recommendation: 'ok',
    claim_check_pass_rate: 0.9,
    ...(overrides.hallucination_hunter ?? {}),
  },
});

describe('isExportBlocked', () => {
  it('allows export when compliance passes AND hunt is ok', () => {
    expect(isExportBlocked(buildReport())).toBe(false);
  });

  it('blocks export when compliance reports any blocker', () => {
    const report = buildReport({
      compliance: {
        passed: false,
        issues: [
          {
            severity: 'blocker',
            section: 'excellence',
            code: 'missing_subsection',
            message_tr: '1.2 eksik',
            message_en: '1.2 missing',
            suggestion: null,
          },
        ],
        ai_disclosure_text: null,
        compliance_score: 0.9,
      },
    });
    expect(isExportBlocked(report)).toBe(true);
  });

  it('blocks export when hunt recommends block_export even though compliance passed', () => {
    const report = buildReport({
      hallucination_hunter: {
        total_citations: 4,
        verified: 2,
        partial_match: 0,
        fabricated: 1,
        not_found: 1,
        errors: 0,
        verification_rate: 0.5,
        flagged_citations: [
          {
            raw_text: '[Smith 2099] fake citation',
            section: 'excellence',
            status: 'fabricated',
            source: 'crossref',
            match_score: null,
            warning: 'DOI did not resolve',
          },
        ],
        recommendation: 'block_export',
        claim_check_pass_rate: 0.4,
      },
    });
    expect(isExportBlocked(report)).toBe(true);
  });

  it('allows export when hunt is absent (no citations sampled)', () => {
    // Backend returns `hallucination_hunter: null` when there's nothing
    // to verify (zero citations). Compliance gate decides on its own.
    expect(
      isExportBlocked({
        compliance: buildReport().compliance,
        hallucination_hunter: null,
      }),
    ).toBe(false);
  });
});
