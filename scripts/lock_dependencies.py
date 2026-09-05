"""Record the installed Windows/Python runtime dependency closure for reproducible releases."""
from importlib.metadata import distribution
from pathlib import Path
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parent.parent


def main():
    required = [Requirement(line.strip()) for line in (ROOT / "requirements.txt").read_text().splitlines()
                if line.strip() and not line.startswith("#")]
    found = {}
    active_extras = {}
    processed = set()
    while required:
        requirement = required.pop()
        key = requirement.name.lower().replace("_", "-")
        extras = active_extras.setdefault(key, set())
        extras.update(requirement.extras)
        signature = (key, tuple(sorted(extras)))
        if signature in processed:
            continue
        processed.add(signature)
        package = distribution(requirement.name)
        found[key] = f"{package.metadata['Name']}=={package.version}"
        for raw in package.requires or []:
            dependency = Requirement(raw)
            if not dependency.marker or any(dependency.marker.evaluate({"extra": extra}) for extra in ({""} | extras)):
                required.append(dependency)
    path = ROOT / "requirements-windows-py312.lock"
    path.write_text("# Verified Windows x64 / Python 3.12 dependency versions\n" + "\n".join(found[k] for k in sorted(found)) + "\n", encoding="utf-8")
    print(f"Recorded {len(found)} packages in {path}")


if __name__ == "__main__":
    main()
