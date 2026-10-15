import functools
import tkinter as tk
from tkinter import ttk

from .core import UserError
from .search import KINDS, PROVIDERS, open_search, search_spec
from .settings import load_settings, save_settings
from .theme import PAPER


class SearchDialog(tk.Toplevel):
    def __init__(self, app, spec=None):
        super().__init__(app.root)
        self.app = app
        self.title("LUNA · 뉴스·동영상 검색")
        self.configure(bg=PAPER)
        self.geometry("640x370")
        self.minsize(600, 370)
        self.transient(app.root)
        options = load_settings(app.data_dir)
        self.query = tk.StringVar(value=spec["query"] if spec else "")
        self.provider = tk.StringVar(value=PROVIDERS[spec["provider"] if spec else options.get("search_provider", "google")])
        self.kind = tk.StringVar(value=KINDS[spec["kind"] if spec else options.get("search_kind", "web")])
        self.feedback = tk.StringVar(value="검색 결과는 기본 브라우저에서 표시합니다. API 키는 필요 없습니다.")
        self.url = tk.StringVar()
        content = ttk.Frame(self, padding=20)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="키워드로 찾아보기", font=("Malgun Gothic", 16, "bold")).pack(anchor="w", pady=(0, 16))
        entry = ttk.Entry(content, textvariable=self.query)
        entry.pack(fill="x", ipady=4)
        entry.bind("<Return>", lambda _event: self.search())
        row = ttk.Frame(content)
        row.pack(fill="x", pady=14)
        ttk.Combobox(row, textvariable=self.provider, values=list(PROVIDERS.values()), state="readonly", width=10).pack(side="left")
        ttk.Combobox(row, textvariable=self.kind, values=list(KINDS.values()), state="readonly", width=10).pack(side="left", padx=10)
        self.search_button = ttk.Button(row, text="검색 결과 열기", command=self.search, style="Accent.TButton")
        self.search_button.pack(side="right")
        ttk.Label(content, text="선택한 검색 서비스·종류를 다음 실행에도 기억합니다.\n검색어는 선택한 서비스에 전송됩니다.", wraplength=580).pack(anchor="w", pady=4)
        ttk.Entry(content, textvariable=self.url, state="readonly").pack(fill="x", pady=10)
        ttk.Label(content, textvariable=self.feedback, wraplength=580).pack(anchor="w", pady=5)
        ttk.Button(content, text="닫기", command=self.close).pack(side="bottom", anchor="e")
        self.protocol("WM_DELETE_WINDOW", self.close)
        entry.focus_set()

    def search(self):
        try:
            provider = next(key for key, label in PROVIDERS.items() if label == self.provider.get())
            kind = next(key for key, label in KINDS.items() if label == self.kind.get())
            spec = search_spec(self.query.get(), provider, kind)
            options = load_settings(self.app.data_dir)
            save_settings(self.app.data_dir, options.get("voice", ""), options.get("speak", True), search_provider=provider, search_kind=kind)
        except (UserError, OSError, StopIteration) as error:
            self.feedback.set(str(error) if isinstance(error, UserError) else "검색 설정을 저장하지 못했어요.")
            return
        self.url.set(spec["url"])
        self.feedback.set("기본 브라우저로 검색 결과를 여는 중…")
        self.search_button.configure(state="disabled")
        self.app.run_task(functools.partial(open_search, spec["query"], provider, kind), self.done, self.failed)

    def done(self, message):
        if self.winfo_exists():
            self.feedback.set(message)
            self.search_button.configure(state="normal")

    def failed(self, error):
        if self.winfo_exists():
            self.feedback.set(str(error))
            self.search_button.configure(state="normal")

    def close(self):
        self.app.search_dialog = None
        self.destroy()
