"""Review export. Required destination: project_root/reveiw/."""

from pathlib import Path
import datetime
import json
import os
import shutil
import zipfile


def export_review(project_root: Path, organic_home: Path, reason: str = "manual") -> Path:
    project_root = Path(project_root)
    organic_home = Path(organic_home)
    review_dir = project_root / "reveiw"
    review_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = review_dir / f"ORGANIC_HERMES_REVIEW_{stamp}.zip"

    manifest = {
        "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "reason": reason,
        "organic_home": str(organic_home),
        "review_dir": str(review_dir),
    }

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        if organic_home.exists():
            for path in organic_home.rglob("*"):
                if path.is_file():
                    # Deliberately skip common secret-bearing files.
                    if path.name.lower() in {".env", "credentials.json", "secrets.json"}:
                        continue
                    arc = "organic_home/" + str(path.relative_to(organic_home)).replace("\\", "/")
                    z.write(path, arc)

    if not out.exists():
        raise RuntimeError(f"Review ZIP was not created: {out}")
    return out
