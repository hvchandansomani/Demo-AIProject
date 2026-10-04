// Doubt Wall front end. Plain JS, polls every 3 s so everyone sees new doubts.
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const POLL_MS = 3000;
  const store = {
    get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
  };

  // An anonymous id per browser, so rate limits are per learner, not per Wi-Fi.
  let clientId = store.get("dw.client", null);
  if (!clientId) {
    clientId = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2) + Date.now());
    store.set("dw.client", clientId);
  }
  const voted = new Set(store.get("dw.voted", []));
  let adminToken = sessionStorage.getItem("dw.admin") || "";
  let sort = "top";
  let lastJson = "";
  const seen = new Set();
  let firstLoad = true;

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function ago(iso) {
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 45) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    return new Date(iso).toLocaleDateString();
  }

  function toast(msg, isError = false) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.toggle("is-error", isError);
    t.classList.add("is-on");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => t.classList.remove("is-on"), 3000);
  }

  async function api(path, opts = {}) {
    const headers = { "Content-Type": "application/json", "X-Client-Id": clientId };
    if (adminToken && path.startsWith("/api/admin")) headers["X-Admin-Token"] = adminToken;
    const res = await fetch(path, { cache: "no-store", ...opts, headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(data.error || `Request failed (${res.status}).`);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  // ------------------------------------------------------------ render
  const upIcon = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7"/></svg>`;
  const tick = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>`;

  function render(doubts) {
    const list = $("#doubts");
    $("#empty").hidden = doubts.length > 0;
    list.innerHTML = doubts.map((d) => {
      const isVoted = voted.has(d.id);
      const fresh = !firstLoad && !seen.has(d.id);
      return `
      <li class="doubt ${d.answered ? "is-answered" : ""} ${fresh ? "is-new" : ""}" data-id="${d.id}">
        <button class="vote ${isVoted ? "is-voted" : ""}" data-vote="${d.id}" ${isVoted ? "disabled" : ""}
          aria-label="${isVoted ? "You upvoted this" : "Upvote this doubt"}, ${d.upvotes} upvotes">
          ${upIcon}<span class="n">${d.upvotes}</span>
        </button>
        <div>
          <p class="q">${esc(d.question)}</p>
          <div class="meta">
            <span class="who">${esc(d.nickname)}</span>
            <span>${ago(d.created_at)}</span>
            ${d.answered ? `<span class="badge-done">${tick}Answered</span>` : ""}
            ${adminToken ? `<span class="mod">
              <button data-answer="${d.id}" data-state="${d.answered ? 0 : 1}">${d.answered ? "Reopen" : "Mark answered"}</button>
              <button class="danger" data-delete="${d.id}">Remove</button></span>` : ""}
          </div>
        </div>
      </li>`;
    }).join("");
    doubts.forEach((d) => seen.add(d.id));
    firstLoad = false;
  }

  function renderStats(s) {
    const parts = [`${s.total} ${s.total === 1 ? "doubt" : "doubts"}`];
    if (s.total) parts.push(`from ${s.people} ${s.people === 1 ? "person" : "people"}`);
    let text = parts.join(" ");
    if (s.total) text += `, ${s.answered} answered`;
    $("#wall-stats").textContent = text;
    $("#f-version").textContent = s.version;
    $("#f-instance").textContent = s.instance;
  }

  // ------------------------------------------------------------ polling
  async function refresh(force = false) {
    if (!force && document.hidden) return;   // background tabs stop polling
    try {
      const [doubts, stats] = await Promise.all([api(`/api/doubts?sort=${sort}`), api("/api/stats")]);
      $("#conn").hidden = true;
      const json = JSON.stringify(doubts) + adminToken;
      if (force || json !== lastJson) { render(doubts); lastJson = json; }
      else { $$(".doubt").forEach((li) => li.classList.remove("is-new")); }
      renderStats(stats);
    } catch (e) {
      const c = $("#conn");
      c.textContent = e.status === 503
        ? "The database is not answering right now. The wall will update again once it's back."
        : e.status >= 500
          ? "The server returned an error. Retrying automatically."
          : e.status === 429 ? e.message : "Can't reach the server. Retrying automatically.";
      c.hidden = false;
    }
  }

  // ------------------------------------------------------------ ask
  const nick = $("#nickname");
  const q = $("#question");
  nick.value = store.get("dw.nick", "");
  const updateCount = () => { $("#char-count").textContent = `${q.value.length} / ${q.maxLength}`; };
  q.addEventListener("input", updateCount);
  updateCount();

  $("#ask-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("#ask-error");
    err.textContent = "";
    const nickname = nick.value.trim();
    const question = q.value.trim();
    if (!nickname) { err.textContent = "Add your name first."; nick.focus(); return; }
    if (question.length < 5) { err.textContent = "Write a little more (at least 5 characters)."; q.focus(); return; }
    const btn = $("#ask-btn");
    btn.disabled = true; btn.textContent = "Posting…";
    try {
      const d = await api("/api/doubts", { method: "POST", body: { nickname, question } });
      store.set("dw.nick", nickname);
      q.value = ""; updateCount();
      seen.add(d.id);
      toast("Doubt posted. It's on the wall for everyone.");
      await refresh(true);
      document.querySelector(`.doubt[data-id="${d.id}"]`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (ex) {
      err.textContent = ex.message;
    } finally {
      btn.disabled = false; btn.textContent = "Post doubt";
    }
  });

  // ------------------------------------------------------------ wall actions
  $("#doubts").addEventListener("click", async (e) => {
    const vote = e.target.closest("[data-vote]");
    const ans = e.target.closest("[data-answer]");
    const del = e.target.closest("[data-delete]");
    try {
      if (vote && !vote.disabled) {
        const id = +vote.dataset.vote;
        vote.disabled = true;
        const r = await api(`/api/doubts/${id}/upvote`, { method: "POST" });
        voted.add(id); store.set("dw.voted", [...voted]);
        vote.classList.add("is-voted");
        vote.querySelector(".n").textContent = r.upvotes;
        refresh(true);
      } else if (ans) {
        await api(`/api/admin/doubts/${ans.dataset.answer}/answered`, { method: "POST", body: { answered: ans.dataset.state === "1" } });
        refresh(true);
      } else if (del) {
        if (!confirm("Remove this doubt for everyone?")) return;
        await api(`/api/admin/doubts/${del.dataset.delete}`, { method: "DELETE" });
        toast("Doubt removed.");
        refresh(true);
      }
    } catch (ex) {
      if (vote) vote.disabled = false;
      if (ex.status === 401) { setAdmin(""); }
      toast(ex.message, true);
    }
  });

  $$(".tab").forEach((t) => t.addEventListener("click", () => {
    sort = t.dataset.sort;
    $$(".tab").forEach((x) => { x.classList.toggle("is-on", x === t); x.setAttribute("aria-selected", x === t); });
    refresh(true);
  }));

  // ------------------------------------------------------------ admin
  function setAdmin(token) {
    adminToken = token;
    if (token) sessionStorage.setItem("dw.admin", token); else sessionStorage.removeItem("dw.admin");
    $("#admin-bar").hidden = !token && !$("#admin-bar").dataset.open;
    $("#admin-form").hidden = !!token;
    $("#admin-on").hidden = !token;
    refresh(true);
  }
  function openAdmin() {
    $("#admin-bar").dataset.open = "1";
    $("#admin-bar").hidden = false;
    $("#admin-form").hidden = !!adminToken;
    $("#admin-on").hidden = !adminToken;
    if (!adminToken) $("#admin-token").focus();
  }
  $("#admin-open").addEventListener("click", openAdmin);
  $("#admin-cancel").addEventListener("click", () => { delete $("#admin-bar").dataset.open; $("#admin-bar").hidden = true; });
  $("#admin-off").addEventListener("click", () => { delete $("#admin-bar").dataset.open; setAdmin(""); toast("Moderation turned off."); });
  $("#admin-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const token = $("#admin-token").value.trim();
    if (!token) return;
    adminToken = token;
    try {
      await api("/api/admin/check", { method: "POST" });
      $("#admin-token").value = "";
      setAdmin(token);
      toast("Moderation is on.");
    } catch (ex) {
      adminToken = "";
      toast(ex.message, true);
    }
  });
  if (adminToken || new URLSearchParams(location.search).has("admin")) openAdmin();

  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(true); });
  refresh(true);
  setInterval(refresh, POLL_MS);
})();
