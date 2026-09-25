"use strict";
const SCOPE_HELP = {
  "knowledge:read": "Search and read the knowledge base",
  "ask": "Ask questions and get answers",
};
const $root = document.getElementById("root");
const $who = document.getElementById("who");
let me = null;

// ---------- tiny helpers (DOM is built with textContent, never innerHTML)
function h(tag, props, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "class") e.className = v;
    else if (k === "data") Object.assign(e.dataset, v);
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) e[k] = v;
  }
  for (const kid of kids.flat()) e.append(kid instanceof Node ? kid : document.createTextNode(kid ?? ""));
  return e;
}
const fmt = (d) => (d ? new Date(d).toLocaleString() : "never");
function toast(msg, bad) {
  const t = h("div", { class: "toast" + (bad ? " bad" : ""), role: "status" }, msg);
  document.body.append(t);
  setTimeout(() => t.remove(), 4000);
}
async function api(path, method = "GET", body) {
  const r = await fetch(path, {
    method, credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.success === false) {
    const err = new Error((j.error && j.error.message) || r.statusText);
    err.status = r.status;
    throw err;
  }
  return j.data;
}
const guard = (fn) => async (ev) => {
  const btn = ev && ev.currentTarget;
  if (btn) btn.disabled = true;
  try { await fn(ev); } catch (e) { toast(e.message, true); } finally { if (btn) btn.disabled = false; }
};
function table(headers, rows, empty) {
  if (!rows.length) return h("p", { class: "muted" }, empty || "Nothing here yet.");
  return h("div", { class: "tablewrap" }, h("table", {},
    h("thead", {}, h("tr", {}, headers.map((x) => h("th", {}, x)))),
    h("tbody", {}, rows.map((r) => h("tr", {}, r.map((c) => h("td", {}, c)))))));
}
const badge = (s) => h("span", { class: "badge " + s }, s);
const field = (label, input) => [h("label", { htmlFor: input.id }, label), input];

// ---------- sign in / register
function authView(mode) {
  const email = h("input", { type: "email", autocomplete: "email", required: true, id: "auth-email", name: "email" });
  const pw = h("input", { type: "password", autocomplete: mode === "login" ? "current-password" : "new-password", id: "auth-pw", name: "password" });
  const name = h("input", { type: "text", autocomplete: "name", id: "auth-name", name: "name" });
  const submit = guard(async () => {
    const body = { email: email.value, password: pw.value };
    if (mode === "register") body.name = name.value;
    me = await api(mode === "login" ? "/auth/login" : "/auth/register", "POST", body);
    boot();
  });
  pw.addEventListener("keydown", (e) => { if (e.key === "Enter") submit({ currentTarget: null }); });
  $who.textContent = "";
  $root.replaceChildren(h("div", { class: "card auth" },
    h("h2", {}, mode === "login" ? "Log in" : "Create your account"),
    mode === "register" ? field("Name", name) : [],
    field("Email", email), field("Password", pw),
    mode === "register" ? h("p", { class: "muted" }, "At least 10 characters.") : [],
    h("div", { class: "row" }, h("button", { onclick: submit }, mode === "login" ? "Log in" : "Register"),
      h("button", { class: "ghost", onclick: () => authView(mode === "login" ? "register" : "login") },
        mode === "login" ? "I need an account" : "I have an account"))));
  email.focus();
}

// ---------- shell
const TABS = [["keys", "API keys"], ["activity", "Activity"], ["account", "Account"]];
const ADMIN_TABS = [["a-overview", "Admin: Overview"], ["a-users", "Users"], ["a-keys", "All keys"],
  ["a-knowledge", "Knowledge"], ["a-audit", "Audit log"]];

function shell() {
  const content = h("div");
  const nav = h("nav", { class: "tabs" });
  const VIEWS = { keys: keysView, activity: activityView, account: accountView, "a-overview": adminOverview,
    "a-users": adminUsers, "a-keys": adminKeys, "a-knowledge": adminKnowledge, "a-audit": adminAudit };
  function go(tab) {
    [...nav.children].forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
    content.replaceChildren(h("p", { class: "muted" }, "Loading..."));
    VIEWS[tab](content).catch((e) => {
      if (e.status === 401) { me = null; authView("login"); } else content.replaceChildren(h("p", { class: "err" }, e.message));
    });
  }
  for (const [id, label] of TABS.concat(me.is_admin ? ADMIN_TABS : [])) {
    nav.append(h("button", { data: { tab: id }, onclick: () => go(id) }, label));
  }
  $who.replaceChildren(me.email + " ", h("button", { class: "ghost small", onclick: guard(async () => {
    await api("/auth/logout", "POST", {}); me = null; authView("login");
  }) }, "Log out"));
  $root.replaceChildren(nav, content);
  go("keys");
}

// ---------- API keys
async function keysView(view) {
  const { keys, scopes, max_active_keys } = await api("/me/keys");
  const newKeyBox = h("div");
  const name = h("input", { placeholder: "e.g. my-website", maxLength: 80, id: "key-name", name: "name" });
  const env = h("select", { id: "key-env", name: "environment" }, h("option", { value: "live" }, "live (60 requests/min)"), h("option", { value: "test" }, "test (20 requests/min)"));
  const expiry = h("select", { id: "key-expiry", name: "expires_in_days" }, [["", "Never expires"], ["30", "30 days"], ["90", "90 days"], ["365", "1 year"]].map(([v, t]) => h("option", { value: v }, t)));
  const checks = Object.keys(scopes).map((s) => {
    const box = h("input", { type: "checkbox", value: s, checked: s === "knowledge:read", id: "check-" + s.replace(':', '-'), name: "scopes" });
    return { s, box, node: h("label", { htmlFor: box.id }, box, h("span", {}, h("code", {}, s), " ", SCOPE_HELP[s] || scopes[s])) };
  });

  function showNewKey(raw, message) {
    newKeyBox.replaceChildren(h("div", { class: "keybox", role: "alert" },
      h("div", { class: "warn" }, message), h("code", {}, raw),
      h("button", { onclick: guard(async () => { await navigator.clipboard.writeText(raw); toast("Copied"); }) }, "Copy API key")));
  }
  const create = guard(async () => {
    const picked = checks.filter((c) => c.box.checked).map((c) => c.s);
    const r = await api("/me/keys", "POST", { name: name.value, environment: env.value, scopes: picked,
      expires_in_days: expiry.value ? Number(expiry.value) : null });
    name.value = "";
    showNewKey(r.api_key, r.message);
    await refresh();
  });

  const list = h("div");
  async function refresh() {
    const data = await api("/me/keys");
    const rows = data.keys.map((k) => {
      const detail = h("div", { class: "hidden" });
      const usage = guard(async () => {
        if (!detail.classList.contains("hidden")) { detail.classList.add("hidden"); return; }
        const u = await api(`/me/keys/${k.id}/usage`);
        const max = Math.max(1, ...u.per_day.map((d) => Number(d.count)));
        detail.replaceChildren(h("p", { class: "muted" }, `Requests in the last 30 days: ${u.total_30d}`),
          u.per_day.map((d) => { const bar = h("div", { class: "bar" }); bar.style.width = (Number(d.count) / max) * 60 + "%";
            return h("div", { class: "barrow" }, h("span", { class: "lbl" }, d.day.slice(5)), bar, String(d.count)); }),
          table(["Time", "Method", "Endpoint", "Status"], u.recent.map((r) => [fmt(r.created_at), r.method, r.endpoint, String(r.status_code)]), "No requests yet."));
        detail.classList.remove("hidden");
      });
      const rotate = guard(async () => {
        const hrs = prompt("Keep the OLD key working for how many hours? (0 = stop it now, max 168)", "24");
        if (hrs === null) return;
        const r = await api(`/me/keys/${k.id}/rotate`, "POST", { grace_hours: Number(hrs) });
        showNewKey(r.api_key, r.message); await refresh();
      });
      const revoke = guard(async () => { if (confirm("Revoke this key? It stops working immediately.")) { await api(`/me/keys/${k.id}/revoke`, "POST", {}); await refresh(); } });
      const del = guard(async () => { if (confirm("Delete this key and its usage history?")) { await api(`/me/keys/${k.id}`, "DELETE", {}); await refresh(); } });
      const copy_id = guard(async () => { await navigator.clipboard.writeText(String(k.id)); toast("Copied ID"); });
      const live = k.status === "active";
      return [[h("strong", {}, k.name), h("div", { class: "muted" }, k.environment)], h("code", {}, k.key_prefix + "..."),
        k.scopes.join(", "), fmt(k.created_at), k.expires_at ? fmt(k.expires_at) : "never", fmt(k.last_used_at), badge(k.status),
        h("div", { class: "row" }, h("button", { class: "ghost small", onclick: copy_id }, "Copy ID"), h("button", { class: "ghost small", onclick: usage }, "Usage"),
          live ? h("button", { class: "ghost small", onclick: rotate }, "Rotate") : [],
          live ? h("button", { class: "ghost small", onclick: revoke }, "Revoke") : [],
          h("button", { class: "danger small", onclick: del }, "Delete"), detail)];
    });
    list.replaceChildren(table(["Name", "Prefix", "Scopes", "Created", "Expires", "Last used", "Status", "Actions"], rows,
      "You have no keys yet. Create your first one above."));
  }
  view.replaceChildren(
    h("div", { class: "card" }, h("h3", {}, "Create an API key"),
      field("Name", name), field("Environment", env), field("Expires", expiry),
      h("label", {}, "Scopes (give the key only what it needs)"), h("div", { class: "checks" }, checks.map((c) => c.node)),
      h("div", { class: "row" }, h("button", { onclick: create }, "Create key"),
        h("span", { class: "muted" }, `Up to ${max_active_keys} active keys.`)), newKeyBox),
    h("div", { class: "card" }, h("h3", {}, "Your keys"), list));
  await refresh();
}

async function activityView(view) {
  const { events } = await api("/me/audit");
  view.replaceChildren(h("div", { class: "card" }, h("h3", {}, "Recent security activity"),
    table(["Time", "Event", "Request ID", "Details"], events.map((e) => [fmt(e.created_at), e.event_type, h("code", {}, e.request_id || ""),
      Object.keys(e.metadata || {}).length ? JSON.stringify(e.metadata) : ""]))));
}

async function accountView(view) {
  const cur = h("input", { type: "password", autocomplete: "current-password", id: "acc-cur", name: "current_password" });
  const next = h("input", { type: "password", autocomplete: "new-password", id: "acc-next", name: "new_password" });
  view.replaceChildren(h("div", { class: "card" }, h("h3", {}, "Account"), h("p", {}, me.email),
    h("h3", {}, "Change password"), field("Current password", cur), field("New password (10+ characters)", next),
    h("p", { class: "muted" }, "Changing your password logs you out of all other devices."),
    h("div", { class: "row" }, h("button", { onclick: guard(async () => {
      await api("/auth/change-password", "POST", { current_password: cur.value, new_password: next.value });
      cur.value = next.value = ""; toast("Password changed");
    }) }, "Change password"))));
}

// ---------- admin
async function adminOverview(view) {
  const s = await api("/admin/api/stats");
  const stat = (n, l) => h("div", { class: "card stat" }, h("b", {}, String(n)), h("span", {}, l));
  view.replaceChildren(h("div", { class: "cards" }, stat(s.users, "Users"), stat(s.new_users_7d, "New users (7 days)"),
    stat(s.active_keys, "Active keys"), stat(s.requests_24h, "API requests (24h)"),
    stat(s.auth_failures_24h, "Auth failures (24h)"), stat(s.knowledge_entries, "Knowledge entries")));
}

async function adminUsers(view) {
  const { users } = await api("/admin/api/users");
  view.replaceChildren(h("div", { class: "card" }, table(["Email", "Name", "Role", "Active keys", "Joined", "Last login", "Status", ""],
    users.map((u) => [u.email, u.name, u.is_admin ? "admin" : "user", String(u.active_keys), fmt(u.created_at), fmt(u.last_login_at),
      badge(u.is_active ? "active" : "off"),
      u.email === me.email ? "" : h("button", { class: (u.is_active ? "danger" : "ghost") + " small", onclick: guard(async () => {
        await api(`/admin/api/users/${u.id}/${u.is_active ? "disable" : "enable"}`, "POST", {}); adminUsers(view); }) }, u.is_active ? "Disable" : "Enable")]))));
}

async function adminKeys(view) {
  const { keys } = await api("/admin/api/keys");
  view.replaceChildren(h("div", { class: "card" }, table(["Owner", "Name", "Prefix", "Scopes", "Last used", "Status", ""],
    keys.map((k) => [k.owner_email, k.name, h("code", {}, k.key_prefix + "..."), k.scopes.join(", "), fmt(k.last_used_at), badge(k.status),
      k.status === "revoked" ? "" : h("button", { class: "danger small", onclick: guard(async () => {
        if (confirm("Revoke this key?")) { await api(`/admin/api/keys/${k.id}/revoke`, "POST", {}); adminKeys(view); } }) }, "Revoke")]))));
}

async function adminKnowledge(view) {
  let editId = null;
  const cat = h("input", { placeholder: "python, git, github...", value: "python", id: "k-cat", name: "category" });
  const title = h("input", { id: "k-title", name: "title" }); const content = h("textarea", { id: "k-content", name: "content" }); const tags = h("input", { placeholder: "tag1, tag2", id: "k-tags", name: "tags" });
  const importBox = h("textarea", { placeholder: '[{"category":"python","title":"Lists","content":"..."}]' });
  const list = h("div"); const save = h("button", {}, "Save entry");
  const reset = () => { editId = null; title.value = content.value = tags.value = ""; save.textContent = "Save entry"; };
  async function load() {
    const { entries } = await api("/admin/api/knowledge");
    list.replaceChildren(table(["Category", "Title", "Updated", ""], entries.map((e) => [h("span", { class: "badge" }, e.category), e.title, fmt(e.updated_at),
      h("div", { class: "row" }, h("button", { class: "ghost small", onclick: () => { editId = e.id; cat.value = e.category; title.value = e.title; content.value = e.content;
        tags.value = (e.tags || []).join(", "); save.textContent = "Update entry"; scrollTo(0, 0); } }, "Edit"),
      h("button", { class: "danger small", onclick: guard(async () => { if (confirm("Delete this entry?")) { await api(`/admin/api/knowledge/${e.id}`, "DELETE", {}); await load(); } }) }, "Delete"))])));
  }
  save.onclick = guard(async () => {
    const body = { category: cat.value, title: title.value, content: content.value, tags: tags.value };
    await (editId ? api(`/admin/api/knowledge/${editId}`, "PUT", body) : api("/admin/api/knowledge", "POST", body));
    reset(); await load(); toast("Saved");
  });
  const imp = guard(async () => {
    const r = await api("/admin/api/knowledge/import", "POST", JSON.parse(importBox.value));
    importBox.value = ""; toast(`Imported ${r.imported} entries`); await load();
  });
  view.replaceChildren(
    h("div", { class: "card" }, h("h3", {}, "Add or edit an entry"), field("Category", cat), field("Title", title), field("Content", content), field("Tags", tags),
      h("div", { class: "row" }, save, h("button", { class: "ghost", onclick: reset }, "Clear"))),
    h("div", { class: "card" }, h("h3", {}, "Bulk import (same category + title updates the old entry)"), importBox,
      h("div", { class: "row" }, h("button", { class: "ghost", onclick: imp }, "Import JSON list"))),
    h("div", { class: "card" }, list));
  await load();
}

async function adminAudit(view) {
  const { events } = await api("/admin/api/audit");
  view.replaceChildren(h("div", { class: "card" }, h("h3", {}, "Latest 200 security events"),
    table(["Time", "User", "Event", "Request ID", "Details"], events.map((e) => [fmt(e.created_at), e.user_email || "-", e.event_type,
      h("code", {}, e.request_id || ""), Object.keys(e.metadata || {}).length ? JSON.stringify(e.metadata) : ""]))));
}

async function boot() {
  try { me = me || await api("/me"); shell(); } catch (_) { authView("login"); }
}
boot();
