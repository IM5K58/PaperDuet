"""Tests that read the bundled sample paper skip when it is absent.

The Rex-Omni sample (fixtures/rex-omni.blocks.json) is kept out of the public
repository; mark tests that depend on its content with @pytest.mark.sample.
"""
from pathlib import Path

import pytest

SAMPLE = Path(__file__).resolve().parents[2] / 'fixtures/rex-omni.blocks.json'


def pytest_configure(config):
    config.addinivalue_line('markers', 'sample: needs the local Rex-Omni sample fixture')


def pytest_collection_modifyitems(config, items):
    if SAMPLE.is_file():
        return
    skip = pytest.mark.skip(reason='Rex-Omni sample fixture is not in this checkout')
    for item in items:
        if 'sample' in item.keywords:
            item.add_marker(skip)
