"""Native Ollama and registered-program settings. Never displays stored keys."""
import functools
import os
import tkinter as tk
from tkinter import filedialog, ttk

from .core import UserError
from .settings import load_settings, save_settings

MODES = {"기본 명령 (AI 끔)": "off", "Ollama 로컬 · API 키 불필요": "local", "Ollama 클라우드 · API 키 필요": "cloud"}


class ConfigDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("LUNA · AI 및 프로그램 설정")
        self.geometry("660x650")
        self.minsize(620, 600)
        self.transient(app.root)
        options = load_settings(app.data_dir)
        self.mode = tk.StringVar(value=next(label for label, value in MODES.items() if value == options.get("ai_mode", "off")))
        self.model = tk.StringVar(value=options.get("ollama_model", "qwen3:4b"))
        self.key = tk.StringVar()
        self.persist = tk.BooleanVar(value=os.name == "nt")
        self.feedback = tk.StringVar(value=app.service.credentials.status())
        self.app_name = tk.StringVar()
        self.program_path = ""
        self.path_label = tk.StringVar(value="실행 파일을 선택해 주세요.")
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=18, pady=18)
        ai = ttk.Frame(notebook, padding=18)
        programs = ttk.Frame(notebook, padding=18)
        notebook.add(ai, text="Ollama AI")
        notebook.add(programs, text="프로그램 등록")
        ttk.Label(ai, text="자연어 명령과 대화", font=("Malgun Gothic", 16, "bold")).pack(anchor="w", pady=(0, 18))
        ttk.Label(ai, text="연결 방식").pack(anchor="w")
        mode_combo = ttk.Combobox(ai, textvariable=self.mode, values=list(MODES), state="readonly")
        mode_combo.pack(fill="x", pady=(5, 15))
        mode_combo.bind("<<ComboboxSelected>>", self.mode_changed)
        ttk.Label(ai, text="모델 이름").pack(anchor="w")
        ttk.Entry(ai, textvariable=self.model).pack(fill="x", pady=(5, 12))
        ttk.Label(ai, text="로컬: qwen3:4b (Ollama 설치 후 ollama pull qwen3:4b)\n클라우드: gpt-oss:120b (계정별 사용 한도 적용)", wraplength=550).pack(anchor="w", pady=(0, 15))
        ttk.Label(ai, text="Ollama API 키 · 클라우드 모드에서만 사용").pack(anchor="w")
        ttk.Entry(ai, textvariable=self.key, show="●").pack(fill="x", pady=(5, 8))
        ttk.Checkbutton(ai, text="이 Windows 사용자에 암호화 저장 (선택 해제 시 현재 세션만)", variable=self.persist).pack(anchor="w")
        if os.name != "nt":
            self.persist.set(False)
        ttk.Label(ai, text="기존 키는 표시하지 않습니다. 빈칸으로 저장하면 기존 키를 유지합니다.\n키는 ollama.com에만 전송하며, 로컬 모드에서는 전송하지 않습니다.", wraplength=550).pack(anchor="w", pady=12)
        row = ttk.Frame(ai)
        row.pack(fill="x", pady=8)
        ttk.Button(row, text="설정 저장", command=self.save).pack(side="left")
        ttk.Button(row, text="연결 테스트", command=self.test_connection).pack(side="left", padx=8)
        ttk.Button(row, text="저장한 키 삭제", command=self.delete_key).pack(side="right")
        ttk.Label(ai, textvariable=self.feedback, wraplength=550).pack(anchor="w", pady=12)
        ttk.Label(programs, text="AI로 실행할 프로그램", font=("Malgun Gothic", 16, "bold")).pack(anchor="w", pady=(0, 12))
        ttk.Label(programs, text="사용자가 직접 선택한 .exe만 등록합니다. AI가 실행 경로나 인자를 만들 수는 없습니다.", wraplength=550).pack(anchor="w", pady=(0, 12))
        self.program_list = ttk.Treeview(programs, columns=("name",), show="headings", height=6, selectmode="browse")
        self.program_list.heading("name", text="이름")
        self.program_list.pack(fill="both", expand=True)
        ttk.Label(programs, text="부를 이름 (예: 크롬, 음악 앱)").pack(anchor="w", pady=(14, 5))
        ttk.Entry(programs, textvariable=self.app_name).pack(fill="x")
        ttk.Label(programs, textvariable=self.path_label, wraplength=550).pack(anchor="w", pady=8)
        row = ttk.Frame(programs)
        row.pack(fill="x")
        ttk.Button(row, text=".exe 선택", command=self.choose_program).pack(side="left")
        ttk.Button(row, text="등록", command=self.register_program).pack(side="left", padx=8)
        ttk.Button(row, text="선택한 등록 삭제", command=self.remove_program).pack(side="right")
        self.program_feedback = tk.StringVar()
        ttk.Label(programs, textvariable=self.program_feedback, wraplength=550).pack(anchor="w", pady=12)
        self.refresh_programs()
        self.protocol("WM_DELETE_WINDOW", self.close)

    def mode_changed(self, _event=None):
        mode = MODES[self.mode.get()]
        if self.model.get() in {"qwen3:4b", "gpt-oss:120b", ""}:
            self.model.set("gpt-oss:120b" if mode == "cloud" else "qwen3:4b")

    def save(self):
        try:
            model = self.model.get().strip()
            import re
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,119}", model):
                raise UserError("올바른 모델 이름을 입력하세요.")
            key = self.key.get()
            if key.strip():
                self.app.service.credentials.set(key, self.persist.get())
                self.key.set("")
            options = load_settings(self.app.data_dir)
            save_settings(self.app.data_dir, options.get("voice", ""), self.app.speak_var.get(),
                          ai_mode=MODES[self.mode.get()], ollama_model=model)
            self.app.mode_var.set(self.mode.get() + " · 데스크톱")
            self.feedback.set("설정 저장 완료 · " + self.app.service.credentials.status())
            return True
        except (UserError, OSError, ValueError) as error:
            self.feedback.set(str(error) if isinstance(error, UserError) else "설정을 저장하지 못했어요.")
            return False

    def test_connection(self):
        if not self.save():
            return
        mode = MODES[self.mode.get()]
        if mode == "off":
            self.feedback.set("로컬 또는 클라우드 모드를 선택하세요.")
            return
        self.feedback.set("Ollama 연결 확인 중…")
        work = functools.partial(self.app.service.ollama.request, mode, self.model.get(), "연결 확인입니다. 조작 없이 짧게 인사만 해 주세요.", self.app.service.programs.catalog())
        self.app.run_task(work, self.connection_done, self.connection_failed)

    def connection_done(self, _result):
        if self.winfo_exists():
            self.feedback.set("Ollama 연결 성공. 이제 자연어로 요청해 보세요.")

    def connection_failed(self, error):
        if self.winfo_exists():
            self.feedback.set(str(error))

    def delete_key(self):
        try:
            self.app.service.credentials.delete()
            self.key.set("")
            self.feedback.set(self.app.service.credentials.status())
        except OSError:
            self.feedback.set("키를 삭제하지 못했어요.")

    def refresh_programs(self):
        self.program_list.delete(*self.program_list.get_children())
        for item in self.app.service.programs.catalog():
            self.program_list.insert("", "end", iid=item["id"], values=(item["name"],))

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
            self.program_feedback.set("등록했어요. AI에게 이 이름으로 실행을 요청하세요.")
        except (UserError, OSError) as error:
            self.program_feedback.set(str(error) if isinstance(error, UserError) else "등록 목록을 저장하지 못했어요.")

    def remove_program(self):
        selected = self.program_list.selection()
        if not selected:
            return
        try:
            self.app.service.programs.remove(selected[0])
            self.refresh_programs()
            self.program_feedback.set("등록을 삭제했어요. 프로그램 파일은 그대로 유지됩니다.")
        except (UserError, OSError) as error:
            self.program_feedback.set(str(error) if isinstance(error, UserError) else "등록 삭제에 실패했어요.")

    def close(self):
        self.key.set("")
        self.app.config_dialog = None
        self.destroy()
