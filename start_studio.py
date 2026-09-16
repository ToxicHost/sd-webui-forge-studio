"""Start Studio from a Git checkout using the existing release launcher."""
from __future__ import annotations
import importlib.util
from pathlib import Path

APP = Path(__file__).resolve().parent


def checkout_launcher(app: Path = APP):
    spec = importlib.util.spec_from_file_location(
        "studio_checkout_launcher", app / "packaging/windows/start_studio.py")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    # A sibling data directory survives source updates and satisfies the
    # existing rule that durable state must not live inside the installation.
    launcher.ROOT = app.parent / (app.name + "-data")
    launcher.APP = app
    launcher.PYTHON = launcher.interpreter_for(app)
    launcher.LAUNCHER = app / "launch_studio.py"
    launcher.CONFIG = launcher.ROOT / "studio-config.json"
    launcher.TEMPLATE = app / "docs/studio/internal-alpha/studio-config.template.json"
    return launcher


def main() -> int:
    launcher = checkout_launcher()
    if not launcher.PYTHON.is_file():
        return launcher.fail("Run Start-Studio.bat to prepare the local Python environment first.")
    try:
        launcher.ROOT.mkdir(parents=True, exist_ok=True)
        for name in ("Stable-diffusion", "VAE", "text_encoder"):
            (APP / "models" / name).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return launcher.fail(f"Could not prepare Studio's folders: {exc}")
    return launcher.main()


if __name__ == "__main__":
    raise SystemExit(main())
