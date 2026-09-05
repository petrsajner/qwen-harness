"""Exercise the real single-model application worker in an isolated project."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from harness.application import ApplicationService
from harness.config import Config, load_config
from harness import servermgmt


def main():
    original = load_config()
    if servermgmt.health(original):
        raise RuntimeError("A model server is already active; run this isolated test after it is stopped")
    with tempfile.TemporaryDirectory(prefix="model-e2e-", dir=ROOT / "runtime") as directory:
        data = copy.deepcopy(original.data)
        data["paths"]["models_dir"] = str(original.path("paths.models_dir"))
        data["paths"]["llama_dir"] = str(original.path("paths.llama_dir"))
        data["skills"]["directory"] = str(ROOT / "skills")
        data["agent"].update(workspace=None, autonomy="auto")
        data["thinking"] = False
        data["work_mode"] = "discussion"
        cfg = Config(data, Path(directory))
        project = cfg.root / "sample-project"
        project.mkdir()
        service = ApplicationService(cfg)
        service.preferences.update(model="q5", thinking="off", autonomy="auto")
        session = service.new_session(str(project), "discussion")
        started = time.monotonic()
        try:
            service.submit(session.id,
                "Use write_file to create probe.txt in the current project with exactly WORKSPACE_OK on its first line. "
                "Then use read_file to verify the content and report the result briefly. Do not do any other work.",
                request_id="real-workspace-probe")
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                job = service.store.job("real-workspace-probe")
                if job["status"] in ("complete", "failed", "stopped"):
                    break
                time.sleep(0.3)
            else:
                service.stop(session.id)
                raise RuntimeError("Real-model integration probe did not finish within its test deadline")
            target = project / "probe.txt"
            if job["status"] != "complete" or not target.is_file() or target.read_text().strip() != "WORKSPACE_OK":
                raise AssertionError(json.dumps({"job": job, "history": session.messages[-8:]}, ensure_ascii=False))
            tools = [m.get("name") for m in session.messages if m.get("role") == "tool"]
            if "write_file" not in tools or "read_file" not in tools:
                raise AssertionError(f"Missing expected tool calls: {tools}")
            result = {"model": "q5", "status": job["status"], "tools": tools,
                      "seconds": round(time.monotonic() - started, 2),
                      "usage": session.meta.get("last_usage"),
                      "context_snapshot": bool(session.meta.get("context_snapshot"))}
            from PIL import Image
            vision_session = service.new_session(work_mode="discussion")
            vision_session.persist()
            image = vision_session.dir / "solid-green.png"
            Image.new("RGB", (320, 240), (0, 190, 0)).save(image)
            image_record = service.store.register_file(image, vision_session.id, "attachment")
            service.submit(vision_session.id,
                "Look at the attached image. Which single color fills it? Reply with one English color word.",
                attachments=[image_record["id"]], request_id="real-vision-probe")
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                vision_job = service.store.job("real-vision-probe")
                if vision_job["status"] in ("complete", "failed", "stopped"):
                    break
                time.sleep(0.3)
            else:
                raise RuntimeError("Vision probe exceeded its test deadline")
            answer = next((str(m.get("content", "")) for m in reversed(vision_session.messages)
                           if m.get("role") == "assistant"), "")
            if vision_job["status"] != "complete" or "green" not in answer.lower():
                raise AssertionError(f"Vision probe failed: {vision_job['status']}: {answer}")
            result["vision_answer"] = answer
            result["vision_usage"] = vision_session.meta.get("last_usage")
            (ROOT / "runtime" / "workspace-model-e2e.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            print(json.dumps(result, indent=2))
        finally:
            service.close()
            servermgmt.stop(cfg, quiet=True)


if __name__ == "__main__":
    main()
