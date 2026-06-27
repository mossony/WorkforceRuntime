let initialized = false;

export function initializeDashboard() {
  if (initialized) return;
  initialized = true;

  /* ===== New homepage design glue ===== */
  let sidebarCollapsed = false;
  let sidebarSearchTerm = "";
  let lastAllTasks = [];
  let lastTasks = [];
  let lastHumanReports = [];
  let expandedReportId = "";
  let pipelineState = { phase: "idle", state: "idle" };

  function fmt(n) {
    if (n == null || n === "" || (typeof n === "number" && !isFinite(n))) return "-";
    const num = Number(n);
    if (!isFinite(num)) return esc(String(n));
    if (Math.abs(num) >= 1000000) return (num / 1000000).toFixed(num % 1000000 === 0 ? 0 : 1) + "M";
    if (Math.abs(num) >= 1000) return (num / 1000).toFixed(num % 1000 === 0 ? 0 : 1) + "k";
    return String(num);
  }

  function onSidebarSearch(value) {
    sidebarSearchTerm = (value || "").toLowerCase();
    renderSidebarTasks(lastAllTasks, lastTasks);
  }

  function onGoalInput(el) {
    const count = document.getElementById("input-char-count");
    const len = (el.value || "").length;
    if (count) count.textContent = len ? `${len} chars` : "";
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }

  function onGoalFocus() {
    const card = document.getElementById("input-card");
    if (card) { card.classList.remove("idle"); card.classList.add("focused"); }
  }

  function onGoalBlur() {
    const card = document.getElementById("input-card");
    const el = document.getElementById("designed-task-goal");
    if (card && !(el && el.value)) { card.classList.remove("focused"); card.classList.add("idle"); }
    else if (card) { card.classList.remove("focused"); card.classList.add("idle"); }
  }

  function applyExample(text) {
    const el = document.getElementById("designed-task-goal");
    if (!el) return;
    el.value = text;
    el.focus();
    onGoalInput(el);
  }

  function renderComposerMode() {
    const selected = Boolean(selectedTaskId);
    const card = document.getElementById("input-card");
    const goal = document.getElementById("designed-task-goal");
    if (card) card.classList.toggle("task-chat-mode", selected);
    if (goal) {
      goal.placeholder = selected ? "Chat with your CEO..." : "Describe the goal for your workforce...";
    }
  }

  function metricColorFor(kind) {
    if (kind === "good") return "#3f7d57";
    if (kind === "bad") return "#b3524b";
    if (kind === "warn") return "#a8742a";
    return "#1c1b19";
  }

  function dotColorFor(kind) {
    if (kind === "good") return "#4a8b63";
    if (kind === "bad") return "#b3524b";
    if (kind === "warn") return "#b07d2f";
    return "#cfcabf";
  }

  function renderStatusBar(data) {
    const host = document.getElementById("status-metrics");
    if (!host) return;
    const company = (data && data.company) || {};
    const nameEl = document.getElementById("company-name-status");
    if (nameEl) nameEl.textContent = company.name || "Workforce Runtime";
    const initialsEl = document.getElementById("company-initials");
    if (initialsEl) {
      const initials = (company.name || "WR").split(/\s+/).map(w => w[0]).filter(Boolean).slice(0, 2).join("").toUpperCase();
      initialsEl.textContent = initials || "WR";
    }
    const tasks = (data && data.tasks) || [];
    const agentsList = (data && data.agents) || [];
    const budget = (data && data.budget) || {};
    const active = tasks.filter(t => ["assigned", "in_progress", "blocked"].includes(t.status)).length;
    const completed = tasks.filter(t => t.status === "completed").length;
    const failed = tasks.filter(t => t.status === "failed").length;
    const busyAgents = agentsList.filter(a => ["busy", "assigned", "in_progress", "running"].includes(a.status)).length;
    const tokensUsed = budget.tokens_used || 0;
    const tokenLimit = budget.token_budget_limit || 0;
    const metrics = [
      { label: "Agents", value: `${agentsList.length}${budget.headcount_limit ? "/" + budget.headcount_limit : ""}`, kind: "neutral" },
      { label: "Working", value: busyAgents, kind: busyAgents ? "warn" : "neutral" },
      { label: "Active", value: active, kind: active ? "warn" : "neutral" },
      { label: "Done", value: completed, kind: completed ? "good" : "neutral" },
      { label: "Failed", value: failed, kind: failed ? "bad" : "neutral" },
      { label: "Tokens", value: tokenLimit ? `${fmt(tokensUsed)}/${fmt(tokenLimit)}` : fmt(tokensUsed), kind: "neutral" },
      { label: "Events", value: fmt((data && (data.agent_output || data.worker_output) || []).length), kind: "neutral" },
    ];
    host.innerHTML = metrics.map(m => `
      <div class="sbar-metric">
        <div class="sbar-metric-lrow">
          <span class="sbar-metric-dot" style="background:${dotColorFor(m.kind)};"></span>
          <span class="sbar-metric-label">${esc(m.label)}</span>
        </div>
        <span class="sbar-metric-val" style="color:${metricColorFor(m.kind)};">${m.value}</span>
      </div>`).join("");
    const sub = document.getElementById("sidebar-sub");
    if (sub) sub.textContent = `${agentsList.length} agent${agentsList.length === 1 ? "" : "s"}`;
  }

  function taskStatusKind(status) {
    if (status === "completed") return "good";
    if (status === "failed" || status === "timed_out") return "bad";
    if (["assigned", "in_progress", "blocked", "running"].includes(status)) return "warn";
    return "neutral";
  }

  function renderSidebarTasks(allTasks, tasks) {
    lastAllTasks = allTasks || [];
    lastTasks = tasks || [];
    const host = document.getElementById("sidebar-tasks");
    if (!host) return;
    const source = (lastAllTasks.length ? lastAllTasks : lastTasks) || [];
    let list = source.slice();
    if (sidebarSearchTerm) {
      list = list.filter(t => `${t.task_id} ${t.title || ""}`.toLowerCase().includes(sidebarSearchTerm));
    }
    const countEl = document.getElementById("sidebar-task-count");
    if (countEl) countEl.textContent = String(list.length);
    const groups = { active: [], completed: [], other: [] };
    for (const t of list) {
      if (["assigned", "in_progress", "blocked", "running"].includes(t.status)) groups.active.push(t);
      else if (t.status === "completed") groups.completed.push(t);
      else groups.other.push(t);
    }
    const renderGroup = (label, items) => {
      if (!items.length) return "";
      return `<div class="task-group-label">${esc(label)}</div>` + items.map(t => {
        const kind = taskStatusKind(t.status);
        const selected = t.task_id === selectedTaskId ? " selected" : "";
        const title = t.title || t.task_id;
        return `<div class="task-item-row${selected}">
          <button class="task-item${selected}" data-action="select-task" data-task-id="${esc(t.task_id)}" title="${esc(title)}">
            <span class="task-item-dot" style="background:${dotColorFor(kind)};border-radius:50%;"></span>
            <span class="task-item-name">${esc(title)}</span>
          </button>
          <span class="task-item-actions">
            <button class="task-item-action" data-action="rename-task" data-task-id="${esc(t.task_id)}" data-title="${esc(title)}" title="Rename task" aria-label="Rename task">
              <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M11.5 2.5l2 2L6 12l-3 .8.8-3z"></path></svg>
            </button>
            <button class="task-item-action" data-action="delete-task" data-task-id="${esc(t.task_id)}" data-title="${esc(title)}" title="Delete task" aria-label="Delete task">
              <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M3 4.5h10M6.5 4.5V3h3v1.5M5 4.5l.5 8h5l.5-8"></path></svg>
            </button>
          </span>
        </div>`;
      }).join("");
    };
    const html = renderGroup("Active", groups.active) + renderGroup("Completed", groups.completed) + renderGroup("Other", groups.other);
    host.innerHTML = html || `<div class="task-group-label">No tasks</div>`;
  }

  async function renameTask(taskId, title) {
    if (!taskId) return;
    const res = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/rename`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || res.statusText);
    }
    await refresh().catch(err => console.error(err));
  }

  function startInlineRename(row, taskId, currentTitle) {
    if (!taskId || row.querySelector(".task-item-rename-input")) return;
    const button = row.querySelector(".task-item");
    const input = document.createElement("input");
    input.className = "task-item-rename-input";
    input.value = currentTitle;
    input.setAttribute("aria-label", "Task title");
    if (button) button.style.display = "none";
    row.insertBefore(input, row.firstChild);
    row.classList.add("renaming");
    input.focus();
    input.select();
    let settled = false;
    const finish = async (commit) => {
      if (settled) return;
      settled = true;
      const next = input.value.trim();
      if (commit && next && next !== currentTitle) {
        try {
          await renameTask(taskId, next);
          return; // refresh() re-renders the sidebar
        } catch (err) {
          console.error(err);
        }
      }
      renderSidebarTasks(lastAllTasks, lastTasks); // revert / cancel
    };
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); finish(true); }
      else if (event.key === "Escape") { event.preventDefault(); finish(false); }
    });
    input.addEventListener("blur", () => finish(true));
  }

  async function deleteTask(taskId) {
    if (!taskId) return;
    const res = await fetch(`/api/tasks/${encodeURIComponent(taskId)}`, { method: "DELETE" });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      window.alert(`Delete failed: ${data.error || res.statusText}`);
      return;
    }
    if (selectedTaskId === taskId) {
      selectedTaskId = "";
      selectedAgentId = null;
      currentTaskScope = new Set();
    }
    await refresh().catch(err => console.error(err));
  }

  function setSettingsStatus(message, kind = "") {
    const el = document.getElementById("settings-status");
    if (!el) return;
    el.textContent = message || "idle";
    el.dataset.kind = kind;
  }

  function renderSettingsNav() {
    document.querySelectorAll(".sb-settings-button").forEach(button => {
      button.classList.toggle("active", button.dataset.settingsView === settingsView);
    });
  }

  function closeSettingsView() {
    settingsView = "";
    document.body.classList.remove("settings-open");
    const section = document.getElementById("settings-view");
    if (section) section.hidden = true;
    renderSettingsNav();
  }

  function openSettingsView(view) {
    settingsView = view === "skills" ? "skills" : "mcp";
    document.body.classList.add("settings-open");
    const section = document.getElementById("settings-view");
    if (section) section.hidden = false;
    renderSettingsNav();
    renderSettingsView();
    const scroll = document.getElementById("main-scroll");
    if (scroll) scroll.scrollTo({ top: 0, behavior: "smooth" });
    loadSettingsView(settingsView).catch(err => setSettingsStatus(String(err), "error"));
  }

  async function loadSettingsView(view) {
    setSettingsStatus("loading...");
    const endpoint = view === "skills" ? "/api/settings/skills" : "/api/settings/mcp";
    const res = await fetch(endpoint, { cache: "no-store" });
    const data = await res.json();
    if (!res.ok || data.ok === false) throw new Error(data.error || `failed to load ${view}`);
    settingsData[view] = data;
    renderSettingsView();
    setSettingsStatus(`loaded ${view}`);
  }

  function renderSettingsView() {
    const section = document.getElementById("settings-view");
    if (!section || !settingsView) return;
    const title = document.getElementById("settings-title");
    const subtitle = document.getElementById("settings-subtitle");
    if (title) title.textContent = settingsView === "skills" ? "Skills" : "MCP Servers";
    if (subtitle) {
      subtitle.textContent = settingsView === "skills"
        ? "Create centrally managed native Codex and Claude Code skills, then assign them globally or to selected workers."
        : "Register external MCP servers once, wrap them centrally, and make them available to selected workers.";
    }
    const host = document.getElementById("settings-content");
    if (!host) return;
    host.innerHTML = settingsView === "skills" ? renderSkillsSettings() : renderMcpSettings();
  }

  function renderMcpForm(mode, server = {}) {
    const authType = server.auth?.type || "none";
    const transport = server.transport || "http";
    const option = (value, label, selectedValue) => `<option value="${esc(value)}" ${value === selectedValue ? "selected" : ""}>${esc(label)}</option>`;
    return `<div class="settings-form mcp-form ${mode === "edit" ? "settings-inline-editor" : ""}" data-mcp-form-mode="${esc(mode)}" data-server-id="${esc(server.id || "")}">
      <div class="settings-form-row">
        <label>Server id<input data-field="id" value="${esc(server.id || "")}" placeholder="github_copilot"></label>
        <label>Tool prefix<input data-field="tool_prefix" value="${esc(server.tool_prefix || server.id || "")}" placeholder="github"></label>
      </div>
      <label>HTTP URL<input data-field="url" value="${esc(server.url || "")}" placeholder="https://api.example.com/mcp/"></label>
      <div class="settings-form-row">
        <label>Transport<select data-field="transport">
          ${option("http", "http", transport)}
          ${option("sse", "sse", transport)}
        </select></label>
        <label>Auth<select data-field="auth_type">
          ${option("none", "none", authType)}
          ${option("bearer", "bearer env", authType)}
          ${option("oauth", "oauth", authType)}
        </select></label>
      </div>
      <div class="settings-form-row">
        <label>Bearer env<input data-field="token_env" value="${esc(server.auth?.token_env || "")}" placeholder="GITHUB_PAT_TOKEN"></label>
        <label>Timeout seconds<input data-field="timeout_seconds" type="number" min="1" value="${esc(server.timeout_seconds || 30)}"></label>
      </div>
      <div class="settings-form-row">
        <label>Allowed agents<input data-field="allowed_agent_ids" value="${esc((server.allowed_agent_ids || ["*"]).join(", "))}"></label>
        <label>Allowed tools<input data-field="allowed_tools" value="${esc((server.allowed_tools || ["*"]).join(", "))}"></label>
      </div>
      <div class="settings-form-row">
        <label>Allowed roles<input data-field="allowed_roles" value="${esc((server.allowed_roles || []).join(", "))}" placeholder="Software Engineer, Researcher"></label>
        <label>Allowed worker types<input data-field="allowed_worker_types" value="${esc((server.allowed_worker_types || []).join(", "))}" placeholder="codex, claude_code"></label>
      </div>
      <div class="settings-form-row">
        <label><span>Enabled</span><input data-field="enabled" type="checkbox" ${server.enabled === false ? "" : "checked"}></label>
        <label><span>Queue tool calls</span><input data-field="queue_enabled" type="checkbox" ${server.queue?.enabled === false ? "" : "checked"}></label>
      </div>
      <div class="settings-actions">
        <button class="settings-secondary" data-action="${mode === "edit" ? "toggle-mcp-edit" : "toggle-settings-add"}" ${mode === "edit" ? `data-server-id="${esc(server.id || "")}"` : `data-settings-add-view="mcp"`}>Cancel</button>
        <button class="settings-primary" data-action="save-mcp-server">Save MCP Server</button>
      </div>
    </div>`;
  }

  function renderMcpSettings() {
    const data = settingsData.mcp || {};
    const servers = data.servers || [];
    const cards = servers.length ? servers.map(server => {
      const authType = server.auth?.type || "none";
      const enabled = server.enabled !== false;
      const queueEnabled = server.queue?.enabled !== false;
      const isEditing = editingMcpServerId === String(server.id || "");
      return `<div class="settings-list-item settings-expandable-item ${isEditing ? "editing" : ""}">
        <div class="settings-list-head">
          <div style="min-width:0;">
            <div class="settings-list-title">${esc(server.id || "unnamed")}</div>
            <div class="settings-list-sub">${esc(server.url || "")}</div>
          </div>
          <div class="settings-row-actions">
            <button class="settings-secondary" data-action="toggle-mcp-edit" data-server-id="${esc(server.id || "")}">${isEditing ? "Close" : "Edit"}</button>
            <button class="settings-danger" data-action="delete-mcp-server" data-server-id="${esc(server.id || "")}">Delete</button>
          </div>
        </div>
        <div class="settings-badges">
          <span class="settings-badge ${enabled ? "good" : "off"}">${enabled ? "enabled" : "disabled"}</span>
          <span class="settings-badge">${esc(server.transport || "http")}</span>
          <span class="settings-badge">${esc(authType)}</span>
          <span class="settings-badge ${queueEnabled ? "good" : "off"}">queue ${queueEnabled ? "on" : "off"}</span>
          <span class="settings-badge">prefix ${esc(server.tool_prefix || server.id || "-")}</span>
        </div>
        ${isEditing ? renderMcpForm("edit", server) : ""}
      </div>`;
    }).join("") : `<div class="settings-empty">No external MCP servers configured.</div>`;
    const addPanel = settingsAddOpen.mcp ? `
      <div class="settings-expand-panel">
        <div class="settings-expand-head">
          <div>
            <h2>Add or update server</h2>
            <div class="settings-list-sub">Save the server into the central runtime config. Workers receive the wrapped MCP clone through Workforce Runtime.</div>
          </div>
          <button class="settings-secondary" data-action="toggle-settings-add" data-settings-add-view="mcp">Close</button>
        </div>
        ${renderMcpForm("add")}
      </div>` : "";
    return `<div class="settings-stack">
      <div class="settings-toolbar">
        <div>
          <h2>Configured MCP servers</h2>
          <div class="settings-list-sub">${servers.length} server${servers.length === 1 ? "" : "s"} configured</div>
        </div>
        <div class="settings-toolbar-actions">
          <button class="settings-secondary" data-action="refresh-settings">Refresh</button>
          <button class="settings-primary" data-action="toggle-settings-add" data-settings-add-view="mcp">${settingsAddOpen.mcp ? "Close" : "Add MCP Server"}</button>
        </div>
      </div>
      ${addPanel}
      <div class="settings-list">${cards}</div>
    </div>`;
  }

  function renderSkillsSettings() {
    const data = settingsData.skills || {};
    const skills = data.skills || [];
    const assignments = data.assignments || [];
    const assignmentCounts = assignments.reduce((acc, item) => {
      acc[item.skill_id] = (acc[item.skill_id] || 0) + 1;
      return acc;
    }, {});
    const skillCards = skills.length ? skills.map(skill => `
      <div class="settings-list-item">
        <div class="settings-list-head">
          <div style="min-width:0;">
            <div class="settings-list-title">${esc(skill.name)}</div>
            <div class="settings-list-sub">${esc(skill.description)}</div>
          </div>
          <span class="settings-badge ${["approved", "published"].includes(skill.status) ? "good" : "off"}">${esc(skill.status)}</span>
        </div>
        <div class="settings-badges">
          <span class="settings-badge">${esc(skill.skill_id)}</span>
          <span class="settings-badge">${esc((skill.provider_targets || []).join(", ") || "-")}</span>
          <span class="settings-badge">${(skill.files || []).length} files</span>
          <span class="settings-badge">${assignmentCounts[skill.skill_id] || 0} assignments</span>
        </div>
      </div>`).join("") : `<div class="settings-empty">No skills registered yet.</div>`;
    const options = skills.map(skill => `<option value="${esc(skill.skill_id)}">${esc(skill.name)} - ${esc(skill.skill_id)}</option>`).join("");
    const assignmentCards = assignments.length ? assignments.slice(-8).reverse().map(item => `
      <div class="settings-list-item">
        <div class="settings-list-title">${esc(item.target_type)}:${esc(item.target_id)}</div>
        <div class="settings-list-sub">${esc(item.skill_id)}</div>
        <div class="settings-badges">
          <span class="settings-badge ${item.enabled ? "good" : "off"}">${item.enabled ? "enabled" : "disabled"}</span>
          <span class="settings-badge ${item.materialize_on_start ? "good" : "off"}">materialize ${item.materialize_on_start ? "on" : "off"}</span>
        </div>
      </div>`).join("") : `<div class="settings-empty">No assignments yet.</div>`;
    const createSkillForm = `<div class="settings-form">
      <label>Name<input id="skill-name" placeholder="repo-reviewer"></label>
      <label>Description<input id="skill-description" placeholder="Review repository changes with project conventions."></label>
      <label>Instructions<textarea id="skill-instructions" placeholder="Write the SKILL.md instructions for Codex and Claude Code..."></textarea></label>
      <div class="settings-form-row">
        <label>Status<select id="skill-status"><option value="approved">approved</option><option value="draft">draft</option><option value="published">published</option><option value="archived">archived</option></select></label>
        <label>Targets<select id="skill-provider-targets" multiple size="2"><option value="codex" selected>Codex</option><option value="claude_code" selected>Claude Code</option></select></label>
      </div>
      <div class="settings-actions">
        <button class="settings-primary" data-action="create-skill">Create Skill</button>
      </div>
    </div>`;
    const assignSkillForm = `<div class="settings-form">
      <label>Skill<select id="assign-skill-id">${options || `<option value="">Create a skill first</option>`}</select></label>
      <div class="settings-form-row">
        <label>Target type<select id="assign-target-type"><option value="global">global</option><option value="agent">agent</option><option value="role">role</option><option value="department">department</option><option value="worker_type">worker_type</option></select></label>
        <label>Target id<input id="assign-target-id" value="*"></label>
      </div>
      <div class="settings-form-row">
        <label><span>Enabled</span><input id="assign-enabled" type="checkbox" checked></label>
        <label><span>Materialize on worker start</span><input id="assign-materialize" type="checkbox" checked></label>
      </div>
      <div class="settings-actions">
        <button class="settings-primary" data-action="assign-skill" ${skills.length ? "" : "disabled"}>Assign Skill</button>
      </div>
    </div>`;
    const addPanel = settingsAddOpen.skills ? `
      <div class="settings-expand-panel">
        <div class="settings-expand-head">
          <div>
            <h2>${settingsSkillAddTab === "assign" ? "Assign skill" : "Create skill"}</h2>
            <div class="settings-list-sub">${settingsSkillAddTab === "assign" ? "Attach an existing skill to all workers or a specific target." : "Create a centrally managed native Codex/Claude skill."}</div>
          </div>
          <button class="settings-secondary" data-action="toggle-settings-add" data-settings-add-view="skills">Close</button>
        </div>
        <div class="settings-tabs">
          <button class="${settingsSkillAddTab === "create" ? "active" : ""}" data-action="set-skill-add-tab" data-skill-add-tab="create">Create skill</button>
          <button class="${settingsSkillAddTab === "assign" ? "active" : ""}" data-action="set-skill-add-tab" data-skill-add-tab="assign">Assign skill</button>
        </div>
        ${settingsSkillAddTab === "assign" ? assignSkillForm : createSkillForm}
      </div>` : "";
    return `<div class="settings-stack">
      <div class="settings-toolbar">
        <div>
          <h2>Registered skills</h2>
          <div class="settings-list-sub">${skills.length} skill${skills.length === 1 ? "" : "s"} · ${assignments.length} assignment${assignments.length === 1 ? "" : "s"}</div>
        </div>
        <div class="settings-toolbar-actions">
          <button class="settings-secondary" data-action="refresh-settings">Refresh</button>
          <button class="settings-primary" data-action="toggle-settings-add" data-settings-add-view="skills">${settingsAddOpen.skills ? "Close" : "Add Skill"}</button>
        </div>
      </div>
      ${addPanel}
      <div class="settings-card settings-list-card">
        <div class="settings-list-head">
          <h2>Skills</h2>
        </div>
        <div class="settings-list">${skillCards}</div>
      </div>
      <div class="settings-card settings-list-card">
        <div class="settings-list-head">
          <h2>Assignments</h2>
        </div>
        <div class="settings-list">${assignmentCards}</div>
      </div>
    </div>`;
  }

  function csvItems(raw, fallback = []) {
    const items = raw.split(",").map(item => item.trim()).filter(Boolean);
    return items.length ? items : fallback;
  }

  function formField(form, field) {
    return form?.querySelector(`[data-field="${field}"]`);
  }

  function formValue(form, field, fallback = "") {
    return String(formField(form, field)?.value || fallback);
  }

  function formCsvValue(form, field, fallback = []) {
    return csvItems(formValue(form, field), fallback);
  }

  function formChecked(form, field) {
    return Boolean(formField(form, field)?.checked);
  }

  function clearFieldError(field) {
    if (!field) return;
    field.removeAttribute("aria-invalid");
    const label = field.closest("label");
    if (label) label.classList.remove("settings-field-invalid");
    const next = field.nextElementSibling;
    if (next?.classList?.contains("settings-field-error")) next.remove();
  }

  function clearFormValidation(form) {
    form?.querySelectorAll("input, select, textarea").forEach(clearFieldError);
  }

  function addFieldError(field, message) {
    if (!field) return;
    clearFieldError(field);
    field.setAttribute("aria-invalid", "true");
    const label = field.closest("label");
    if (label) label.classList.add("settings-field-invalid");
    const error = document.createElement("div");
    error.className = "settings-field-error";
    error.textContent = message;
    field.insertAdjacentElement("afterend", error);
  }

  function applyFormErrors(form, errors) {
    clearFormValidation(form);
    for (const error of errors) {
      addFieldError(error.field, error.message);
    }
    const first = errors[0]?.field;
    if (first) first.focus({ preventScroll: true });
    return errors.length === 0;
  }

  function requireField(errors, field, message) {
    if (!String(field?.value || "").trim()) {
      errors.push({ field, message });
    }
  }

  function validateMcpForm(form, payload) {
    const errors = [];
    requireField(errors, formField(form, "id"), "Server id is required.");
    requireField(errors, formField(form, "url"), "HTTP URL is required.");
    if (payload.server.auth?.type === "bearer") {
      requireField(errors, formField(form, "token_env"), "Bearer auth needs a token environment variable.");
    }
    const urlField = formField(form, "url");
    const url = String(urlField?.value || "").trim();
    if (url) {
      try {
        const parsed = new URL(url);
        if (!["http:", "https:"].includes(parsed.protocol)) {
          errors.push({ field: urlField, message: "Use an http or https URL." });
        }
      } catch {
        errors.push({ field: urlField, message: "Enter a valid URL." });
      }
    }
    const timeoutField = formField(form, "timeout_seconds");
    const timeout = Number(timeoutField?.value || 0);
    if (!Number.isFinite(timeout) || timeout <= 0) {
      errors.push({ field: timeoutField, message: "Timeout must be greater than 0." });
    }
    return applyFormErrors(form, errors);
  }

  function validateSkillCreateForm() {
    const form = document.getElementById("skill-name")?.closest(".settings-form");
    const errors = [];
    requireField(errors, document.getElementById("skill-name"), "Skill name is required.");
    requireField(errors, document.getElementById("skill-description"), "Description is required.");
    return applyFormErrors(form, errors);
  }

  function validateSkillAssignForm() {
    const form = document.getElementById("assign-skill-id")?.closest(".settings-form");
    const errors = [];
    requireField(errors, document.getElementById("assign-skill-id"), "Choose a skill to assign.");
    requireField(errors, document.getElementById("assign-target-id"), "Target id is required. Use * for global.");
    return applyFormErrors(form, errors);
  }

  function selectedOptions(id) {
    const el = document.getElementById(id);
    if (!el) return [];
    return Array.from(el.selectedOptions || []).map(option => option.value).filter(Boolean);
  }

  function mcpPayloadFromForm(form) {
    return {
      server: {
        id: formValue(form, "id"),
        tool_prefix: formValue(form, "tool_prefix"),
        url: formValue(form, "url"),
        transport: formValue(form, "transport", "http"),
        auth: {
          type: formValue(form, "auth_type", "none"),
          token_env: formValue(form, "token_env"),
        },
        timeout_seconds: Number(formValue(form, "timeout_seconds", "30")),
        allowed_agent_ids: formCsvValue(form, "allowed_agent_ids", ["*"]),
        allowed_tools: formCsvValue(form, "allowed_tools", ["*"]),
        allowed_roles: formCsvValue(form, "allowed_roles"),
        allowed_worker_types: formCsvValue(form, "allowed_worker_types"),
        enabled: formChecked(form, "enabled"),
        queue_enabled: formChecked(form, "queue_enabled"),
      }
    };
  }

  async function startMcpOauth(server, oauthWindow) {
    setSettingsStatus("starting OAuth...");
    const res = await fetch("/api/settings/mcp/oauth/start", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ server }),
    });
    const data = await res.json();
    if (!res.ok || data.ok === false) throw new Error(data.error || "failed to start OAuth");
    if (oauthWindow && !oauthWindow.closed) {
      oauthWindow.location = data.authorization_url;
    } else if (data.authorization_url) {
      window.open(data.authorization_url, "_blank", "noopener,noreferrer");
    }
    setSettingsStatus("OAuth window opened. Complete authorization in the new tab.");
  }

  async function saveMcpServer(target) {
    const form = target?.closest(".mcp-form");
    if (!form) throw new Error("MCP form not found");
    const payload = mcpPayloadFromForm(form);
    if (!validateMcpForm(form, payload)) {
      setSettingsStatus("Fix the required MCP fields.", "error");
      return;
    }
    const wantsOauth = payload.server.auth?.type === "oauth";
    const oauthWindow = wantsOauth ? window.open("about:blank", "_blank") : null;
    if (oauthWindow) {
      oauthWindow.document.write("<p style='font-family:system-ui;padding:18px;'>Preparing OAuth authorization...</p>");
    }
    try {
      setSettingsStatus("saving MCP server...");
      const res = await fetch("/api/settings/mcp/servers", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok || data.ok === false) throw new Error(data.error || "failed to save MCP server");
      settingsData.mcp = data;
      settingsAddOpen.mcp = false;
      editingMcpServerId = "";
      renderSettingsView();
      setSettingsStatus(`saved ${data.saved?.id || "server"}`);
      if (wantsOauth) {
        await startMcpOauth(data.saved || payload.server, oauthWindow);
      }
    } catch (err) {
      if (oauthWindow && !oauthWindow.closed) oauthWindow.close();
      throw err;
    }
  }

  async function deleteMcpServer(serverId) {
    const id = String(serverId || "").trim();
    if (!id) throw new Error("MCP server id is required");
    if (!window.confirm(`Delete MCP server "${id}"?`)) return;
    setSettingsStatus(`deleting ${id}...`);
    const res = await fetch(`/api/settings/mcp/servers/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
    const data = await res.json();
    if (!res.ok || data.ok === false) throw new Error(data.error || "failed to delete MCP server");
    settingsData.mcp = data;
    if (editingMcpServerId === id) editingMcpServerId = "";
    renderSettingsView();
    setSettingsStatus(`deleted ${id}`);
  }

  async function createSkill() {
    if (!validateSkillCreateForm()) {
      setSettingsStatus("Fix the required skill fields.", "error");
      return;
    }
    const payload = {
      name: document.getElementById("skill-name")?.value || "",
      description: document.getElementById("skill-description")?.value || "",
      instructions: document.getElementById("skill-instructions")?.value || "",
      status: document.getElementById("skill-status")?.value || "approved",
      provider_targets: selectedOptions("skill-provider-targets"),
      source: "dashboard",
    };
    setSettingsStatus("creating skill...");
    const res = await fetch("/api/settings/skills", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || data.ok === false) throw new Error(data.error || "failed to create skill");
    settingsData.skills = data;
    settingsAddOpen.skills = false;
    renderSettingsView();
    setSettingsStatus(`created ${data.created?.name || "skill"}`);
  }

  async function assignSkill() {
    if (!validateSkillAssignForm()) {
      setSettingsStatus("Fix the required assignment fields.", "error");
      return;
    }
    const payload = {
      skill_id: document.getElementById("assign-skill-id")?.value || "",
      target_type: document.getElementById("assign-target-type")?.value || "global",
      target_id: document.getElementById("assign-target-id")?.value || "*",
      enabled: Boolean(document.getElementById("assign-enabled")?.checked),
      materialize_on_start: Boolean(document.getElementById("assign-materialize")?.checked),
    };
    setSettingsStatus("assigning skill...");
    const res = await fetch("/api/settings/skills/assignments", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || data.ok === false) throw new Error(data.error || "failed to assign skill");
    settingsData.skills = data;
    settingsAddOpen.skills = false;
    renderSettingsView();
    setSettingsStatus("assignment saved");
  }

  const PIPELINE_STEPS = [
    { key: "validate", label: "Brief" },
    { key: "request", label: "Request" },
    { key: "model", label: "Design" },
    { key: "render", label: "Staff" },
    { key: "done", label: "Ready" },
  ];

  function pipelinePhaseIndex(phase) {
    const map = { validate: 0, request: 1, model: 2, draft: 2, render: 3, staff: 3, execute: 3, done: 4, report: 4 };
    return map[phase] != null ? map[phase] : -1;
  }

  function renderPipelineCard() {
    const host = document.getElementById("pipeline-card");
    if (!host) return;
    const prog = (typeof designedTaskProgress !== "undefined" && designedTaskProgress) || pipelineState;
    const state = prog.state || "idle";
    const phase = prog.phase || "idle";
    const activeIdx = pipelinePhaseIndex(phase);
    const running = state === "running" || state === "active";
    const finished = state === "finished";
    const failed = state === "failed";

    if (state === "idle" && activeIdx < 0) {
      host.style.display = "none";
      host.innerHTML = "";
      return;
    }
    host.style.display = "block";

    let bg = "#fff", border = "1px solid #e6e3de", badgeBg = "#f3f1ec", badgeColor = "#6b675f", badgeText = "Idle", iconBg = "#f3f1ec", iconColor = "#86827a";
    if (running) { bg = "#fbf9f4"; border = "1px solid #ecdcb8"; badgeBg = "#f6ecd5"; badgeColor = "#9a6c25"; badgeText = "Running"; iconBg = "#f6ecd5"; iconColor = "#b07d2f"; }
    else if (finished) { bg = "#f6faf7"; border = "1px solid #cbe6d4"; badgeBg = "#e3f1e8"; badgeColor = "#3f7d57"; badgeText = "Complete"; iconBg = "#e3f1e8"; iconColor = "#4a8b63"; }
    else if (failed) { bg = "#fbf4f3"; border = "1px solid #ecccc8"; badgeBg = "#f6e0dd"; badgeColor = "#b3524b"; badgeText = "Failed"; iconBg = "#f6e0dd"; iconColor = "#b3524b"; }

    host.style.background = bg;
    host.style.border = border;

    const title = prog.title || (running ? "Designing your organization" : finished ? "Organization ready" : failed ? "Pipeline failed" : "Pipeline");
    const detail = prog.detail || "";

    const stepsHtml = PIPELINE_STEPS.map((step, i) => {
      let dotBorder = "#ddd9d2", dotInner = "transparent", labelColor = "#a39e95";
      const done = (finished) || (activeIdx > i);
      const isActive = activeIdx === i && (running);
      if (done) { dotBorder = "#4a8b63"; dotInner = "#4a8b63"; labelColor = "#3f7d57"; }
      else if (isActive) { dotBorder = "#b07d2f"; dotInner = "#b07d2f"; labelColor = "#9a6c25"; }
      else if (failed && activeIdx === i) { dotBorder = "#b3524b"; dotInner = "#b3524b"; labelColor = "#b3524b"; }
      const dotStyle = isActive ? "animation:wrPulse 1.4s ease-in-out infinite;" : "";
      const col = `<div class="pipe-step-col">
        <div class="pipe-dot-outer" style="border-color:${dotBorder};${dotStyle}"><span class="pipe-dot-inner" style="background:${dotInner};"></span></div>
        <span class="pipe-step-label" style="color:${labelColor};">${esc(step.label)}</span>
      </div>`;
      if (i === PIPELINE_STEPS.length - 1) return col;
      let lineColor = "#e3e0da";
      let lineStyle = `background:${lineColor};`;
      if (activeIdx > i || finished) { lineStyle = "background:#9fd4b7;"; }
      else if (isActive) { lineStyle = "background:repeating-linear-gradient(90deg,#d9c79c 0 6px,transparent 6px 12px);background-size:14px 2px;animation:wrFlow .7s linear infinite;"; }
      return `<div class="pipe-step-wrap" style="flex:1;display:flex;align-items:center;">${col}<div class="pipe-line" style="${lineStyle}"></div></div>`;
    }).join("");

    const retryHtml = failed
      ? `<button class="pipe-retry" data-action="design-task-config" style="border:1px solid #ecccc8;background:#fff;color:#b3524b;">Retry</button>`
      : "";

    host.innerHTML = `
      <div class="pipe-head">
        <div class="pipe-head-left">
          <div class="pipe-icon" style="background:${iconBg};color:${iconColor};">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M8 1.5v3M8 11.5v3M1.5 8h3M11.5 8h3M3.4 3.4l2.1 2.1M10.5 10.5l2.1 2.1M12.6 3.4l-2.1 2.1M5.5 10.5l-2.1 2.1" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>
          </div>
          <div>
            <div class="pipe-title">${esc(title)}</div>
            ${detail ? `<div class="pipe-sub">${esc(detail)}</div>` : ""}
          </div>
        </div>
        <div class="pipe-head-right">
          <span class="pipe-badge" style="background:${badgeBg};color:${badgeColor};">${esc(badgeText)}</span>
          ${retryHtml}
        </div>
      </div>
      <div class="pipe-steps">${stepsHtml}</div>`;
  }

  function selectedTaskLabel() {
    const task = (lastAllTasks || []).find(item => item.task_id === selectedTaskId)
      || (lastTasks || []).find(item => item.task_id === selectedTaskId);
    if (!selectedTaskId) return "";
    return task ? `${task.task_id} - ${task.title || "Untitled task"}` : selectedTaskId;
  }

  function flattenOrgForLayout(nodes, depth = 0, result = { nodes: [], edges: [] }) {
    for (const node of nodes || []) {
      const activity = ensureAgentActivity(node.id, node.activity);
      const summary = summarizeActivity(activity, node);
      const active = Boolean(summary.active || (node.current_task_ids || []).length || node.status === "busy" || node.status === "blocked");
      result.nodes.push({ node, depth, summary, active, width: 230, height: 112 });
      for (const child of node.children || []) {
        result.edges.push({ id: `${node.id}->${child.id}`, source: node.id, target: child.id });
      }
      flattenOrgForLayout(node.children || [], depth + 1, result);
    }
    return result;
  }

  function fallbackSimpleOrgLayout(flattened) {
    const byDepth = new Map();
    for (const item of flattened.nodes) {
      const list = byDepth.get(item.depth) || [];
      list.push(item);
      byDepth.set(item.depth, list);
    }
    const positions = new Map();
    const xGap = 38;
    const yGap = 74;
    let width = 0;
    let height = 0;
    for (const [depth, list] of byDepth.entries()) {
      const rowWidth = list.length * 230 + Math.max(0, list.length - 1) * xGap;
      width = Math.max(width, rowWidth);
      list.forEach((item, index) => {
        positions.set(item.node.id, {
          x: index * (230 + xGap),
          y: depth * (112 + yGap),
          width: 230,
          height: 112,
        });
      });
      height = Math.max(height, depth * (112 + yGap) + 112);
    }
    for (const [depth, list] of byDepth.entries()) {
      const rowWidth = list.length * 230 + Math.max(0, list.length - 1) * xGap;
      const offset = Math.max(0, (width - rowWidth) / 2);
      for (const item of list) {
        positions.get(item.node.id).x += offset;
      }
    }
    return { positions, edges: flattened.edges, width, height, engine: "fallback" };
  }

  let elkLoadPromise = null;

  function ensureELK() {
    if (window.ELK) return Promise.resolve(window.ELK);
    if (elkLoadPromise) return elkLoadPromise;
    elkLoadPromise = new Promise(resolve => {
      try {
        const xhr = new XMLHttpRequest();
        xhr.open("GET", "/assets/elk.bundled.js", true);
        xhr.onreadystatechange = () => {
          if (xhr.readyState !== 4) return;
          if (xhr.status >= 200 && xhr.status < 300) {
            try {
              new Function(`${xhr.responseText}\n//# sourceURL=/assets/elk.bundled.js`)();
            } catch (err) {
              console.warn("ELK local loader failed", err);
            }
          }
          resolve(window.ELK || null);
        };
        xhr.onerror = () => resolve(null);
        xhr.send();
      } catch (err) {
        console.warn("ELK local loader unavailable", err);
        resolve(null);
      }
    });
    return elkLoadPromise;
  }

  async function layoutSimpleOrg(flattened) {
    await ensureELK();
    if (!window.ELK) return fallbackSimpleOrgLayout(flattened);
    const graph = {
      id: "root",
      layoutOptions: {
        "elk.algorithm": "layered",
        "elk.direction": "DOWN",
        "elk.spacing.nodeNode": "36",
        "elk.layered.spacing.nodeNodeBetweenLayers": "74",
        "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
      },
      children: flattened.nodes.map(item => ({ id: item.node.id, width: item.width, height: item.height })),
      edges: flattened.edges.map(edge => ({ id: edge.id, sources: [edge.source], targets: [edge.target] })),
    };
    const elk = new ELK();
    const layout = await elk.layout(graph);
    const positions = new Map();
    for (const child of layout.children || []) {
      positions.set(child.id, { x: child.x || 0, y: child.y || 0, width: child.width || 230, height: child.height || 112 });
    }
    return {
      positions,
      edges: layout.edges || flattened.edges,
      width: layout.width || 0,
      height: layout.height || 0,
      engine: "elk",
    };
  }

  function edgePath(edge, positions) {
    const section = (edge.sections || [])[0];
    if (section?.startPoint && section?.endPoint) {
      const points = [section.startPoint, ...(section.bendPoints || []), section.endPoint];
      return points.map((point, index) => `${index ? "L" : "M"}${point.x} ${point.y}`).join(" ");
    }
    const sourceId = edge.source || edge.sources?.[0];
    const targetId = edge.target || edge.targets?.[0];
    const source = positions.get(sourceId);
    const target = positions.get(targetId);
    if (!source || !target) return "";
    const sx = source.x + source.width / 2;
    const sy = source.y + source.height;
    const tx = target.x + target.width / 2;
    const ty = target.y;
    const midY = sy + Math.max(28, (ty - sy) / 2);
    return `M${sx} ${sy} C${sx} ${midY}, ${tx} ${midY}, ${tx} ${ty}`;
  }

  function simpleOrgStatusKind(status, active) {
    if (active || ["busy", "assigned", "in_progress", "running", "blocked"].includes(status)) return "active";
    if (["completed", "idle", "finished"].includes(status)) return "good";
    if (["failed", "timed_out"].includes(status)) return "bad";
    return "";
  }

  function renderSimpleOrgLayout(flattened, layout) {
    const stage = document.getElementById("simple-org-stage");
    const canvas = document.getElementById("simple-org-canvas");
    if (!stage || !canvas) return;
    const padding = 24;
    const width = Math.max(520, Math.ceil((layout.width || 0) + padding * 2));
    const height = Math.max(360, Math.ceil((layout.height || 0) + padding * 2));
    stage.dataset.layoutEngine = layout.engine || "unknown";
    stage.style.width = `${Math.ceil(width * simpleOrgScale)}px`;
    stage.style.height = `${Math.ceil(height * simpleOrgScale)}px`;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    canvas.style.transform = `scale(${simpleOrgScale})`;
    const edgeMarkup = (layout.edges || [])
      .map(edge => edgePath(edge, layout.positions))
      .filter(Boolean)
      .map(path => `<path class="simple-org-edge" d="${esc(path)}"></path>`)
      .join("");
    const cardMarkup = flattened.nodes.map(item => {
      const pos = layout.positions.get(item.node.id) || { x: 0, y: 0, width: 230, height: 112 };
      const status = item.node.status || "idle";
      const statusKind = simpleOrgStatusKind(status, item.active);
      const work = (item.node.current_task_ids || []).join(", ") || "No assigned task";
      return `<button class="simple-org-card ${item.active ? "active" : ""}" data-action="agent-detail" data-agent-detail="${esc(item.node.id)}" style="left:${pos.x + padding}px;top:${pos.y + padding}px;">
        <div class="simple-org-card-head">
          <div class="simple-org-card-title">
            ${renderAgentIcon(item.node.icon)}
            <div>
              <div class="simple-org-card-name">${esc(item.node.name || item.node.id)}</div>
              <div class="simple-org-card-role">${esc(item.node.role || "Agent")}</div>
            </div>
          </div>
          <span class="simple-org-status ${esc(statusKind)}">${esc(status)}</span>
        </div>
        <div class="simple-org-card-summary">${esc(item.summary.text || "Idle.")}</div>
        <div class="simple-agent-work">${esc(work)}</div>
      </button>`;
    }).join("");
    canvas.innerHTML = `<svg class="simple-org-edge-layer" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><g transform="translate(${padding} ${padding})">${edgeMarkup}</g></svg>${cardMarkup}`;
  }

  async function renderSimpleTaskOrg(data) {
    const section = document.getElementById("simple-org-section");
    const subtitle = document.getElementById("simple-org-subtitle");
    const canvas = document.getElementById("simple-org-canvas");
    if (!section || !canvas) return;
    if (!selectedTaskId) {
      section.classList.remove("visible");
      canvas.innerHTML = "";
      if (subtitle) subtitle.textContent = "Select a task to inspect its workforce.";
      return;
    }
    section.classList.add("visible");
    if (subtitle) subtitle.textContent = selectedTaskLabel();
    if (!orgChart.length) {
      canvas.innerHTML = `<div class="simple-org-empty">No organization is recorded for this task yet.</div>`;
      return;
    }
    const layoutRun = ++simpleOrgLayoutRun;
    const flattened = flattenOrgForLayout(orgChart);
    const layout = await layoutSimpleOrg(flattened);
    if (layoutRun !== simpleOrgLayoutRun) return;
    renderSimpleOrgLayout(flattened, layout);
  }

  function rerenderSimpleOrg() {
    renderSimpleTaskOrg({}).catch(err => {
      const canvas = document.getElementById("simple-org-canvas");
      if (canvas) canvas.innerHTML = `<div class="simple-org-empty">Could not render organization tree: ${esc(err)}</div>`;
    });
  }

  function zoomSimpleOrg(delta) {
    simpleOrgScale = Math.max(0.35, Math.min(1.8, Number((simpleOrgScale + delta).toFixed(2))));
    rerenderSimpleOrg();
  }

  function fitSimpleOrg() {
    const frame = document.getElementById("simple-org-frame");
    const canvas = document.getElementById("simple-org-canvas");
    if (!frame || !canvas) return;
    const rawWidth = Number.parseFloat(canvas.style.width || "0");
    const rawHeight = Number.parseFloat(canvas.style.height || "0");
    if (!rawWidth || !rawHeight) return;
    simpleOrgScale = Math.max(0.35, Math.min(1, Math.min((frame.clientWidth - 24) / rawWidth, (frame.clientHeight - 24) / rawHeight)));
    rerenderSimpleOrg();
  }

  function reportStatusMeta(report) {
    const requiresDecision = report.requires_decision || report.decision_required || (report.kind === "decision");
    if (requiresDecision) return { text: "Needs decision", bg: "#f6ecd5", color: "#9a6c25", dot: "#b07d2f" };
    const status = (report.status || "").toLowerCase();
    if (status.includes("fail") || status.includes("block")) return { text: "Blocked", bg: "#f6e0dd", color: "#b3524b", dot: "#b3524b" };
    return { text: "Informational", bg: "#e3f1e8", color: "#3f7d57", dot: "#4a8b63" };
  }

  function renderHomeReportCard(report) {
    const meta = reportStatusMeta(report);
    const fromId = report.from_agent_id || report.author || "CEO";
    const initials = String(fromId).split(/[\s_-]+/).map(w => w[0]).filter(Boolean).slice(0, 2).join("").toUpperCase() || "AI";
    const title = report.title || report.summary_title || `Report ${report.report_id || ""}`.trim();
    const time = report.created_at ? new Date(report.created_at).toLocaleString() : "";
    const confidence = report.confidence != null ? Number(report.confidence) : null;
    const confPct = confidence != null ? Math.round(confidence <= 1 ? confidence * 100 : confidence) : null;
    const confColor = confPct == null ? "#cfcabf" : (confPct >= 70 ? "#4a8b63" : confPct >= 40 ? "#b07d2f" : "#b3524b");
    const summary = report.message || report.summary || report.body || "";
    const risks = report.risks || [];
    const recommendation = report.recommendation || "";
    const nextActions = report.next_actions || report.next_steps || [];
    const decisionQ = report.decision_question || report.decision || (report.requires_decision ? (report.question || "Approve and proceed?") : "");

    const sevColor = (sev) => {
      const s = String(sev || "").toLowerCase();
      if (s.includes("high") || s.includes("crit")) return { bg: "#f6e0dd", color: "#b3524b" };
      if (s.includes("med")) return { bg: "#f6ecd5", color: "#9a6c25" };
      return { bg: "#eceae5", color: "#76726b" };
    };

    const risksHtml = risks.length ? `
      <div class="wr-slabel">Risks</div>
      <div class="wr-risks">
        ${risks.map(r => {
          const text = typeof r === "string" ? r : (r.description || r.text || r.risk || "");
          const sev = typeof r === "string" ? "" : (r.severity || r.level || "");
          const sc = sevColor(sev);
          return `<div class="wr-risk"><span class="wr-risk-sev" style="background:${sc.bg};color:${sc.color};">${esc((sev || "note").toUpperCase())}</span><span class="wr-risk-text">${esc(text)}</span></div>`;
        }).join("")}
      </div>` : "";

    const recHtml = (recommendation || nextActions.length) ? `
      <div class="wr-rec-grid">
        ${recommendation ? `<div><div class="wr-slabel">Recommendation</div><p class="wr-rec-text">${esc(recommendation)}</p></div>` : "<div></div>"}
        ${nextActions.length ? `<div><div class="wr-slabel">Next actions</div><div class="wr-next-actions">${nextActions.map(a => {
          const t = typeof a === "string" ? a : (a.description || a.text || a.action || "");
          return `<div class="wr-na"><span style="color:#86827a;">→</span><span>${esc(t)}</span></div>`;
        }).join("")}</div></div>` : ""}
      </div>` : "";

    const detailOpen = expandedReportId && expandedReportId === String(report.report_id || "");
    const detailHtml = detailOpen ? `
      <div class="wr-report-detail">
        <div class="wr-slabel">Report detail</div>
        <pre>${esc(JSON.stringify(report, null, 2))}</pre>
      </div>` : "";

    const decisionHtml = decisionQ ? `
      <div class="wr-dec-box">
        <div class="wr-dec-hdr">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" style="color:#b07d2f;"><path d="M8 1.5 14.5 13H1.5L8 1.5Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M8 6.2v3M8 11.2v.01" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>
          <span class="wr-dec-label">Decision required</span>
        </div>
        <p class="wr-dec-q">${esc(decisionQ)}</p>
        <div class="wr-dec-actions">
          <button class="wr-dec-primary" data-action="report-detail" data-report-id="${esc(report.report_id || "")}">${detailOpen ? "Hide details" : "Review &amp; decide"}</button>
          <button class="wr-dec-alt" data-action="report-detail" data-report-id="${esc(report.report_id || "")}">${detailOpen ? "Collapse" : "View details"}</button>
        </div>
      </div>` : "";

    return `
      <div class="wr-report-card">
        <div class="wr-rh">
          <div class="wr-rh-left">
            <div class="wr-avatar">${esc(initials)}</div>
            <div style="min-width:0;">
              <div class="wr-rtitle">${esc(title)}</div>
              <div class="wr-rmeta">
                <span>${esc(fromId)}</span>
                ${time ? `<span class="wr-rmeta-dot"></span><span>${esc(time)}</span>` : ""}
                ${report.task_id ? `<span class="wr-rmeta-dot"></span><span>${esc(report.task_id)}</span>` : ""}
              </div>
            </div>
          </div>
          <div class="wr-rh-right">
            <span class="wr-sbadge" style="background:${meta.bg};color:${meta.color};"><span class="wr-sbadge-dot" style="background:${meta.dot};"></span>${esc(meta.text)}</span>
            ${confPct != null ? `<div class="wr-conf-row"><span class="wr-conf-lbl">Confidence</span><div class="wr-conf-bar"><div class="wr-conf-fill" style="width:${confPct}%;background:${confColor};"></div></div><span class="wr-conf-val">${confPct}%</span></div>` : ""}
          </div>
        </div>
        <div class="wr-rbody">
          ${summary ? `<div class="wr-slabel">Summary</div><p class="wr-summary">${esc(summary)}</p>` : ""}
          ${risksHtml}
          ${recHtml}
        </div>
        ${decisionHtml}
        ${detailHtml}
      </div>`;
  }

  function renderHomeHumanReports(reports) {
    const host = document.getElementById("home-human-reports");
    if (!host) return;
    const section = document.getElementById("home-reports-section");
    if (section) section.hidden = !selectedTaskId;
    if (!selectedTaskId) {
      host.innerHTML = "";
      const countEl = document.getElementById("reports-count");
      if (countEl) countEl.textContent = "0";
      const fromEl = document.getElementById("reports-from");
      if (fromEl) fromEl.textContent = "";
      return;
    }
    const list = reports || [];
    lastHumanReports = list;
    const countEl = document.getElementById("reports-count");
    if (countEl) countEl.textContent = String(list.length);
    const fromEl = document.getElementById("reports-from");
    if (fromEl) {
      const names = Array.from(new Set(list.map(r => r.from_agent_id || r.author).filter(Boolean)));
      fromEl.textContent = names.length ? `from ${names.slice(0, 3).join(", ")}${names.length > 3 ? "…" : ""}` : "";
    }
    if (!list.length) {
      host.innerHTML = `
        <div class="no-reports-card">
          <div class="no-reports-icon">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none"><path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v9A1.5 1.5 0 0 1 18.5 16H9l-4 4V5.5Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>
          </div>
          <div class="no-reports-title">No reports yet</div>
          <div class="no-reports-desc">When your workforce reaches a milestone or needs a decision, reports from leadership will appear here.</div>
        </div>`;
      return;
    }
    host.innerHTML = list.map(renderHomeReportCard).join("");
  }

  const DEFAULT_CONFIG = {
    dashboard: { refresh_interval_ms: 5000, max_visible_agents: 80, collapse_depth: 3, show_idle_activity: true },
    activity: { recent_output_items: 12, recent_tool_items: 12, recent_event_items: 10, full_stream_limit: 200, global_output_limit: 200 },
    summaries: { mode: "local", max_chars: 140 }
  };
  const ACTIVITY_EVENT_TYPES = new Set([
    "task_created",
    "task_assigned",
    "task_status_updated",
    "discussion_message",
    "report_registered",
    "human_report_registered",
    "manager_review_created",
    "manager_review_decided",
    "progress_checked",
    "agent_hired",
    "agent_profile_updated",
    "system_prompt_updated",
    "task_document_upserted",
    "tool_request_submitted",
    "tool_request_approved",
    "tool_request_rejected",
    "agent_run_path_registered",
    "agent_run_attempt_started",
    "agent_run_attempt_failed",
    "agent_run_retrying",
    "trace_file_written",
    "runtime_config_updated",
  ]);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const statusClass = (value) => esc(value).replace(/[^a-zA-Z0-9_-]/g, "_");
  const rows = (headers, values) => {
    const head = `<tr>${headers.map(h => `<th>${esc(h)}</th>`).join("")}</tr>`;
    const body = values.map(row => `<tr>${row.map(cell => `<td>${cell}</td>`).join("")}</tr>`).join("");
    return head + body;
  };
  let eventCursor = 0;
  let eventSource = null;
  let liveOutput = [];
  let orgChart = [];
  let agents = [];
  let agentActivity = {};
  let dashboardConfig = structuredClone(DEFAULT_CONFIG);
  let expandedNodes = new Set();
  let collapsedNodes = new Set();
  let selectedAgentId = null;
  let selectedTaskId = "";
  let settingsView = "";
  let settingsData = { mcp: null, skills: null };
  let settingsAddOpen = { mcp: false, skills: false };
  let settingsSkillAddTab = "create";
  let editingMcpServerId = "";
  let currentTaskScope = new Set();
  let visibleNodeCount = 0;
  let dashboardMode = localStorage.getItem("workforceDashboardMode") || "simple";
  let communicationPulses = [];
  let longRfcDemoStatus = { status: "idle", running: false };
  let realLlmBenchmarkStatus = { status: "idle", running: false };
  let claudeSteerDemoStatus = { status: "idle", running: false };
  let designedTaskStatus = { status: "idle", running: false };
  let designedTaskConfig = null;
  let designedTaskProgress = {
    active: false,
    state: "idle",
    phase: "idle",
    title: "No design request running.",
    detail: "Click Design Org/Config to generate a draft organization.",
    startedAt: 0,
    finishedAt: 0,
  };
  let designedTaskProgressTimer = null;
  let runtimeConfig = null;
  let refreshScheduled = false;
  let simpleOrgScale = 1;
  let simpleOrgLayoutRun = 0;

  function deepMerge(base, override) {
    const result = {...base};
    for (const [key, value] of Object.entries(override || {})) {
      if (value && typeof value === "object" && !Array.isArray(value) && base[key] && typeof base[key] === "object") {
        result[key] = deepMerge(base[key], value);
      } else {
        result[key] = value;
      }
    }
    return result;
  }

  function cfg(section, key, fallback) {
    return dashboardConfig?.[section]?.[key] ?? fallback;
  }

  function clip(value, limit = cfg("summaries", "max_chars", 140)) {
    const text = String(value ?? "").replace(/\s+/g, " ").trim();
    return text.length > limit ? `${text.slice(0, Math.max(limit - 3, 1))}...` : text;
  }

  function renderOutput() {
    const groupedOutput = aggregateOutputItems(liveOutput).slice(-cfg("activity", "global_output_limit", 200));
    document.getElementById("output").innerHTML = groupedOutput.map(renderOutputBlock).join("") || `<div class="muted">No agent output.</div>`;
  }

  function renderOutputBlock(item) {
    const label = `${item.task_id || "-"} ${item.agent_id || "-"} ${item.stream || "output"}`;
    return `<div class="output-block">
      <div><span class="pill">${esc(label)}</span></div>
      <div class="output-text">${esc(item.text || "")}</div>
    </div>`;
  }

  function aggregateOutputItems(items) {
    const groups = [];
    for (const item of items || []) {
      const last = groups[groups.length - 1];
      if (last && sameOutputStream(last, item)) {
        last.text = appendStreamText(last.text || "", item.text || "");
        last.timestamp = item.timestamp || last.timestamp;
        last.event_id = item.event_id || last.event_id;
      } else {
        groups.push({ ...item, text: String(item.text || "") });
      }
    }
    return groups;
  }

  function appendOutputItem(items, item, limit) {
    const last = items[items.length - 1];
    if (last && sameOutputStream(last, item)) {
      last.text = appendStreamText(last.text || "", item.text || "");
      last.timestamp = item.timestamp || last.timestamp;
      last.event_id = item.event_id || last.event_id;
    } else {
      items.push({ ...item, text: String(item.text || "") });
    }
    return items.slice(-limit);
  }

  function sameOutputStream(left, right) {
    return String(left?.agent_id || "") === String(right?.agent_id || "")
      && String(left?.task_id || "") === String(right?.task_id || "")
      && String(left?.run_id || "") === String(right?.run_id || "")
      && String(left?.stream || "output") === String(right?.stream || "output");
  }

  function appendStreamText(current, next) {
    return String(current || "") + String(next || "");
  }

  function renderDemoStatus() {
    renderRunStatus({
      labelId: "long-rfc-demo-status",
      buttonId: "start-long-rfc-demo",
      statusPayload: longRfcDemoStatus,
      resultText: longRfcDemoStatus?.result?.final_status ? ` final=${longRfcDemoStatus.result.final_status}` : "",
    });
    const benchmarkResult = realLlmBenchmarkStatus?.result;
    const overall = (benchmarkResult?.scores || []).find(score => score.name === "overall");
    renderRunStatus({
      labelId: "real-llm-benchmark-status",
      buttonId: "start-real-llm-benchmark",
      statusPayload: realLlmBenchmarkStatus,
      resultText: benchmarkResult?.ok != null ? ` ok=${benchmarkResult.ok} score=${overall ? overall.score : ""}` : "",
    });
    renderRunStatus({
      labelId: "claude-steer-demo-status",
      buttonId: "start-claude-steer-demo",
      statusPayload: claudeSteerDemoStatus,
      resultText: claudeSteerDemoStatus?.result?.root_task_id ? ` root=${claudeSteerDemoStatus.result.root_task_id}` : "",
    });
    const designedResult = designedTaskStatus?.result;
    renderRunStatus({
      labelId: "designed-task-status",
      buttonId: "start-designed-task",
      statusPayload: designedTaskStatus,
      resultText: designedResult?.root_task_id ? ` root=${designedResult.root_task_id}` : designedTaskStatus?.root_task_id ? ` root=${designedTaskStatus.root_task_id}` : "",
    });
    const designButton = document.getElementById("design-task-config");
    if (designButton) designButton.disabled = Boolean(designedTaskStatus?.running);
  }

  function renderRunStatus({labelId, buttonId, statusPayload, resultText}) {
    const label = document.getElementById(labelId);
    const button = document.getElementById(buttonId);
    if (!label || !button) return;
    const status = statusPayload?.status || "idle";
    const runId = statusPayload?.run_id ? ` ${statusPayload.run_id}` : "";
    const error = statusPayload?.error ? ` error=${clip(statusPayload.error, 90)}` : "";
    label.textContent = `${status}${runId}${resultText || ""}${error}`;
    button.disabled = Boolean(statusPayload?.running);
  }

  function startDesignedProgress({ phase, title, detail }) {
    designedTaskProgress = {
      active: true,
      state: "active",
      phase,
      title,
      detail,
      startedAt: Date.now(),
      finishedAt: 0,
    };
    if (designedTaskProgressTimer) window.clearInterval(designedTaskProgressTimer);
    designedTaskProgressTimer = window.setInterval(renderDesignedProgress, 1000);
    renderDesignedProgress();
  }

  function updateDesignedProgress({ phase, title, detail }) {
    designedTaskProgress = {
      ...designedTaskProgress,
      active: true,
      state: "active",
      phase: phase || designedTaskProgress.phase,
      title: title || designedTaskProgress.title,
      detail: detail || designedTaskProgress.detail,
    };
    renderDesignedProgress();
  }

  function finishDesignedProgress({ state = "finished", phase = "done", title, detail }) {
    if (designedTaskProgressTimer) {
      window.clearInterval(designedTaskProgressTimer);
      designedTaskProgressTimer = null;
    }
    designedTaskProgress = {
      ...designedTaskProgress,
      active: false,
      state,
      phase,
      title: title || designedTaskProgress.title,
      detail: detail || designedTaskProgress.detail,
      finishedAt: Date.now(),
    };
    renderDesignedProgress();
  }

  function renderDesignedProgress() {
    const panel = document.getElementById("designed-task-progress");
    if (!panel) return;
    renderComposerMode();
    const title = document.getElementById("designed-task-progress-title");
    const detail = document.getElementById("designed-task-progress-detail");
    const elapsed = document.getElementById("designed-task-progress-elapsed");
    const steps = document.getElementById("designed-task-progress-steps");
    panel.classList.toggle("active", Boolean(designedTaskProgress.active));
    panel.classList.toggle("finished", designedTaskProgress.state === "finished");
    panel.classList.toggle("failed", designedTaskProgress.state === "failed");
    if (title) title.textContent = designedTaskProgress.title || "No design request running.";
    if (detail) detail.textContent = designedTaskProgress.detail || "";
    if (elapsed) {
      if (designedTaskProgress.startedAt) {
        const end = designedTaskProgress.active ? Date.now() : (designedTaskProgress.finishedAt || Date.now());
        elapsed.textContent = `${Math.max(0, Math.round((end - designedTaskProgress.startedAt) / 1000))}s elapsed`;
      } else {
        elapsed.textContent = "idle";
      }
    }
    if (steps) steps.innerHTML = renderDesignedProgressSteps(designedTaskProgress.phase);
    // Drive the new homepage submit button + pipeline card
    const submitBtn = document.getElementById("design-task-config");
    const runBtn = document.getElementById("run-designed-task");
    const submitLabel = document.getElementById("submit-label");
    const iconArrow = document.getElementById("submit-icon-arrow");
    const iconSpin = document.getElementById("submit-icon-spin");
    const running = Boolean(designedTaskProgress.active);
    if (submitBtn) submitBtn.classList.toggle("running", running);
    if (runBtn) {
      runBtn.hidden = Boolean(selectedTaskId || !designedTaskConfig || running);
      runBtn.disabled = Boolean(designedTaskStatus?.running);
    }
    if (iconArrow) iconArrow.style.display = running ? "none" : "";
    if (iconSpin) iconSpin.style.display = running ? "" : "none";
    if (submitLabel) {
      submitLabel.textContent = selectedTaskId ? "Send" : (running ? "Designing…" : (designedTaskProgress.state === "finished" ? "Re-design" : "Design Org"));
    }
    renderPipelineCard();
  }

  function renderDesignedProgressSteps(activePhase) {
    const items = [
      ["validate", "validate goal"],
      ["request", "send request"],
      ["model", "wait for model"],
      ["render", "render draft"],
      ["done", "ready"],
    ];
    return items.map(([phase, label]) =>
      `<span class="progress-step ${phase === activePhase ? "active" : ""}">${esc(label)}</span>`
    ).join("");
  }

  function nextPaint() {
    return new Promise(resolve => window.requestAnimationFrame(() => window.setTimeout(resolve, 0)));
  }

  async function refreshDemoStatus() {
    const [longRes, benchmarkRes, claudeSteerRes, designedRes] = await Promise.all([
      fetch("/api/demos/long-rfc/status", { cache: "no-store" }),
      fetch("/api/demos/real-llm-benchmark/status", { cache: "no-store" }),
      fetch("/api/demos/claude-steer/status", { cache: "no-store" }),
      fetch("/api/designed-task/status", { cache: "no-store" }),
    ]);
    longRfcDemoStatus = await longRes.json();
    realLlmBenchmarkStatus = await benchmarkRes.json();
    claudeSteerDemoStatus = await claudeSteerRes.json();
    const serverDesignedTaskStatus = await designedRes.json();
    if (designedTaskStatus?.status !== "designing") {
      designedTaskStatus = serverDesignedTaskStatus;
    }
    if (designedTaskStatus?.root_task_id && !selectedTaskId) {
      selectedTaskId = designedTaskStatus.root_task_id;
      scheduleRefresh(0);
    }
    renderDemoStatus();
  }

  async function startLongRfcDemo() {
    longRfcDemoStatus = { status: "starting", running: true };
    renderDemoStatus();
    const res = await fetch("/api/demos/long-rfc/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({})
    });
    longRfcDemoStatus = await res.json();
    if (!res.ok && !longRfcDemoStatus.error) {
      longRfcDemoStatus.error = `HTTP ${res.status}`;
    }
    renderDemoStatus();
    await refresh();
  }

  async function startRealLlmBenchmark() {
    realLlmBenchmarkStatus = { status: "starting", running: true };
    renderDemoStatus();
    const res = await fetch("/api/demos/real-llm-benchmark/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ judge: "heuristic", use_llm: true, reset: true })
    });
    realLlmBenchmarkStatus = await res.json();
    if (!res.ok && !realLlmBenchmarkStatus.error) {
      realLlmBenchmarkStatus.error = `HTTP ${res.status}`;
    }
    renderDemoStatus();
    await refresh();
  }

  async function startClaudeSteerDemo() {
    claudeSteerDemoStatus = { status: "starting", running: true };
    renderDemoStatus();
    const res = await fetch("/api/demos/claude-steer/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({})
    });
    claudeSteerDemoStatus = await res.json();
    if (!res.ok && !claudeSteerDemoStatus.error) {
      claudeSteerDemoStatus.error = `HTTP ${res.status}`;
    }
    renderDemoStatus();
    await refresh();
  }

  function currentCeoAgentId() {
    const rooted = (orgChart || []).find(node => node && node.id);
    if (rooted?.id) return rooted.id;
    const ceo = (agents || []).find(agent => `${agent.role || ""} ${agent.name || ""}`.toLowerCase().includes("ceo")
      || `${agent.role || ""}`.toLowerCase().includes("chief executive"));
    return ceo?.id || "ceo";
  }

  async function sendTaskCeoMessage() {
    const input = document.getElementById("designed-task-goal");
    const message = (input?.value || "").trim();
    if (!selectedTaskId || !message) {
      return;
    }
    const submitLabel = document.getElementById("submit-label");
    if (submitLabel) submitLabel.textContent = "Sending...";
    const res = await fetch("/api/agents/steer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        agent_id: currentCeoAgentId(),
        task_id: selectedTaskId,
        message,
        action: "message",
        from_agent_id: "human",
      })
    });
    const payload = await res.json();
    if (!res.ok || !payload.ok) {
      throw new Error(payload.error || payload.message || `HTTP ${res.status}`);
    }
    if (input) {
      input.value = "";
      onGoalInput(input);
    }
    if (submitLabel) submitLabel.textContent = "Send";
    await refresh();
  }

  async function routePrimaryComposerAction() {
    const input = document.getElementById("designed-task-goal");
    const message = (input?.value || "").trim();
    if (selectedTaskId && message) {
      await sendTaskCeoMessage();
      return;
    }
    await designTaskConfig();
  }

  async function designTaskConfig() {
    const goal = document.getElementById("designed-task-goal").value.trim();
    if (!goal) {
      designedTaskStatus = { status: "failed", running: false, error: "Enter a task goal first." };
      finishDesignedProgress({
        state: "failed",
        phase: "validate",
        title: "Design request was not started.",
        detail: "Enter a task goal first.",
      });
      renderDemoStatus();
      return;
    }
    designedTaskStatus = { status: "designing", running: true };
    renderDemoStatus();
    const payload = {
      goal,
      headcount_limit: Number(document.getElementById("designed-task-headcount").value || runtimeConfig?.designed_task?.headcount_limit || 6),
      token_budget: Number(document.getElementById("designed-task-token-budget").value || runtimeConfig?.designed_task?.token_budget || 600000),
      management_model: document.getElementById("designed-task-management-model").value.trim() || runtimeConfig?.designed_task?.management_model || "openai/gpt-oss-120b:free",
      worker_model: document.getElementById("designed-task-worker-model").value.trim() || runtimeConfig?.designed_task?.worker_model || "poolside/laguna-m.1:free",
      decision_backend: runtimeConfig?.designed_task?.decision_backend || "codex",
      management_worker_type: runtimeConfig?.designed_task?.management_worker_type || "codex",
      worker_worker_type: runtimeConfig?.designed_task?.worker_worker_type || "codex",
      use_llm: true,
    };
    startDesignedProgress({
      phase: "request",
      title: "Designing organization/config draft...",
      detail: `Request is being sent to the ${payload.decision_backend} org-design agent with ${payload.management_model}.`,
    });
    await nextPaint();
    updateDesignedProgress({
      phase: "model",
      detail: "Waiting for the model response. The server is still working until this changes to ready or failed.",
    });
    const res = await fetch("/api/designed-task/design", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      designedTaskStatus = { status: "failed", running: false, error: data.error || `HTTP ${res.status}` };
      finishDesignedProgress({
        state: "failed",
        phase: "model",
        title: "Design failed.",
        detail: data.error || `HTTP ${res.status}`,
      });
      renderDemoStatus();
      return;
    }
    updateDesignedProgress({
      phase: "render",
      title: "Rendering draft organization tree...",
      detail: "Model response received. Parsing config JSON and drawing the hierarchy.",
    });
    designedTaskConfig = data.config;
    document.getElementById("designed-task-config-json").value = JSON.stringify(designedTaskConfig, null, 2);
    designedTaskStatus = { status: "draft_ready", running: false, error: "" };
    renderDraftOrganizationTree();
    finishDesignedProgress({
      state: "finished",
      phase: "done",
      title: "Draft organization is ready.",
      detail: `Generated ${designedTaskConfig?.organization?.agents?.length || 0} agents. Review the tree or JSON, then start the task.`,
    });
    renderDemoStatus();
  }

  async function startDesignedTask() {
    const editor = document.getElementById("designed-task-config-json");
    let config;
    try {
      config = JSON.parse(editor.value || "{}");
    } catch (err) {
      designedTaskStatus = { status: "failed", running: false, error: `Invalid JSON: ${err}` };
      finishDesignedProgress({
        state: "failed",
        phase: "validate",
        title: "Cannot start task.",
        detail: `Invalid JSON: ${err}`,
      });
      renderDemoStatus();
      return;
    }
    designedTaskConfig = config;
    renderDraftOrganizationTree();
    designedTaskStatus = { status: "starting", running: true };
    selectedTaskId = "";
    currentTaskScope = new Set();
    startDesignedProgress({
      phase: "request",
      title: "Starting confirmed task run...",
      detail: "Submitting the confirmed config to the runtime and waiting for the root task id.",
    });
    renderDemoStatus();
    await nextPaint();
    const res = await fetch("/api/designed-task/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config })
    });
    designedTaskStatus = await res.json();
    if (!res.ok && !designedTaskStatus.error) {
      designedTaskStatus.error = `HTTP ${res.status}`;
    }
    if (res.ok && !designedTaskStatus.error) {
      finishDesignedProgress({
        state: "finished",
        phase: "done",
        title: "Task run has started.",
        detail: designedTaskStatus.root_task_id
          ? `Root task: ${designedTaskStatus.root_task_id}. Watch the org chart, task filter, replay, and live output below.`
          : "Runtime accepted the task. Watch the org chart, task filter, replay, and live output below.",
      });
    } else {
      finishDesignedProgress({
        state: "failed",
        phase: "request",
        title: "Task run failed to start.",
        detail: designedTaskStatus.error || `HTTP ${res.status}`,
      });
    }
    renderDemoStatus();
    await refresh();
  }

  function setRuntimeConfigStatus(text) {
    const status = document.getElementById("runtime-config-status");
    if (status) status.textContent = text;
  }

  function setTraceExportStatus(html) {
    const status = document.getElementById("task-trace-export-status");
    if (status) status.innerHTML = html;
  }

  async function loadRuntimeConfig() {
    setRuntimeConfigStatus("loading");
    const res = await fetch("/api/runtime-config", { cache: "no-store" });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      setRuntimeConfigStatus(data.error || `HTTP ${res.status}`);
      return;
    }
    runtimeConfig = data.config || {};
    document.getElementById("runtime-config-json").value = JSON.stringify(runtimeConfig, null, 2);
    applyRuntimeConfigDefaults();
    setRuntimeConfigStatus(`loaded ${data.path || ""}`.trim());
  }

  async function saveRuntimeConfig() {
    const editor = document.getElementById("runtime-config-json");
    let config;
    try {
      config = JSON.parse(editor.value || "{}");
    } catch (err) {
      setRuntimeConfigStatus(`invalid JSON: ${err}`);
      return;
    }
    setRuntimeConfigStatus("saving");
    const res = await fetch("/api/runtime-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config })
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      setRuntimeConfigStatus(data.error || `HTTP ${res.status}`);
      return;
    }
    runtimeConfig = data.config || {};
    editor.value = JSON.stringify(runtimeConfig, null, 2);
    applyRuntimeConfigDefaults();
    setRuntimeConfigStatus(`saved ${data.path || ""}`.trim());
    await refresh();
  }

  function applyRuntimeConfigDefaults() {
    const defaults = runtimeConfig?.designed_task || {};
    const setValue = (id, value) => {
      const element = document.getElementById(id);
      if (element && value != null) {
        element.value = value;
      }
    };
    setValue("designed-task-headcount", defaults.headcount_limit);
    setValue("designed-task-token-budget", defaults.token_budget);
    setValue("designed-task-management-model", defaults.management_model);
    setValue("designed-task-worker-model", defaults.worker_model);
  }

  async function exportSelectedTaskTrace() {
    if (!selectedTaskId) {
      setTraceExportStatus("select a task first");
      return;
    }
    setTraceExportStatus("exporting");
    const res = await fetch("/api/tasks/export-trace", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_id: selectedTaskId, include_descendants: true })
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      setTraceExportStatus(esc(data.error || `HTTP ${res.status}`));
      return;
    }
    setTraceExportStatus(`<a href="${esc(data.url)}" target="_blank" rel="noreferrer">trace file</a>`);
    await refresh();
  }

  function readDesignedTaskConfigFromEditor() {
    const editor = document.getElementById("designed-task-config-json");
    const text = editor?.value?.trim() || "";
    if (!text) return { config: designedTaskConfig, error: "" };
    try {
      const config = JSON.parse(text);
      designedTaskConfig = config;
      return { config, error: "" };
    } catch (err) {
      return { config: null, error: String(err) };
    }
  }

  function renderDraftOrganizationTree() {
    const treeHost = document.getElementById("draft-org-tree");
    const summary = document.getElementById("draft-org-summary");
    if (!treeHost || !summary) return;
    const { config, error } = readDesignedTaskConfigFromEditor();
    if (error) {
      summary.textContent = "invalid JSON";
      treeHost.innerHTML = `<div class="org-placeholder">Cannot render draft tree: ${esc(error)}</div>`;
      return;
    }
    const organization = config?.organization || {};
    const agents = Array.isArray(organization.agents) ? organization.agents : [];
    if (!agents.length) {
      summary.textContent = "no draft";
      treeHost.innerHTML = `<div class="muted">No draft yet.</div>`;
      return;
    }
    const tree = buildDraftAgentTree(agents);
    const companyName = organization.company?.name || config?.case?.title || "designed org";
    summary.textContent = `${agents.length} agents - ${companyName}`;
    treeHost.innerHTML = `<ul class="draft-tree">${tree.map(node => renderDraftAgentNode(node)).join("")}</ul>`;
  }

  function buildDraftAgentTree(agents) {
    const nodeById = new Map();
    const roots = [];
    for (const agent of agents || []) {
      const id = String(agent.id || agent.name || `agent_${nodeById.size + 1}`);
      nodeById.set(id, { ...agent, id, children: [] });
    }
    for (const node of nodeById.values()) {
      const managerId = node.manager_id == null ? "" : String(node.manager_id);
      const manager = managerId ? nodeById.get(managerId) : null;
      if (manager) {
        manager.children.push(node);
      } else {
        roots.push(node);
      }
    }
    return roots;
  }

  function renderDraftAgentNode(node) {
    const children = node.children || [];
    const role = node.role || "";
    const type = node.worker_type || "";
    const nodeClass = draftAgentClass(node);
    const responsibilities = (node.responsibilities || []).slice(0, 3).join(", ");
    const childrenMarkup = children.length
      ? `<ul class="draft-children">${children.map(renderDraftAgentNode).join("")}</ul>`
      : "";
    return `<li class="draft-node">
      <div class="draft-card ${esc(nodeClass)}">
        <div class="draft-card-head">
          <div>
            <div class="draft-agent-name">${esc(node.name || node.id)}</div>
            <div class="draft-agent-role">${esc(role || "Agent")} ${node.department ? "- " + esc(node.department) : ""}</div>
          </div>
          <span class="draft-badge">${esc(node.id)}</span>
        </div>
        <div class="draft-agent-meta">${esc(type || "worker")} - ${esc(node.model || "no model")}</div>
        ${responsibilities ? `<div class="draft-agent-responsibilities">${esc(responsibilities)}</div>` : ""}
      </div>
      ${childrenMarkup}
    </li>`;
  }

  function draftAgentClass(node) {
    const text = `${node.role || ""} ${node.worker_type || ""} ${node.department || ""}`.toLowerCase();
    if (text.includes("ceo") || text.includes("chief") || text.includes("executive")) return "executive";
    if (text.includes("hr") || text.includes("human resources")) return "hr";
    if (text.includes("manager") || text.includes("lead") || text.includes("vp")) return "manager";
    if (text.includes("worker") || text.includes("analyst") || text.includes("research")) return "worker";
    return "generic";
  }

  function renderOrgChart() {
    renderModeControls();
    visibleNodeCount = 0;
    const agentCount = agents.length || countNodes(orgChart);
    document.body.classList.toggle("mode-simple", dashboardMode === "simple");
    document.body.classList.toggle("mode-debug", dashboardMode === "debug");
    document.getElementById("org-summary").textContent = `${agentCount} agents - ${dashboardMode} mode - collapse depth ${cfg("dashboard", "collapse_depth", 3)}`;
    renderCommunicationPulses();
    document.getElementById("org-chart").innerHTML = orgChart.length
      ? `<ul class="org-tree">${orgChart.map(node => renderOrgNode(node, 0)).join("")}</ul>`
      : `<div class="muted">No agents.</div>`;
  }

  function renderModeControls() {
    document.body.classList.toggle("mode-simple", dashboardMode === "simple");
    document.body.classList.toggle("mode-debug", dashboardMode === "debug");
    const simple = document.getElementById("mode-simple");
    const debug = document.getElementById("mode-debug");
    if (simple) simple.classList.toggle("active", dashboardMode === "simple");
    if (debug) debug.classList.toggle("active", dashboardMode === "debug");
    const slider = document.getElementById("sb-mode-slider");
    if (slider) slider.style.transform = dashboardMode === "debug" ? "translateX(100%)" : "translateX(0)";
    if (simple) simple.style.color = dashboardMode === "simple" ? "#1c1b19" : "#8a867f";
    if (debug) debug.style.color = dashboardMode === "debug" ? "#1c1b19" : "#8a867f";
  }

  function renderCommunicationPulses() {
    const host = document.getElementById("communication-pulses");
    if (!host) return;
    const now = Date.now();
    communicationPulses = communicationPulses.filter(pulse => pulse.expiresAt > now);
    host.innerHTML = communicationPulses.length
      ? communicationPulses.map(pulse => `<div class="communication-pulse ${esc(pulse.kind || "")}">${esc(pulse.text)}</div>`).join("")
      : `<div class="muted">No recent assignments, discussions, or reports.</div>`;
  }

  function appendCommunicationPulse(event) {
    const pulse = communicationPulseFromEvent(event);
    if (!pulse) return;
    communicationPulses.push({
      ...pulse,
      id: event.event_id || `${Date.now()}_${communicationPulses.length}`,
      expiresAt: Date.now() + 4500,
    });
    communicationPulses = communicationPulses.slice(-8);
    renderCommunicationPulses();
    window.setTimeout(renderCommunicationPulses, 4700);
  }

  function communicationPulseFromEvent(event) {
    const payload = event.payload || {};
    const actor = event.actor_id || payload.from_agent_id || "system";
    const tool = payload.tool_name || "";
    if (event.event_type === "discussion_message") {
      const target = payload.to_agent_id || payload.target_agent_id || "peer";
      return { kind: "discuss", text: `${actor} discussed with ${target}: ${clip(payload.message || "", 80)}` };
    }
    if (event.event_type === "report_registered") {
      const target = payload.to_agent_id || "manager";
      return { kind: "report", text: `${actor} reported to ${target}: ${clip(payload.summary || payload.status || "", 80)}` };
    }
    if (event.event_type === "human_report_registered") {
      return { kind: "human", text: `${actor} reported to human: ${clip(payload.title || payload.message || "", 80)}` };
    }
    if (!isToolCallEvent(event.event_type || "")) return null;
    if (tool === "assign") {
      const target = payload.to_agent_id || payload.assigned_to || payload.target_agent_id || "worker";
      const verb = event.event_type.endsWith("_started") ? "assigning" : event.event_type.endsWith("_finished") ? "assigned" : "assign";
      return { kind: "assign", text: `${actor} ${verb} ${target}: ${clip(payload.title || payload.message || payload.task_id || "", 80)}` };
    }
    if (tool === "discuss") {
      const target = payload.to_agent_id || payload.target_agent_id || "peer";
      return { kind: "discuss", text: `${actor} discussing with ${target}: ${clip(payload.message || "", 80)}` };
    }
    if (tool === "report" || tool === "report_to_human") {
      const target = tool === "report_to_human" ? "human" : (payload.to_agent_id || "manager");
      return { kind: tool === "report_to_human" ? "human" : "report", text: `${actor} reporting to ${target}: ${clip(payload.title || payload.message || payload.report_id || "", 80)}` };
    }
    if (tool === "check_progress") {
      const target = payload.to_agent_id || payload.target_agent_id || payload.worker_id || "worker";
      return { kind: "report", text: `${actor} checking progress with ${target}` };
    }
    return null;
  }

  function renderOrgNode(node, depth) {
    const maxVisible = cfg("dashboard", "max_visible_agents", 80);
    if (visibleNodeCount >= maxVisible && depth > 0) {
      const total = 1 + Number(node.descendant_count || 0);
      return `<li class="org-node"><div class="org-placeholder">${esc(node.name)} and ${esc(total)} agent(s) hidden by display limit.</div></li>`;
    }
    visibleNodeCount += 1;
    const activity = ensureAgentActivity(node.id, node.activity);
    const summary = summarizeActivity(activity, node);
    const active = Boolean(summary.active || (node.current_task_ids || []).length || node.status === "busy" || node.status === "blocked");
    const work = (node.current_task_ids || []).join(", ") || "-";
    const children = node.children || [];
    const hasChildren = children.length > 0;
    const collapsed = isNodeCollapsed(node, depth);
    const toggle = hasChildren
      ? `<button class="tree-toggle" data-action="toggle-node" data-agent-id="${esc(node.id)}" data-depth="${esc(depth)}" title="Toggle reports">${collapsed ? "+" : "-"}</button>`
      : "";
    const childrenMarkup = hasChildren
      ? collapsed
        ? `<ul class="org-children"><li class="org-node"><div class="org-placeholder">${esc(children.length)} direct report(s), ${esc(node.descendant_count || children.length)} total below.</div></li></ul>`
        : `<ul class="org-children">${children.map(child => renderOrgNode(child, depth + 1)).join("")}</ul>`
      : "";
    if (dashboardMode !== "debug") {
      return renderSimpleOrgNode({ node, depth, summary, active, work, toggle, childrenMarkup });
    }
    return `<li class="org-node">
      <div class="agent-node ${active ? "active" : ""}" data-agent-id="${esc(node.id)}">
        <div class="agent-node-head">
          <div class="agent-title">
            ${renderAgentIcon(node.icon)}
            <div>
              <div class="agent-name">${esc(node.name)}</div>
              <div class="agent-meta">${esc(node.role)} - ${esc(node.worker_type)} - ${esc(node.model || "no model")}</div>
              <div class="agent-meta">${esc(renderModelLimit(node.model_capabilities))}</div>
              <div class="agent-meta">tasks: ${esc(work)}</div>
            </div>
          </div>
          <div class="agent-controls">
            ${toggle}
            <button class="secondary-button" data-action="agent-detail" data-agent-detail="${esc(node.id)}">Details</button>
            <span class="status ${statusClass(node.status)}">${esc(node.status)}</span>
          </div>
        </div>
        <div class="agent-summary ${active ? "active" : ""}">
          <span class="summary-dot"></span>
          <span class="summary-text">${esc(summary.text || "Idle.")}</span>
        </div>
        <div class="activity-grid">
          ${renderActivityBlock("Output", aggregateOutputItems(activity.output), renderOutputItem)}
          ${(activity.errors || []).length ? renderActivityBlock("Errors", aggregateOutputItems(activity.errors), renderErrorItem) : ""}
          ${renderActivityBlock("Tools", activity.tools, renderToolItem)}
          ${renderActivityBlock("Events", activity.events, renderEventItem)}
        </div>
      </div>
      ${childrenMarkup}
    </li>`;
  }

  function renderSimpleOrgNode({ node, summary, active, work, toggle, childrenMarkup }) {
    const status = node.status || "idle";
    return `<li class="org-node">
      <div class="agent-node simple-agent-node ${active ? "active" : ""}" data-agent-id="${esc(node.id)}">
        <div class="simple-agent-main">
          <div class="agent-title">
            ${renderAgentIcon(node.icon)}
            <div>
              <div class="agent-name">${esc(node.name || node.id)}</div>
              <div class="simple-agent-role">${esc(node.role || "Agent")} - ${esc(node.worker_type || "")}</div>
            </div>
          </div>
          <div class="agent-controls">
            ${toggle}
            <button class="secondary-button" data-action="agent-detail" data-agent-detail="${esc(node.id)}">Open</button>
            <span class="status ${statusClass(status)}">${esc(status)}</span>
          </div>
        </div>
        <div class="simple-agent-summary ${active ? "active" : ""}">
          <span class="summary-dot"></span>
          <span class="summary-text">${esc(summary.text || "Idle.")}</span>
        </div>
        <div class="simple-agent-work">tasks: ${esc(work)}</div>
      </div>
      ${childrenMarkup}
    </li>`;
  }

  function isNodeCollapsed(node, depth) {
    if (!(node.children || []).length) return false;
    if (expandedNodes.has(node.id)) return false;
    if (collapsedNodes.has(node.id)) return true;
    return depth >= cfg("dashboard", "collapse_depth", 3);
  }

  function countNodes(nodes) {
    return (nodes || []).reduce((total, node) => total + 1 + countNodes(node.children || []), 0);
  }

  function renderAgentIcon(icon) {
    const label = esc(icon?.label || "AI");
    if (icon?.image_url) {
      return `<img class="agent-icon" src="${esc(icon.image_url)}" alt="${label}" title="${label}" onerror="this.outerHTML='<span class=&quot;agent-icon-fallback&quot; title=&quot;${label}&quot;>${label.slice(0, 3)}</span>'">`;
    }
    return `<span class="agent-icon-fallback" title="${label}">${label.slice(0, 3)}</span>`;
  }

  function renderModelLimit(capabilities) {
    const context = capabilities?.context_window_tokens;
    const output = capabilities?.max_output_tokens;
    if (!context && !output) return "model limits: unknown";
    const parts = [];
    if (context) parts.push(`context ${Number(context).toLocaleString()} tokens`);
    if (output) parts.push(`output ${Number(output).toLocaleString()} tokens`);
    return `model limits: ${parts.join(", ")}`;
  }

  function renderActivityBlock(title, items, renderItem) {
    const body = (items || []).slice(-5).map(renderItem).join("") || `<div class="muted">None.</div>`;
    return `<div class="activity-block"><div class="activity-title">${esc(title)}</div>${body}</div>`;
  }

  function renderOutputItem(item) {
    const label = item.stream === "error" ? "Error" : (item.stream || "output");
    const cls = item.stream === "error" ? " activity-error" : "";
    return `<div class="output-block${cls}">
      <div><span class="pill">${esc(label)}</span></div>
      <div class="output-text">${esc(item.text || "")}</div>
    </div>`;
  }

  function renderErrorItem(item) {
    return `<div class="activity-item activity-error">Error: ${esc(item.text || "")}</div>`;
  }

  function renderHumanReports(reports) {
    const host = document.getElementById("human-reports");
    if (!host) return;
    const items = (reports || []).slice().reverse();
    host.innerHTML = items.length
      ? items.map(renderHumanReportCard).join("")
      : `<div class="muted">No CEO report to human yet.</div>`;
  }

  function renderHumanReportCard(report) {
    const confidence = report.confidence == null ? "" : ` confidence=${Number(report.confidence).toFixed(2)}`;
    const status = report.status ? ` status=${report.status}` : "";
    const decision = report.requires_decision ? `<span class="pill">human decision needed</span>` : `<span class="pill">for human</span>`;
    const nextAction = report.next_action
      ? `<div class="human-report-next">Next action: ${esc(report.next_action)}</div>`
      : "";
    return `<div class="human-report-card ${report.requires_decision ? "requires-decision" : ""}">
      <div class="human-report-head">
        <div>
          <div class="human-report-title">${esc(report.title || "CEO report to human")}</div>
          <div class="human-report-meta">from ${esc(report.from_agent_id || "-")} task=${esc(report.task_id || "-")}${esc(status)}${esc(confidence)}</div>
        </div>
        ${decision}
      </div>
      <div class="human-report-message">${esc(report.message || "")}</div>
      ${nextAction}
    </div>`;
  }

  function renderManagerReports(reports) {
    const host = document.getElementById("manager-reports");
    if (!host) return;
    const items = (reports || []).slice().reverse();
    host.innerHTML = items.length
      ? items.map(renderManagerReportCard).join("")
      : `<div class="muted">No internal manager report yet.</div>`;
  }

  function renderManagerReportCard(report) {
    const confidence = report.confidence == null ? "" : ` confidence=${Number(report.confidence).toFixed(2)}`;
    const decision = report.requires_decision ? `<span class="pill">manager decision needed</span>` : `<span class="pill">${esc(report.status || "report")}</span>`;
    const workDone = (report.work_done || []).length
      ? `<div class="manager-report-next">Work done: ${esc((report.work_done || []).join("; "))}</div>`
      : "";
    const evidence = renderReportEvidence(report.evidence || []);
    const nextAction = report.next_action
      ? `<div class="manager-report-next">Next action: ${esc(report.next_action)}</div>`
      : "";
    return `<div class="manager-report-card">
      <div class="manager-report-head">
        <div>
          <div class="manager-report-title">${esc(report.report_id || "manager report")}</div>
          <div class="manager-report-meta">${esc(report.from_agent_id || "-")} -> ${esc(report.to_agent_id || "-")} task=${esc(report.task_id || "-")}${esc(confidence)}</div>
        </div>
        ${decision}
      </div>
      <div class="manager-report-summary">${esc(report.summary || "")}</div>
      ${workDone}
      ${evidence}
      ${nextAction}
    </div>`;
  }

  function renderReportEvidence(evidenceItems) {
    const evidence = (evidenceItems || []).slice(0, 4).map(item => {
      const type = item?.type || "evidence";
      const path = item?.path || "";
      if (path) return `${esc(type)} ${renderFileLink(path, "file")}`;
      return esc(type);
    }).join(" ");
    return evidence ? `<div class="manager-report-evidence">Evidence: ${evidence}</div>` : "";
  }

  function renderToolItem(item) {
    const target = item.target_agent_id ? ` -> ${item.target_agent_id}` : "";
    const result = item.result_id ? ` ${item.result_id}` : "";
    return `<div class="activity-item">${esc(item.status || "call")} ${esc(item.tool_name || "tool")}${esc(target)}${esc(result)} ${esc(item.message || "")}</div>`;
  }

  function renderEventItem(item) {
    return `<div class="activity-item">${esc(item.event_type || "event")} ${esc(item.task_id || "")} ${esc(item.detail || "")}</div>`;
  }

  function ensureAgentActivity(agentId, fallback = null) {
    if (!agentActivity[agentId]) {
      agentActivity[agentId] = fallback || { output: [], full_output: [], errors: [], tools: [], events: [] };
    }
    if (!agentActivity[agentId].output) agentActivity[agentId].output = [];
    if (!agentActivity[agentId].full_output) agentActivity[agentId].full_output = [...agentActivity[agentId].output];
    if (!agentActivity[agentId].errors) agentActivity[agentId].errors = [];
    if (!agentActivity[agentId].tools) agentActivity[agentId].tools = [];
    if (!agentActivity[agentId].events) agentActivity[agentId].events = [];
    return agentActivity[agentId];
  }

  function summarizeActivity(activity, node = null) {
    const candidates = [];
    const tool = (activity.tools || []).slice(-1)[0];
    if (tool) {
      const target = tool.target_agent_id ? ` -> ${tool.target_agent_id}` : "";
      const verb = tool.status === "started" ? "Using" : tool.status === "finished" ? "Finished" : tool.status || "Tool";
      candidates.push({
        timestamp: tool.timestamp || "",
        text: clip(`${verb} ${tool.tool_name || "tool"}${target}`),
        task_id: tool.task_id || "",
        event_type: tool.event_type || "",
      });
    }
    const event = (activity.events || []).slice(-1)[0];
    if (event) {
      candidates.push({
        timestamp: event.timestamp || "",
        text: clip(`${event.event_type || "event"} ${event.detail || ""}`),
        task_id: event.task_id || "",
        event_type: event.event_type || "",
      });
    }
    candidates.sort((a, b) => String(a.timestamp).localeCompare(String(b.timestamp)));
    const latest = candidates[candidates.length - 1];
    if (latest) {
      return {...latest, mode: "local", active: Boolean((node?.current_task_ids || []).length || node?.status === "busy" || node?.status === "blocked")};
    }
    if ((node?.current_task_ids || []).length) {
      return {mode: "local", text: `Working on ${(node.current_task_ids || []).join(", ")}`, active: true};
    }
    if (node?.status === "busy" || node?.status === "blocked" || node?.status === "assigned" || node?.status === "in_progress") {
      return {mode: "local", text: `Status: ${node.status}`, active: true};
    }
    return {mode: "local", text: "Idle.", active: false};
  }

  function compactEventDetail(event) {
    const payload = event.payload || {};
    const keys = ["tool_name", "requested_tool_name", "assigned_to", "to_agent_id", "target_agent_id", "profile_agent_id", "status", "stream", "returncode", "timed_out", "report_id", "human_report_id", "decision", "title", "message", "url", "trace_path", "run_dir", "prompt_path", "response_path", "raw_response_path", "error_path", "attempt", "max_attempts", "next_attempt", "delay_seconds", "doc_id", "request_id", "revision"];
    return keys.filter(key => payload[key] != null).map(key => `${key}=${clip(payload[key], 120)}`).join(" ");
  }

  function appendAgentEvent(event) {
    if (selectedTaskId && !eventMatchesCurrentTaskFilter(event)) return;
    if (!event.actor_id) return;
    appendCommunicationPulse(event);
    const activity = ensureAgentActivity(event.actor_id);
    if (event.event_type === "worker_output" || event.event_type === "agent_output") {
      const item = {
        event_id: event.event_id,
        event_type: event.event_type,
        timestamp: event.timestamp,
        task_id: event.task_id,
        agent_id: event.actor_id,
        run_id: event.payload?.run_id,
        stream: event.payload?.stream,
        text: event.payload?.text,
      };
      liveOutput = appendOutputItem(liveOutput, item, cfg("activity", "global_output_limit", 200));
      if (item.stream === "error") {
        activity.errors = appendOutputItem(activity.errors, item, cfg("activity", "recent_output_items", 12));
      } else {
        activity.output = appendOutputItem(activity.output, item, cfg("activity", "recent_output_items", 12));
      }
      activity.full_output = appendOutputItem(activity.full_output, item, cfg("activity", "full_stream_limit", 200));
    } else if (isToolCallEvent(event.event_type || "")) {
      const status = (event.event_type || "").startsWith("mcp_tool_call_")
        ? (event.event_type || "").replace("mcp_tool_call_", "")
        : (event.event_type || "").replace("tool_call_", "");
      activity.tools.push({
        event_id: event.event_id,
        timestamp: event.timestamp,
        event_type: event.event_type,
        task_id: event.task_id,
        agent_id: event.actor_id,
        tool_name: event.payload?.tool_name,
        status,
        target_agent_id: event.payload?.target_agent_id || event.payload?.to_agent_id || event.payload?.assigned_to || event.payload?.worker_id,
        message: event.payload?.message || event.payload?.title || event.payload?.error || event.payload?.url || "",
        result_id: event.payload?.task_id || event.payload?.report_id || event.payload?.human_report_id || event.payload?.event_id || "",
      });
      activity.tools = activity.tools.slice(-cfg("activity", "recent_tool_items", 12));
    } else if (ACTIVITY_EVENT_TYPES.has(event.event_type || "")) {
      activity.events.push({
        event_id: event.event_id,
        timestamp: event.timestamp,
        event_type: event.event_type,
        task_id: event.task_id,
        agent_id: event.actor_id,
        detail: compactEventDetail(event),
      });
      activity.events = activity.events.slice(-cfg("activity", "recent_event_items", 10));
    } else {
      return;
    }
    activity.summary = summarizeActivity(activity, findNodeById(event.actor_id));
    renderOutput();
    renderOrgChart();
    if (selectedAgentId === event.actor_id) renderAgentDetail();
  }

  function eventMatchesCurrentTaskFilter(event) {
    if (!selectedTaskId) return true;
    if (!currentTaskScope.size) return false;
    if (event.task_id && currentTaskScope.has(event.task_id)) return true;
    const payload = event.payload || {};
    for (const key of ["task_id", "parent_task_id", "root_goal_id", "root_task_id", "final_task_id", "reviewed_task_id"]) {
      if (payload[key] && currentTaskScope.has(String(payload[key]))) return true;
    }
    for (const key of ["task_ids", "current_task_ids"]) {
      if (Array.isArray(payload[key]) && payload[key].some(value => currentTaskScope.has(String(value)))) return true;
    }
    return false;
  }

  function isToolCallEvent(eventType) {
    return eventType.startsWith("mcp_tool_call_") || eventType.startsWith("tool_call_");
  }

  function findNodeById(agentId, nodes = orgChart) {
    for (const node of nodes || []) {
      if (node.id === agentId) return node;
      const found = findNodeById(agentId, node.children || []);
      if (found) return found;
    }
    return null;
  }

  function renderAgentDetail() {
    const drawer = document.getElementById("agent-detail");
    const backdrop = document.getElementById("agent-backdrop");
    if (!selectedAgentId) {
      drawer.setAttribute("aria-hidden", "true");
      drawer.innerHTML = "";
      backdrop.hidden = true;
      document.body.classList.remove("detail-open");
      return;
    }
    const node = findNodeById(selectedAgentId) || agents.find(agent => agent.id === selectedAgentId) || {id: selectedAgentId, name: selectedAgentId, role: "", status: ""};
    const activity = ensureAgentActivity(selectedAgentId, node.activity);
    const summary = summarizeActivity(activity, node);
    const profile = node.personal_profile || {};
    const work = (node.current_task_ids || []).join(", ") || "-";
    const modelLimits = renderModelLimit(node.model_capabilities);
    const systemPrompt = node.system_prompt || "No system prompt stored for this agent.";
    drawer.setAttribute("aria-hidden", "false");
    backdrop.hidden = false;
    document.body.classList.add("detail-open");
    drawer.innerHTML = `
      <div class="detail-head">
        <div>
          <h3>${esc(node.name || node.id)}</h3>
          <div class="muted">${esc(node.role || "")} - ${esc(node.worker_type || "")} - <span class="status ${statusClass(node.status)}">${esc(node.status || "")}</span></div>
        </div>
        <button class="secondary-button" data-action="close-detail">Close</button>
      </div>
      <div class="detail-body">
        <div class="detail-section">
          <h3>Current Summary</h3>
          <div class="agent-summary ${summary.active ? "active" : ""}"><span class="summary-dot"></span><span class="summary-text">${esc(summary.text || "Idle.")}</span></div>
          <div class="agent-meta">tasks: ${esc(work)} - summary mode: ${esc(summary.mode || "local")}</div>
        </div>
        <div class="detail-section">
          <h3>Personal Profile</h3>
          <div class="activity-item">summary: ${esc(profile.summary || "No profile summary yet.")}</div>
          <div class="activity-item">specialty tags: ${esc((profile.specialty_tags || []).join(", ") || "-")}</div>
          <div class="activity-item">can do: ${esc((profile.can_do || []).slice(-6).join("; ") || "-")}</div>
          <div class="activity-item">knows about: ${esc((profile.knows_about || []).slice(-6).join("; ") || "-")}</div>
          <div class="activity-item">experiences: ${esc((profile.experiences || []).length || 0)} - revision ${esc(profile.revision || "-")}</div>
        </div>
        <div class="detail-section">
          <h3>Model And Prompt</h3>
          <div class="activity-item">model: ${esc(node.model || "runtime default")}</div>
          <div class="activity-item">${esc(modelLimits)}</div>
          <pre>${esc(systemPrompt)}</pre>
        </div>
        <div class="detail-section">
          <h3>Full Stream</h3>
          <div class="stream-box">${aggregateOutputItems(activity.full_output || activity.output || []).map(renderOutputItem).join("") || `<div class="muted output-line">No stream output.</div>`}</div>
        </div>
        <div class="detail-section">
          <h3>Tool Calls</h3>
          ${(activity.tools || []).map(renderToolItem).join("") || `<div class="muted">No tool calls.</div>`}
        </div>
        <div class="detail-section">
          <h3>Events</h3>
          ${(activity.events || []).map(renderEventItem).join("") || `<div class="muted">No events.</div>`}
        </div>
      </div>`;
  }

  function connectStream() {
    if (eventSource || !window.EventSource) return;
    eventSource = new EventSource(`/api/events/stream?after=${eventCursor}`);
    eventSource.addEventListener("open", () => {
      document.getElementById("stream-status").textContent = "live";
    });
    eventSource.addEventListener("runtime_event", (message) => {
      const item = JSON.parse(message.data);
      eventCursor = Math.max(eventCursor, item.sequence || 0);
      appendAgentEvent(item.event || {});
      scheduleRefresh(250);
    });
    eventSource.addEventListener("heartbeat", (message) => {
      const item = JSON.parse(message.data);
      eventCursor = Math.max(eventCursor, item.cursor || 0);
      if (longRfcDemoStatus?.running || realLlmBenchmarkStatus?.running || claudeSteerDemoStatus?.running || designedTaskStatus?.running) {
        refreshDemoStatus().catch(err => console.error(err));
      }
    });
    eventSource.onerror = () => {
      document.getElementById("stream-status").textContent = "reconnecting";
    };
  }

  function scheduleRefresh(delay = 250) {
    if (refreshScheduled) return;
    refreshScheduled = true;
    window.setTimeout(() => {
      refreshScheduled = false;
      refresh().catch(err => console.error(err));
    }, delay);
  }

  async function refresh() {
    const stateUrl = selectedTaskId ? `/api/state?task_id=${encodeURIComponent(selectedTaskId)}` : "/api/state";
    const res = await fetch(stateUrl, { cache: "no-store" });
    const data = await res.json();
    dashboardConfig = deepMerge(DEFAULT_CONFIG, data.config || {});
    eventCursor = Math.max(eventCursor, data.cursor || 0);
    liveOutput = data.agent_output || data.worker_output || [];
    orgChart = data.org_chart || [];
    agents = data.agents || [];
    agentActivity = data.agent_activity || {};
    currentTaskScope = new Set(data.task_filter?.task_ids || []);
    renderTaskFilterOptions(data.all_tasks || data.tasks || []);
    document.getElementById("mission").textContent = `${data.company.name} - ${data.company.mission || "No mission"}`;
    document.getElementById("updated").textContent = new Date().toLocaleTimeString();
    const active = data.tasks.filter(t => ["assigned", "in_progress", "blocked"].includes(t.status)).length;
    const completed = data.tasks.filter(t => t.status === "completed").length;
    const failed = data.tasks.filter(t => t.status === "failed").length;
    const traceLinks = (data.trace_files || []).slice(-2).map(file => renderFileLink(file.path, file.label || file.run_id || "trace")).join(" ") || "-";
    document.getElementById("metrics").innerHTML = [
      ["Agents", `${data.agents.length}${data.budget.headcount_limit ? " / " + data.budget.headcount_limit : ""}`],
      ["Active Tasks", active],
      ["Completed", completed],
      ["Failed", failed],
      ["Tokens", `${data.budget.tokens_used} / ${data.budget.token_budget_limit}`],
      ["Trace Files", traceLinks],
      ["Output Events", liveOutput.length],
    ].map(([label, value]) => `<div class="panel span-3"><h2>${esc(label)}</h2><div class="metric">${value}</div></div>`).join("");
    document.getElementById("agents").innerHTML = rows(["Agent", "Role", "Status", "Model", "Current Work"], data.agents.map(a => [
      `<button class="table-action-button" data-action="agent-detail" data-agent-detail="${esc(a.id)}" title="${esc(a.name)}">${esc(a.name)}</button>`,
      esc(a.role),
      `<span class="status ${statusClass(a.status)}">${esc(a.status)}</span>`,
      esc(a.model || "-"),
      esc((a.current_task_ids || []).join(", ") || "-")
    ]));
    renderOrgChart();
    renderStatusBar(data);
    renderSidebarTasks(data.all_tasks || data.tasks || [], data.tasks || []);
    renderPipelineCard();
    renderSimpleTaskOrg(data).catch(err => {
      const canvas = document.getElementById("simple-org-canvas");
      if (canvas) canvas.innerHTML = `<div class="simple-org-empty">Could not render organization tree: ${esc(err)}</div>`;
    });
    renderHomeHumanReports(data.human_reports || []);
    renderHumanReports(data.human_reports || []);
    renderManagerReports(data.reports || []);
    document.getElementById("tasks").innerHTML = rows(["Task", "Title", "Status", "Assignee"], data.tasks.map(t => [
      esc(t.task_id),
      esc(t.title),
      `<span class="status ${statusClass(t.status)}">${esc(t.status)}</span>`,
      esc(t.assigned_to || "-")
    ]));
    document.getElementById("runs").innerHTML = rows(["Run", "Task", "Agent", "Kind", "Status", "Runtime", "Files"], (data.agent_runs || data.worker_runs).map(r => [
      esc(r.run_id),
      esc(r.task_id || "-"),
      esc(r.agent_id),
      esc(r.kind || "worker"),
      `<span class="status ${statusClass(r.status)}">${esc(r.status)}${r.returncode != null ? " " + esc(r.returncode) : ""}</span>`,
      esc(r.executable || r.adapter || r.model || "-"),
      [
        r.prompt_path ? renderFileLink(r.prompt_path, "prompt") : "",
        r.raw_response_path ? renderFileLink(r.raw_response_path, "raw") : "",
        r.response_path ? renderFileLink(r.response_path, "response") : "",
        r.error_path ? renderFileLink(r.error_path, "error") : "",
        r.last_attempt_error_path ? renderFileLink(r.last_attempt_error_path, "attempt-error") : "",
        r.stdout_path ? renderFileLink(r.stdout_path, "stdout") : "",
        r.stderr_path ? renderFileLink(r.stderr_path, "stderr") : ""
      ].filter(Boolean).join(" ") || "-"
    ]));
    document.getElementById("reports").innerHTML = rows(["Report", "From", "To", "Task", "Status", "Summary"], data.reports.map(r => [
      esc(r.report_id),
      esc(r.from_agent_id),
      esc(r.to_agent_id),
      esc(r.task_id),
      esc(r.status),
      esc(r.summary).slice(0, 220)
    ]));
    renderOutput();
    renderAgentDetail();
    await refreshDemoStatus();
    document.getElementById("replay").textContent = data.event_replay;
    document.getElementById("trajectories").textContent = data.trajectories;
    connectStream();
  }

  function renderTaskFilterOptions(tasks) {
    const select = document.getElementById("task-filter-select");
    if (!select) return;
    const current = selectedTaskId;
    select.innerHTML = `<option value="">All tasks</option>` + (tasks || []).map(task => {
      const label = `${task.task_id} - ${task.title || ""}`.slice(0, 140);
      return `<option value="${esc(task.task_id)}">${esc(label)}</option>`;
    }).join("");
    select.value = current;
  }

  function renderFileLink(path, label = "file") {
    if (!path) return "-";
    return `<a href="/api/file?path=${encodeURIComponent(path)}" target="_blank" rel="noreferrer">${esc(label)}</a>`;
  }

  document.addEventListener("click", (event) => {
    const target = event.target.closest("[data-action]");
    if (!target) return;
    const action = target.dataset.action;
    if (action === "agent-detail") {
      selectedAgentId = target.dataset.agentDetail;
      renderAgentDetail();
    }
    if (action === "close-detail") {
      selectedAgentId = null;
      renderAgentDetail();
    }
    if (action === "toggle-node") {
      const agentId = target.dataset.agentId;
      const depth = Number(target.dataset.depth || 0);
      const node = findNodeById(agentId);
      if (!node) return;
      if (isNodeCollapsed(node, depth)) {
        expandedNodes.add(agentId);
        collapsedNodes.delete(agentId);
      } else {
        collapsedNodes.add(agentId);
        expandedNodes.delete(agentId);
      }
      renderOrgChart();
    }
    if (action === "set-dashboard-mode") {
      dashboardMode = target.dataset.mode === "debug" ? "debug" : "simple";
      localStorage.setItem("workforceDashboardMode", dashboardMode);
      renderModeControls();
      renderOrgChart();
      rerenderSimpleOrg();
    }
    if (action === "toggle-composer-config") {
      const panel = document.getElementById("composer-config-panel");
      if (panel) panel.hidden = !panel.hidden;
    }
    if (action === "toggle-debug-config") {
      dashboardMode = "debug";
      localStorage.setItem("workforceDashboardMode", dashboardMode);
      renderModeControls();
      renderOrgChart();
      document.getElementById("runtime-config-json")?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    if (action === "simple-org-zoom-in") {
      zoomSimpleOrg(0.1);
    }
    if (action === "simple-org-zoom-out") {
      zoomSimpleOrg(-0.1);
    }
    if (action === "simple-org-fit") {
      fitSimpleOrg();
    }
    if (action === "toggle-sidebar") {
      sidebarCollapsed = !sidebarCollapsed;
      const sb = document.getElementById("sidebar");
      if (sb) sb.classList.toggle("collapsed", sidebarCollapsed);
      localStorage.setItem("workforceSidebarCollapsed", sidebarCollapsed ? "1" : "0");
    }
    if (action === "open-settings") {
      selectedAgentId = null;
      openSettingsView(target.dataset.settingsView || "mcp");
    }
    if (action === "refresh-settings") {
      if (settingsView) loadSettingsView(settingsView).catch(err => setSettingsStatus(String(err), "error"));
    }
    if (action === "toggle-settings-add") {
      const view = target.dataset.settingsAddView || settingsView;
      settingsAddOpen[view] = !settingsAddOpen[view];
      if (view === "mcp" && settingsAddOpen[view]) editingMcpServerId = "";
      renderSettingsView();
    }
    if (action === "set-skill-add-tab") {
      settingsSkillAddTab = target.dataset.skillAddTab === "assign" ? "assign" : "create";
      settingsAddOpen.skills = true;
      renderSettingsView();
    }
    if (action === "toggle-mcp-edit") {
      const serverId = target.dataset.serverId || "";
      editingMcpServerId = editingMcpServerId === serverId ? "" : serverId;
      if (editingMcpServerId) settingsAddOpen.mcp = false;
      renderSettingsView();
    }
    if (action === "save-mcp-server") {
      saveMcpServer(target).catch(err => setSettingsStatus(String(err), "error"));
    }
    if (action === "delete-mcp-server") {
      deleteMcpServer(target.dataset.serverId || "").catch(err => setSettingsStatus(String(err), "error"));
    }
    if (action === "create-skill") {
      createSkill().catch(err => setSettingsStatus(String(err), "error"));
    }
    if (action === "assign-skill") {
      assignSkill().catch(err => setSettingsStatus(String(err), "error"));
    }
    if (action === "new-task") {
      closeSettingsView();
      selectedTaskId = "";
      selectedAgentId = null;
      currentTaskScope = new Set();
      const goal = document.getElementById("designed-task-goal");
      if (goal) { goal.value = ""; onGoalInput(goal); goal.focus(); }
      renderDesignedProgress();
      const scroll = document.getElementById("main-scroll");
      if (scroll) scroll.scrollTo({ top: 0, behavior: "smooth" });
      renderSidebarTasks(lastAllTasks, lastTasks);
      refresh().catch(err => console.error(err));
    }
    if (action === "select-task") {
      closeSettingsView();
      selectedTaskId = target.dataset.taskId || "";
      selectedAgentId = null;
      currentTaskScope = new Set();
      renderSidebarTasks(lastAllTasks, lastTasks);
      renderDesignedProgress();
      refresh().catch(err => console.error(err));
    }
    if (action === "rename-task") {
      const row = target.closest(".task-item-row");
      if (row) startInlineRename(row, target.dataset.taskId || "", target.dataset.title || "");
    }
    if (action === "delete-task") {
      const taskId = target.dataset.taskId || "";
      const current = target.dataset.title || taskId;
      if (!window.confirm(`Delete task "${current}"? This cannot be undone.`)) return;
      deleteTask(taskId).catch(err => console.error(err));
    }
    if (action === "report-detail") {
      const reportId = target.dataset.reportId || "";
      expandedReportId = expandedReportId === reportId ? "" : reportId;
      renderHomeHumanReports(lastHumanReports);
      document.getElementById("home-reports-section")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    if (action === "start-long-rfc-demo") {
      startLongRfcDemo().catch(err => {
        longRfcDemoStatus = { status: "failed", running: false, error: String(err) };
        renderDemoStatus();
      });
    }
    if (action === "start-real-llm-benchmark") {
      startRealLlmBenchmark().catch(err => {
        realLlmBenchmarkStatus = { status: "failed", running: false, error: String(err) };
        renderDemoStatus();
      });
    }
    if (action === "start-claude-steer-demo") {
      startClaudeSteerDemo().catch(err => {
        claudeSteerDemoStatus = { status: "failed", running: false, error: String(err) };
        renderDemoStatus();
      });
    }
    if (action === "design-task-config") {
      routePrimaryComposerAction().catch(err => {
        designedTaskStatus = { status: "failed", running: false, error: String(err) };
        finishDesignedProgress({
          state: "failed",
          phase: "model",
          title: selectedTaskId ? "Message failed." : "Design failed.",
          detail: String(err),
        });
        renderDemoStatus();
      });
    }
    if (action === "start-designed-task") {
      startDesignedTask().catch(err => {
        designedTaskStatus = { status: "failed", running: false, error: String(err) };
        finishDesignedProgress({
          state: "failed",
          phase: "request",
          title: "Task run failed.",
          detail: String(err),
        });
        renderDemoStatus();
      });
    }
    if (action === "load-runtime-config") {
      loadRuntimeConfig().catch(err => setRuntimeConfigStatus(String(err)));
    }
    if (action === "save-runtime-config") {
      saveRuntimeConfig().catch(err => setRuntimeConfigStatus(String(err)));
    }
    if (action === "export-task-trace") {
      exportSelectedTaskTrace().catch(err => setTraceExportStatus(esc(String(err))));
    }
  });

  document.addEventListener("change", (event) => {
    if (event.target?.id === "task-filter-select") {
      selectedTaskId = event.target.value || "";
      selectedAgentId = null;
      currentTaskScope = new Set();
      refresh().catch(err => console.error(err));
    }
  });

  document.addEventListener("input", (event) => {
    if (event.target?.id === "designed-task-config-json") {
      renderDraftOrganizationTree();
    }
    if (event.target?.closest?.(".settings-form")) {
      clearFieldError(event.target);
      setSettingsStatus("editing...");
    }
  });

  document.addEventListener("change", (event) => {
    if (event.target?.closest?.(".settings-form")) {
      clearFieldError(event.target);
      setSettingsStatus("editing...");
    }
  });

  sidebarCollapsed = localStorage.getItem("workforceSidebarCollapsed") === "1";
  const sbInit = document.getElementById("sidebar");
  if (sbInit) sbInit.classList.toggle("collapsed", sidebarCollapsed);
  renderDraftOrganizationTree();
  renderModeControls();
  renderSettingsNav();
  renderDesignedProgress();
  renderPipelineCard();
  loadRuntimeConfig().catch(err => setRuntimeConfigStatus(String(err)));
  refresh().catch(err => console.error(err));

  window.onSidebarSearch = onSidebarSearch;
  window.onGoalInput = onGoalInput;
  window.onGoalFocus = onGoalFocus;
  window.onGoalBlur = onGoalBlur;
  window.applyExample = applyExample;
}
