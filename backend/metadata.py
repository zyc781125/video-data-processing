from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_metadata(output_dir: Path, video: dict[str, Any]) -> Path:
    source_path = Path(video["source_path"])
    directory = output_dir / source_path.parent
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{source_path.stem}__metadata.json"
    target.write_text(json.dumps({
        "source_id": video["source_id"], "source_path": video["source_path"],
        "source_hash": video["source_hash"], "source_duration": video["duration"],
        "width": video["width"], "height": video["height"], "fps_info": video["fps_info"],
        "decision": video.get("decision"), "status": video["status"], "updated_at": video["updated_at"],
        "segments": video.get("segments", []),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
