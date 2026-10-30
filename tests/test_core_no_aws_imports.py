"""core/ must stay portable: no AWS SDK or AWS runtime imports."""

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parent.parent / "core"
FORBIDDEN_EXACT = {"boto3", "botocore"}
FORBIDDEN_PREFIXES = ("aws_", "aws_lambda_")


def _forbidden(module: str) -> bool:
    root = module.split(".")[0]
    return root in FORBIDDEN_EXACT or root.startswith(FORBIDDEN_PREFIXES)


def _imports(path: Path) -> list[str]:
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module)
    return names


def test_core_has_no_aws_imports() -> None:
    offenders = [
        f"{path.relative_to(CORE.parent)}: {name}"
        for path in sorted(CORE.rglob("*.py"))
        for name in _imports(path)
        if _forbidden(name)
    ]
    assert not offenders, offenders


def test_detector_flags_aws_imports(tmp_path: Path) -> None:
    sample = tmp_path / "x.py"
    sample.write_text("import boto3\nfrom aws_lambda_powertools import Logger\n")
    assert [n for n in _imports(sample) if _forbidden(n)] == [
        "boto3",
        "aws_lambda_powertools",
    ]
