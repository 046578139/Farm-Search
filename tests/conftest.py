import pytest

from farmsearch.config import Config
from farmsearch.fixtures.synthetic import build_fixture
from farmsearch.pipeline import run_pipeline


@pytest.fixture(scope="session")
def fixture_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("fixture")
    build_fixture(d)
    return d


@pytest.fixture(scope="session")
def cfg(fixture_dir):
    return Config.load(fixture_dir / "pipeline.yaml")


@pytest.fixture(scope="session")
def result(cfg, fixture_dir):
    return run_pipeline(cfg, stages=(1, 2, 3, 4), out_dir=fixture_dir / "outputs", write=True)


@pytest.fixture(scope="session")
def scored(result):
    return result["scored"].set_index("account_id")


@pytest.fixture(scope="session")
def stage1(result):
    return result["stage1"].parcels.set_index("account_id")
