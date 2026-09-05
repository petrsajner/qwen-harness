"""Run the production UI/API against disposable data and a deterministic model."""
import copy
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from test_workspace import Model
from harness.application import ApplicationService
from harness.config import Config, load_config
from harness.projects import Projects
from harness.web_api import create_app
import uvicorn


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="ui-fixture-", dir=ROOT / "runtime") as directory:
        data = copy.deepcopy(load_config().data)
        data["agent"].update(workspace=None, autonomy="auto")
        data["work_mode"] = "development"
        data["skills"]["directory"] = str(ROOT / "skills")
        cfg = Config(data, Path(directory))
        service = ApplicationService(cfg, llm_factory=Model, manage_model=False)
        service.preferences["language"] = "cs"
        project = Projects(cfg).create_new("Studio poznámek")
        session = service.new_session(project["path"], "development")
        session.meta["title"] = "Vyhledávání a ukládání"
        session.add("user", "Doplň vyhledávání a zachovej průběžné ukládání poznámek.")
        session.add("assistant", "Prošel jsem ukládání poznámek. Následuje doplnění vyhledávání a kontrola obnovení rozepsané práce.")
        uvicorn.run(create_app(cfg, service=service), host="127.0.0.1", port=7878, log_level="warning")
