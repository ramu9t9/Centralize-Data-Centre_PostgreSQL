#!/usr/bin/env python3
"""
Broadcast Control Panel - Tkinter GUI
Controls Live/Replay modes for WebSocket Broadcaster
"""

import sys
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, timedelta, timezone
import threading
import queue
from typing import Optional, Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import db

# Import utility functions
try:
    from services.utils import utc_to_ist, format_timestamp_for_display
except ImportError:
    # Try adding project root to sys.path (common when running from services/ directly)
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from services.utils import utc_to_ist, format_timestamp_for_display  # type: ignore
    except Exception:
        # Final fallback: minimal formatting (keeps GUI working but less pretty)
        def utc_to_ist(ts: str) -> str:
            return ts[:19] if len(ts) >= 19 else ts
        def format_timestamp_for_display(ts: str) -> str:
            return ts[:19] if len(ts) >= 19 else ts

# Import broadcaster (handle both direct import and relative import)
try:
    from services.websocket_broadcaster_service import WebSocketBroadcaster
except ImportError:
    # Try relative import
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from services.websocket_broadcaster_service import WebSocketBroadcaster


class CalendarPopup:
    """Modern calendar popup for date selection (restricted to database date range in Replay mode)"""
    
    def __init__(self, parent, date_var: tk.StringVar, theme_colors: dict, min_date=None, max_date=None):
        self.date_var = date_var
        self.theme = theme_colors
        self.min_date = min_date  # Only allow dates >= min_date (from DB)
        self.max_date = max_date  # Only allow dates <= max_date (from DB)
        self.win = tk.Toplevel(parent)
        self.win.title("Select Date")
        self.win.configure(bg=theme_colors.get("bg", "#0f1115"))
        self.win.overrideredirect(False)
        self.win.transient(parent)
        self.win.grab_set()
        self.win.geometry("320x300")
        self.win.resizable(False, False)
        
        # Center near parent
        self.win.update_idletasks()
        x = parent.winfo_rootx() + 50
        y = parent.winfo_rooty() + 50
        self.win.geometry(f"+{x}+{y}")
        
        # Current month/year
        try:
            parts = date_var.get().split("-")
            self.year = int(parts[0]) if len(parts) >= 1 else datetime.now().year
            self.month = int(parts[1]) if len(parts) >= 2 else datetime.now().month
        except (ValueError, IndexError):
            now = datetime.now()
            self.year, self.month = now.year, now.month
        
        self._build_ui()
        self.win.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.win.bind("<Escape>", lambda e: self._on_cancel())
    
    def _build_ui(self):
        bg = self.theme.get("bg", "#0f1115")
        surface = self.theme.get("surface", "#151922")
        fg = self.theme.get("fg", "#e8eaed")
        accent = self.theme.get("accent", "#4aa3ff")
        muted = self.theme.get("muted", "#a7b0c0")
        border = self.theme.get("border", "#2a2f3a")
        
        main = tk.Frame(self.win, bg=bg, padx=16, pady=16)
        main.pack(fill="both", expand=True)
        
        # Month/Year header with nav
        header = tk.Frame(main, bg=bg)
        header.pack(fill="x", pady=(0, 12))
        
        def nav(delta):
            self.month += delta
            if self.month > 12:
                self.month, self.year = 1, self.year + 1
            elif self.month < 1:
                self.month, self.year = 12, self.year - 1
            self._refresh_calendar()
        
        tk.Button(header, text="◀", font=("Segoe UI", 10), width=3,
                  bg=surface, fg=fg, relief="flat", bd=0, cursor="hand2",
                  activebackground=accent, activeforeground="white",
                  command=lambda: nav(-1)).pack(side="left", padx=(0, 8))
        
        self.month_label = tk.Label(header, text="", font=("Segoe UI", 12, "bold"),
                                    bg=bg, fg=fg)
        self.month_label.pack(side="left", expand=True)
        
        tk.Button(header, text="▶", font=("Segoe UI", 10), width=3,
                  bg=surface, fg=fg, relief="flat", bd=0, cursor="hand2",
                  activebackground=accent, activeforeground="white",
                  command=lambda: nav(1)).pack(side="right", padx=(8, 0))
        
        # Data range hint (when restricted to DB)
        if self.min_date is not None and self.max_date is not None:
            range_hint = tk.Label(main, text=f"Data available: {self.min_date.strftime('%d %b %Y')} – {self.max_date.strftime('%d %b %Y')}",
                                  font=("Segoe UI", 8), bg=bg, fg=muted)
            range_hint.pack(anchor="w", pady=(0, 4))
        
        # Weekday headers
        week_frame = tk.Frame(main, bg=bg)
        week_frame.pack(fill="x", pady=(0, 4))
        for wd in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]:
            lbl = tk.Label(week_frame, text=wd, font=("Segoe UI", 9), bg=bg, fg=muted, width=4)
            lbl.pack(side="left", expand=True)
        
        # Calendar grid
        self.grid_frame = tk.Frame(main, bg=bg)
        self.grid_frame.pack(fill="both", expand=True, pady=(0, 12))
        
        # Today / Cancel
        btn_frame = tk.Frame(main, bg=bg)
        btn_frame.pack(fill="x")
        self.today_btn = tk.Button(btn_frame, text="Today", font=("Segoe UI", 10),
                                   bg=surface, fg=accent, relief="flat", padx=12, pady=6, cursor="hand2",
                                   command=self._set_today)
        self.today_btn.pack(side="left", padx=(0, 8))
        tk.Button(btn_frame, text="Cancel", font=("Segoe UI", 10),
                  bg=surface, fg=muted, relief="flat", padx=12, pady=6, cursor="hand2",
                  command=self._on_cancel).pack(side="right")
        
        self._refresh_calendar()
    
    def _refresh_calendar(self):
        for w in self.grid_frame.winfo_children():
            w.destroy()
        
        month_names = ["January", "February", "March", "April", "May", "June",
                       "July", "August", "September", "October", "November", "December"]
        self.month_label.config(text=f"{month_names[self.month - 1]} {self.year}")
        
        surface = self.theme.get("surface", "#151922")
        fg = self.theme.get("fg", "#e8eaed")
        accent = self.theme.get("accent", "#4aa3ff")
        muted = self.theme.get("muted", "#a7b0c0")
        today = datetime.now()
        
        # First day of month (0=Mon, 6=Sun)
        first = datetime(self.year, self.month, 1)
        start_offset = (first.weekday()) % 7  # Mon=0
        days_in_month = (datetime(self.year, self.month + 1, 1) - timedelta(days=1)).day
        
        try:
            current = self.date_var.get()
            sel = datetime.strptime(current, "%Y-%m-%d") if current else None
        except ValueError:
            sel = None
        
        # Use grid layout: 7 columns (Mon-Sun), multiple rows
        def place_cell(widget, row, col):
            widget.grid(row=row, column=col, padx=2, pady=2, sticky="nsew")
        
        # Pad empty cells at start of first row
        for c in range(start_offset):
            cell = tk.Frame(self.grid_frame, bg=surface, width=36, height=32)
            place_cell(cell, 0, c)
        
        # Date buttons in 7-column grid (grey out dates outside DB range)
        for d in range(1, days_in_month + 1):
            idx = start_offset + (d - 1)
            row, col = idx // 7, idx % 7
            dt = datetime(self.year, self.month, d)
            d_date = dt.date()
            is_today = (d_date == today.date())
            is_selected = sel and (d_date == sel.date())
            # Check if date is within database range
            in_range = True
            if self.min_date is not None and d_date < self.min_date:
                in_range = False
            if self.max_date is not None and d_date > self.max_date:
                in_range = False
            
            if in_range:
                btn_bg = accent if is_selected else (surface if is_today else surface)
                btn_fg = "white" if is_selected else (accent if is_today else fg)
                btn = tk.Button(self.grid_frame, text=str(d), font=("Segoe UI", 10),
                               bg=btn_bg, fg=btn_fg, relief="flat", width=4, cursor="hand2",
                               activebackground=accent, activeforeground="white",
                               command=lambda day=d: self._select(day))
                if is_today and not is_selected:
                    btn.config(borderwidth=1, highlightbackground=accent, highlightthickness=1)
            else:
                # Greyed out - date not in database
                grey_bg = self.theme.get("border", "#2a2f3a")
                grey_fg = self.theme.get("muted", "#a7b0c0")
                btn = tk.Button(self.grid_frame, text=str(d), font=("Segoe UI", 10),
                               bg=grey_bg, fg=grey_fg, relief="flat", width=4, cursor="arrow",
                               state="disabled")
            place_cell(btn, row, col)
        
        # Fill remaining cells to complete last row
        total = start_offset + days_in_month
        remainder = (7 - (total % 7)) % 7
        last_row = (total - 1) // 7 if total > 0 else 0
        for i in range(remainder):
            col = (total % 7) + i
            cell = tk.Frame(self.grid_frame, bg=surface, width=36, height=32)
            place_cell(cell, last_row, col)
        
        # Equal column weights so cells distribute evenly
        for c in range(7):
            self.grid_frame.columnconfigure(c, weight=1)
        
        # Enable/disable Today button based on whether today is in DB range
        today_in_range = True
        if self.min_date is not None and today.date() < self.min_date:
            today_in_range = False
        if self.max_date is not None and today.date() > self.max_date:
            today_in_range = False
        if hasattr(self, 'today_btn'):
            if today_in_range:
                self.today_btn.config(state="normal", cursor="hand2")
            else:
                self.today_btn.config(state="disabled", cursor="arrow")
    
    def _select(self, day):
        self.date_var.set(f"{self.year}-{self.month:02d}-{day:02d}")
        self.win.destroy()
    
    def _set_today(self):
        t = datetime.now()
        d = t.date()
        if self.min_date is not None and d < self.min_date:
            return
        if self.max_date is not None and d > self.max_date:
            return
        self.date_var.set(t.strftime("%Y-%m-%d"))
        self.win.destroy()
    
    def _on_cancel(self):
        self.win.grab_release()
        self.win.destroy()


class DateTimePicker(ttk.Frame):
    """Custom datetime picker widget with calendar popup"""
    
    def __init__(self, parent, theme_panel=None):
        super().__init__(parent, style="Card.TFrame")
        self.theme_panel = theme_panel
        
        # Date entry with calendar button
        self.date_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        date_frame = ttk.Frame(self, style="Card.TFrame")
        date_frame.pack(side="left", padx=4)
        ttk.Label(self, text="Date:", style="Card.TLabel").pack(side="left", padx=4)
        self.date_entry = ttk.Entry(date_frame, textvariable=self.date_var, width=12)
        self.date_entry.pack(side="left")
        self._calendar_btn = ttk.Button(date_frame, text="📅", width=3,
                                        command=self._show_calendar)
        self._calendar_btn.pack(side="left", padx=(4, 0))
        self.date_entry.bind("<Button-1>", lambda e: self._show_calendar())
        
        # Time entry
        self.time_var = tk.StringVar(value="09:15")
        ttk.Label(self, text="Time:", style="Card.TLabel").pack(side="left", padx=4)
        time_entry = ttk.Entry(self, textvariable=self.time_var, width=8)
        time_entry.pack(side="left", padx=4)
    
    def _show_calendar(self):
        if self.theme_panel:
            theme = {
                "bg": getattr(self.theme_panel, "bg_color", "#0f1115"),
                "surface": getattr(self.theme_panel, "surface_color", "#151922"),
                "fg": getattr(self.theme_panel, "fg_color", "#e8eaed"),
                "accent": getattr(self.theme_panel, "accent_color", "#4aa3ff"),
                "muted": getattr(self.theme_panel, "muted_fg", "#a7b0c0"),
                "border": getattr(self.theme_panel, "border_color", "#2a2f3a"),
            }
            min_date, max_date = getattr(self.theme_panel, "get_db_date_range", lambda: (None, None))()
        else:
            theme = {"bg": "#0f1115", "surface": "#151922", "fg": "#e8eaed",
                     "accent": "#4aa3ff", "muted": "#a7b0c0", "border": "#2a2f3a"}
            min_date, max_date = None, None
        root = self.winfo_toplevel()
        CalendarPopup(root, self.date_var, theme, min_date=min_date, max_date=max_date)
    
    def get_datetime(self) -> datetime:
        """Get datetime from picker"""
        date_str = self.date_var.get()
        time_str = self.time_var.get()
        try:
            return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except ValueError:
            raise ValueError(f"Invalid date/time format: {date_str} {time_str}")
    
    def set_datetime(self, dt: datetime):
        """Set datetime in picker"""
        self.date_var.set(dt.strftime("%Y-%m-%d"))
        self.time_var.set(dt.strftime("%H:%M"))


