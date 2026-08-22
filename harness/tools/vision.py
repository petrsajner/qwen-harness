"""Vision nástroj - přiložení obrázku do konverzace (model ho uvidí)."""
from __future__ import annotations

from pathlib import Path

from harness.session import IMG_MIMES
from harness.tools.base import AgentContext, Tool


class ViewImageTool(Tool):
    name = "view_image"
    description = ("Attach an image file to the conversation so you can see it with your vision. "
                   "Supported: " + ", ".join(sorted(IMG_MIMES)) + ". Use for analyzing photos, diagrams, screenshots, UI mockups etc.")
    parameters = {
        "path": {"type": "string", "description": "Path to the image file"},
    }
    required = ["path"]

    def run(self, ctx: AgentContext, path: str) -> str:
        p = ctx.resolve(path)
        if not p.exists():
            return f"ERROR: Image not found: {p}"
        if p.suffix.lower() not in IMG_MIMES:
            return f"ERROR: Unsupported image type {p.suffix} (supported: {', '.join(sorted(IMG_MIMES))})"
        from PIL import Image
        with Image.open(p) as im:
            w, h = im.size
        ctx.pending_images.append(p)
        return f"Image attached ({w}x{h}, {p.stat().st_size / 1024:.0f} KB): {p.name}. You will see it in the next message."


def register_vision_tools(registry) -> None:
    registry.register(ViewImageTool())
