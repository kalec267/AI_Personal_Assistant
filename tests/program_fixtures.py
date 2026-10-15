"""Explicit user registrations for tests of history, proposals and device calls."""
from types import SimpleNamespace
from unittest.mock import patch


def register_fixture_programs(programs):
    for identifier, name in (("notepad", "메모장"), ("calculator", "계산기")):
        filename = programs.path.parent / f"fixture-{identifier}.exe"
        filename.write_bytes(b"unit-test-fixture; never execute")
        with patch("assistant.programs.uuid.uuid4", return_value=SimpleNamespace(hex=identifier)):
            programs.register(name, filename)
