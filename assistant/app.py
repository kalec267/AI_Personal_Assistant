"""Native desktop entry point. No browser, web view, or HTTP server is used."""
from __future__ import annotations

import argparse
import datetime as dt
import functools
import gc
import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .core import UserError
from . import desktop
from .service import AssistantService
from .config_dialog import ConfigDialog
from .local_voice import install_model, MODEL_NAME, MODEL_FILES
from .settings import default_data_dir, load_settings, save_settings
from .voice import VoiceRouter, preferred_voice, recognize_once
from .audio_diagnostics import diagnose_audio
from .wake_word import WakeListener, install_wake_model, model_ready, recognize_locked, transcribe_command
from .theme import BG, PAPER, INK, MUTED, BLUE, LINE, SURFACE, TINT, apply_theme


class EventDialog(tk.Toplevel):
    def __init__(self, app, draft=None, turn_id=None):
        super().__init__(app.root)
        self.app = app
        self.turn_id = turn_id
        self.title("LUNA · 새 일정")
        self.configure(bg=PAPER)
        self.resizable(False, False)
        self.transient(app.root)
        self.grab_set()
        start = dt.datetime.now().replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
        draft = draft or {"title": "", "start": start.isoformat(timespec="minutes"), "end": (start + dt.timedelta(hours=1)).isoformat(timespec="minutes")}
        self.title_var = tk.StringVar(value=draft["title"])
        self.start_var = tk.StringVar(value=draft["start"].replace("T", " "))
        self.end_var = tk.StringVar(value=draft["end"].replace("T", " "))
        self.error_var = tk.StringVar()
        content = tk.Frame(self, bg=PAPER, padx=28, pady=24)
        content.pack(fill="both", expand=True)
        app.label(content, "새 일정", size=20, bold=True).pack(anchor="w", pady=(0, 10))
        for text, variable in [("일정 이름", self.title_var), ("시작 · YYYY-MM-DD HH:MM", self.start_var), ("종료 · YYYY-MM-DD HH:MM", self.end_var)]:
            app.label(content, text, size=10, color=MUTED).pack(anchor="w", pady=(12, 5))
            entry = ttk.Entry(content, textvariable=variable, width=40)
            entry.pack(fill="x", ipady=5)
            if variable is self.title_var:
                entry.focus_set()
        app.label(content, "PC 현지 시간으로 저장합니다.", size=9, color=MUTED).pack(anchor="w", pady=(10, 0))
        tk.Label(content, textvariable=self.error_var, bg=PAPER, fg="#aa435d", wraplength=330, justify="left").pack(anchor="w", pady=8)
        app.button(content, "일정 저장하기", self.save, primary=True).pack(fill="x")
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def save(self):
        try:
            event = self.app.service.calendar.add(self.title_var.get(), self.start_var.get(), self.end_var.get())
        except (UserError, OSError) as error:
            self.error_var.set(str(error))
            return
        self.app.day_var.set(event["start"][:10])
        self.app.service.draft_saved(self.turn_id, event)
        self.app.refresh_events()
        self.app.append_message("루나", "일정을 저장했어요.")
        self.app.say("일정을 저장했어요.")
        self.destroy()


