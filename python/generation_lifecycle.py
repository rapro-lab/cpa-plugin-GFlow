"""任务生命周期：超时、单项取消和浏览器回收，不自动重发生成。"""
import asyncio
import json
import secrets
import re
import os
import threading
import sys


async def close_browsers():
    name = 'src.services.browser_captcha_personal'
    if name in sys.modules:
        service = sys.modules[name].BrowserCaptchaService
        try:
            await asyncio.wait_for(service.reset_shared_instances(), timeout=12)
        except (Exception, asyncio.CancelledError):
            pass


class TaskPipe:
    """请求专属 Windows 命名管道；取消不经过正在生成的 HTTP 处理器。"""
    def __init__(self, request_id, loop, task, active):
        self.handle = None
        self.closed = False
        if os.name != 'nt': return
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p]
        self.kernel.CreateNamedPipeW.restype = wintypes.HANDLE
        self.kernel.ConnectNamedPipe.argtypes = [wintypes.HANDLE,ctypes.c_void_p]
        self.kernel.ReadFile.argtypes = [wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD),ctypes.c_void_p]
        self.kernel.WriteFile.argtypes = self.kernel.ReadFile.argtypes
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
        self.kernel.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        self.handle = self.kernel.CreateNamedPipeW('\\\\.\\pipe\\gflow-cancel-'+request_id,3,0x8,1,512,512,0,None)
        if self.handle in (None,ctypes.c_void_p(-1).value):
            self.handle=None
            return
        def serve():
            try:
                connected = self.kernel.ConnectNamedPipe(self.handle,None) or ctypes.get_last_error()==535
                if not connected or self.closed: return
                buffer=ctypes.create_string_buffer(64)
                count=wintypes.DWORD()
                if not self.kernel.ReadFile(self.handle,buffer,64,ctypes.byref(count),None): return
                if buffer.raw[:count.value].strip()!=b'cancel': return
                # 先确认，随后取消本项任务；其他任务和用户浏览器不受影响。
                reply=b'cancelled\n'
                self.kernel.WriteFile(self.handle,reply,len(reply),ctypes.byref(count),None)
                loop.call_soon_threadsafe(task.cancel)
                # 某些浏览器启动调用会短暂阻塞事件循环。仅回收本 worker
                # 创建、且带插件专用 profile 的 Chrome，以便让取消立即生效。
                if not active.is_set(): return
                import psutil
                profile = os.environ.get('PERSONAL_BROWSER_USER_DATA_DIR', '').strip()
                profile_root = str((__import__('pathlib').Path(profile) if profile else __import__('pathlib').Path(__file__).parent/'flow2api/src/tmp').resolve()).lower()
                for process in psutil.Process(os.getpid()).children(recursive=True):
                    try:
                        if process.name().lower() not in ('chrome.exe','msedge.exe','chromium.exe'): continue
                        if not any('user-data-dir=' in arg and profile_root in arg.lower() for arg in process.cmdline()): continue
                        children=process.children(recursive=True)
                        for child in reversed(children):
                            try: child.terminate()
                            except psutil.NoSuchProcess: pass
                        process.terminate()
                    except psutil.Error: pass
            finally:
                self.close()
        threading.Thread(target=serve,daemon=True).start()

    def close(self):
        if self.closed: return
        self.closed=True
        if self.handle:
            self.kernel.CancelIoEx(self.handle, None)
            self.kernel.CloseHandle(self.handle)
            self.handle=None


