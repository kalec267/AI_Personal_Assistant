import tkinter as tk
from tkinter import ttk

from .core import UserError


class RoutinesPage(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.pack(fill="both", expand=True)
        self.app = app
        self.identifier = None
        self.name = tk.StringVar()
        self.volume_enabled = tk.BooleanVar(value=False)
        self.volume = tk.IntVar(value=30)
        self.url = tk.StringVar()
        self.feedback = tk.StringVar()
        ttk.Label(self, text="사용자 루틴", font=("Malgun Gothic", 16, "bold")).pack(anchor="w", pady=(0, 12))
        list_row = ttk.Frame(self)
        list_row.pack(fill="x")
        self.items = ttk.Treeview(list_row, columns=("name", "count"), show="headings", height=2, selectmode="browse", style="Routine.Treeview")
        self.items.heading("name", text="부를 이름")
        self.items.heading("count", text="동작 수")
        self.items.column("count", width=70, stretch=False)
        item_scroll = ttk.Scrollbar(list_row, command=self.items.yview)
        self.items.configure(yscrollcommand=item_scroll.set)
        item_scroll.pack(side="right", fill="y")
        self.items.pack(side="left", fill="x", expand=True)
        self.items.bind("<<TreeviewSelect>>", self.selected)
        name_row = ttk.Frame(self)
        name_row.pack(fill="x", pady=(12, 0))
        ttk.Label(name_row, text="부를 이름").pack(side="left", padx=(0, 10))
        ttk.Entry(name_row, textvariable=self.name).pack(side="left", fill="x", expand=True)
        ttk.Label(self, text="실행할 프로그램 · Ctrl 또는 Shift로 여러 개 선택").pack(anchor="w", pady=(12, 5))
        program_row = ttk.Frame(self)
        program_row.pack(fill="x")
        self.programs = ttk.Treeview(program_row, columns=("name",), show="headings", height=3, selectmode="extended", style="Routine.Treeview")
        self.programs.heading("name", text="등록 프로그램")
        program_scroll = ttk.Scrollbar(program_row, command=self.programs.yview)
        self.programs.configure(yscrollcommand=program_scroll.set)
        program_scroll.pack(side="right", fill="y")
        self.programs.pack(side="left", fill="x", expand=True)
        row = ttk.Frame(self)
        row.pack(fill="x", pady=10)
        ttk.Checkbutton(row, text="볼륨도 설정", variable=self.volume_enabled).pack(side="left")
        ttk.Spinbox(row, from_=0, to=100, textvariable=self.volume, width=5).pack(side="left", padx=10)
        ttk.Label(row, text="%").pack(side="left")
        ttk.Label(row, text="HTTPS 사이트").pack(side="left", padx=(12, 6))
        ttk.Entry(row, textvariable=self.url, width=22).pack(side="left", fill="x", expand=True)
        row = ttk.Frame(self)
        row.pack(fill="x", pady=12)
        ttk.Button(row, text="새 루틴", command=self.new).pack(side="left")
        ttk.Button(row, text="루틴 저장", command=self.save, style="Accent.TButton").pack(side="left", padx=8)
        ttk.Button(row, text="선택 루틴 삭제", command=self.remove).pack(side="right")
        ttk.Label(self, text="프로그램은 표시 순서대로 실행하며 총 1~5개 동작을 저장합니다.\n저장 후 ‘루나, 작업 준비해줘’처럼 부르면 실행합니다. 음성은 바로 실행하고 텍스트는 동작을 확인합니다.", wraplength=580).pack(anchor="w", pady=5)
        ttk.Label(self, textvariable=self.feedback, wraplength=580).pack(anchor="w", pady=5)
        self.refresh_programs()
        self.refresh()

    def refresh_programs(self):
        selected = set(self.programs.selection())
        self.programs.delete(*self.programs.get_children())
        for item in self.app.service.programs.catalog():
            self.programs.insert("", "end", iid=item["id"], values=(item["name"],))
        self.programs.selection_set([item for item in selected if self.programs.exists(item)])

    def refresh(self):
        self.items.delete(*self.items.get_children())
        try:
            for item in self.app.service.routines.catalog():
                self.items.insert("", "end", iid=item["id"], values=(item["name"], item["count"]))
        except UserError as error:
            self.feedback.set(str(error))

    def new(self):
        self.identifier = None
        self.items.selection_remove(*self.items.selection())
        self.name.set("")
        self.url.set("")
        self.volume_enabled.set(False)
        self.programs.selection_remove(*self.programs.selection())

    def selected(self, _event=None):
        selected = self.items.selection()
        if not selected:
            return
        self.identifier = selected[0]
        name = self.items.item(self.identifier, "values")[0]
        self.name.set(name)
        self.volume_enabled.set(False)
        self.url.set("")
        self.programs.selection_remove(*self.programs.selection())
        try:
            calls = self.app.service.routines.get(name)
            ids = [call["function"]["arguments"]["app_id"] for call in calls if call["function"]["name"] == "launch_program"]
            self.programs.selection_set([item for item in ids if self.programs.exists(item)])
            for call in calls:
                if call["function"]["name"] == "set_volume":
                    self.volume_enabled.set(True)
                    self.volume.set(call["function"]["arguments"]["value"])
                elif call["function"]["name"] == "open_website":
                    self.url.set(call["function"]["arguments"]["url"])
            self.feedback.set("등록 해제된 프로그램이 있습니다. 다시 선택하고 저장하세요." if any(not self.programs.exists(item) for item in ids) else "")
        except (UserError, KeyError, TypeError):
            self.feedback.set("루틴 내용을 읽지 못했어요. 삭제 후 다시 등록하세요.")

    def save(self):
        try:
            ids = [item for item in self.programs.get_children() if item in self.programs.selection()]
            self.identifier = self.app.service.routines.save(self.name.get(), ids, self.volume.get() if self.volume_enabled.get() else None,
                                                           self.url.get().strip(), self.identifier)
            self.refresh()
            self.feedback.set(f"저장했어요. ‘{self.name.get()} 실행해줘’라고 요청하세요.")
        except (UserError, OSError, tk.TclError) as error:
            self.feedback.set(str(error) if isinstance(error, UserError) else "루틴을 저장하지 못했어요. 입력값과 폴더 권한을 확인하세요.")

    def remove(self):
        if not self.identifier:
            return
        try:
            self.app.service.routines.remove(self.identifier)
            self.new()
            self.refresh()
            self.feedback.set("루틴을 삭제했어요. 프로그램 등록과 파일은 유지합니다.")
        except (UserError, OSError) as error:
            self.feedback.set(str(error) if isinstance(error, UserError) else "루틴을 삭제하지 못했어요.")
