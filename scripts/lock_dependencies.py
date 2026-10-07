"""Record installed runtime dependency closure, excluding unrelated inherited packages."""
from pathlib import Path
from importlib.metadata import distribution
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
import tomllib

root = Path(__file__).resolve().parents[1]
project = tomllib.loads((root / "pyproject.toml").read_text())
selected = project["project"]["dependencies"] + project["project"]["optional-dependencies"]["test"]
selected += project["project"]["optional-dependencies"].get("gemini", [])
pending = [Requirement(r) for r in selected]
versions, visited = {}, set()
while pending:
    requirement = pending.pop()
    name = canonicalize_name(requirement.name)
    extras = tuple(sorted(requirement.extras))
    if (name, extras) in visited:
        continue
    visited.add((name, extras))
    package = distribution(name)
    versions[name] = package.version
    for raw in package.requires or []:
        dependency = Requirement(raw)
        if dependency.marker is None or any(dependency.marker.evaluate({"extra": extra}) for extra in ("", *extras)):
            pending.append(dependency)
header = "# Installed dependency closure verified on Windows / Python 3.11.16.\n# Regenerate on other platforms; torch wheels and markers are platform-specific.\n"
(root / "requirements-lock.txt").write_text(header + "\n".join(f"{name}=={version}" for name,version in sorted(versions.items())) + "\n")
print(f"Recorded {len(versions)} runtime/test/selected-provider dependencies, excluding unrelated inherited packages.")
