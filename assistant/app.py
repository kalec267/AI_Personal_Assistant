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

BG, PAPER, INK, MUTED, BLUE, LINE = "#f4f6fb", "#ffffff", "#253047", "#7b879e", "#526de0", "#e5eaf3"


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
        self.listening = False
        self.voice_items = []
        self.installing_voice = False
        self.last_dialog = None
        self.config_dialog = None
        self.search_dialog = None
        self.attachment_dialog = None
        self.attachments = []
        self.analysis_progress = queue.Queue()
        self.attachment_note = tk.StringVar(value="첨부한 파일은 현재 대화에서만 유지됩니다.")
        self.mode_var = tk.StringVar(value={"off": "기본 명령", "local": "Ollama 로컬", "cloud": "Ollama 클라우드"}.get(self.settings.get("ai_mode", "off"), "기본 명령") + " · 데스크톱")
        self.command_var = tk.StringVar()
        self.day_var = tk.StringVar(value=dt.date.today().isoformat())
        self.status_var = tk.StringVar(value="준비됐어요. 무엇을 도와드릴까요?")
        self.voice_var = tk.StringVar(value=self.settings.get("voice", ""))
        self.voice_note = tk.StringVar(value="Windows 음성 목록을 불러오는 중…")
        self.speak_var = tk.BooleanVar(value=self.settings.get("speak", True))
        self.root.title("LUNA · 나만의 개인 비서")
        self.root.geometry("1180x760")
        self.root.minsize(960, 620)
        self.root.configure(bg=BG)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._style()
        self._build()
        self.root.bind("<Configure>", self._resize_layout, add="+")
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

    @staticmethod
    def label(parent, text, *, size=11, color=INK, bold=False):
        return tk.Label(parent, text=text, font=("Malgun Gothic", size, "bold" if bold else "normal"), bg=parent.cget("bg"), fg=color, anchor="w", justify="left")

    @staticmethod
    def button(parent, text, command, primary=False):
        return tk.Button(parent, text=text, command=command, bg=BLUE if primary else "#f0f3fc", fg="white" if primary else "#566a9c",
                         activebackground="#435bc5" if primary else "#e5ebfb", activeforeground="white" if primary else INK,
                         relief="flat", bd=0, padx=13, pady=9, cursor="hand2", font=("Malgun Gothic", 10), takefocus=True)

    def _style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TEntry", fieldbackground=PAPER, padding=6, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
        style.configure("TCombobox", padding=5, fieldbackground=PAPER, background=BG)
        style.configure("Treeview", background=PAPER, fieldbackground=PAPER, foreground=INK, rowheight=37, borderwidth=0, font=("Malgun Gothic", 10))
        style.configure("Treeview.Heading", background="#f1f4fa", foreground=MUTED, font=("Malgun Gothic", 9), padding=8, relief="flat")
        style.map("Treeview", background=[("selected", "#e5eaff")], foreground=[("selected", "#344caa")])

    def panel(self, parent, index, title):
        frame = tk.Frame(parent, bg=PAPER, padx=20, pady=17, highlightbackground=LINE, highlightcolor=LINE, highlightthickness=1)
        self.label(frame, index, size=8, color=MUTED).pack(anchor="w")
        self.label(frame, title, size=16, bold=True).pack(anchor="w", pady=(5, 13))
        return frame

    def _build(self):
        header = tk.Frame(self.root, bg=BG, padx=28, pady=18)
        header.grid(row=0, column=0, sticky="ew")
        self.label(header, "LUNA ✦", size=23, bold=True).pack(side="left")
        self.label(header, "내 일상에, 조금 더 여유를.", size=10, color=MUTED).pack(side="left", padx=22)
        self.settings_button = self.button(header, "설정", self.open_config)
        self.settings_button.pack(side="right")
        self.search_button = self.button(header, "뉴스·동영상 검색", self.open_search)
        self.search_button.pack(side="right", padx=8)
        tk.Label(header, textvariable=self.mode_var, bg=BG, fg=BLUE, font=("Malgun Gothic", 9)).pack(side="right", padx=15)
        body = tk.Frame(self.root, bg=BG, padx=28)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=3, uniform="panels")
        body.columnconfigure(1, weight=2, uniform="panels")
        body.rowconfigure(0, weight=1)
        left = self.panel(body, "01 / ASSISTANT", "오늘도 함께할게요.")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        orb = tk.Canvas(left, height=65, bg=PAPER, highlightthickness=0)
        self.orb = orb
        orb.pack(fill="x")
        orb.bind("<Configure>", self._draw_orb)
        tk.Label(left, textvariable=self.status_var, bg=PAPER, fg=MUTED, font=("Malgun Gothic", 9), wraplength=460).pack(pady=(0, 12))
        transcript = tk.Frame(left, bg=PAPER, height=1)
        transcript.pack_propagate(False)
        transcript.pack(fill="both", expand=True)
        self.chat = tk.Text(transcript, bg="#f7f8fc", fg=INK, relief="flat", bd=0, wrap="word", font=("Malgun Gothic", 11), padx=15, pady=12, height=4, state="disabled")
        scroll = ttk.Scrollbar(transcript, command=self.chat.yview)
        self.chat.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.chat.pack(side="left", fill="both", expand=True)
        self.chat.tag_configure("루나", foreground=BLUE)
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
        note = self.label(left, "마이크: Google 음성 서비스에 오디오 전송.\nOllama 클라우드: 첨부에서 읽은 내용·이미지도 전송됩니다.", size=8, color=MUTED)
        note.configure(wraplength=470)
        note.pack(anchor="w", pady=(9, 0))
        self.privacy_note = note
        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        schedule = self.panel(right, "02 / YOUR DAY", "나의 일정")
        schedule.grid(row=0, column=0, sticky="nsew")
        day_row = tk.Frame(schedule, bg=PAPER)
        day_row.pack(fill="x", pady=(0, 10))
        self.button(day_row, "‹", lambda: self.shift_day(-1)).pack(side="left")
        day_entry = ttk.Entry(day_row, textvariable=self.day_var, width=12)
        day_entry.pack(side="left", padx=5, fill="x", expand=True)
        day_entry.bind("<Return>", lambda _event: self.refresh_events())
        self.button(day_row, "›", lambda: self.shift_day(1)).pack(side="left")
        self.button(day_row, "조회", self.refresh_events).pack(side="left", padx=(5, 0))
        tree_area = tk.Frame(schedule, bg=PAPER)
        self.tree = ttk.Treeview(tree_area, columns=("time", "title"), show="headings", selectmode="browse", height=5)
        self.tree.heading("time", text="시작")
        self.tree.heading("title", text="일정 이름")
        self.tree.column("time", width=64, minwidth=60, stretch=False)
        self.tree.column("title", width=200, minwidth=100)
        tree_scroll = ttk.Scrollbar(tree_area, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        self.event_detail = tk.StringVar(value="일정을 선택하면 종료 시간을 볼 수 있어요.")
        detail_label = tk.Label(schedule, textvariable=self.event_detail, bg=PAPER, fg=MUTED, font=("Malgun Gothic", 9), wraplength=360, justify="left", anchor="w")
        self.tree.bind("<<TreeviewSelect>>", self._event_selected)
        actions = tk.Frame(schedule, bg=PAPER)
        actions.pack(side="bottom", fill="x")
        detail_label.pack(side="bottom", fill="x", pady=8)
        tree_area.pack(fill="both", expand=True)
        self.button(actions, "+ 일정", self.open_event, primary=True).pack(side="left")
        self.button(actions, "삭제", self.delete_event).pack(side="left", padx=6)
        self.button(actions, "내보내기", self.export_calendar).pack(side="right")
        footer = self.label(self.root, "목소리 · 볼륨 · 프로그램 · 대화 기억을 상단 설정에서 나에게 맞게 바꿔 보세요.", size=9, color=MUTED)
        footer.grid(row=2, column=0, sticky="w", padx=28, pady=(14, 18))
        self.entry.focus_set()

    @staticmethod
    def _draw_orb(event):
        canvas = event.widget
        canvas.delete("all")
        x, y = event.width / 2, event.height / 2
        outer = min(30, max(10, y - 2))
        for radius, color in [(outer, "#eef1ff"), (outer * .8, "#dce3ff"), (outer * .6, "#bdccfc")]:
            canvas.create_oval(x-radius, y-radius, x+radius, y+radius, fill=color, outline="")
        canvas.create_text(x, y, text="✦", fill="white", font=("Segoe UI", max(12, round(outer * .73))))

    def _resize_layout(self, event):
        if event.widget is self.root:
            height = 30 if event.height < 700 else 65
            if int(self.orb.cget("height")) != height:
                self.orb.configure(height=height)

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
            self.append_message("루나", "안녕하세요. 당신의 개인 비서 루나예요. 일정을 정리하거나 PC를 제어해 보세요.")

    def new_conversation(self):
        if self.command_busy:
            self.status_var.set("현재 요청이 끝난 뒤 새 대화를 시작해 주세요.")
            return False
        _, turns = self.service.history.snapshot()
        if turns and not messagebox.askyesno("새 대화", "최근 대화를 초기화할까요? PC에 저장한 최근 대화도 비워집니다.", parent=self.root):
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
        self.settings = load_settings(self.data_dir)
        changed = self.service.sync_settings(self.settings)
        self.speak_var.set(self.settings.get("speak", True))
        self.voice_var.set(self.settings.get("voice", ""))
        self.mode_var.set({"off": "기본 명령", "local": "Ollama 로컬", "cloud": "Ollama 클라우드"}.get(self.settings.get("ai_mode", "off"), "기본 명령") + " · 데스크톱")
        if not self.speak_var.get():
            self.voice.stop()
        if self.selected_voice():
            self.voice_note.set(self._voice_description(self.selected_voice()))
        if changed:
            self.render_conversation()
            self.attachments = []
            self.refresh_attachments()
            self.status_var.set("연결 방식 또는 모델이 바뀌어 새 대화를 시작했어요.")

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
                identifier, succeeded, value = self.jobs.get_nowait()
                callbacks = self.callbacks.pop(identifier, None)
                if callbacks is None:
                    continue
                callback = callbacks[0 if succeeded else 1]
                try:
                    callback(value)
                except Exception:
                    self.show_error(UserError("화면을 갱신하지 못했어요. 앱을 다시 시작해 주세요."))
        except queue.Empty:
            pass
        self._poll_id = self.root.after(60, self._poll)

    def show_error(self, error):
        self.status_var.set(str(error).splitlines()[0][:160] if str(error) else "처리 중 오류가 발생했어요.")
        self.append_message("오류", str(error))

    def submit(self, text=None):
        if self.command_busy:
            return
        text = (self.command_var.get() if text is None else text).strip()
        if not text:
            return
        self.command_busy = True
        self.command_var.set("")
        self.send_button.configure(state="disabled")
        self.append_message("나", text)
        self.status_var.set("부탁하신 내용을 확인하고 있어요…")
        self.run_task(functools.partial(self.service.execute, text, attachments=tuple(self.attachments), progress=self.analysis_progress.put), self._command_done, self._command_failed)

    def _command_failed(self, error):
        self.command_busy = False
        self.send_button.configure(state="normal")
        self.show_error(error)

    def _command_done(self, result):
        self.command_busy = False
        self.send_button.configure(state="normal")
        self.status_var.set(self.service.history.warning or "준비됐어요. 무엇을 도와드릴까요?")
        self.append_message("루나", result["message"])
        self.say(result["message"])
        if result.get("open_settings"):
            self.open_config()
        if result.get("search"):
            self.open_search(result["search"])
        if result.get("draft"):
            self.open_event(result["draft"], result.get("history_turn"))
        if result.get("proposal"):
            proposal = result["proposal"]
            if self.confirm_actions(proposal):
                self.command_busy = True
                self.send_button.configure(state="disabled")
                self.status_var.set("확인한 동작을 실행하고 있어요…")
                self.run_task(functools.partial(self.service.confirm_proposal, proposal["id"]), self._command_done, self._command_failed)
            else:
                self.service.discard_proposal(proposal["id"])
                self.append_message("루나", "취소했어요. 제안된 동작은 실행하지 않았습니다.")

    def confirm_actions(self, proposal):
        if proposal.get("windows_command"):
            from .command_dialog import CommandDialog
            dialog = CommandDialog(self.root, proposal["summary"])
            self.root.wait_window(dialog)
            return dialog.approved
        return messagebox.askyesno("AI 동작 확인", proposal["summary"] + "\n\n이 순서로 실행할까요?", parent=self.root)

    def open_config(self):
        if self.config_dialog is not None and self.config_dialog.winfo_exists():
            self.config_dialog.lift()
            return
        self.config_dialog = ConfigDialog(self)

    def open_search(self, spec=None):
        from .search_dialog import SearchDialog
        if self.search_dialog is not None and self.search_dialog.winfo_exists():
            self.search_dialog.close()
        self.search_dialog = SearchDialog(self, spec)
        if spec:
            self.search_dialog.search()

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
            self.attachment_note.set(f"첨부 {len(self.attachments)}개 · {names[:90]}\n첨부 분석 모드 · 클릭하면 목록/읽기 안내 · PC 명령·검색은 비우기 후 사용")
        else:
            self.attachment_note.set("첨부한 파일은 현재 대화에서만 유지됩니다.")
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
        self.event_detail.set(f"일정 {len(events)}개 · 선택하면 전체 시간을 볼 수 있어요." if events else "이 날은 저장된 일정이 없어요. + 일정으로 추가해 보세요.")

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
        if self.listening:
            return
        self.listening = True
        self.voice.stop()
        self.mic_button.configure(state="disabled", text="듣는 중…")
        self.status_var.set("주변 소음 확인 후 듣습니다. 잠시 뒤 말씀해 주세요. 최대 10초 동안 인식해요.")
        self.run_task(self.recognizer, self._heard, self._listen_failed)

    def _heard(self, text):
        self.listening = False
        self.mic_button.configure(state="normal", text="마이크")
        self.command_var.set(text)
        self.entry.focus_set()
        self.status_var.set("인식한 내용을 확인하고 보내기 버튼을 눌러 주세요.")

    def _listen_failed(self, error):
        self.listening = False
        self.mic_button.configure(state="normal", text="마이크")
        self.show_error(error)

    def _voices_loaded(self, voices):
        self.voice_items = voices
        name = preferred_voice(voices, self.voice_var.get() or self.settings.get("voice", ""))
        if name:
            self.voice_var.set(name)
            self.voice_note.set(self._voice_description(name))
        else:
            self.voice_note.set("한국어 여성 음성이 없어요. Windows 한국어 음성 설치 후 앱을 다시 시작하거나 목록에서 직접 선택하세요.")
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
            self.voice.stop()

    def say(self, text, force=False, name=None):
        if not force and not self.speak_var.get():
            return
        name = self.selected_voice() if name is None else name
        if not name:
            if force:
                self.voice_note.set("먼저 사용할 음성을 선택해 주세요. 한국어 여성 음성은 Windows 설정에서 설치할 수 있어요.")
            return
        if self.listening:
            return
        self.run_task(functools.partial(self.voice.speak, text, name), lambda _value: None, lambda error: self.voice_note.set(str(error)))

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.voice.close()
        self.callbacks.clear()
        self.last_dialog = None
        self.config_dialog = None
        self.search_dialog = None
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
