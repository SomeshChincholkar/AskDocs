(() => {
  "use strict";

  const TOKEN_KEY = "askdocs_token";
  const EXPIRY_KEY = "askdocs_token_expires_at";
  const ANONYMOUS_KEY = "askdocs_is_anonymous";
  const USERNAME_KEY = "askdocs_username";
  const STORAGE_KEYS = [TOKEN_KEY, EXPIRY_KEY, ANONYMOUS_KEY, USERNAME_KEY];
  const state = {
    sessions: [],
    sessionId: null,
    documents: [],
    busy: false,
    dragDepth: 0,
    authMode: "login"
  };
  const el = (id) => document.getElementById(id);
  const sessionList = el("session-list");
  const messages = el("messages");
  const welcome = el("welcome");
  const question = el("question");
  const sendButton = el("send-button");
  const fileInput = el("file-input");
  const dropOverlay = el("drop-overlay");

  function toast(message, success = false) {
    const node = document.createElement("div");
    node.className = `toast${success ? " success" : ""}`;
    node.textContent = message;
    el("toast-region").append(node);
    window.setTimeout(() => node.remove(), 4800);
  }

  function clearAuth() {
    for (const key of STORAGE_KEYS) localStorage.removeItem(key);
  }

  function storeAuth(result) {
    localStorage.setItem(TOKEN_KEY, result.access_token);
    localStorage.setItem(EXPIRY_KEY, result.expires_at);
    localStorage.setItem(ANONYMOUS_KEY, String(result.user.is_anonymous));
    if (result.user.username) localStorage.setItem(USERNAME_KEY, result.user.username);
    else localStorage.removeItem(USERNAME_KEY);
  }

  function hasUsableToken() {
    const token = localStorage.getItem(TOKEN_KEY);
    const expiresAt = Date.parse(localStorage.getItem(EXPIRY_KEY) || "");
    return Boolean(token && Number.isFinite(expiresAt) && expiresAt > Date.now());
  }

  function formatApiError(response, body) {
    if (response.status === 413) return "That file is too large. Try a smaller file.";
    if (response.status === 415) return "That file type is not supported.";
    if (response.status === 429) return body.detail || "Too many attempts. Please try again later.";
    if (Array.isArray(body.detail)) {
      return body.detail.map((item) => item.msg).filter(Boolean).join(" ");
    }
    return body.detail || `Request failed (${response.status}).`;
  }

  async function apiFetch(url, options = {}, recoverAuth = true) {
    const headers = new Headers(options.headers || {});
    const token = localStorage.getItem(TOKEN_KEY);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    const response = await fetch(url, {
      ...options,
      credentials: "omit",
      headers
    });

    if (!response.ok) {
      let body = {};
      try {
        body = await response.clone().json();
      } catch (_) {
        body = {};
      }
      if (
        response.status === 401 &&
        recoverAuth &&
        ["token_invalid", "no_token"].includes(body.code)
      ) {
        const wasRegistered = localStorage.getItem(ANONYMOUS_KEY) === "false";
        clearAuth();
        if (wasRegistered) toast("Your session expired, please log in again");
        await bootstrapAnonymous();
        window.location.reload();
        throw new Error("Session refreshed.");
      }
      throw new Error(formatApiError(response, body));
    }
    return response;
  }

  async function json(url, options) {
    const response = await apiFetch(url, options);
    if (response.status === 204) return null;
    return response.json();
  }

  async function bootstrapAnonymous() {
    if (hasUsableToken()) return;
    clearAuth();
    const response = await apiFetch("/api/auth/anonymous", { method: "POST" }, false);
    const created = await response.json();
    if (hasUsableToken()) return;
    storeAuth(created);
  }

  function updateAccountHeader() {
    const isAnonymous = localStorage.getItem(ANONYMOUS_KEY) !== "false";
    el("auth-open").hidden = !isAnonymous;
    el("logout").hidden = isAnonymous;
    el("account-hint").hidden = !isAnonymous;
    el("account-username").textContent = isAnonymous
      ? ""
      : localStorage.getItem(USERNAME_KEY) || "Account";
  }

  function appendInlineText(parent, text) {
    const pattern = /\*\*[^*]+\*\*|`[^`]+`/g;
    let start = 0;
    let match;
    while ((match = pattern.exec(text)) !== null) {
      if (match.index > start) {
        parent.append(document.createTextNode(text.slice(start, match.index)));
      }
      const token = match[0];
      const node = document.createElement(token.startsWith("**") ? "strong" : "code");
      node.textContent = token.startsWith("**") ? token.slice(2, -2) : token.slice(1, -1);
      parent.append(node);
      start = pattern.lastIndex;
    }
    if (start < text.length) parent.append(document.createTextNode(text.slice(start)));
  }

  function createRichText(content) {
    const fragment = document.createDocumentFragment();
    const lines = String(content).split("\n");
    let index = 0;
    while (index < lines.length) {
      if (!lines[index].trim()) {
        index += 1;
        continue;
      }
      if (lines[index].startsWith("```")) {
        const codeLines = [];
        index += 1;
        while (index < lines.length && !lines[index].startsWith("```")) {
          codeLines.push(lines[index]);
          index += 1;
        }
        if (index < lines.length) index += 1;
        const pre = document.createElement("pre");
        const code = document.createElement("code");
        code.textContent = codeLines.join("\n");
        pre.append(code);
        fragment.append(pre);
        continue;
      }
      if (/^[-*] /.test(lines[index])) {
        const list = document.createElement("ul");
        while (index < lines.length && /^[-*] /.test(lines[index])) {
          const item = document.createElement("li");
          appendInlineText(item, lines[index].slice(2));
          list.append(item);
          index += 1;
        }
        fragment.append(list);
        continue;
      }
      const paragraph = document.createElement("p");
      let firstLine = true;
      while (
        index < lines.length &&
        lines[index].trim() &&
        !lines[index].startsWith("```") &&
        !/^[-*] /.test(lines[index])
      ) {
        if (!firstLine) paragraph.append(document.createElement("br"));
        appendInlineText(paragraph, lines[index]);
        firstLine = false;
        index += 1;
      }
      fragment.append(paragraph);
    }
    return fragment;
  }

  function updateComposer() {
    const ready = state.documents.some((doc) => doc.status === "ready");
    sendButton.disabled = state.busy || !ready || !question.value.trim();
    el("composer-hint").textContent = ready
      ? "Ask a question about the documents in this chat."
      : "Add a document and wait for it to finish processing before asking.";
  }

  function renderSessions() {
    sessionList.replaceChildren();
    if (!state.sessions.length) {
      const empty = document.createElement("div");
      empty.className = "empty-sessions";
      empty.textContent = "Your chats will appear here";
      sessionList.append(empty);
      return;
    }
    for (const session of state.sessions) {
      const row = document.createElement("div");
      row.className = `session-entry${session.id === state.sessionId ? " active" : ""}`;
      const open = document.createElement("button");
      open.className = "session-open";
      open.type = "button";
      open.textContent = session.title;
      open.title = session.title;
      open.addEventListener("click", () => selectSession(session.id));
      const rename = document.createElement("button");
      rename.className = "session-action";
      rename.type = "button";
      rename.textContent = "✎";
      rename.title = "Rename chat";
      rename.setAttribute("aria-label", `Rename ${session.title}`);
      rename.addEventListener("click", (event) => {
        event.stopPropagation();
        renameSession(session);
      });
      const remove = document.createElement("button");
      remove.className = "session-action";
      remove.type = "button";
      remove.textContent = "×";
      remove.title = "Delete chat";
      remove.setAttribute("aria-label", `Delete ${session.title}`);
      remove.addEventListener("click", (event) => {
        event.stopPropagation();
        deleteSession(session);
      });
      row.append(open, rename, remove);
      sessionList.append(row);
    }
  }

  function appendSources(body, sources) {
    if (!sources.length) return;
    const sourceList = document.createElement("div");
    sourceList.className = "sources";
    for (const source of sources) {
      const chip = document.createElement("span");
      chip.className = "source-chip";
      chip.textContent = `${source.filename}, p.${source.page}`;
      chip.title = source.snippet || "";
      sourceList.append(chip);
    }
    body.append(sourceList);
  }

  function addMessage(role, content, sources = []) {
    const row = document.createElement("article");
    row.className = `message ${role}`;
    if (role === "assistant") {
      const avatar = document.createElement("div");
      avatar.className = "avatar";
      avatar.textContent = "✦";
      row.append(avatar);
    }
    const body = document.createElement("div");
    body.className = "bubble";
    body.append(createRichText(content));
    appendSources(body, sources);
    row.append(body);
    messages.append(row);
    welcome.hidden = true;
    el("conversation").scrollTop = el("conversation").scrollHeight;
    return body;
  }

  function renderMessages(items) {
    messages.replaceChildren();
    welcome.hidden = items.length > 0;
    for (const item of items) addMessage(item.role, item.content, item.sources || []);
    el("conversation").scrollTop = el("conversation").scrollHeight;
  }

  async function refreshSessions() {
    state.sessions = await json("/api/sessions");
    renderSessions();
  }

  async function refreshDocuments() {
    if (!state.sessionId) return;
    state.documents = await json(`/api/sessions/${state.sessionId}/documents`);
    renderDocuments();
  }

  function renderDocuments() {
    const list = el("document-list");
    list.replaceChildren();
    el("file-count").textContent = `${state.documents.length} ${state.documents.length === 1 ? "file" : "files"}`;
    for (const doc of state.documents) {
      const chip = document.createElement("div");
      chip.className = "document-chip";
      const dot = document.createElement("span");
      dot.className = `status-dot ${doc.status}`;
      const name = document.createElement("span");
      name.className = "document-name";
      name.textContent = doc.filename;
      name.title = doc.filename;
      const status = document.createElement("span");
      status.className = `status-label ${doc.status}`;
      status.textContent = doc.status === "ready" ? "Ready" : doc.status === "failed" ? "Failed" : "Reading";
      if (doc.error) status.title = doc.error;
      const remove = document.createElement("button");
      remove.className = "remove-document";
      remove.type = "button";
      remove.textContent = "×";
      remove.title = `Remove ${doc.filename}`;
      remove.setAttribute("aria-label", `Remove ${doc.filename}`);
      remove.addEventListener("click", () => removeDocument(doc));
      chip.append(dot, name, status, remove);
      list.append(chip);
    }
    updateComposer();
  }

  async function selectSession(id) {
    if (state.sessionId === id) return;
    state.sessionId = id;
    el("sidebar").classList.remove("open");
    try {
      const session = state.sessions.find((item) => item.id === id);
      el("session-title").textContent = session ? session.title : "Chat";
      renderSessions();
      const [history] = await Promise.all([
        json(`/api/sessions/${id}/messages`),
        refreshDocuments()
      ]);
      renderMessages(history);
    } catch (error) {
      toast(error.message);
    }
  }

  async function createSession() {
    try {
      const session = await json("/api/sessions", {
        method: "POST",
        body: JSON.stringify({ title: "New chat" })
      });
      await refreshSessions();
      await selectSession(session.id);
      question.focus();
    } catch (error) {
      toast(error.message);
    }
  }

  async function loadMostRecentSession() {
    state.sessionId = null;
    state.documents = [];
    messages.replaceChildren();
    await refreshSessions();
    if (state.sessions.length) await selectSession(state.sessions[0].id);
    else await createSession();
  }

  async function renameSession(session) {
    const title = window.prompt("Rename chat", session.title);
    if (title === null || !title.trim()) return;
    try {
      const updated = await json(`/api/sessions/${session.id}`, {
        method: "PATCH",
        body: JSON.stringify({ title: title.trim() })
      });
      if (state.sessionId === updated.id) el("session-title").textContent = updated.title;
      await refreshSessions();
    } catch (error) {
      toast(error.message);
    }
  }

  async function deleteSession(session) {
    if (!window.confirm(`Delete “${session.title}” and its documents?`)) return;
    try {
      await apiFetch(`/api/sessions/${session.id}`, { method: "DELETE" });
      if (state.sessionId === session.id) {
        state.sessionId = null;
        state.documents = [];
        messages.replaceChildren();
        welcome.hidden = false;
        await loadMostRecentSession();
      } else {
        await refreshSessions();
      }
    } catch (error) {
      toast(error.message);
    }
  }

  async function removeDocument(doc) {
    try {
      await apiFetch(`/api/documents/${doc.id}`, { method: "DELETE" });
      state.documents = state.documents.filter((item) => item.id !== doc.id);
      renderDocuments();
      toast("Document removed.", true);
    } catch (error) {
      toast(error.message);
    }
  }

  async function pollDocument(id, sessionId) {
    while (state.sessionId === sessionId) {
      try {
        const status = await json(`/api/documents/${id}/status`);
        if (state.sessionId !== sessionId) return;
        const doc = state.documents.find((item) => item.id === id);
        if (doc) Object.assign(doc, status);
        renderDocuments();
        if (status.status !== "processing") {
          if (status.status === "failed") toast(status.error || "This document could not be processed.");
          else toast("Document is ready to use.", true);
          return;
        }
      } catch (error) {
        toast(error.message);
        return;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
    }
  }

  async function uploadFiles(files) {
    if (!files.length || !state.sessionId) return;
    const form = new FormData();
    for (const file of files) form.append("files", file);
    try {
      const result = await json(`/api/sessions/${state.sessionId}/documents`, {
        method: "POST",
        body: form
      });
      await refreshDocuments();
      for (const doc of result.documents) pollDocument(doc.id, state.sessionId);
    } catch (error) {
      toast(error.message);
    } finally {
      fileInput.value = "";
    }
  }

  async function readSse(response, answerBody) {
    if (!response.body) throw new Error("Streaming is not supported by this browser.");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let answer = "";
    let sources = [];
    async function consume(frame) {
      let eventName = "message";
      let data = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (!data) return;
      const payload = JSON.parse(data);
      if (eventName === "token") {
        answer += payload.text || "";
        answerBody.replaceChildren(createRichText(answer));
        el("conversation").scrollTop = el("conversation").scrollHeight;
      } else if (eventName === "sources") {
        sources = payload;
      } else if (eventName === "error") {
        throw new Error(payload.message || "The answer could not be generated.");
      } else if (eventName === "done") {
        appendSources(answerBody, sources);
      }
    }
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      buffer = buffer.replace(/\r\n/g, "\n");
      const frames = buffer.split("\n\n");
      buffer = frames.pop() || "";
      for (const frame of frames) await consume(frame);
      if (done) break;
    }
    if (buffer.trim()) await consume(buffer);
  }

  async function sendQuestion(text) {
    if (!text.trim() || !state.sessionId || sendButton.disabled) return;
    addMessage("user", text);
    const answerBody = addMessage("assistant", "");
    question.value = "";
    state.busy = true;
    updateComposer();
    try {
      const response = await apiFetch(`/api/sessions/${state.sessionId}/ask`, {
        method: "POST",
        body: JSON.stringify({ question: text })
      });
      await readSse(response, answerBody);
      await refreshSessions();
      const active = state.sessions.find((item) => item.id === state.sessionId);
      if (active) el("session-title").textContent = active.title;
    } catch (error) {
      answerBody.textContent = "I couldn't complete that answer.";
      toast(error.message);
    } finally {
      state.busy = false;
      updateComposer();
      question.focus();
    }
  }

  function setAuthMode(mode) {
    state.authMode = mode;
    const isLogin = mode === "login";
    el("login-tab").classList.toggle("active", isLogin);
    el("register-tab").classList.toggle("active", !isLogin);
    el("login-tab").setAttribute("aria-selected", String(isLogin));
    el("register-tab").setAttribute("aria-selected", String(!isLogin));
    el("auth-title").textContent = isLogin ? "Welcome back" : "Create your account";
    el("auth-submit").textContent = isLogin ? "Log in" : "Create account";
    el("auth-password").autocomplete = isLogin ? "current-password" : "new-password";
    el("auth-error").hidden = true;
    el("auth-error").textContent = "";
  }

  function openAuthDialog(mode = "login") {
    setAuthMode(mode);
    el("auth-dialog").showModal();
    el("auth-username").focus();
  }

  async function submitAuth(event) {
    event.preventDefault();
    const submit = el("auth-submit");
    const error = el("auth-error");
    error.hidden = true;
    submit.disabled = true;
    submit.textContent = "Please wait…";
    let result;
    try {
      result = await json(`/api/auth/${state.authMode}`, {
        method: "POST",
        body: JSON.stringify({
          username: el("auth-username").value,
          password: el("auth-password").value
        })
      });
    } catch (requestError) {
      error.textContent = requestError.message;
      error.hidden = false;
      submit.disabled = false;
      submit.textContent = state.authMode === "login" ? "Log in" : "Create account";
      return;
    } finally {
      if (!result) {
        submit.disabled = false;
        submit.textContent = state.authMode === "login" ? "Log in" : "Create account";
      }
    }
    storeAuth(result);
    el("auth-dialog").close();
    updateAccountHeader();
    try {
      await loadMostRecentSession();
    } catch (loadError) {
      toast(`Signed in, but chats could not be loaded: ${loadError.message}`);
    }
    if (result.merged && result.merged.sessions > 0) {
      toast(`Moved ${result.merged.sessions} previous chats to your account`, true);
    }
  }

  async function logout() {
    try {
      await apiFetch("/api/auth/logout-all", { method: "POST" });
    } catch (error) {
      if (error.message === "Session refreshed.") return;
      toast(`Could not invalidate other sessions: ${error.message}`);
    }
    clearAuth();
    window.location.reload();
  }

  el("new-chat").addEventListener("click", createSession);
  el("rename-chat").addEventListener("click", () => {
    const session = state.sessions.find((item) => item.id === state.sessionId);
    if (session) renameSession(session);
  });
  el("mobile-menu").addEventListener("click", () => el("sidebar").classList.toggle("open"));
  el("attach-button").addEventListener("click", () => fileInput.click());
  el("welcome-upload").addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => uploadFiles([...fileInput.files]));
  question.addEventListener("input", updateComposer);
  question.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!sendButton.disabled) sendQuestion(question.value);
    }
  });
  el("composer").addEventListener("submit", (event) => {
    event.preventDefault();
    sendQuestion(question.value);
  });
  el("auth-open").addEventListener("click", () => openAuthDialog("login"));
  el("logout").addEventListener("click", logout);
  el("login-tab").addEventListener("click", () => setAuthMode("login"));
  el("register-tab").addEventListener("click", () => setAuthMode("register"));
  el("auth-close").addEventListener("click", () => el("auth-dialog").close());
  el("auth-dialog").addEventListener("close", () => {
    el("auth-password").value = "";
    el("auth-password").type = "password";
    el("password-toggle").textContent = "Show";
  });
  el("auth-form").addEventListener("submit", submitAuth);
  el("password-toggle").addEventListener("click", () => {
    const password = el("auth-password");
    password.type = password.type === "password" ? "text" : "password";
    el("password-toggle").textContent = password.type === "password" ? "Show" : "Hide";
  });
  el("auth-dialog").addEventListener("click", (event) => {
    if (event.target === el("auth-dialog")) el("auth-dialog").close();
  });

  for (const eventName of ["dragenter", "dragover"]) {
    document.addEventListener(eventName, (event) => {
      if (!event.dataTransfer?.types.includes("Files")) return;
      event.preventDefault();
      state.dragDepth += eventName === "dragenter" ? 1 : 0;
      dropOverlay.classList.add("visible");
    });
  }
  document.addEventListener("dragleave", (event) => {
    if (!event.dataTransfer?.types.includes("Files")) return;
    state.dragDepth = Math.max(0, state.dragDepth - 1);
    if (!state.dragDepth) dropOverlay.classList.remove("visible");
  });
  document.addEventListener("drop", (event) => {
    if (!event.dataTransfer?.files.length) return;
    event.preventDefault();
    state.dragDepth = 0;
    dropOverlay.classList.remove("visible");
    uploadFiles([...event.dataTransfer.files]);
  });

  (async () => {
    try {
      await bootstrapAnonymous();
      updateAccountHeader();
      await loadMostRecentSession();
    } catch (error) {
      toast(error.message);
    }
    updateComposer();
  })();
})();
