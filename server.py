#!/usr/bin/env python3
"""Local Trance application server and filesystem-backed app data API."""
import base64, http.client, http.server, ipaddress, io, json, logging, mimetypes, os, re, subprocess, sys, tempfile, threading, urllib.error, urllib.parse, urllib.request, wave
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, 'data')
LIBRARY = os.path.join(DATA, 'library')
EXTENSIONS = os.path.join(ROOT, 'extensions')
IMG = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.avif', '.bmp', '.svg'}
VID = {'.mp4', '.webm', '.mov', '.m4v', '.ogv', '.mkv'}
AUD = {'.mp3', '.wav', '.ogg', '.oga', '.m4a', '.aac', '.flac', '.opus', '.weba'}
SPRITES = {'.png', '.jpg', '.jpeg'}
STATE_FILES = {
    'settings': ('.json', {}),
    'history': ('.json', []),
    'presets': ('.json', {}),
    'deleted': ('.json', {}),
    'memory': ('.txt', ''),
    'memorybooks': ('.json', []),
    'galleryprefs': ('.json', {}),
    'homechat': ('.json', []),
    'chatmemory': ('.json', {'summary': '', 'throughAt': 0}),
    'vectormemory': ('.json', {'model': '', 'entries': []}),
    'fnflyrics': ('.json', {'text': '', 'name': '', 'trackId': ''}),
}
AI_REQUEST_LOCK = threading.Lock()
# Separate lock for Kokoro TTS so it never blocks behind KoboldCpp inference and
# vice versa. The two are independent servers; a single shared lock meant a 30–60 s
# model inference could physically prevent TTS from even opening its connection.
AI_SPEECH_LOCK = threading.Lock()
AI_PROXY_PATHS = {
    '/api/ai/chat', '/api/ai/chat/stream', '/api/ai/speech', '/api/ai/tokens',
    '/api/ai/context', '/api/ai/embeddings'
}
KINDS = {  # kind -> (folder, allowed extensions)
    'media': ('media', IMG | VID),
    'music': ('music', AUD),
    'voice': ('voices', AUD),
    'ono': ('onomatopoeia', AUD),
    'stk': ('stickers', IMG),
    'chr': ('character', IMG),
    'expressions': ('sprites', SPRITES),
}

def atomic_write(path, body):
    """Replace a data file atomically so a crash cannot leave a partial record."""
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=os.path.basename(path) + '.', suffix='.tmp', dir=parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(body)
        os.replace(temp_path, path)
    except Exception:
        try: os.unlink(temp_path)
        except OSError: pass
        raise

def build_video_thumbnail(ident):
    """Create one small JPEG preview with FFmpeg only when the UI requests it."""
    folder = os.path.join(LIBRARY, ident)
    target = os.path.join(folder, 'thumb.bin')
    if os.path.isfile(target): return True
    try:
        with open(os.path.join(folder, 'record.json'), encoding='utf-8') as stream: rec = json.load(stream)
        if not isinstance(rec, dict) or not rec.get('vid'): return False
        source = os.path.join(folder, 'original.bin')
        if not os.path.isfile(source) and rec.get('src') == 'folder' and isinstance(rec.get('filePath'), str):
            candidate = os.path.abspath(os.path.join(ROOT, rec['filePath']))
            if os.path.commonpath((ROOT, candidate)) == ROOT and os.path.isfile(candidate): source = candidate
        if not os.path.isfile(source): return False
        fd, temporary = tempfile.mkstemp(prefix='thumb.', suffix='.jpg', dir=folder)
        os.close(fd)
        try:
            command = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-ss', '0.5', '-i', source,
                       '-frames:v', '1', '-vf', 'scale=440:-2:force_original_aspect_ratio=decrease', '-q:v', '5', temporary]
            completed = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=20, check=False)
            if completed.returncode or not os.path.isfile(temporary) or not os.path.getsize(temporary): return False
            os.replace(temporary, target)
            return True
        finally:
            try: os.unlink(temporary)
            except OSError: pass
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        logging.getLogger('trance.server').warning('Could not create video thumbnail for %s: %s', ident, error)
        return False

