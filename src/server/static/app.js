const state = {
  sessionId: null,
  sending: false,
};

const $ = (id) => document.getElementById(id);

const elements = {
  globalStatus: $("global-status"),
  healthButton: $("health-button"),
  healthDetails: $("health-details"),
  refreshSkillsButton: $("refresh-skills-button"),
  skillForm: $("skill-form"),
  skillName: $("skill-name"),
  skillFile: $("skill-file"),
  skillOverwrite: $("skill-overwrite"),
  uploadSkillButton: $("upload-skill-button"),
  skillUploadStatus: $("skill-upload-status"),
  skillsList: $("skills-list"),
  sessionLabel: $("session-label"),
  maxTurns: $("max-turns"),
  newChatButton: $("new-chat-button"),
  chatLog: $("chat-log"),
  chatForm: $("chat-form"),
  chatInput: $("chat-input"),
  sendButton: $("send-button"),
};

function setGlobalStatus(text, kind = "muted") {
  elements.globalStatus.textContent = text;
  elements.globalStatus.className = `status-pill status-${kind}`;
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof payload === "object" && payload !== null ? payload.detail : payload;
    throw new Error(detail || `Request failed with ${response.status}`);
  }
  return payload;
}

function renderDetails(target, rows) {
  target.replaceChildren(
    ...rows.map(([label, value]) => {
      const row = document.createElement("div");
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = label;
      dd.textContent = value == null || value === "" ? "-" : String(value);
      row.append(dt, dd);
      return row;
    })
  );
}

async function checkHealth() {
  elements.healthButton.disabled = true;
  setGlobalStatus("Checking health", "muted");
  try {
    const health = await requestJson("/health");
    renderDetails(elements.healthDetails, [
      ["Status", health.status],
      ["Provider", health.provider],
      ["Configured", health.provider_configured ? "Yes" : "No"],
      ["Skills dir", health.skills_dir],
      ["Runtime root", health.runtime_root],
      ["Workspace root", health.workspace_root],
    ]);
    setGlobalStatus(health.provider_configured ? "Runtime ready" : "Provider not configured", health.provider_configured ? "ok" : "error");
  } catch (error) {
    renderDetails(elements.healthDetails, [["Error", error.message]]);
    setGlobalStatus("Health check failed", "error");
  } finally {
    elements.healthButton.disabled = false;
  }
}

function renderSkills(skills) {
  if (!skills.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No skills installed";
    elements.skillsList.replaceChildren(empty);
    return;
  }

  const nodes = skills.map((skill) => {
    const item = document.createElement("article");
    item.className = "skill-item";

    const title = document.createElement("div");
    title.className = "skill-title";

    const name = document.createElement("span");
    name.textContent = skill.name;
    const source = document.createElement("span");
    source.textContent = skill.loaded_from;
    title.append(name, source);

    const description = document.createElement("div");
    description.className = "skill-description";
    description.textContent = skill.description || "No description";

    const meta = document.createElement("div");
    meta.className = "skill-meta";
    meta.textContent = skill.skill_root || "";

    item.append(title, description, meta);
    return item;
  });
  elements.skillsList.replaceChildren(...nodes);
}

async function loadSkills() {
  elements.refreshSkillsButton.disabled = true;
  try {
    const skills = await requestJson("/skills");
    renderSkills(skills);
  } catch (error) {
    const message = document.createElement("div");
    message.className = "message error";
    message.textContent = `Failed to load skills: ${error.message}`;
    elements.skillsList.replaceChildren(message);
  } finally {
    elements.refreshSkillsButton.disabled = false;
  }
}

