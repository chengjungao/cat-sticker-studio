/* 表情包工坊 —— 前端逻辑
   零依赖，不用任何框架。三个要点：
   1. 出图是长任务，所以**只轮询进度**，不做长连接（几秒一次，轮询更稳）
   2. 阶段 → 百分比有一张明确的映射表，进度条不会乱跳
   3. 界面上的照片、词表、选项都跟后端状态同步，刷新页面不丢 */

const $ = (id) => document.getElementById(id);

let STATE = null;          // /api/state 的快照
let TIMER = null;          // 轮询定时器
let RUNNING = false;

/* 阶段 → 进度条区间。四步的耗时比例来自实测：
   定妆照约 1 张的量，16 帧占大头，后处理只有几秒。 */
const STAGE_RANGE = {
  hero: [0, 12],
  frames: [12, 78],
  post: [78, 88],
  assets: [88, 92],
  banner: [92, 98],
  all: [100, 100],
};

const STAGE_LABEL = {
  hero: "出定妆照",
  frames: "出表情图",
  post: "抠图与合成",
  assets: "封面与图标",
  banner: "详情页横幅",
  all: "收尾",
};

/* ───────────────── 小工具 ───────────────── */

function toast(msg, kind = "") {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast " + kind;
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.add("hidden"), 3200);
}

async function api(path, body) {
  const opt = body
    ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : {};
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({ error: "返回的不是 JSON" }));
  if (j && j.ok === false && j.error) toast(j.error, "err");
  return j;
}

function setBadge(text, kind) {
  const b = $("health-badge");
  b.textContent = text;
  b.className = "badge " + (kind || "badge-idle");
}

/* ───────────────── 主角 ───────────────── */

/* 「照片里的主角是什么」。它决定两件事：
   一、提示词按哪一类写（动物 / 人物 / 物件，判断在后端 prompts.kind_of）；
   二、第 2 步那句举例、以及人称用「它」还是「他」。
   每改一次都存一次，所以刷新页面不丢，也不会出现「提示语按狗、出图按猫」。 */

let SUBJ_TIMER = null;

function paintSubject(kind, hint, active) {
  $("desc-who").textContent = kind === "人物" ? "他" : "它";
  $("desc").placeholder = hint || "";
  [...$("subj-chips").querySelectorAll("button")].forEach((b) =>
    b.classList.toggle("on", b.dataset.subject === active));
}

function renderChips(list) {
  const box = $("subj-chips");
  box.innerHTML = "";
  list.forEach((s) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip";
    b.dataset.subject = s;
    b.textContent = s;
    b.addEventListener("click", () => saveSubject(s, true));
    box.appendChild(b);
  });
}

async function saveSubject(subject, setInput) {
  const r = await api("/api/subject", { subject });
  if (!r || r.ok === false) return;
  if (setInput) $("subject").value = r.subject;
  paintSubject(r.kind, r.desc_hint, r.subject);
}

/* 点「开始」之前先把输入框里那半句落库，避免防抖还没触发就开跑 */
async function flushSubject() {
  clearTimeout(SUBJ_TIMER);
  return saveSubject($("subject").value, false);
}

$("subject").addEventListener("input", () => {
  clearTimeout(SUBJ_TIMER);
  SUBJ_TIMER = setTimeout(() => saveSubject($("subject").value, false), 400);
});
$("subject").addEventListener("blur", () => flushSubject());

/* ───────────────── 初始化 ───────────────── */

async function boot() {
  STATE = await api("/api/state");
  if (!STATE || !STATE.ok) return toast("读工程状态失败", "err");

  $("subject").value = STATE.subject || "";
  renderChips(STATE.subject_chips || []);
  paintSubject(STATE.kind, STATE.desc_hint, STATE.subject);
  $("desc").value = STATE.desc || "";
  $("from-photo").checked = STATE.from_photo !== false;
  $("seed").value = STATE.seed;

  renderMoods(STATE.moods && STATE.moods.length ? STATE.moods : STATE.mood_defaults);
  renderPhoto();
  if (STATE.has_hero) {
    $("hero-box").classList.remove("hidden");
    $("hero-img").src = "/img/原图/hero.png?_=" + Date.now();
  }
  renderResults();
  $("btn-continue").classList.add("hidden");
  poll();
}

function renderMoods(texts) {
  const box = $("moods");
  box.innerHTML = "";
  for (let i = 0; i < 8; i++) {
    const inp = document.createElement("input");
    inp.type = "text";
    inp.maxLength = 8;
    inp.value = texts[i] || "";
    inp.placeholder = "第 " + (i + 1) + " 个";
    box.appendChild(inp);
  }
}

