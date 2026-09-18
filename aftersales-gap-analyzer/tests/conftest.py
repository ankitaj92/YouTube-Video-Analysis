import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest  # noqa: E402

from afsgap.filters.kpi import KpiFilter  # noqa: E402
from afsgap.filters.sources import SourceClassifier  # noqa: E402
from afsgap.filters.tolerance import ToleranceFilter  # noqa: E402
from afsgap.models import ValidationReport  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def tolerance():
    return ToleranceFilter()


@pytest.fixture
def kpi():
    return KpiFilter()


@pytest.fixture
def classifier():
    return SourceClassifier()


@pytest.fixture
def report():
    return ValidationReport()


@pytest.fixture
def fixtures_dir():
    return FIXTURES
