"""
LAU Blackboard Sync - Core Engine (Production Version)
Secure, transparent Microsoft Edge / Google Chrome / Brave integration.
- Launches the student's real installed browser through the OS using subprocess.Popen.
- Connects over Chrome DevTools Protocol (CDP) without automation flags.
- Zero credentials stored or read. All login is handled directly by the user on LAU SSO.
- Session tokens are encrypted at rest using Windows DPAPI (CryptProtectData).
- Extraction runs through direct Playwright APIRequestContext using DPAPI-secured cookies.
"""

import os
import sys
import time
import json
import re
import shutil
import queue
import subprocess
import threading
import winreg
import urllib.request
import urllib.error
import ctypes
from ctypes import wintypes
from datetime import datetime
from playwright.sync_api import sync_playwright

LAU_ELEARN_URL = "https://elearn.lau.edu.lb/ultra/course"
LAU_LOGIN_PORTAL = "https://elearn.lau.edu.lb"
LAU_API_ME_URL = "https://elearn.lau.edu.lb/learn/api/public/v1/users/me"

# Standard dedicated app directory
APP_DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "LectureFetch")
BROWSER_PROFILE_DIR = os.path.join(APP_DATA_DIR, "browser-profile")
STORAGE_STATE_PATH = os.path.join(APP_DATA_DIR, "storage_state.json")
MANIFEST_PATH = os.path.join(APP_DATA_DIR, "sync_manifest.json")

def get_default_download_dir() -> str:
    """Returns Desktop\\Lectures by default, or Desktop\\Extracted Lectures if Lectures already exists."""
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    lectures_dir = os.path.join(desktop, "Lectures")
    if os.path.exists(lectures_dir):
        return os.path.join(desktop, "Extracted Lectures")
    return lectures_dir

DEFAULT_DOWNLOAD_DIR = get_default_download_dir()

# Legacy migration paths
LEGACY_PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".blackboard_edge_profile")

# --- Windows DPAPI Helpers (ctypes) ---

class DATA_BLOB(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_byte))]

crypt32 = ctypes.windll.crypt32
kernel32 = ctypes.windll.kernel32

def dpapi_protect(data: bytes) -> bytes:
    """Encrypts byte sequence using current Windows user credentials via DPAPI."""
    in_blob = DATA_BLOB(len(data), ctypes.cast(ctypes.c_char_p(data), ctypes.POINTER(ctypes.c_byte)))
    out_blob = DATA_BLOB()
    if not crypt32.CryptProtectData(ctypes.byref(in_blob), "LectureFetch Session", None, None, None, 0, ctypes.byref(out_blob)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)

def dpapi_unprotect(data: bytes) -> bytes:
    """Decrypts DPAPI-encrypted byte sequence for the current Windows user."""
    in_blob = DATA_BLOB(len(data), ctypes.cast(ctypes.c_char_p(data), ctypes.POINTER(ctypes.c_byte)))
    out_blob = DATA_BLOB()
    if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)

def save_secure_storage_state(file_path: str, state_dict: dict):
    """Encrypts and atomically writes storage state to disk with DPAPI."""
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    raw_bytes = json.dumps(state_dict, indent=2, ensure_ascii=False).encode("utf-8")
    enc_bytes = dpapi_protect(raw_bytes)
    temp_path = file_path + ".tmp"
    with open(temp_path, "wb") as f:
        f.write(enc_bytes)
    if os.path.exists(file_path):
        os.remove(file_path)
    os.rename(temp_path, file_path)

def load_secure_storage_state(file_path: str) -> dict | None:
    """Loads and decrypts storage state from disk, with transparent migration from legacy plaintext."""
    if not os.path.exists(file_path):
        return None
    try:
        with open(file_path, "rb") as f:
            data = f.read()
        if not data:
            return None
        stripped = data.strip()
        # Transparent migration for legacy plaintext JSON
        if stripped.startswith(b"{"):
            try:
                state_dict = json.loads(data.decode("utf-8"))
                save_secure_storage_state(file_path, state_dict)
                return state_dict
            except Exception:
                pass
        dec_bytes = dpapi_unprotect(data)
        return json.loads(dec_bytes.decode("utf-8"))
    except Exception as e:
        print(f"[SECURITY] Could not read secure storage state: {e}")
        return None