class LunaApp:
    def __init__(self, root, data_dir, *, voice=None, recognizer=recognize_once, load_voices=True):
        # Tk objects in unreachable cycles must be finalized on the UI thread.
        # Automatic cyclic GC can otherwise run during document parsing or an
        # HTTP response on a worker. This GUI process collects cycles explicitly.
        gc.disable()
        gc.collect()
        self.root = root
        self.gc_ticks = 0
        self.data_dir = Path(data_dir)
        self.service = AssistantService(data_dir)
        self.voice = voice or VoiceRouter(data_dir)
        self.recognizer = recognizer
        self.settings = load_settings(data_dir)
        self.jobs = queue.Queue()
        self.callbacks = {}
        self.next_job = 0
        self.closed = False
        self.command_busy = False
        self.voice_command = False
        self.listening = False
        self.audio_checking = False
        self.listen_progress = queue.Queue()
        self.speech_generation = 0
        self.speech_active = False
        self.microphone_lock = threading.Lock()
        self.wake = None
        self.wake_listening = False
        self.wake_pending = False
        self.wake_job = 0
        self.installing_wake = False
        self.wake_note = tk.StringVar(value="호출어 감지 준비 중…")
        self.voice_items = []
        self.installing_voice = False
        self.last_dialog = None
        self.config_dialog = None
        self.attachment_dialog = None
        self.attachments = []
        self.analysis_progress = queue.Queue()
        self.attachment_note = tk.StringVar(value="첨부 없음")
        self.mode_var = tk.StringVar(value={"off": "기본 명령", "local": "Ollama 로컬", "cloud": "Ollama 클라우드", "openai": "ChatGPT · OpenAI"}.get(self.settings.get("ai_mode", "off"), "기본 명령") + " · 데스크톱")
        self.command_var = tk.StringVar()
        self.day_var = tk.StringVar(value=dt.date.today().isoformat())
        self.status_var = tk.StringVar(value="준비됐어요. 무엇을 도와드릴까요?")
        self.voice_var = tk.StringVar(value=self.settings.get("voice", ""))
        self.voice_note = tk.StringVar(value="Windows 음성 목록을 불러오는 중…")
        self.speak_var = tk.BooleanVar(value=self.settings.get("speak", True))
        self.voice_speed = self.settings.get("voice_speed", 1.0)
        self.root.title("LUNA · 나만의 개인 비서")
        self.root.geometry("1180x760")
        self.root.minsize(960, 620)
        self.root.configure(bg=BG)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._style()
        self._build()
        self.refresh_events()
        self.render_conversation()
        self._poll_id = self.root.after(60, self._poll)
        if load_voices:
            self.run_task(self.voice.list, self._voices_loaded, lambda error: self.voice_note.set(str(error)))
        else:
            self.voice_note.set("로컬 한국어 여성 음성 또는 Windows 설치 음성을 선택할 수 있어요.")
        if self.settings.get("apply_volume_on_start", False) and os.name == "nt":
            self.run_task(functools.partial(desktop.volume, "set", self.settings.get("volume", 30)),
                          lambda message: self.status_var.set(message), self.show_error)
        self.configure_wake()

    @staticmethod
    def label(parent, text, *, size=11, color=INK, bold=False):
        return tk.Label(parent, text=text, font=("Malgun Gothic", size, "bold" if bold else "normal"), bg=parent.cget("bg"), fg=color, anchor="w", justify="left")

    @staticmethod
    def button(parent, text, command, primary=False):
        return tk.Button(parent, text=text, command=command, bg=BLUE if primary else SURFACE, fg="white" if primary else BLUE,
                         activebackground="#5945c9" if primary else TINT, activeforeground="white" if primary else BLUE,
                         disabledforeground="#a1a8bb", relief="flat", bd=0, padx=13, pady=9,
                         highlightthickness=1, highlightbackground=BLUE if primary else SURFACE, highlightcolor=BLUE,
                         cursor="hand2", font=("Malgun Gothic", 10, "bold" if primary else "normal"), takefocus=True)

    def _style(self):
        apply_theme(self.root)

    def _build(self):
        header = tk.Frame(self.root, bg=BG, padx=28, pady=18)
        header.grid(row=0, column=0, sticky="ew")
        self.label(header, "LUNA ✦", size=23, bold=True).pack(side="left")
        self.settings_button = self.button(header, "설정", self.open_config)
        self.settings_button.pack(side="right")
        tk.Label(header, textvariable=self.mode_var, bg=BG, fg=BLUE, font=("Malgun Gothic", 9)).pack(side="right", padx=15)
        body = tk.Frame(self.root, bg=BG, padx=24)
        body.grid(row=1, column=0, sticky="nsew", pady=(0, 20))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0, minsize=300)
        body.rowconfigure(0, weight=1)
        self.main_tabs = ttk.Notebook(body)
        self.main_tabs.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        self.chat_page = tk.Frame(self.main_tabs, bg=PAPER, padx=16, pady=16,
                                  highlightbackground=LINE, highlightcolor=LINE, highlightthickness=1)
        self.main_tabs.add(self.chat_page, text="대화")
        from .search_panel import SearchPanel
        self.search_panel = SearchPanel(self, self.main_tabs)
        self.main_tabs.add(self.search_panel, text="검색")
        self.main_tabs.bind("<<NotebookTabChanged>>", self._tab_changed)
        left = self.chat_page
        tk.Label(left, textvariable=self.status_var, bg=PAPER, fg=MUTED,
                 font=("Malgun Gothic", 9), wraplength=500, anchor="w", justify="left").pack(fill="x", pady=(0, 12))
        transcript = tk.Frame(left, bg=PAPER, height=1)
        transcript.pack_propagate(False)
        transcript.pack(fill="both", expand=True)
        self.chat = tk.Text(transcript, bg=SURFACE, fg=INK, relief="flat", bd=0, wrap="word", font=("Malgun Gothic", 11), padx=18, pady=14, height=4, state="disabled")
        scroll = ttk.Scrollbar(transcript, command=self.chat.yview)
        self.chat.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.chat.pack(side="left", fill="both", expand=True)
        self.chat.tag_configure("루나", foreground=BLUE, font=("Malgun Gothic", 10, "bold"), spacing3=5)
        self.chat.tag_configure("나", foreground=MUTED, font=("Malgun Gothic", 10, "bold"), spacing3=5)
        self.chat.tag_configure("오류", foreground="#b04b65")
        suggestions = tk.Frame(left, bg=PAPER)
        suggestions.pack(fill="x", pady=10)
        self.button(suggestions, "회의 일정 추가", lambda: self.submit("내일 오후 3시 팀 회의 일정 추가")).pack(side="left", padx=(0, 7))
        self.button(suggestions, "오늘 일정", lambda: self.submit("오늘 일정 보여줘")).pack(side="left")
        self.button(suggestions, "새 대화", self.new_conversation).pack(side="right")
        attachment_row = tk.Frame(left, bg=PAPER)
        attachment_row.pack(fill="x", pady=(0, 6))
        self.attach_button = self.button(attachment_row, "파일 첨부", self.choose_attachments)
        self.attach_button.pack(side="left")
        self.summary_button = self.button(attachment_row, "첨부 요약", self.summarize_attachments)
        self.summary_button.pack(side="left", padx=6)
        self.button(attachment_row, "비우기", self.clear_attachments).pack(side="right")
        attachment_label = tk.Label(left, textvariable=self.attachment_note, bg=PAPER, fg=BLUE, wraplength=450,
                                    justify="left", anchor="w", font=("Malgun Gothic", 8), cursor="hand2")
        attachment_label.pack(fill="x", pady=(0, 7))
        attachment_label.bind("<Button-1>", lambda _event: self.open_attachments())
        input_row = tk.Frame(left, bg=PAPER)
        input_row.pack(fill="x")
        self.entry = ttk.Entry(input_row, textvariable=self.command_var)
        self.entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.entry.bind("<Return>", lambda _event: self.submit())
        self.mic_button = self.button(input_row, "마이크", self.listen)
        self.mic_button.pack(side="left", padx=6)
        self.send_button = self.button(input_row, "보내기", self.submit, primary=True)
        self.send_button.pack(side="left")
        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="new")
        right.columnconfigure(0, weight=1)
        schedule = tk.Frame(right, bg=PAPER, padx=14, pady=16, highlightbackground=LINE, highlightthickness=1)
        self.calendar_panel = schedule
        schedule.grid(row=0, column=0, sticky="new")
        self.label(schedule, "나의 일정", size=15, bold=True).pack(anchor="w", pady=(0, 14))
        day_row = tk.Frame(schedule, bg=PAPER)
        day_row.pack(fill="x", pady=(0, 10))
        previous = self.button(day_row, "‹", lambda: self.shift_day(-1))
        previous.configure(padx=6, pady=5)
        previous.pack(side="left")
        day_entry = ttk.Entry(day_row, textvariable=self.day_var, width=10)
        day_entry.pack(side="left", padx=4, fill="x", expand=True)
        day_entry.bind("<Return>", lambda _event: self.refresh_events())
        following = self.button(day_row, "›", lambda: self.shift_day(1))
        following.configure(padx=6, pady=5)
        following.pack(side="left")
        refresh = self.button(day_row, "조회", self.refresh_events)
        refresh.configure(padx=6, pady=5)
        refresh.pack(side="left", padx=(4, 0))
        tree_area = tk.Frame(schedule, bg=PAPER)
        self.tree = ttk.Treeview(tree_area, columns=("time", "title"), show="headings", selectmode="browse", height=4, style="Calendar.Treeview")
        self.tree.heading("time", text="시작")
        self.tree.heading("title", text="일정 이름")
        self.tree.column("time", width=64, minwidth=60, stretch=False)
        self.tree.column("title", width=150, minwidth=100)
        tree_scroll = ttk.Scrollbar(tree_area, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        self.calendar_empty = tk.Frame(tree_area, bg=PAPER, padx=12, pady=12)
        self.label(self.calendar_empty, "일정 없음", size=10, color=MUTED).pack(anchor="center")
        self.event_detail = tk.StringVar()
        detail_label = tk.Label(schedule, textvariable=self.event_detail, bg=PAPER, fg=MUTED, font=("Malgun Gothic", 9), wraplength=260, justify="left", anchor="w")
        self.tree.bind("<<TreeviewSelect>>", self._event_selected)
        actions = tk.Frame(schedule, bg=PAPER)
        actions.pack(side="bottom", fill="x")
        detail_label.pack(side="bottom", fill="x", pady=8)
        tree_area.pack(fill="both", expand=True)
        self.button(actions, "+ 일정", self.open_event, primary=True).pack(side="left")
        self.button(actions, "삭제", self.delete_event).pack(side="left", padx=6)
        self.button(actions, "내보내기", self.export_calendar).pack(side="right")
        self.entry.focus_set()

    def _tab_changed(self, _event=None):
        selected = self.main_tabs.select()
        if selected == str(self.search_panel):
            self.search_panel.entry.focus_set()
        elif selected == str(self.chat_page):
            self.entry.focus_set()

    def append_message(self, speaker, text):
        self.chat.configure(state="normal")
        self.chat.insert("end", f"{speaker}\n", speaker)
        self.chat.insert("end", f"{text}\n\n")
        if int(self.chat.index("end-1c").split(".")[0]) > 500:
            self.chat.delete("1.0", "100.0")
        self.chat.configure(state="disabled")
        self.chat.see("end-3c")

    def render_conversation(self):
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.configure(state="disabled")
        _, turns = self.service.history.snapshot()
        if turns:
            for turn in turns:
                self.append_message("나", turn["user"])
                self.append_message("루나", turn["assistant"])
        else:
            self.append_message("루나", "안녕하세요. 루나예요.")

    def new_conversation(self, *, confirm=True):
        if self.command_busy:
            self.status_var.set("현재 요청이 끝난 뒤 새 대화를 시작해 주세요.")
            return False
        _, turns = self.service.history.snapshot()
        if confirm and turns and not messagebox.askyesno("새 대화", "최근 대화를 초기화할까요? PC에 저장한 최근 대화도 비워집니다.", parent=self.root):
            return False
        try:
            self.service.clear_conversation()
        except OSError:
            self.show_error(UserError("저장된 대화를 지우지 못했어요. 데이터 폴더 권한을 확인해 주세요."))
            return False
        self.render_conversation()
        self.clear_attachments()
        self.status_var.set("새 대화를 시작했어요.")
        return True

    def apply_settings(self):
        previous = self.settings
        self.settings = load_settings(self.data_dir)
        self.search_panel.reflect_settings([key for key in ("search_provider", "search_kind") if previous.get(key) != self.settings.get(key)])
        changed = self.service.sync_settings(self.settings)
        self.speak_var.set(self.settings.get("speak", True))
        self.voice_var.set(self.settings.get("voice", ""))
        self.voice_speed = self.settings.get("voice_speed", 1.0)
        self.mode_var.set({"off": "기본 명령", "local": "Ollama 로컬", "cloud": "Ollama 클라우드", "openai": "ChatGPT · OpenAI"}.get(self.settings.get("ai_mode", "off"), "기본 명령") + " · 데스크톱")
        if not self.speak_var.get():
            self.stop_speech()
        if self.selected_voice():
            self.voice_note.set(self._voice_description(self.selected_voice()))
        if changed:
            self.render_conversation()
            self.attachments = []
            self.refresh_attachments()
            self.status_var.set("연결 방식 또는 모델이 바뀌어 새 대화를 시작했어요.")
        self.configure_wake()

    def run_task(self, work, success, error=None):
        # Keep every Tk callback on the UI thread. Workers must hold no Tk objects,
        # even indirectly: releasing a final Tk reference off-thread can abort Tcl.
        gc.collect()
        self.next_job += 1
        identifier = self.next_job
        self.callbacks[identifier] = (success, error or self.show_error)
        results = self.jobs
        def run():
            try:
                value = work()
                results.put((identifier, True, value))
            except Exception as exception:
                safe_error = UserError(str(exception)) if isinstance(exception, UserError) else UserError("처리 중 오류가 발생했어요. 장치 연결과 데이터 폴더 권한을 확인해 주세요.")
                results.put((identifier, False, safe_error))
        threading.Thread(target=run, daemon=True).start()

    def _poll(self):
        if self.closed:
            return
        self.gc_ticks += 1
        if self.gc_ticks % 20 == 0:
            gc.collect()
        try:
            while True:
                self.status_var.set(self.analysis_progress.get_nowait())
        except queue.Empty:
            pass
        try:
            while True:
                message = self.listen_progress.get_nowait()
                if self.listening and not self.wake_listening and not self.wake_pending:
                    self.status_var.set(message)
        except queue.Empty:
            pass
        try:
            while True:
                identifier, succeeded, value = self.jobs.get_nowait()
                callbacks = self.callbacks.pop(identifier, None)
                if callbacks is None:
                    continue
                callback = callbacks[0 if succeeded else 1]
                try:
                    callback(value)
                    if self.closed:
                        return
                except Exception:
                    self.show_error(UserError("화면을 갱신하지 못했어요. 앱을 다시 시작해 주세요."))
        except queue.Empty:
            pass
        self.sync_wake_availability()
        self.poll_wake()
        self.sync_wake_availability()
        self._poll_id = self.root.after(60, self._poll)

    def show_error(self, error):
        self.status_var.set(str(error).splitlines()[0][:160] if str(error) else "처리 중 오류가 발생했어요.")
        self.append_message("오류", str(error))

    def submit(self, text=None, *, from_voice=False):
        if self.command_busy or self.listening or self.audio_checking:
            return
        text = (self.command_var.get() if text is None else text).strip()
        if not text:
            return
        self.stop_speech()
        self.command_busy = True
        self.sync_wake_availability()
        self.voice_command = from_voice
        self.command_var.set("")
        self.send_button.configure(state="disabled")
        self.mic_button.configure(state="disabled")
        self.append_message("나", text)
        self.status_var.set("부탁하신 내용을 확인하고 있어요…")
        self.run_task(functools.partial(self.service.execute, text, attachments=tuple(self.attachments), progress=self.analysis_progress.put, view=self.view_context()), self._command_done, self._command_failed)

    def view_context(self):
        selected = self.tree.selection()
        event = self.visible_events.get(selected[0]) if selected else None
        return {"calendar_day": self.day_var.get(), "selected_event": dict(event) if event else None,
                "settings": load_settings(self.data_dir),
                "attachments": [{"index": index + 1, "name": item.name} for index, item in enumerate(self.attachments)],
                "voices": [{key: item.get(key, "") for key in ("name", "culture", "gender")} for item in self.voice_items[:40]]}

    def _command_failed(self, error):
        was_voice = self.voice_command
        self.command_busy = False
        self.voice_command = False
        self.send_button.configure(state="normal")
        self.mic_button.configure(state="normal")
        self.show_error(error)
        if was_voice:
            self.say(str(error))

    def _command_done(self, result):
        was_voice = self.voice_command
        proposal = result.get("proposal")
        if proposal and self.voice_command and proposal.get("voice_auto", False) and not proposal.get("windows_command"):
            # The spoken request authorizes the validated ordinary app action.
            # Execute the existing validated, one-use proposal without another prompt.
            self.status_var.set("음성 명령을 실행하고 있어요…")
            self.run_task(functools.partial(self.service.confirm_proposal, proposal["id"]), self._command_done, self._command_failed)
            return
        if result.get("draft") and self.voice_command:
            self.status_var.set("말씀하신 일정을 저장하고 있어요…")
            self.run_task(functools.partial(self.service.save_draft, result["draft"], result.get("history_turn"), generation=result.get("history_generation")), self._command_done, self._command_failed)
            return
        self.command_busy = False
        self.send_button.configure(state="normal")
        self.mic_button.configure(state="normal")
        self.status_var.set(self.service.history.warning or "준비됐어요. 무엇을 도와드릴까요?")
        if result.get("settings_changed"):
            self.apply_settings()
            if result.get("conversation_changed"):
                self.render_conversation()
                self.clear_attachments()
            if self.config_dialog is not None and self.config_dialog.winfo_exists():
                self.config_dialog.reflect_settings(result["settings_changed"])
            self.search_panel.reflect_settings(result["settings_changed"])
        self.append_message("루나", result["message"])
        if not result.get("ui_actions"):
            self.say(result["message"])
        if result.get("calendar_changed"):
            if result.get("calendar_day"):
                self.day_var.set(result["calendar_day"])
            self.refresh_events()
        if result.get("programs_changed") and self.config_dialog is not None and self.config_dialog.winfo_exists():
            self.config_dialog.refresh_programs()
        if result.get("saved_event"):
            self.day_var.set(result["saved_event"]["start"][:10])
            self.refresh_events()
        if result.get("open_settings"):
            self.open_config(result.get("settings_tab"))
        if result.get("search"):
            self.open_search(result["search"])
        if result.get("draft"):
            self.open_event(result["draft"], result.get("history_turn"))
        if proposal:
            if self.confirm_actions(proposal):
                self.command_busy = True
                self.send_button.configure(state="disabled")
                self.mic_button.configure(state="disabled")
                self.status_var.set("확인한 동작을 실행하고 있어요…")
                self.run_task(functools.partial(self.service.confirm_proposal, proposal["id"]), self._command_done, self._command_failed)
            else:
                self.service.discard_proposal(proposal["id"])
                self.append_message("루나", "취소했어요. 제안된 동작은 실행하지 않았습니다.")
                self.voice_command = False
        else:
            self.voice_command = False
        for directive in result.get("ui_actions", []):
            try:
                self.apply_ui_action(directive, from_voice=was_voice)
                if self.closed:
                    return
            except (UserError, OSError, ValueError, tk.TclError) as error:
                self.show_error(error)
                if was_voice:
                    self.say(str(error))

    def apply_ui_action(self, args, *, from_voice=False):
        kind = args["action"]
        if kind in {"open_settings", "test_connection", "manage_api_key", "delete_api_key", "restart_admin", "register_program"}:
            tab = {"register_program": "programs", "restart_admin": "windows"}.get(kind, args.get("tab", "ai"))
            self.open_config(tab)
            dialog = self.config_dialog
            if kind == "register_program":
                dialog.app_name.set(args.get("name", ""))
                before = {item["id"] for item in self.service.programs.catalog()}
                dialog.program_path = ""
                dialog.choose_program()
                if dialog.program_path:
                    dialog.register_program()
                    added = [item for item in self.service.programs.catalog() if item["id"] not in before]
                    if added:
                        message = f"‘{added[0]['name']}’ 프로그램을 등록했어요."
                        self.append_message("루나", message)
                        self.say(message)
                    else:
                        raise UserError(dialog.program_feedback.get())
                else:
                    self.status_var.set("프로그램 파일 선택을 취소했어요.")
            elif kind == "test_connection":
                dialog.test_connection()
            elif kind == "manage_api_key":
                if "persist" in args:
                    dialog.persist.set(args["persist"])
                dialog.key_entry.focus_set()
            elif kind == "delete_api_key":
                dialog.delete_key()
            elif kind == "restart_admin":
                dialog.restart_admin()
        elif kind == "close_settings":
            if self.config_dialog is not None:
                self.config_dialog.close()
        elif kind == "save_settings":
            if self.config_dialog is not None and not self.config_dialog.save():
                raise UserError(self.config_dialog.save_feedback.get())
        elif kind == "open_search":
            self.open_search()
        elif kind == "listen":
            self.listen()
        elif kind == "add_event":
            self.open_event()
        elif kind == "attach_files":
            self.choose_attachments()
        elif kind == "show_attachments":
            self.open_attachments()
        elif kind == "clear_attachments":
            self.clear_attachments()
            self.status_var.set("첨부를 비웠어요.")
        elif kind == "remove_attachment":
            index = args["index"] - 1
            if not 0 <= index < len(self.attachments):
                raise UserError("해당 번호의 첨부가 없어요.")
            self.attachments.pop(index)
            self.refresh_attachments()
        elif kind == "summarize_attachments":
            self.summarize_attachments()
        elif kind == "new_conversation":
            self.new_conversation(confirm=False)
        elif kind == "stop_speech":
            self.stop_speech()
        elif kind == "test_voice":
            if self.config_dialog is not None:
                self.config_dialog.test_voice()
            else:
                self.say("안녕하세요. 당신의 개인 비서 루나예요.", force=True)
        elif kind == "check_audio":
            self.open_config()
            self.config_dialog.notebook.select(2)
            self.config_dialog.check_audio()
        elif kind == "install_voice":
            self.install_voice()
        elif kind == "install_wake":
            self.install_wake()
        elif kind == "show_calendar":
            self.day_var.set(args.get("day", self.day_var.get()))
            self.refresh_events()
        elif kind == "export_calendar_dialog":
            self.export_calendar()
        elif kind == "close_app":
            self.close()

    def confirm_actions(self, proposal):
        if proposal.get("windows_command"):
            from .command_dialog import CommandDialog
            dialog = CommandDialog(self.root, proposal["summary"])
            self.root.wait_window(dialog)
            return dialog.approved
        return messagebox.askyesno("AI 동작 확인", proposal["summary"] + "\n\n이 순서로 실행할까요?", parent=self.root)

    def open_config(self, tab=None):
        if self.config_dialog is not None and self.config_dialog.winfo_exists():
            self.config_dialog.lift()
        else:
            self.config_dialog = ConfigDialog(self)
        if tab in {"ai", "programs", "sound", "windows"}:
            self.config_dialog.notebook.select({"ai": 0, "programs": 1, "sound": 2, "windows": 3}[tab])

    def open_search(self, spec=None):
        if spec:
            self.search_panel.set_spec(spec)
            self.search_panel.search()
        else:
            self.main_tabs.select(self.search_panel)
            self.search_panel.entry.focus_set()

    def choose_attachments(self):
        if self.command_busy:
            return
        from .attachments import MAX_FILES, read_files
        filenames = filedialog.askopenfilenames(parent=self.root, title="분석할 문서·이미지 선택 (파일당 최대 20MB)",
                     filetypes=[("문서·이미지", "*.pdf *.pptx *.ppt *.xlsx *.xls *.docx *.txt *.md *.csv *.json *.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff"), ("모든 파일", "*.*")])
        if not filenames:
            return
        if len(filenames) + len(self.attachments) > MAX_FILES:
            self.show_error(UserError("첨부는 5개까지 사용할 수 있어요. 기존 첨부를 비우거나 일부를 제거하세요."))
            return
        self.command_busy = True
        self.send_button.configure(state="disabled")
        self.attach_button.configure(state="disabled")
        self.status_var.set("첨부 파일에서 텍스트·이미지를 읽고 있어요…")
        self.run_task(functools.partial(read_files, tuple(filenames)), self.attachments_loaded, self.attachments_failed)

    def summarize_attachments(self):
        if not self.attachments:
            self.show_error(UserError("먼저 요약할 문서나 이미지를 첨부하세요."))
            return
        self.submit("첨부 자료의 핵심 내용과 중요한 수치·결론을 한국어로 요약해 줘")

    def attachments_loaded(self, result):
        from .attachments import validate_attachments
        self.command_busy = False
        self.send_button.configure(state="normal")
        self.attach_button.configure(state="normal")
        errors = list(result["errors"])
        for item in result["items"]:
            try:
                validate_attachments(self.attachments + [item])
                self.attachments.append(item)
            except UserError as error:
                errors.append(f"{item.name}: {error}")
        self.refresh_attachments()
        if errors:
            self.show_error(UserError("\n".join(errors)))
        else:
            self.status_var.set("첨부 준비 완료. ‘첨부 요약’을 누르거나 질문을 입력하세요.")
        self.open_attachments()

    def attachments_failed(self, error):
        self.command_busy = False
        self.send_button.configure(state="normal")
        self.attach_button.configure(state="normal")
        self.show_error(error)

    def refresh_attachments(self):
        if self.attachments:
            names = ", ".join(item.name for item in self.attachments)
            self.attachment_note.set(f"첨부 {len(self.attachments)}개 · {names[:90]}")
        else:
            self.attachment_note.set("첨부 없음")
        if self.attachment_dialog is not None and self.attachment_dialog.winfo_exists():
            self.attachment_dialog.refresh()

    def clear_attachments(self):
        if not self.command_busy:
            self.attachments = []
            self.refresh_attachments()

    def open_attachments(self):
        from .attachment_dialog import AttachmentDialog
        if self.attachment_dialog is not None and self.attachment_dialog.winfo_exists():
            self.attachment_dialog.lift()
        else:
            self.attachment_dialog = AttachmentDialog(self)

    def open_event(self, draft=None, turn_id=None):
        if self.last_dialog is not None and self.last_dialog.winfo_exists():
            self.last_dialog.lift()
            return
        self.last_dialog = EventDialog(self, draft, turn_id)

    def refresh_events(self):
        try:
            events = self.service.events_on(self.day_var.get())
        except (ValueError, OSError):
            self.show_error(UserError("날짜를 YYYY-MM-DD 형식으로 입력해 주세요."))
            return
        self.visible_events = {event["id"]: event for event in events}
        self.tree.delete(*self.tree.get_children())
        for event in events:
            self.tree.insert("", "end", iid=event["id"], values=(event["start"][11:], event["title"]))
        self.event_detail.set(f"일정 {len(events)}개" if events else "")
        if events:
            self.calendar_empty.place_forget()
        else:
            self.calendar_empty.place(relx=.48, rely=.45, anchor="center")

    def shift_day(self, offset):
        try:
            date = dt.date.fromisoformat(self.day_var.get()) + dt.timedelta(days=offset)
        except (ValueError, OverflowError):
            self.show_error(UserError("올바른 날짜를 먼저 입력해 주세요."))
            return
        self.day_var.set(date.isoformat())
        self.refresh_events()

    def _event_selected(self, _event):
        selection = self.tree.selection()
        if selection and selection[0] in self.visible_events:
            event = self.visible_events[selection[0]]
            self.event_detail.set(f"{event['start'].replace('T', ' ')} — {event['end'].replace('T', ' ')}")

    def delete_event(self):
        selection = self.tree.selection()
        if not selection:
            self.status_var.set("삭제할 일정을 먼저 선택해 주세요.")
            return
        event = self.visible_events[selection[0]]
        if not messagebox.askyesno("일정 삭제", f"‘{event['title']}’ 일정을 삭제할까요?", parent=self.root):
            return
        try:
            self.service.calendar.delete(event["id"])
            self.refresh_events()
            self.append_message("루나", "일정을 삭제했어요.")
        except (UserError, OSError) as error:
            self.show_error(error)

    def export_calendar(self):
        filename = filedialog.asksaveasfilename(parent=self.root, title="전체 일정 내보내기", defaultextension=".ics", initialfile="luna-calendar.ics", filetypes=[("캘린더 파일", "*.ics")])
        if filename:
            try:
                Path(filename).write_bytes(self.service.calendar.export().encode("utf-8"))
                self.status_var.set("전체 일정을 내보냈어요.")
            except OSError:
                self.show_error(UserError("파일을 저장할 수 없어요. 폴더의 쓰기 권한을 확인해 주세요."))

    def listen(self):
        if self.listening or self.command_busy or self.audio_checking:
            return
        self.listening = True
        self.sync_wake_availability()
        self.stop_speech()
        self.mic_button.configure(state="disabled", text="듣는 중…")
        self.send_button.configure(state="disabled")
        self.status_var.set("마이크를 준비하고 있어요… ‘지금 말씀하세요’가 표시되면 말해 주세요.")
        while not self.listen_progress.empty():
            self.listen_progress.get_nowait()
        recognizer = (functools.partial(recognize_once, progress=self.listen_progress.put)
                      if self.recognizer is recognize_once else self.recognizer)
        self.run_task(functools.partial(recognize_locked, recognizer, self.microphone_lock), self._heard, self._listen_failed)

    def configure_wake(self):
        if self.wake is not None and self.wake.failed:
            # Settings save / device reconnection should allow a single retry.
            self.stop_wake()
        if not self.settings.get("wake_enabled", True):
            self.stop_wake()
            self.wake_note.set("‘루나’ 호출 감지 꺼짐 · 마이크 버튼 사용 가능")
        elif os.name != "nt":
            self.wake_note.set("‘루나’ 호출 감지는 Windows에서 사용할 수 있어요.")
        elif not model_ready(self.data_dir):
            self.wake_note.set("‘루나’ 호출어 모델 설치 필요 · 설정 → 소리 · 음성")
        elif self.wake is None:
            self.wake_note.set("‘루나’ 호출어 모델을 불러오는 중…")
            self.wake = WakeListener(self.data_dir, self.microphone_lock)
            self.wake.start()
            self.sync_wake_availability()

    def stop_wake(self):
        self.wake_job += 1
        if self.wake is not None:
            self.wake.close()
            self.wake = None
        if self.wake_listening or self.wake_pending:
            self.wake_listening = False
            self.wake_pending = False
            self.listening = False
            self.mic_button.configure(state="normal", text="마이크")
            self.send_button.configure(state="normal")

    def sync_wake_availability(self):
        if self.wake is None or self.wake.failed:
            return
        available = (not self.closed and not self.command_busy and not self.audio_checking and (not self.listening or self.wake_listening)
                     and (not self.speech_active or self.settings.get("wake_during_speech", False))
                     and self.root.grab_current() is None)
        if not available and self.wake_listening:
            self.wake_listening = False
            self.listening = False
            self.mic_button.configure(state="normal", text="마이크")
            self.send_button.configure(state="disabled" if self.command_busy else "normal")
        self.wake.set_available(available)
        if not available:
            self.wake_note.set("‘루나’ 호출 감지 일시 정지")

    def poll_wake(self):
        listener = self.wake
        if listener is None:
            return
        while True:
            try:
                epoch, kind, value = listener.events.get_nowait()
            except queue.Empty:
                return
            if self.closed or listener is not self.wake or epoch != listener.epoch:
                continue
            if kind == "error":
                self.wake_note.set(value)
                if self.wake_listening:
                    self.wake_listening = False
                    self._listen_failed(UserError(value))
                else:
                    self.status_var.set(value)
            elif kind == "ready":
                self.wake_note.set("‘루나’라고 불러 주세요 · 마이크에서 호출어 감지 중")
            elif kind == "wake":
                if self.command_busy or self.listening:
                    continue
                self.wake_listening = True
                self.listening = True
                self.stop_speech()
                self.mic_button.configure(state="disabled", text="듣는 중…")
                self.send_button.configure(state="disabled")
                self.status_var.set("듣고 있어요. 명령을 말씀해 주세요.")
            elif kind == "audio" and self.wake_listening:
                self.wake_listening = False
                self.wake_pending = True
                self.wake_job += 1
                job = self.wake_job
                listener.set_available(False)
                self.status_var.set("말씀하신 명령을 인식하고 있어요…")

                def heard(text, token=job):
                    if not self.closed and token == self.wake_job:
                        self._heard(text)

                def failed(error, token=job):
                    if not self.closed and token == self.wake_job:
                        self._listen_failed(error)

                self.run_task(functools.partial(transcribe_command, value), heard, failed)
            elif kind == "timeout" and self.wake_listening:
                self.wake_listening = False
                listener.set_available(False)
                self._listen_failed(UserError(value))

    def install_wake(self):
        if self.installing_wake:
            return
        self.installing_wake = True
        self.wake_note.set("한국어 호출어 모델 다운로드 중… 설치 후 호출 감지는 오프라인입니다.")
        self.refresh_voice_settings()
        self.run_task(functools.partial(install_wake_model, self.data_dir), self._wake_installed, self._wake_install_failed)

    def _wake_installed(self, _path):
        self.installing_wake = False
        self.stop_wake()
        self.configure_wake()
        self.refresh_voice_settings()

    def _wake_install_failed(self, error):
        self.installing_wake = False
        self.wake_note.set(str(error))
        self.refresh_voice_settings()

    def _heard(self, text):
        self.wake_pending = False
        self.listening = False
        self.mic_button.configure(state="normal", text="마이크")
        self.send_button.configure(state="normal")
        if not isinstance(text, str) or not text.strip():
            self.show_error(UserError("인식된 명령이 없어요. 다시 말씀해 주세요."))
            return
        self.submit(text.strip(), from_voice=True)

    def _listen_failed(self, error):
        self.wake_pending = False
        self.listening = False
        self.mic_button.configure(state="normal", text="마이크")
        self.send_button.configure(state="normal")
        self.show_error(error)

    def _voices_loaded(self, voices):
        self.voice_items = voices
        name = preferred_voice(voices, self.voice_var.get() or self.settings.get("voice", ""))
        if name:
            self.voice_var.set(name)
            self.voice_note.set(self._voice_description(name))
        else:
            self.voice_note.set("사용 가능한 한국어 여성 음성이 없어요. 설정 → 소리 · 음성에서 ‘로컬 음성 설치’를 누르거나 설치된 목소리를 선택하세요.")
        self.refresh_voice_settings()

    def refresh_voice_settings(self):
        if self.config_dialog is not None and self.config_dialog.winfo_exists():
            self.config_dialog.refresh_voices()

    def _voice_description(self, name):
        if name.startswith("local:"):
            ready = all((self.data_dir / "models" / MODEL_NAME / filename).is_file() for filename in MODEL_FILES)
            return "Supertonic 한국어 여성 음성 · 모델 설치 완료 · 오프라인 출력" if ready else "Supertonic 한국어 여성 음성 · ‘로컬 음성 설치’를 한 번 눌러 모델을 받으세요 (약 81MB)."
        return "Windows에 설치된 음성을 사용해요. ‘들어보기’로 확인해 주세요."

    def install_voice(self):
        if self.installing_voice:
            return
        self.installing_voice = True
        self.refresh_voice_settings()
        self.voice_note.set("로컬 모델 다운로드·SHA-256 검증 중… 약 81MB이며 설치 후에는 오프라인으로 출력합니다.")
        self.run_task(functools.partial(install_model, self.data_dir), self._voice_installed, self._voice_install_failed)

    def _voice_installed(self, _path):
        self.installing_voice = False
        self.settings["voice"] = "local:F1"
        self.voice_var.set("local:F1")
        self.run_task(self.voice.list, self._installed_voices_loaded, self._voice_install_failed)

    def _installed_voices_loaded(self, voices):
        self._voices_loaded(voices)
        if self.config_dialog is not None and self.config_dialog.winfo_exists():
            self.config_dialog.voice_var.set(self.selected_voice())
        self.persist_settings()

    def _voice_install_failed(self, error):
        self.installing_voice = False
        self.refresh_voice_settings()
        self.voice_note.set(str(error))

    def selected_voice(self):
        return self.voice_var.get()

    def persist_settings(self):
        try:
            name = self.selected_voice() if self.voice_items else self.settings.get("voice", "")
            save_settings(self.data_dir, name, self.speak_var.get())
            if name:
                self.voice_note.set(self._voice_description(name))
        except OSError:
            self.voice_note.set("음성 설정을 저장하지 못했어요. 데이터 폴더의 쓰기 권한을 확인해 주세요.")

    def _toggle_speech(self):
        self.persist_settings()
        if not self.speak_var.get():
            self.stop_speech()

    def stop_speech(self):
        self.speech_generation += 1
        self.speech_active = False
        self.voice.stop()

    def check_audio(self, voice_name, speak_enabled, success, failure):
        if self.audio_checking or self.listening or self.command_busy:
            failure(UserError("진행 중인 명령·음성 인식·점검이 끝난 뒤 다시 점검하세요."))
            return
        self.audio_checking = True
        self.sync_wake_availability()
        self.stop_speech()
        self.mic_button.configure(state="disabled")
        self.send_button.configure(state="disabled")
        self.status_var.set("음성 장치 점검 중… 잠시 말씀해 주세요. 오디오는 외부로 전송하지 않습니다.")

        def done(value, failed=False):
            self.audio_checking = False
            self.mic_button.configure(state="normal")
            self.send_button.configure(state="normal")
            self.status_var.set("음성 점검을 마쳤어요. 설정 창에서 결과를 확인하세요.")
            self.sync_wake_availability()
            (failure if failed else success)(value)

        check = functools.partial(diagnose_audio, self.data_dir, voice_name, speak_enabled)
        self.run_task(functools.partial(recognize_locked, check, self.microphone_lock), done,
                      functools.partial(done, failed=True))

    def say(self, text, force=False, name=None, speed=None):
        if not force and not self.speak_var.get():
            return
        name = self.selected_voice() if name is None else name
        if not name:
            self.voice_note.set("사용할 음성이 없어요. 설정 → 소리 · 음성에서 로컬 음성을 설치하거나 목소리를 선택하고 저장하세요.")
            self.status_var.set("음성 출력 준비 필요 · 설정에서 목소리를 선택하세요.")
            return
        if self.listening or self.audio_checking:
            return
        speed = self.voice_speed if speed is None else speed
        self.speech_generation += 1
        generation = self.speech_generation
        self.speech_active = True
        self.voice_note.set("음성을 준비하고 있어요… 로컬 음성의 첫 실행은 모델을 불러오는 시간이 필요합니다.")
        self.sync_wake_availability()

        def finished(_value):
            if generation == self.speech_generation:
                self.speech_active = False
                self.voice_note.set("음성 재생 처리를 마쳤어요. 소리가 안 들리면 설정 → 소리 · 음성에서 음성 점검을 실행하세요.")

        def failed(error):
            if not self.closed and generation == self.speech_generation:
                self.speech_active = False
                self.voice_note.set(str(error))
                self.status_var.set("음성 출력 오류: " + str(error).splitlines()[0][:140])

        # Reserve on the UI thread. A worker that starts late must keep its old
        # ticket rather than cancelling or reviving speech for a newer command.
        try:
            work = self.voice.prepare(text, name, speed=speed)
        except (UserError, OSError, RuntimeError) as error:
            failed(error)
            return
        self.run_task(work, finished, failed)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stop_wake()
        self.voice.close()
        self.callbacks.clear()
        self.last_dialog = None
        self.config_dialog = None
        self.attachment_dialog = None
        self.attachments = []
        self.root.after_cancel(self._poll_id)
        self.root.destroy()
        gc.collect()


def main():
    parser = argparse.ArgumentParser(description="LUNA native desktop assistant")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    parser.add_argument("--voice-sample", type=Path, help="Generate an offline Korean WAV without opening the UI")
    options = parser.parse_args()
    if options.voice_sample:
        from .local_voice import LocalVoice
        LocalVoice(options.data_dir).synthesize("안녕하세요. 개인 비서 루나입니다.", filename=options.voice_sample)
        return
    if os.name == "nt":
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (OSError, AttributeError):
            pass
    root = tk.Tk()
    try:
        LunaApp(root, options.data_dir)
    except Exception:
        messagebox.showerror("LUNA 시작 오류", "앱을 시작하지 못했습니다. 데이터 폴더의 쓰기 권한과 Python의 Tcl/Tk 설치를 확인해 주세요.", parent=root)
        root.destroy()
        raise
    root.mainloop()


if __name__ == "__main__":
    main()