function moodTexts() {
  return [...$("moods").querySelectorAll("input")].map((i) => i.value.trim() || i.placeholder);
}

function renderPhoto() {
  const has = STATE && STATE.photos && STATE.photos.length > 0;
  $("drop-empty").classList.toggle("hidden", has);
  $("drop-preview").classList.toggle("hidden", !has);
  if (has) $("photo-img").src = "/img/照片/" + encodeURIComponent(STATE.photos[0]) + "?_=" + Date.now();
}

/* ───────────────── 照片 ───────────────── */

function readFile(file, cb) {
  if (!file) return;
  if (file.size > 12 * 1024 * 1024) return toast("这张图太大了，先压到 12MB 以内", "err");
  const fr = new FileReader();
  fr.onload = () => cb(fr.result, file.name);
  fr.readAsDataURL(file);
}

$("drop").addEventListener("click", (e) => {
  if (e.target.closest("button")) return;
  $("file").click();
});
$("file").addEventListener("change", (e) => {
  readFile(e.target.files[0], uploadPhoto);
  e.target.value = "";
});
["dragenter", "dragover"].forEach((ev) =>
  $("drop").addEventListener(ev, (e) => { e.preventDefault(); $("drop").classList.add("hot"); })
);
["dragleave", "drop"].forEach((ev) =>
  $("drop").addEventListener(ev, (e) => { e.preventDefault(); $("drop").classList.remove("hot"); })
);
$("drop").addEventListener("drop", (e) => {
  const f = e.dataTransfer.files[0];
  readFile(f, uploadPhoto);
});
$("btn-replace").addEventListener("click", (e) => { e.stopPropagation(); $("file").click(); });
$("btn-remove").addEventListener("click", async (e) => {
  e.stopPropagation();
  await api("/api/photo/delete", {});
  STATE = await api("/api/state");
  renderPhoto();
  toast("照片已删掉");
});

async function uploadPhoto(dataUrl, name) {
  toast("照片在上传…");
  const r = await api("/api/photo", { data: dataUrl, name });
  if (r.ok) {
    STATE = await api("/api/state");
    renderPhoto();
    toast("照片已就位", "ok");
  }
}

/* ───────────────── 生成 ───────────────── */

$("btn-run").addEventListener("click", () => run({
  desc: $("desc").value,
  subject: $("subject").value,
  from_photo: $("from-photo").checked,
  seed: Number($("seed").value) || undefined,
  pause_after_hero: $("pause-hero").checked,
  texts: moodTexts(),
}));

$("btn-continue").addEventListener("click", async () => {
  await api("/api/moods", { texts: moodTexts() });
  await run({
    desc: $("desc").value,
    subject: $("subject").value,
    from_photo: $("from-photo").checked,
    seed: Number($("seed").value) || undefined,
    skip_hero: true,
  });
});

async function run(payload) {
  if ($("from-photo").checked && !(STATE.photos || []).length) {
    return toast("先传一张照片，或者关掉「用照片当参考」", "err");
  }
  await flushSubject();
  await api("/api/moods", { texts: payload.texts || moodTexts() });
  const r = await api("/api/run", payload);
  if (r.ok) {
    RUNNING = true;
    $("btn-run").disabled = true;
    $("btn-cancel").classList.remove("hidden");
    $("btn-continue").classList.add("hidden");
    $("log").innerHTML = "";
    toast("开始生成，第一次要等模型加载，之后每张十几秒");
    poll();
  }
}

$("btn-cancel").addEventListener("click", async () => {
  await api("/api/cancel", {});
  toast("已请求取消");
});

$("btn-hero").addEventListener("click", async () => {
  await flushSubject();
  await api("/api/moods", { texts: moodTexts() });
  const r = await api("/api/hero", {
    desc: $("desc").value, subject: $("subject").value,
    from_photo: $("from-photo").checked,
    seed: Number($("seed").value) || undefined,
  });
  if (r.ok) { RUNNING = true; $("btn-cancel").classList.remove("hidden"); }
});
$("btn-frames").addEventListener("click", async () => {
  await flushSubject();
  await api("/api/moods", { texts: moodTexts() });
  const r = await api("/api/frames", {
    desc: $("desc").value, subject: $("subject").value,
    from_photo: $("from-photo").checked,
    seed: Number($("seed").value) || undefined,
  });
  if (r.ok) { RUNNING = true; $("btn-cancel").classList.remove("hidden"); }
});
$("btn-post").addEventListener("click", async () => {
  await flushSubject();
  const r = await api("/api/post", {});
  if (r.ok) { RUNNING = true; $("btn-cancel").classList.remove("hidden"); }
});
$("btn-repack").addEventListener("click", async () => {
  await flushSubject();
  const r = await api("/api/pack", {});
  if (r.ok) toast("已重新打包", "ok");
});