# --- Browser Detection & Selection ---

def get_default_browser_progid() -> str | None:
    """Reads the default Windows browser ProgId from HKCU UserChoice."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice") as key:
            val, _ = winreg.QueryValueEx(key, "ProgId")
            return str(val)
    except Exception:
        return None

def find_browser_exe(browser_name: str) -> str | None:
    """Finds path for a specific browser executable via App Paths registry keys and common directories."""
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        key_path = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{browser_name}"
        try:
            with winreg.OpenKey(root, key_path) as key:
                val, _ = winreg.QueryValueEx(key, "")
                if val and os.path.exists(val):
                    return str(val)
        except Exception:
            pass

    prog_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    prog_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local_appdata = os.environ.get("LOCALAPPDATA", "")

    fallbacks = {
        "msedge.exe": [
            os.path.join(prog_files_x86, r"Microsoft\Edge\Application\msedge.exe"),
            os.path.join(prog_files, r"Microsoft\Edge\Application\msedge.exe"),
            os.path.join(local_appdata, r"Microsoft\Edge\Application\msedge.exe"),
        ],
        "chrome.exe": [
            os.path.join(prog_files, r"Google\Chrome\Application\chrome.exe"),
            os.path.join(prog_files_x86, r"Google\Chrome\Application\chrome.exe"),
            os.path.join(local_appdata, r"Google\Chrome\Application\chrome.exe"),
        ],
        "brave.exe": [
            os.path.join(prog_files, r"BraveSoftware\Brave-Browser\Application\brave.exe"),
            os.path.join(prog_files_x86, r"BraveSoftware\Brave-Browser\Application\brave.exe"),
            os.path.join(local_appdata, r"BraveSoftware\Brave-Browser\Application\brave.exe"),
        ]
    }

    for path in fallbacks.get(browser_name, []):
        if path and os.path.exists(path):
            return path
    return None

def get_ordered_browser_candidates() -> list[tuple[str, str]]:
    """
    Returns an ordered list of installed supported browsers to try:
    1. Default browser (if Edge, Chrome, or Brave).
    2. Edge (system-native on Windows).
    3. Chrome.
    4. Brave.
    Duplicates are filtered out while preserving preference order.
    """
    candidates = []
    seen = set()

    # 1. Default browser
    prog_id = get_default_browser_progid()
    mapping = {
        "MSEdgeHTM": ("msedge.exe", "Microsoft Edge"),
        "ChromeHTML": ("chrome.exe", "Google Chrome"),
        "BraveHTML": ("brave.exe", "Brave"),
    }
    if prog_id and prog_id in mapping:
        exe_name, friendly_name = mapping[prog_id]
        path = find_browser_exe(exe_name)
        if path and path.lower() not in seen:
            candidates.append((path, friendly_name))
            seen.add(path.lower())

    # 2. General fallback cascade
    standard_order = [
        ("msedge.exe", "Microsoft Edge"),
        ("chrome.exe", "Google Chrome"),
        ("brave.exe", "Brave"),
    ]
    for exe_name, friendly_name in standard_order:
        path = find_browser_exe(exe_name)
        if path and path.lower() not in seen:
            candidates.append((path, friendly_name))
            seen.add(path.lower())

    return candidates

def detect_supported_browser() -> tuple[str | None, str | None]:
    candidates = get_ordered_browser_candidates()
    return candidates[0] if candidates else (None, None)

def sanitize_filename(filename):
    """Clean filename of invalid Windows path characters."""
    clean = re.sub(r'[\\/*?:"<>|]', "_", filename)
    clean = clean.strip(". ")
    return clean or "document"


class BlackboardWorkerThread(threading.Thread):
    """
    Dedicated worker thread that exclusively owns and manages Playwright.
    Executes all browser actions sequentially via a thread-safe task queue.
    """
    def __init__(self, app_data_dir=APP_DATA_DIR):
        super().__init__(name="BlackboardPlaywrightWorker", daemon=True)
        self.app_data_dir = app_data_dir
        self.browser_profile_dir = os.path.join(self.app_data_dir, "browser-profile")
        self.storage_state_path = os.path.join(self.app_data_dir, "storage_state.json")
        self.manifest_path = os.path.join(self.app_data_dir, "sync_manifest.json")
        self.port_file = os.path.join(self.browser_profile_dir, "DevToolsActivePort")

        os.makedirs(self.app_data_dir, exist_ok=True)
        os.makedirs(self.browser_profile_dir, exist_ok=True)

        self.task_queue = queue.Queue()
        self.playwright = None
        self.is_running = True
        self.cancel_requested = False
        self.active_browser_proc = None

        self._migrate_legacy_files()

    def _migrate_legacy_files(self):
        """Migrate existing manifest or storage_state from previous locations if present."""
        legacy_dirs = [
            os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "LAU Blackboard Sync"),
            LEGACY_PROFILE_DIR
        ]
        for l_dir in legacy_dirs:
            if not os.path.exists(self.manifest_path):
                l_manifest = os.path.join(l_dir, "sync_manifest.json")
                if os.path.exists(l_manifest):
                    try:
                        shutil.copy2(l_manifest, self.manifest_path)
                    except Exception:
                        pass
            if not os.path.exists(self.storage_state_path):
                l_storage = os.path.join(l_dir, "storage_state.json")
                if os.path.exists(l_storage):
                    try:
                        load_secure_storage_state(l_storage)
                        shutil.copy2(l_storage, self.storage_state_path)
                    except Exception:
                        pass

    def run(self):
        try:
            self.playwright = sync_playwright().start()
        except Exception as e:
            print(f"[WORKER CRITICAL] Failed to initialize Playwright: {e}")

        while self.is_running:
            try:
                task = self.task_queue.get()
                if task is None:
                    break
                action, args, kwargs, reply_queue = task
                fn = getattr(self, f"_do_{action}", None)
                if fn is None:
                    reply_queue.put((False, ValueError(f"Unknown action: {action}")))
                else:
                    try:
                        res = fn(*args, **kwargs)
                        reply_queue.put((True, res))
                    except Exception as err:
                        reply_queue.put((False, err))
                self.task_queue.task_done()
            except Exception as loop_err:
                print(f"[WORKER LOOP ERROR] {loop_err}")

        self._shutdown()

    def _shutdown(self):
        if self.playwright:
            try:
                self.playwright.stop()
            except Exception:
                pass
            self.playwright = None

    def call(self, action, *args, **kwargs):
        """Dispatches an action to the worker thread and waits synchronously for result."""
        if not self.is_alive():
            raise RuntimeError("Blackboard worker thread is not running.")
        reply_queue = queue.Queue()
        self.task_queue.put((action, args, kwargs, reply_queue))
        success, res = reply_queue.get()
        if not success:
            raise res
        return res

    def stop(self):
        self.is_running = False
        self.task_queue.put(None)
        self.join(timeout=5)

    def cancel_login(self):
        self.cancel_requested = True
        if self.active_browser_proc and self.active_browser_proc.poll() is None:
            try:
                self.active_browser_proc.terminate()
            except Exception:
                pass

    def _load_manifest(self):
        if os.path.exists(self.manifest_path):
            try:
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"version": 1, "is_connected": False, "downloaded_files": {}, "courses": []}

    def _save_manifest(self, manifest):
        try:
            with open(self.manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _get_active_port(self) -> int | None:
        """Verifies if DevToolsActivePort exists and responds to HTTP requests."""
        if not os.path.exists(self.port_file):
            return None
        try:
            with open(self.port_file, "r") as f:
                p_line = f.readline().strip()
                if p_line:
                    port = int(p_line)
                    req = urllib.request.Request(f"http://127.0.0.1:{port}/json/version")
                    with urllib.request.urlopen(req, timeout=1.5) as resp:
                        if resp.status == 200:
                            return port
        except Exception:
            pass
        return None

    def _launch_and_attach_browser(self, browser_path: str, friendly_name: str, status_fn=None, log_fn=print):
        """
        Launches the real browser via subprocess.Popen with a dedicated profile.
        Attaches over CDP (connect_over_cdp).
        Never passes automation flags.
        Returns (browser_obj, context_obj).
        """
        self.cancel_requested = False
        active_port = self._get_active_port()

        if active_port:
            log_fn(f"[BROWSER] Re-attaching to existing active {friendly_name} session (Port {active_port})...")
            port = active_port
        else:
            if os.path.exists(self.port_file):
                try:
                    os.remove(self.port_file)
                except Exception:
                    pass

            cmd = [
                browser_path,
                f"--user-data-dir={self.browser_profile_dir}",
                "--remote-debugging-port=0",
                "--no-first-run",
                "--no-default-browser-check",
                LAU_LOGIN_PORTAL
            ]

            if status_fn:
                status_fn("Opening your browser...")
            log_fn(f"[BROWSER] Opening official LAU portal in {friendly_name}...")

            # Visible window for the user, DEVNULL for std streams
            self.active_browser_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            # Poll for DevToolsActivePort up to 15 seconds
            port = None
            start_t = time.time()
            while time.time() - start_t < 15:
                if self.cancel_requested:
                    raise RuntimeError("Login cancelled by user.")
                if os.path.exists(self.port_file):
                    try:
                        with open(self.port_file, "r") as f:
                            lines = f.readlines()
                            if lines:
                                port = int(lines[0].strip())
                                break
                    except Exception:
                        pass
                time.sleep(0.3)

            if not port:
                raise RuntimeError("Could not establish browser debugging connection (port file timed out).")

        # Connect over CDP
        browser = self.playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        return browser, context

    def _close_browser_cdp(self, browser):
        """Cleanly closes the physical browser window using CDP Browser.close."""
        try:
            cdp = browser.new_browser_cdp_session()
            cdp.send("Browser.close")
        except Exception:
            pass
        try:
            browser.close()
        except Exception:
            pass
        self.active_browser_proc = None

    def _create_authenticated_request_context(self):
        """
        Creates a headless Playwright APIRequestContext using DPAPI-decrypted cookies.
        Requires 0 browser windows and 0 browser binaries.
        """
        state = load_secure_storage_state(self.storage_state_path)
        if not state:
            return self.playwright.request.new_context()

        # Write to temporary file for Playwright's storage_state parameter
        temp_state = os.path.join(self.app_data_dir, "_temp_state.json")
        try:
            with open(temp_state, "w", encoding="utf-8") as f:
                json.dump(state, f)
            req_ctx = self.playwright.request.new_context(storage_state=temp_state)
            return req_ctx
        finally:
            if os.path.exists(temp_state):
                try:
                    os.remove(temp_state)
                except Exception:
                    pass

    def _do_is_logged_in(self):
        """Check whether account is registered as connected and session is live."""
        manifest = self._load_manifest()
        if not manifest.get("is_connected", False):
            return False

        # Verify live API access with DPAPI cookies
        try:
            req_ctx = self._create_authenticated_request_context()
            resp = req_ctx.get(LAU_API_ME_URL, timeout=4000)
            is_ok = (resp.status == 200)
            req_ctx.dispose()
            return is_ok
        except Exception:
            return True # Fallback to cached connected state if offline

    def _do_extract_courses(self, req_ctx=None, log_fn=print):
        """Fetch courses using the authenticated user's ID via memberships API."""
        dispose_after = False
        if req_ctx is None:
            req_ctx = self._create_authenticated_request_context()
            dispose_after = True

        courses = []
        try:
            resp_me = req_ctx.get(LAU_API_ME_URL, timeout=10000)
            if not resp_me.ok:
                return []
            user_id = resp_me.json().get("id")
            if not user_id:
                return []

            mem_url = f"https://elearn.lau.edu.lb/learn/api/v1/users/{user_id}/memberships?expand=course&limit=100"
            resp_mem = req_ctx.get(mem_url, timeout=15000)
            if resp_mem.ok:
                data = resp_mem.json()
                seen_ids = set()
                for r in data.get("results", []):
                    c = r.get("course", {})
                    c_id = c.get("id")
                    c_name = c.get("name", "").strip()
                    c_code = c.get("courseId", "").strip()
                    if c_id and c_name and c_id not in seen_ids:
                        seen_ids.add(c_id)
                        courses.append({
                            "id": c_id,
                            "name": c_name,
                            "code": c_code,
                            "title": c_name
                        })
        except Exception as e:
            log_fn(f"[ERROR] Failed extracting courses: {e}")
        finally:
            if dispose_after:
                req_ctx.dispose()
        return courses

    def _do_login(self, status_fn=None, log_fn=print):
        """
        Interactive Login Flow:
        1. Detects user's real browser (Edge, Chrome, Brave).
        2. Launches visible browser window via subprocess.Popen with dedicated profile.
        3. Attaches over CDP (zero automation flags).
        4. Polls LAU API endpoint /learn/api/public/v1/users/me until authenticated (200).
        5. Saves storage_state.json encrypted via Windows DPAPI.
        6. Closes browser window via CDP and updates manifest.
        """
        candidates = get_ordered_browser_candidates()
        if not candidates:
            return {"success": False, "error": "NO_SUPPORTED_BROWSER"}

        browser = None
        context = None
        active_friendly_name = None

        for b_path, b_name in candidates:
            try:
                browser, context = self._launch_and_attach_browser(
                    b_path, b_name, status_fn=status_fn, log_fn=log_fn
                )
                active_friendly_name = b_name
                break
            except Exception as launch_err:
                log_fn(f"[WARN] Could not initialize {b_name} ({launch_err}). Trying fallback browser...")
                continue

        if not browser or not context:
            log_fn("[ERROR] All available browsers failed to launch.")
            return {"success": False, "error": "ALL_BROWSERS_FAILED"}

        if status_fn:
            status_fn("Waiting for you to sign in...")
        log_fn(f"[AUTH] Please sign in with your LAU student account in the opened {active_friendly_name} window.")
        log_fn("[NOTE] You sign in directly on LAU's official page. This app never sees your password.")

        logged_in = False
        courses = []
        timeout_seconds = 300 # 5-minute timeout as requested
        start_time = time.time()

        try:
            while time.time() - start_time < timeout_seconds:
                if self.cancel_requested:
                    log_fn("[AUTH] Login cancelled by user.")
                    break

                # Check if browser was closed early by user
                if self.active_browser_proc and self.active_browser_proc.poll() is not None:
                    # Give CDP a moment to verify
                    if not self._get_active_port():
                        log_fn("[AUTH] Browser window was closed before signing in.")
                        break

                time.sleep(2)

                # Poll authentication using context cookies against the real endpoint
                try:
                    resp = context.request.get(LAU_API_ME_URL, timeout=4000)
                    if resp.status == 200:
                        logged_in = True
                        break
                except Exception:
                    pass

            if logged_in:
                if status_fn:
                    status_fn("Signed in")
                log_fn("[SUCCESS] Sign-in verified successfully via LAU Blackboard Ultra!")

                # Extract and secure storage state with DPAPI
                raw_state = context.storage_state()
                # Extend session cookie expiration
                future_exp = time.time() + 365 * 86400
                for c in raw_state.get("cookies", []):
                    if c.get("expires", 0) <= 0:
                        c["expires"] = future_exp

                save_secure_storage_state(self.storage_state_path, raw_state)
                log_fn("[SECURITY] Session state secured at rest with Windows DPAPI.")

                # Extract courses
                courses = self._do_extract_courses(req_ctx=context.request, log_fn=log_fn)
                manifest = self._load_manifest()
                manifest["is_connected"] = True
                manifest["courses"] = courses
                self._save_manifest(manifest)

                # Close browser window cleanly via CDP
                self._close_browser_cdp(browser)
                return {"success": True, "courses": courses}
            else:
                self._close_browser_cdp(browser)
                return {"success": False, "error": "CANCELLED_OR_TIMEOUT"}
        except Exception as e:
            self._close_browser_cdp(browser)
            log_fn(f"[ERROR] Login error: {e}")
            return {"success": False, "error": str(e)}

    def _do_fetch_courses(self, log_fn=print):
        """Retrieve enrolled courses from active session or cached manifest."""
        manifest = self._load_manifest()

        if manifest.get("is_connected", False):
            req_ctx = self._create_authenticated_request_context()
            resp = req_ctx.get(LAU_API_ME_URL, timeout=4000)
            if resp.status == 200:
                courses = self._do_extract_courses(req_ctx=req_ctx, log_fn=log_fn)
                req_ctx.dispose()
                if courses:
                    manifest["courses"] = courses
                    manifest["is_connected"] = True
                    self._save_manifest(manifest)
                    log_fn(f"[SUCCESS] Discovered {len(courses)} active course(s):")
                    for idx, c in enumerate(courses, 1):
                        log_fn(f"  {idx}. {c['name']} ({c['code']})")
                    return courses
            else:
                req_ctx.dispose()
                log_fn("[SESSION] Session expired. Automatically refreshing login...")
                res = self._do_login(log_fn=log_fn)
                if res.get("success"):
                    return res.get("courses", [])

        cached = manifest.get("courses", [])
        if cached:
            log_fn(f"[COURSES] Loaded {len(cached)} course(s) from local cache:")
            for idx, c in enumerate(cached, 1):
                log_fn(f"  {idx}. {c['name']} ({c['code']})")
            return cached

        log_fn("[INFO] No active session found. Please click 'Connect Account'.")
        return []

    def _fetch_contents_recursive(self, req_ctx, course_id, folder_id="ROOT", depth=0, max_depth=5):
        """Recursively query Blackboard Ultra Contents API for files."""
        if depth > max_depth:
            return []

        files = []
        endpoint = f"https://elearn.lau.edu.lb/learn/api/v1/courses/{course_id}/contents/{folder_id}/children?limit=100"
        try:
            resp = req_ctx.get(endpoint, timeout=25000)
            if not resp.ok:
                return []
            data = resp.json()
            for item in data.get("results", []):
                handler = item.get("contentHandler", "")
                r_id = item.get("id")
                title = item.get("title") or item.get("name") or "Untitled"

                content_detail = item.get("contentDetail", {})
                file_info = None
                for k, v in content_detail.items():
                    if isinstance(v, dict) and "file" in v:
                        file_info = v["file"]
                        break

                if file_info:
                    files.append({
                        "id": r_id,
                        "title": title,
                        "name": file_info.get("fileName") or file_info.get("name") or title,
                        "permanent_url": file_info.get("permanentUrl"),
                        "size": file_info.get("fileSize", 0),
                        "mime": file_info.get("mimeType", "")
                    })
                elif handler in ["resource/x-bb-folder", "resource/x-bb-lesson", "resource/x-bb-module"] or item.get("hasChildren"):
                    files.extend(self._fetch_contents_recursive(req_ctx, course_id, r_id, depth + 1, max_depth))
        except Exception:
            pass
        return files

    def _do_sync_courses_batch(self, selected_courses, dest_dir, log_fn=print):
        """Download course materials recursively using authenticated API request context."""
        req_ctx = self._create_authenticated_request_context()

        # Check if session is live; if 401, re-trigger login
        resp_check = req_ctx.get(LAU_API_ME_URL, timeout=4000)
        if resp_check.status != 200:
            req_ctx.dispose()
            log_fn("[SESSION] Session expired or invalid. Launching login...")
            res = self._do_login(log_fn=log_fn)
            if not res.get("success"):
                log_fn("[ERROR] Extraction cancelled: unable to authenticate session.")
                return 0
            req_ctx = self._create_authenticated_request_context()

        manifest = self._load_manifest()
        total_downloaded = 0

        try:
            for course in selected_courses:
                course_folder = os.path.join(dest_dir, sanitize_filename(course['name']))
                os.makedirs(course_folder, exist_ok=True)

                log_fn(f"\n==================================================")
                log_fn(f"[COURSE] Scanning: {course['name']}")
                log_fn(f"[FOLDER] {course_folder}")
                log_fn(f"==================================================")

                files = self._fetch_contents_recursive(req_ctx, course['id'])
                log_fn(f"[FOUND] {len(files)} total document(s) and resource(s).")

                for f_info in files:
                    clean_name = sanitize_filename(f_info['name'])
                    file_id = f_info['id']
                    perm_url = f_info.get("permanent_url")

                    if "." not in clean_name and "pdf" in f_info.get("mime", "").lower():
                        clean_name += ".pdf"

                    target_path = os.path.join(course_folder, clean_name)

                    if file_id in manifest.get("downloaded_files", {}) or (os.path.exists(target_path) and os.path.getsize(target_path) > 1000):
                        log_fn(f"  [SKIPPED - EXISTS] {clean_name}")
                        continue

                    if not perm_url:
                        continue

                    dl_url = f"https://elearn.lau.edu.lb{perm_url}?xythos-download=true"
                    referer = f"https://elearn.lau.edu.lb/ultra/courses/{course['id']}/file/{file_id}?courseId={course['id']}"

                    log_fn(f"  -> [DOWNLOADING] {clean_name}...")
                    try:
                        resp = req_ctx.get(dl_url, headers={"referer": referer, "Accept-Encoding": "identity"}, timeout=60000)
                        if resp.ok and len(resp.body()) > 500:
                            with open(target_path, "wb") as f_out:
                                f_out.write(resp.body())
                            actual_kb = round(os.path.getsize(target_path) / 1024, 1)
                            log_fn(f"     [SAVED] {clean_name} ({actual_kb} KB)")
                            manifest.setdefault("downloaded_files", {})[file_id] = {
                                "course": course['name'],
                                "filename": clean_name,
                                "path": target_path,
                                "date": datetime.now().isoformat()
                            }
                            total_downloaded += 1
                        else:
                            log_fn(f"     [NOTICE] Could not download {clean_name} (status {resp.status})")
                    except Exception as err:
                        log_fn(f"     [ERROR] Error downloading {clean_name}: {err}")
        finally:
            req_ctx.dispose()

        self._save_manifest(manifest)
        return total_downloaded

    def _do_logout(self):
        """
        Log Out / Disconnect Action:
        Strictly deletes storage_state.json and purges the browser-profile folder.
        """
        if os.path.exists(self.storage_state_path):
            try:
                os.remove(self.storage_state_path)
            except Exception:
                pass

        if os.path.exists(self.browser_profile_dir):
            try:
                shutil.rmtree(self.browser_profile_dir, ignore_errors=True)
            except Exception:
                pass

        manifest = self._load_manifest()
        manifest["is_connected"] = False
        manifest["courses"] = []
        self._save_manifest(manifest)
        return True

    _do_disconnect = _do_logout


