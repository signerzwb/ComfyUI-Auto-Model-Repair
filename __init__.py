import logging
from pathlib import Path
from aiohttp import web

from server import PromptServer

from .resolver import ModelResolverService

PLUGIN_DIR = Path(__file__).resolve().parent
WEB_DIR = PLUGIN_DIR / "web"
CONFIG_PATH = PLUGIN_DIR / "config.json"

logger = logging.getLogger("ComfyUI-Auto-Model-Repair")

service = ModelResolverService(plugin_dir=PLUGIN_DIR, config_path=CONFIG_PATH)

routes = PromptServer.instance.routes


@routes.get("/auto_model_repair/status")
async def auto_model_repair_status(request):
    try:
        return web.json_response({
            "ok": True,
            "plugin": "ComfyUI-Auto-Model-Repair",
            "version": "0.3.0",
        })
    except Exception as e:
        logger.exception("status failed")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@routes.post("/auto_model_repair/scan_workflow")
async def auto_model_repair_scan_workflow(request):
    try:
        payload = await request.json()
        workflow = payload.get("workflow")
        if not workflow:
            return web.json_response({"ok": False, "error": "missing workflow"}, status=400)

        result = service.scan_workflow(workflow)
        return web.json_response({"ok": True, "data": result})
    except Exception as e:
        logger.exception("scan_workflow failed")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@routes.post("/auto_model_repair/resolve_workflow")
async def auto_model_repair_resolve_workflow(request):
    try:
        payload = await request.json()
        workflow = payload.get("workflow")
        apply_threshold = int(payload.get("apply_threshold", 92))
        if not workflow:
            return web.json_response({"ok": False, "error": "missing workflow"}, status=400)

        result = service.resolve_workflow(workflow, auto_apply_threshold=apply_threshold)
        return web.json_response({"ok": True, "data": result})
    except Exception as e:
        logger.exception("resolve_workflow failed")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@routes.post("/auto_model_repair/apply_selected")
async def auto_model_repair_apply_selected(request):
    try:
        payload = await request.json()
        workflow = payload.get("workflow")
        selections = payload.get("selections", [])

        if not workflow:
            return web.json_response({"ok": False, "error": "missing workflow"}, status=400)

        result = service.apply_selected_matches(workflow, selections)
        return web.json_response({"ok": True, "data": result})
    except Exception as e:
        logger.exception("apply_selected failed")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


WEB_DIRECTORY = str(WEB_DIR)
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