class BroadcastControlPanel:
    """Tkinter GUI control panel for WebSocket Broadcaster"""
    
    def __init__(self, broadcaster: Optional[WebSocketBroadcaster] = None):
        self.broadcaster = broadcaster
        self.root = tk.Tk()
        self.root.title("📡 NIFTY Data Broadcast Control")
        self.root.geometry("700x800")
        
        # ---- Theme palette (Windows 11-like dark) ----
        self.bg_color = "#0f1115"  # window background
        self.surface_color = "#151922"  # card background
        self.surface2 = "#10131a"  # alt surface
        self.fg_color = "#e8eaed"  # text
        self.muted_fg = "#a7b0c0"  # muted text
        self.border_color = "#2a2f3a"  # subtle border
        self.entry_bg = "#0f1115"  # entry field background
        self.accent_color = "#4aa3ff"  # Win11-ish blue
        
        # Spacing scale for consistent layout (Win11 is airy)
        self.pad_x = 10
        self.pad_y = 8
        self.gap_y = 12  # Increased for more airy feel
        
        # Apply dark theme to root window
        self.root.configure(bg=self.bg_color, cursor="arrow")
        
        # Configure ttk styles for modern dark theme
        style = ttk.Style(self.root)
        style.theme_use('clam')
        
        # Main window surface
        style.configure("Main.TFrame", background=self.bg_color)
        
        # Card sections (LabelFrames)
        style.configure(
            "Card.TLabelframe",
            background=self.surface_color,
            bordercolor=self.border_color,
            relief="solid",
            borderwidth=1
        )
        style.configure(
            "Card.TLabelframe.Label",
            background=self.surface_color,
            foreground=self.fg_color,
            font=("Segoe UI", 10, "bold"),
            padding=(6, 2)
        )
        
        # Card frames (inside LabelFrames)
        style.configure("Card.TFrame", background=self.surface_color)
        
        # Labels: main vs card (Win11 uses slightly larger fonts)
        style.configure("Main.TLabel", 
                       background=self.bg_color, 
                       foreground=self.fg_color, 
                       font=("Segoe UI", 10))
        style.configure("Card.TLabel", 
                       background=self.surface_color, 
                       foreground=self.fg_color, 
                       font=("Segoe UI", 10))
        style.configure("Muted.Card.TLabel", 
                       background=self.surface_color, 
                       foreground=self.muted_fg, 
                       font=("Segoe UI", 10))
        
        # Buttons (Win11-like hover/pressed feedback)
        style.configure(
            "TButton",
            background=self.surface2,
            foreground=self.fg_color,
            padding=(12, 8),
            borderwidth=1,
            relief="flat",
            font=("Segoe UI", 10)
        )
        style.map(
            "TButton",
            background=[("active", "#1b2130"), ("pressed", "#0d1016"), ("disabled", "#10131a")],
            foreground=[("disabled", "#9aa4b2"), ("!disabled", self.fg_color)]
        )
        
        # Primary button style (for Start button)
        style.configure(
            "Primary.TButton",
            background=self.accent_color,
            foreground="#0f1115",
            padding=(12, 8),
            borderwidth=1,
            relief="flat",
            font=("Segoe UI", 10, "bold")
        )
        style.map(
            "Primary.TButton",
            background=[("active", "#6bb3ff"), ("pressed", "#2f7fd6"), ("disabled", "#10131a")],
            foreground=[("disabled", "#9aa4b2"), ("!disabled", "#0f1115")]
        )
        
        # Segmented button styles (for mode toggle)
        style.configure(
            "Seg.TButton",
            background=self.surface2,
            foreground=self.fg_color,
            padding=(14, 8),
            borderwidth=1,
            relief="flat",
            font=("Segoe UI", 10)
        )
        style.map(
            "Seg.TButton",
            background=[("active", "#1b2130"), ("pressed", "#0d1016"), ("disabled", "#10131a")],
            foreground=[("disabled", "#9aa4b2"), ("!disabled", self.fg_color)]
        )
        style.configure(
            "SegOn.TButton",
            background=self.accent_color,
            foreground="#0f1115",
            padding=(14, 8),
            borderwidth=1,
            relief="flat",
            font=("Segoe UI", 10, "bold")
        )
        style.map(
            "SegOn.TButton",
            background=[("active", self.accent_color), ("pressed", self.accent_color), ("disabled", "#10131a")],
            foreground=[("disabled", "#9aa4b2"), ("!disabled", "#0f1115")]
        )
        
        # Radio/Check: keep them on card surfaces
        style.configure("TRadiobutton", 
                       background=self.surface_color, 
                       foreground=self.fg_color, 
                       font=("Segoe UI", 9))
        style.map("TRadiobutton",
                 background=[("active", self.surface_color)],
                 foreground=[("active", self.fg_color)])
        
        style.configure("TCheckbutton", 
                       background=self.surface_color, 
                       foreground=self.fg_color, 
                       font=("Segoe UI", 9))
        style.map("TCheckbutton",
                 background=[("active", self.surface_color)],
                 foreground=[("active", self.fg_color)])
        
        # Entry
        style.configure(
            "TEntry",
            fieldbackground=self.entry_bg,
            foreground=self.fg_color,
            bordercolor=self.border_color,
            insertcolor=self.fg_color,
            padding=6,
            font=("Segoe UI", 10),
            relief="flat"
        )
        style.map(
            "TEntry",
            bordercolor=[("focus", self.accent_color), ("!focus", self.border_color)],
            lightcolor=[("focus", self.accent_color)],
            darkcolor=[("focus", self.accent_color)]
        )
        
        # Progressbar
        style.configure(
            "TProgressbar",
            background=self.accent_color,
            troughcolor=self.entry_bg,
            borderwidth=0
        )

        # Treeview (for Broadcast tab)
        style.configure(
            "Treeview",
            background=self.surface_color,
            fieldbackground=self.surface_color,
            foreground=self.fg_color,
            rowheight=22,
            bordercolor=self.border_color
        )
        style.configure(
            "Treeview.Heading",
            background=self.surface2,
            foreground=self.fg_color,
            relief="flat",
            font=("Segoe UI", 9, "bold")
        )
        style.map(
            "Treeview",
            background=[("selected", "#1b2130")],
            foreground=[("selected", self.fg_color)]
        )
        
        # Scrollbar
        style.configure("TScrollbar", 
                       background=self.surface2, 
                       troughcolor=self.surface_color,
                       bordercolor=self.border_color,
                       arrowcolor=self.fg_color,
                       borderwidth=0)
        style.map("TScrollbar",
                 background=[("active", "#18233d")])
        
        # Prevent window from closing on error
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # Message queue for thread-safe GUI updates
        self.message_queue = queue.Queue()
        
        # Update flag
        self.update_running = False
        
        try:
            # Setup UI
            self.setup_ui()
            
            # Start periodic updates
            self.start_periodic_updates()
            
            # Log initial message
            self.log_message("Control Panel initialized")
            self.log_message("📋 Instructions:")
            self.log_message("   1. Click 'Start Data Collector' to begin collecting NIFTY data")
            self.log_message("   2. Select Live or Replay mode")
            self.log_message("   3. Click 'Start' to begin broadcasting")
            
            # Ensure window is visible
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception as e:
            # Show error in a message box instead of crashing
            import traceback
            error_msg = f"Error initializing GUI: {e}\n\n{traceback.format_exc()}"
            print(error_msg)  # Also print to console
            try:
                messagebox.showerror("Initialization Error", error_msg)
            except:
                pass  # If messagebox fails, at least we printed it
            raise
    
    def setup_ui(self):
        """Setup the user interface"""
        
        # Create a canvas with scrollbar for the entire window
        canvas = tk.Canvas(self.root, bg=self.bg_color, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.root, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas, style="Main.TFrame")
        
        # Configure scrollbar cursor
        scrollbar.bind("<Enter>", lambda e: scrollbar.configure(cursor="arrow"))
        scrollbar.bind("<Leave>", lambda e: scrollbar.configure(cursor=""))
        
        # Configure canvas scrolling
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        # Create window in canvas for the scrollable frame
        canvas_window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        
        # Configure canvas to resize with window
        def configure_canvas_width(event):
            canvas_width = event.width
            canvas.itemconfig(canvas_window, width=canvas_width)
        canvas.bind('<Configure>', configure_canvas_width)
        
        # Configure mousewheel scrolling (Windows and Mac)
        def on_mousewheel(event):
            # Windows: event.delta is in multiples of 120
            # Linux: event.delta is typically 4 or 5
            if event.delta:
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            else:
                # Linux: event.num is 4 (scroll up) or 5 (scroll down)
                if event.num == 4:
                    canvas.yview_scroll(-1, "units")
                elif event.num == 5:
                    canvas.yview_scroll(1, "units")
        
        # Bind mousewheel events for different platforms
        canvas.bind_all("<MouseWheel>", on_mousewheel)  # Windows and Mac
        canvas.bind_all("<Button-4>", on_mousewheel)     # Linux scroll up
        canvas.bind_all("<Button-5>", on_mousewheel)    # Linux scroll down
        
        # Pack canvas and scrollbar
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # Connect scrollbar to canvas
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Main container (now inside scrollable frame)
        main_frame = ttk.Frame(scrollable_frame, padding=15, style="Main.TFrame")
        main_frame.pack(fill="both", expand=True)
        
        # Data Collector Control Frame
        collector_frame = ttk.LabelFrame(main_frame, text="Data Collector", padding=self.pad_x, style="Card.TLabelframe")
        collector_frame.pack(fill="x", pady=(0, self.gap_y))
        
        collector_btn_frame = ttk.Frame(collector_frame, style="Card.TFrame")
        collector_btn_frame.pack(fill="x")
        
        self.collector_status = ttk.Label(collector_frame, text="Status: Not started", style="Muted.Card.TLabel")
        self.collector_status.pack(anchor="w", pady=2)
        
        self.start_collector_btn = ttk.Button(collector_btn_frame, text="▶ Start Data Collector", 
                                             command=self.start_data_collector)
        self.start_collector_btn.pack(side="left", padx=5)
        
        self.stop_collector_btn = ttk.Button(collector_btn_frame, text="⏹ Stop Data Collector", 
                                            command=self.stop_data_collector, state="disabled")
        self.stop_collector_btn.pack(side="left", padx=5)
        
        # Track collector process
        self.collector_process = None
        self.collector_running = False
        
        # Track replay metrics (kept for potential future enhancements)
        self._last_replay_wall_time = None
        self._last_replay_index = None
        
        # Track selected vs actual loaded range
        self.selected_start_time = None
        self.selected_end_time = None
        self.first_loaded_timestamp = None
        self.last_loaded_timestamp = None
        
        # Mode Selection Frame - Segmented button toggle
        mode_frame = ttk.LabelFrame(main_frame, text="Mode Selection", padding=self.pad_x, style="Card.TLabelframe")
        mode_frame.pack(fill="x", pady=(0, self.gap_y))
        
        self.mode_var = tk.StringVar(value="LIVE")
        self.live_btn = ttk.Button(mode_frame, text="Live", style="SegOn.TButton",
                                  command=lambda: self._set_mode("LIVE"))
        self.replay_btn = ttk.Button(mode_frame, text="Replay", style="Seg.TButton",
                                    command=lambda: self._set_mode("REPLAY"))
        self.live_btn.pack(side="left", padx=6)
        self.replay_btn.pack(side="left", padx=6)
        
        # Track if broadcasting is active
        self.broadcasting_active = False
        
        # Replay Settings Frame
        self.replay_frame = ttk.LabelFrame(main_frame, text="Replay Settings", padding=self.pad_x, style="Card.TLabelframe")
        self.replay_frame.pack(fill="x", pady=(0, self.gap_y))
        
        # Start Date/Time
        ttk.Label(self.replay_frame, text="Start:", style="Card.TLabel").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.start_picker = DateTimePicker(self.replay_frame, theme_panel=self)
        self.start_picker.grid(row=0, column=1, sticky="w", padx=5, pady=5)
        
        # End Date/Time
        ttk.Label(self.replay_frame, text="End:", style="Card.TLabel").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        self.end_picker = DateTimePicker(self.replay_frame, theme_panel=self)
        # Set end time to today 15:30 (market close)
        end_dt = datetime.now().replace(hour=15, minute=30, second=0, microsecond=0)
        self.end_picker.set_datetime(end_dt)
        self.end_picker.grid(row=1, column=1, sticky="w", padx=5, pady=5)
        
        # Speed selection - Segmented buttons
        ttk.Label(self.replay_frame, text="Speed:", style="Card.TLabel").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        speed_frame = ttk.Frame(self.replay_frame, style="Card.TFrame")
        speed_frame.grid(row=2, column=1, sticky="w", padx=5, pady=5)
        
        self.speed_var = tk.DoubleVar(value=1.0)
        self.speed_buttons = {}
        speed_values = [1, 2, 5, 10, 100]
        speed_labels = ["1x", "2x", "5x", "10x", "Max"]
        
        for speed, label in zip(speed_values, speed_labels):
            btn = ttk.Button(speed_frame, text=label, style="Seg.TButton",
                           command=lambda s=speed: self._set_speed(s))
            btn.pack(side="left", padx=3)
            self.speed_buttons[speed] = btn
        
        # Set initial selected speed button
        self._set_speed(1.0)
        
        # Hide Replay Settings when in Live mode (calendar/date selection only for Replay)
        if self.mode_var.get() == "LIVE":
            self.replay_frame.pack_forget()
        
        # Control Buttons Frame
        self.control_frame = ttk.LabelFrame(main_frame, text="Controls", padding=self.pad_x, style="Card.TLabelframe")
        self.control_frame.pack(fill="x", pady=(0, self.gap_y))
        
        self.start_btn = ttk.Button(self.control_frame, text="▶ Start", style="Primary.TButton", command=self.start_broadcast)
        self.start_btn.pack(side="left", padx=5)
        
        self.pause_btn = ttk.Button(self.control_frame, text="⏸ Pause", command=self.pause_broadcast, state="disabled")
        self.pause_btn.pack(side="left", padx=5)
        
        self.stop_btn = ttk.Button(self.control_frame, text="⏹ Stop", command=self.stop_broadcast, state="disabled")
        self.stop_btn.pack(side="left", padx=5)
        
        self.reset_btn = ttk.Button(self.control_frame, text="↺ Reset", command=self.reset_broadcast, state="disabled")
        self.reset_btn.pack(side="left", padx=5)
        
        # Status Display Frame
        status_frame = ttk.LabelFrame(main_frame, text="Status", padding=self.pad_x, style="Card.TLabelframe")
        status_frame.pack(fill="both", expand=True, pady=(0, self.gap_y))
        
        # Status labels with better fonts
        self.status_mode = ttk.Label(status_frame, text="Current Mode: 🟢 Live (ready - not broadcasting)", 
                                    style="Card.TLabel", font=("Segoe UI", 10, "bold"))
        self.status_mode.pack(anchor="w", pady=3)
        
        self.status_broadcasting = ttk.Label(status_frame, text="Broadcasting: ○ Stopped - Click Start to begin broadcasting",
                                           style="Card.TLabel")
        self.status_broadcasting.pack(anchor="w", pady=3)
        
        self.status_time = ttk.Label(status_frame, text="Current Time: --", style="Card.TLabel")
        self.status_time.pack(anchor="w", pady=3)
        
        # Selected vs Loaded range display (for Replay mode)
        self.status_range = ttk.Label(status_frame, text="", style="Muted.Card.TLabel")
        self.status_range.pack(anchor="w", pady=2)
        
        self.status_records = ttk.Label(status_frame, text="Records: 0 / 0", style="Card.TLabel")
        self.status_records.pack(anchor="w", pady=3)
        
        # Progress bar
        self.progress = ttk.Progressbar(status_frame, mode="determinate", length=400)
        self.progress.pack(fill="x", pady=(8, 6))
        
        # Extra replay status line (Now/Speed)
        self.status_replay_metrics = ttk.Label(status_frame, text="", style="Muted.Card.TLabel")
        self.status_replay_metrics.pack(anchor="w", pady=(2, 0))
        
        # Keep a spacer label for layout stability (ETA removed)
        self.status_replay_eta = ttk.Label(status_frame, text="", style="Muted.Card.TLabel")
        self.status_replay_eta.pack(anchor="w", pady=(0, 6))
        
        self.status_clients = ttk.Label(status_frame, text="Clients: 0 connected", style="Card.TLabel")
        self.status_clients.pack(anchor="w", pady=3)
        
        # VPS Sync Frame
        vps_frame = ttk.LabelFrame(main_frame, text="VPS Sync", padding=self.pad_x, style="Card.TLabelframe")
        vps_frame.pack(fill="x", pady=(0, self.gap_y))
        
        # Local DB status
        self.local_db_status = ttk.Label(vps_frame, text="Local DB: Checking...", style="Card.TLabel")
        self.local_db_status.pack(anchor="w", pady=2)
        
        # VPS DB status
        self.vps_db_status = ttk.Label(vps_frame, text="VPS DB: Not checked", style="Card.TLabel")
        self.vps_db_status.pack(anchor="w", pady=2)
        
        # Gap status
        self.gap_status = ttk.Label(vps_frame, text="Gap Status: Checking...", style="Card.TLabel", foreground="#ffa500")
        self.gap_status.pack(anchor="w", pady=2)
        
        # Gap summary (detailed information)
        self.gap_summary = ttk.Label(vps_frame, text="", style="Card.TLabel", foreground="#4a9eff", wraplength=600)
        self.gap_summary.pack(anchor="w", pady=2)
        
        # Date Range Selection Frame
        range_frame = ttk.LabelFrame(vps_frame, text="Sync Date Range", padding=self.pad_x, style="Card.TLabelframe")
        range_frame.pack(fill="x", pady=(0, 5))
        
        # Radio buttons for range selection
        self.range_var = tk.StringVar(value="7")  # Default to Last 7 Days
        range_options = [
            ("Last 7 Days", "7"),
            ("Last 14 Days", "14"),
            ("Last 30 Days", "30"),
            ("Custom Days", "custom_days"),
            ("Custom Date Range", "custom_range"),
            ("Full Database", "full")
        ]
        
        range_radio_frame = ttk.Frame(range_frame, style="Card.TFrame")
        range_radio_frame.pack(fill="x", pady=2)
        
        for i, (label, value) in enumerate(range_options):
            rb = ttk.Radiobutton(
                range_radio_frame,
                text=label,
                variable=self.range_var,
                value=value,
                style="Card.TRadiobutton"
            )
            rb.grid(row=i//3, column=i%3, sticky="w", padx=5, pady=2)
        
        # Custom days input (shown when "Custom Days" is selected)
        self.custom_days_frame = ttk.Frame(range_frame, style="Card.TFrame")
        self.custom_days_var = tk.StringVar(value="60")
        ttk.Label(self.custom_days_frame, text="Number of days:", style="Card.TLabel").pack(side="left", padx=5)
        self.custom_days_entry = ttk.Entry(self.custom_days_frame, textvariable=self.custom_days_var, width=10)
        self.custom_days_entry.pack(side="left", padx=5)
        
        # Custom date range inputs (shown when "Custom Date Range" is selected)
        self.custom_range_frame = ttk.Frame(range_frame, style="Card.TFrame")
        ttk.Label(self.custom_range_frame, text="From:", style="Card.TLabel").pack(side="left", padx=5)
        self.custom_from_date = ttk.Entry(self.custom_range_frame, width=12)
        self.custom_from_date.pack(side="left", padx=5)
        self.custom_from_date.insert(0, (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"))
        
        ttk.Label(self.custom_range_frame, text="To:", style="Card.TLabel").pack(side="left", padx=5)
        self.custom_to_date = ttk.Entry(self.custom_range_frame, width=12)
        self.custom_to_date.pack(side="left", padx=5)
        self.custom_to_date.insert(0, datetime.now().strftime("%Y-%m-%d"))
        
        # Update visibility based on selection
        def update_range_ui(*args):
            selected = self.range_var.get()
            if selected == "custom_days":
                self.custom_days_frame.pack(fill="x", pady=2)
                self.custom_range_frame.pack_forget()
            elif selected == "custom_range":
                self.custom_days_frame.pack_forget()
                self.custom_range_frame.pack(fill="x", pady=2)
            else:
                self.custom_days_frame.pack_forget()
                self.custom_range_frame.pack_forget()
        
        self.range_var.trace("w", update_range_ui)
        update_range_ui()  # Initial update
        
        # Sync buttons (store references for hand cursor)
        sync_btn_frame = ttk.Frame(vps_frame, style="Card.TFrame")
        sync_btn_frame.pack(fill="x", pady=5)
        
        self.sync_btn = ttk.Button(sync_btn_frame, text="🔄 Sync with VPS", command=self.sync_with_vps)
        self.sync_btn.pack(side="left", padx=5)
        
        self.gaps_btn = ttk.Button(sync_btn_frame, text="📊 Check Gaps", command=self.check_gaps)
        self.gaps_btn.pack(side="left", padx=5)
        
        self.fill_gaps_btn = ttk.Button(sync_btn_frame, text="🔧 Fill Gaps for Date", command=self.fill_gaps_for_date)
        self.fill_gaps_btn.pack(side="left", padx=5)
        
        self.delete_test_btn = ttk.Button(sync_btn_frame, text="🗑️ Delete Test Data", command=self.delete_test_data)
        self.delete_test_btn.pack(side="left", padx=5)
        
        self.auto_sync_var = tk.BooleanVar(value=False)
        self.auto_sync_chk = ttk.Checkbutton(sync_btn_frame, text="⚙️ Auto-Sync", variable=self.auto_sync_var,
                                            command=self.toggle_auto_sync)
        self.auto_sync_chk.pack(side="left", padx=5)
        
        self.sync_in_terminal_var = tk.BooleanVar(value=True)
        self.sync_in_terminal_chk = ttk.Checkbutton(
            sync_btn_frame,
            text="🖥️ Sync in new terminal (single-line progress bar)",
            variable=self.sync_in_terminal_var
        )
        self.sync_in_terminal_chk.pack(side="left", padx=5)
        
        # Sync progress bar
        self.sync_progress = ttk.Progressbar(vps_frame, mode="determinate", length=400)
        self.sync_progress.pack(fill="x", pady=5)
        self.sync_progress_label = ttk.Label(vps_frame, text="", style="Card.TLabel")
        self.sync_progress_label.pack(anchor="w", pady=2)
        
        self.last_sync_label = ttk.Label(vps_frame, text="Last Sync: Never", style="Muted.Card.TLabel")
        self.last_sync_label.pack(anchor="w", pady=2)
        
        # Logs Display Frame
        logs_frame = ttk.LabelFrame(main_frame, text="Logs", padding=self.pad_x, style="Card.TLabelframe")
        logs_frame.pack(fill="both", expand=True, pady=(0, 0))

        # Notebook with two tabs: Logs + Broadcast
        self.logs_notebook = ttk.Notebook(logs_frame)
        self.logs_notebook.pack(fill="both", expand=True)

        logs_tab = ttk.Frame(self.logs_notebook, style="Card.TFrame")
        broadcast_tab = ttk.Frame(self.logs_notebook, style="Card.TFrame")
        self.logs_notebook.add(logs_tab, text="Logs")
        self.logs_notebook.add(broadcast_tab, text="Broadcast")

        # --- Logs tab ---
        self.logs_scrollbar = ttk.Scrollbar(logs_tab)
        self.logs_scrollbar.pack(side="right", fill="y")

        self.logs_text = tk.Text(
            logs_tab,
            height=8,
            state="disabled",
            yscrollcommand=self.logs_scrollbar.set,
            bg=self.entry_bg,
            fg=self.fg_color,
            insertbackground=self.fg_color,
            selectbackground=self.accent_color,
            selectforeground=self.fg_color,
            borderwidth=1,
            relief="solid",
            highlightthickness=1,
            highlightbackground=self.border_color,
            highlightcolor=self.accent_color,
            font=("Consolas", 10)
        )
        self.logs_text.pack(side="left", fill="both", expand=True)
        self.logs_scrollbar.config(command=self.logs_text.yview)

        # --- Broadcast tab (table) ---
        top_bar = ttk.Frame(broadcast_tab, style="Card.TFrame")
        top_bar.pack(fill="x", pady=(0, 6))

        self.show_broadcast_log_var = tk.BooleanVar(value=True)
        self.show_broadcast_log_chk = ttk.Checkbutton(
            top_bar,
            text="Capture broadcast data",
            variable=self.show_broadcast_log_var
        )
        self.show_broadcast_log_chk.pack(side="left", padx=(0, 10))

        self.broadcast_max_rows = 5000
        self.broadcast_row_count_var = tk.StringVar(value="Rows: 0")
        ttk.Label(top_bar, textvariable=self.broadcast_row_count_var, style="Muted.Card.TLabel").pack(side="left")

        def _clear_broadcast_table():
            try:
                for iid in self.broadcast_tree.get_children():
                    self.broadcast_tree.delete(iid)
                self.broadcast_row_count_var.set("Rows: 0")
            except Exception:
                pass

        ttk.Button(top_bar, text="Clear", command=_clear_broadcast_table).pack(side="right")

        table_frame = ttk.Frame(broadcast_tab, style="Card.TFrame")
        table_frame.pack(fill="both", expand=True)

        cols = ("date", "time", "mode", "symbol", "ltp", "volume", "oi", "iv", "source", "clients")
        self.broadcast_tree = ttk.Treeview(table_frame, columns=cols, show="headings", height=10)

        headings = {
            "date": "Date",
            "time": "Time",
            "mode": "Mode",
            "symbol": "Symbol",
            "ltp": "LTP",
            "volume": "Volume",
            "oi": "OI",
            "iv": "IV",
            "source": "Source",
            "clients": "Clients",
        }
        widths = {
            "date": 95,
            "time": 85,
            "mode": 70,
            "symbol": 220,
            "ltp": 70,
            "volume": 80,
            "oi": 70,
            "iv": 60,
            "source": 80,
            "clients": 60,
        }
        for c in cols:
            self.broadcast_tree.heading(c, text=headings[c])
            self.broadcast_tree.column(c, width=widths[c], anchor="w", stretch=(c == "symbol"))

        self.broadcast_tree.tag_configure("LIVE", foreground="#4caf50")
        self.broadcast_tree.tag_configure("REPLAY", foreground="#4aa3ff")

        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.broadcast_tree.yview)
        xscroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.broadcast_tree.xview)
        self.broadcast_tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        self.broadcast_tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")
        xscroll.pack(side="bottom", fill="x")
        
        # Add hand cursor to clickable widgets
        self._add_hand_cursors()
        
        # Initial status check (delay to ensure GUI main loop is running)
        # Only check local DB status and collector status on startup
        # Gap check will be done only when user clicks "Check Gaps" button
        self.root.after(500, self.update_local_db_status)
        self.root.after(1000, self.check_collector_status)

        # Hook broadcaster debug logs into GUI log panel
        if self.broadcaster and hasattr(self.broadcaster, "set_broadcast_log_callback"):
            try:
                self.broadcaster.set_broadcast_log_callback(self._on_broadcast_log)
            except Exception:
                pass

    def _on_broadcast_log(self, info: dict):
        """Receive broadcast debug info from broadcaster thread and enqueue it for GUI logging."""
        try:
            if not getattr(self, "show_broadcast_log_var", None) or not self.show_broadcast_log_var.get():
                return
        except Exception:
            # If toggle isn't available for some reason, default to logging
            pass

        try:
            mode = info.get("mode", "?")
            rows = info.get("rows") or []
            self.message_queue.put({"type": "broadcast_rows", "mode": mode, "rows": rows})
        except Exception:
            pass

    def _append_broadcast_rows_ui(self, rows: list, mode: str):
        """Append rows to broadcast Treeview (must be called on main thread)."""
        if not hasattr(self, "broadcast_tree"):
            return
        try:
            for r in rows:
                if not isinstance(r, dict):
                    continue
                ts = r.get("ts")
                ts_disp = format_timestamp_for_display(ts) if ts else "--"
                parts = ts_disp.split()
                date_s = parts[0] if len(parts) >= 1 else "--"
                time_s = parts[1] if len(parts) >= 2 else "--"

                self.broadcast_tree.insert(
                    "",
                    "end",
                    values=(
                        date_s,
                        time_s,
                        mode,
                        r.get("symbol", ""),
                        r.get("ltp", ""),
                        r.get("volume", ""),
                        r.get("oi", ""),
                        r.get("iv", ""),
                        r.get("source", ""),
                        r.get("clients", 0),
                    ),
                    tags=(mode,)
                )

            children = self.broadcast_tree.get_children()
            if len(children) > self.broadcast_max_rows:
                for iid in children[: len(children) - self.broadcast_max_rows]:
                    self.broadcast_tree.delete(iid)

            self.broadcast_row_count_var.set(f"Rows: {len(self.broadcast_tree.get_children())}")
            self.broadcast_tree.yview_moveto(1.0)
        except Exception:
            pass
    
    def _add_hand_cursors(self):
        """Add appropriate cursors to all interactive widgets"""
        def make_hand_cursor(widget):
            widget.bind("<Enter>", lambda e: widget.configure(cursor="hand2"))
            widget.bind("<Leave>", lambda e: widget.configure(cursor=""))
        
        def hand_if_enabled(widget):
            """Hand cursor if enabled, not-allowed if disabled"""
            def on_enter(_):
                cur_state = str(widget.cget("state"))
                widget.configure(cursor="hand2" if cur_state != "disabled" else "no")
            widget.bind("<Enter>", on_enter)
            widget.bind("<Leave>", lambda e: widget.configure(cursor=""))
        
        def set_cursor(widget, cursor_name: str):
            """Set cursor for widget"""
            widget.bind("<Enter>", lambda e: widget.configure(cursor=cursor_name))
            widget.bind("<Leave>", lambda e: widget.configure(cursor=""))
        
        # Apply hand cursor to enabled buttons (with disabled check)
        for widget in [self.start_collector_btn, self.stop_collector_btn,
                      self.start_btn, self.pause_btn, self.stop_btn, self.reset_btn,
                      self.live_btn, self.replay_btn,
                      self.sync_btn, self.gaps_btn, self.fill_gaps_btn, self.delete_test_btn]:
            hand_if_enabled(widget)
        
        # Checkboxes always get hand cursor
        make_hand_cursor(self.auto_sync_chk)
        if hasattr(self, "sync_in_terminal_chk"):
            make_hand_cursor(self.sync_in_terminal_chk)
        
        # Speed buttons
        for btn in self.speed_buttons.values():
            hand_if_enabled(btn)
        
        # I-beam cursor for Entry widgets (in DateTimePicker)
        def add_ibeam_to_entries(parent):
            """Recursively add I-beam cursor to all Entry widgets"""
            for child in parent.winfo_children():
                if isinstance(child, ttk.Entry):
                    set_cursor(child, "xterm")
                elif hasattr(child, 'winfo_children'):
                    add_ibeam_to_entries(child)
        
        add_ibeam_to_entries(self.replay_frame)
        
        # I-beam cursor for Text widget (logs)
        self.logs_text.configure(cursor="xterm")
        
        # Cursor for scrollbar (arrow for up/down navigation)
        if hasattr(self, 'logs_scrollbar'):
            self.logs_scrollbar.bind("<Enter>", lambda e: self.logs_scrollbar.configure(cursor="arrow"))
            self.logs_scrollbar.bind("<Leave>", lambda e: self.logs_scrollbar.configure(cursor=""))
    
    def start_data_collector(self):
        """Start the VPS Data Collector in a separate terminal window"""
        try:
            import subprocess
            import sys
            
            # Check if already running
            if self.collector_running:
                self.log_message("⚠️ Data Collector is already running")
                return
            
            # Path to the collector script
            collector_script = Path(__file__).parent.parent / "vps_system" / "nifty_stream_local_sqlite.py"
            
            if not collector_script.exists():
                self.log_message(f"❌ Data Collector script not found: {collector_script}")
                messagebox.showerror("Error", f"Data Collector script not found:\n{collector_script}")
                return
            
            # Determine Python command
            python_cmd = "py"
            try:
                import shutil
                if not shutil.which("py"):
                    python_cmd = "python"
            except:
                python_cmd = "python"
            
            # Start collector in new terminal window
            collector_script_str = str(collector_script)
            cmd = f'start "VPS Data Collector" {python_cmd} "{collector_script_str}"'
            self.collector_process = subprocess.Popen(cmd, shell=True)
            
            self.collector_running = True
            self.start_collector_btn.config(state="disabled")
            self.stop_collector_btn.config(state="normal")
            self.collector_status.config(text="Status: Running (check terminal window)", foreground="#4caf50")
            self.log_message("✅ Data Collector started in separate terminal window")
            self.log_message(f"   Script: {collector_script.name}")
            self.log_message("   The collector will login and start collecting NIFTY data every 5 seconds")
            
        except Exception as e:
            self.log_message(f"❌ Error starting Data Collector: {e}")
            messagebox.showerror("Error", f"Failed to start Data Collector:\n{e}")
    
    def stop_data_collector(self):
        """Stop the VPS Data Collector"""
        try:
            import subprocess
            
            if not self.collector_running:
                self.log_message("⚠️ Data Collector is not running")
                return
            
            # Try to find and kill the process
            try:
                result = subprocess.run(
                    ["powershell", "-ExecutionPolicy", "Bypass", "-Command",
                     "Get-Process python -ErrorAction SilentlyContinue | "
                     "Where-Object { "
                     "$cmd = (Get-WmiObject Win32_Process -Filter \"ProcessId = $($_.Id)\").CommandLine; "
                     "$cmd -and $cmd -like '*nifty_stream_local_sqlite.py*' "
                     "} | Stop-Process -Force"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                
                if result.returncode == 0 or "Stop-Process" in result.stdout or result.stdout.strip() == "":
                    self.collector_running = False
                    self.start_collector_btn.config(state="normal")
                    self.stop_collector_btn.config(state="disabled")
                    self.collector_status.config(text="Status: Stopped", style="Muted.Card.TLabel")
                    self.log_message("✅ Data Collector stopped")
                else:
                    self.log_message("⚠️ Could not stop Data Collector - you may need to close the terminal window manually")
            except Exception as e:
                self.log_message(f"⚠️ Error stopping Data Collector: {e}")
                self.log_message("   You may need to close the terminal window manually")
                
        except Exception as e:
            self.log_message(f"❌ Error stopping Data Collector: {e}")
    
    def check_collector_status(self):
        """Check if Data Collector is running (periodic check)"""
        try:
            import subprocess
            check_script = Path(__file__).parent.parent / "scripts" / "check_vps_process.ps1"
            if check_script.exists():
                result = subprocess.run(
                    ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(check_script)],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                is_running = (result.returncode == 0)
                
                if is_running != self.collector_running:
                    self.collector_running = is_running
                    if is_running:
                        self.start_collector_btn.config(state="disabled")
                        self.stop_collector_btn.config(state="normal")
                        self.collector_status.config(text="Status: Running", foreground="#4caf50")
                    else:
                        self.start_collector_btn.config(state="normal")
                        self.stop_collector_btn.config(state="disabled")
                        self.collector_status.config(text="Status: Not started", style="Muted.Card.TLabel")
        except:
            pass  # Silently fail - don't spam errors
        
        # Schedule next check
        self.root.after(5000, self.check_collector_status)  # Check every 5 seconds
    
    def _set_speed(self, speed: float):
        """Set speed and update button visual selection"""
        self.speed_var.set(speed)
        
        # Update button visuals
        for spd, btn in self.speed_buttons.items():
            if spd == speed:
                btn.configure(style="SegOn.TButton")
            else:
                btn.configure(style="Seg.TButton")
    
    def get_db_date_range(self) -> tuple:
        """Return (min_date, max_date) from PostgreSQL ltp_ticks, or (None, None) if no data."""
        try:
            conn = db.get_connection()
            if not db.table_exists(conn, "ltp_ticks"):
                conn.close()
                return (None, None)
            cursor = conn.cursor()
            cursor.execute("SELECT MIN(ts), MAX(ts) FROM ltp_ticks")
            row = cursor.fetchone()
            conn.close()
            if not row or not row[0] or not row[1]:
                return (None, None)
            min_ts, max_ts = str(row[0])[:10], str(row[1])[:10]
            min_date = datetime.strptime(min_ts, "%Y-%m-%d").date()
            max_date = datetime.strptime(max_ts, "%Y-%m-%d").date()
            return (min_date, max_date)
        except Exception:
            return (None, None)
    
    def _set_mode(self, mode: str):
        """Set mode and update button visual selection"""
        # Don't allow mode change if broadcasting is active
        if self.broadcasting_active:
            # Revert to previous mode
            current_mode = self.broadcaster.get_mode() if self.broadcaster else "LIVE"
            self.mode_var.set(current_mode)
            # Update button visuals to match current mode
            if current_mode == "LIVE":
                self.live_btn.configure(style="SegOn.TButton")
                self.replay_btn.configure(style="Seg.TButton")
            else:
                self.live_btn.configure(style="Seg.TButton")
                self.replay_btn.configure(style="SegOn.TButton")
            self.log_message("⚠️ Cannot change mode while broadcasting. Please stop first.")
            return
        
        self.mode_var.set(mode)
        
        # Visual selection
        if mode == "LIVE":
            self.live_btn.configure(style="SegOn.TButton")
            self.replay_btn.configure(style="Seg.TButton")
        else:
            self.live_btn.configure(style="Seg.TButton")
            self.replay_btn.configure(style="SegOn.TButton")
        
        # Call the mode change handler
        self.on_mode_change()
    
    def on_mode_change(self):
        """Handle mode selection change (non-blocking, no database loading)"""
        mode = self.mode_var.get()
        if self.broadcaster:
            # Change mode immediately (non-blocking, no database operations)
            old_mode = self.broadcaster.get_mode()
            self.broadcaster.set_mode(mode)
            self.log_message(f"Mode selected: {mode}")
            
            # Show/hide replay settings based on mode (calendar only for Replay)
            if mode == "REPLAY":
                # Show and enable replay settings
                self.replay_frame.pack(fill="x", pady=(0, self.gap_y), before=self.control_frame)
                for widget in self.replay_frame.winfo_children():
                    try:
                        widget.config(state="normal")
                    except Exception:
                        pass
                for btn in self.speed_buttons.values():
                    btn.config(state="normal")
                self.log_message("Configure replay settings (date range, speed) and click Start to load and begin replay")
            else:
                # Hide replay settings in Live mode (no date selection needed)
                self.replay_frame.pack_forget()
                self.log_message("Live mode selected - click Start to begin monitoring database")
            
            # Update UI
            self.update_status_display()
                
            # Reset button states when switching modes
            self.start_btn.config(state="normal")
            self.pause_btn.config(state="disabled")
            self.stop_btn.config(state="disabled")
            self.reset_btn.config(state="disabled")
    
    def start_broadcast(self):
        """Start broadcasting (non-blocking)"""
        if not self.broadcaster:
            messagebox.showerror("Error", "Broadcaster not connected")
            return
        
        mode = self.mode_var.get()
        
        # Disable start button immediately to prevent double-clicks
        self.start_btn.config(state="disabled")
        self.log_message("Starting broadcast...")
        
        # Run in background thread to avoid blocking GUI
        def start_in_background():
            try:
                if mode == "REPLAY":
                    # Get replay settings
                    try:
                        start_dt = self.start_picker.get_datetime()
                        end_dt = self.end_picker.get_datetime()
                        speed = self.speed_var.get()
                        
                        if start_dt >= end_dt:
                            self.root.after(0, lambda: (
                                messagebox.showerror("Error", "Start time must be before end time"),
                                self.start_btn.config(state="normal")
                            ))
                            return
                        
                        # Convert IST datetime to UTC for database query
                        # Database stores timestamps in UTC, so we need to convert IST to UTC
                        # Assume the datetime picker gives IST time (09:15 IST = 03:45 UTC)
                        from datetime import timezone, timedelta
                        ist_offset = timedelta(hours=5, minutes=30)
                        # Treat the datetime as IST and convert to UTC
                        start_dt_ist = start_dt.replace(tzinfo=timezone(ist_offset))
                        end_dt_ist = end_dt.replace(tzinfo=timezone(ist_offset))
                        start_dt_utc = start_dt_ist.astimezone(timezone.utc)
                        end_dt_utc = end_dt_ist.astimezone(timezone.utc)
                        
                        # Convert to canonical DB timestamp strings (UTC, no timezone suffix)
                        # DB stores `ts` as TEXT like "YYYY-MM-DDTHH:MM:SS" (no "+00:00"/"Z").
                        start_iso = start_dt_utc.strftime("%Y-%m-%dT%H:%M:%S")
                        end_iso = end_dt_utc.strftime("%Y-%m-%dT%H:%M:%S")
                        
                        # Log the conversion for debugging
                        start_ist_str = start_dt.strftime("%Y-%m-%d %H:%M:%S IST")
                        start_utc_str = start_dt_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
                        
                        # Update GUI: Show loading message
                        self.root.after(0, lambda: (
                            self.log_message(f"Configuring replay: {start_ist_str} ({start_utc_str}) to {end_iso} UTC at {speed}x speed"),
                            self.status_records.config(text="Loading records from database..."),
                            self.status_time.config(text="Please wait, this may take a moment...")
                        ))
                        
                        # Configure replay settings first
                        self.broadcaster.set_replay_config(start_iso, end_iso, speed)
                        
                        # Now load the records (this is the time-consuming part)
                        self.root.after(0, lambda: (
                            self.log_message("Loading replay records from database (this may take a while)..."),
                            self.status_records.config(text="Loading records from database...")
                        ))
                        
                        # Load records with error handling
                        try:
                            success = self.broadcaster.load_replay_records()
                        except Exception as load_error:
                            import traceback
                            error_msg = f"Exception while loading records: {load_error}"
                            print(f"[Load Error] {error_msg}")
                            print(traceback.format_exc())
                            self.root.after(0, lambda err=error_msg: (
                                messagebox.showerror("Error", f"Failed to load replay records:\n{err}"),
                                self.start_btn.config(state="normal"),
                                self.status_records.config(text="Failed to load records"),
                                self.log_message(f"❌ Error: {err}")
                            ))
                            return
                        
                        if not success:
                            self.root.after(0, lambda: (
                                messagebox.showerror("Error", "Failed to load replay records. Check date range and database."),
                                self.start_btn.config(state="normal"),
                                self.status_records.config(text="Failed to load records"),
                                self.log_message("Failed to load replay records")
                            ))
                            return
                        
                        # Check if first loaded record matches selected start time
                        with self.broadcaster.replay_config_lock:
                            loaded_records = self.broadcaster.replay_config.get('replay_records', [])
                        
                        if loaded_records:
                            first_record_ts = loaded_records[0].get('ts')
                            if first_record_ts:
                                try:
                                    # Parse first record timestamp (assume UTC if tz is missing)
                                    first_ts_dt = datetime.fromisoformat(first_record_ts.replace('Z', '+00:00'))
                                    if first_ts_dt.tzinfo is None:
                                        first_ts_dt = first_ts_dt.replace(tzinfo=timezone.utc)
                                    # Compare with selected start (converted to UTC)
                                    time_diff_sec = (first_ts_dt - start_dt_utc).total_seconds()
                                    
                                    if time_diff_sec > 60:  # More than 1 minute difference
                                        # First record is after selected start - data gap exists
                                        time_diff_hours = time_diff_sec / 3600
                                        first_ts_ist = format_timestamp_for_display(first_record_ts)
                                        
                                        # Log detailed warning
                                        self.root.after(0, lambda: (
                                            self.log_message(f"⚠️ WARNING: Data gap detected!"),
                                            self.log_message(f"   Selected start: {self.selected_start_time} ({start_iso})"),
                                            self.log_message(f"   First available: {first_ts_ist} ({first_record_ts})"),
                                            self.log_message(f"   Gap: {time_diff_hours:.2f} hours ({int(time_diff_sec/60)} minutes)"),
                                            self.log_message(f"   ⚠️ Replay will start from first available record, not selected start time!")
                                        ))
                                        
                                        # Also show in status
                                        self.root.after(0, lambda: (
                                            self.status_records.config(text=f"⚠️ Starting from {first_ts_ist.split()[1] if ' ' in first_ts_ist else first_ts_ist} (gap: {time_diff_hours:.1f}h)")
                                        ))
                                    elif time_diff_sec < -60:  # First record is before selected start (shouldn't happen)
                                        self.root.after(0, lambda: (
                                            self.log_message(f"⚠️ First record ({format_timestamp_for_display(first_record_ts)}) is before selected start time"),
                                            self.log_message(f"   This shouldn't happen - check query logic")
                                        ))
                                    else:
                                        # Within 1 minute - close enough
                                        self.root.after(0, lambda: (
                                            self.log_message(f"✅ Replay starting at selected time (within 1 minute tolerance)")
                                        ))
                                except Exception as e:
                                    # If parsing fails, just log it
                                    import traceback
                                    self.root.after(0, lambda err=str(e): (
                                        self.log_message(f"⚠️ Could not verify start time match: {err}"),
                                        self.log_message(traceback.format_exc())
                                    ))
                        
                        # Ensure replay starts from the beginning (reset index)
                        # This is important if replay was previously running
                        with self.broadcaster.replay_config_lock:
                            self.broadcaster.replay_config['replay_index'] = 0
                            self.broadcaster.replay_config['current_position'] = 0
                            self.broadcaster.replay_config['current_timestamp'] = None
                        
                        # Set mode to REPLAY and start broadcasting after records are loaded
                        self.broadcaster.set_mode("REPLAY")
                        self.broadcaster.start_broadcasting()
                        self.broadcasting_active = True
                        
                        # Get record count and timestamp range for display
                        progress = self.broadcaster.get_replay_progress()
                        total = progress.get('total', 0)
                        
                        # Store selected range (in IST for display)
                        self.selected_start_time = start_dt.strftime("%Y-%m-%d %H:%M:%S IST")
                        self.selected_end_time = end_dt.strftime("%Y-%m-%d %H:%M:%S IST")
                        
                        # Get first and last record timestamps if available
                        first_ts_msg = ""
                        if self.broadcaster.replay_config.get('replay_records'):
                            records = self.broadcaster.replay_config['replay_records']
                            if records:
                                first_record = records[0]
                                last_record = records[-1]
                                first_ts = first_record.get('ts', 'N/A')
                                last_ts = last_record.get('ts', 'N/A')
                                
                                # Store for status display
                                self.first_loaded_timestamp = first_ts
                                self.last_loaded_timestamp = last_ts
                                
                                first_ts_formatted = format_timestamp_for_display(first_ts) if first_ts != 'N/A' else 'N/A'
                                last_ts_formatted = format_timestamp_for_display(last_ts) if last_ts != 'N/A' else 'N/A'
                                # Hide "Loaded ..." info in UI/logs (can be confusing during testing)
                                first_ts_msg = ""
                                
                                # Log warning if first record doesn't match selected start
                                if first_ts != 'N/A':
                                    try:
                                        first_ts_dt = datetime.fromisoformat(first_ts.replace('Z', '+00:00'))
                                        start_utc_dt = start_dt_utc
                                        if first_ts_dt > start_utc_dt:
                                            time_diff = (first_ts_dt - start_utc_dt).total_seconds() / 3600
                                            self.log_message(f"⚠️ Warning: First record is {time_diff:.1f} hours after selected start time")
                                            self.log_message(f"   Selected: {self.selected_start_time}")
                                            self.log_message(f"   First available: {first_ts_formatted}")
                                    except:
                                        pass
                        else:
                            self.first_loaded_timestamp = None
                            self.last_loaded_timestamp = None
                        
                        self.root.after(0, lambda total_rec=total, ts_msg=first_ts_msg: (
                            self.log_message(f"✅ Loaded {total_rec:,} records. Starting replay...{ts_msg}"),
                            self.live_btn.config(state="disabled"),
                            self.replay_btn.config(state="disabled")
                        ))
                        
                    except ValueError as e:
                        self.root.after(0, lambda err=str(e): (
                            messagebox.showerror("Error", f"Invalid date/time: {err}"),
                            self.start_btn.config(state="normal")
                        ))
                        return
                else:
                    # Live mode - ensure mode is set and start broadcasting
                    # NOTE: Live mode does NOT start the VPS Data Collector
                    # The collector must be started separately via start_all_services.bat
                    self.broadcaster.set_mode("LIVE")
                    
                    # Ensure broadcaster service is running
                    if not self.broadcaster.running:
                        self.root.after(0, lambda: (
                            self.log_message("⚠️ Broadcaster service is not running!"),
                            self.start_btn.config(state="normal")
                        ))
                        return
                    
                    # Check if VPS collector is running (optional check)
                    collector_running = None
                    try:
                        import subprocess
                        check_script = Path(__file__).parent.parent / "scripts" / "check_vps_process.ps1"
                        if check_script.exists():
                            result = subprocess.run(
                                ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(check_script)],
                                capture_output=True,
                                text=True,
                                timeout=3
                            )
                            # Script returns 0 if running, non-zero if not
                            collector_running = (result.returncode == 0)
                    except:
                        collector_running = None  # Couldn't check
                    
                    # Start broadcasting
                    self.broadcaster.start_broadcasting()
                    self.broadcasting_active = True
                    
                    # Log status
                    if collector_running is False:
                        self.root.after(0, lambda: (
                            self.log_message("⚠️ VPS Data Collector does not appear to be running"),
                            self.log_message("   Start it separately using: start_all_services.bat"),
                            self.log_message("   Live mode will monitor database once collector starts writing data")
                        ))
                    elif collector_running is True:
                        self.root.after(0, lambda: self.log_message("✅ VPS Data Collector is running - monitoring for new data"))
                    else:
                        self.root.after(0, lambda: self.log_message("📡 Live mode active - monitoring database for new records"))
                    
                    # Open new terminal window to show Live mode logs
                    try:
                        import subprocess
                        import sys
                        
                        # Create a script to monitor and display Live mode activity
                        scripts_dir = Path(__file__).parent.parent / "scripts"
                        scripts_dir.mkdir(parents=True, exist_ok=True)
                        monitor_script = scripts_dir / "monitor_live_mode.py"
                        
                        # Use existing PostgreSQL monitor script (scripts/monitor_live_mode.py)
                        # No need to generate - script already uses DATABASE_URL
                        
                        # Open in new terminal window
                        if sys.platform == "win32":
                            # Use proper Windows command to open new terminal
                            monitor_script_str = str(monitor_script)
                            # Escape the path properly for Windows
                            cmd = f'start cmd /k py "{monitor_script_str}"'
                            subprocess.Popen(cmd, shell=True)
                            self.root.after(0, lambda: self.log_message("📺 Live mode monitor opened in new terminal window"))
                    except Exception as e:
                        self.root.after(0, lambda err=str(e): self.log_message(f"⚠️ Could not open monitor window: {err}"))
                    
                    # Check if PostgreSQL has data
                    try:
                        conn = db.get_connection()
                        if db.table_exists(conn, "ltp_ticks"):
                            cursor = conn.cursor()
                            cursor.execute("SELECT COUNT(*) FROM ltp_ticks")
                            count = cursor.fetchone()[0]
                            cursor.execute("SELECT MAX(ts) FROM ltp_ticks")
                            latest_ts = cursor.fetchone()[0]
                            conn.close()
                            ts_str = str(latest_ts)[:19] if latest_ts else 'N/A'
                            self.root.after(0, lambda cnt=count, ts=ts_str: (
                                self.log_message("✅ Live mode started - monitoring database for new records"),
                                self.log_message(f"   Database: {cnt:,} total records, Latest: {ts}")
                            ))
                        else:
                            conn.close()
                            self.root.after(0, lambda: (
                                self.log_message("⚠️ Table ltp_ticks not found - run sync or init first"),
                                self.log_message("   Set DATABASE_URL and run sync_nifty_db.py")
                            ))
                    except Exception as e:
                        self.root.after(0, lambda err=str(e): self.log_message(
                            f"⚠️ Live mode started but database check failed: {err}"
                        ))
                
                # Update button states and disable other mode radio button
                self.root.after(0, lambda: (
                    self.pause_btn.config(state="disabled"),  # Pause not applicable to Live mode
                    self.stop_btn.config(state="normal"),
                    self.reset_btn.config(state="disabled"),  # Reset not applicable to Live mode
                    self.live_btn.config(state="disabled" if mode == "REPLAY" else "normal"),
                    self.replay_btn.config(state="disabled" if mode == "LIVE" else "normal"),
                    self.update_status_display()
                ))
            except Exception as e:
                self.root.after(0, lambda err=str(e): (
                    self.log_message(f"Error starting broadcast: {err}"),
                    self.start_btn.config(state="normal"),
                    messagebox.showerror("Error", f"Failed to start broadcast: {err}")
                ))
        
        # Start in background thread
        threading.Thread(target=start_in_background, daemon=True).start()
    
    def pause_broadcast(self):
        """Pause broadcasting (only works for Replay mode)"""
        if not self.broadcaster:
            return
        
        mode = self.broadcaster.get_mode()
        if mode == "REPLAY":
            if self.broadcaster.replay_config['paused']:
                self.broadcaster.resume_replay()
                self.pause_btn.config(text="⏸ Pause")
                self.log_message("Replay resumed")
            else:
                self.broadcaster.pause_replay()
                self.pause_btn.config(text="▶ Resume")
                self.log_message("Replay paused")
        else:
            self.log_message("Pause is only available in Replay mode")
    
    def stop_broadcast(self):
        """Stop broadcasting"""
        if not self.broadcaster:
            return
        
        mode = self.broadcaster.get_mode()
        
        # Stop broadcasting
        self.broadcaster.stop_broadcasting()
        self.broadcasting_active = False
        
        if mode == "REPLAY":
            self.broadcaster.stop_replay()
            self.log_message("Replay stopped")
        else:
            self.log_message("Live mode stopped")
        
        # Reset button states and re-enable mode selection
        self.start_btn.config(state="normal")
        self.pause_btn.config(state="disabled")
        self.stop_btn.config(state="disabled")
        self.reset_btn.config(state="disabled")
        self.live_btn.config(state="normal")
        self.replay_btn.config(state="normal")
        
        self.update_status_display()
    
    def reset_broadcast(self):
        """Reset replay"""
        if not self.broadcaster:
            return
        
        self.broadcaster.stop_replay()
        self.log_message("Replay reset")
        self.update_status_display()
    
    def sync_with_vps(self):
        """Sync with VPS (with progress bar)"""
        if getattr(self, "sync_in_terminal_var", None) and self.sync_in_terminal_var.get():
            # Run in new terminal window - progress bar displays on single line
            self._sync_in_new_terminal()
        else:
            self.log_message("Starting sync with VPS...")
            self.sync_progress['value'] = 0
            self.sync_progress_label.config(text="Preparing sync...")
            threading.Thread(target=self._sync_thread, daemon=True).start()
    
    def _sync_in_new_terminal(self):
        """Run sync in a new terminal window for single-line progress bar display"""
        import subprocess
        import sys as _sys
        from datetime import datetime
        proj_root = Path(__file__).parent.parent
        sync_script = Path(__file__).parent / "sync_nifty_db.py"
        if not sync_script.exists():
            self.log_message("❌ sync_nifty_db.py not found")
            return
        self.log_message("🖥️ Starting sync in new terminal window (progress bar will show there)...")
        self.sync_progress['value'] = 10
        self.sync_progress_label.config(text="Sync running in terminal window...")
        try:
            cmd = [_sys.executable, str(sync_script)]
            creation_flags = 0
            if _sys.platform == "win32":
                creation_flags = subprocess.CREATE_NEW_CONSOLE
            proc = subprocess.Popen(
                cmd,
                cwd=str(proj_root),
                creationflags=creation_flags,
            )
            self.log_message("   Check the new terminal window for sync progress")
            # Wait for completion in background, then update GUI
            def wait_and_notify():
                proc.wait()
                st = datetime.now().strftime("%Y-%m-%d %H:%M")
                if proc.returncode == 0:
                    msg = "✅ Sync completed successfully"
                    def on_success():
                        self.sync_progress.config(value=100)
                        self.sync_progress_label.config(text=msg, foreground="#4caf50")
                        self.log_message(msg)
                        self.last_sync_label.config(text=f"Last Sync: {st}", foreground="#4caf50")
                        self.update_local_db_status()
                        self.root.after(1500, self.check_gaps)  # Refresh gap status after sync
                    self.root.after(0, on_success)
                else:
                    msg = f"❌ Sync failed (exit code {proc.returncode}) - check terminal for details"
                    self.root.after(0, lambda: (
                        self.sync_progress.config(value=0),
                        self.sync_progress_label.config(text=msg, foreground="#f44336"),
                        self.log_message(msg),
                        self.last_sync_label.config(text=f"Last Sync: Failed ({st})", foreground="#f44336"),
                    ))
            threading.Thread(target=wait_and_notify, daemon=True).start()
        except Exception as e:
            self.log_message(f"❌ Failed to start sync in new terminal: {e}")
            self.sync_progress.config(value=0)
            self.sync_progress_label.config(text=f"Error: {e}")
    
    def _sync_thread(self):
        """Background sync thread with progress updates"""
        try:
            # Import datetime at function level to ensure it's available throughout
            from datetime import datetime, timedelta, timezone
            
            try:
                from services.data_sync_service import DataSyncService
            except ImportError:
                import sys
                from pathlib import Path
                sys.path.insert(0, str(Path(__file__).parent.parent))
                from services.data_sync_service import DataSyncService
            
            sync_service = DataSyncService()
            
            # Prefer using the canonical sync helpers (timestamp normalization, key path, etc.)
            # to avoid drift between GUI and CLI sync behavior.
            try:
                from services import sync_nifty_db as sync_nifty_db
            except Exception:
                # Fallback: when running as a script with services/ on sys.path
                import sys
                from pathlib import Path
                sys.path.insert(0, str(Path(__file__).parent))
                import sync_nifty_db  # type: ignore
            
            # Use the same info helpers as the CLI sync script (includes timestamp normalization)
            vps_latest, vps_count, vps_earliest = sync_nifty_db.get_vps_db_info()
            local_latest, local_count, local_earliest = sync_nifty_db.get_local_db_info()
            
            # STEP 1: If local has MORE than VPS, trim first (VPS is source of truth)
            if local_latest and local_count is not None and vps_count is not None and int(local_count) > int(vps_count):
                self.root.after(0, lambda: (
                    self.sync_progress.config(value=5),
                    self.sync_progress_label.config(text="Trimming extra records to match VPS...")
                ))
                self.log_message(f"✂️ Local has {int(local_count) - int(vps_count):,} extra records - trimming first...")
                def trim_progress(msg, pct):
                    self.root.after(0, lambda: (
                        self.sync_progress.config(value=min(15, pct)),
                        self.sync_progress_label.config(text=msg)
                    ))
                deleted = sync_nifty_db.trim_local_to_match_vps(progress_callback=trim_progress)
                if deleted < 0:
                    self.root.after(0, lambda: (
                        self.sync_progress.config(value=0),
                        self.sync_progress_label.config(text="❌ Trim failed"),
                        self.log_message("❌ Trim failed - aborting sync")
                    ))
                    return
                self.log_message(f"✅ Trimmed {deleted:,} extra records")
                local_latest, local_count, local_earliest = sync_nifty_db.get_local_db_info()
            
            # Update progress: Checking gaps
            self.root.after(0, lambda: (
                self.sync_progress.config(value=10),
                self.sync_progress_label.config(text="Checking for gaps...")
            ))
            
            # Compare timestamps EXACTLY like sync_nifty_db.py
            gap = None
            
            if not local_latest or local_count is None:
                self.log_message("❌ Cannot read local DB info (missing DB/table?)")
                gap = None
            elif not vps_latest or vps_count is None:
                self.log_message("❌ Cannot read VPS DB info (SSH/key/network?)")
                gap = None
            else:
                # Canonical comparison (sync_nifty_db handles suffixes like +00:00 / Z)
                local_norm = sync_nifty_db.normalize_ts(local_latest) or local_latest
                vps_norm = sync_nifty_db.normalize_ts(vps_latest) or vps_latest
                
                if local_norm == vps_norm:
                    # Same latest timestamp; counts must match too, otherwise there are missing rows inside history.
                    if int(local_count) == int(vps_count):
                        # Counts match - verify data match via reconciliation
                        self.log_message("🔍 Verifying data match (count + content)...")
                        try:
                            if local_earliest and vps_earliest:
                                earliest = min(local_earliest, vps_earliest)
                            else:
                                earliest = local_earliest or vps_earliest
                            if earliest:
                                earliest_dt = datetime.fromisoformat((sync_nifty_db.normalize_ts(earliest) or earliest).replace('Z', '+00:00'))
                                latest_dt = datetime.fromisoformat((vps_norm or "").replace('Z', '+00:00'))
                                start_date = earliest_dt.strftime("%Y-%m-%dT%H:%M:%S")
                                end_date = latest_dt.strftime("%Y-%m-%dT%H:%M:%S")
                                broken_timestamps = sync_nifty_db.reconcile_range(start_date, end_date)
                                if broken_timestamps and len(broken_timestamps) > 0:
                                    self.log_message(f"📋 Found {len(broken_timestamps)} timestamps with data mismatch - repairing...")
                                    records = sync_nifty_db.fetch_missing_records_for_date_range(start_date, end_date, broken_timestamps)
                                    if records and len(records) > 0:
                                        sync_nifty_db.backup_local_db()
                                        cnt = sync_nifty_db.insert_records(records)
                                        self.log_message(f"✅ Data repair complete: {cnt:,} records updated")
                                        self.root.after(0, lambda: (
                                            self.sync_progress.config(value=100),
                                            self.sync_progress_label.config(text=f"✅ Data repair complete: {cnt:,} records", foreground="#4caf50"),
                                            self.last_sync_label.config(text=f"Last Sync: {datetime.now().strftime('%Y-%m-%d %H:%M')}", foreground="#4caf50"),
                                            self.update_local_db_status(),
                                        ))
                                        self.root.after(1500, self.check_gaps)
                                        return
                        except Exception as e:
                            self.log_message(f"⚠️ Data verification skipped: {e}")
                        gap = None
                        self.log_message(f"✅ Already in sync: MAX(ts)={local_norm}, COUNT={local_count:,} (data verified)")
                    else:
                        record_diff = int(vps_count) - int(local_count)
                        self.log_message("⚠️ Same MAX(ts) but COUNT differs → running smart gap fill")
                        self.log_message(f"   Local={local_count:,}, VPS={vps_count:,}, Missing≈{record_diff:,}")
                        
                        # Decide date range based on GUI selection (use latest from VPS as the cap)
                        try:
                            # Choose an "end" anchor for UI ranges: VPS latest is the source-of-truth
                            end_anchor_dt = datetime.fromisoformat((vps_norm or "").replace('Z', '+00:00'))
                            local_earliest_dt = None
                            vps_earliest_dt = None
                            
                            if local_earliest:
                                try:
                                    local_earliest_dt = datetime.fromisoformat(
                                        (sync_nifty_db.normalize_ts(local_earliest) or local_earliest).replace('Z', '+00:00')
                                    )
                                except:
                                    local_earliest_dt = None
                            if vps_earliest:
                                try:
                                    vps_earliest_dt = datetime.fromisoformat(
                                        (sync_nifty_db.normalize_ts(vps_earliest) or vps_earliest).replace('Z', '+00:00')
                                    )
                                except:
                                    vps_earliest_dt = None
                            
                            earliest_dt = None
                            if local_earliest_dt and vps_earliest_dt:
                                earliest_dt = min(local_earliest_dt, vps_earliest_dt)
                            else:
                                earliest_dt = local_earliest_dt or vps_earliest_dt
                            
                            range_selection = self.range_var.get()
                            if range_selection == "full":
                                # Full DB: earliest → latest (fallback to last 30 days if earliest unknown)
                                if earliest_dt:
                                    start_date = earliest_dt.strftime("%Y-%m-%d")
                                else:
                                    start_date = (end_anchor_dt - timedelta(days=30)).strftime("%Y-%m-%d")
                                end_date = end_anchor_dt.strftime("%Y-%m-%d")
                            elif range_selection in ["7", "14", "30"]:
                                days = int(range_selection)
                                start_date = (end_anchor_dt - timedelta(days=days)).strftime("%Y-%m-%d")
                                end_date = end_anchor_dt.strftime("%Y-%m-%d")
                            elif range_selection == "custom_days":
                                try:
                                    days = int(self.custom_days_var.get().strip())
                                    if days <= 0:
                                        raise ValueError("Days must be positive")
                                except Exception:
                                    self.log_message(f"⚠️ Invalid custom days ({self.custom_days_var.get()}), using 7 days")
                                    days = 7
                                start_date = (end_anchor_dt - timedelta(days=days)).strftime("%Y-%m-%d")
                                end_date = end_anchor_dt.strftime("%Y-%m-%d")
                            elif range_selection == "custom_range":
                                try:
                                    from_date = datetime.strptime(self.custom_from_date.get().strip(), "%Y-%m-%d")
                                    to_date = datetime.strptime(self.custom_to_date.get().strip(), "%Y-%m-%d")
                                    if from_date > to_date:
                                        raise ValueError("From > To")
                                    # Cap end-date to anchor (avoid future)
                                    to_date = min(to_date, end_anchor_dt)
                                    start_date = from_date.strftime("%Y-%m-%d")
                                    end_date = to_date.strftime("%Y-%m-%d")
                                except Exception:
                                    self.log_message("⚠️ Invalid custom range, using 7 days")
                                    start_date = (end_anchor_dt - timedelta(days=7)).strftime("%Y-%m-%d")
                                    end_date = end_anchor_dt.strftime("%Y-%m-%d")
                            else:
                                start_date = (end_anchor_dt - timedelta(days=7)).strftime("%Y-%m-%d")
                                end_date = end_anchor_dt.strftime("%Y-%m-%d")
                            
                            self.log_message(f"📅 Gap-fill range: {start_date} → {end_date} (selected: {range_selection})")
                            
                            # Detect + fetch + insert missing rows
                            broken_timestamps = sync_nifty_db.detect_specific_gaps(start_date, end_date)
                            if not broken_timestamps:
                                self.log_message("✅ No broken timestamps detected in selected range")
                                gap = None
                            else:
                                self.log_message(f"📋 Found {len(broken_timestamps)} broken timestamps")
                                records = sync_nifty_db.fetch_missing_records_for_date_range(start_date, end_date, broken_timestamps)
                                if records is None:
                                    self.log_message("❌ Failed to fetch missing records")
                                    gap = None
                                elif len(records) == 0:
                                    self.log_message("ℹ️ No records fetched (may already exist)")
                                    gap = None
                                else:
                                    self.root.after(0, lambda: (
                                        self.sync_progress.config(value=70),
                                        self.sync_progress_label.config(text=f"Inserting {len(records):,} records...")
                                    ))
                                    
                                    backup_result = sync_nifty_db.backup_local_db()
                                    if backup_result:
                                        self.log_message(f"✅ Backup created: {backup_result.name}")
                                    
                                    processed = sync_nifty_db.insert_records(records)
                                    sync_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                                    
                                    self.root.after(0, lambda cnt=processed, st=sync_time: (
                                        self.sync_progress.config(value=100),
                                        self.sync_progress_label.config(text=f"✅ Sync complete! Processed {cnt:,} records"),
                                        self.log_message(f"✅ Gap fill complete! Processed {cnt:,} records"),
                                        self.last_sync_label.config(text=f"Last Sync: {st}"),
                                        self.update_local_db_status()
                                    ))
                                    
                                    self.root.after(1000, self.check_gaps)
                                    return
                        except Exception as e:
                            self.log_message(f"⚠️ Error in smart gap detection: {e}")
                            import traceback
                            self.log_message(traceback.format_exc())
                            gap = None
                elif local_norm > vps_norm:
                    self.log_message(f"⚠️ Local database appears newer than VPS. Local: {local_norm}, VPS: {vps_norm}")
                    gap = None
                else:
                    gap = (local_norm, vps_norm)
                    self.log_message(f"📈 Gap detected: Local ends at {local_norm}, VPS has data until {vps_norm}")
            
            if gap:
                start_ts, end_ts = gap
                
                # Use the same fetch logic as sync_nifty_db.py - fetch ALL records after local_latest
                # This ensures we don't miss any records, even if timestamps match
                self.log_message(f"📥 Starting sync: fetching all records after {start_ts}")
                
                # Calculate estimated records for progress
                try:
                    start_dt = datetime.fromisoformat(start_ts.replace('Z', '+00:00'))
                    end_dt = datetime.fromisoformat(end_ts.replace('Z', '+00:00'))
                    time_diff = (end_dt - start_dt).total_seconds()
                    estimated_records = int((time_diff / 5) * 23)
                except Exception as calc_err:
                    self.log_message(f"⚠️ Error calculating estimated records: {calc_err}")
                    estimated_records = 10000
                
                # Update progress: Fetching data
                self.root.after(0, lambda: (
                    self.sync_progress.config(value=20),
                    self.sync_progress_label.config(text=f"Fetching data from VPS (estimated ~{estimated_records:,} records)...")
                ))
                
                # Use sync_nifty_db.py's fetch_incremental_data logic
                # This uses "ts > local_latest" to get ALL records after local, ensuring no missing records
                try:
                    # Create backup before sync (like sync_nifty_db.py)
                    self.log_message("💾 Creating backup before sync...")
                    backup_result = sync_nifty_db.backup_local_db()
                    if backup_result:
                        self.log_message(f"✅ Backup created: {backup_result.name}")
                    
                    # Fetch incremental data using sync_nifty_db.py's method
                    # FIX: Use overlap to catch records at same timestamp
                    # This uses "ts >= (local_latest - 15min)" to ensure no missing records
                    records = sync_nifty_db.fetch_incremental_data(start_ts, use_overlap=True, overlap_minutes=15)
                    
                    if records is None:
                        raise Exception("Failed to fetch data from VPS")
                    
                    if len(records) == 0:
                        self.log_message("ℹ️ No new records to sync")
                        count = 0
                    else:
                        # Insert records using sync_nifty_db.py's method
                        self.root.after(0, lambda: (
                            self.sync_progress.config(value=50),
                            self.sync_progress_label.config(text=f"Inserting {len(records):,} records into local database...")
                        ))
                        
                        count = sync_nifty_db.insert_records(records)
                        
                except ImportError:
                    # Fallback to data_sync_service if sync_nifty_db not available
                    self.log_message("⚠️ Using fallback sync method")
                    
                    # Fill gap (this may take time)
                    def update_progress_periodically():
                        """Periodic progress update during sync"""
                        import time
                        progress = 20
                        while progress < 90:
                            time.sleep(1)
                            progress += 5
                            self.root.after(0, lambda p=progress: (
                                self.sync_progress.config(value=p),
                                self.sync_progress_label.config(text=f"Syncing... {p}%")
                            ))
                    
                    progress_thread = threading.Thread(target=update_progress_periodically, daemon=True)
                    progress_thread.start()
                    
                    count = sync_service.fill_gap(start_ts, end_ts)
                except Exception as e:
                    raise Exception(f"Sync failed: {e}")
                
                # Update progress: Complete
                # Capture datetime value before lambda to avoid closure issues
                sync_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                self.root.after(0, lambda cnt=count, st=sync_time: (
                    self.sync_progress.config(value=100),
                    self.sync_progress_label.config(text=f"✅ Sync complete! Added {cnt:,} records"),
                    self.log_message(f"✅ Sync complete! Added {cnt:,} records"),
                    self.last_sync_label.config(text=f"Last Sync: {st}")
                ))
                
                # Update local DB status after sync
                self.update_local_db_status()
                
                # Re-check gaps
                self.root.after(1000, self.check_gaps)
            else:
                # Capture datetime value before lambda to avoid closure issues
                sync_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                self.root.after(0, lambda st=sync_time: (
                    self.sync_progress.config(value=100),
                    self.sync_progress_label.config(text="✅ Already up-to-date - no sync needed"),
                    self.log_message("✅ Already up-to-date"),
                    self.last_sync_label.config(text=f"Last Sync: {st}")
                ))
        except Exception as e:
            # Capture error message before lambda to avoid closure issues
            error_msg = str(e)
            self.root.after(0, lambda err=error_msg: (
                self.sync_progress.config(value=0),
                self.sync_progress_label.config(text=f"❌ Sync error: {err}"),
                self.log_message(f"❌ Sync error: {err}")
            ))
    
    def check_gaps(self):
        """Check for gaps in local database and compare with VPS (non-blocking)"""
        def check_in_background():
            try:
                from pathlib import Path as PathLib
                try:
                    from services.data_sync_service import DataSyncService
                except ImportError:
                    import sys
                    sys.path.insert(0, str(PathLib(__file__).parent.parent))
                    from services.data_sync_service import DataSyncService
                
                # Prefer canonical sync helpers for DB info + timestamp normalization
                try:
                    from services import sync_nifty_db as sync_nifty_db
                except Exception:
                    import sys
                    from pathlib import Path
                    sys.path.insert(0, str(Path(__file__).parent))
                    import sync_nifty_db  # type: ignore
                
                self.root.after(0, lambda: (
                    self.gap_status.config(text="Checking gaps...", foreground="orange"),
                    self.gap_summary.config(text="")
                ))
                
                # Canonical info (normalized like the CLI sync script)
                vps_latest, vps_count, _vps_earliest = sync_nifty_db.get_vps_db_info()
                local_latest, local_count, _local_earliest = sync_nifty_db.get_local_db_info()
                
                # Update VPS DB status (convert UTC to IST for display)
                self.root.after(0, lambda: self.vps_db_status.config(
                    text="VPS DB: Checking...", foreground="orange"
                ))
                
                # Update VPS DB status (convert UTC to IST for display)
                if vps_latest and vps_count is not None:
                    vps_display_time = format_timestamp_for_display(vps_latest)
                    vps_status_text = f"VPS DB: Last record {vps_display_time}, Total: {vps_count:,}"
                    self.root.after(0, lambda txt=vps_status_text: self.vps_db_status.config(
                        text=txt, foreground="green"
                    ))
                elif vps_latest is None and vps_count is None:
                    self.root.after(0, lambda: self.vps_db_status.config(
                        text="VPS DB: Connection failed or database not accessible", foreground="red"
                    ))
                else:
                    self.root.after(0, lambda: self.vps_db_status.config(
                        text="VPS DB: Partial data retrieved", foreground="orange"
                    ))
                
                # Compare local and VPS using EXACTLY the same logic as sync_with_vps()
                gap = None
                gap_info = None
                
                if not local_latest or local_count is None or not vps_latest or vps_count is None:
                    gap = None
                else:
                    local_norm = sync_nifty_db.normalize_ts(local_latest) or local_latest
                    vps_norm = sync_nifty_db.normalize_ts(vps_latest) or vps_latest
                
                if not local_latest or local_count is None or not vps_latest or vps_count is None:
                    gap = None
                elif local_norm == vps_norm:
                    # Databases are in sync (same timestamp) - EXACTLY like sync_nifty_db.py
                    gap = None
                    if int(vps_count) != int(local_count):
                        record_diff = int(vps_count) - int(local_count)
                        # If counts differ, we *do* have missing records (even if MAX(ts) matches).
                        # Show this as a "logical gap" and encourage sync to run smart gap fill.
                        estimated_time_diff = (max(record_diff, 0) / 23) * 5  # seconds (~23 records per 5-sec interval)
                        try:
                            local_dt = datetime.fromisoformat((local_norm or "").replace('Z', '+00:00'))
                            estimated_end_dt = local_dt + timedelta(seconds=estimated_time_diff)
                            gap = (local_norm, estimated_end_dt.strftime("%Y-%m-%dT%H:%M:%S"))
                        except:
                            gap = (local_norm, vps_norm)
                        
                        hours = int(estimated_time_diff / 3600) if estimated_time_diff else 0
                        minutes = int((estimated_time_diff % 3600) / 60) if estimated_time_diff else 0
                        gap_info = {
                            'duration_hours': hours,
                            'duration_minutes': minutes,
                            'missing_records': record_diff,
                            'local_count': local_count,
                            'vps_count': vps_count
                        }
                elif local_norm > vps_norm:
                    # Local is newer - no gap
                    gap = None
                else:
                    # VPS is newer - there's a gap
                    gap = (local_norm, vps_norm)
                    
                    # Calculate gap info
                    try:
                        local_dt = datetime.fromisoformat((local_norm or "").replace('Z', '+00:00'))
                        vps_dt = datetime.fromisoformat((vps_norm or "").replace('Z', '+00:00'))
                        time_diff = (vps_dt - local_dt).total_seconds()
                        
                        hours = int(time_diff / 3600)
                        minutes = int((time_diff % 3600) / 60)
                        estimated_records = int((time_diff / 5) * 23)
                        record_diff = (int(vps_count) - int(local_count)) if (vps_count is not None and local_count is not None) else 0
                        
                        gap_info = {
                            'duration_hours': hours,
                            'duration_minutes': minutes,
                            'estimated_records': estimated_records,
                            'local_count': local_count,
                            'vps_count': vps_count,
                            'missing_records': record_diff if record_diff > 0 else estimated_records
                        }
                    except:
                        pass
                
                # Now use the same comparison logic - check if time_diff > 30 OR record_diff > 1000
                # When record_diff < 0: local has MORE than VPS - treat as count mismatch (handled in else block)
                if gap and gap_info:
                    time_diff_sec = gap_info.get('duration_hours', 0) * 3600 + gap_info.get('duration_minutes', 0) * 60
                    record_diff = gap_info.get('missing_records', 0)
                    
                    if record_diff < 0:
                        gap = None  # Local has more - handle as count mismatch below
                    elif time_diff_sec <= 30 and record_diff <= 1000:
                        gap = None  # Not significant enough (local slightly behind)
                elif gap:
                    # If we have gap but no gap_info, calculate it
                    try:
                        local_dt = datetime.fromisoformat(local_latest.replace('Z', '+00:00'))
                        vps_dt = datetime.fromisoformat(vps_latest.replace('Z', '+00:00'))
                        time_diff = (vps_dt - local_dt).total_seconds()
                        record_diff = (vps_count - local_count) if (vps_count is not None and local_count is not None) else 0
                        
                        if time_diff <= 30 and record_diff <= 1000:
                            gap = None  # Not significant enough
                    except:
                        pass
                # If local_latest is None but vps_latest exists, gap is None (will show error)
                
                # Calculate gap information
                if gap:
                    start_ts, end_ts = gap
                    
                    if gap_info:
                        # Use detailed gap info from VPS comparison
                        summary_text = (
                            f"Gap Duration: {gap_info['duration_hours']}h {gap_info['duration_minutes']}m | "
                            f"Missing Records: ~{gap_info['missing_records']:,} | "
                            f"Local: {gap_info['local_count']:,} | VPS: {gap_info['vps_count']:,}"
                        )
                    else:
                        # Fallback to estimated gap info
                        try:
                            start_dt = datetime.fromisoformat(start_ts.replace('Z', '+00:00'))
                            end_dt = datetime.fromisoformat(end_ts.replace('Z', '+00:00'))
                            time_diff = (end_dt - start_dt).total_seconds()
                            hours = int(time_diff / 3600)
                            minutes = int((time_diff % 3600) / 60)
                            estimated_records = int((time_diff / 5) * 23)
                            
                            summary_text = (
                                f"Gap Duration: {hours}h {minutes}m | "
                                f"Estimated Records: ~{estimated_records:,} | "
                                f"Local DB: {local_count:,} records"
                            )
                        except:
                            summary_text = f"Gap: {format_timestamp_for_display(start_ts)} to {format_timestamp_for_display(end_ts)}"
                    
                    # Format timestamps in IST for display
                    start_display = format_timestamp_for_display(start_ts)
                    end_display = format_timestamp_for_display(end_ts)
                    
                    self.root.after(0, lambda st=start_display, et=end_display, summ=summary_text: (
                        self.gap_status.config(
                            text=f"🔴 Gap Detected: {st} to {et}", 
                            foreground="red"
                        ),
                        self.gap_summary.config(
                            text=summ,
                            foreground="red"
                        )
                    ))
                else:
                    # No gap from VPS perspective - but check if counts match
                    if vps_latest and vps_count is not None and local_count is not None:
                        if int(local_count) > int(vps_count):
                            # Local has MORE records than VPS - possible test data or duplicates
                            diff = int(local_count) - int(vps_count)
                            sync_text = (
                                f"Local: {local_count:,} | VPS: {vps_count:,} | "
                                f"Local has {diff:,} extra records (Sync will trim automatically)"
                            )
                            self.root.after(0, lambda txt=sync_text: (
                                self.gap_status.config(
                                    text="⚠️ Count mismatch - Local has more records than VPS",
                                    foreground="#ffa500"
                                ),
                                self.gap_summary.config(text=txt, foreground="#ffa500")
                            ))
                        elif int(local_count) < int(vps_count):
                            # Should not reach here if gap logic is correct, but handle anyway
                            sync_text = f"Local: {local_count:,} | VPS: {vps_count:,} | Sync needed"
                            self.root.after(0, lambda txt=sync_text: (
                                self.gap_status.config(
                                    text="⚠️ Count mismatch - run Sync to fix",
                                    foreground="#ffa500"
                                ),
                                self.gap_summary.config(text=txt, foreground="#ffa500")
                            ))
                        else:
                            sync_text = f"Local: {local_count:,} records | VPS: {vps_count:,} records | All synchronized"
                            self.root.after(0, lambda txt=sync_text: (
                                self.gap_status.config(
                                    text="✅ No Gaps - Database is up to date",
                                    foreground="green"
                                ),
                                self.gap_summary.config(text=txt, foreground="green")
                            ))
                    else:
                        sync_text = f"Local DB: {local_count:,} records | All data synchronized"
                        self.root.after(0, lambda txt=sync_text: (
                            self.gap_status.config(
                                text="✅ No Gaps - Database is up to date",
                                foreground="green"
                            ),
                            self.gap_summary.config(text=txt, foreground="green")
                        ))
            except Exception as e:
                self.root.after(0, lambda err=str(e)[:50]: (
                    self.gap_status.config(
                        text=f"❌ Error: {err}", 
                        foreground="red"
                    ),
                    self.gap_summary.config(text="")
                ))
        
        # Run in background thread to avoid blocking
        threading.Thread(target=check_in_background, daemon=True).start()
    
    def toggle_auto_sync(self):
        """Toggle auto-sync mode"""
        if self.auto_sync_var.get():
            self.log_message("Auto-sync enabled (every 5 minutes)")
            # Start auto-sync thread
            threading.Thread(target=self._auto_sync_loop, daemon=True).start()
        else:
            self.log_message("Auto-sync disabled")
    
    def _auto_sync_loop(self):
        """Auto-sync loop (runs every 5 minutes)"""
        import time
        while self.auto_sync_var.get():
            time.sleep(300)  # 5 minutes
            if self.auto_sync_var.get():
                self.sync_with_vps()
    
    def delete_test_data(self):
        """Delete test data for a specific date (for testing gap filling)"""
        # Ask user for date
        from tkinter import simpledialog
        
        date_str = simpledialog.askstring(
            "Delete Test Data",
            "Enter date to delete entries from (YYYY-MM-DD):\n\nExample: 2026-01-01",
            initialvalue="2026-01-01"
        )
        
        if not date_str:
            return
        
        # Validate date format
        try:
            test_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            messagebox.showerror("Error", "Invalid date format. Please use YYYY-MM-DD")
            return
        
        # Ask for confirmation
        result = messagebox.askyesno(
            "Confirm Deletion",
            f"Are you sure you want to delete some random entries for {date_str}?\n\n"
            f"This will delete approximately 30% of records for that date.\n"
            f"This action cannot be undone!"
        )
        
        if not result:
            return
        
        # Run deletion in background thread
        def delete_in_background():
            try:
                self.log_message(f"🗑️ Starting deletion of test data for {date_str}...")
                
                try:
                    conn = db.get_connection()
                except Exception:
                    self.root.after(0, lambda: (
                        messagebox.showerror("Error", "Cannot connect to database. Set DATABASE_URL."),
                        self.log_message("❌ Database connection failed")
                    ))
                    return
                
                # Calculate date range (start and end of day in UTC)
                from datetime import timezone, timedelta
                ist_offset = timedelta(hours=5, minutes=30)
                
                # Market hours: 09:15 to 15:30 IST
                market_start_ist = test_date.replace(hour=9, minute=15, second=0, microsecond=0)
                market_end_ist = test_date.replace(hour=15, minute=30, second=0, microsecond=0)
                
                # Convert to UTC
                market_start_utc = (market_start_ist.replace(tzinfo=timezone(ist_offset))).astimezone(timezone.utc)
                market_end_utc = (market_end_ist.replace(tzinfo=timezone(ist_offset))).astimezone(timezone.utc)
                
                start_ts = market_start_utc.strftime("%Y-%m-%dT%H:%M:%S")
                end_ts = market_end_utc.strftime("%Y-%m-%dT%H:%M:%S")
                
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, symbol, ts FROM ltp_ticks 
                    WHERE ts >= %s::timestamptz AND ts <= %s::timestamptz
                    ORDER BY RANDOM()
                """, (start_ts, end_ts))
                
                all_records = cursor.fetchall()
                total_records = len(all_records)
                
                if total_records == 0:
                    self.root.after(0, lambda: (
                        messagebox.showinfo("Info", f"No records found for {date_str}"),
                        self.log_message(f"ℹ️ No records found for {date_str}")
                    ))
                    conn.close()
                    return
                
                # Delete approximately 30% of records (random selection)
                delete_count = int(total_records * 0.3)
                records_to_delete = all_records[:delete_count]
                
                deleted_ids = [r[0] for r in records_to_delete]
                
                # Delete records
                placeholders = ','.join(['%s'] * len(deleted_ids))
                cursor.execute(f"DELETE FROM ltp_ticks WHERE id IN ({placeholders})", deleted_ids)
                
                conn.commit()
                conn.close()
                
                self.root.after(0, lambda: (
                    messagebox.showinfo("Success", 
                        f"Deleted {delete_count:,} records ({delete_count/total_records*100:.1f}%) for {date_str}\n\n"
                        f"Total records before: {total_records:,}\n"
                        f"Records deleted: {delete_count:,}\n"
                        f"Remaining: {total_records - delete_count:,}"),
                    self.log_message(f"✅ Deleted {delete_count:,} records for {date_str}"),
                    self.update_local_db_status()
                ))
                
            except Exception as e:
                error_msg = str(e)
                self.root.after(0, lambda err=error_msg: (
                    messagebox.showerror("Error", f"Failed to delete test data:\n{err}"),
                    self.log_message(f"❌ Error: {err}")
                ))
        
        threading.Thread(target=delete_in_background, daemon=True).start()
    
    def fill_gaps_for_date(self):
        """Fill gaps for a specific date using smart gap detection"""
        from tkinter import simpledialog
        
        date_str = simpledialog.askstring(
            "Fill Gaps for Date",
            "Enter date to fill gaps for (YYYY-MM-DD):\n\nExample: 2026-01-01",
            initialvalue="2026-01-01"
        )
        
        if not date_str:
            return
        
        # Validate date format
        try:
            test_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            messagebox.showerror("Error", "Invalid date format. Please use YYYY-MM-DD")
            return
        
        # Run gap filling in background thread
        def fill_gaps_in_background():
            try:
                self.log_message(f"🔧 Starting gap fill for {date_str}...")
                self.root.after(0, lambda: (
                    self.sync_progress.config(value=10),
                    self.sync_progress_label.config(text=f"Detecting gaps for {date_str}...")
                ))
                
                # Import sync functions
                import sys
                from pathlib import Path
                sys.path.insert(0, str(Path(__file__).parent))
                
                try:
                    from sync_nifty_db import detect_specific_gaps, fetch_missing_records_for_date_range, insert_records, backup_local_db
                except ImportError:
                    self.root.after(0, lambda: (
                        messagebox.showerror("Error", "Could not import sync functions. Make sure sync_nifty_db.py is available."),
                        self.log_message("❌ Could not import sync functions")
                    ))
                    return
                
                # Create backup
                self.log_message("💾 Creating backup before gap fill...")
                backup_result = backup_local_db()
                if backup_result:
                    self.log_message(f"✅ Backup created: {backup_result.name}")
                
                # Detect gaps
                self.root.after(0, lambda: (
                    self.sync_progress.config(value=20),
                    self.sync_progress_label.config(text="Detecting missing records...")
                ))
                
                broken_timestamps = detect_specific_gaps(date_str, date_str)
                
                if broken_timestamps is None:
                    self.root.after(0, lambda: (
                        messagebox.showerror("Error", "Failed to detect gaps. Check connection to VPS."),
                        self.log_message("❌ Failed to detect gaps"),
                        self.sync_progress.config(value=0),
                        self.sync_progress_label.config(text="Error detecting gaps")
                    ))
                    return
                
                if not broken_timestamps:
                    self.root.after(0, lambda: (
                        messagebox.showinfo("Info", f"No gaps found for {date_str}"),
                        self.log_message(f"✅ No gaps found for {date_str}"),
                        self.sync_progress.config(value=100),
                        self.sync_progress_label.config(text="✅ No gaps found")
                    ))
                    return
                
                self.log_message(f"📋 Found {len(broken_timestamps)} broken timestamps")
                
                # Fetch missing records
                self.root.after(0, lambda: (
                    self.sync_progress.config(value=40),
                    self.sync_progress_label.config(text=f"Fetching records for {len(broken_timestamps)} timestamps from VPS...")
                ))
                
                records = fetch_missing_records_for_date_range(date_str, date_str, broken_timestamps)
                
                if records is None:
                    self.root.after(0, lambda: (
                        messagebox.showerror("Error", "Failed to fetch missing records from VPS."),
                        self.log_message("❌ Failed to fetch missing records"),
                        self.sync_progress.config(value=0),
                        self.sync_progress_label.config(text="Error fetching records")
                    ))
                    return
                
                if len(records) == 0:
                    self.root.after(0, lambda: (
                        messagebox.showinfo("Info", "No records to insert (all may already exist)"),
                        self.log_message("ℹ️ No records to insert"),
                        self.sync_progress.config(value=100),
                        self.sync_progress_label.config(text="✅ No records to insert")
                    ))
                    return
                
                # Insert records
                self.root.after(0, lambda: (
                    self.sync_progress.config(value=70),
                    self.sync_progress_label.config(text=f"Inserting {len(records)} records...")
                ))
                
                inserted = insert_records(records)
                
                # Update progress
                sync_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                self.root.after(0, lambda cnt=inserted, st=sync_time: (
                    self.sync_progress.config(value=100),
                    self.sync_progress_label.config(text=f"✅ Gap fill complete! Added {cnt:,} records"),
                    self.log_message(f"✅ Gap fill complete! Added {cnt:,} records for {date_str}"),
                    self.last_sync_label.config(text=f"Last Gap Fill: {st}"),
                    messagebox.showinfo("Success", f"Gap fill complete!\n\nAdded {cnt:,} records for {date_str}"),
                    self.update_local_db_status()
                ))
                
            except Exception as e:
                error_msg = str(e)
                import traceback
                traceback.print_exc()
                self.root.after(0, lambda err=error_msg: (
                    messagebox.showerror("Error", f"Failed to fill gaps:\n{err}"),
                    self.log_message(f"❌ Error: {err}"),
                    self.sync_progress.config(value=0),
                    self.sync_progress_label.config(text=f"Error: {err[:50]}")
                ))
        
        threading.Thread(target=fill_gaps_in_background, daemon=True).start()
    
    def update_local_db_status(self):
        """Update local database status (thread-safe - can be called from any thread)"""
        def update_in_background():
            try:
                conn = db.get_connection()
                if not db.table_exists(conn, "ltp_ticks"):
                    conn.close()
                    try:
                        self.root.after(0, lambda: self.local_db_status.config(
                            text="Local DB: Table not found (set DATABASE_URL & run sync)"
                        ))
                    except Exception:
                        pass
                    return
                cursor = conn.cursor()
                cursor.execute("SELECT MAX(ts) FROM ltp_ticks")
                latest_ts = cursor.fetchone()[0]
                
                # Get total count
                cursor.execute("SELECT COUNT(*) FROM ltp_ticks")
                total_count = cursor.fetchone()[0]
                
                conn.close()
                
                # Update GUI on main thread (always use root.after for thread safety)
                # Use try-except to handle cases where GUI is closed
                try:
                    if latest_ts:
                        ts_str = str(latest_ts).replace(' ', 'T')[:19]
                        display_ts = format_timestamp_for_display(ts_str)
                        self.root.after(0, lambda ts=display_ts, cnt=total_count: (
                            self.local_db_status.config(text=f"Local DB: Last record {ts}, Total: {cnt:,}")
                        ))
                    else:
                        self.root.after(0, lambda: self.local_db_status.config(text="Local DB: Empty"))
                except Exception as gui_error:
                    # GUI might be closed or not ready yet
                    pass
            except Exception as e:
                error_msg = str(e)[:50]
                # Check if root still exists before updating
                try:
                    self.root.after(0, lambda msg=error_msg: self.local_db_status.config(
                        text=f"Local DB: Error - {msg}"
                    ))
                except:
                    pass  # GUI might be closed
        
        # Always run in background thread to avoid blocking
        import threading
        threading.Thread(target=update_in_background, daemon=True).start()
    
    def update_status_display(self):
        """Update status display (must be called from main thread)"""
        if not self.broadcaster:
            return
        
        try:
            # Get mode (this is thread-safe, but do it quickly)
            mode = self.broadcaster.get_mode()
            gui_mode = self.mode_var.get()
            
            # Show if mode is switching (GUI mode != broadcaster mode)
            if gui_mode != mode:
                mode_text = f"🔄 Switching to {gui_mode}..."
            else:
                mode_text = "🟢 Live" if mode == "LIVE" else "🔵 Replay"
            
            self.status_mode.config(text=f"Current Mode: {mode_text}")
            # Color coding
            try:
                if gui_mode != mode:
                    self.status_mode.config(foreground="#ffa500")  # orange (switching)
                else:
                    self.status_mode.config(foreground="#4caf50" if mode == "LIVE" else "#4aa3ff")
            except Exception:
                pass
            
            # Broadcasting status (quick check)
            try:
                is_broadcasting = self.broadcasting_active
                is_service_running = self.broadcaster.running
                
                if is_broadcasting and is_service_running:
                    self.status_broadcasting.config(text="Broadcasting: ● Active")
                    try:
                        self.status_broadcasting.config(foreground="#4caf50")
                    except Exception:
                        pass
                elif is_service_running:
                    self.status_broadcasting.config(text="Broadcasting: ○ Stopped - Click Start to begin")
                    try:
                        self.status_broadcasting.config(foreground=self.muted_fg)
                    except Exception:
                        pass
                else:
                    self.status_broadcasting.config(text="Broadcasting: ○ Service not running")
                    try:
                        self.status_broadcasting.config(foreground="#f44336")
                    except Exception:
                        pass
            except:
                self.status_broadcasting.config(text="Broadcasting: ?")
            
            # Clients (quick check)
            try:
                client_count = len(self.broadcaster.subscribers)
                self.status_clients.config(text=f"Clients: {client_count} connected")
            except:
                self.status_clients.config(text="Clients: ?")
            
            # Replay progress (only if in replay mode)
            if mode == "REPLAY":
                try:
                    # Get progress with timeout protection
                    try:
                        progress = self.broadcaster.get_replay_progress()
                    except Exception as prog_error:
                        # If get_replay_progress fails, show error and skip this update
                        if not hasattr(self, '_last_progress_error') or self._last_progress_error != str(prog_error):
                            self._last_progress_error = str(prog_error)
                            import traceback
                            print(f"[Progress Error] {prog_error}")
                            print(traceback.format_exc())
                        self.status_records.config(text="Records: Error getting progress")
                        return  # Skip rest of update
                    
                    current = progress.get('current', 0)
                    total = progress.get('total', 0)
                    pct = progress.get('progress_pct', 0.0)
                    
                    # If total is 0, records might not be loaded yet
                    if total == 0:
                        self.status_records.config(text="Records: Loading... (0 records found)")
                        return
                    
                    # Selected range display (hide "Loaded ..." info as requested)
                    if self.selected_start_time and self.selected_end_time:
                        try:
                            sel_date = self.selected_start_time.split()[0]
                            sel_start_t = self.selected_start_time.split()[1]
                            sel_end_t = self.selected_end_time.split()[1]
                            self.status_range.config(text=f"Selected Window: {sel_date} {sel_start_t}–{sel_end_t} IST")
                        except Exception:
                            self.status_range.config(text=f"Selected Window: {self.selected_start_time} → {self.selected_end_time}")
                    else:
                        self.status_range.config(text="")
                    
                    # Show records with percentage
                    self.status_records.config(text=f"Records: {current:,} / {total:,} ({pct:.2f}%)")
                    self.progress['value'] = pct
                    
                    # Replay Time: show selected date/time in IST (not raw UTC)
                    current_timestamp = progress.get('current_timestamp')
                    if current_timestamp:
                        # Format current timestamp being broadcast to show seconds in IST
                        formatted_time = format_timestamp_for_display(current_timestamp)
                        self.status_time.config(text=f"Replay Time: {formatted_time}")
                    elif progress.get('start_time'):
                        # Fallback to start_time if current_timestamp not available yet
                        start_time = progress.get('start_time')
                        formatted_time = format_timestamp_for_display(start_time)
                        self.status_time.config(text=f"Replay Time: {formatted_time} (starting)")
                    else:
                        self.status_time.config(text="Replay Time: --")
                    
                    # Replay metrics: Elapsed / Remaining / Speed (no ETA)
                    try:
                        speed = float(self.speed_var.get())

                        start_ts = progress.get("start_time")
                        end_ts = progress.get("end_time")
                        cur_ts = progress.get("current_timestamp")

                        def _parse_iso(ts: str):
                            if not ts:
                                return None
                            ts2 = ts.replace("Z", "+00:00")
                            try:
                                return datetime.fromisoformat(ts2)
                            except Exception:
                                return None

                        def _fmt_td(td: timedelta) -> str:
                            s = int(max(td.total_seconds(), 0))
                            h = s // 3600
                            m = (s % 3600) // 60
                            sec = s % 60
                            return f"{h:02d}:{m:02d}:{sec:02d}"

                        start_dt = _parse_iso(start_ts)
                        end_dt = _parse_iso(end_ts)
                        cur_dt = _parse_iso(cur_ts)

                        if start_dt and end_dt and cur_dt:
                            elapsed = cur_dt - start_dt
                            remaining = end_dt - cur_dt
                            self.status_replay_metrics.config(
                                text=f"Elapsed {_fmt_td(elapsed)}  •  Remaining {_fmt_td(remaining)}  •  Speed {speed:g}x"
                            )
                        else:
                            # Fallback if timestamps not available yet
                            self.status_replay_metrics.config(text=f"Speed {speed:g}x")

                        self.status_replay_eta.config(text="")  # keep blank (ETA removed)
                    except Exception:
                        self.status_replay_metrics.config(text="")
                        self.status_replay_eta.config(text="")
                        
                except Exception as e:
                    # Log the error for debugging (but don't block the GUI)
                    import traceback
                    error_msg = f"Error updating replay status: {e}"
                    # Only log once per error type to avoid spam
                    if not hasattr(self, '_last_status_error') or self._last_status_error != str(e):
                        self._last_status_error = str(e)
                        print(f"[Status Update Error] {error_msg}")
                        print(traceback.format_exc())
                    
                    self.status_records.config(text="Records: Error - check logs")
                    self.status_time.config(text="Replay Time: --")
                    # Always show something (never blank)
                    try:
                        speed = float(self.speed_var.get())
                        self.status_replay_metrics.config(text=f"Speed {speed:g}x")
                    except:
                        self.status_replay_metrics.config(text="Progress: Error")
                    self.status_replay_eta.config(text="")
            else:
                # Live mode
                self.status_records.config(text="Records: Live mode (monitoring database)")
                self.progress['value'] = 0
                self.status_time.config(text="Current Time: Live (real-time)")
                # Clear replay metrics in Live mode
                self.status_replay_metrics.config(text="")
                self.status_replay_eta.config(text="")
                self.status_range.config(text="")
                # Reset replay tracking
                self._last_replay_wall_time = None
                self._last_replay_index = None
                # Reset range tracking
                self.selected_start_time = None
                self.selected_end_time = None
                self.first_loaded_timestamp = None
                self.last_loaded_timestamp = None
        except Exception as e:
            # Don't crash on status update errors, but log them
            pass
    
    def start_periodic_updates(self):
        """Start periodic status updates"""
        self.update_running = True
        
        def process_queue():
            """Process message queue from main thread"""
            try:
                # Process message queue (non-blocking, limit to prevent blocking)
                messages_processed = 0
                while messages_processed < 10:  # Limit messages per cycle
                    try:
                        message = self.message_queue.get_nowait()
                        if not message:
                            messages_processed += 1
                            continue
                        if isinstance(message, dict) and message.get("type") == "broadcast_rows":
                            self._append_broadcast_rows_ui(message.get("rows") or [], message.get("mode", "?"))
                        else:
                            self.log_message(str(message))
                        messages_processed += 1
                    except queue.Empty:
                        break
            except Exception as e:
                pass  # Silently ignore errors in queue processing
        
        def update_status():
            """Update status display from main thread"""
            try:
                self.update_status_display()
            except Exception as e:
                pass  # Silently ignore errors in status update
        
        def periodic_update():
            """Periodic update function called from main thread"""
            if self.update_running:
                # Process queue
                process_queue()
                
                # Update status
                update_status()
                
                # Schedule next update (1 second)
                self.root.after(1000, periodic_update)
        
        # Start periodic updates (called from main thread)
        self.root.after(1000, periodic_update)
    
    def log_message(self, message: str):
        """Add message to logs"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.logs_text.config(state="normal")
        self.logs_text.insert("end", f"[{timestamp}] {message}\n")
        self.logs_text.config(state="disabled")
        self.logs_text.see("end")
    
    def on_closing(self):
        """Handle window closing"""
        self.update_running = False
        self.root.destroy()
    
    def run(self):
        """Run the GUI main loop"""
        try:
            # Ensure window stays on top initially (optional, can be removed)
            self.root.lift()
            self.root.attributes('-topmost', True)
            self.root.after_idle(lambda: self.root.attributes('-topmost', False))
            
            # Start main loop
            self.root.mainloop()
        except Exception as e:
            import traceback
            error_msg = f"Error in GUI main loop: {e}\n\n{traceback.format_exc()}"
            print(error_msg)
            messagebox.showerror("GUI Error", error_msg)
            raise


if __name__ == "__main__":
    # Run as a standalone app: create broadcaster + start it in background thread,
    # then attach GUI control panel to it.
    import asyncio
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # PostgreSQL via DATABASE_URL (no file path)
    broadcaster = WebSocketBroadcaster(
        db_path=None,
        ws_host="localhost",
        ws_port=8765,
        auto_start=False
    )

    def _run_broadcaster():
        try:
            asyncio.run(broadcaster.start())
        except Exception:
            logging.getLogger(__name__).exception("Broadcaster thread crashed")

    broadcaster_thread = threading.Thread(
        target=_run_broadcaster,
        daemon=True,
        name="BroadcasterThread"
    )
    broadcaster_thread.start()

    panel = BroadcastControlPanel(broadcaster=broadcaster)

    # Ensure broadcaster stops when GUI closes
    _orig_on_closing = panel.on_closing

    def _on_closing_wrapped():
        try:
            broadcaster.stop()
        except Exception:
            pass
        try:
            broadcaster_thread.join(timeout=3.0)
        except Exception:
            pass
        _orig_on_closing()

    panel.on_closing = _on_closing_wrapped
    panel.root.protocol("WM_DELETE_WINDOW", panel.on_closing)

    panel.run()

