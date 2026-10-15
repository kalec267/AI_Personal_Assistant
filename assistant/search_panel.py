import functools
import tkinter as tk
from tkinter import ttk

from .core import UserError
from .search import KINDS, PROVIDERS, open_search, search_spec
from .settings import load_settings, save_settings


class SearchPanel(ttk.Frame):
    """One persistent search tab; opening it never creates a new window."""
    def __init__(self, app, parent):
        super().__init__(parent, padding=20)
        self.app = app
        self.busy = False
        options = load_settings(app.data_dir)
        self.query = tk.StringVar()
        self.provider = tk.StringVar(value=PROVIDERS[options.get("search_provider", "google")])
        self.kind = tk.StringVar(value=KINDS[options.get("search_kind", "web")])
        self.feedback = tk.StringVar()
        self.url = tk.StringVar()
        ttk.Label(self, text="뉴스·동영상 검색", font=("Malgun Gothic", 16, "bold")).pack(anchor="w", pady=(0, 18))
        search_row = ttk.Frame(self)
        search_row.pack(fill="x")
        self.entry = ttk.Entry(search_row, textvariable=self.query)
        self.entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.entry.bind("<Return>", lambda _event: self.search())
        self.search_button = ttk.Button(search_row, text="브라우저로 검색", command=self.search, style="Accent.TButton")
        self.search_button.pack(side="right", padx=(10, 0))
        row = ttk.Frame(self)
        row.pack(fill="x", pady=14)
        ttk.Combobox(row, textvariable=self.provider, values=list(PROVIDERS.values()), state="readonly", width=10).pack(side="left")
        ttk.Combobox(row, textvariable=self.kind, values=list(KINDS.values()), state="readonly", width=10).pack(side="left", padx=10)
        ttk.Entry(self, textvariable=self.url, state="readonly").pack(fill="x", pady=10)
        ttk.Label(self, textvariable=self.feedback, wraplength=560).pack(anchor="w", pady=5)

    def set_spec(self, spec):
        self.query.set(spec["query"])
        self.provider.set(PROVIDERS[spec["provider"]])
        self.kind.set(KINDS[spec["kind"]])

    def search(self):
        if self.busy:
            self.feedback.set("검색 결과를 여는 중입니다. 잠시 기다려 주세요.")
            return
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
        self.busy = True
        self.search_button.configure(state="disabled")
        self.app.run_task(functools.partial(open_search, spec["query"], provider, kind), self.done, self.failed)

    def reflect_settings(self, keys):
        options = load_settings(self.app.data_dir)
        if "search_provider" in keys:
            self.provider.set(PROVIDERS[options.get("search_provider", "google")])
        if "search_kind" in keys:
            self.kind.set(KINDS[options.get("search_kind", "web")])

    def done(self, message):
        self.busy = False
        if self.winfo_exists():
            self.feedback.set(message)
            self.search_button.configure(state="normal")

    def failed(self, error):
        self.busy = False
        if self.winfo_exists():
            self.feedback.set(str(error))
            self.search_button.configure(state="normal")

