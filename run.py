import threading
import webbrowser

import uvicorn

from botpricer.app import create_app


if __name__ == '__main__':
    threading.Timer(1.5, lambda: webbrowser.open('http://127.0.0.1:8000')).start()
    uvicorn.run(create_app(), host='127.0.0.1', port=8000, workers=1, access_log=False)
