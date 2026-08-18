from __future__ import annotations

import json

from .contracts import PresenterPacket


def presenter_system_instruction() -> str:
    return (
        "You are the final Organic presenter. Explain only the validated packet. "
        "Do not recompute, rejudge, replace, or contradict verified results. "
        "If verified=false, do not manufacture an answer. You may improve wording only."
    )


def presenter_payload(packet: PresenterPacket) -> str:
    return json.dumps(packet.to_dict(), ensure_ascii=False, sort_keys=True)
