"""HTTP runtime service for Clawd Codex."""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.agent.conversation import Conversation
from src.config import get_default_provider, get_provider_config, load_config
from src.providers import get_provider_class
from src.skills.loader import get_all_skills
from src.tool_system.agent_loop import run_agent_loop
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.tool_system.protocol import ToolCall


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 31366
MAX_ZIP_BYTES = 25 * 1024 * 1024
MAX_EXTRACTED_BYTES = 100 * 1024 * 1024
MAX_ZIP_MEMBERS = 2_000
STATIC_DIR = Path(__file__).with_name("static")
INDEX_HTML = STATIC_DIR / "index.html"


def _default_skills_dir() -> Path:
    return Path(os.environ.get("CLAWD_SKILLS_DIR", Path.home() / ".clawd" / "skills")).expanduser().resolve()


def _runtime_root() -> Path:
    return Path(os.environ.get("CLAWD_RUNTIME_ROOT", Path.home() / ".clawd" / "runtime")).expanduser().resolve()


def _workspace_root() -> Path:
    default = _runtime_root() / "workspaces"
    return Path(os.environ.get("CLAWD_WORKSPACE_ROOT", default)).expanduser().resolve()


def _utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


class SkillInfo(BaseModel):
    name: str
    description: str
    loaded_from: str
    skill_root: str | None = None
    user_invocable: bool = True


class SkillInstallResponse(BaseModel):
    name: str
    path: str
    overwritten: bool


class HealthResponse(BaseModel):
    status: str
    provider: str
    provider_configured: bool
    skills_dir: str
    runtime_root: str
    workspace_root: str


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str | None = None
    max_turns: int = Field(default=20, ge=1, le=100)


class ChatResponse(BaseModel):
    session_id: str
    workspace_dir: str
    response: str
    usage: dict[str, Any] | None
    num_turns: int
    history: list[dict[str, Any]]


class HistoryResponse(BaseModel):
    session_id: str
    provider: str
    model: str
    workspace_dir: str
    created_at: str
    updated_at: str
    history: list[dict[str, Any]]


@dataclass
class RuntimeSession:
    session_id: str
    provider: str
    model: str
    workspace_dir: Path
    conversation: Conversation
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "provider": self.provider,
            "model": self.model,
            "workspace_dir": str(self.workspace_dir),
            "conversation": self.conversation.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeSession":
        return cls(
            session_id=str(data["session_id"]),
            provider=str(data["provider"]),
            model=str(data["model"]),
            workspace_dir=Path(data["workspace_dir"]).expanduser().resolve(),
            conversation=Conversation.from_dict(data["conversation"]),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
        )


