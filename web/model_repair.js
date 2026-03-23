import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";

const PLUGIN_NAME = "ComfyUI.AutoModelRepair";
const FLOATING_BUTTON_ID = "auto-model-repair-floating-btn";
const PANEL_ID = "auto-model-repair-panel";

function ensurePanel() {
  let panel = document.getElementById(PANEL_ID);
  if (panel) return panel;

  panel = document.createElement("div");
  panel.id = PANEL_ID;
  panel.style.position = "fixed";
  panel.style.right = "20px";
  panel.style.top = "80px";
  panel.style.width = "460px";
  panel.style.maxHeight = "70vh";
  panel.style.overflow = "auto";
  panel.style.zIndex = "10001";
  panel.style.background = "#1f1f1f";
  panel.style.color = "#fff";
  panel.style.border = "1px solid #444";
  panel.style.borderRadius = "10px";
  panel.style.padding = "12px";
  panel.style.boxShadow = "0 8px 30px rgba(0,0,0,.35)";
  panel.style.fontSize = "13px";
  panel.style.display = "none";

  document.body.appendChild(panel);
  return panel;
}

function showMessage(message) {
  if (app?.ui?.dialog?.show) {
    app.ui.dialog.show(message);
  } else {
    alert(message);
  }
}

function getWorkflowJSON() {
  if (!app?.graph) return null;
  return app.graph.serialize();
}