class GenerationLifecycle:
    def __init__(self, service):
        self.service = service
        self.tasks = set()
        self.lock = asyncio.Lock()
        self.server = None
        self.controls = {}

    async def start(self):
        # 临时控制端口仅绑定本机；每次请求使用不可猜测的能力令牌。
        self.server = await asyncio.start_server(self.handle_control, '127.0.0.1', 0, limit=8192)

    async def handle_control(self, reader, writer):
        try:
            raw = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), timeout=3)
            head = raw.decode('ascii', errors='replace').split('\r\n')
            headers = dict(line.split(':', 1) for line in head[1:] if ':' in line)
            token = next((v.strip().removeprefix('Bearer ') for k, v in headers.items() if k.lower() == 'authorization'), '')
            task = self.controls.get(token)
            accepted = head[0] == 'POST /cancel HTTP/1.1' and task is not None and not task.done()
            body = json.dumps({'cancelled': accepted}).encode()
            status = b'200 OK' if accepted else b'404 Not Found'
            writer.write(b'HTTP/1.1 ' + status + b'\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(body)).encode() + b'\r\nConnection: close\r\n\r\n' + body)
            await writer.drain()
            if accepted:
                task.cancel()
        except (Exception, asyncio.CancelledError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    def submit(self, req_id, params):
        task = asyncio.create_task(self.run(req_id, params))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def monitor_client(self, pid, task):
        if not isinstance(pid, int) or pid <= 0:
            return
        try:
            import psutil
            client = psutil.Process(pid)
            created = client.create_time()
            while not task.done():
                await asyncio.sleep(1)
                if not client.is_running() or client.create_time() != created:
                    task.cancel()
                    return
        except Exception:
            task.cancel()

    def fail(self, req_id, stream, code, message):
        # Every failure must reach the native executor error channel, including
        # cancellation/timeouts after streaming has already begun.
        self.service.send_line({
            'id': req_id, 'done': True,
            'error': {'code': code, 'message': message,
                      'status_code': 408 if code == 'generation_timeout' else 499},
        })

    async def run(self, req_id, params):
        token = secrets.token_urlsafe(32)
        task = asyncio.current_task()
        self.controls[token] = task
        stream = bool(params.get('stream'))
        monitor = asyncio.create_task(self.monitor_client(params.get('client_process_id'), task))
        control_file = None
        pipe_control = None
        active = threading.Event()
        try:
            port = self.server.sockets[0].getsockname()[1]
            control = {'url': f'http://127.0.0.1:{port}/cancel', 'token': token}
            client_id = params.get('client_request_id', '')
            if re.fullmatch(r'[a-f0-9-]{36}-[0-3]', client_id):
                pipe_control = TaskPipe(client_id, asyncio.get_running_loop(), task, active)
                control_file = self.service.tmp_dir / ('control-' + client_id + '.json')
                control_file.write_text(json.dumps(control), encoding='utf-8')
            if stream:
                port = self.server.sockets[0].getsockname()[1]
                control = {'url': f'http://127.0.0.1:{port}/cancel', 'token': token}
                self.service.send_line({'id': req_id, 'stream': True, 'chunk': 'data: ' + json.dumps({'gflow_control': control, 'choices': [{'delta': {'content': ''}}]}) + '\n\n'})
            # 不让并发任务互相关闭浏览器。等待锁也能取消。
            async with self.lock:
                active.set()
                try:
                    model = params.get('model', '')
                    seconds = 360 if ('banana' in model or 'image' in model) else 1560
                    async with asyncio.timeout(seconds):
                        await self.service.handle_chat_completion(req_id, params)
                finally:
                    try:
                        await close_browsers()
                    finally:
                        active.clear()
        except asyncio.TimeoutError:
            self.fail(req_id, stream, 'generation_timeout', "GFlow generation timed out and browser retries stopped; check the Google Flow login, network and captcha state")
        except asyncio.CancelledError:
            self.fail(req_id, stream, 'cancelled', "GFlow stopped the generation task and released the dedicated browser; any submitted cloud cost is determined by the platform")
        except Exception as error:
            self.fail(req_id, stream, 'generation_error', str(error)[:400])
        finally:
            if pipe_control: pipe_control.close()
            self.controls.pop(token, None)
            if control_file:
                control_file.unlink(missing_ok=True)
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)

    async def shutdown(self):
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)
        await close_browsers()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
