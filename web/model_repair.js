import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";

const PLUGIN_NAME = "ComfyUI.AutoModelRepair";
const FLOATING_BUTTON_ID = "auto-model-repair-floating-btn";
const PANEL_ID = "auto-model-repair-panel";
const DOWNLOAD_PANEL_ID = "auto-model-repair-download-panel";

let downloadPollTimer = null;
let downloadPanelVisible = false;

function showMessage(message) {
  if (app?.ui?.dialog?.show) app.ui.dialog.show(message);
  else alert(message);
}

function getWorkflowJSON() {
  return app?.graph ? app.graph.serialize() : null;
}

async function postJSON(url, body) {
  const response = await api.fetchApi(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return await response.json();
}

async function getJSON(url) {
  const response = await api.fetchApi(url, { method: "GET" });
  return await response.json();
}

function applyResolvedWorkflow(workflow) {
  if (!workflow) return;
  if (typeof app?.loadGraphData === "function") app.loadGraphData(workflow);
  else if (typeof app?.graph?.configure === "function") app.graph.configure(workflow);
}

function ensurePanel() {
  let panel = document.getElementById(PANEL_ID);
  if (panel) return panel;

  panel = document.createElement("div");
  Object.assign(panel.style, {
    position: "fixed",
    right: "20px",
    top: "80px",
    width: "480px",
    maxHeight: "70vh",
    overflow: "auto",
    zIndex: "10001",
    background: "#1f1f1f",
    color: "#fff",
    border: "1px solid #444",
    borderRadius: "10px",
    padding: "12px",
    boxShadow: "0 8px 30px rgba(0,0,0,.35)",
    fontSize: "13px",
    display: "none",
  });
  panel.id = PANEL_ID;
  document.body.appendChild(panel);
  return panel;
}

function ensureDownloadPanel() {
  let panel = document.getElementById(DOWNLOAD_PANEL_ID);
  if (panel) return panel;

  panel = document.createElement("div");
  Object.assign(panel.style, {
    position: "fixed",
    left: "20px",
    bottom: "20px",
    width: "420px",
    maxHeight: "45vh",
    overflow: "auto",
    zIndex: "10001",
    background: "#1a1a1a",
    color: "#fff",
    border: "1px solid #444",
    borderRadius: "10px",
    padding: "12px",
    boxShadow: "0 8px 30px rgba(0,0,0,.35)",
    fontSize: "13px",
    display: "none",
  });
  panel.id = DOWNLOAD_PANEL_ID;
  document.body.appendChild(panel);
  return panel;
}

function collectSelections() {
  return Array.from(
    document.querySelectorAll(`#${PANEL_ID} input[type="radio"]:checked`)
  ).map((el) => ({
    node_id: el.dataset.nodeId,
    widget_index: Number(el.dataset.widgetIndex),
    filename: el.value,
    model_type: el.dataset.modelType || "",
  }));
}

async function applySelectedMatches() {
  const workflow = getWorkflowJSON();
  if (!workflow) return showMessage("没有可修复的工作流");

  const selections = collectSelections();
  if (!selections.length) return showMessage("你还没有选择任何候选项");

  const result = await postJSON("/auto_model_repair/apply_selected", {
    workflow,
    selections,
  });

  if (!result?.ok) {
    return showMessage(`应用失败: ${result?.error || "unknown error"}`);
  }

  applyResolvedWorkflow(result.data.workflow);
  const s = result.data?.summary || {};
  showMessage(`已应用 ${s.applied_count || 0} 项，跳过 ${s.skipped_count || 0} 项`);
}

async function scanWorkflow() {
  const workflow = getWorkflowJSON();
  if (!workflow) return showMessage("没有可扫描的工作流");

  const result = await postJSON("/auto_model_repair/scan_workflow", { workflow });
  if (!result?.ok) {
    return showMessage(`扫描失败: ${result?.error || "unknown error"}`);
  }

  renderPanel(result.data);
}

async function autoResolveWorkflow() {
  const workflow = getWorkflowJSON();
  if (!workflow) return showMessage("没有可修复的工作流");

  const result = await postJSON("/auto_model_repair/resolve_workflow", {
    workflow,
    apply_threshold: 92,
  });

  if (!result?.ok) {
    return showMessage(`修复失败: ${result?.error || "unknown error"}`);
  }

  applyResolvedWorkflow(result.data.workflow);
  const s = result.data?.summary || {};
  showMessage(`自动修复完成：已应用 ${s.applied_count || 0} 项，未解决 ${s.unresolved_count || 0} 项`);
  renderPanel(result.data.scan);
}

async function createDownloadTask(downloadInfo, item) {
  const useMirror = document.getElementById("amr-use-hf-mirror")?.checked || false;

  const result = await postJSON("/auto_model_repair/downloads/create", {
    url: downloadInfo.url,
    filename: downloadInfo.filename || item.expected,
    model_type: item.model_type,
    node_id: item.node_id,
    widget_index: item.widget_index,
    use_hf_mirror: useMirror,
  });

  if (!result?.ok) {
    return showMessage(`创建下载失败: ${result?.error || "unknown error"}`);
  }

  downloadPanelVisible = true;
  ensureDownloadPanel().style.display = "block";
  startDownloadPolling();
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  let n = bytes;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${n.toFixed(n >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

async function refreshDownloads() {
  const result = await getJSON("/auto_model_repair/downloads");
  if (result?.ok) renderDownloadPanel(result.data?.tasks || []);
}

function renderDownloadPanel(tasks) {
  const panel = ensureDownloadPanel();

  const rows = tasks
    .map((task) => {
      const progress =
        task.total_bytes > 0
          ? `${task.progress}%`
          : task.status === "completed"
          ? "100%"
          : "—";

      const speed = task.speed_bytes ? `${formatBytes(task.speed_bytes)}/s` : "";

      const statusColor =
        task.status === "completed"
          ? "#7CFC98"
          : task.status === "failed"
          ? "#FF6B6B"
          : task.status === "cancelled"
          ? "#999"
          : task.status === "downloading"
          ? "#FFD166"
          : "#bbb";

      const actionButton =
        task.status === "downloading"
          ? `<button class="amr-cancel-download" data-task-id="${task.task_id}" style="background:#b91c1c;color:#fff;border:none;border-radius:6px;padding:4px 8px;cursor:pointer;">取消</button>`
          : `<button class="amr-remove-download" data-task-id="${task.task_id}" style="background:#333;color:#fff;border:1px solid #555;border-radius:6px;padding:4px 8px;cursor:pointer;">移除</button>`;

      return `
        <div style="padding:10px;border:1px solid #333;border-radius:8px;margin-top:8px;">
          <div style="font-weight:700;">${task.filename}</div>
          <div style="margin-top:4px;color:#bbb;">目标目录: ${task.save_dir}</div>
          <div style="margin-top:4px;color:${statusColor};">状态: ${task.status}${task.error ? ` · ${task.error}` : ""}</div>
          <div style="margin-top:4px;">进度: ${progress} ${speed ? `· ${speed}` : ""}</div>
          <div style="margin-top:6px;height:8px;background:#333;border-radius:999px;overflow:hidden;">
            <div style="height:100%;width:${task.progress || 0}%;background:#2563eb;"></div>
          </div>
          <div style="margin-top:8px;">${actionButton}</div>
        </div>
      `;
    })
    .join("");

  panel.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;">
      <div style="font-size:16px;font-weight:700;">Download Center</div>
      <button id="amr-close-downloads" style="background:#333;color:#fff;border:1px solid #555;border-radius:6px;padding:4px 8px;cursor:pointer;">关闭</button>
    </div>
    <div style="margin-top:8px;color:#bbb;">任务数: ${tasks.length}</div>
    <div style="margin-top:12px;">${rows || '<div style="color:#999;">暂无下载任务</div>'}</div>
  `;

  panel.style.display = downloadPanelVisible ? "block" : "none";

  document.getElementById("amr-close-downloads")?.addEventListener("click", () => {
    downloadPanelVisible = false;
    panel.style.display = "none";
  });

  panel.querySelectorAll(".amr-cancel-download").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await postJSON("/auto_model_repair/downloads/cancel", {
        task_id: btn.dataset.taskId,
      });
      await refreshDownloads();
    });
  });

  panel.querySelectorAll(".amr-remove-download").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await postJSON("/auto_model_repair/downloads/remove", {
        task_id: btn.dataset.taskId,
      });
      await refreshDownloads();
    });
  });
}

function startDownloadPolling() {
  if (downloadPollTimer) return;
  refreshDownloads();
  downloadPollTimer = setInterval(async () => {
    await refreshDownloads();
  }, 1200);
}

function renderPanel(data) {
  const panel = ensurePanel();
  const summary = data?.summary || {};
  const items = data?.items || [];

  const rows = items
    .map((item, idx) => {
      const query = encodeURIComponent(item.expected || "");
      const modelscopeUrl = `https://modelscope.cn/models?name=${query}`;
      const huggingfaceUrl = `https://huggingface.co/models?search=${query}`;

      const statusHtml = item.exists
        ? `<span style="color:#7CFC98">已存在</span>`
        : item.best_match
        ? `<span style="color:#FFD166">可匹配 ${item.best_match.filename} (${item.best_match.score})</span>`
        : `<div><span style="color:#FF6B6B">未匹配</span><div style="margin-top:6px;"><a href="${modelscopeUrl}" target="_blank" style="color:#4da3ff;text-decoration:none;">去魔塔搜索</a><span style="margin:0 8px;color:#666;">|</span><a href="${huggingfaceUrl}" target="_blank" style="color:#4da3ff;text-decoration:none;">去 HuggingFace 搜索</a></div></div>`;

      const candidateGroupName = `amr_select_${item.node_id}_${item.widget_index}`;

      const candidates = (item.candidates || [])
        .map(
          (c) => `
          <label style="display:block;margin-top:6px;color:#bbb;cursor:pointer;">
            <input
              type="radio"
              name="${candidateGroupName}"
              value="${c.filename}"
              data-node-id="${item.node_id}"
              data-widget-index="${item.widget_index}"
              data-model-type="${item.model_type}"
              style="margin-right:6px;"
            />
            ${c.filename} <span style="color:#888">(${c.score})</span>
          </label>`
        )
        .join("");

      const downloadHtml = item.download
        ? `
        <div style="margin-top:8px;">
          <div style="color:#7dd3fc;">可下载（来自工作流备注）</div>
          <div style="margin-top:4px;word-break:break-all;color:#bbb;">${item.download.filename}</div>
          <div style="margin-top:6px;display:flex;gap:8px;flex-wrap:wrap;">
            <button class="amr-download-btn" data-item-index="${idx}" style="background:#16a34a;color:#fff;border:none;border-radius:6px;padding:6px 10px;cursor:pointer;">
              下载并放入对应目录
            </button>
            <a href="${item.download.url}" target="_blank" style="color:#4da3ff;text-decoration:none;line-height:30px;">打开原链接</a>
          </div>
        </div>`
        : "";

      return `
        <div style="padding:10px;border:1px solid #333;border-radius:8px;margin-top:8px;">
          <div><b>${item.model_type}</b> · node ${item.node_id} · ${item.widget_name}</div>
          <div style="margin-top:4px;word-break:break-all;">期望: ${item.expected}</div>
          <div style="margin-top:4px;">状态: ${statusHtml}</div>
          ${candidates ? `<div style="margin-top:6px;">候选:${candidates}</div>` : ""}
          ${downloadHtml}
        </div>
      `;
    })
    .join("");

  panel.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;">
      <div style="font-size:16px;font-weight:700;">Auto Model Repair</div>
      <button id="amr-close" style="background:#333;color:#fff;border:1px solid #555;border-radius:6px;padding:4px 8px;cursor:pointer;">关闭</button>
    </div>
    <div style="margin-top:10px;color:#ccc;">
      总计 ${summary.total || 0} 项，已存在 ${summary.exists || 0}，缺失 ${summary.missing || 0}，可自动修复 ${summary.auto_resolvable || 0}，可下载 ${summary.downloadable || 0}
    </div>
    <div style="margin-top:10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
      <button id="amr-apply-selected" style="background:#16a34a;color:#fff;border:none;border-radius:6px;padding:8px 10px;cursor:pointer;">应用已选择项</button>
      <button id="amr-apply" style="background:#2563eb;color:#fff;border:none;border-radius:6px;padding:8px 10px;cursor:pointer;">自动修复高分匹配</button>
      <button id="amr-rescan" style="background:#333;color:#fff;border:1px solid #555;border-radius:6px;padding:8px 10px;cursor:pointer;">重新扫描</button>
      <button id="amr-open-downloads" style="background:#333;color:#fff;border:1px solid #555;border-radius:6px;padding:8px 10px;cursor:pointer;">下载中心</button>
      <label style="display:flex;align-items:center;gap:6px;color:#bbb;">
        <input id="amr-use-hf-mirror" type="checkbox" />HF Mirror
      </label>
    </div>
    <div style="margin-top:12px;">${rows || '<div style="color:#999;">未发现模型项</div>'}</div>
  `;

  panel.style.display = "block";

  document.getElementById("amr-close")?.addEventListener("click", () => {
    panel.style.display = "none";
  });
  document.getElementById("amr-rescan")?.addEventListener("click", scanWorkflow);
  document.getElementById("amr-apply")?.addEventListener("click", autoResolveWorkflow);
  document.getElementById("amr-apply-selected")?.addEventListener("click", applySelectedMatches);
  document.getElementById("amr-open-downloads")?.addEventListener("click", () => {
    downloadPanelVisible = true;
    ensureDownloadPanel().style.display = "block";
    startDownloadPolling();
  });

  panel.querySelectorAll(".amr-download-btn").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const item = items[Number(btn.dataset.itemIndex)];
      if (item?.download) await createDownloadTask(item.download, item);
    })
  );
}

function ensureFloatingButton() {
  let container = document.getElementById(FLOATING_BUTTON_ID);
  if (container) return container;

  container = document.createElement("div");
  container.id = FLOATING_BUTTON_ID;
  Object.assign(container.style, {
    position: "fixed",
    left: "20px",
    bottom: "20px",
    zIndex: "10000",
    display: "flex",
    alignItems: "center",
    gap: "6px",
  });

  const mainBtn = document.createElement("button");
  mainBtn.textContent = "神都猫模型助手";
  Object.assign(mainBtn.style, {
    padding: "8px 12px",
    background: "#2563eb",
    color: "#fff",
    border: "none",
    borderRadius: "6px",
    cursor: "pointer",
    fontSize: "13px",
  });

  const closeBtn = document.createElement("button");
  closeBtn.textContent = "×";
  Object.assign(closeBtn.style, {
    width: "24px",
    height: "24px",
    background: "#333",
    color: "#fff",
    border: "none",
    borderRadius: "6px",
    cursor: "pointer",
    fontSize: "14px",
    lineHeight: "1",
  });

  mainBtn.onclick = scanWorkflow;
  closeBtn.onclick = () => {
    container.style.display = "none";
  };

  container.appendChild(mainBtn);
  container.appendChild(closeBtn);
  document.body.appendChild(container);
  return container;
}

app.registerExtension({
  name: PLUGIN_NAME,
  async setup() {
    const mount = () => {
      if (!document.body) return setTimeout(mount, 500);
      ensureFloatingButton();
      ensurePanel();
      ensureDownloadPanel();
    };
    mount();
  },
});