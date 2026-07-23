"""Desktop entry: start FastAPI and open a native window."""

from __future__ import annotations

import socket
import threading
import time
import webbrowser


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def run_server(port: int = 8787) -> None:
    import uvicorn
    from openpvscope.api.app import app

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


def main() -> None:
    port = 8787
    url = f"http://127.0.0.1:{port}"

    thread = threading.Thread(target=run_server, kwargs={"port": port}, daemon=True)
    thread.start()
    time.sleep(0.8)

    try:
        import webview

        webview.create_window("OpenPVScope", url, width=1440, height=900)
        webview.start()
    except Exception:
        webbrowser.open(url)
        print(f"OpenPVScope API running at {url}")
        thread.join()


if __name__ == "__main__":
    main()
