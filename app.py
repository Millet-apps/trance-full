#!/usr/bin/env python3
"""Trance desktop application. The UI runs in an isolated, non-persistent WebEngine profile."""
import os
import sys
import threading
import logging
from logging.handlers import RotatingFileHandler

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
os.makedirs(os.path.join(ROOT, 'data'), exist_ok=True)
for folder in ('media', 'music', 'voices', 'onomatopoeia', 'stickers', 'character'):
    os.makedirs(os.path.join(ROOT, folder), exist_ok=True)

log = logging.getLogger('trance')
log.setLevel(logging.INFO)
log.propagate = False
if not log.handlers:
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')
    file_log = RotatingFileHandler(os.path.join(ROOT, 'data', 'trance.log'), maxBytes=2_000_000, backupCount=3, encoding='utf-8')
    file_log.setFormatter(formatter)
    log.addHandler(file_log)
    console_log = logging.StreamHandler()
    console_log.setFormatter(formatter)
    log.addHandler(console_log)

def log_uncaught(exc_type, exc, tb):
    log.critical('Uncaught desktop application error', exc_info=(exc_type, exc, tb))

sys.excepthook = log_uncaught

def log_thread_error(args):
    log.critical('Uncaught error in thread %s', args.thread.name if args.thread else 'unknown',
                 exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

threading.excepthook = log_thread_error

from server import Handler, TranceHTTPServer

server = TranceHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, name='TranceLocalDataServer', daemon=True).start()

from PySide6.QtCore import QUrl, Qt, QObject, Slot
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QMainWindow, QFileDialog
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel

class TrancePage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, line, source):
        severity = getattr(level, 'name', str(level)).lower()
        logger = log.error if 'error' in severity else log.warning if 'warning' in severity else log.info
        logger('JavaScript console (%s) %s:%s: %s', level, source, line, message)

app = QApplication(sys.argv)
app.setApplicationName('Trance')
app.setOrganizationName('Trance')

window = QMainWindow()
window.setWindowTitle('Trance')
window.setWindowIcon(QIcon(os.path.join(ROOT, 'data', 'icon-512.png')))
window.resize(1440, 920)
fullscreen = {'active': False, 'geometry': None, 'maximized': False}

def set_native_fullscreen(enabled):
    enabled = bool(enabled)
    if enabled == fullscreen['active']:
        return
    if enabled:
        fullscreen['geometry'] = window.geometry()
        fullscreen['maximized'] = window.isMaximized()
        page.runJavaScript('window.tranceNativeFullscreen = true')
        window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        window.showFullScreen()
        fullscreen['active'] = True
    else:
        page.runJavaScript('window.tranceNativeFullscreen = false')
        window.showNormal()
        window.setWindowFlag(Qt.WindowType.FramelessWindowHint, False)
        if fullscreen['geometry'] is not None:
            window.setGeometry(fullscreen['geometry'])
        if fullscreen['maximized']:
            window.showMaximized()
        else:
            window.showNormal()
        fullscreen['active'] = False

def handle_web_fullscreen(request):
    request.accept()
    state = request.toggleOn
    set_native_fullscreen(state() if callable(state) else state)

fullscreen_shortcut = QShortcut(QKeySequence('F11'), window)
fullscreen_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
view = QWebEngineView(window)
profile = QWebEngineProfile(window)  # unnamed profiles are off-the-record
profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
page = TrancePage(profile, view)
page.fullScreenRequested.connect(handle_web_fullscreen)
def toggle_app_fullscreen():
    if fullscreen['active']:
        page.runJavaScript('if (document.fullscreenElement) document.exitFullscreen()')
        set_native_fullscreen(False)
    else:
        set_native_fullscreen(True)

fullscreen_shortcut.activated.connect(toggle_app_fullscreen)
class TranceBridge(QObject):
    @Slot()
    def toggleFullscreen(self):
        toggle_app_fullscreen()

    @Slot()
    def exitApp(self):
        app.quit()

    @Slot()
    def reloadApp(self):
        page.triggerAction(QWebEnginePage.WebAction.ReloadAndBypassCache)

view.setPage(page)
web_channel = QWebChannel(page)
web_channel.registerObject('trance', TranceBridge(page))
page.setWebChannel(web_channel)
def save_download(request):
    suggested = request.suggestedFileName() or 'trance-settings.txt'
    path, _ = QFileDialog.getSaveFileName(window, 'Export Trance settings', suggested, 'Text files (*.txt);;JSON files (*.json);;All files (*)')
    if not path:
        request.cancel()
        return
    request.setDownloadDirectory(os.path.dirname(path) or ROOT)
    request.setDownloadFileName(os.path.basename(path))
    request.accept()
profile.downloadRequested.connect(save_download)
settings = page.settings()
for attr in ('LocalStorageEnabled', 'ServiceWorkersEnabled', 'LocalContentCanAccessFileUrls', 'LocalContentCanAccessRemoteUrls'):
    enum = getattr(QWebEngineSettings.WebAttribute, attr, None)
    if enum is not None:
        settings.setAttribute(enum, False)
settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
window.setCentralWidget(view)
window.show()
view.load(QUrl(f'http://127.0.0.1:{server.server_port}/'))
log.info('Trance started; local data server listening on 127.0.0.1:%s', server.server_port)
def stop_server():
    server.shutdown()
    server.server_close()

app.aboutToQuit.connect(stop_server)
sys.exit(app.exec())
