"""One native settings window for AI, conversation, programs, sound and voice."""
import functools
import os
import re
import tkinter as tk
from tkinter import filedialog, ttk

from . import desktop, windows_control
from .core import UserError
from .settings import load_settings, save_settings, model_settings, selected_model
from .theme import PAPER, BLUE, MUTED

MODES = {"기본 명령 (AI 끔)": "off", "Ollama 로컬 · API 키 불필요": "local", "Ollama 클라우드 · API 키 필요": "cloud",
         "ChatGPT · OpenAI API 키 필요": "openai"}


class SettingsPage(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        canvas = tk.Canvas(self, highlightthickness=0, background=PAPER)
        scroll = ttk.Scrollbar(self, command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self.content = ttk.Frame(canvas, padding=20)
        item = canvas.create_window(0, 0, window=self.content, anchor="nw")
        self.content.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(item, width=event.width))


class ConfigDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("LUNA · 설정")
        self.configure(bg=PAPER)
        self.geometry("720x720")
        self.minsize(660, 600)
        self.transient(app.root)
        options = load_settings(app.data_dir)
        self.mode = tk.StringVar(value=next(label for label, value in MODES.items() if value == options.get("ai_mode", "off")))
        self._display_mode = options.get("ai_mode", "off")
        self._model_drafts = {}
        self.model = tk.StringVar(value=selected_model(options))
        self.vision_model = tk.StringVar(value=options.get(model_settings(self._display_mode)[1], ""))
        self.key = tk.StringVar()
        self.key_label = tk.StringVar(value=self.api_key_label())
        self.persist = tk.BooleanVar(value=os.name == "nt")
        self.remember = tk.BooleanVar(value=options.get("remember_chat", False))
        self.history_turns = tk.IntVar(value=options.get("history_turns", 10))
        self.speak = tk.BooleanVar(value=app.speak_var.get())
        self.wake_enabled = tk.BooleanVar(value=options.get("wake_enabled", True))
        self.wake_during_speech = tk.BooleanVar(value=options.get("wake_during_speech", False))
        self.voice_speed = tk.DoubleVar(value=options.get("voice_speed", 1.0))
        self.speed_label = tk.StringVar(value=f"{self.voice_speed.get():.2f}배")
        self.voice_var = tk.StringVar(value=app.selected_voice())
        self.volume = tk.DoubleVar(value=options.get("volume", 30))
        self.volume_label = tk.StringVar(value=f"{round(self.volume.get())}%")
        self.start_volume = tk.BooleanVar(value=options.get("apply_volume_on_start", False))
        self.windows_access = tk.BooleanVar(value=options.get("windows_control", False))
        self.windows_feedback = tk.StringVar()
        self.feedback = tk.StringVar(value=app.service.credentials_for(self._display_mode).status())
        self.mode.trace_add("write", self.mode_changed)
        self.save_feedback = tk.StringVar(value="변경 후 ‘설정 저장’을 눌러 적용하세요.")
        self.sound_feedback = tk.StringVar()
        self.program_feedback = tk.StringVar()
        self.app_name = tk.StringVar()
        self.program_path = ""
        self.path_label = tk.StringVar(value="실행 파일을 선택해 주세요.")
        footer = ttk.Frame(self, padding=(18, 10))
        footer.pack(side="bottom", fill="x")
        buttons = ttk.Frame(footer)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="닫기", command=self.close).pack(side="right")
        self.save_button = ttk.Button(buttons, text="설정 저장", command=self.save, style="Accent.TButton")
        self.save_button.pack(side="right", padx=8)
        ttk.Label(buttons, text="저장한 사용자 설정은 재실행 후에도 유지됩니다.").pack(side="left")
        ttk.Label(footer, textvariable=self.save_feedback, wraplength=640).pack(anchor="w", pady=(7, 0))
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=18, pady=(18, 0))
        pages = [SettingsPage(self.notebook) for _ in range(4)]
        for page, name in zip(pages, ("AI · 대화", "프로그램", "소리 · 음성", "Windows 제어")):
            self.notebook.add(page, text=name)
        self._build_ai(pages[0].content)
        self._build_programs(pages[1].content)
        self._build_sound(pages[2].content)
        self._build_windows(pages[3].content)
        self.refresh_programs()
        self.refresh_voices()
        self.protocol("WM_DELETE_WINDOW", self.close)

    @staticmethod
    def heading(parent, text):
        ttk.Label(parent, text=text, font=("Malgun Gothic", 16, "bold")).pack(anchor="w", pady=(0, 15))

    def _build_ai(self, frame):
        self.heading(frame, "AI 연결과 대화 기억")
        ttk.Label(frame, text="연결 방식").pack(anchor="w")
        mode = ttk.Combobox(frame, textvariable=self.mode, values=list(MODES), state="readonly")
        mode.pack(fill="x", pady=(5, 12))
        mode.bind("<<ComboboxSelected>>", self.mode_changed)
        ttk.Label(frame, text="모델 이름").pack(anchor="w")
        ttk.Entry(frame, textvariable=self.model).pack(fill="x", pady=(5, 10))
        ttk.Label(frame, text="Ollama 로컬: qwen3:4b · Ollama 클라우드: gpt-oss:120b\nChatGPT: gpt-4.1-mini · 계정에서 사용 가능한 모델 이름으로 변경할 수 있습니다.\nOpenAI API는 ChatGPT 구독과 별도 과금됩니다.", wraplength=580).pack(anchor="w", pady=(0, 12))
        ttk.Label(frame, text="이미지 분석 모델 · 빈칸이면 위 모델 사용").pack(anchor="w")
        ttk.Entry(frame, textvariable=self.vision_model).pack(fill="x", pady=(5, 8))
        ttk.Label(frame, text="이미지 입력 지원 모델이 필요합니다. Ollama 로컬 예: gemma3:4b (별도 다운로드).\nOpenAI gpt-4.1-mini는 이미지를 지원하므로 빈칸으로 사용할 수 있습니다.", wraplength=580).pack(anchor="w", pady=(0, 12))
        ttk.Label(frame, textvariable=self.key_label).pack(anchor="w")
        self.key_entry = ttk.Entry(frame, textvariable=self.key, show="●")
        self.key_entry.pack(fill="x", pady=5)
        ttk.Checkbutton(frame, text="이 Windows 사용자에 키 암호화 저장", variable=self.persist).pack(anchor="w")
        ttk.Label(frame, text="기존 키는 표시하지 않습니다. 빈칸은 선택한 서비스의 기존 키를 유지합니다.\n체크 해제 시 새 키는 현재 세션에서만 사용합니다. 연결 방식 변경 시 입력 중인 키는 비웁니다.\n키 발급: platform.openai.com/api-keys 또는 ollama.com/settings/keys", wraplength=580).pack(anchor="w", pady=8)
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(0, 15))
        ttk.Button(row, text="연결 테스트", command=self.test_connection).pack(side="left")
        ttk.Button(row, text="저장한 키 삭제", command=self.delete_key).pack(side="right")
        ttk.Separator(frame).pack(fill="x", pady=8)
        ttk.Label(frame, text="선택한 AI에 함께 보낼 최근 질문·답변 수").pack(anchor="w", pady=(8, 5))
        ttk.Spinbox(frame, from_=1, to=20, textvariable=self.history_turns, width=7).pack(anchor="w")
        ttk.Checkbutton(frame, text="최근 대화를 이 PC에 저장해 앱 재시작 후에도 이어가기", variable=self.remember).pack(anchor="w", pady=(10, 5))
        ttk.Label(frame, text="선택하지 않아도 앱을 사용하는 동안 최근 대화를 기억합니다.\n저장 기록은 암호화되지 않은 개인 파일이며, 클라우드 모드에서는 최근 대화도 전송됩니다.\n모델·연결 방식 변경 또는 ‘새 대화’를 누르면 문맥을 초기화합니다.", wraplength=580).pack(anchor="w", pady=8)
        ttk.Label(frame, text="클라우드 연결에서는 질문·최근 대화와 첨부 내용·이미지가 선택한 AI 서비스로 전송됩니다.", wraplength=580).pack(anchor="w", pady=5)
        ttk.Button(frame, text="새 대화 시작", command=self.app.new_conversation).pack(anchor="w", pady=5)
        ttk.Label(frame, textvariable=self.feedback, wraplength=580).pack(anchor="w", pady=12)

    def _build_programs(self, frame):
        self.heading(frame, "실행 프로그램")
        ttk.Label(frame, text="기본 등록 프로그램은 없습니다. .exe 파일과 음성으로 부를 이름을 직접 등록하세요.\n예: ‘크롬’으로 등록 → ‘루나, 크롬 열어줘’. AI 연결 없이도 실행할 수 있습니다.\n등록·삭제는 즉시 저장되며, 부를 이름은 서로 달라야 합니다.", wraplength=580).pack(anchor="w", pady=(0, 12))
        self.program_list = ttk.Treeview(frame, columns=("name",), show="headings", height=7, selectmode="browse")
        self.program_list.heading("name", text="프로그램 이름")
        self.program_list.pack(fill="x")
        self.program_empty = ttk.Label(frame, text="등록된 프로그램이 없어요. 아래에서 첫 프로그램을 추가해 주세요.", wraplength=580)
        ttk.Label(frame, text="부를 이름 (예: 크롬, 음악 앱)").pack(anchor="w", pady=(15, 5))
        ttk.Entry(frame, textvariable=self.app_name).pack(fill="x")
        ttk.Label(frame, textvariable=self.path_label, wraplength=580).pack(anchor="w", pady=10)
        row = ttk.Frame(frame)
        row.pack(fill="x")
        ttk.Button(row, text=".exe 선택", command=self.choose_program).pack(side="left")
        ttk.Button(row, text="등록", command=self.register_program).pack(side="left", padx=8)
        ttk.Button(row, text="선택한 등록 삭제", command=self.remove_program).pack(side="right")
        ttk.Label(frame, textvariable=self.program_feedback, wraplength=580).pack(anchor="w", pady=15)

    def _build_sound(self, frame):
        self.heading(frame, "목소리와 사운드")
        self.audio_check_button = ttk.Button(frame, text="음성 입력·출력 점검", command=self.check_audio)
        self.audio_check_button.pack(anchor="w", pady=(0, 8))
        self.audio_description = ttk.Label(frame, text="점검 중 1초 정도 말해 주세요. 기본 마이크·입력 레벨·음성 모델과 읽기 설정을 확인합니다.\n이 점검은 음성을 외부로 전송하거나 인식한 명령을 실행하지 않습니다.", wraplength=580)
        self.audio_description.pack(anchor="w", pady=(0, 8))
        self.audio_report_frame = ttk.Frame(frame)
        self.audio_report = tk.Text(self.audio_report_frame, height=9, wrap="word", state="disabled", font=("Malgun Gothic", 9))
        report_scroll = ttk.Scrollbar(self.audio_report_frame, command=self.audio_report.yview)
        self.audio_report.configure(yscrollcommand=report_scroll.set)
        report_scroll.pack(side="right", fill="y")
        self.audio_report.pack(side="left", fill="x", expand=True)
        ttk.Checkbutton(frame, text="‘루나’라고 부르면 명령 듣기 · 앱 실행 중 자동 감지", variable=self.wake_enabled).pack(anchor="w", pady=5)
        ttk.Checkbutton(frame, text="답변 재생 중에도 호출 감지 · 이어폰 사용 권장", variable=self.wake_during_speech).pack(anchor="w", pady=5)
        ttk.Label(frame, text="호출 대기는 이 PC에서 오프라인으로 인식합니다.\n호출 후 명령 음성은 기존 Google 인식에 전송합니다.\n스피커의 루나 목소리를 호출로 오인하지 않도록 기본값은 답변 중 감지 일시 정지입니다.", wraplength=580).pack(anchor="w", pady=8)
        self.install_wake_button = ttk.Button(frame, text="호출어 모델 설치 / 다시 시작", command=self.app.install_wake)
        self.install_wake_button.pack(anchor="w")
        ttk.Label(frame, textvariable=self.app.wake_note, wraplength=580).pack(anchor="w", pady=8)
        ttk.Separator(frame).pack(fill="x", pady=10)
        ttk.Checkbutton(frame, text="답변을 음성으로 읽기", variable=self.speak).pack(anchor="w", pady=(0, 12))
        ttk.Label(frame, text="목소리 · local:F1~F5는 한국어 여성 음성").pack(anchor="w")
        self.voice_combo = ttk.Combobox(frame, textvariable=self.voice_var, state="readonly")
        self.voice_combo.pack(fill="x", pady=8)
        ttk.Label(frame, text="음성 출력 속도").pack(anchor="w", pady=(8, 0))
        speed_row = ttk.Frame(frame)
        speed_row.pack(fill="x", pady=8)
        self.speed_scale = ttk.Scale(speed_row, from_=0.5, to=2.0, variable=self.voice_speed, command=self.speed_changed)
        self.speed_scale.pack(side="left", fill="x", expand=True)
        ttk.Label(speed_row, textvariable=self.speed_label, width=8, foreground=BLUE, font=("Malgun Gothic", 11, "bold")).pack(side="left", padx=10)
        ttk.Button(speed_row, text="기본 속도", command=self.reset_speed).pack(side="right")
        ttk.Label(frame, text="0.5배 느리게 · 1.0배 기본 · 2.0배 빠르게\n미리 듣기로 확인하고 저장하면 다음 답변부터 적용됩니다.\nWindows 설치 음성은 가까운 속도 단계로 적용됩니다.", wraplength=580, foreground=MUTED).pack(anchor="w", pady=(0, 10))
        row = ttk.Frame(frame)
        row.pack(fill="x")
        ttk.Button(row, text="목소리 들어보기", command=self.test_voice).pack(side="left")
        self.install_voice_button = ttk.Button(row, text="로컬 음성 설치 (약 81MB)", command=self.app.install_voice)
        self.install_voice_button.pack(side="right")
        ttk.Label(frame, textvariable=self.app.voice_note, wraplength=580).pack(anchor="w", pady=12)
        ttk.Separator(frame).pack(fill="x", pady=10)
        ttk.Label(frame, text="저장할 볼륨 값").pack(anchor="w", pady=(8, 0))
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=8)
        ttk.Scale(row, from_=0, to=100, variable=self.volume, command=lambda value: self.volume_label.set(f"{round(float(value))}%")).pack(side="left", fill="x", expand=True)
        ttk.Label(row, textvariable=self.volume_label, width=6).pack(side="right")
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=5)
        self.volume_button = ttk.Button(row, text="지금 볼륨 적용", command=self.apply_volume)
        self.volume_button.pack(side="left")
        mute = ttk.Button(row, text="음소거", command=lambda: self.sound_command("mute"))
        mute.pack(side="left", padx=8)
        self.unmute_button = ttk.Button(row, text="음소거 해제", command=lambda: self.sound_command("unmute"))
        self.unmute_button.pack(side="left")
        if os.name != "nt":
            for button in (self.volume_button, mute, self.unmute_button):
                button.configure(state="disabled")
        ttk.Checkbutton(frame, text="앱 시작 시 저장한 볼륨을 자동 적용", variable=self.start_volume).pack(anchor="w", pady=(12, 5))
        ttk.Label(frame, text="볼륨 값은 ‘설정 저장’으로 기억합니다. ‘지금 볼륨 적용’은 현재 PC의 소리를 바꿉니다.", wraplength=580).pack(anchor="w", pady=8)
        ttk.Label(frame, textvariable=self.sound_feedback, wraplength=580).pack(anchor="w", pady=8)

    def api_key_label(self):
        return "OpenAI API 키 · ChatGPT 연결 전용" if MODES.get(self.mode.get()) == "openai" else "Ollama API 키 · 클라우드에서만 사용"

    def mode_changed(self, *_event):
        mode = MODES.get(self.mode.get(), "off")
        if mode == self._display_mode:
            return
        self._model_drafts[self._display_mode] = (self.model.get(), self.vision_model.get())
        self._display_mode = mode
        options = load_settings(self.app.data_dir)
        model_key, vision_key, default = model_settings(mode)
        model = options.get(model_key) or default
        if mode in {"local", "cloud"} and model in {"qwen3:4b", "gpt-oss:120b"}:
            model = default
        model, vision = self._model_drafts.get(mode, (model, options.get(vision_key, "")))
        self.model.set(model)
        self.vision_model.set(vision)
        self.key.set("")
        self.key_label.set(self.api_key_label())
        self.feedback.set(self.app.service.credentials_for(mode).status())

    def _build_windows(self, frame):
        self.heading(frame, "Windows 조작 권한")
        admin = windows_control.is_admin()
        state = "관리자 권한" if admin else "일반 사용자 권한"
        if os.name != "nt":
            state = "Windows PC에서만 실행 가능"
        ttk.Label(frame, text="현재 실행 권한: " + state).pack(anchor="w", pady=(0, 15))
        ttk.Checkbutton(frame, text="Windows 고급 제어 허용 (PowerShell 명령)", variable=self.windows_access).pack(anchor="w", pady=8)
        ttk.Label(frame, text="켜고 ‘설정 저장’을 누르면 AI가 Windows 명령을 제안할 수 있습니다.\n"
                  "파일·폴더 생성/이동/삭제, 프로그램 실행·프로세스 종료, 서비스·레지스트리·시스템 설정 등을 현재 계정의 권한 안에서 제어합니다.\n"
                  "매번 실행 전 실제 명령 전체와 작업 설명을 확인합니다. 파일 삭제나 시스템 변경은 되돌리지 못할 수 있습니다.",
                  wraplength=580, justify="left").pack(anchor="w", pady=10)
        ttk.Label(frame, text="명령 실행 결과는 최근 대화에 포함됩니다. 클라우드 모드에서는 후속 질문과 함께 결과도 전송되므로 비밀 정보 조회를 요청하지 마세요.",
                  wraplength=580, justify="left").pack(anchor="w", pady=(0, 12))
        ttk.Separator(frame).pack(fill="x", pady=10)
        ttk.Label(frame, text="관리자 권한이 필요한 작업").pack(anchor="w", pady=(5, 8))
        self.admin_button = ttk.Button(frame, text="설정 저장 후 관리자 권한으로 다시 실행", command=self.restart_admin)
        self.admin_button.pack(anchor="w", pady=5)
        if os.name != "nt" or admin:
            self.admin_button.configure(state="disabled")
        ttk.Label(frame, text="Windows UAC 창에서 직접 승인해야 합니다. 승인되면 현재 앱을 닫고 관리자 앱으로 전환합니다.\n"
                  "세션에만 입력한 API 키와 저장하지 않은 대화는 재입력이 필요할 수 있습니다.\n"
                  "관리자 실행은 이번 실행에만 적용됩니다. Windows 보안 정책·보호된 프로세스 등 운영체제의 제한은 유지됩니다.",
                  wraplength=580, justify="left").pack(anchor="w", pady=10)
        ttk.Label(frame, textvariable=self.windows_feedback, wraplength=580).pack(anchor="w", pady=10)

    def restart_admin(self):
        if self.app.command_busy or self.app.listening or self.app.audio_checking:
            self.windows_feedback.set("진행 중인 요청이나 음성 인식이 끝난 뒤 다시 실행하세요.")
            return
        if self.app.installing_voice:
            self.windows_feedback.set("로컬 음성 설치가 끝난 뒤 다시 실행하세요.")
            return
        if self.app.last_dialog is not None and self.app.last_dialog.winfo_exists():
            self.windows_feedback.set("일정 초안 창을 저장하거나 닫은 뒤 다시 실행하세요.")
            return
        if not self.save():
            return
        self.app.command_busy = True
        self.app.send_button.configure(state="disabled")
        self.app.mic_button.configure(state="disabled")
        self.app.stop_speech()
        self.admin_button.configure(state="disabled")
        self.windows_feedback.set("Windows UAC 승인을 기다리고 있어요…")
        self.app.run_task(functools.partial(windows_control.restart_as_admin, self.app.data_dir),
                          self.admin_started, self.admin_failed)

    def admin_started(self, _result):
        self.app.close()

    def admin_failed(self, error):
        self.app.command_busy = False
        self.app.send_button.configure(state="normal")
        self.app.mic_button.configure(state="normal")
        if self.winfo_exists():
            self.admin_button.configure(state="normal")
            self.windows_feedback.set(str(error))

    def save(self):
        try:
            model = self.model.get().strip()
            vision_model = self.vision_model.get().strip()
            if self.mode.get() not in MODES or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,119}", model):
                raise UserError("올바른 연결 방식과 모델 이름을 입력하세요.")
            if vision_model and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,119}", vision_model):
                raise UserError("이미지 분석 모델 이름을 확인하세요.")
            turns, volume = self.history_turns.get(), round(self.volume.get())
            speed = round(self.voice_speed.get(), 2)
            if not 0.5 <= speed <= 2.0:
                raise UserError("음성 출력 속도는 0.5~2.0배로 설정하세요.")
            if not 1 <= turns <= 20 or not 0 <= volume <= 100:
                raise UserError("최근 대화 수는 1~20, 볼륨은 0~100으로 설정하세요.")
            mode = MODES[self.mode.get()]
            credentials = self.app.service.credentials_for(mode)
            if self.key.get().strip():
                credentials.set(self.key.get(), self.persist.get())
                self.key.set("")
            model_key, vision_key, _ = model_settings(mode)
            save_settings(self.app.data_dir, self.voice_var.get(), self.speak.get(), ai_mode=mode,
                          **{model_key: model, vision_key: vision_model}, remember_chat=self.remember.get(), history_turns=turns,
                          volume=volume, apply_volume_on_start=self.start_volume.get(), windows_control=self.windows_access.get(),
                          voice_speed=speed, wake_enabled=self.wake_enabled.get(), wake_during_speech=self.wake_during_speech.get())
            self.app.apply_settings()
            self.feedback.set("사용자 설정 저장 완료 · " + credentials.status())
            self.save_feedback.set("사용자 설정을 저장했어요. 재실행 후에도 유지됩니다.")
            self.sound_feedback.set("목소리·출력 속도·답변 읽기·볼륨 기본값을 저장했어요.")
            return True
        except (UserError, OSError, ValueError, tk.TclError) as error:
            message = str(error) if isinstance(error, UserError) else "설정을 저장하지 못했어요. 입력값과 데이터 폴더 권한을 확인하세요."
            self.feedback.set(message)
            self.save_feedback.set(message)
            return False

    def test_connection(self):
        if not self.save():
            return
        mode = MODES[self.mode.get()]
        if mode == "off":
            self.feedback.set("Ollama 또는 ChatGPT 연결을 선택하세요.")
            return
        client = self.app.service.client_for(mode)
        model = self.model.get()
        self.feedback.set(client.name + " 연결 확인 중…")
        work = functools.partial(client.request, mode, model, "연결 확인입니다. 조작 없이 짧게 인사만 해 주세요.", self.app.service.programs.catalog())
        self.app.run_task(work, functools.partial(self.connection_done, mode, model), functools.partial(self.connection_failed, mode, model))

    def connection_done(self, mode, model, _result):
        if self.winfo_exists() and MODES[self.mode.get()] == mode and self.model.get() == model:
            self.feedback.set(self.app.service.client_for(mode).name + " 연결 성공. 이제 자연어로 요청해 보세요.")

    def connection_failed(self, mode, model, error):
        if self.winfo_exists() and MODES[self.mode.get()] == mode and self.model.get() == model:
            self.feedback.set(str(error))

    def delete_key(self):
        try:
            credentials = self.app.service.credentials_for(MODES[self.mode.get()])
            credentials.delete()
            self.key.set("")
            self.feedback.set(credentials.status())
        except OSError:
            self.feedback.set("키를 삭제하지 못했어요.")

    def refresh_programs(self):
        self.program_list.delete(*self.program_list.get_children())
        items = self.app.service.programs.catalog()
        for item in items:
            self.program_list.insert("", "end", iid=item["id"], values=(item["name"],))
        if items:
            self.program_empty.pack_forget()
        else:
            self.program_empty.pack(after=self.program_list, anchor="w", pady=8)

    def choose_program(self):
        filename = filedialog.askopenfilename(parent=self, title="실행할 프로그램 선택", filetypes=[("Windows 실행 파일", "*.exe")])
        if filename:
            from pathlib import Path
            self.program_path = filename
            self.path_label.set(filename)
            if not self.app_name.get():
                self.app_name.set(Path(filename).stem)

    def register_program(self):
        try:
            self.app.service.programs.register(self.app_name.get(), self.program_path)
            self.refresh_programs()
            name = self.app_name.get().strip()
            self.program_feedback.set(f"등록했어요. ‘{name} 열어줘’ 또는 ‘루나, {name} 실행해줘’라고 말씀하세요.")
        except (UserError, OSError) as error:
            self.program_feedback.set(str(error) if isinstance(error, UserError) else "등록 목록을 저장하지 못했어요.")

    def remove_program(self):
        selected = self.program_list.selection()
        if selected:
            try:
                self.app.service.programs.remove(selected[0])
                self.refresh_programs()
                self.program_feedback.set("등록 삭제를 저장했어요. 프로그램 파일은 그대로 유지됩니다.")
            except (UserError, OSError) as error:
                self.program_feedback.set(str(error) if isinstance(error, UserError) else "등록 삭제에 실패했어요.")

    def refresh_voices(self):
        values = [voice["name"] for voice in self.app.voice_items]
        self.voice_combo.configure(values=values)
        if self.voice_var.get() not in values and self.app.selected_voice() in values:
            self.voice_var.set(self.app.selected_voice())
        self.install_voice_button.configure(state="disabled" if self.app.installing_voice else "normal")
        self.install_wake_button.configure(state="disabled" if self.app.installing_wake else "normal")

    def reflect_settings(self, keys):
        options = load_settings(self.app.data_dir)
        variables = {"voice": self.voice_var, "speak": self.speak, "voice_speed": self.voice_speed,
                     "wake_enabled": self.wake_enabled, "wake_during_speech": self.wake_during_speech,
                     "remember_chat": self.remember, "history_turns": self.history_turns, "volume": self.volume,
                     "apply_volume_on_start": self.start_volume, "windows_control": self.windows_access}
        if "ai_mode" in keys:
            self.mode.set(next(label for label, mode in MODES.items() if mode == options.get("ai_mode", "off")))
        model_key, vision_key, _ = model_settings(MODES[self.mode.get()])
        variables.update({model_key: self.model, vision_key: self.vision_model})
        for key in keys:
            if key in variables and key in options:
                variables[key].set(options[key])
        if "ai_mode" in keys:
            self.model.set(selected_model(options))
            self.vision_model.set(options.get(vision_key, ""))
        self.speed_changed(self.voice_speed.get())
        self.volume_label.set(f"{round(self.volume.get())}%")
        self.save_feedback.set("음성·AI 명령으로 변경한 설정을 반영했어요.")

    def test_voice(self):
        self.app.say("안녕하세요. 당신의 개인 비서 루나예요.", force=True, name=self.voice_var.get(), speed=round(self.voice_speed.get(), 2))

    def check_audio(self):
        self.audio_report_frame.pack(after=self.audio_description, fill="x", pady=(0, 12))
        self.audio_check_button.configure(state="disabled")
        self.audio_checked("점검 중입니다. 잠시 말씀해 주세요…", finished=False)
        self.app.check_audio(self.voice_var.get(), self.speak.get(), self.audio_checked, self.audio_check_failed)

    def audio_checked(self, result, finished=True):
        if not self.winfo_exists():
            return
        self.audio_report.configure(state="normal")
        self.audio_report.delete("1.0", "end")
        self.audio_report.insert("1.0", result)
        self.audio_report.configure(state="disabled")
        if finished:
            self.audio_check_button.configure(state="normal")

    def audio_check_failed(self, error):
        self.audio_checked(str(error))

    def speed_changed(self, value):
        self.speed_label.set(f"{float(value):.2f}배")

    def reset_speed(self):
        self.voice_speed.set(1.0)
        self.speed_changed(1.0)

    def apply_volume(self):
        self.sound_command("set", round(self.volume.get()))

    def sound_command(self, mode, value=0):
        self.sound_feedback.set("사운드 명령 처리 중…")
        self.app.run_task(functools.partial(desktop.volume, mode, value), self.sound_done, self.sound_failed)

    def sound_done(self, message):
        if self.winfo_exists():
            self.sound_feedback.set(message)

    def sound_failed(self, error):
        if self.winfo_exists():
            self.sound_feedback.set(str(error))

    def close(self):
        self.key.set("")
        self.app.config_dialog = None
        self.destroy()
