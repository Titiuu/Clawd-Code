from __future__ import annotations

import importlib
import io
import sys
from types import SimpleNamespace
import zipfile

import pytest
from fastapi import HTTPException


def _load_app(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAWD_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("CLAWD_SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setenv("CLAWD_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    sys.modules.pop("src.server.app", None)
    return importlib.import_module("src.server.app")


def _skill_zip(*, nested: bool = True, body: str = "Do the thing.") -> bytes:
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as zf:
        prefix = "demo/" if nested else ""
        zf.writestr(
            f"{prefix}SKILL.md",
            "---\ndescription: Demo skill\n---\n\n" + body + "\n",
        )
        zf.writestr(f"{prefix}notes.txt", "extra")
    return raw.getvalue()


def test_upload_skill_and_conflict(monkeypatch, tmp_path):
    app_mod = _load_app(monkeypatch, tmp_path)
    response = app_mod.service.install_skill("demo", _skill_zip(), overwrite=False)
    assert response.name == "demo"

    with pytest.raises(HTTPException) as conflict:
        app_mod.service.install_skill("demo", _skill_zip(body="changed"), overwrite=False)
    assert conflict.value.status_code == 409

    overwrite = app_mod.service.install_skill("demo", _skill_zip(body="changed"), overwrite=True)
    assert overwrite.overwritten is True

    listed = app_mod.service.list_skills()
    assert [skill.name for skill in listed] == ["demo"]


def test_upload_skill_does_not_require_copytree(monkeypatch, tmp_path):
    app_mod = _load_app(monkeypatch, tmp_path)

    def fail_copytree(*args, **kwargs):
        raise OSError("copytree should not be used")

    monkeypatch.setattr(app_mod.shutil, "copytree", fail_copytree)

    response = app_mod.service.install_skill("demo", _skill_zip(), overwrite=False)

    assert response.name == "demo"
    assert (tmp_path / "skills" / "demo" / "SKILL.md").exists()


def test_serves_default_ui(monkeypatch, tmp_path):
    app_mod = _load_app(monkeypatch, tmp_path)

    response = app_mod.index()
    head_response = app_mod.index_head()

    assert response.path == app_mod.INDEX_HTML
    assert head_response.path == app_mod.INDEX_HTML
    assert app_mod.INDEX_HTML.exists()
    assert "Clawd Runtime" in app_mod.INDEX_HTML.read_text(encoding="utf-8")


def test_serves_static_assets(monkeypatch, tmp_path):
    app_mod = _load_app(monkeypatch, tmp_path)

    assert (app_mod.STATIC_DIR / "app.css").exists()
    assert (app_mod.STATIC_DIR / "app.js").exists()
    static_mounts = [route for route in app_mod.app.routes if getattr(route, "path", None) == "/static"]
    assert static_mounts


def test_upload_rejects_multiple_skills(monkeypatch, tmp_path):
    app_mod = _load_app(monkeypatch, tmp_path)
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as zf:
        zf.writestr("one/SKILL.md", "---\ndescription: One\n---\n")
        zf.writestr("two/SKILL.md", "---\ndescription: Two\n---\n")

    with pytest.raises(HTTPException) as exc:
        app_mod.service.install_skill("bad", raw.getvalue(), overwrite=False)
    assert exc.value.status_code == 400


def test_upload_rejects_zip_path_escape(monkeypatch, tmp_path):
    app_mod = _load_app(monkeypatch, tmp_path)
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as zf:
        zf.writestr("../SKILL.md", "---\ndescription: Escape\n---\n")

    with pytest.raises(HTTPException) as exc:
        app_mod.service.install_skill("escape", raw.getvalue(), overwrite=False)
    assert exc.value.status_code == 400


def test_chat_creates_session_and_history(monkeypatch, tmp_path):
    app_mod = _load_app(monkeypatch, tmp_path)

    monkeypatch.setattr(app_mod, "get_default_provider", lambda: "openai")
    monkeypatch.setattr(
        app_mod,
        "get_provider_config",
        lambda provider: {
            "api_key": "test-key",
            "base_url": "https://example.test/v1",
            "default_model": "test-model",
        },
    )
    monkeypatch.setattr(
        app_mod.AgentRuntimeService,
        "_build_provider",
        lambda self, provider_name: SimpleNamespace(model="test-model"),
    )

    def fake_run_agent_loop(*, conversation, **kwargs):
        conversation.add_assistant_message("hello from runtime")
        return SimpleNamespace(
            response_text="hello from runtime",
            usage={"input_tokens": 1, "output_tokens": 2},
            num_turns=1,
        )

    monkeypatch.setattr(app_mod, "run_agent_loop", fake_run_agent_loop)
    payload = app_mod.service.chat(app_mod.ChatRequest(message="hi"))
    assert payload.response == "hello from runtime"
    assert payload.usage == {"input_tokens": 1, "output_tokens": 2}
    assert payload.history[0]["role"] == "user"
    assert payload.history[1]["role"] == "assistant"

    history = app_mod.service.history(payload.session_id)
    assert history.history == payload.history


def test_chat_rejects_unconfigured_provider(monkeypatch, tmp_path):
    app_mod = _load_app(monkeypatch, tmp_path)
    monkeypatch.setattr(app_mod, "get_default_provider", lambda: "openai")
    monkeypatch.setattr(
        app_mod,
        "get_provider_config",
        lambda provider: {
            "api_key": "",
            "base_url": "https://example.test/v1",
            "default_model": "test-model",
        },
    )

    with pytest.raises(HTTPException) as exc:
        app_mod.service.chat(app_mod.ChatRequest(message="hi"))
    assert exc.value.status_code == 400
    assert "api key is not configured" in str(exc.value.detail)
