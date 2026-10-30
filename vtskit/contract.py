"""Machine-readable contract (`python -m vtskit contract --json`), in the shape the AI Video
Production OS registry reads (skill_id, version, contract_version, provides,
dependencies, not_provided)."""

from . import __version__

SKILL_ID = "vtskit"
CONTRACT_VERSION = "1.0"

TOOLS = [
    {"tool_id": "vtskit/titleset", "kind": "python", "python": "vtskit.titleset.write", "writes_media": True, "mutates_input": False,
     "description": "Write a titleset (title and menu VOBs with filled navigation packs, VTS_nn_0.IFO and .BUP) into a VIDEO_TS folder"},
    {"tool_id": "vtskit/vmg", "kind": "python", "python": "vtskit.vmg.write", "writes_media": True, "mutates_input": False,
     "description": "Write the video manager (VIDEO_TS.IFO, .BUP and the title menu's VOB)"},
    {"tool_id": "vtskit/subtitles", "kind": "python", "python": "vtskit.subtitles.mux", "writes_media": True, "mutates_input": False,
     "description": "Add subpicture subtitle streams to a title's program stream, as spumux does"},
]


def contract():
    return {
        "skill_id": SKILL_ID,
        "id": SKILL_ID,
        "version": __version__,
        "contract_version": CONTRACT_VERSION,
        "provides": [
            {"id": "dvd.titleset", "lifecycle": "EXPERIMENTAL", "tool_id": "vtskit/titleset"},
            {"id": "dvd.video_manager", "lifecycle": "EXPERIMENTAL", "tool_id": "vtskit/vmg"},
            {"id": "dvd.subtitles", "lifecycle": "EXPERIMENTAL", "tool_id": "vtskit/subtitles"},
        ],
        # No Skill dependencies (ffmpeg-skill is not used).
        "dependencies": [],
        "external_tools": [],
        "not_provided": [
            "encoding the MPEG-2 program streams (ffmpeg, oncemore)",
            "disc images (udfkit)",
            "burning a disc",
            "a command line beyond this contract (the kit is a Python library)",
            "network access of any kind",
        ],
        "tools": TOOLS,
        "python_api": "vtskit.titleset, vtskit.vmg, vtskit.subtitles, vtskit.lang, vtskit.ifo",
        "execution": {"canonical_invocation": ["python", "-m", "vtskit", "contract", "--json"],
                      "network": False, "shell": False},
    }