/* ───────────────── 轮询 ───────────────── */

async function poll() {
  const p = await api("/api/progress");
  if (!p) return;
  renderProgress(p);
  renderLog(p.log);
  if (p.hero && p.hero.path) {
    $("hero-box").classList.remove("hidden");
    $("hero-img").src = "/img/原图/hero.png?_=" + Date.now();
  }
  if (p.paused && p.hero) $("btn-continue").classList.remove("hidden");

  if (p.running) {
    RUNNING = true;
    $("btn-run").disabled = true;
    $("btn-cancel").classList.remove("hidden");
    clearTimeout(TIMER);
    TIMER = setTimeout(poll, 1200);
  } else if (RUNNING) {
    RUNNING = false;
    $("btn-run").disabled = false;
    $("btn-cancel").classList.add("hidden");
    STATE = await api("/api/state");
    renderPhoto();
    renderResults();
    if (p.msg && /完成|成功/.test(p.msg)) toast(p.msg, "ok");
  }
}

function renderProgress(p) {
  const stage = p.stage || "";
  $("stage-name").textContent = STAGE_LABEL[stage] || (stage ? stage : "待命");
  $("stage-time").textContent = p.elapsed ? p.elapsed.toFixed(1) + " 秒" : "";
  $("stage-msg").textContent = p.msg || "准备中…";

  const q = p.queue || {};
  const bits = [];
  if (p.total > 1) bits.push("完成 " + (p.done || 0) + "/" + p.total);
  if (q.running || q.pending) bits.push("队列 " + (q.running || 0) + "跑/" + (q.pending || 0) + "等");
  $("queue-info").textContent = bits.join(" · ");

  let pct = 0;
  const rg = STAGE_RANGE[stage];
  if (rg) {
    pct = rg[0];
    if (stage === "frames" && p.total > 0) {
      const inner = Math.min(1, (p.done || 0) / p.total);
      pct = rg[0] + (rg[1] - rg[0]) * inner;
    } else if (rg[1] > rg[0]) {
      pct = rg[0] + (rg[1] - rg[0]) * 0.25;
    }
  } else if (!p.running && p.result) {
    pct = 100;
  }
  $("bar").style.width = Math.max(0, Math.min(100, pct)) + "%";
}

