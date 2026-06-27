from __future__ import annotations

import importlib


def test_hexagonal_packages_are_importable() -> None:
    packages = [
        "quiniela.domain",
        "quiniela.application",
        "quiniela.ports",
        "quiniela.adapters",
        "quiniela.infrastructure",
    ]

    for package in packages:
        module = importlib.import_module(package)
        assert module.__name__ == package