async function uploadSkill(event) {
  event.preventDefault();
  const name = elements.skillName.value.trim();
  const file = elements.skillFile.files[0];
  if (!name || !file) {
    elements.skillUploadStatus.textContent = "Skill name and zip file are required.";
    return;
  }

  const formData = new FormData();
  formData.append("file", file);
  const overwrite = elements.skillOverwrite.checked ? "true" : "false";

  elements.uploadSkillButton.disabled = true;
  elements.skillUploadStatus.textContent = "Uploading...";
  try {
    const result = await requestJson(`/skills/${encodeURIComponent(name)}?overwrite=${overwrite}`, {
      method: "PUT",
      body: formData,
    });
    elements.skillUploadStatus.textContent = `Installed ${result.name}${result.overwritten ? " with overwrite" : ""}.`;
    elements.skillForm.reset();
    await loadSkills();
  } catch (error) {
    elements.skillUploadStatus.textContent = error.message.includes("already exists")
      ? "Skill already exists. Enable overwrite and upload again."
      : error.message;
  } finally {
    elements.uploadSkillButton.disabled = false;
  }
}

function updateSessionLabel() {
  elements.sessionLabel.textContent = state.sessionId ? `Session ${state.sessionId}` : "No active session";
}

function ensureChatPlaceholder() {
  if (elements.chatLog.children.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "Send a message to start a runtime session.";
    elements.chatLog.append(empty);
  }
}

function clearChatPlaceholder() {
  const empty = elements.chatLog.querySelector(".empty-state");
  if (empty) {
    empty.remove();
  }
}

function appendMessage(role, body, meta = "") {
  clearChatPlaceholder();
  const message = document.createElement("article");
  message.className = `message ${role}`;

  const roleNode = document.createElement("div");
  roleNode.className = "message-role";
  roleNode.textContent = role;

  const bodyNode = document.createElement("div");
  bodyNode.className = "message-body";
  bodyNode.textContent = body;

  message.append(roleNode, bodyNode);
  if (meta) {
    const metaNode = document.createElement("div");
    metaNode.className = "message-meta";
    metaNode.textContent = meta;
    message.append(metaNode);
  }
  elements.chatLog.append(message);
  elements.chatLog.scrollTop = elements.chatLog.scrollHeight;
  return message;
}

function appendError(body) {
  appendMessage("error", body);
}

function normalizeMaxTurns() {
  const value = Number.parseInt(elements.maxTurns.value, 10);
  if (Number.isNaN(value)) {
    return 20;
  }
  return Math.min(100, Math.max(1, value));
}

async function sendMessage(event) {
  event.preventDefault();
  if (state.sending) {
    return;
  }
  const text = elements.chatInput.value.trim();
  if (!text) {
    return;
  }

  state.sending = true;
  elements.sendButton.disabled = true;
  elements.chatInput.disabled = true;
  appendMessage("user", text);
  elements.chatInput.value = "";
  const pending = appendMessage("assistant", "Waiting for runtime...");

  try {
    const payload = {
      message: text,
      max_turns: normalizeMaxTurns(),
    };
    if (state.sessionId) {
      payload.session_id = state.sessionId;
    }

    const result = await requestJson("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.sessionId = result.session_id;
    updateSessionLabel();
    pending.querySelector(".message-body").textContent = result.response || "";
    const usage = result.usage ? `, usage ${JSON.stringify(result.usage)}` : "";
    const meta = pending.querySelector(".message-meta") || document.createElement("div");
    meta.className = "message-meta";
    meta.textContent = `Turns ${result.num_turns}${usage}`;
    if (!meta.parentNode) {
      pending.append(meta);
    }
  } catch (error) {
    pending.remove();
    appendError(error.message);
  } finally {
    state.sending = false;
    elements.sendButton.disabled = false;
    elements.chatInput.disabled = false;
    elements.chatInput.focus();
  }
}

function newChat() {
  state.sessionId = null;
  elements.chatLog.replaceChildren();
  updateSessionLabel();
  ensureChatPlaceholder();
  elements.chatInput.focus();
}

elements.healthButton.addEventListener("click", checkHealth);
elements.refreshSkillsButton.addEventListener("click", loadSkills);
elements.skillForm.addEventListener("submit", uploadSkill);
elements.chatForm.addEventListener("submit", sendMessage);
elements.newChatButton.addEventListener("click", newChat);
elements.chatInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
    elements.chatForm.requestSubmit();
  }
});

updateSessionLabel();
ensureChatPlaceholder();
checkHealth();
loadSkills();
