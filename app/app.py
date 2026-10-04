"""
LAU Blackboard Sync - Desktop Application
A modern, beautiful White & Orange GUI for extracting and organizing Blackboard course materials.
Built with CustomTkinter & Playwright over CDP.
Zero credentials stored: completely official OS browser launch (Edge, Chrome, Brave).
Session state is secured with Windows DPAPI.
"""

import os
import sys
import time
import threading
import queue
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk

from core_engine import (
    BlackboardEngine, 
    DEFAULT_DOWNLOAD_DIR,
    get_default_download_dir,
    detect_supported_browser
)

# App Configuration & Theme
ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("green")

# Colors
ORANGE_PRIMARY = "#FF6B00"
ORANGE_HOVER = "#E55A00"
BG_WHITE = "#FFFFFF"
CARD_BG = "#F8FAFC"
CARD_BORDER = "#E2E8F0"
TEXT_DARK = "#0F172A"
TEXT_MUTED = "#64748B"
CONSOLE_BG = "#0B0F19"
CONSOLE_TEXT = "#10B981"
CONSOLE_WARN = "#F59E0B"

def get_resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller bundle."""
    base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)

class BlackboardSyncApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("LectureFetch — Blackboard Lecture Extractor")
        self.geometry("860, 620")
        self.minsize(760, 500)
        self.configure(fg_color=BG_WHITE)

        ico_file = get_resource_path("app_icon.ico")
        if os.path.exists(ico_file):
            try:
                self.iconbitmap(ico_file)
            except Exception:
                pass

        self.engine = BlackboardEngine()
        self.log_queue = queue.Queue()
        self.status_queue = queue.Queue()
        self.is_syncing = False
        self.is_logging_in = False

        # Load permanent persistent connection state
        manifest = self.engine.load_manifest()
        self.is_connected = manifest.get("is_connected", False)
        self.courses = manifest.get("courses", [])
        self.course_vars = {}

        self._build_ui()
        self._start_pollers()

        # Initialize UI immediately according to persistent connection
        self._update_status_ui(self.is_connected)
        if self.is_connected:
            self._render_courses_ui()
            self.log("[STATUS] Connected to Blackboard Ultra.")
            if self.courses:
                self.log(f"[COURSES] Loaded {len(self.courses)} active enrolled course(s).")
        else:
            self._render_courses_ui()
            self.log("[STATUS] No active session found. Click 'Connect Account' to sign in via your browser.")

        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _build_ui(self):
        # 1. Top Header Bar (Pinned to top)
        header_frame = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=10, border_width=1, border_color=CARD_BORDER)
        header_frame.pack(side="top", fill="x", padx=16, pady=(10, 6))

        # Title & Subtitle with Clean App Logo
        title_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        title_box.pack(side="left", padx=12, pady=8)

        logo_file = get_resource_path("logo_rounded.png")
        if not os.path.exists(logo_file):
            logo_file = get_resource_path("logo_transparent.png")

        if os.path.exists(logo_file):
            try:
                from PIL import Image
                logo_pil = Image.open(logo_file)
                self.logo_ctk = ctk.CTkImage(light_image=logo_pil, dark_image=logo_pil, size=(52, 52))
                lbl_logo = ctk.CTkLabel(title_box, image=self.logo_ctk, text="")
                lbl_logo.pack(side="left", padx=(0, 12))
            except Exception:
                pass

        title_text_box = ctk.CTkFrame(title_box, fg_color="transparent")
        title_text_box.pack(side="left")

        title_lbl = ctk.CTkLabel(
            title_text_box, 
            text="LectureFetch", 
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color=TEXT_DARK
        )
        title_lbl.pack(anchor="w")

        subtitle_lbl = ctk.CTkLabel(
            title_text_box, 
            text="Automated lecture organizer for university students", 
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=TEXT_MUTED
        )
        subtitle_lbl.pack(anchor="w")

        # Security reassurance text
        self.sec_lbl = ctk.CTkLabel(
            title_text_box,
            text="🔒 You sign in on your university's official page. This app never sees your password.",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color="#0D9488"
        )
        self.sec_lbl.pack(anchor="w", pady=(1, 0))

        # Status Pill, Reset Button & Connect/Logout Button
        action_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        action_box.pack(side="right", padx=12, pady=8)

        self.status_pill = ctk.CTkLabel(
            action_box,
            text="⚪ Checking...",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=TEXT_MUTED,
            fg_color="#EDF2F7",
            corner_radius=10,
            padx=10,
            pady=3
        )
        self.status_pill.pack(side="left", padx=(0, 8))

        self.btn_reset = ctk.CTkButton(
            action_box,
            text="🗑️ Reset All Data",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            fg_color="#F1F5F9",
            hover_color="#E2E8F0",
            text_color="#475569",
            corner_radius=6,
            height=30,
            command=self.on_reset_clicked
        )
        self.btn_reset.pack(side="left", padx=(0, 6))

        self.btn_connect = ctk.CTkButton(
            action_box,
            text="👤 Connect Account",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            fg_color=ORANGE_PRIMARY,
            hover_color=ORANGE_HOVER,
            text_color="#FFFFFF",
            corner_radius=6,
            height=30,
            command=self.on_connect_clicked
        )
        self.btn_connect.pack(side="left")

        # 4. Bottom Footer Bar (PINNED TO BOTTOM FIRST - guaranteed never clipped)
        footer = ctk.CTkFrame(self, fg_color="#F8FAFC", corner_radius=8, border_width=1, border_color="#E2E8F0")
        footer.pack(side="bottom", fill="x", padx=16, pady=(4, 10))

        footer_inner = ctk.CTkFrame(footer, fg_color="transparent")
        footer_inner.pack(fill="x", padx=12, pady=5)

        ctk.CTkLabel(
            footer_inner,
            text="v1.1.0 • Open Source • Windows DPAPI Secured",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=TEXT_MUTED
        ).pack(side="left")

        btn_wish = ctk.CTkButton(
            footer_inner,
            text="🧡 like this tool? feel free to help out (+961 70 007 193)",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            fg_color="#FF6B00",
            hover_color="#EA580C",
            text_color="#FFFFFF",
            corner_radius=6,
            height=28,
            command=self.show_wish_support_modal
        )
        btn_wish.pack(side="right")

        # 2. Main Work Area (Packed from top down between header and footer)
        # Destination Folder Card (Compact inline layout)
        dest_card = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=8, border_width=1, border_color=CARD_BORDER)
        dest_card.pack(side="top", fill="x", padx=16, pady=(0, 6))

        dest_row = ctk.CTkFrame(dest_card, fg_color="transparent")
        dest_row.pack(fill="x", padx=12, pady=6)

        ctk.CTkLabel(
            dest_row,
            text="📁 Download Destination:",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=TEXT_DARK
        ).pack(side="left", padx=(0, 8))

        self.dest_entry = ctk.CTkEntry(
            dest_row,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            fg_color="#FFFFFF",
            border_color="#CBD5E1",
            text_color=TEXT_DARK,
            height=28
        )
        self.dest_entry.insert(0, DEFAULT_DOWNLOAD_DIR)
        self.dest_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        btn_browse = ctk.CTkButton(
            dest_row,
            text="Browse...",
            width=80,
            height=28,
            fg_color="#E2E8F0",
            hover_color="#CBD5E1",
            text_color=TEXT_DARK,
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            command=self.on_browse_clicked
        )
        btn_browse.pack(side="left")

        # Course Selection Section Card
        self.courses_card = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=8, border_width=1, border_color=CARD_BORDER)
        self.courses_card.pack(side="top", fill="x", padx=16, pady=(0, 6))

        c_header_box = ctk.CTkFrame(self.courses_card, fg_color="transparent")
        c_header_box.pack(fill="x", padx=12, pady=(6, 4))

        ctk.CTkLabel(
            c_header_box,
            text="📚 Enrolled Courses:",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color=TEXT_DARK
        ).pack(side="left")

        self.btn_refresh = ctk.CTkButton(
            c_header_box,
            text="🔄 Refresh",
            width=75,
            height=24,
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            fg_color="#E2E8F0",
            hover_color="#CBD5E1",
            text_color=TEXT_DARK,
            command=self.on_refresh_courses_clicked
        )
        self.btn_refresh.pack(side="right")

        self.btn_deselect_all = ctk.CTkButton(
            c_header_box,
            text="Uncheck All",
            width=80,
            height=24,
            font=ctk.CTkFont(family="Segoe UI", size=11),
            fg_color="#F1F5F9",
            hover_color="#E2E8F0",
            text_color="#475569",
            command=self.deselect_all_courses
        )
        self.btn_deselect_all.pack(side="right", padx=6)

        self.btn_select_all = ctk.CTkButton(
            c_header_box,
            text="Check All",
            width=70,
            height=24,
            font=ctk.CTkFont(family="Segoe UI", size=11),
            fg_color="#F1F5F9",
            hover_color="#E2E8F0",
            text_color="#475569",
            command=self.select_all_courses
        )
        self.btn_select_all.pack(side="right")

        # Scrollable Frame for courses
        self.courses_scroll = ctk.CTkScrollableFrame(
            self.courses_card,
            fg_color="#FFFFFF",
            corner_radius=6,
            border_width=1,
            border_color="#E2E8F0",
            height=100
        )
        self.courses_scroll.pack(fill="x", padx=12, pady=(0, 6))

        # Big Action Button
        self.btn_sync = ctk.CTkButton(
            self,
            text="⚡ START EXTRACTION & SYNC",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            fg_color=ORANGE_PRIMARY,
            hover_color=ORANGE_HOVER,
            text_color="#FFFFFF",
            corner_radius=8,
            height=38,
            command=self.on_start_sync_clicked
        )
        self.btn_sync.pack(side="top", fill="x", padx=16, pady=(0, 6))

        # 3. Execution Terminal Box (Takes all remaining vertical space)
        console_container = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=8, border_width=1, border_color=CARD_BORDER)
        console_container.pack(side="top", fill="both", expand=True, padx=16, pady=(0, 6))

        console_header = ctk.CTkFrame(console_container, fg_color="transparent")
        console_header.pack(fill="x", padx=10, pady=(4, 2))

        ctk.CTkLabel(
            console_header,
            text="💻 Execution Terminal Output:",
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            text_color=TEXT_DARK
        ).pack(side="left")

        btn_clear = ctk.CTkButton(
            console_header,
            text="Clear",
            width=50,
            height=20,
            font=ctk.CTkFont(family="Segoe UI", size=10),
            fg_color="#E2E8F0",
            hover_color="#CBD5E1",
            text_color=TEXT_DARK,
            command=self.clear_console
        )
        btn_clear.pack(side="right")

        self.console_text = ctk.CTkTextbox(
            console_container,
            fg_color=CONSOLE_BG,
            text_color=CONSOLE_TEXT,
            font=ctk.CTkFont(family="Consolas", size=11),
            corner_radius=6,
            border_width=0
        )
        self.console_text.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        self.console_text.configure(state="disabled")

    # Logging & Status Queue Helpers
    def log(self, message):
        """Thread-safe logging helper."""
        timestamp = time.strftime("[%H:%M:%S] ")
        self.log_queue.put(timestamp + str(message))

    def _start_pollers(self):
        # Poll log queue
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.console_text.configure(state="normal")
                self.console_text.insert("end", line + "\n")
                self.console_text.see("end")
                self.console_text.configure(state="disabled")
        except queue.Empty:
            pass

        # Poll status text updates
        try:
            while True:
                state_text = self.status_queue.get_nowait()
                if self.is_logging_in:
                    self.status_pill.configure(
                        text=f"⏳ {state_text}",
                        text_color="#D97706",
                        fg_color="#FEF3C7"
                    )
        except queue.Empty:
            pass

        self.after(100, self._start_pollers)

    def clear_console(self):
        self.console_text.configure(state="normal")
        self.console_text.delete("1.0", "end")
        self.console_text.configure(state="disabled")

    def _update_status_ui(self, is_connected):
        self.is_connected = is_connected
        if is_connected:
            self.status_pill.configure(
                text="🟢 Connected",
                text_color="#15803D",
                fg_color="#DCFCE7"
            )
            self.btn_connect.configure(
                text="🔌 Log Out",
                fg_color="#DC2626",
                hover_color="#B91C1C",
                state="normal"
            )
        else:
            self.status_pill.configure(
                text="🔴 Disconnected",
                text_color="#B91C1C",
                fg_color="#FEE2E2"
            )
            self.btn_connect.configure(
                text="👤 Connect Account",
                fg_color=ORANGE_PRIMARY,
                hover_color=ORANGE_HOVER,
                state="normal"
            )

    def on_browse_clicked(self):
        folder = filedialog.askdirectory(initialdir=self.dest_entry.get())
        if folder:
            self.dest_entry.delete(0, "end")
            self.dest_entry.insert(0, folder)
            self.engine.download_dir = folder

    def show_unsupported_browser_dialog(self):
        """Shows modal dialog if Edge, Chrome, or Brave are not detected."""
        dlg = ctk.CTkToplevel(self)
        dlg.title("Browser Not Supported")
        dlg.geometry("500, 310")
        dlg.resizable(False, False)
        dlg.configure(fg_color="#FFFFFF")
        dlg.grab_set()

        ctk.CTkLabel(
            dlg,
            text="⚠️ Supported Browser Required",
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
            text_color="#DC2626"
        ).pack(pady=(20, 8))

        msg = (
            "Blackboard Sync needs Microsoft Edge, Google Chrome, or Brave\n"
            "to sign you in securely. Firefox isn't supported.\n\n"
            "Please install one of the supported Chromium-based browsers below:"
        )
        ctk.CTkLabel(
            dlg,
            text=msg,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=TEXT_MUTED,
            justify="center"
        ).pack(padx=20, pady=(0, 15))

        btn_box = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_box.pack(pady=5)

        ctk.CTkButton(
            btn_box,
            text="🌐 Download Microsoft Edge",
            width=200,
            command=lambda: webbrowser.open("https://www.microsoft.com/edge")
        ).pack(pady=4)

        ctk.CTkButton(
            btn_box,
            text="🌐 Download Google Chrome",
            width=200,
            fg_color="#0284C7",
            hover_color="#0369A1",
            command=lambda: webbrowser.open("https://www.google.com/chrome")
        ).pack(pady=4)

        action_row = ctk.CTkFrame(dlg, fg_color="transparent")
        action_row.pack(pady=(15, 0))

        def _try_again():
            dlg.destroy()
            self.on_connect_clicked()

        ctk.CTkButton(
            action_row,
            text="🔄 Try Again",
            width=100,
            fg_color=ORANGE_PRIMARY,
            hover_color=ORANGE_HOVER,
            command=_try_again
        ).pack(side="left", padx=8)

        ctk.CTkButton(
            action_row,
            text="Cancel",
            width=80,
            fg_color="#E2E8F0",
            hover_color="#CBD5E1",
            text_color=TEXT_DARK,
            command=dlg.destroy
        ).pack(side="left", padx=8)

    def on_connect_clicked(self):
        if self.is_syncing:
            messagebox.showwarning("Busy", "A sync operation is currently running.")
            return

        # If currently logging in, clicking cancels
        if self.is_logging_in:
            self.engine.cancel_login()
            self.is_logging_in = False
            self._update_status_ui(False)
            self.log("[AUTH] Login cancelled by user.")
            return

        # If already connected, clicking logs out
        if getattr(self, "is_connected", False):
            confirm = messagebox.askyesno(
                "Log Out", 
                "Are you sure you want to log out from Blackboard?\n\n"
                "This will remove your saved session token and clear the browser profile."
            )
            if not confirm:
                return
            self.engine.logout()
            self.is_connected = False
            self.courses.clear()
            self._render_courses_ui()
            self._update_status_ui(False)
            self.log("[AUTH] Logged out! Session state and browser profile purged.")
            return

        # Verify browser availability before launching thread
        path, friendly = detect_supported_browser()
        if not path:
            self.show_unsupported_browser_dialog()
            return

        # Start interactive login
        self.is_logging_in = True
        self.btn_connect.configure(text="❌ Cancel", fg_color="#64748B", hover_color="#475569")
        self.status_pill.configure(
            text="⏳ Opening your browser...",
            text_color="#D97706",
            fg_color="#FEF3C7"
        )

        def _run_login():
            def _status_cb(st):
                self.status_queue.put(st)

            res = self.engine.login(status_fn=_status_cb, log_fn=self.log)
            self.is_logging_in = False

            if res.get("error") == "NO_SUPPORTED_BROWSER":
                self.after(0, self.show_unsupported_browser_dialog)
                self.after(0, lambda: self._update_status_ui(False))
                return

            success = res.get("success", False)
            self.after(0, lambda: self._update_status_ui(success))
            if success:
                self.courses = res.get("courses", [])
                self.after(0, self._render_courses_ui)
                self.log(f"[AUTH] Successfully linked {len(self.courses)} course(s).")
            else:
                err = res.get("error", "")
                if "CANCEL" in err:
                    self.log("[AUTH] Sign-in cancelled.")
                else:
                    self.log(f"[AUTH] Sign-in not completed: {err}")

        threading.Thread(target=_run_login, daemon=True).start()

    def on_reset_clicked(self):
        if self.is_syncing or self.is_logging_in:
            messagebox.showwarning("Busy", "Cannot reset while an operation is currently in progress.")
            return

        confirm = messagebox.askyesno(
            "Reset All Data",
            "Are you sure you want to reset?\n\n"
            "This will:\n"
            "• Clear download history manifest\n"
            "• Delete encrypted DPAPI session tokens\n"
            "• Clean out test download folder\n"
            "• Log out and purge browser profile\n\n"
            "Do you want to proceed?"
        )
        if not confirm:
            return

        self.log("[RESET] Clearing download history manifest...")
        self.engine.logout()

        manifest = self.engine.load_manifest()
        manifest["downloaded_files"] = {}
        manifest["courses"] = []
        manifest["is_connected"] = False
        self.engine.save_manifest(manifest)

        self.is_connected = False
        self.courses.clear()
        self._render_courses_ui()
        self._update_status_ui(False)
        self.log("[RESET] Reset complete! Session disconnected and test data cleared.")
        messagebox.showinfo("Reset Complete", "All test data cleared and session logged out.\nClick 'Connect Account' to sign in.")

    def select_all_courses(self):
        for var, _ in self.course_vars.values():
            var.set(True)

    def deselect_all_courses(self):
        for var, _ in self.course_vars.values():
            var.set(False)

    def on_refresh_courses_clicked(self):
        if self.is_syncing or self.is_logging_in:
            return

        def _run():
            self.btn_refresh.configure(state="disabled")
            courses = self.engine.fetch_courses(log_fn=self.log)
            if courses:
                self.courses = courses
                self.after(0, self._render_courses_ui)
            self.after(0, lambda: self.btn_refresh.configure(state="normal"))

        threading.Thread(target=_run, daemon=True).start()

    def _render_courses_ui(self):
        # Clear existing checkboxes
        for widget in self.courses_scroll.winfo_children():
            widget.destroy()

        self.course_vars.clear()

        if not self.courses:
            ctk.CTkLabel(
                self.courses_scroll,
                text="No active session. Click 'Connect Account' to sign in and load your courses.",
                font=ctk.CTkFont(family="Segoe UI", size=12),
                text_color=TEXT_MUTED
            ).pack(pady=10)
            return

        # Render checkboxes
        for c in self.courses:
            var = ctk.BooleanVar(value=True)
            self.course_vars[c['title']] = (var, c)
            cb = ctk.CTkCheckBox(
                self.courses_scroll,
                text=c['title'],
                variable=var,
                font=ctk.CTkFont(family="Segoe UI", size=12),
                fg_color=ORANGE_PRIMARY,
                hover_color=ORANGE_HOVER
            )
            cb.pack(anchor="w", padx=10, pady=4)

    def on_start_sync_clicked(self):
        if self.is_syncing or self.is_logging_in:
            messagebox.showwarning("Busy", "An operation is already running.")
            return

        dest = self.dest_entry.get().strip()
        if not dest:
            dest = get_default_download_dir()
            self.dest_entry.delete(0, "end")
            self.dest_entry.insert(0, dest)

        selected_courses = [c for var, c in self.course_vars.values() if var.get()]
        if not selected_courses:
            messagebox.showwarning("No Selection", "Please select at least one course to sync.")
            return

        self.is_syncing = True
        self.btn_sync.configure(state="disabled", text="⏳ EXTRACTING LECTURES...", fg_color="#94A3B8")

        def _run_sync():
            start_time = time.time()
            self.log(f"[START] Beginning extraction for {len(selected_courses)} course(s)...")

            total_files = self.engine.sync_courses_batch(selected_courses, dest_dir=dest, log_fn=self.log)

            elapsed = round(time.time() - start_time, 1)
            self.log("==================================================")
            self.log(f"[ALL DONE] Synced {total_files} new lecture file(s) across all courses in {elapsed}s!")
            self.log(f"[LOCATION] Stored in: {dest}")
            self.log("==================================================")

            self.after(0, lambda: self.btn_sync.configure(state="normal", text="⚡ START EXTRACTION & SYNC", fg_color=ORANGE_PRIMARY))
            self.after(0, lambda: setattr(self, 'is_syncing', False))
            self.after(0, lambda: messagebox.showinfo("Extraction Complete", f"Successfully synced {total_files} file(s) in {elapsed} seconds!"))

        threading.Thread(target=_run_sync, daemon=True).start()

    def show_wish_support_modal(self):
        modal = ctk.CTkToplevel(self)
        modal.title("Support LectureFetch")
        modal.geometry("480, 340")
        modal.resizable(False, False)
        modal.configure(fg_color="#FFFFFF")
        modal.grab_set()

        ctk.CTkLabel(
            modal,
            text="🧡 Support the Project!",
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
            text_color=ORANGE_PRIMARY
        ).pack(pady=(20, 5))

        desc = (
            "like this tool? feel free to help out (+961 70 007 193)\n\n"
            "This software is 100% free and open-source for university students.\n"
            "Any tip helps cover maintenance, hosting, and future updates!"
        )
        ctk.CTkLabel(
            modal,
            text=desc,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=TEXT_MUTED,
            justify="center"
        ).pack(padx=20, pady=10)

        card = ctk.CTkFrame(modal, fg_color="#FFF7ED", border_width=1, border_color="#FDBA74", corner_radius=10)
        card.pack(fill="x", padx=30, pady=10)

        ctk.CTkLabel(
            card,
            text="Wish Money Account:",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#9A3412"
        ).pack(pady=(10, 2))

        ctk.CTkLabel(
            card,
            text="Phone: +961 70 007 193  |  Name: Jad Mehtar",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color="#C2410C"
        ).pack(pady=(0, 6))

        def _copy_num():
            try:
                self.clipboard_clear()
                self.clipboard_append("+96170007193")
                btn_copy.configure(text="✓ Copied to clipboard!", fg_color="#15803D")
            except Exception:
                pass

        btn_copy = ctk.CTkButton(
            card,
            text="📋 Copy Number (+961 70 007 193)",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            fg_color="#EA580C",
            hover_color="#C2410C",
            width=230,
            height=28,
            command=_copy_num
        )
        btn_copy.pack(pady=(0, 10))

        ctk.CTkButton(
            modal,
            text="Close",
            width=100,
            fg_color="#E2E8F0",
            hover_color="#CBD5E1",
            text_color=TEXT_DARK,
            command=modal.destroy
        ).pack(pady=(10, 0))

    def on_closing(self):
        try:
            self.engine.close()
        except Exception:
            pass
        self.destroy()

if __name__ == "__main__":
    app = BlackboardSyncApp()
    app.mainloop()