def scan():
    out = {}
    for kind, (folder, exts) in KINDS.items():
        items = []
        base = os.path.join(ROOT, folder)
        for dp, _, fns in os.walk(base):
            for fn in sorted(fns):
                if os.path.splitext(fn)[1].lower() not in exts:
                    continue
                full = os.path.join(dp, fn)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                rel = os.path.relpath(full, ROOT).replace(os.sep, '/')
                items.append({'path': rel, 'size': st.st_size, 'mtime': int(st.st_mtime)})
        out[kind] = items
    return out

def extension_manifests():
    """Read extension manifests without executing or trusting their code."""
    manifests = []
    if not os.path.isdir(EXTENSIONS): return manifests
    for folder_name in sorted(os.listdir(EXTENSIONS)):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', folder_name): continue
        manifest_path = os.path.join(EXTENSIONS, folder_name, 'manifest.json')
        try:
            with open(manifest_path, encoding='utf-8') as stream: manifest = json.load(stream)
            if not isinstance(manifest, dict): continue
            ident, entry = manifest.get('id'), manifest.get('entry')
            if not isinstance(ident, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', ident): continue
            if not isinstance(entry, str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,120}\.js', entry): continue
            entry_path = os.path.join(EXTENSIONS, folder_name, entry)
            if not os.path.isfile(entry_path): continue
            manifests.append({
                'id': ident,
                'name': str(manifest.get('name') or ident)[:120],
                'description': str(manifest.get('description') or '')[:500],
                'version': str(manifest.get('version') or '')[:40],
                'defaultEnabled': manifest.get('defaultEnabled') is True,
                'entry': '/extensions/' + urllib.parse.quote(folder_name) + '/' + urllib.parse.quote(entry),
            })
        except (OSError, ValueError, TypeError):
            logging.getLogger('trance.server').warning('Ignoring invalid extension manifest: %s', manifest_path)
    return manifests

def library_purge_plan():
    """Find unavailable records and redundant available records, retaining one usable copy."""
    rows = []
    if not os.path.isdir(LIBRARY): return [], {'records': 0, 'unavailable': 0, 'duplicates': 0, 'files': 0, 'bytes': 0}
    for ident in os.listdir(LIBRARY):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', ident): continue
        folder = os.path.join(LIBRARY, ident)
        try:
            with open(os.path.join(folder, 'record.json'), encoding='utf-8') as stream: rec = json.load(stream)
            if not isinstance(rec, dict): continue
        except (OSError, ValueError): continue
        cached = os.path.isfile(os.path.join(folder, 'original.bin'))
        source = rec.get('filePath', '') if rec.get('src') == 'folder' else ''
        source_path = os.path.abspath(os.path.join(ROOT, source)) if isinstance(source, str) and source else ''
        try: source_available = bool(source_path and os.path.commonpath((ROOT, source_path)) == ROOT and os.path.isfile(source_path))
        except (OSError, ValueError): source_available = False
        rows.append({'id': ident, 'record': rec, 'available': cached or source_available, 'folder': folder})
    unavailable = [r for r in rows if not r['available']]
    available = [r for r in rows if r['available']]
    groups = {}
    for row in available:
        rec = row['record']; key = rec.get('key')
        if key: groups.setdefault(str(rec.get('kind', '')) + '|' + str(rec.get('src', '')) + '|' + str(key), []).append(row)
    duplicates = []
    for group in groups.values():
        if len(group) < 2: continue
        keep = max(group, key=lambda r: (r['record'].get('added', 0) if isinstance(r['record'].get('added', 0), (int, float)) else 0, r['id']))
        duplicates.extend(r for r in group if r is not keep)
    candidates = unavailable + duplicates
    summary = {'records': len(rows), 'unavailable': len(unavailable), 'duplicates': len(duplicates), 'files': 0, 'bytes': 0}
    for row in candidates:
        for dp, dirs, names in os.walk(row['folder'], followlinks=False):
            dirs[:] = [name for name in dirs if not os.path.islink(os.path.join(dp, name))]
            for name in names:
                full = os.path.join(dp, name)
                try:
                    if os.path.islink(full): continue
                    size = os.path.getsize(full); summary['files'] += 1; summary['bytes'] += size
                except OSError: pass
    return candidates, summary

class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      '.webmanifest': 'application/manifest+json', '.js': 'text/javascript', '.svg': 'image/svg+xml'}
    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)
    def send_media_file(self, full, content_type):
        """Stream a cached asset with byte ranges for responsive video seeks."""
        try:
            size = os.path.getsize(full)
        except OSError:
            self.send_error(404); return
        if size == 0:
            self.send_response(200); self.send_header('Content-Type', content_type); self.send_header('Content-Length', '0'); self.send_header('Accept-Ranges', 'bytes'); self.send_header('Cache-Control', 'no-store'); self.end_headers(); return
        start, end = 0, max(0, size - 1)
        range_header = self.headers.get('Range', '')
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header.strip())
        partial = bool(match)
        if match:
            first, last = match.groups()
            if first:
                start = int(first)
                if last: end = min(end, int(last))
            elif last:
                length = int(last)
                start = max(0, size - length)
            if start > end or start >= size:
                self.send_response(416); self.send_header('Content-Range', 'bytes */' + str(size)); self.end_headers(); return
        length = max(0, end - start + 1)
        self.send_response(206 if partial else 200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(length))
        self.send_header('Accept-Ranges', 'bytes')
        if partial: self.send_header('Content-Range', 'bytes %s-%s/%s' % (start, end, size))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            with open(full, 'rb') as stream:
                stream.seek(start)
                remaining = length
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk: break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            logging.getLogger('trance.server').debug('Media client disconnected while streaming %s', full)
    def do_GET(self):
        path = self.path.split('?', 1)[0]
        if path in {'/api/extensions', '/extensions/manifest.json'}:
            body = json.dumps(extension_manifests(), ensure_ascii=False).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json; charset=utf-8'); self.send_header('Content-Length', str(len(body))); self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(body); return
        if path == '/api/health':
            library = {'records': 0, 'cached': 0, 'source_available': 0, 'missing': 0, 'invalid': 0}
            if os.path.isdir(LIBRARY):
                for ident in os.listdir(LIBRARY):
                    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', ident): continue
                    folder = os.path.join(LIBRARY, ident)
                    try:
                        with open(os.path.join(folder, 'record.json'), encoding='utf-8') as stream: rec = json.load(stream)
                    except (OSError, ValueError):
                        library['invalid'] += 1; continue
                    if not isinstance(rec, dict):
                        library['invalid'] += 1; continue
                    library['records'] += 1
                    if os.path.isfile(os.path.join(folder, 'original.bin')):
                        library['cached'] += 1; continue
                    source = rec.get('filePath', '') if rec.get('src') == 'folder' else ''
                    source_path = os.path.abspath(os.path.join(ROOT, source)) if isinstance(source, str) and source else ''
                    try: source_available = bool(source_path and os.path.commonpath((ROOT, source_path)) == ROOT and os.path.isfile(source_path))
                    except (OSError, ValueError): source_available = False
                    if source_available: library['source_available'] += 1
                    else: library['missing'] += 1
            state = {}
            for name, (extension, _) in STATE_FILES.items():
                filename = name + extension
                full = os.path.join(DATA, filename)
                state[filename] = os.path.getsize(full) if os.path.isfile(full) else 0
            recent_log = []
            try:
                with open(os.path.join(DATA, 'trance.log'), encoding='utf-8', errors='replace') as stream:
                    recent_log = [line.rstrip()[-1600:] for line in stream.readlines()[-100:]]
            except OSError: pass
            recent_errors = [line for line in recent_log if re.search(r'\b(ERROR|WARNING|CRITICAL)\b', line)]
            body = json.dumps({'app': 'Trance', 'python': sys.version.split()[0], 'data_dir': DATA,
                               'data_writable': os.path.isdir(DATA) and os.access(DATA, os.W_OK),
                               'library': library, 'state_bytes': state, 'recent_log': recent_log,
                               'recent_errors': recent_errors[-60:]}, ensure_ascii=False).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json; charset=utf-8'); self.send_header('Content-Length', str(len(body))); self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(body); return
        if path == '/api/library/purge':
            _, summary = library_purge_plan()
            body = json.dumps(summary).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json; charset=utf-8'); self.send_header('Content-Length', str(len(body))); self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(body); return
        if path == '/api/list':
            body = json.dumps(scan()).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith('/api/state/'):
            name = path.rsplit('/', 1)[-1]
            state_file = STATE_FILES.get(name)
            if state_file is None:
                self.send_error(404); return
            ext, default = state_file
            full = os.path.join(DATA, name + ext)
            try:
                with open(full, 'rb') as f: body = f.read()
            except FileNotFoundError:
                body = default.encode('utf-8') if isinstance(default, str) else json.dumps(default).encode('utf-8')
            self.send_response(200); self.send_header('Content-Type', 'text/plain; charset=utf-8' if ext == '.txt' else 'application/json; charset=utf-8'); self.send_header('Content-Length', str(len(body))); self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(body); return
        if path == '/api/library':
            # The browser only needs metadata at startup.  Returning one newest
            # record per source avoids restoring stale duplicate cache entries.
            # Original files remain available through their per-record endpoint.
            rows = []
            if os.path.isdir(LIBRARY):
                for ident in os.listdir(LIBRARY):
                    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', ident): continue
                    folder = os.path.join(LIBRARY, ident)
                    try:
                        with open(os.path.join(folder, 'record.json'), encoding='utf-8') as f: rec = json.load(f)
                        has_cached_file = os.path.isfile(os.path.join(folder, 'original.bin'))
                        source = rec.get('filePath', '') if rec.get('src') == 'folder' else ''
                        source_path = os.path.abspath(os.path.join(ROOT, source)) if isinstance(source, str) and source else ''
                        source_is_available = bool(source_path and os.path.commonpath((ROOT, source_path)) == ROOT and os.path.isfile(source_path))
                        if has_cached_file or source_is_available:
                            rec['_cached'] = has_cached_file
                            rec['_thumb'] = os.path.isfile(os.path.join(folder, 'thumb.bin')) or bool(rec.get('vid'))
                            rows.append(rec)
                    except (OSError, ValueError, TypeError): pass
            latest = {}
            for rec in rows:
                ident = str(rec.get('id', ''))
                key = str(rec.get('kind', '')) + '|' + str(rec.get('src', '')) + '|' + str(rec.get('key', ident))
                rank = (rec.get('added', 0) if isinstance(rec.get('added', 0), (int, float)) else 0, ident)
                if key not in latest or rank > latest[key][0]: latest[key] = (rank, rec)
            duplicates = len(rows) - len(latest)
            rows = [item[1] for item in latest.values()]
            body = json.dumps(rows, ensure_ascii=False).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json; charset=utf-8'); self.send_header('Content-Length', str(len(body))); self.send_header('X-Trance-Library-Duplicates', str(duplicates)); self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(body); return
        m = re.fullmatch(r'/api/library/([a-zA-Z0-9_-]{1,80})/(blob|thumb|processed)', path)
        if m:
            ident, kind = m.groups(); name = {'blob':'original.bin','thumb':'thumb.bin','processed':'processed.bin'}[kind]; full = os.path.join(LIBRARY, ident, name)
            if kind == 'thumb' and not os.path.isfile(full) and not build_video_thumbnail(ident):
                self.send_error(404); return
            if kind == 'processed': content_type = 'audio/wav'
            elif kind == 'thumb': content_type = 'image/jpeg'
            else:
                try:
                    with open(os.path.join(LIBRARY, ident, 'record.json'), encoding='utf-8') as f: rec = json.load(f)
                except (OSError, ValueError): rec = {}
                content_type = rec.get('type') if isinstance(rec, dict) else None
                if not isinstance(content_type, str) or '/' not in content_type:
                    content_type = mimetypes.guess_type(rec.get('name', '') if isinstance(rec, dict) else '')[0] or 'application/octet-stream'
            self.send_media_file(full, content_type); return
        super().do_GET()
    def do_POST(self):
        path = self.path.split('?', 1)[0]
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size < 0 or size > 1024 * 1024 * 1024: self.send_error(413); return
            raw = self.rfile.read(size)
            if path in AI_PROXY_PATHS:
                request = json.loads(raw.decode('utf-8'))
                if not isinstance(request, dict):
                    self.send_error(400, 'AI request must be a JSON object')
                    return
                base = str(request.get('base', '')).strip().rstrip('/')
                if base and not re.match(r'^https?://', base, re.IGNORECASE):
                    base = 'http://' + base
                parsed = urllib.parse.urlsplit(base)
                if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
                    self.send_error(400, 'AI service address must start with http:// or https://'); return
                if path in {'/api/ai/chat', '/api/ai/chat/stream'}:
                    route, method, payload = '/v1/chat/completions', 'POST', request.get('payload', {})
                elif path.endswith('/speech'):
                    route, method, payload = '/v1/audio/speech', 'POST', request.get('payload', {})
                elif path.endswith('/embeddings'):
                    route, method, payload = '/v1/embeddings', 'POST', request.get('payload', {})
                elif path.endswith('/tokens'):
                    route, method, payload = '/api/extra/tokencount', 'POST', request.get('payload', {})
                else:
                    route, method, payload = '/api/extra/true_max_context_length', 'GET', None
                target_base = base
                if route.startswith('/api/extra/'):
                    clean_path = re.sub(r'/v1/?$', '', parsed.path.rstrip('/'))
                    target_base = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, clean_path, '', ''))
                elif route.startswith('/v1/') and parsed.path.rstrip('/').endswith('/v1'):
                    route = route[3:]
                upstream_url = target_base + route
                streaming_chat = path == '/api/ai/chat/stream'
                accept = 'text/event-stream' if streaming_chat else (
                    'audio/mpeg, audio/*;q=0.9, */*;q=0.8' if path.endswith('/speech') else
                    'application/json')
                upstream = urllib.request.Request(
                    upstream_url,
                    data=json.dumps(payload).encode('utf-8') if payload is not None else None,
                    headers={'Content-Type': 'application/json', 'Accept': accept},
                    method=method)
                logging.getLogger('trance.server').info('AI proxy received %s request for %s', path, upstream_url)
                # Use a dedicated lock for Kokoro TTS so it never queues behind a
                # long KoboldCpp inference, and KoboldCpp requests never queue behind
                # a Kokoro audio download. The two services are physically separate.
                lock = AI_SPEECH_LOCK if path.endswith('/speech') else AI_REQUEST_LOCK
                with lock:
                    try:
                        host = (parsed.hostname or '').lower()
                        try: loopback = ipaddress.ip_address(host).is_loopback
                        except ValueError: loopback = host == 'localhost' or host.endswith('.localhost')
                        opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if loopback else urllib.request.build_opener()
                        response = opener.open(upstream, timeout=120 if path.endswith('/speech') else 30 if path.endswith('/embeddings') else 15 if path.endswith(('/tokens','/context')) else 180)
                        status = response.status
                        content_type = response.headers.get('Content-Type', 'application/json')
                        if streaming_chat and 200 <= status < 300 and 'text/event-stream' in content_type.lower():
                            self.send_response(status)
                            self.send_header('Content-Type', content_type)
                            self.send_header('Cache-Control', 'no-cache, no-transform')
                            self.send_header('X-Accel-Buffering', 'no')
                            self.send_header('Connection', 'close')
                            self.end_headers()
                            sent = 0
                            try:
                                read_chunk = getattr(response, 'read1', response.read)
                                while True:
                                    chunk = read_chunk(4096)
                                    if not chunk: break
                                    self.wfile.write(chunk)
                                    self.wfile.flush()
                                    sent += len(chunk)
                            except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as error:
                                logging.getLogger('trance.server').exception('KoboldCpp chat stream failed after %s bytes from %s', sent, upstream_url)
                                try:
                                    event = json.dumps({'error': {'message': 'KoboldCpp stream ended unexpectedly: ' + str(error)}}).encode('utf-8')
                                    self.wfile.write(b'data: ' + event + b'\n\n')
                                    self.wfile.flush()
                                except (BrokenPipeError, ConnectionResetError, OSError):
                                    pass
                            finally:
                                response.close()
                            logging.getLogger('trance.server').info('AI proxy streamed %s bytes from %s', sent, upstream_url)
                            self.close_connection = True
                            return
                        try:
                            result = response.read()
                            logging.getLogger('trance.server').info('AI proxy completed %s with HTTP %s, %s, %s bytes', path, status, content_type, len(result))
                        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as e:
                            if path.endswith('/speech'):
                                logging.getLogger('trance.server').exception('Kokoro audio stream failed after HTTP %s from %s', status, upstream_url)
                                result = json.dumps({'error': 'Kokoro returned HTTP ' + str(status) + ' but reset the connection while sending audio. Generation began and then the Kokoro server closed the stream: ' + str(e)}).encode()
                                content_type = 'application/json; charset=utf-8'; status = 502
                            else:
                                raise
                    except urllib.error.HTTPError as e:
                        result = e.read() or str(e).encode(); content_type = e.headers.get('Content-Type', 'text/plain'); status = e.code
                        logging.getLogger('trance.server').error('AI proxy %s got HTTP %s from %s: %s', path, status, upstream_url, result[:1000].decode('utf-8', 'replace'))
                    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as e:
                        reason = getattr(e, 'reason', e)
                        endpoint = upstream_url
                        service_name = 'Kokoro' if path.endswith('/speech') else 'KoboldCpp'
                        logging.getLogger('trance.server').exception('%s proxy could not reach %s', service_name, endpoint)
                        if isinstance(reason, ConnectionResetError):
                            message = 'Connection to ' + endpoint + ' was reset by the service. Check the ' + service_name + ' server log and verify that this is its HTTP API address and port; this is an upstream connection failure, not a browser CORS error.'
                        elif isinstance(reason, ConnectionRefusedError):
                            message = 'No service accepted the connection at ' + endpoint + '. Start the configured AI service and check its HTTP API port.'
                        else:
                            message = 'Could not reach AI service at ' + endpoint + ': ' + str(reason)
                        result = json.dumps({'error': message}).encode(); content_type = 'application/json; charset=utf-8'; status = 502
                if path.endswith('/speech') and request.get('response_encoding') == 'base64' and 200 <= status < 300:
                    audio_type = content_type
                    if 'wav' in audio_type.lower():
                        try:
                            with wave.open(io.BytesIO(result), 'rb') as wav_file:
                                logging.getLogger('trance.server').info('Kokoro WAV validated: channels=%s rate=%s width=%s frames=%s', wav_file.getnchannels(), wav_file.getframerate(), wav_file.getsampwidth(), wav_file.getnframes())
                        except (wave.Error, EOFError) as e:
                            logging.getLogger('trance.server').error('Kokoro labeled audio/wav returned invalid WAV data: %s; first 16 bytes=%s', e, result[:16].hex())
                    result = json.dumps({'audio_base64': base64.b64encode(result).decode('ascii'), 'content_type': audio_type}).encode('utf-8')
                    content_type = 'application/json; charset=utf-8'
                self.send_response(status); self.send_header('Content-Type', content_type); self.send_header('Content-Length', str(len(result))); self.send_header('Cache-Control', 'no-store'); self.send_header('Connection', 'close'); self.end_headers()
                try:
                    self.wfile.write(result); self.wfile.flush()
                    logging.getLogger('trance.server').info('AI proxy sent %s response body: %s bytes, HTTP %s', path, len(result), status)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    logging.getLogger('trance.server').exception('Browser disconnected while receiving %s response body (%s bytes)', path, len(result))
                self.close_connection = True
                return
            if path.startswith('/api/state/'):
                name = path.rsplit('/', 1)[-1]
                state_file = STATE_FILES.get(name)
                if state_file is None: self.send_error(404); return
                ext, _ = state_file
                if ext == '.json': json.loads(raw.decode('utf-8'))
                os.makedirs(DATA, exist_ok=True)
                atomic_write(os.path.join(DATA, name + ext), raw)
                self.send_response(204); self.end_headers(); return
            if path == '/api/library':
                rec = json.loads(raw.decode('utf-8'))
                if not isinstance(rec, dict): self.send_error(400, 'Library record must be a JSON object'); return
                ident = rec.get('id', '')
                if not isinstance(ident, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', ident): self.send_error(400); return
                folder = os.path.join(LIBRARY, ident); os.makedirs(folder, exist_ok=True)
                files = rec.pop('_files', {})
                if not isinstance(files, dict): self.send_error(400, 'Library files must be an object'); return
                for key, filename in [('blob','original.bin'),('thumb','thumb.bin'),('processed','processed.bin')]:
                    if key in files:
                        atomic_write(os.path.join(folder, filename), base64.b64decode(files[key], validate=True))
                for k in ('blob','thumb','proc'):
                    if k in rec:
                        if k == 'proc' and isinstance(rec[k], dict): rec[k] = {a:b for a,b in rec[k].items() if a != 'blob'}
                        else: rec.pop(k, None)
                record_bytes = json.dumps(rec, ensure_ascii=False).encode('utf-8')
                atomic_write(os.path.join(folder, 'record.json'), record_bytes)
                self.send_response(204); self.end_headers(); return
            if path == '/api/library/purge':
                request = json.loads(raw.decode('utf-8'))
                if not isinstance(request, dict) or request.get('confirm') is not True:
                    self.send_error(400, 'Explicit purge confirmation is required'); return
                import shutil
                candidates, before = library_purge_plan(); removed = 0; errors = []
                for row in candidates:
                    try:
                        shutil.rmtree(row['folder'])
                        removed += 1
                    except FileNotFoundError: removed += 1
                    except OSError as error: errors.append(row['id'] + ': ' + str(error))
                _, after = library_purge_plan()
                result = {'removed_records': removed, 'before': before, 'remaining': after, 'errors': errors}
                body = json.dumps(result).encode()
                self.send_response(200 if not errors else 207); self.send_header('Content-Type', 'application/json; charset=utf-8'); self.send_header('Content-Length', str(len(body))); self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(body); return
            self.send_error(404)
        except (ValueError, TypeError, base64.binascii.Error) as e:
            logging.getLogger('trance.server').warning('Rejected invalid request for %s: %s', self.path.split('?', 1)[0], e)
            self.send_error(400, str(e))
        except OSError as e:
            logging.getLogger('trance.server').exception('Could not persist request to %s', self.path.split('?', 1)[0])
            self.send_error(500, 'Could not persist Trance data: ' + str(e))
    def do_DELETE(self):
        m = re.fullmatch(r'/api/library/([a-zA-Z0-9_-]{1,80})', self.path.split('?', 1)[0])
        if not m: self.send_error(404); return
        import shutil
        shutil.rmtree(os.path.join(LIBRARY, m.group(1)), ignore_errors=True)
        self.send_response(204); self.end_headers()
    def log_message(self, *a):
        pass
    def log_error(self, fmt, *args):
        logging.getLogger('trance.server').warning(fmt, *args)

class TranceHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    def handle_error(self, request, client_address):
        logging.getLogger('trance.server').exception('Unhandled HTTP request error from %s', client_address[0])

if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    srv = TranceHTTPServer(('127.0.0.1', port), Handler)
    print('Trance running at http://127.0.0.1:%d/  (Ctrl+C to stop)' % port, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