class AgentRuntimeService:
    def __init__(self) -> None:
        self.skills_dir = _default_skills_dir()
        self.runtime_root = _runtime_root()
        self.sessions_dir = self.runtime_root / "sessions"
        self.workspace_root = _workspace_root()
        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        os.environ["CLAWD_SKILLS_DIR"] = str(self.skills_dir)

    def health(self) -> HealthResponse:
        provider = get_default_provider()
        configured = False
        try:
            configured = bool(get_provider_config(provider).get("api_key"))
        except Exception:
            configured = False
        return HealthResponse(
            status="ok",
            provider=provider,
            provider_configured=configured,
            skills_dir=str(self.skills_dir),
            runtime_root=str(self.runtime_root),
            workspace_root=str(self.workspace_root),
        )

    def list_skills(self) -> list[SkillInfo]:
        skills = sorted(
            get_all_skills(user_skills_dir=self.skills_dir),
            key=lambda skill: skill.name.lower(),
        )
        return [
            SkillInfo(
                name=skill.name,
                description=skill.description,
                loaded_from=skill.loaded_from,
                skill_root=skill.skill_root,
                user_invocable=skill.user_invocable,
            )
            for skill in skills
        ]

    def install_skill(self, skill_name: str, zip_bytes: bytes, *, overwrite: bool) -> SkillInstallResponse:
        clean_name = self._validate_skill_name(skill_name)
        if len(zip_bytes) > MAX_ZIP_BYTES:
            raise HTTPException(status_code=413, detail="skill zip is too large")

        target = (self.skills_dir / clean_name).resolve()
        self._ensure_child(target, self.skills_dir)
        if target.exists() and not overwrite:
            raise HTTPException(status_code=409, detail=f"skill already exists: {clean_name}")

        with tempfile.TemporaryDirectory(prefix="clawd-skill-") as tmp:
            tmp_path = Path(tmp)
            zip_path = tmp_path / "skill.zip"
            zip_path.write_bytes(zip_bytes)
            extract_dir = tmp_path / "extract"
            extract_dir.mkdir()
            self._safe_extract_zip(zip_path, extract_dir)
            skill_root = self._find_single_skill_root(extract_dir)

            install_tmp = self.skills_dir / f".{clean_name}.{uuid4().hex}.tmp"
            if install_tmp.exists():
                shutil.rmtree(install_tmp)
            try:
                self._copy_skill_tree(skill_root, install_tmp)
                self._replace_directory(install_tmp, target)
            except OSError as exc:
                if install_tmp.exists():
                    shutil.rmtree(install_tmp, ignore_errors=True)
                raise HTTPException(
                    status_code=500,
                    detail=f"failed to install skill into {self.skills_dir}: {exc}",
                ) from exc

        return SkillInstallResponse(name=clean_name, path=str(target), overwritten=overwrite)

    def chat(self, request: ChatRequest) -> ChatResponse:
        session_id = request.session_id or uuid4().hex
        self._validate_session_id(session_id)
        lock = self._session_lock(session_id)
        with lock:
            session = self._load_or_create_session(session_id)
            provider = self._build_provider(session.provider)
            registry = build_default_registry()
            context = ToolContext(workspace_root=session.workspace_dir, cwd=session.workspace_dir)
            context.config = load_config().get("session", {})
            context.permission_handler = self._deny_permission_request

            prompt = self._expand_skill_slash(request.message, registry, context)
            session.conversation.add_user_message(prompt)
            result = run_agent_loop(
                conversation=session.conversation,
                provider=provider,
                tool_registry=registry,
                tool_context=context,
                max_turns=request.max_turns,
                stream=False,
                verbose=False,
            )
            session.updated_at = _utc_now()
            session.model = getattr(provider, "model", None) or session.model
            self._save_session(session)
            return ChatResponse(
                session_id=session.session_id,
                workspace_dir=str(session.workspace_dir),
                response=result.response_text,
                usage=result.usage,
                num_turns=result.num_turns,
                history=session.conversation.get_messages(),
            )

    def history(self, session_id: str) -> HistoryResponse:
        self._validate_session_id(session_id)
        session = self._load_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
        return HistoryResponse(
            session_id=session.session_id,
            provider=session.provider,
            model=session.model,
            workspace_dir=str(session.workspace_dir),
            created_at=session.created_at,
            updated_at=session.updated_at,
            history=session.conversation.get_messages(),
        )

    def _build_provider(self, provider_name: str):
        try:
            config = get_provider_config(provider_name)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"provider is not configured: {provider_name}") from exc
        if not config.get("api_key"):
            raise HTTPException(status_code=400, detail=f"api key is not configured for provider: {provider_name}")
        provider_class = get_provider_class(provider_name)
        return provider_class(
            api_key=config["api_key"],
            base_url=config.get("base_url"),
            model=config.get("default_model"),
        )

    def _load_or_create_session(self, session_id: str) -> RuntimeSession:
        existing = self._load_session(session_id)
        if existing is not None:
            existing.workspace_dir.mkdir(parents=True, exist_ok=True)
            return existing
        provider = get_default_provider()
        config = get_provider_config(provider)
        model = str(config.get("default_model") or "")
        workspace_dir = (self.workspace_root / session_id).resolve()
        self._ensure_child(workspace_dir, self.workspace_root)
        workspace_dir.mkdir(parents=True, exist_ok=True)
        now = _utc_now()
        return RuntimeSession(
            session_id=session_id,
            provider=provider,
            model=model,
            workspace_dir=workspace_dir,
            conversation=Conversation(max_history=load_config().get("session", {}).get("max_history", 100)),
            created_at=now,
            updated_at=now,
        )

    def _load_session(self, session_id: str) -> RuntimeSession | None:
        path = self.sessions_dir / f"{session_id}.json"
        if not path.exists():
            return None
        try:
            return RuntimeSession.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"failed to load session: {session_id}") from exc

    def _save_session(self, session: RuntimeSession) -> None:
        path = self.sessions_dir / f"{session.session_id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(session.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def _session_lock(self, session_id: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(session_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[session_id] = lock
            return lock

    def _expand_skill_slash(self, message: str, registry, context: ToolContext) -> str:
        stripped = message.strip()
        if not stripped.startswith("/") or stripped in {"/", "/help"}:
            return message
        body = stripped[1:]
        skill_name, _, args = body.partition(" ")
        try:
            result = registry.dispatch(
                ToolCall(name="Skill", input={"skill": skill_name, "args": args}),
                context,
            )
        except Exception:
            return message
        payload = result.output if isinstance(result.output, dict) else {}
        prompt = payload.get("prompt")
        if result.is_error or not payload.get("success") or not isinstance(prompt, str) or not prompt.strip():
            return message
        return prompt

    @staticmethod
    def _deny_permission_request(tool_name: str, message: str, suggestion: str | None) -> tuple[bool, bool]:
        return False, False

    @staticmethod
    def _validate_skill_name(skill_name: str) -> str:
        clean = skill_name.strip()
        if not clean or clean in {".", ".."}:
            raise HTTPException(status_code=400, detail="invalid skill name")
        if "/" in clean or "\\" in clean:
            raise HTTPException(status_code=400, detail="skill name must not contain path separators")
        return clean

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        if not session_id or any(ch in session_id for ch in "/\\"):
            raise HTTPException(status_code=400, detail="invalid session_id")
        if session_id in {".", ".."}:
            raise HTTPException(status_code=400, detail="invalid session_id")

    @staticmethod
    def _ensure_child(path: Path, parent: Path) -> None:
        try:
            path.resolve().relative_to(parent.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="path escapes configured root") from exc

    def _safe_extract_zip(self, zip_path: Path, extract_dir: Path) -> None:
        try:
            with zipfile.ZipFile(zip_path) as zf:
                infos = zf.infolist()
                if not infos:
                    raise HTTPException(status_code=400, detail="skill zip is empty")
                if len(infos) > MAX_ZIP_MEMBERS:
                    raise HTTPException(status_code=400, detail="skill zip contains too many files")
                total_size = 0
                for info in infos:
                    name = info.filename
                    if not name or name.startswith("/") or "\\" in name:
                        raise HTTPException(status_code=400, detail=f"unsafe zip path: {name}")
                    target = (extract_dir / name).resolve()
                    self._ensure_child(target, extract_dir)
                    if any(part == ".." for part in Path(name).parts):
                        raise HTTPException(status_code=400, detail=f"unsafe zip path: {name}")
                    mode = info.external_attr >> 16
                    if stat.S_ISLNK(mode):
                        raise HTTPException(status_code=400, detail=f"zip symlinks are not allowed: {name}")
                    total_size += info.file_size
                    if total_size > MAX_EXTRACTED_BYTES:
                        raise HTTPException(status_code=413, detail="skill zip expands to too much data")
                zf.extractall(extract_dir)
        except zipfile.BadZipFile as exc:
            raise HTTPException(status_code=400, detail="invalid zip file") from exc

    @staticmethod
    def _find_single_skill_root(extract_dir: Path) -> Path:
        skill_files = [path for path in extract_dir.rglob("SKILL.md") if path.is_file()]
        if len(skill_files) != 1:
            raise HTTPException(status_code=400, detail="zip must contain exactly one SKILL.md")
        return skill_files[0].parent

    def _copy_skill_tree(self, source: Path, target: Path) -> None:
        """Copy skill files without metadata operations that can fail on pod filesystems."""
        self._ensure_child(target, self.skills_dir)
        target.mkdir(parents=True, exist_ok=False)
        for root, dirs, files in os.walk(source):
            root_path = Path(root)
            relative_root = root_path.relative_to(source)
            target_root = target / relative_root
            for directory in dirs:
                (target_root / directory).mkdir(exist_ok=True)
            for filename in files:
                src = root_path / filename
                dst = target_root / filename
                shutil.copyfile(src, dst)

    @staticmethod
    def _replace_directory(source: Path, target: Path) -> None:
        backup: Path | None = None
        if target.exists():
            backup = target.with_name(f".{target.name}.{uuid4().hex}.bak")
            os.replace(target, backup)
        try:
            os.replace(source, target)
        except Exception:
            if backup is not None and backup.exists() and not target.exists():
                os.replace(backup, target)
            raise
        if backup is not None and backup.exists():
            shutil.rmtree(backup)


service = AgentRuntimeService()
app = FastAPI(title="Clawd Agent Runtime", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(INDEX_HTML)


@app.head("/", include_in_schema=False)
def index_head() -> FileResponse:
    return FileResponse(INDEX_HTML)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return service.health()


@app.get("/skills", response_model=list[SkillInfo])
def list_skills() -> list[SkillInfo]:
    return service.list_skills()


@app.put("/skills/{skill_name}", response_model=SkillInstallResponse)
def upload_skill(
    skill_name: str,
    overwrite: bool = Query(default=False),
    file: UploadFile = File(...),
) -> SkillInstallResponse:
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="file must be a .zip archive")
    data = file.file.read()
    return service.install_skill(skill_name, data, overwrite=overwrite)


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    return service.chat(request)


@app.get("/sessions/{session_id}/history", response_model=HistoryResponse)
def history(session_id: str) -> HistoryResponse:
    return service.history(session_id)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=DEFAULT_HOST, port=DEFAULT_PORT)
