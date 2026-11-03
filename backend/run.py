"""AITP 服务入口。"""

from __future__ import annotations

import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from aitp.app import create_app  # noqa: E402
from aitp.config import load_config  # noqa: E402

config = load_config()
app = create_app(config)


def main() -> None:
    host = app.config["AITP_HOST"]
    port = app.config["AITP_PORT"]
    print(f"AITP listening on http://{host}:{port}", flush=True)

    if app.config.get("AITP_DEBUG"):
        app.run(host=host, port=port, debug=True, threaded=True)
        return

    from waitress import serve

    serve(app, host=host, port=port, threads=8)


if __name__ == "__main__":
    main()
