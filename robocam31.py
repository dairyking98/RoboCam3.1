import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import cv2
from PIL import Image, ImageTk
import os
import glob
import time
import numpy as np

from robocam.config import get_config
from robocam.motion import MotionController
from robocam.camera import Camera
from robocam.calibration import CalibrationManager, WellPlate
from robocam.experiment import ExperimentRunner

class WellGrid(tk.Canvas):
    """Custom Tkinter canvas to draw a clickable/draggable well plate grid."""
    def __init__(self, parent, rows=8, cols=12, mode="navigate", cell_w=30, cell_h=20, spacing=2, **kwargs):
        super().__init__(parent, **kwargs)
        self.rows = rows
        self.cols = cols
        self.mode = mode # "navigate" or "select"
        self.cell_w = cell_w
        self.cell_h = cell_h
        self.spacing = spacing
        
        self.selected = [[True for _ in range(cols)] for _ in range(rows)]
        self.drag_target = None
        
        self.bind("<Configure>", self._on_resize)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        
        self.on_well_clicked = None # callback(row, col)
        self.on_selection_changed = None # callback()
        
    def rebuild(self, rows, cols):
        self.rows = rows
        self.cols = cols
        self.selected = [[True for _ in range(cols)] for _ in range(rows)]
        self._draw()
        if self.on_selection_changed:
            self.on_selection_changed()
            
    def get_selected_indices(self):
        indices = []
        idx = 0
        for r in range(self.rows):
            for c in range(self.cols):
                if self.selected[r][c]:
                    indices.append(idx)
                idx += 1
        return indices
        
    def set_all(self, state):
        for r in range(self.rows):
            for c in range(self.cols):
                self.selected[r][c] = state
        self._draw()
        if self.on_selection_changed:
            self.on_selection_changed()
            
    def invert(self):
        for r in range(self.rows):
            for c in range(self.cols):
                self.selected[r][c] = not self.selected[r][c]
        self._draw()
        if self.on_selection_changed:
            self.on_selection_changed()

    def _on_resize(self, event):
        self._draw()
        
    def _get_cell_at(self, x, y):
        col = (x - self.spacing) // (self.cell_w + self.spacing)
        row = (y - self.spacing) // (self.cell_h + self.spacing)
        if 0 <= row < self.rows and 0 <= col < self.cols:
            return row, col
        return None

    def _on_press(self, event):
        cell = self._get_cell_at(event.x, event.y)
        if not cell: return
        r, c = cell
        
        if self.mode == "navigate":
            if self.on_well_clicked:
                self.on_well_clicked(r, c)
        elif self.mode == "select":
            self.drag_target = not self.selected[r][c]
            self.selected[r][c] = self.drag_target
            self._draw()
            if self.on_selection_changed:
                self.on_selection_changed()

    def _on_drag(self, event):
        if self.mode != "select" or self.drag_target is None: return
        cell = self._get_cell_at(event.x, event.y)
        if not cell: return
        r, c = cell
        if self.selected[r][c] != self.drag_target:
            self.selected[r][c] = self.drag_target
            self._draw()
            if self.on_selection_changed:
                self.on_selection_changed()

    def _on_release(self, event):
        self.drag_target = None

    def _draw(self):
        self.delete("all")
        w = self.cols * (self.cell_w + self.spacing) + self.spacing
        h = self.rows * (self.cell_h + self.spacing) + self.spacing
        self.config(width=w, height=h)
        
        for r in range(self.rows):
            for c in range(self.cols):
                x0 = self.spacing + c * (self.cell_w + self.spacing)
                y0 = self.spacing + r * (self.cell_h + self.spacing)
                x1 = x0 + self.cell_w
                y1 = y0 + self.cell_h
                
                label = f"{chr(ord('A') + r)}{c + 1}"
                
                if self.mode == "select":
                    bg = "#2a7ae2" if self.selected[r][c] else "#555555"
                    fg = "white" if self.selected[r][c] else "#aaaaaa"
                else:
                    bg = "#3a3a3a"
                    fg = "white"
                    
                self.create_rectangle(x0, y0, x1, y1, fill=bg, outline="#333333")
                self.create_text(x0 + self.cell_w/2, y0 + self.cell_h/2, text=label, fill=fg, font=("Arial", 8))


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("RoboCam 3.1")
        self.root.geometry("1200x800")
        
        self.simulate = False
        self.config = get_config()
        self.motion = None
        self.camera = Camera(simulate=self.simulate)
        self.cal_mgr = CalibrationManager()
        self.exp_runner = None
        
        self.calib_preview_label = None
        self.exp_preview_label = None
        
        self._build_gui()
        self._connect_motion()
        self.update_camera_preview()
        
    def _connect_motion(self):
        try:
            self.motion = MotionController(simulate=self.simulate)
            self.exp_runner = ExperimentRunner(self.motion, self.camera)
            backend_name = self.config.get("hardware.motion_backend", "marlin").upper()
            self.lbl_status.config(text=f"Connected: {backend_name}", foreground="green")
            self._update_pos_label()
        except Exception as e:
            self.lbl_status.config(text=f"Connection Error: {e}", foreground="red")
            self.motion = None
        
    def _build_gui(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.tab_motion = ttk.Frame(self.notebook)
        self.tab_calib = ttk.Frame(self.notebook)
        self.tab_exp = ttk.Frame(self.notebook)
        
        self.notebook.add(self.tab_motion, text="Setup & Manual Control")
        self.notebook.add(self.tab_calib, text="Calibration")
        self.notebook.add(self.tab_exp, text="Experiment")
        
        self._build_motion_tab()
        self._build_calib_tab()
        self._build_exp_tab()
        
    def _build_motion_tab(self):
        # Top: Connection Settings
        conn_frame = ttk.LabelFrame(self.tab_motion, text="Connection Settings")
        conn_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(conn_frame, text="Backend:").grid(row=0, column=0, padx=5, pady=5)
        self.var_backend = tk.StringVar(value=self.config.get("hardware.motion_backend", "marlin"))
        cb_backend = ttk.Combobox(conn_frame, textvariable=self.var_backend, values=["marlin", "klipper"], state="readonly", width=10)
        cb_backend.grid(row=0, column=1, padx=5, pady=5)
        
        ttk.Label(conn_frame, text="Klipper Host:").grid(row=0, column=2, padx=5, pady=5)
        self.var_klipper_host = tk.StringVar(value=self.config.get("hardware.klipper.host", "127.0.0.1"))
        ttk.Entry(conn_frame, textvariable=self.var_klipper_host, width=15).grid(row=0, column=3, padx=5, pady=5)
        
        ttk.Button(conn_frame, text="Apply & Reconnect", command=self._apply_connection).grid(row=0, column=4, padx=10, pady=5)
        
        self.lbl_status = ttk.Label(conn_frame, text="Disconnected", foreground="red")
        self.lbl_status.grid(row=0, column=5, padx=10, pady=5)
        
        self.lbl_cam_status = ttk.Label(conn_frame, text=f"Camera: {self.camera.backend or 'None'}", foreground="blue")
        self.lbl_cam_status.grid(row=0, column=6, padx=10, pady=5)
        
        # Camera Controls (Exposure / Gain)
        cam_ctrl_frame = ttk.LabelFrame(self.tab_motion, text="Camera Settings")
        cam_ctrl_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Exposure: Slider + Entry
        ttk.Label(cam_ctrl_frame, text="Exposure (ms):").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        self.var_exp_ms = tk.DoubleVar(value=self.camera.get_exposure() / 1000.0)
        self.scale_exp = ttk.Scale(cam_ctrl_frame, from_=0.1, to=2000.0, variable=self.var_exp_ms, command=self._on_exp_slider, length=200)
        self.scale_exp.grid(row=0, column=1, padx=5, pady=5)
        
        exp_entry = ttk.Entry(cam_ctrl_frame, textvariable=self.var_exp_ms, width=8)
        exp_entry.grid(row=0, column=2, padx=5, pady=5)
        exp_entry.bind("<Return>", self._on_exp_entry)
        exp_entry.bind("<FocusOut>", self._on_exp_entry)
        
        # Gain: Slider + Entry
        ttk.Label(cam_ctrl_frame, text="Gain:").grid(row=0, column=3, padx=5, pady=5, sticky=tk.W)
        self.var_gain = tk.IntVar(value=self.camera.get_gain())
        self.scale_gain = ttk.Scale(cam_ctrl_frame, from_=0, to=500, variable=self.var_gain, command=self._on_gain_slider, length=150)
        self.scale_gain.grid(row=0, column=4, padx=5, pady=5)
        
        gain_entry = ttk.Entry(cam_ctrl_frame, textvariable=self.var_gain, width=5)
        gain_entry.grid(row=0, column=5, padx=5, pady=5)
        gain_entry.bind("<Return>", self._on_gain_entry)
        gain_entry.bind("<FocusOut>", self._on_gain_entry)
        
        # Resolution
        ttk.Label(cam_ctrl_frame, text="Resolution:").grid(row=1, column=0, padx=5, pady=5, sticky=tk.W)
        self.var_res = tk.StringVar()
        self.cb_res = ttk.Combobox(cam_ctrl_frame, textvariable=self.var_res, state="readonly", width=15)
        self.cb_res.grid(row=1, column=1, padx=5, pady=5, sticky=tk.W)
        self.cb_res.bind("<<ComboboxSelected>>", self._on_res_change)
        
        self._populate_resolutions()

        # Left side: Camera Preview (Motion Tab)
        cam_frame = ttk.LabelFrame(self.tab_motion, text="Camera Preview")
        cam_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.cam_label = ttk.Label(cam_frame)
        self.cam_label.pack(fill=tk.BOTH, expand=True)
        
        # Right side: Controls
        ctrl_frame = ttk.Frame(self.tab_motion)
        ctrl_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=5, pady=5)
        
        pos_frame = ttk.LabelFrame(ctrl_frame, text="Position")
        pos_frame.pack(fill=tk.X, pady=5)
        self.lbl_pos = ttk.Label(pos_frame, text="X: 0.00 Y: 0.00 Z: 0.00", font=("Arial", 12, "bold"))
        self.lbl_pos.pack(pady=10)
        
        btn_home = ttk.Button(pos_frame, text="Home All", command=self._cmd_home)
        btn_home.pack(pady=5)
        
        jog_frame = ttk.LabelFrame(ctrl_frame, text="Jog Controls")
        jog_frame.pack(fill=tk.X, pady=5)
        
        self.step_var = tk.DoubleVar(value=1.0)
        steps = ttk.Frame(jog_frame)
        steps.pack(pady=5)
        ttk.Radiobutton(steps, text="0.1", variable=self.step_var, value=0.1).pack(side=tk.LEFT)
        ttk.Radiobutton(steps, text="1.0", variable=self.step_var, value=1.0).pack(side=tk.LEFT)
        ttk.Radiobutton(steps, text="10.0", variable=self.step_var, value=10.0).pack(side=tk.LEFT)
        
        grid = ttk.Frame(jog_frame)
        grid.pack(pady=5)
        ttk.Button(grid, text="Y+", command=lambda: self._jog(Y=1)).grid(row=0, column=1)
        ttk.Button(grid, text="X-", command=lambda: self._jog(X=-1)).grid(row=1, column=0)
        ttk.Button(grid, text="X+", command=lambda: self._jog(X=1)).grid(row=1, column=2)
        ttk.Button(grid, text="Y-", command=lambda: self._jog(Y=-1)).grid(row=2, column=1)
        ttk.Button(grid, text="Z+", command=lambda: self._jog(Z=1)).grid(row=0, column=3, padx=10)
        ttk.Button(grid, text="Z-", command=lambda: self._jog(Z=-1)).grid(row=2, column=3, padx=10)
        
    def _build_calib_tab(self):
        # 3 columns: Preview | Controls | Map
        paned = ttk.PanedWindow(self.tab_calib, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Col 1: Preview
        preview_frame = ttk.LabelFrame(paned, text="Live Camera Preview")
        self.calib_preview_label = ttk.Label(preview_frame)
        self.calib_preview_label.pack(fill=tk.BOTH, expand=True)
        paned.add(preview_frame, weight=2)
        
        # Col 2: Controls
        ctrl_frame = ttk.Frame(paned)
        paned.add(ctrl_frame, weight=1)
        
        corners_group = ttk.LabelFrame(ctrl_frame, text="Corner Calibration")
        corners_group.pack(fill=tk.X, pady=5)
        
        ttk.Label(corners_group, text="1. Jog to corner, then click 'Set'").grid(row=0, column=0, columnspan=2, pady=5, sticky=tk.W)
        
        self.lbl_ul = ttk.Label(corners_group, text="Upper Left: Not Set")
        self.lbl_ul.grid(row=1, column=0, sticky=tk.W, pady=2)
        ttk.Button(corners_group, text="Set UL", command=lambda: self._set_corner('ul')).grid(row=1, column=1, padx=5)
        
        self.lbl_ur = ttk.Label(corners_group, text="Upper Right: Not Set")
        self.lbl_ur.grid(row=2, column=0, sticky=tk.W, pady=2)
        ttk.Button(corners_group, text="Set UR", command=lambda: self._set_corner('ur')).grid(row=2, column=1, padx=5)
        
        self.lbl_ll = ttk.Label(corners_group, text="Lower Left: Not Set")
        self.lbl_ll.grid(row=3, column=0, sticky=tk.W, pady=2)
        ttk.Button(corners_group, text="Set LL", command=lambda: self._set_corner('ll')).grid(row=3, column=1, padx=5)
        
        self.lbl_lr = ttk.Label(corners_group, text="Lower Right: Not Set")
        self.lbl_lr.grid(row=4, column=0, sticky=tk.W, pady=2)
        ttk.Button(corners_group, text="Set LR", command=lambda: self._set_corner('lr')).grid(row=4, column=1, padx=5)
        
        size_group = ttk.LabelFrame(ctrl_frame, text="Plate Dimensions")
        size_group.pack(fill=tk.X, pady=5)
        
        ttk.Label(size_group, text="Columns (X):").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.var_w = tk.IntVar(value=12)
        w_entry = ttk.Entry(size_group, textvariable=self.var_w, width=5)
        w_entry.grid(row=0, column=1, sticky=tk.W, pady=2)
        w_entry.bind("<FocusOut>", self._on_plate_dim_change)
        w_entry.bind("<Return>", self._on_plate_dim_change)
        
        ttk.Label(size_group, text="Rows (Y):").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.var_d = tk.IntVar(value=8)
        d_entry = ttk.Entry(size_group, textvariable=self.var_d, width=5)
        d_entry.grid(row=1, column=1, sticky=tk.W, pady=2)
        d_entry.bind("<FocusOut>", self._on_plate_dim_change)
        d_entry.bind("<Return>", self._on_plate_dim_change)
        
        ttk.Label(size_group, text="Pattern:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.var_pattern = tk.StringVar(value=WellPlate.PATTERN_RASTER)
        ttk.Combobox(size_group, textvariable=self.var_pattern, values=[WellPlate.PATTERN_RASTER, WellPlate.PATTERN_SNAKE], state="readonly").grid(row=2, column=1, sticky=tk.W, pady=2)
        
        ttk.Button(size_group, text="Update Map & Save", command=self._save_calib).grid(row=3, column=0, columnspan=2, pady=10)
        
        # Col 3: Map
        map_frame = ttk.LabelFrame(paned, text="Well Map (Click to Navigate)")
        paned.add(map_frame, weight=1)
        
        self.calib_grid = WellGrid(map_frame, rows=8, cols=12, mode="navigate")
        self.calib_grid.pack(expand=True)
        self.calib_grid.on_well_clicked = self._navigate_to_well
        
    def _build_exp_tab(self):
        # 3 columns: Preview | Settings | Selection
        paned = ttk.PanedWindow(self.tab_exp, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Col 1: Preview
        preview_frame = ttk.LabelFrame(paned, text="Live Camera Preview")
        self.exp_preview_label = ttk.Label(preview_frame)
        self.exp_preview_label.pack(fill=tk.BOTH, expand=True)
        paned.add(preview_frame, weight=2)
        
        # Col 2: Settings
        settings_frame = ttk.Frame(paned)
        paned.add(settings_frame, weight=1)
        
        grp = ttk.LabelFrame(settings_frame, text="Experiment Settings")
        grp.pack(fill=tk.X, pady=5)
        
        ttk.Label(grp, text="Experiment Name:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.var_exp_name = tk.StringVar(value="my_experiment")
        ttk.Entry(grp, textvariable=self.var_exp_name).grid(row=0, column=1, sticky=tk.W, pady=5)
        
        ttk.Label(grp, text="Calibration File:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.var_cal_file = tk.StringVar()
        cal_sub = ttk.Frame(grp)
        cal_sub.grid(row=1, column=1, sticky=tk.W, pady=5)
        self.cb_cal = ttk.Combobox(cal_sub, textvariable=self.var_cal_file, state="readonly", width=15)
        self.cb_cal.pack(side=tk.LEFT)
        self.cb_cal.bind("<<ComboboxSelected>>", self._on_cal_file_selected)
        ttk.Button(cal_sub, text="Refresh", command=self._refresh_cals).pack(side=tk.LEFT, padx=2)
        
        ttk.Label(grp, text="Delay per well (s):").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.var_delay = tk.DoubleVar(value=1.0)
        ttk.Entry(grp, textvariable=self.var_delay, width=5).grid(row=2, column=1, sticky=tk.W, pady=5)
        
        self.var_fast_raw = tk.BooleanVar(value=False)
        ttk.Checkbutton(grp, text="Fast Raw Capture (.npy)\n(Requires Post-Processing)", variable=self.var_fast_raw).grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=5)
        
        ctrl_frame = ttk.Frame(settings_frame)
        ctrl_frame.pack(fill=tk.X, pady=20)
        self.btn_start = ttk.Button(ctrl_frame, text="Start Experiment", command=self._start_exp)
        self.btn_start.pack(side=tk.LEFT, padx=5)
        self.btn_stop = ttk.Button(ctrl_frame, text="Stop", command=self._stop_exp, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=5)
        
        self.lbl_exp_status = ttk.Label(settings_frame, text="Status: Ready", font=("Arial", 10, "italic"), foreground="blue")
        self.lbl_exp_status.pack(pady=10, anchor=tk.W)
        
        # Col 3: Selection
        sel_frame = ttk.LabelFrame(paned, text="Well Selection (Drag to toggle)")
        paned.add(sel_frame, weight=1)
        
        tb = ttk.Frame(sel_frame)
        tb.pack(fill=tk.X, pady=2)
        ttk.Button(tb, text="Check All", command=lambda: self.exp_grid.set_all(True)).pack(side=tk.LEFT, padx=2)
        ttk.Button(tb, text="Uncheck All", command=lambda: self.exp_grid.set_all(False)).pack(side=tk.LEFT, padx=2)
        ttk.Button(tb, text="Invert", command=lambda: self.exp_grid.invert()).pack(side=tk.LEFT, padx=2)
        
        self.lbl_sel_count = ttk.Label(tb, text="0/0 selected", foreground="gray")
        self.lbl_sel_count.pack(side=tk.RIGHT, padx=5)
        
        self.exp_grid = WellGrid(sel_frame, rows=8, cols=12, mode="select")
        self.exp_grid.pack(expand=True)
        self.exp_grid.on_selection_changed = self._update_sel_count
        
        self._refresh_cals()
        self._update_sel_count()
        
    def _on_plate_dim_change(self, event=None):
        r = self.var_d.get()
        c = self.var_w.get()
        self.calib_grid.rebuild(r, c)
        self.exp_grid.rebuild(r, c)
        self._update_sel_count()
        
    def _update_sel_count(self):
        if not hasattr(self, 'exp_grid'): return
        sel = len(self.exp_grid.get_selected_indices())
        tot = self.exp_grid.rows * self.exp_grid.cols
        self.lbl_sel_count.config(text=f"{sel}/{tot} selected")

    def _navigate_to_well(self, row, col):
        if not self.motion: return
        # Try to generate path from current corners
        self.cal_mgr.width = self.var_w.get()
        self.cal_mgr.depth = self.var_d.get()
        self.cal_mgr.pattern = self.var_pattern.get()
        try:
            positions, _ = self.cal_mgr.generate_path()
            idx = row * self.cal_mgr.width + col
            if 0 <= idx < len(positions):
                x, y, z = positions[idx]
                def task():
                    self.motion.move_absolute(X=x, Y=y, Z=z)
                    self.root.after(0, self._update_pos_label)
                threading.Thread(target=task, daemon=True).start()
        except Exception as e:
            messagebox.showwarning("Navigation Error", f"Cannot navigate: {e}\nSet all 4 corners first.")

    def _on_cal_file_selected(self, event=None):
        cal_file = self.var_cal_file.get()
        if not cal_file: return
        cal_path = os.path.join(get_config().get("paths.calibration_dir", "config/calibrations"), cal_file)
        try:
            # We just want to know dimensions to rebuild grid
            import json
            with open(cal_path, 'r') as f:
                data = json.load(f)
            w = data.get('width', 12)
            d = data.get('depth', 8)
            self.var_w.set(w)
            self.var_d.set(d)
            self._on_plate_dim_change()
        except Exception:
            pass

    # --- Camera Settings ---
    def _on_exp_slider(self, val):
        ms = float(val)
        self.var_exp_ms.set(round(ms, 2))
        self.camera.set_exposure(int(ms * 1000))
        
    def _on_exp_entry(self, event=None):
        try:
            ms = self.var_exp_ms.get()
            self.camera.set_exposure(int(ms * 1000))
        except:
            pass

    def _on_gain_slider(self, val):
        g = int(float(val))
        self.var_gain.set(g)
        self.camera.set_gain(g)
        
    def _on_gain_entry(self, event=None):
        try:
            g = self.var_gain.get()
            self.camera.set_gain(g)
        except:
            pass
        
    def _populate_resolutions(self):
        if hasattr(self.camera, 'get_supported_resolutions'):
            res_list = self.camera.get_supported_resolutions()
            str_list = [f"{w}x{h}" for w, h in res_list]
            self.cb_res['values'] = str_list
            current = f"{self.camera.resolution[0]}x{self.camera.resolution[1]}"
            if current in str_list:
                self.cb_res.set(current)
            elif str_list:
                self.cb_res.set(str_list[-1])
                self._on_res_change(None)
                
    def _on_res_change(self, event):
        val = self.var_res.get()
        if val and "x" in val:
            w, h = map(int, val.split("x"))
            if hasattr(self.camera, 'set_resolution'):
                self.camera.set_resolution(w, h)

    def _apply_connection(self):
        self.config.set("hardware.motion_backend", self.var_backend.get())
        self.config.set("hardware.klipper.host", self.var_klipper_host.get())
        self.lbl_status.config(text="Connecting...", foreground="orange")
        self.root.update()
        self._connect_motion()
        
    def _refresh_cals(self):
        cal_dir = get_config().get("paths.calibration_dir", "config/calibrations")
        if os.path.exists(cal_dir):
            files = [os.path.basename(f) for f in glob.glob(os.path.join(cal_dir, "*.json"))]
            self.cb_cal['values'] = files
            if files and not self.var_cal_file.get():
                self.cb_cal.set(files[0])
                self._on_cal_file_selected()
                
    def _update_pos_label(self):
        if self.motion and self.motion.X is not None:
            self.lbl_pos.config(text=f"X: {self.motion.X:.2f} Y: {self.motion.Y:.2f} Z: {self.motion.Z:.2f}")
            
    def _cmd_home(self):
        if not self.motion: return
        def task():
            self.motion.home()
            self.root.after(0, self._update_pos_label)
        threading.Thread(target=task, daemon=True).start()
        
    def _jog(self, X=0, Y=0, Z=0):
        if not self.motion: return
        step = self.step_var.get()
        def task():
            self.motion.move_relative(X=X*step if X else None, 
                                      Y=Y*step if Y else None, 
                                      Z=Z*step if Z else None)
            self.root.after(0, self._update_pos_label)
        threading.Thread(target=task, daemon=True).start()
        
    def _set_corner(self, corner):
        if not self.motion or self.motion.X is None:
            messagebox.showerror("Error", "Position unknown. Please connect and home first.")
            return
            
        pos = (self.motion.X, self.motion.Y, self.motion.Z)
        if corner == 'ul':
            self.cal_mgr.upper_left = pos
            self.lbl_ul.config(text=f"Upper Left: {pos[0]:.1f}, {pos[1]:.1f}, {pos[2]:.1f}")
        elif corner == 'ur':
            self.cal_mgr.upper_right = pos
            self.lbl_ur.config(text=f"Upper Right: {pos[0]:.1f}, {pos[1]:.1f}, {pos[2]:.1f}")
        elif corner == 'll':
            self.cal_mgr.lower_left = pos
            self.lbl_ll.config(text=f"Lower Left: {pos[0]:.1f}, {pos[1]:.1f}, {pos[2]:.1f}")
        elif corner == 'lr':
            self.cal_mgr.lower_right = pos
            self.lbl_lr.config(text=f"Lower Right: {pos[0]:.1f}, {pos[1]:.1f}, {pos[2]:.1f}")
            
    def _save_calib(self):
        self.cal_mgr.width = self.var_w.get()
        self.cal_mgr.depth = self.var_d.get()
        self.cal_mgr.pattern = self.var_pattern.get()
        try:
            name = "calib_" + str(int(time.time()))
            self.cal_mgr.save(name)
            messagebox.showinfo("Success", f"Saved as {name}.json")
            self._refresh_cals()
        except Exception as e:
            messagebox.showerror("Error", str(e))
            
    def _update_exp_status(self, msg):
        self.lbl_exp_status.config(text=f"Status: {msg}")
        self.root.update_idletasks()

    def _start_exp(self):
        if not self.motion or not self.exp_runner:
            messagebox.showerror("Error", "Motion controller not connected.")
            return
            
        cal_file = self.var_cal_file.get()
        if not cal_file:
            messagebox.showerror("Error", "Select a calibration file")
            return
            
        cal_path = os.path.join(get_config().get("paths.calibration_dir", "config/calibrations"), cal_file)
        try:
            positions, labels = self.cal_mgr.load(cal_path)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load cal: {e}")
            return
            
        # Filter positions based on user selection in the grid
        selected_indices = self.exp_grid.get_selected_indices()
        if not selected_indices:
            messagebox.showerror("Error", "No wells selected!")
            return
            
        filtered_pos = [positions[i] for i in selected_indices if i < len(positions)]
        filtered_labels = [labels[i] for i in selected_indices if i < len(labels)]
            
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        
        def task():
            self.exp_runner.run(
                self.var_exp_name.get(), 
                filtered_pos, 
                filtered_labels, 
                self.var_delay.get(),
                callback=lambda msg: self.root.after(0, self._update_exp_status, msg),
                fast_raw_mode=self.var_fast_raw.get()
            )
            self.root.after(0, self._exp_done)
            
        threading.Thread(target=task, daemon=True).start()
        
    def _stop_exp(self):
        self.exp_runner.stop()
        
    def _exp_done(self):
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        messagebox.showinfo("Done", "Experiment completed or stopped.")
        
    def update_camera_preview(self):
        # Determine the preview mode based on experiment state
        is_experiment_running = self.exp_runner and self.exp_runner.running
        
        active_tab_id = self.notebook.select()
        active_tab_text = self.notebook.tab(active_tab_id, "text")
        
        target_label = None
        if active_tab_text == "Setup & Manual Control":
            target_label = self.cam_label
        elif active_tab_text == "Calibration":
            target_label = self.calib_preview_label
        elif active_tab_text == "Experiment":
            target_label = self.exp_preview_label
            
        if not target_label:
            self.root.after(100, self.update_camera_preview)
            return
            
        if is_experiment_running and self.exp_runner.is_fast_raw_mode:
            self._display_placeholder(target_label, "Preview disabled during Fast Raw Capture")
            self.root.after(500, self.update_camera_preview)
            return
            
        if is_experiment_running and not self.exp_runner.is_fast_raw_mode:
            last_path = self.exp_runner.last_written_image_path
            if last_path and os.path.exists(last_path):
                try:
                    frame = cv2.imread(last_path)
                    if frame is not None:
                        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        self._render_frame_to_gui(target_label, frame, add_crosshair=False)
                except Exception:
                    pass
            self.root.after(500, self.update_camera_preview)
            return
            
        if self.camera.running:
            frame = self.camera.get_frame()
            if frame is not None:
                if self.camera.backend == "picamera2":
                    pass # already rgb
                else:
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                
                # Only add crosshair on Setup and Calibration tabs
                add_cross = active_tab_text in ["Setup & Manual Control", "Calibration"]
                self._render_frame_to_gui(target_label, frame, add_crosshair=add_cross)
                
        self.root.after(33, self.update_camera_preview)
        
    def _display_placeholder(self, target_label, text):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        font = cv2.FONT_HERSHEY_SIMPLEX
        text_size = cv2.getTextSize(text, font, 0.8, 2)[0]
        text_x = (640 - text_size[0]) // 2
        text_y = (480 + text_size[1]) // 2
        cv2.putText(frame, text, (text_x, text_y), font, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
        img = Image.fromarray(frame)
        imgtk = ImageTk.PhotoImage(image=img)
        target_label.imgtk = imgtk
        target_label.configure(image=imgtk)
        
    def _render_frame_to_gui(self, target_label, frame, add_crosshair=True):
        h, w = frame.shape[:2]
        
        # Calculate aspect ratio preserving resize to fit 640x480 max bounds
        target_w, target_h = 640, 480
        scale = min(target_w/w, target_h/h)
        new_w, new_h = int(w * scale), int(h * scale)
        
        if add_crosshair:
            # Draw crosshair BEFORE resize so it's perfectly centered on the sensor data
            # Fix green horizontal bar by making crosshair thinner and semi-transparent
            # We'll just use a simple 1px line which is standard
            cv2.line(frame, (w//2, 0), (w//2, h), (0, 255, 0), 1)
            cv2.line(frame, (0, h//2), (w, h//2), (0, 255, 0), 1)
            
        frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
        
        # Pad with black to make it exactly 640x480 so UI doesn't jump
        padded = np.zeros((target_h, target_w, 3), dtype=np.uint8)
        x_off = (target_w - new_w) // 2
        y_off = (target_h - new_h) // 2
        padded[y_off:y_off+new_h, x_off:x_off+new_w] = frame
        
        img = Image.fromarray(padded)
        imgtk = ImageTk.PhotoImage(image=img)
        target_label.imgtk = imgtk
        target_label.configure(image=imgtk)
        
    def on_close(self):
        self.camera.stop()
        if self.exp_runner:
            self.exp_runner.stop()
        if self.motion:
            self.motion.disconnect()
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