async function postJSON(url, body) {
  const response = await api.fetchApi(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return await response.json();
}

function applyResolvedWorkflow(workflow) {
  if (!workflow) return;

  if (typeof app?.loadGraphData === "function") {
    app.loadGraphData(workflow);
    return;
  }

  if (typeof app?.graph?.configure === "function") {
    app.graph.configure(workflow);
  }
}

function collectSelections() {
  const checked = Array.from(
    document.querySelectorAll(`#${PANEL_ID} input[type="radio"]:checked`)
  );

  return checked.map((el) => ({
    node_id: el.dataset.nodeId,
    widget_index: Number(el.dataset.widgetIndex),
    filename: el.value,
    model_type: el.dataset.modelType || "",
  }));
}

async function applySelectedMatches() {
  const workflow = getWorkflowJSON();
  if (!workflow) {
    showMessage("没有可修复的工作流");
    return;
  }

  const selections = collectSelections();
  if (!selections.length) {
    showMessage("你还没有选择任何候选项");
    return;
  }

  const result = await postJSON("/auto_model_repair/apply_selected", {
    workflow,
    selections,
  });

  if (!result?.ok) {
    showMessage(`应用失败: ${result?.error || "unknown error"}`);
    return;
  }

  applyResolvedWorkflow(result.data.workflow);

  const summary = result.data?.summary || {};
  showMessage(`已应用 ${summary.applied_count || 0} 项，跳过 ${summary.skipped_count || 0} 项`);
}

async function scanWorkflow() {
  const workflow = getWorkflowJSON();
  if (!workflow) {
    showMessage("没有可扫描的工作流");
    return;
  }

  const result = await postJSON("/auto_model_repair/scan_workflow", { workflow });
  if (!result?.ok) {
    showMessage(`扫描失败: ${result?.error || "unknown error"}`);
    return;
  }

  renderPanel(result.data);
}

async function autoResolveWorkflow() {
  const workflow = getWorkflowJSON();
  if (!workflow) {
    showMessage("没有可修复的工作流");
    return;
  }

  const result = await postJSON("/auto_model_repair/resolve_workflow", {
    workflow,
    apply_threshold: 92,
  });

  if (!result?.ok) {
    showMessage(`修复失败: ${result?.error || "unknown error"}`);
    return;
  }

  applyResolvedWorkflow(result.data.workflow);

  const summary = result.data?.summary || {};
  showMessage(
    `自动修复完成：已应用 ${summary.applied_count || 0} 项，未解决 ${summary.unresolved_count || 0} 项`
  );

  renderPanel(result.data.scan);
}

function renderPanel(data) {
  const panel = ensurePanel();
  const summary = data?.summary || {};
  const items = data?.items || [];

  const rows = items.map((item) => {
    const query = encodeURIComponent(item.expected || "");
    const modelscopeUrl = `https://modelscope.cn/models?name=${query}`;
    const huggingfaceUrl = `https://huggingface.co/models?search=${query}`;

    let statusHtml = "";

    if (item.exists) {
      statusHtml = `<span style="color:#7CFC98">已存在</span>`;
    } else if (item.best_match) {
      statusHtml = `<span style="color:#FFD166">可匹配 ${item.best_match.filename} (${item.best_match.score})</span>`;
    } else {
      statusHtml = `
        <div>
          <span style="color:#FF6B6B">未匹配</span>
          <div style="margin-top:6px;">
            <a href="${modelscopeUrl}" target="_blank" style="color:#4da3ff;text-decoration:none;">
              去魔塔搜索
            </a>
            <span style="margin:0 8px;color:#666;">|</span>
            <a href="${huggingfaceUrl}" target="_blank" style="color:#4da3ff;text-decoration:none;">
              去 HuggingFace 搜索
            </a>
          </div>
        </div>
      `;
    }

    const candidateGroupName = `amr_select_${item.node_id}_${item.widget_index}`;

    const candidates = (item.candidates || [])
      .map((c) => `
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
        </label>
      `)
      .join("");

    return `
      <div style="padding:10px;border:1px solid #333;border-radius:8px;margin-top:8px;">
        <div><b>${item.model_type}</b> · node ${item.node_id} · ${item.widget_name}</div>
        <div style="margin-top:4px;word-break:break-all;">期望: ${item.expected}</div>
        <div style="margin-top:4px;">状态: ${statusHtml}</div>
        ${candidates ? `<div style="margin-top:6px;">候选:${candidates}</div>` : ""}
      </div>
    `;
  }).join("");

  panel.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;">
      <div style="font-size:16px;font-weight:700;">Auto Model Repair</div>
      <button
        id="amr-close"
        style="background:#333;color:#fff;border:1px solid #555;border-radius:6px;padding:4px 8px;cursor:pointer;"
      >
        关闭
      </button>
    </div>

    <div style="margin-top:10px;color:#ccc;">
      总计 ${summary.total || 0} 项，已存在 ${summary.exists || 0}，缺失 ${summary.missing || 0}，可自动修复 ${summary.auto_resolvable || 0}
    </div>

    <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;">
      <button
        id="amr-apply-selected"
        style="background:#16a34a;color:#fff;border:none;border-radius:6px;padding:8px 10px;cursor:pointer;"
      >
        应用已选择项
      </button>
      <button
        id="amr-apply"
        style="background:#2563eb;color:#fff;border:none;border-radius:6px;padding:8px 10px;cursor:pointer;"
      >
        自动修复高分匹配
      </button>
      <button
        id="amr-rescan"
        style="background:#333;color:#fff;border:1px solid #555;border-radius:6px;padding:8px 10px;cursor:pointer;"
      >
        重新扫描
      </button>
    </div>

    <div style="margin-top:12px;">
      ${rows || '<div style="color:#999;">未发现模型项</div>'}
    </div>
  `;

  panel.style.display = "block";

  document.getElementById("amr-close")?.addEventListener("click", () => {
    panel.style.display = "none";
  });

  document.getElementById("amr-rescan")?.addEventListener("click", async () => {
    await scanWorkflow();
  });

  document.getElementById("amr-apply")?.addEventListener("click", async () => {
    await autoResolveWorkflow();
  });

  document.getElementById("amr-apply-selected")?.addEventListener("click", async () => {
    await applySelectedMatches();
  });
}

function ensureFloatingButton() {
  let btn = document.getElementById(FLOATING_BUTTON_ID);
  if (btn) return btn;

let container = document.createElement("div");
container.id = FLOATING_BUTTON_ID;
container.style.position = "fixed";
container.style.left = "20px";   // 你可以改成 left
container.style.bottom = "20px";
container.style.zIndex = "10000";
container.style.display = "flex";
container.style.alignItems = "center";
container.style.gap = "6px";

const mainBtn = document.createElement("button");
mainBtn.textContent = "神都猫模型替换助手";
mainBtn.style.padding = "8px 12px";
mainBtn.style.background = "#2563eb";
mainBtn.style.color = "#fff";
mainBtn.style.border = "none";
mainBtn.style.borderRadius = "6px";
mainBtn.style.cursor = "pointer";
mainBtn.style.fontSize = "13px";

const closeBtn = document.createElement("button");
closeBtn.textContent = "×";
closeBtn.style.width = "24px";
closeBtn.style.height = "24px";
closeBtn.style.background = "#333";
closeBtn.style.color = "#fff";
closeBtn.style.border = "none";
closeBtn.style.borderRadius = "6px";
closeBtn.style.cursor = "pointer";
closeBtn.style.fontSize = "14px";
closeBtn.style.lineHeight = "1";

mainBtn.onclick = async () => {
  await scanWorkflow();
};

closeBtn.onclick = () => {
  container.style.display = "none";
};

container.appendChild(mainBtn);
container.appendChild(closeBtn);

document.body.appendChild(container);

  btn.style.position = "fixed";
  btn.style.left = "20px";
  btn.style.bottom = "20px";
  btn.style.zIndex = "10000";
  btn.style.padding = "10px 14px";
  btn.style.background = "#2563eb";
  btn.style.color = "#fff";
  btn.style.border = "none";
  btn.style.borderRadius = "8px";
  btn.style.cursor = "pointer";
  btn.style.boxShadow = "0 6px 20px rgba(0,0,0,.3)";
  btn.style.fontSize = "14px";

  btn.onclick = async () => {
    await scanWorkflow();
  };

  document.body.appendChild(btn);
  return btn;
}

app.registerExtension({
  name: PLUGIN_NAME,

  async setup() {
    const mount = () => {
      if (!document.body) {
        setTimeout(mount, 500);
        return;
      }
      ensureFloatingButton();
      ensurePanel();
    };

    mount();
  },
});
