(() => {
  "use strict";

  const state = { sessions: [], sessionId: null, documents: [], busy: false, dragDepth: 0 };
  const el = (id) => document.getElementById(id);
  const sessionList = el("session-list");
  const messages = el("messages");
  const welcome = el("welcome");
  const question = el("question");
  const sendButton = el("send-button");
  const fileInput = el("file-input");
  const dropOverlay = el("drop-overlay");

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[char]);
  }

  function markdown(value) {
    let safe = escapeHtml(value);
    safe = safe.replace(/```([\s\S]*?)```/g, "<pre><code>$1</code></pre>");
    safe = safe.replace(/`([^`]+)`/g, "<code>$1</code>");
    safe = safe.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    safe = safe.replace(/(^|\n)[-*] (.+)/g, "$1<li>$2</li>");
    safe = safe.replace(/(<li>[\s\S]*?<\/li>)(?:\n<li>[\s\S]*?<\/li>)*/g, "<ul>$&</ul>");
    return safe.split(/\n{2,}/).map((part) => part.startsWith("<ul>") || part.startsWith("<pre>")
      ? part
      : `<p>${part.replace(/\n/g, "<br>")}</p>`).join("");
  }

  function toast(message, success = false) {
    const node = document.createElement("div");
    node.className = `toast${success ? " success" : ""}`;
    node.textContent = message;
    el("toast-region").append(node);
    window.setTimeout(() => node.remove(), 4800);
  }

  async function api(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      ...options,
      headers: { ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }), ...options.headers }
    });
    if (!response.ok) {
      let detail = `Request failed (${response.status}).`;
      try {
        const body = await response.json();
        detail = body.detail || detail;
      } catch (_) { /* Keep the status-based message for non-JSON responses. */ }
      if (response.status === 413) detail = "That file is too large. Try a smaller file.";
      if (response.status === 415) detail = "That file type is not supported.";
      if (response.status === 429) detail = "You’re sending requests too quickly. Please try again later.";
      throw new Error(detail);
    }
    if (response.status === 204) return null;
    return response;
  }

  async function json(url, options) {
    const response = await api(url, options);
    return response.json();
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
    body.innerHTML = markdown(content);
    if (sources.length) {
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
      await api(`/api/sessions/${session.id}`, { method: "DELETE" });
      if (state.sessionId === session.id) {
        state.sessionId = null;
        state.documents = [];
        messages.replaceChildren();
        welcome.hidden = false;
        await refreshSessions();
        if (state.sessions.length) await selectSession(state.sessions[0].id);
        else await createSession();
      } else {
        await refreshSessions();
      }
    } catch (error) {
      toast(error.message);
    }
  }

  async function removeDocument(doc) {
    try {
      await api(`/api/documents/${doc.id}`, { method: "DELETE" });
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
        answerBody.innerHTML = markdown(answer);
        el("conversation").scrollTop = el("conversation").scrollHeight;
      } else if (eventName === "sources") {
        sources = payload;
      } else if (eventName === "error") {
        throw new Error(payload.message || "The answer could not be generated.");
      } else if (eventName === "done") {
        if (sources.length) {
          const holder = document.createElement("div");
          holder.className = "sources";
          for (const source of sources) {
            const chip = document.createElement("span");
            chip.className = "source-chip";
            chip.textContent = `${source.filename}, p.${source.page}`;
            chip.title = source.snippet || "";
            holder.append(chip);
          }
          answerBody.append(holder);
        }
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
    question.style.height = "auto";
    state.busy = true;
    updateComposer();
    try {
      const response = await api(`/api/sessions/${state.sessionId}/ask`, {
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

  el("new-chat").addEventListener("click", createSession);
  el("rename-chat").addEventListener("click", () => {
    const session = state.sessions.find((item) => item.id === state.sessionId);
    if (session) renameSession(session);
  });
  el("mobile-menu").addEventListener("click", () => el("sidebar").classList.toggle("open"));
  el("attach-button").addEventListener("click", () => fileInput.click());
  el("welcome-upload").addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => uploadFiles([...fileInput.files]));
  question.addEventListener("input", () => {
    question.style.height = "auto";
    question.style.height = `${Math.min(question.scrollHeight, 150)}px`;
    updateComposer();
  });
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
      await json("/api/me");
      await refreshSessions();
      if (state.sessions.length) await selectSession(state.sessions[0].id);
      else await createSession();
    } catch (error) {
      toast(error.message);
    }
    updateComposer();
  })();
})();