class BlackboardEngine:
    """
    Public facade for BlackboardEngine.
    Delegates operations to dedicated worker thread.
    Thread-safe and callable from any application thread.
    """
    def __init__(self, download_dir=None, app_data_dir=APP_DATA_DIR):
        self.download_dir = download_dir or get_default_download_dir()
        self.app_data_dir = app_data_dir
        self.manifest_path = os.path.join(self.app_data_dir, "sync_manifest.json")
        os.makedirs(self.app_data_dir, exist_ok=True)

        self.worker = BlackboardWorkerThread(self.app_data_dir)
        self.worker.start()

    def load_manifest(self):
        return self.worker._load_manifest()

    def save_manifest(self, manifest):
        self.worker._save_manifest(manifest)

    def is_connected(self):
        manifest = self.load_manifest()
        return manifest.get("is_connected", False)

    def is_logged_in(self):
        return self.worker.call("is_logged_in")

    def login(self, status_fn=None, log_fn=print):
        return self.worker.call("login", status_fn=status_fn, log_fn=log_fn)

    def cancel_login(self):
        self.worker.cancel_login()

    def fetch_courses(self, log_fn=print):
        return self.worker.call("fetch_courses", log_fn=log_fn)

    def sync_courses_batch(self, selected_courses, dest_dir=None, log_fn=print):
        if dest_dir is None:
            dest_dir = self.download_dir
        return self.worker.call("sync_courses_batch", selected_courses=selected_courses, dest_dir=dest_dir, log_fn=log_fn)

    def logout(self):
        return self.worker.call("logout")

    disconnect = logout

    def close(self):
        self.worker.stop()
