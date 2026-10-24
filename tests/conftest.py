import csv
import shutil
from pathlib import Path

import pytest
from app.core.config import Settings
from app.data.synthetic import GeneratorConfig, generate_demo
from app.db.session import Database


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data", runtime_dir=tmp_path / "runtime", artifact_dir=tmp_path / "artifacts")


@pytest.fixture
def db(settings):
    database = Database(settings.runtime_dir / "restockiq.sqlite3")
    database.initialize()
    yield database
    database.engine.dispose()


@pytest.fixture(scope="session")
def demo(tmp_path_factory):
    root = tmp_path_factory.mktemp("default-demo")
    return generate_demo(Settings(data_dir=root / "data", runtime_dir=root / "runtime"), GeneratorConfig())


@pytest.fixture(scope="session")
def small_demo(tmp_path_factory):
    root = tmp_path_factory.mktemp("small-demo")
    return generate_demo(Settings(data_dir=root / "data", runtime_dir=root / "runtime"), GeneratorConfig(days=60, sku_count=5))


@pytest.fixture
def batch_dir(small_demo, tmp_path):
    target = tmp_path / "input"
    shutil.copytree(small_demo.operational_dir, target)
    return target


def read_csv(path):
    with Path(path).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def mutate_csv(directory, filename, mutate):
    path = directory / filename
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields, rows = reader.fieldnames, list(reader)
    mutate(rows)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
