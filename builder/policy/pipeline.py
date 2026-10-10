"""Bounded offline ingestion, extraction validation, coverage and composition."""
from dataclasses import dataclass

from builder.brief.sources import BriefBundle, load_briefs
from builder.llm.extraction import ExtractionRequest, ModelConfig
from builder.policy.candidate import CandidatePolicy, check_coverage, loads
from builder.policy.codec import IRValidationError
from builder.policy.composition import Composition, compose
from builder.policy.models import ValidationFinding, ValidationReport


@dataclass(frozen=True)
class PipelineResult:
    sources: BriefBundle
    candidate: CandidatePolicy | None
    effective: Composition | None
    validation: ValidationReport
    coverage: ValidationReport
    attempts: int

    @property
    def compilable(self):
        return False


def extract_briefs(paths, provider=None, *, config=None, max_repairs=1) -> PipelineResult:
    if type(max_repairs) is not int or not 0 <= max_repairs <= 2:
        raise ValueError('max_repairs must be 0, 1, or 2.')
    bundle = load_briefs(paths)
    missing = ValidationReport(tuple(ValidationFinding('coverage_missing', f'sections[{i}]',
        'Potentially normative source section has no classification.') for i, _ in enumerate(bundle.sections)), False)
    if provider is None:
        report = ValidationReport((ValidationFinding('provider_missing', '$', 'Supply an extraction adapter; no deterministic policy parser is substituted.'),), False)
        return PipelineResult(bundle, None, None, report, missing, 0)
    if (not isinstance(config, ModelConfig) or type(config.provider) is not str
            or type(config.model) is not str or not config.provider.strip() or not config.model.strip()):
        raise ValueError('Supply a provider/model configuration for the adapter.')
    feedback = ()
    for attempt in range(max_repairs + 1):
        candidate, effective, coverage = None, None, missing
        try:
            text = provider.extract(ExtractionRequest(bundle, config, attempt, feedback))
            candidate = loads(text, bundle)
            coverage = check_coverage(candidate, bundle)
            effective = compose(candidate)
            validation = effective.report
        except IRValidationError as exc:
            validation = exc.report
        except Exception:
            # Provider exception text may contain credentials or private prompts.
            validation = ValidationReport((ValidationFinding('extraction_failed', '$', 'Extraction failed; review the adapter privately.'),), False)
        result = PipelineResult(bundle, candidate, effective, validation, coverage, attempt + 1)
        if validation.valid and coverage.valid:
            return result
        feedback = validation.findings + coverage.findings
    return result