function renderLog(lines) {
  const box = $("log");
  if (!lines || !lines.length) return;
  const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
  box.innerHTML = lines.map((l) => "<div>" + esc(l) + "</div>").join("");
  if (atBottom) box.scrollTop = box.scrollHeight;
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

/* ───────────────── 结果 ───────────────── */

function renderResults() {
  if (!STATE) return;
  const gifs = STATE.gifs || [];
  const dark = $("dark-bg").checked;

  if (gifs.length) {
    $("result").classList.remove("hidden");
    const box = $("gifs");
    box.innerHTML = "";
    gifs.forEach((f, i) => {
      const text = f.replace(/^\d+_/, "").replace(/_\d+\.gif$/, "");
      const c = document.createElement("div");
      c.className = "gif-card" + (dark ? " dark" : "");
      c.innerHTML =
        '<img src="/img/01_表情图/' + encodeURIComponent(f) + '?_=' + Date.now() + '" alt="' + esc(text) + '">' +
        '<div class="gif-name">' + esc(text) + "</div>" +
        '<div class="gif-meta">' + f + "</div>" +
        '<div class="gif-actions"><button class="ghost small" data-remake="' + (i + 1) + '">这一张重出</button></div>';
      box.appendChild(c);
    });
    box.querySelectorAll("[data-remake]").forEach((b) =>
      b.addEventListener("click", async () => {
        const r = await api("/api/remake", { index: Number(b.dataset.remake), texts: moodTexts() });
        if (r.ok) { RUNNING = true; $("btn-cancel").classList.remove("hidden"); toast("正在重出这一张…"); poll(); }
      })
    );
  }

  // 另外三件
  const a = $("assets");
  a.innerHTML = "";
  const cards = [
    { name: "封面_240.png", dir: "02_封面", spec: "240×240 · PNG · 透明底 · ≤500KB" },
    { name: "图标_50.png", dir: "03_聊天面板图标", spec: "50×50 · PNG · 透明底 · ≤100KB" },
  ];
  cards.forEach((c) => {
    const d = document.createElement("div");
    d.className = "asset-card";
    d.innerHTML =
      '<img src="/img/' + c.dir + "/" + c.name + "?_=" + Date.now() + '" alt="' + c.name + '">' +
      '<div class="asset-name">' + c.name + "</div>" +
      '<div class="asset-spec">' + c.spec + "</div>";
    a.appendChild(d);
  });
  // 横幅
  const bd = document.createElement("div");
  bd.className = "asset-card";
  bd.innerHTML =
    '<img src="/img/04_详情页横幅/横幅_750x400.png?_=' + Date.now() + '" alt="横幅">' +
    '<div class="asset-name">横幅_750x400.png</div>' +
    '<div class="asset-spec">750×400 · ≤500KB · 无文字 / 非白底 / 不透明</div>';
  a.appendChild(bd);
}

$("dark-bg").addEventListener("change", renderResults);

$("btn-zip").addEventListener("click", () => {
  if (!STATE.zips || !STATE.zips.length) return toast("还没打包，先点「重新打包」", "err");
  window.location.href = "/download?name=" + encodeURIComponent(STATE.zips[STATE.zips.length - 1]);
});

$("btn-manifest").addEventListener("click", async () => {
  const box = $("manifest");
  if (!box.classList.contains("hidden")) return box.classList.add("hidden");
  const st = await api("/api/state");
  if (!st || !st.has_manifest) return toast("还没有提交清单，跑完一次生成就有了", "err");
  const res = await fetch("/download?name=" + encodeURIComponent("提交清单.md"));
  box.textContent = await res.text();
  box.classList.remove("hidden");
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
});

/* ───────────────── 环境自检 / 设置 ───────────────── */

$("btn-health").addEventListener("click", async () => {
  setBadge("检查中…", "badge");
  const h = await api("/api/health");
  if (!h || !h.ok) {
    setBadge("连不上 ComfyUI", "badge-err");
    return toast((h && h.errors && h.errors[0]) || "连不上 ComfyUI", "err");
  }
  const i = h.info || {};
  setBadge("就绪 · " + (i.device || "GPU") + " " + (i.vram_free_gb || "?") + "GB 空闲", "badge-ok");
  toast("ComfyUI " + i.comfyui_version + " 已连通", "ok");
});

$("btn-open").addEventListener("click", async () => {
  const r = await api("/api/open", { what: "folder" });
  if (r.ok) toast("已打开工程文件夹");
});

$("btn-settings").addEventListener("click", () => {
  const c = STATE.config;
  $("cfg-url").value = c.comfy_url || "";
  $("cfg-unet").value = (c.models || {}).unet || "";
  $("cfg-clip").value = (c.models || {}).clip || "";
  $("cfg-vae").value = (c.models || {}).vae || "";
  $("cfg-steps").value = (c.generate || {}).steps || 25;
  $("cfg-cfg").value = (c.generate || {}).cfg || 1;
  $("cfg-frame-ms").value = (c.sticker || {}).frame_ms || 400;
  $("cfg-size").value = (c.sticker || {}).size || 240;
  $("model-list").textContent = "";
  $("modal").classList.remove("hidden");
});
$("btn-close-modal").addEventListener("click", () => $("modal").classList.add("hidden"));
$("modal").addEventListener("click", (e) => {
  if (e.target === $("modal")) $("modal").classList.add("hidden");
});

$("btn-check-models").addEventListener("click", async () => {
  $("model-list").textContent = "读取中…";
  const m = await api("/api/models");
  if (!m || !m.ok) return ($("model-list").textContent = (m && m.error) || "读不到");
  $("model-list").textContent = [
    m.has_qwen_nodes ? "✓ 有 Qwen-Image 节点" : "✗ 没有 TextEncodeQwenImage21 节点",
    "", "扩散模型：", ...(m.unet || ["（空）"]),
    "", "文本编码器：", ...(m.clip || ["（空）"]),
    "", "VAE：", ...(m.vae || ["（空）"]),
  ].join("\n");
});

$("btn-save-cfg").addEventListener("click", async () => {
  const c = JSON.parse(JSON.stringify(STATE.config));
  c.comfy_url = $("cfg-url").value.trim();
  c.models = c.models || {};
  c.models.unet = $("cfg-unet").value.trim();
  c.models.clip = $("cfg-clip").value.trim();
  c.models.vae = $("cfg-vae").value.trim();
  c.generate = c.generate || {};
  c.generate.steps = Number($("cfg-steps").value) || 25;
  c.generate.cfg = Number($("cfg-cfg").value) || 1;
  c.sticker = c.sticker || {};
  c.sticker.frame_ms = Number($("cfg-frame-ms").value) || 400;
  c.sticker.size = Number($("cfg-size").value) || 240;
  const r = await api("/api/config", { config: c });
  if (r.ok) {
    STATE.config = r.config;
    $("modal").classList.add("hidden");
    toast("设置已保存", "ok");
  }
});

boot();
