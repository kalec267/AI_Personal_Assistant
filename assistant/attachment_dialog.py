import tkinter as tk
from tkinter import ttk


class AttachmentDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("LUNA · 첨부 자료")
        self.geometry("700x460")
        self.minsize(600, 400)
        self.transient(app.root)
        footer = ttk.Frame(self, padding=14)
        footer.pack(side="bottom", fill="x")
        ttk.Button(footer, text="파일 추가", command=app.choose_attachments).pack(side="left")
        ttk.Button(footer, text="선택한 첨부 제거", command=self.remove).pack(side="left", padx=10)
        ttk.Button(footer, text="닫기", command=self.close).pack(side="right")
        content = ttk.Frame(self, padding=16)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="현재 대화에 함께 보낼 자료", font=("Malgun Gothic", 15, "bold")).pack(anchor="w", pady=(0, 8))
        self.items = ttk.Treeview(content, columns=("name", "type"), show="headings", height=5, selectmode="browse")
        self.items.heading("name", text="파일 이름")
        self.items.heading("type", text="읽은 내용")
        self.items.column("name", width=320)
        self.items.column("type", width=180)
        self.items.pack(fill="x", pady=8)
        self.preview = tk.Text(content, height=7, wrap="word", state="disabled", font=("Malgun Gothic", 10))
        self.preview.pack(fill="both", expand=True)
        self.items.bind("<<TreeviewSelect>>", self.select)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()

    def refresh(self):
        self.items.delete(*self.items.get_children())
        for index, item in enumerate(self.app.attachments):
            self.items.insert("", "end", iid=str(index), values=(item.name, "이미지" if item.image else f"텍스트 {len(item.text):,}자"))
        if self.app.attachments:
            self.items.selection_set("0")
        self.select()

    def select(self, _event=None):
        selected = self.items.selection()
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        if selected and int(selected[0]) < len(self.app.attachments):
            item = self.app.attachments[int(selected[0])]
            self.preview.insert("1.0", item.note + "\n\n" + (item.text[:2500] + ("\n(미리 보기 일부)" if len(item.text) > 2500 else "") if item.text else "이미지 분석에는 이미지 입력을 지원하는 Ollama 모델이 필요합니다."))
        self.preview.configure(state="disabled")

    def remove(self):
        selected = self.items.selection()
        if selected and not self.app.command_busy:
            self.app.attachments.pop(int(selected[0]))
            self.app.refresh_attachments()

    def close(self):
        self.app.attachment_dialog = None
        self.destroy()
