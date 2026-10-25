from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "packaging" / "deploy_site.py"


def _load():
    spec = importlib.util.spec_from_file_location("deploy_site_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_mcp_host_is_pinned_to_an_exact_version():
    module = _load()
    package = next(part for part in module.SERVER if part.startswith("--package="))

    assert "@latest" not in package
    assert package == f"--package=@hostinger/mcp@{module.MCP_VERSION}"
    assert module.MCP_VERSION[0].isdigit()
