from copy import deepcopy
def clone_workflow(workflow): return deepcopy(workflow)
def iter_workflow_nodes(workflow):
    if isinstance(workflow.get("nodes"), list):
        for node in workflow["nodes"]: yield node
def get_node_identity(node):
    node_id = str(node.get("id", "")); node_type = str(node.get("type", "") or ""); title = str(node.get("title", "") or node_type); return node_id, node_type, title
def extract_node_widgets(node):
    widgets_values = node.get("widgets_values"); widgets = node.get("widgets") or []; out = []
    if isinstance(widgets, list) and widgets:
        for idx, widget in enumerate(widgets):
            name = str(widget.get("name", "") or f"widget_{idx}"); value = widgets_values[idx] if isinstance(widgets_values, list) and idx < len(widgets_values) else None
            out.append({"index": idx, "name": name, "value": value, "widget": widget})
        return out
    if isinstance(widgets_values, list):
        for idx, value in enumerate(widgets_values): out.append({"index": idx, "name": f"widget_{idx}", "value": value, "widget": None})
    return out
def set_widget_value(node, widget_index, value):
    widgets_values = node.get("widgets_values")
    if not isinstance(widgets_values, list) or widget_index < 0 or widget_index >= len(widgets_values): return False
    widgets_values[widget_index] = value; return True
