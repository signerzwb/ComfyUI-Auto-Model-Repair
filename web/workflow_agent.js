import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { ComfyDialog } from "../../scripts/ui.js";

const EXTENSION_NAME = "ShenDuMao.WorkflowDoctor";
const API = "/workflow-doctor/v1";
const FOLDERS = ["checkpoints", "loras", "vae", "text_encoders", "diffusion_models", "clip_vision", "embeddings", "controlnet", "upscale_models"];
const AGENT_SESSION_KEY = "shendumao.workflow-doctor.agent.session";
const AGENT_REMEMBER_KEY = "shendumao.workflow-doctor.agent.remember";

function el(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function button(text, handler, variant = "neutral") {
  const tone = variant === true ? "primary" : variant;
  const node = el("button", `sdm-button${tone !== "neutral" ? ` ${tone}` : ""}`, text);
  node.type = "button";
  node.addEventListener("click", async () => {
    node.disabled = true;
    try { await handler(node); } catch (error) { window.alert(error.message || String(error)); } finally { node.disabled = false; }
  });
  return node;
}

async function request(path, options = {}) {
  const response = await api.fetchApi(`${API}${path}`, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) throw new Error(data.error || `请求失败（${response.status}）`);
  return data.data ?? data;
}

async function post(path, body) {
  return request(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

function graph() { return app.canvas?.graph || app.graph; }
function workflow() {
  const current = graph();
  if (!current?.serialize) throw new Error("无法读取当前画布，请先打开一个工作流。");
  return JSON.parse(JSON.stringify(current.serialize()));
}
function canvasImage() {
  try { return app.canvas?.canvas?.toDataURL?.("image/jpeg", 0.72) || null; } catch (_) { return null; }
}
function bytes(value) {
  let size = Number(value || 0), unit = 0;
  const units = ["B", "KB", "MB", "GB", "TB"];
  if (!size) return "大小未知";
  while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
  return `${size.toFixed(unit > 1 ? 1 : 0)} ${units[unit]}`;
}
function downloadJson(filename, data) {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json;charset=utf-8" }));
  link.download = filename; link.click();
  window.setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}
function loadAgentConfig() {
  const raw = sessionStorage.getItem(AGENT_SESSION_KEY) || localStorage.getItem(AGENT_REMEMBER_KEY);
  try {
    const value = JSON.parse(raw || "{}");
    return { api_key: String(value.api_key || ""), base_url: String(value.base_url || "https://api.deepseek.com"), model: String(value.model || "deepseek-v4-flash-vision-exp"), remembered: Boolean(localStorage.getItem(AGENT_REMEMBER_KEY)) };
  } catch (_) {
    return { api_key: "", base_url: "https://api.deepseek.com", model: "deepseek-v4-flash-vision-exp", remembered: false };
  }
}
function saveAgentConfig(config, remembered) {
  const value = JSON.stringify({ api_key: config.api_key.trim(), base_url: config.base_url.trim(), model: config.model.trim() });
  sessionStorage.setItem(AGENT_SESSION_KEY, value);
  if (remembered) localStorage.setItem(AGENT_REMEMBER_KEY, value);
  else localStorage.removeItem(AGENT_REMEMBER_KEY);
}
function agentPayload(state, value) {
  const config = state.agentConfig;
  if (!config.api_key.trim()) throw new Error("请先在“设置”页填写智能体 API Key。");
  return { ...value, agent_config: { api_key: config.api_key.trim(), base_url: config.base_url.trim(), model: config.model.trim() } };
}
function validateAgentConfig(candidate) {
  if (candidate.api_key.length < 12) throw new Error("API Key 看起来不完整。");
  if (!/^https:\/\/[^/?#]+(?:\/[^?#]*)?$/.test(candidate.base_url)) throw new Error("API 地址必须是 HTTPS 根地址。");
  if (!candidate.model) throw new Error("请填写模型名称。");
  return candidate;
}
function card(title, text = "") {
  const item = el("div", "sdm-card");
  if (title) item.append(el("div", "sdm-card-title", title));
  if (text) item.append(el("div", "sdm-muted", text));
  return item;
}
function finding(item) {
  const node = el("div", `sdm-finding ${item.severity || "info"}`);
  node.append(el("div", "sdm-card-title", item.title || "提示"));
  if (item.detail) node.append(el("div", "", item.detail));
  if (item.recommendation) node.append(el("div", "sdm-muted", item.recommendation));
  if (item.node_ids?.length) node.append(el("div", "sdm-muted", `节点：${item.node_ids.join("、")}`));
  return node;
}
function select(items, selected = "") {
  const node = el("select", "sdm-select");
  for (const [value, label] of items) {
    const option = document.createElement("option"); option.value = value; option.textContent = label;
    option.selected = value === selected; node.append(option);
  }
  return node;
}
function consent(text, checked = false) {
  const label = el("label", "sdm-consent"); const input = document.createElement("input");
  input.type = "checkbox"; input.checked = checked; label.append(input, el("span", "", text));
  return { label, input };
}

function applyLayout(layout, state) {
  const current = graph();
  if (!current) throw new Error("当前没有可编辑画布");
  state.beforeLayout = workflow();
  current.beforeChange?.();
  try {
    for (const node of current._nodes || []) {
      const next = layout.positions?.[String(node.id)];
      if (next) node.pos = [...next];
    }
    // Semantic rebuild means "replace groups", including the unusual case
    // where the planner returns no new group.  Leaving old boxes behind would
    // make the canvas look like two layouts were stacked together.
    if (layout.mode === "semantic_rebuild") addGroups(current, layout.groups || [], state);
  } finally { current.afterChange?.(); }
  current.change?.(); app.canvas?.setDirty?.(true, true);
}

function addGroups(current, groups, state) {
  state.createdGroups = [];
  const existingGroups = Array.isArray(current._groups) ? [...current._groups] : [];
  // In semantic-rebuild mode old groups describe the old layout and must not
  // survive underneath the rebuilt graph. Capture their constructor first:
  // newer ComfyUI frontends may not expose LGraphGroup on window.
  const Group = window.LGraphGroup || window.LiteGraph?.LGraphGroup || existingGroups[0]?.constructor;
  for (const existing of existingGroups) {
    try { current.remove?.(existing); } catch (_) { /* fall through to array cleanup */ }
  }
  if (Array.isArray(current._groups) && current._groups.length) current._groups.splice(0, current._groups.length);
  if (!Group || !current.add) return;
  for (const plan of groups) {
    try {
      const group = new Group(plan.title);
      group.pos = [...plan.pos]; group.size = [...plan.size];
      group.color = plan.color; group.properties = { description: plan.description, workflow_doctor: true, stage_id: plan.stage_id, group_id: plan.group_id };
      current.add(group); state.createdGroups.push(group);
    } catch (error) {
      console.warn("[神都猫] 无法创建语义分组", error);
    }
  }
}

function restoreLayout(state) {
  if (!state.beforeLayout) throw new Error("本次会话中没有可撤销的排版。");
  const current = graph();
  current.configure?.(state.beforeLayout);
  current.change?.(); app.canvas?.setDirty?.(true, true);
  state.beforeLayout = null;
}

async function saveSnapshot(label) { return post("/snapshots/create", { workflow: workflow(), label }); }

function renderApp(host) {
  const state = { report: null, nodes: [], models: null, layout: null, beforeLayout: null, createdGroups: [], downloads: [], status: null, agentConfig: loadAgentConfig() };
  host.innerHTML = "";
  const root = el("div", "sdm-root");
  const header = el("div", "sdm-header");
  const title = el("div"); title.append(el("div", "sdm-title", "神都猫"), el("div", "sdm-subtitle", "ComfyUI 工作流助手 v1.0"));
  const community = el("div", "sdm-community", "加入 AI 讨论QQ群：340983417");
  const status = el("div", "sdm-status", "正在检查环境…"); state.status = status; header.append(title, community, status); root.append(header);
  const tabs = [["overview", "工作流检查"], ["repair", "缺失节点修复安装"], ["models", "缺失模型修复下载"], ["layout", "语义排版"], ["agent", "智能体"], ["history", "历史"], ["settings", "设置"]];
  const tabBar = el("div", "sdm-tabs"), panels = {}, tabButtons = {};
  function activateTab(id) {
    root.querySelectorAll(".sdm-tab,.sdm-panel").forEach((node) => node.classList.remove("active"));
    tabButtons[id]?.classList.add("active"); panels[id]?.classList.add("active");
  }
  for (const [id, label] of tabs) {
    const tab = el("button", `sdm-tab${id === "overview" ? " active" : ""}`, label); tab.type = "button";
    tab.addEventListener("click", () => activateTab(id));
    tabButtons[id] = tab; tabBar.append(tab); panels[id] = el("div", `sdm-panel${id === "overview" ? " active" : ""}`);
  }
  root.append(tabBar, ...Object.values(panels));

  const overview = panels.overview;
  const overviewResult = el("div");
  const dataConsent = consent("我确认将脱敏后的节点、连线、组件值发送给 DeepSeek。", false);
  const imageConsent = consent("附带当前画布截图，用于检查可读性与重叠。", false);
  const scan = async () => {
    activateTab("overview");
    overviewResult.replaceChildren(card("正在检查", "读取当前工作流、本机节点和模型依赖…"));
    const current = workflow();
    [state.report, state.nodes, state.models] = await Promise.all([
      post("/workflow/analyze", { workflow: current }), post("/nodes/resolve-packages", { workflow: current }), post("/models/scan", { workflow: current }),
    ]);
    overviewResult.replaceChildren();
    const summary = state.report.summary;
    overviewResult.append(card(`工作流健康度 ${summary.score}/100`, `${summary.node_count} 个节点 · ${summary.link_count} 条连线 · ${state.nodes.length} 类缺失节点 · ${state.models.summary.missing} 个缺失模型`));
    if (!state.report.findings.length) overviewResult.append(card("结构正常", "没有发现阻止运行的结构错误。"));
    state.report.findings.forEach((item) => overviewResult.append(finding(item)));
    renderRepair(); renderModels();
    const actions = el("div", "sdm-actions");
    if (state.nodes.length) actions.append(button(`处理 ${state.nodes.length} 类缺失节点`, async () => activateTab("repair"), "install"));
    if (state.models.summary.missing) actions.append(button(`处理 ${state.models.summary.missing} 个缺失模型`, async () => activateTab("models"), "download"));
    actions.append(button("生成排版方案", async () => activateTab("layout"), "info"));
    overviewResult.append(actions);
  };
  overview.append(card("先检查，再处理", "读取当前画布后，将缺失节点、模型和结构问题送到对应功能页。检查不会修改工作流。"), button("检查当前工作流", scan, "primary"), overviewResult);
  overview.append(button("导出当前迁移报告", async () => {
    if (!state.report) throw new Error("请先完成一次完整检查。");
    const report = { product: "神都猫 ComfyUI 工作流助手 v1.0", created_at: new Date().toISOString(), diagnostics: state.report, missing_nodes: state.nodes, model_requirements: state.models, layout: state.layout };
    const stored = await post("/reports/create", { report });
    downloadJson(`神都猫工作流迁移报告-${new Date().toISOString().slice(0, 10)}.json`, report);
    overviewResult.append(el("div", "sdm-success", `迁移报告已保存并导出：${stored.filename}`));
  }));

  const repair = panels.repair;
  const repairOutput = el("div");
  function packageKey(pack) {
    return String(pack.package_id || pack.reference || pack.files?.[0] || pack.title || "");
  }
  async function installPackage(pack, affected, host) {
    const plan = await post("/node-packs/install-plan", { node_type: affected[0].node_type });
    const request = plan.find((entry) => entry.title === pack.title) || plan[0];
    if (!request) throw new Error("没有可执行的安装计划");
    const instanceCount = affected.reduce((total, item) => total + Number(item.count || 0), 0);
    if (!window.confirm(`将通过 ComfyUI-Manager 安装“${pack.title}”。\n它会修复 ${affected.length} 类节点、${instanceCount} 个实例；完成后需要重启 ComfyUI。是否继续？`)) return;
    const queueResponse = await api.fetchApi("/manager/queue/status");
    const queue = await queueResponse.json().catch(() => ({}));
    if (queue.is_processing) throw new Error("ComfyUI-Manager 当前正在执行其他任务，请等待完成后再安装。");
    const response = await api.fetchApi(request.request.url, { method: request.request.method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(request.request.body) });
    if (!response.ok) throw new Error(`Manager 拒绝安装（${response.status}），请检查 Manager 安全设置。`);
    await api.fetchApi("/manager/queue/start");
    host.append(el("div", "sdm-success", "已提交给 Manager。重启 ComfyUI 后，请回到“工作流检查”重新检查。"));
  }
  function renderRepair() {
    repairOutput.replaceChildren();
    if (!state.nodes?.length) { repairOutput.append(card("还没有诊断结果", "请到“工作流检查”页检查当前工作流。")); return; }
    const packages = new Map(), unresolved = [];
    for (const item of state.nodes) {
      const candidates = item.package_candidates || [];
      if (!candidates.length) { unresolved.push(item); continue; }
      for (const pack of candidates) {
        const key = packageKey(pack);
        if (!packages.has(key)) packages.set(key, { pack, affected: [] });
        packages.get(key).affected.push(item);
      }
    }
    for (const { pack, affected } of packages.values()) {
      const count = affected.reduce((total, item) => total + Number(item.count || 0), 0);
      const itemCard = card(`节点包：${pack.title}`, `影响 ${affected.length} 类缺失节点 · ${count} 个实例`);
      itemCard.append(el("div", "sdm-badge", "可安装原节点包"), el("div", "sdm-muted", pack.reference || "ComfyUI-Manager 索引"));
      for (const item of affected) {
        const row = el("div", "sdm-affected-node");
        row.append(el("div", "sdm-card-title", `${item.node_type} · ${item.count} 个实例`), el("div", "sdm-muted", item.message)); itemCard.append(row);
      }
      itemCard.append(button(`安装此节点包（修复 ${count} 个实例）`, async () => installPackage(pack, affected, itemCard), "install"));
      repairOutput.append(itemCard);
    }
    if (unresolved.length) {
      const pending = card("待识别资源", `${unresolved.length} 类节点还没有可靠的安装来源。`);
      pending.append(el("div", "sdm-badge", "需要智能判断"));
      for (const item of unresolved) pending.append(el("div", "sdm-affected-node", `${item.node_type} · ${item.count} 个实例`));
      pending.append(el("div", "sdm-muted", "可到“智能体”页查看智能解读；不会自动替换节点。")); repairOutput.append(pending);
    }
  }
  repair.append(button("重新检查缺失节点", scan), repairOutput);

  const models = panels.models;
  const modelOutput = el("div");
  const downloadOutput = el("div");
  function targetFolder(file, requirement) {
    const folder = file?.suggested_folder || requirement?.role || "checkpoints";
    return FOLDERS.includes(folder) ? folder : "checkpoints";
  }
  function filenameScore(expected, filename) {
    const left = String(expected || "").toLowerCase().replace(/[^a-z0-9]/g, "");
    const right = String(filename || "").toLowerCase().replace(/[^a-z0-9]/g, "");
    if (!left || !right) return 0;
    if (left === right) return 100;
    return left.includes(right) || right.includes(left) ? 80 : [...new Set(left)].filter((value) => right.includes(value)).length;
  }
  function renderDownloadQueue() {
    downloadOutput.replaceChildren();
    if (!state.downloads.length) return;
    const queue = card("下载管理", "每个下载会校验文件；成功后自动回填到对应节点并重新扫描。 ");
    for (const item of state.downloads) {
      const row = el("div", `sdm-download ${item.status}`);
      const statusText = { planning: "智能体正在分析来源", ready: "等待确认下载", queued: "排队等待（最多同时 3 个）", downloading: "正在下载", done: "已下载并回填", failed: "下载失败，可重新入队续传", skipped: "没有可靠来源" }[item.status] || item.status;
      row.append(el("div", "sdm-card-title", item.requirement.expected), el("div", "sdm-muted", `${statusText}${item.file ? ` · ${item.file.name} → models/${targetFolder(item.file, item.requirement)}` : ""}`));
      if (item.total_bytes || item.downloaded_bytes) {
        const progress = document.createElement("progress"); progress.max = Number(item.total_bytes || 1); progress.value = Math.min(Number(item.downloaded_bytes || 0), progress.max); row.append(progress, el("div", "sdm-muted", `${bytes(item.downloaded_bytes)} / ${item.total_bytes ? bytes(item.total_bytes) : "大小未知"}`));
      }
      if (item.error) row.append(el("div", "sdm-muted", item.error));
      if (item.status === "failed" && item.file && item.candidate) row.append(button("重新入队并续传", async () => retryDownload(item), "download"));
      queue.append(row);
    }
    const ready = state.downloads.filter((item) => item.status === "ready");
    if (ready.length) queue.append(button(`确认下载并自动回填 ${ready.length} 项`, runDownloadQueue, "download"));
    downloadOutput.append(queue);
  }
  async function retryDownload(item) {
    const result = await post("/models/downloads/enqueue", { jobs: [{ provider: item.candidate.provider, download_url: item.file.download_url, filename: item.file.name, folder: targetFolder(item.file, item.requirement), sha256: item.file.sha256, size_bytes: item.file.size_bytes }] });
    item.queue_id = result.jobs[0]?.id; item.status = result.jobs[0]?.status || "queued"; item.error = null; renderDownloadQueue(); pollDownloadQueue();
  }
  async function buildAutoDownloadPlan() {
    const missing = (state.models?.requirements || []).filter((item) => item.status === "missing");
    if (!missing.length) throw new Error("当前没有需要下载的缺失模型。");
    state.downloads = missing.map((requirement) => ({ requirement, status: "planning" })); renderDownloadQueue();
    if (!dataConsent.input.checked) { activateTab("agent"); throw new Error("请先在“智能体”页确认模型来源检索的数据发送范围。"); }
    for (const job of state.downloads) {
      try {
        const searched = await post("/models/agent-search", agentPayload(state, { requirement: job.requirement }));
        const candidate = (searched.results || []).find((item) => item.compatible !== false && Number(item.compatibility_score || 0) >= 55);
        if (!candidate) { job.status = "skipped"; job.error = `${searched.plan?.summary || "智能体未找到可靠模型仓库。"} 未展示或下载低可信候选。`; renderDownloadQueue(); continue; }
        const listed = candidate.direct_file ? { files: [candidate.direct_file] } : await post("/models/files", { provider: candidate.provider, model_id: candidate.id });
        const files = (listed.files || []).filter((item) => item.safe && item.download_url);
        const file = files.sort((left, right) => filenameScore(job.requirement.expected, right.name) - filenameScore(job.requirement.expected, left.name))[0];
        if (!file) { job.status = "skipped"; job.error = "候选页面没有可安全下载的模型文件。"; renderDownloadQueue(); continue; }
        job.candidate = candidate; job.file = file; job.status = "ready";
      } catch (error) {
        job.status = "failed"; job.error = error.message || String(error);
      }
      renderDownloadQueue();
    }
  }
  async function runDownloadQueue() {
    const ready = state.downloads.filter((item) => item.status === "ready");
    if (!ready.length) return;
    const names = ready.map((item) => `• ${item.requirement.expected} ← ${item.file.name}`).join("\n");
    if (!window.confirm(`将下载并自动回填以下 ${ready.length} 项模型：\n${names}\n\n文件不会覆盖同名文件；下载完成后会重新扫描。是否继续？`)) return;
    await saveSnapshot("批量下载模型并回填前");
    const result = await post("/models/downloads/enqueue", { jobs: ready.map((job) => ({ provider: job.candidate.provider, download_url: job.file.download_url, filename: job.file.name, folder: targetFolder(job.file, job.requirement), sha256: job.file.sha256, size_bytes: job.file.size_bytes })) });
    ready.forEach((job, index) => { job.queue_id = result.jobs[index]?.id; job.status = result.jobs[index]?.status || "queued"; }); renderDownloadQueue(); pollDownloadQueue();
  }
  async function pollDownloadQueue() {
    const active = state.downloads.filter((item) => item.queue_id && !["done", "failed"].includes(item.status));
    if (!active.length) return;
    try {
      const response = await post("/models/downloads/status", { job_ids: active.map((item) => item.queue_id) });
      for (const server of response.jobs || []) {
        const job = state.downloads.find((item) => item.queue_id === server.id); if (!job) continue;
        Object.assign(job, server);
        if (job.status === "done" && !job.applied) { applyDownloadedModel(job.requirement, job.filename); job.applied = true; }
      }
      renderDownloadQueue();
      if (state.downloads.some((item) => item.queue_id && !["done", "failed"].includes(item.status))) window.setTimeout(pollDownloadQueue, 1000);
      else { state.models = await post("/models/scan", { workflow: workflow() }); renderModels(); renderDownloadQueue(); }
    } catch (error) { state.downloads.filter((item) => item.queue_id && !["done", "failed"].includes(item.status)).forEach((item) => { item.error = `无法读取下载进度：${error.message || error}`; }); renderDownloadQueue(); }
  }
  function renderModels() {
    modelOutput.replaceChildren();
    if (!state.models) { modelOutput.append(card("还没有模型诊断", "请到“工作流检查”页检查当前工作流。")); return; }
    for (const requirement of state.models.requirements || []) {
      const item = card(requirement.expected, `${requirement.role} · ${requirement.family || "模型家族待判断"} · ${requirement.status === "available" ? "本机已找到" : "需要修复"}`);
      if (requirement.local_candidates?.length) item.append(el("div", "sdm-muted", `本机候选：${requirement.local_candidates.slice(0, 3).map((candidate) => `${candidate.filename}（${candidate.score}%）`).join("；")}`));
      const search = button("智能体查找来源", async () => showModelSearch(requirement, item), "info");
      item.append(search); modelOutput.append(item);
    }
    if (!state.models.requirements?.length) modelOutput.append(card("未发现模型文件引用", "可能是纯工具工作流，或节点使用了动态模型值。"));
  }
  async function showModelSearch(requirement, hostCard) {
    hostCard.querySelector(".sdm-search-results")?.remove();
    if (!dataConsent.input.checked) { activateTab("agent"); throw new Error("请先在“智能体”页确认模型来源检索的数据发送范围。"); }
    const output = el("div", "sdm-search-results", "智能体正在理解模型用途并检索可信仓库…"); hostCard.append(output);
    try {
      const data = await post("/models/agent-search", agentPayload(state, { requirement }));
      output.replaceChildren();
      output.append(card(`需要下载：${requirement.expected}`, `用途：${requirement.role || "待判断"} · 推荐目录：models/${targetFolder(null, requirement)}${data.plan?.summary ? ` · 智能体判断：${data.plan.summary}` : ""}`));
      for (const error of data.errors || []) output.append(finding({ severity: "warning", title: `${error.provider} 暂不可用`, detail: error.message }));
      const reliable = (data.results || []).filter((item) => item.compatible !== false && Number(item.compatibility_score || 0) >= 55);
      for (const result of reliable) {
        const resultCard = el("div", "sdm-option"); resultCard.append(el("div", "sdm-card-title", `${result.name} · ${result.compatibility_score ?? "?"}%`));
        resultCard.append(el("div", "sdm-muted", `${result.source_label || result.provider} · ${result.type} · ${result.compatible === false ? "存在兼容风险" : "可进一步核对"}`));
        resultCard.append(button("查看文件与下载来源", async () => showFiles(result, resultCard, requirement), "info")); output.append(resultCard);
      }
      if (!reliable.length) output.append(finding({ severity: "warning", title: "没有可靠下载候选", detail: "已过滤低可信的热门噪声结果；当前不会下载错误模型。可稍后重试智能检索或手动提供可信仓库链接。" }));
    } catch (error) { output.replaceChildren(finding({ severity: "error", title: "搜索模型失败", detail: error.message || String(error) })); }
  }
  function applyDownloadedModel(requirement, filename) {
    const target = (graph()?._nodes || []).find((node) => String(node.id) === String(requirement.node_id));
    const index = Number(requirement.widget_index);
    if (!target || !Number.isInteger(index) || !target.widgets?.[index]) throw new Error("无法定位需要回填的模型组件；请在节点中手动选择下载的文件。");
    target.widgets[index].value = filename;
    target.widgets_values = target.widgets.map((widget) => widget.value);
    target.setDirtyCanvas?.(true, true); graph()?.change?.(); app.canvas?.setDirty?.(true, true);
  }
  async function showFiles(result, hostCard, requirement) {
    hostCard.querySelector(".sdm-file-results")?.remove();
    const output = el("div", "sdm-file-results", "正在获取文件与下载来源…"); hostCard.append(output);
    try {
      const data = result.direct_file ? { files: [result.direct_file] } : await post("/models/files", { provider: result.provider, model_id: result.id }); output.replaceChildren();
      const files = (data.files || []).filter((item) => item.safe && item.download_url).slice(0, 16);
      if (!files.length) { output.append(finding({ severity: "warning", title: "没有可下载文件", detail: "该候选未提供安全的模型文件，或来源需要登录授权。" })); return; }
      for (const file of files) {
      const row = el("div", "sdm-file"); row.append(el("div", "sdm-card-title", file.name), el("div", "sdm-muted", `${file.version || ""} ${bytes(file.size_bytes)}`));
      const folder = select(FOLDERS.map((value) => [value, value]), file.suggested_folder || "checkpoints");
      const source = select((file.sources || [{ label: "官方来源", url: file.download_url }]).map((item) => [item.url, item.label]));
      row.append(folder, source, button("确认下载", async () => {
        if (!window.confirm(`下载 ${file.name} 到 models/${folder.value}？\n将校验文件大小${file.sha256 ? "和 SHA256" : ""}，不会覆盖已有文件。`)) return;
        await saveSnapshot("下载模型并回填前");
        const queued = await post("/models/downloads/enqueue", { jobs: [{ provider: result.provider, download_url: source.value, filename: file.name, folder: folder.value, sha256: file.sha256, size_bytes: file.size_bytes }] });
        const task = { requirement, candidate: result, file: { ...file, download_url: source.value, suggested_folder: folder.value }, status: "ready" };
        state.downloads.push(task);
        task.queue_id = queued.jobs[0]?.id; task.status = queued.jobs[0]?.status || "queued"; renderDownloadQueue(); pollDownloadQueue();
        row.append(el("div", "sdm-success", "已加入下载管理队列；可在本页顶部查看进度并自动回填。"));
      }, "download")); output.append(row);
      }
    } catch (error) { output.replaceChildren(finding({ severity: "error", title: "获取文件失败", detail: error.message || String(error) })); }
  }
  models.append(card("批量修复", "先智能查找可信来源，再由你确认后按推荐目录下载、校验并自动回填。无法可靠匹配的模型不会下载。"), button("重新扫描模型", scan, "info"), button("一键尝试下载修复所有模型", buildAutoDownloadPlan, "primary"), downloadOutput, modelOutput);

  const layout = panels.layout;
  const layoutOutput = el("div");
  const layoutMode = select([["semantic_rebuild", "重新理解并分组"], ["preserve_groups", "保留现有分组"], ["structure_only", "只整理位置"]], "semantic_rebuild");
  layout.append(card("排版规则", "主流程从左到右；同一阶段从上到下；主链在上方；分支形成纵向泳道；只修改画布表现，不改参数和执行逻辑。"), layoutMode,
    button("生成语义排版方案", async () => {
      state.layout = await post("/layout/plan", { workflow: workflow(), mode: layoutMode.value }); renderLayoutPlan();
    }, "primary"),
    button("使用智能理解生成方案", async () => {
      if (!dataConsent.input.checked) { activateTab("agent"); throw new Error("请先在“智能体”页确认数据发送范围。"); }
      state.layout = null; layoutOutput.replaceChildren(card("正在理解工作流", "智能体只优化分区名称、用途与泳道，不会改变左到右的真实依赖关系。"));
      const result = await post("/agent/plan-layout", agentPayload(state, { workflow: workflow(), screenshot: imageConsent.input.checked ? canvasImage() : null, mode: layoutMode.value }));
      state.layout = result.layout; renderLayoutPlan(result.agent);
    }, "info"),
    button("撤销本次排版", async () => restoreLayout(state), "neutral"), layoutOutput);
  function renderLayoutPlan(agent = null) {
    layoutOutput.replaceChildren();
    if (!state.layout) return;
    const planCard = card("排版方案已生成", `${state.layout.summary.node_count} 个节点 · ${state.layout.summary.group_count} 个语义分区 · ${state.layout.summary.columns} 列`);
    planCard.append(button("应用此排版方案", async () => {
      await saveSnapshot("应用语义排版前"); applyLayout(state.layout, state);
      planCard.append(el("div", "sdm-success", "已应用。可使用上方“撤销本次排版”恢复。"));
    }, "apply"));
    layoutOutput.append(planCard);
    for (const group of state.layout.groups || []) layoutOutput.append(card(group.title, group.description));
    for (const suggestion of state.layout.reroute_suggestions || []) layoutOutput.append(card("建议检查长连线", `${suggestion.reason} 建议位置：${suggestion.suggested_pos.join("，")}。`));
    if (agent?.summary) layoutOutput.append(card("智能体说明", agent.summary));
  }

  const agent = panels.agent;
  const agentOutput = el("div");
  agent.append(card("智能体边界", "智能体是可选增强：本地检查、安装和排版都可独立使用。它用于解释陌生节点、判断替代方向，以及为语义排版补充分区名称和用途。"), dataConsent.label, imageConsent.label,
    button("智能解读当前工作流", async () => {
      if (!dataConsent.input.checked) throw new Error("请先确认数据发送范围。");
      agentOutput.replaceChildren(el("div", "sdm-muted", "DeepSeek 正在分析工作流…"));
      const result = await post("/agent/analyze", agentPayload(state, { workflow: workflow(), screenshot: imageConsent.input.checked ? canvasImage() : null }));
      agentOutput.replaceChildren(card("智能解读", result.summary || "分析完成")); (result.findings || []).forEach((item) => agentOutput.append(finding(item)));
      const actions = el("div", "sdm-actions"); actions.append(button("查看缺失节点修复安装", async () => activateTab("repair"), "install"), button("查看缺失模型修复下载", async () => activateTab("models"), "download"), button("使用智能理解排版", async () => activateTab("layout"), "info")); agentOutput.append(actions);
    }, "primary"), agentOutput);

  const history = panels.history;
  const historyOutput = el("div");
  history.append(button("刷新历史", async () => {
    const items = await request("/snapshots"); historyOutput.replaceChildren();
    for (const item of items) {
      const entry = card(item.label, new Date(item.created_at * 1000).toLocaleString());
      entry.append(button("恢复到画布", async () => {
        if (!window.confirm(`恢复“${item.label}”会替换当前画布，是否继续？`)) return;
        const snapshot = await request(`/snapshots/${encodeURIComponent(item.id)}`); graph().configure?.(snapshot.workflow); graph().change?.(); app.canvas?.setDirty?.(true, true);
      })); historyOutput.append(entry);
    }
    if (!items.length) historyOutput.append(card("没有历史快照", "在应用排版前会自动保存快照。"));
  }, true), historyOutput);

  const settingsPanel = panels.settings;
  const keyInput = document.createElement("input"); keyInput.className = "sdm-input"; keyInput.type = "password"; keyInput.autocomplete = "off"; keyInput.placeholder = "粘贴 API Key（仅本次浏览器会话）"; keyInput.value = state.agentConfig.api_key;
  const baseInput = document.createElement("input"); baseInput.className = "sdm-input"; baseInput.type = "url"; baseInput.placeholder = "https://api.deepseek.com"; baseInput.value = state.agentConfig.base_url;
  const modelInput = document.createElement("input"); modelInput.className = "sdm-input"; modelInput.type = "text"; modelInput.placeholder = "模型名称"; modelInput.value = state.agentConfig.model;
  const rememberConfig = consent("在此浏览器中记住密钥（默认只保留到关闭浏览器）。", state.agentConfig.remembered);
  const settingsOutput = el("div");
  settingsPanel.append(card("智能体配置", "配置保存在浏览器会话中，调用时才通过本机 ComfyUI 转发给你填写的 API 地址；不会写入 ComfyUI 配置文件、工作流、快照或报告。"), el("label", "sdm-field-label", "API Key"), keyInput, el("label", "sdm-field-label", "API 地址"), baseInput, el("label", "sdm-field-label", "模型名称"), modelInput, rememberConfig.label,
    button("测试连接（不保存配置）", async () => {
      const candidate = validateAgentConfig({ api_key: keyInput.value.trim(), base_url: baseInput.value.trim(), model: modelInput.value.trim() });
      settingsOutput.replaceChildren(el("div", "sdm-muted", "正在验证 API Key、地址和模型…"));
      const result = await post("/agent/test", { agent_config: candidate });
      settingsOutput.replaceChildren(el("div", "sdm-success", `连接成功 · ${result.model} · ${result.message}`));
    }, "info"),
    button("保存前台配置", async () => {
      const candidate = validateAgentConfig({ api_key: keyInput.value.trim(), base_url: baseInput.value.trim(), model: modelInput.value.trim() });
      saveAgentConfig(candidate, rememberConfig.input.checked); state.agentConfig = { ...candidate, remembered: rememberConfig.input.checked };
      status.textContent = `智能体已在前台配置 · ${candidate.model}`; status.classList.add("ready");
      settingsOutput.replaceChildren(el("div", "sdm-success", rememberConfig.input.checked ? "已保存到此浏览器。" : "已保存到本次浏览器会话；关闭浏览器后自动清除。"));
    }, true),
    button("清除本机前台配置", async () => {
      sessionStorage.removeItem(AGENT_SESSION_KEY); localStorage.removeItem(AGENT_REMEMBER_KEY); keyInput.value = ""; state.agentConfig = { api_key: "", base_url: "https://api.deepseek.com", model: "deepseek-v4-flash-vision-exp", remembered: false }; rememberConfig.input.checked = false;
      baseInput.value = state.agentConfig.base_url; modelInput.value = state.agentConfig.model; status.textContent = "离线模式 · 可在设置页配置智能体"; status.classList.remove("ready"); settingsOutput.replaceChildren(el("div", "sdm-success", "已清除浏览器中的前台配置。"));
    }), settingsOutput);

  host.append(root);
  request("/status").then((info) => { const configured = Boolean(state.agentConfig.api_key) || info.ai_configured; status.textContent = configured ? `智能体已配置 · ${state.agentConfig.api_key ? state.agentConfig.model : info.model}` : "离线模式 · 可在设置页配置"; status.classList.toggle("ready", configured); }).catch(() => { status.textContent = "后端未加载"; });
}

const stylesheet = document.createElement("link"); stylesheet.rel = "stylesheet"; stylesheet.href = new URL("./workflow_agent.css", import.meta.url).href; document.head.append(stylesheet);

let sidebarRegistered = false;
function openWorkflowDoctor() {
  const dialog = new ComfyDialog();
  dialog.element.style.zIndex = 1100;
  dialog.element.style.width = "min(560px, calc(100vw - 36px))";
  dialog.element.style.height = "min(760px, calc(100vh - 36px))";
  dialog.element.style.padding = "0";
  dialog.element.style.overflow = "hidden";
  const host = el("div", "sdm-dialog");
  dialog.show(host); renderApp(host);
}

app.registerExtension({
  name: EXTENSION_NAME,
  commands: [{
    id: "ShenDuMao.WorkflowDoctor.Open",
    label: "打开神都猫工作流助手",
    icon: "pi pi-sparkles",
    function: openWorkflowDoctor,
  }],
  init() {
    if (!app.extensionManager?.registerSidebarTab || window.__SHENDUMAO_WORKFLOW_DOCTOR__) return;
    window.__SHENDUMAO_WORKFLOW_DOCTOR__ = true;
    sidebarRegistered = true;
    app.extensionManager.registerSidebarTab({ id: "shendumao-workflow-doctor", icon: "pi pi-sparkles", title: "神都猫工作流助手", tooltip: "修复、整理和迁移 ComfyUI 工作流", type: "custom", render: renderApp, destroy: () => {} });
  },
  async setup() {
    if (sidebarRegistered || document.getElementById("shendumao-workflow-doctor-menu")) return;
    try {
      const { ComfyButton } = await import("../../scripts/ui/components/button.js");
      const nativeButton = new ComfyButton({ icon: "sparkles", content: "神都猫", tooltip: "打开神都猫工作流助手", action: openWorkflowDoctor, classList: "comfyui-button comfyui-menu-mobile-collapse" });
      if (app.menu?.settingsGroup?.element) {
        app.menu.settingsGroup.element.before(nativeButton.element);
        return;
      }
    } catch (_) { /* 旧版前端继续使用菜单回退。 */ }
    const menu = document.querySelector(".comfy-menu");
    if (!menu) return;
    const menuButton = button("神都猫工作流助手", openWorkflowDoctor, true);
    menuButton.id = "shendumao-workflow-doctor-menu";
    menu.append(menuButton);
  },
});
