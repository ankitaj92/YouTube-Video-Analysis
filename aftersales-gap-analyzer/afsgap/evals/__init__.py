"""Evaluation harness: does the pipeline produce good analysis, not just valid output?"""

from .schema import CaseResult, CaseScore, EvalCase, EvalRun

__all__ = ["EvalCase", "CaseResult", "CaseScore", "EvalRun"]
