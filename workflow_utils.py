from copy import deepcopy
from typing import Any, Dict, List, Tuple


def clone_workflow(workflow: Dict[str, Any]) -> Dict[str, Any]:
    return deepcopy(workflow)


def iter_workflow_nodes(workflow: Dict[str, Any]):
    if isinstance(workflow.get("nodes"), list):
        for node in workflow["nodes"]:
            yield node


def get_node_identity(node: Dict[str, Any]) -> Tuple[str, str, str]:
    node_id = str(node.get("id", ""))
    node_type = str(node.get("type", "") or "")
    title = str(node.get("title", "") or node_type)
    return node_id, node_type, title


def extract_node_widgets(node: Dict[str, Any]) -> List[Dict[str, Any]]:
    widgets_values = node.get("widgets_values")
    widgets = node.get("widgets") or []
    out: List[Dict[str, Any]] = []

    if isinstance(widgets, list) and widgets:
        for idx, widget in enumerate(widgets):
            name = str(widget.get("name", "") or f"widget_{idx}")
            value = None
            if isinstance(widgets_values, list) and idx < len(widgets_values):
                value = widgets_values[idx]
            out.append({
                "index": idx,
                "name": name,
                "value": value,
                "widget": widget,
            })
        return out

    if isinstance(widgets_values, list):
        for idx, value in enumerate(widgets_values):
            out.append({
                "index": idx,
                "name": f"widget_{idx}",
                "value": value,
                "widget": None,
            })
    return out


def set_widget_value(node: Dict[str, Any], widget_index: int, value: Any):
    widgets_values = node.get("widgets_values")
    if not isinstance(widgets_values, list):
        return False
    if widget_index < 0 or widget_index >= len(widgets_values):
        return False
    widgets_values[widget_index] = value
    return True
