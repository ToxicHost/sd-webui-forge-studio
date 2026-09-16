"""Standalone launcher for Forge Studio.

Internal alpha (the default): a configured, contained, NO_MODEL start.

    venv\\Scripts\\python.exe launch_studio.py --config <path-to-config.json>

The legacy mock shell remains available for UI work without any backend:

    venv\\Scripts\\python.exe launch_studio.py --mock [--port N] [--demo]
"""

from pathlib import Path
import sys

APP_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_ROOT))


def _mock_arguments(arguments: list[str]) -> list[str]:
    """Remove real-backend-only options before entering the mock parser.

    The double-click wrapper always supplies ``--config <path>`` and appends
    ``--mock`` when requested. The mock presentation deliberately has no
    configuration-file contract, so forwarding that pair makes argparse
    refuse the otherwise valid shortcut before Studio can start.
    """

    forwarded: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--config":
            if index + 1 >= len(arguments):
                raise SystemExit("--config requires a path")
            index += 2
            continue
        if argument.startswith("--config="):
            index += 1
            continue
        forwarded.append(argument)
        index += 1
    return forwarded


if __name__ == "__main__":
    arguments = sys.argv[1:]
    if "--mock" in arguments:
        import forge_studio.presentation

        arguments.remove("--mock")
        raise SystemExit(
            forge_studio.presentation.main(_mock_arguments(arguments))
        )
    import forge_studio.launch

    raise SystemExit(forge_studio.launch.main(arguments))
