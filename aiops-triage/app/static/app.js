// AIOps Triage front end. Plain JS, no build step, so the Docker image stays simple.
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const POLL_MS = 4000;
  const BASE = window.BASE || "";

  let incidents = [];
  let selectedId = null;   // null = follow the newest incident
  let newestSeen = 0;
  let filter = "all";

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const sevLabel = { critical: "Critical", warning: "Warning", info: "Info" };
  const srcLabel = { manual: "Manual", alertmanager: "Alertmanager", cloudwatch: "CloudWatch" };

  function ago(iso) {
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 45) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    return new Date(iso).toLocaleDateString();
  }

  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.add("is-on");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => t.classList.remove("is-on"), 2600);
  }

  async function api(path, opts = {}) {
    const res = await fetch(BASE + path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401 && path !== "/ready") { location.href = BASE + "/login"; }
    if (!res.ok) throw new Error(data.error || `Request failed with status ${res.status}`);
    return data;
  }

  // ------------------------------------------------------------ diagnosis
  function renderDiagnosis(inc, fresh = false) {
    const box = $("#diagnosis");
    if (!inc) {
      box.dataset.severity = "none";
      box.innerHTML = `<div class="diag-empty"><h2>No incidents yet</h2>
        <p>Pick a sample on the left and press Diagnose, or point Alertmanager at
        <code>/webhook/alertmanager</code>. The diagnosis appears here.</p></div>`;
      return;
    }
    const a = inc.analysis || {};
    const m = inc.meta || {};
    const engine = m.mode === "claude" ? "Claude" : "Rules";
    const tokens = (m.input_tokens || 0) + (m.output_tokens || 0);

    box.dataset.severity = inc.severity;
    box.innerHTML = `
      <div class="diag-top">
        <span class="sev-badge">${sevLabel[inc.severity] || inc.severity}</span>
        <span class="tag">${esc(srcLabel[inc.source] || inc.source)} incident #${inc.id}, ${ago(inc.created_at)}</span>
        <span class="tag">Diagnosed by <strong>${engine}</strong> in ${m.latency_ms ?? 0} ms${tokens ? `, ${tokens} tokens` : ""}</span>
        <span class="tag">Confidence <strong>${esc(a.confidence || "n/a")}</strong></span>
      </div>
      <div class="diag-title">${esc(inc.title)}</div>
      <p class="diag-cause">${esc(a.root_cause)}</p>
      ${a.impact ? `<p class="diag-impact"><strong>Impact:</strong> ${esc(a.impact)}</p>` : ""}
      ${m.error ? `<p class="diag-error">${esc(m.error)}</p>` : ""}
      <div class="diag-cols">
        <div>
          <h3>What to do</h3>
          <ol class="steps">${(a.suggested_actions || []).map((s) => `<li>${esc(s)}</li>`).join("")}</ol>
        </div>
        <div>
          <h3>Commands to run</h3>
          <ul class="cmds">${(a.commands || []).map((c) =>
            `<li class="cmd"><code>${esc(c)}</code><button class="copy" data-copy="${esc(c)}">Copy</button></li>`).join("")}</ul>
        </div>
      </div>
      <details class="raw"><summary>Original alert text</summary><pre>${esc(inc.raw)}</pre></details>`;

    if (fresh) {
      box.classList.remove("is-new");
      void box.offsetWidth;
      box.classList.add("is-new");
    }
  }

  // ------------------------------------------------------------ incident log
  function renderList() {
    const list = $("#incidents");
    const shown = incidents.filter((i) => filter === "all" || i.severity === filter);
    list.innerHTML = shown.map((i) => `
      <li class="incident ${i.id === currentId() ? "is-selected" : ""}" data-id="${i.id}" data-severity="${i.severity}" tabindex="0">
        <span class="bar"></span>
        <span class="inc-sev">${sevLabel[i.severity] || i.severity}</span>
        <div class="inc-body">
          <div class="inc-title">${esc(i.title)}</div>
          <div class="inc-cause">${esc(i.analysis?.root_cause)}</div>
        </div>
        <div class="inc-meta"><span class="src">${esc(srcLabel[i.source] || i.source)}</span><br>${ago(i.created_at)}</div>
      </li>`).join("");
    $("#list-empty").hidden = shown.length > 0;
    $("#list-empty").textContent = incidents.length && !shown.length
      ? `No ${filter} incidents.`
      : "Incidents from every source are listed here, newest first.";
  }

  const currentId = () => selectedId ?? incidents[0]?.id ?? null;

  function select(id) {
    selectedId = id === incidents[0]?.id ? null : id;
    renderDiagnosis(incidents.find((i) => i.id === id));
    renderList();
  }

  // ------------------------------------------------------------ polling
  async function refresh() {
    try {
      const [list, stats] = await Promise.all([api("/api/incidents"), api("/api/stats")]);
      incidents = list;
      const newest = list[0]?.id || 0;
      const isNew = newest > newestSeen && newestSeen !== 0;
      if (newest !== newestSeen) {
        if (selectedId === null) renderDiagnosis(list[0], isNew);
        newestSeen = newest;
      } else if (!list.length) {
        renderDiagnosis(null);
      }
      if (selectedId !== null && !list.some((i) => i.id === selectedId)) {
        selectedId = null;
        renderDiagnosis(list[0]);
      }
      renderList();
      renderStats(stats);
      setHealth("ok");
    } catch (e) {
      setHealth("down");
    }
  }

  function renderStats(s) {
    $("#s-total").textContent = s.total;
    $("#s-critical").textContent = s.critical;
    $("#s-warning").textContent = s.warning;
    $("#s-latency").textContent = `${s.avg_latency_ms} ms`;
    $("#s-tokens").textContent = s.tokens.toLocaleString();
    $("#s-cost").textContent = `$${s.cost_usd < 0.01 && s.cost_usd > 0 ? s.cost_usd.toFixed(4) : s.cost_usd.toFixed(2)}`;
    $("#instance").textContent = s.instance;
    renderChaos(s.chaos);
  }

  async function setHealth(state) {
    const el = $("#health");
    if (state === "ok") {
      try {
        await api("/ready");
      } catch {
        state = "degraded";
      }
    }
    el.dataset.state = state;
    $("#health-text").textContent = { ok: "Healthy", degraded: "Not ready", down: "Unreachable" }[state];
  }

  function renderChaos(c) {
    if (!c) return;
    const st = $("#chaos-state");
    if (!st) return;
    if (c.unreachable) {
      st.textContent = `${c.target} unreachable`;
      st.classList.add("is-hot");
      return;
    }
    const hot = [];
    if (c.error_rate > 0) hot.push(`${c.error_rate}% requests failing`);
    if (c.latency_ms > 0) hot.push(`+${(c.latency_ms / 1000).toFixed(1)} s latency`);
    if (c.cpu_burn_seconds_left > 0) hot.push(`CPU burn ${c.cpu_burn_seconds_left}s left`);
    if (c.ready === false) hot.push("readiness failing");
    let text = hot.length ? hot.join(", ") : "All calm";
    if (c.pods > 1) text += ` (${c.pods_ok === c.pods ? c.pods : `${c.pods_ok} of ${c.pods}`} pods)`;
    st.textContent = text;
    st.classList.toggle("is-hot", hot.length > 0 || (c.pods && c.pods_ok < c.pods));
    $('[data-chaos="errors"]')?.classList.toggle("is-active", c.error_rate > 0);
    $('[data-chaos="latency"]')?.classList.toggle("is-active", c.latency_ms > 0);
    $('[data-chaos="cpu"]')?.classList.toggle("is-active", c.cpu_burn_seconds_left > 0);
    $('[data-chaos="notready"]')?.classList.toggle("is-active", c.ready === false);
  }

  // ------------------------------------------------------------ actions
  $("#report-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $("#alert-text").value.trim();
    const btn = $("#analyze-btn");
    const err = $("#form-error");
    err.textContent = "";
    if (!text) { err.textContent = "Paste an alert or log line to diagnose."; return; }
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span><span class="btn-label">Diagnosing</span>`;
    try {
      const inc = await api("/api/analyze", { method: "POST", body: { text } });
      selectedId = null;
      incidents = [inc, ...incidents.filter((i) => i.id !== inc.id)];
      newestSeen = inc.id;
      renderDiagnosis(inc, true);
      renderList();
      refresh();
    } catch (ex) {
      err.textContent = ex.message;
    } finally {
      btn.disabled = false;
      btn.innerHTML = `<span class="btn-label">Diagnose</span>`;
    }
  });

  $$("[data-sample]").forEach((b) => b.addEventListener("click", () => {
    $("#alert-text").value = window.SAMPLES[+b.dataset.sample].text;
    $("#alert-text").focus();
  }));

  $("#incidents").addEventListener("click", (e) => {
    const li = e.target.closest(".incident");
    if (li) select(+li.dataset.id);
  });
  $("#incidents").addEventListener("keydown", (e) => {
    const li = e.target.closest(".incident");
    if (li && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); select(+li.dataset.id); }
  });

  $$(".filter").forEach((b) => b.addEventListener("click", () => {
    filter = b.dataset.filter;
    $$(".filter").forEach((x) => x.classList.toggle("is-on", x === b));
    renderList();
  }));

  $("#clear-btn").addEventListener("click", async () => {
    if (!incidents.length) return;
    await api("/api/incidents", { method: "DELETE" });
    incidents = []; selectedId = null; newestSeen = 0;
    renderDiagnosis(null); renderList();
    toast("Incident log cleared on this pod");
  });

  document.addEventListener("click", async (e) => {
    const c = e.target.closest(".copy");
    if (!c) return;
    try {
      await navigator.clipboard.writeText(c.dataset.copy);
      c.textContent = "Copied";
      setTimeout(() => (c.textContent = "Copy"), 1400);
    } catch {
      toast("Copy is blocked here; select the command instead");
    }
  });

  // ------------------------------------------------------------ chaos
  const post = (action, body) => api(`/api/chaos/${action}`, { method: "POST", body });
  const chaosActions = {
    errors: () => post("errors", { percent: 30 }).then((r) => toast(`30% of requests to ${r.target} now fail`)),
    latency: () => post("latency", { ms: 1500 }).then((r) => toast(`Requests to ${r.target} now take 1.5 s longer`)),
    cpu: () => post("cpu", { seconds: 120 }).then((r) => toast(`Burning CPU on ${r.target} for 2 minutes`)),
    notready: () => post("ready", { ready: false }).then((r) => toast(`${r.target} readiness probe now returns 503`)),
    reset: () => post("reset").then(() => { toast("All chaos stopped"); $("#traffic-result").textContent = ""; }),
    traffic: async () => {
      const out = $("#traffic-result");
      out.textContent = "Sending 50 requests…";
      const r = await post("traffic");
      out.textContent = `Sent ${r.sent} requests: ${r.ok} succeeded, ${r.failed} failed, ${r.avg_ms} ms average.`;
    },
  };
  $$("button[data-chaos]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    try { await chaosActions[b.dataset.chaos](); await refresh(); }
    catch (ex) { toast(ex.message); }
    finally { b.disabled = false; }
  }));

  refresh();
  setInterval(refresh, POLL_MS);
})();
