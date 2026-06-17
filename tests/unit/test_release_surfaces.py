from __future__ import annotations

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_distroless_dockerfile_installs_release_extras_like_runtime_image() -> None:
    dockerfile = (_REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    distroless = (_REPO_ROOT / "Dockerfile.distroless").read_text(encoding="utf-8")

    expected = "uv sync --frozen --no-dev --no-install-project --extra metrics --extra tracing"
    assert expected in dockerfile
    assert expected in distroless


def test_helm_chart_description_matches_multi_agent_alb_scope() -> None:
    chart = yaml.safe_load((_REPO_ROOT / "deploy" / "helm" / "agent-load-balancer" / "Chart.yaml").read_text())

    description = chart["description"]
    assert "Agent Load Balancer" in description
    assert "Codex" in description
    assert "Gemini" in description
    assert "Antigravity" in description
    assert "OpenAI API load balancer" not in description
    assert {"agent", "antigravity", "gemini", "codex"}.issubset(set(chart["keywords"]))
