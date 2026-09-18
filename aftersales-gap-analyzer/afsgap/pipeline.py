"""Pipeline orchestration.

    current process  ->  SAP research  ->  industry research  ->  gap analysis
         (pre-filter)     (SAP domains)     (broad + allowlist)     (3-way)
                                                                       |
                                                        design document + final gate

Each stage writes its result into ``.cache/stages`` so a re-run can skip the
expensive research legs while you iterate on the analysis or the report.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Type, TypeVar

from pydantic import BaseModel

from .analysis.current_process import load_current_process
from .analysis.gap import GapAnalyzer
from .config import Settings
from .filters.kpi import KpiFilter
from .filters.sources import SourceClassifier
from .filters.tolerance import ToleranceFilter
from .models import (
    IndustryResearchResult,
    RunResult,
    SapResearchResult,
    ValidationReport,
)
from .report.design_doc import render_markdown, write_reports
from .research.industry import IndustryResearcher
from .research.sap import SapResearcher
from .validation import validate_document

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        client: Any,
        *,
        offline: bool = False,
        use_cache: bool = True,
    ) -> None:
        self.settings = settings
        self.client = client
        self.offline = offline
        self.use_cache = use_cache
        self.tolerance = ToleranceFilter()
        self.kpi = KpiFilter()
        self.classifier = SourceClassifier()

    # ------------------------------------------------------------------
    def run(self, process_path: str | Path, skip_industry: bool = False) -> tuple[RunResult, dict[str, Path]]:
        report = ValidationReport()

        logger.info("Stage 1/5: loading and pre-filtering the current process")
        process = load_current_process(process_path, self.tolerance, report)

        logger.info("Stage 2/5: SAP standard research (official SAP domains only)")
        sap = self._cached(
            f"{process.process_id}.sap",
            SapResearchResult,
            lambda: SapResearcher(self.client, self.settings, self.classifier, self.tolerance).run(process, report),
            report,
        )

        logger.info("Stage 3/5: industry benchmark research (broad search + allowlist + OpenAlex)")
        if skip_industry:
            industry = IndustryResearchResult(
                benchmark=self._empty_benchmark(process.process_name),
            )
            report.warn("[industry] stage skipped by request; the benchmark column is empty.")
        else:
            industry = self._cached(
                f"{process.process_id}.industry",
                IndustryResearchResult,
                lambda: IndustryResearcher(
                    self.client, self.settings, self.classifier, self.tolerance, self.kpi
                ).run(process, report),
                report,
            )

        logger.info("Stage 4/5: three-way gap analysis")
        analysis = GapAnalyzer(self.client, self.tolerance, self.kpi).run(process, sap, industry, report)

        result = RunResult(
            process_id=process.process_id,
            process_name=process.process_name,
            run_date=date.today(),
            current_process=process,
            sap_research=sap,
            industry_research=industry,
            gap_analysis=analysis,
            validation=report,
            offline=self.offline,
        )

        logger.info("Stage 5/5: rendering the design document and running the final gate")
        document = render_markdown(result)
        validate_document(document, result, self.tolerance, self.kpi, report)
        result.validation = report

        paths = write_reports(result, self.settings.output_dir)
        return result, paths

    # ------------------------------------------------------------------
    def _cached(self, key: str, schema: Type[T], produce, report: ValidationReport) -> T:
        path = self.settings.cache_dir / "stages" / f"{key}.json"
        if self.use_cache and path.exists():
            logger.info("  reusing cached stage %s", key)
            # The cached payload is already filtered, but the exclusion records
            # from the run that produced it are not replayed - say so rather than
            # let the document imply nothing was excluded at this stage.
            report.warn(
                f"[cache] stage '{key}' was reused from {path.name}; its exclusion records are "
                "not shown in this log. Re-run with --no-cache for a complete log."
            )
            return schema.model_validate(json.loads(path.read_text(encoding="utf-8")))
        value = produce()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8")
        return value

    @staticmethod
    def _empty_benchmark(process_name: str):
        from .models import IndustryBenchmark

        return IndustryBenchmark(process_name=process_name, summary="Industry research was skipped for this run.")
