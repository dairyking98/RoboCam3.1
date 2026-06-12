import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import cv2
from PIL import Image, ImageTk
import os
import glob
import time
import json
import numpy as np

from robocam.config import get_config
from robocam.motion import MotionController
from robocam.camera import Camera
from robocam.calibration import CalibrationManager, WellPlate
from robocam.experiment import ExperimentRunner

def datetime_name():
    return time.strftime("%Y%m%d_%H%M%S")


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
        self.var_custom_step = tk.StringVar(value="")
        steps = ttk.Frame(jog_frame)
        steps.pack(pady=5)
        for val in [0.1, 1.0, 10.0]:
            ttk.Radiobutton(steps, text=str(val), variable=self.step_var, value=val).pack(side=tk.LEFT)
        ttk.Radiobutton(steps, text="Custom:", variable=self.step_var, value=-1).pack(side=tk.LEFT)
        custom_entry = ttk.Entry(steps, textvariable=self.var_custom_step, width=6)
        custom_entry.pack(side=tk.LEFT, padx=2)
        custom_entry.bind("<FocusIn>", lambda e: self.step_var.set(-1))
        custom_entry.bind("<Return>", lambda e: self.step_var.set(-1))
        
        grid = ttk.Frame(jog_frame)
        grid.pack(pady=5)
        ttk.Button(grid, text="Y+", command=lambda: self._jog(Y=1)).grid(row=0, column=1)
        ttk.Button(grid, text="X-", command=lambda: self._jog(X=-1)).grid(row=1, column=0)
        ttk.Button(grid, text="X+", command=lambda: self._jog(X=1)).grid(row=1, column=2)
        ttk.Button(grid, text="Y-", command=lambda: self._jog(Y=-1)).grid(row=2, column=1)
        ttk.Button(grid, text="Z+", command=lambda: self._jog(Z=1)).grid(row=0, column=3, padx=10)
        ttk.Button(grid, text="Z-", command=lambda: self._jog(Z=-1)).grid(row=2, column=3, padx=10)

        # Go-To XYZ
        goto_frame = ttk.LabelFrame(ctrl_frame, text="Go To Position")
        goto_frame.pack(fill=tk.X, pady=5)
        ttk.Label(goto_frame, text="X:").grid(row=0, column=0, padx=3, pady=5)
        self.var_goto_x = tk.StringVar(value="")
        ttk.Entry(goto_frame, textvariable=self.var_goto_x, width=7).grid(row=0, column=1, padx=3, pady=5)
        ttk.Label(goto_frame, text="Y:").grid(row=0, column=2, padx=3, pady=5)
        self.var_goto_y = tk.StringVar(value="")
        ttk.Entry(goto_frame, textvariable=self.var_goto_y, width=7).grid(row=0, column=3, padx=3, pady=5)
        ttk.Label(goto_frame, text="Z:").grid(row=0, column=4, padx=3, pady=5)
        self.var_goto_z = tk.StringVar(value="")
        ttk.Entry(goto_frame, textvariable=self.var_goto_z, width=7).grid(row=0, column=5, padx=3, pady=5)
        ttk.Button(goto_frame, text="Go", command=self._goto_xyz).grid(row=0, column=6, padx=5, pady=5)

    def _build_jog_buttons(self, parent):
        steps = ttk.Frame(parent)
        steps.pack(pady=4)
        for val in [0.1, 1.0, 10.0]:
            ttk.Radiobutton(steps, text=str(val), variable=self.step_var, value=val).pack(side=tk.LEFT)
        ttk.Radiobutton(steps, text="Custom:", variable=self.step_var, value=-1).pack(side=tk.LEFT)
        custom_entry = ttk.Entry(steps, textvariable=self.var_custom_step, width=6)
        custom_entry.pack(side=tk.LEFT, padx=2)
        custom_entry.bind("<FocusIn>", lambda e: self.step_var.set(-1))
        custom_entry.bind("<Return>", lambda e: self.step_var.set(-1))

        grid = ttk.Frame(parent)
        grid.pack(pady=4)
        ttk.Button(grid, text="Y+", command=lambda: self._jog(Y=1)).grid(row=0, column=1)
        ttk.Button(grid, text="X-", command=lambda: self._jog(X=-1)).grid(row=1, column=0)
        ttk.Button(grid, text="X+", command=lambda: self._jog(X=1)).grid(row=1, column=2)
        ttk.Button(grid, text="Y-", command=lambda: self._jog(Y=-1)).grid(row=2, column=1)
        ttk.Button(grid, text="Z+", command=lambda: self._jog(Z=1)).grid(row=0, column=3, padx=8)
        ttk.Button(grid, text="Z-", command=lambda: self._jog(Z=-1)).grid(row=2, column=3, padx=8)

        # Go-To XYZ (inline in calibration tab too)
        goto_frame = ttk.LabelFrame(parent, text="Go To Position")
        goto_frame.pack(fill=tk.X, pady=4)
        ttk.Label(goto_frame, text="X:").grid(row=0, column=0, padx=3, pady=4)
        ttk.Entry(goto_frame, textvariable=self.var_goto_x, width=7).grid(row=0, column=1, padx=3, pady=4)
        ttk.Label(goto_frame, text="Y:").grid(row=0, column=2, padx=3, pady=4)
        ttk.Entry(goto_frame, textvariable=self.var_goto_y, width=7).grid(row=0, column=3, padx=3, pady=4)
        ttk.Label(goto_frame, text="Z:").grid(row=0, column=4, padx=3, pady=4)
        ttk.Entry(goto_frame, textvariable=self.var_goto_z, width=7).grid(row=0, column=5, padx=3, pady=4)
        ttk.Button(goto_frame, text="Go", command=self._goto_xyz).grid(row=0, column=6, padx=5, pady=4)

    def _build_camera_controls(self, parent, include_resolution=False):
        ttk.Label(parent, text="Exposure (ms):").grid(row=0, column=0, padx=3, pady=3, sticky=tk.W)
        ttk.Scale(parent, from_=0.1, to=2000.0, variable=self.var_exp_ms, command=self._on_exp_slider, length=150).grid(row=0, column=1, padx=3, pady=3)
        exp_entry = ttk.Entry(parent, textvariable=self.var_exp_ms, width=8)
        exp_entry.grid(row=0, column=2, padx=3, pady=3)
        exp_entry.bind("<Return>", self._on_exp_entry)
        exp_entry.bind("<FocusOut>", self._on_exp_entry)

        ttk.Label(parent, text="Gain:").grid(row=1, column=0, padx=3, pady=3, sticky=tk.W)
        ttk.Scale(parent, from_=0, to=500, variable=self.var_gain, command=self._on_gain_slider, length=150).grid(row=1, column=1, padx=3, pady=3)
        gain_entry = ttk.Entry(parent, textvariable=self.var_gain, width=8)
        gain_entry.grid(row=1, column=2, padx=3, pady=3)
        gain_entry.bind("<Return>", self._on_gain_entry)
        gain_entry.bind("<FocusOut>", self._on_gain_entry)

        if include_resolution:
            ttk.Label(parent, text="Resolution:").grid(row=2, column=0, padx=3, pady=3, sticky=tk.W)
            self.cb_res_calib = ttk.Combobox(parent, textvariable=self.var_res, state="readonly", width=15)
            self.cb_res_calib.grid(row=2, column=1, columnspan=2, padx=3, pady=3, sticky=tk.W)
            self.cb_res_calib.bind("<<ComboboxSelected>>", self._on_res_change)
            if hasattr(self, "cb_res") and self.cb_res["values"]:
                self.cb_res_calib["values"] = self.cb_res["values"]
        
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

        move_group = ttk.LabelFrame(ctrl_frame, text="Movement Controls")
        move_group.pack(fill=tk.X, pady=5)
        self.lbl_calib_pos = ttk.Label(move_group, text="X: 0.00 Y: 0.00 Z: 0.00", font=("Arial", 10, "bold"))
        self.lbl_calib_pos.pack(pady=5)
        ttk.Button(move_group, text="Home All", command=self._cmd_home).pack(pady=2)
        self._build_jog_buttons(move_group)

        cam_group = ttk.LabelFrame(ctrl_frame, text="Camera Controls")
        cam_group.pack(fill=tk.X, pady=5)
        self._build_camera_controls(cam_group, include_resolution=True)

        quick_group = ttk.LabelFrame(ctrl_frame, text="Quick Capture")
        quick_group.pack(fill=tk.X, pady=5)
        ttk.Label(quick_group, text="Format:").grid(row=0, column=0, sticky=tk.W, padx=3, pady=3)
        self.var_quick_fmt = tk.StringVar(value="jpg")
        ttk.Combobox(quick_group, textvariable=self.var_quick_fmt, values=["jpg", "png", "tif"], state="readonly", width=6).grid(row=0, column=1, sticky=tk.W, padx=3, pady=3)
        ttk.Button(quick_group, text="Capture Image", command=self._quick_capture_image).grid(row=1, column=0, columnspan=2, sticky=tk.EW, padx=3, pady=3)
        ttk.Label(quick_group, text="Video seconds:").grid(row=2, column=0, sticky=tk.W, padx=3, pady=3)
        self.var_quick_video_s = tk.DoubleVar(value=5.0)
        ttk.Entry(quick_group, textvariable=self.var_quick_video_s, width=6).grid(row=2, column=1, sticky=tk.W, padx=3, pady=3)
        ttk.Button(quick_group, text="Record Video", command=self._quick_capture_video).grid(row=3, column=0, columnspan=2, sticky=tk.EW, padx=3, pady=3)
        self.lbl_quick_status = ttk.Label(quick_group, text="Ready", foreground="gray")
        self.lbl_quick_status.grid(row=4, column=0, columnspan=2, sticky=tk.W, padx=3, pady=3)
        
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
        
        ttk.Label(size_group, text="Name:").grid(row=3, column=0, sticky=tk.W, pady=2)
        self.var_calib_name = tk.StringVar(value="calibration")
        ttk.Entry(size_group, textvariable=self.var_calib_name, width=18).grid(row=3, column=1, sticky=tk.W, pady=2)

        file_buttons = ttk.Frame(size_group)
        file_buttons.grid(row=4, column=0, columnspan=2, pady=10, sticky=tk.EW)
        ttk.Button(file_buttons, text="Update Map", command=self._update_calib_map).pack(side=tk.LEFT, padx=2)
        ttk.Button(file_buttons, text="Save", command=self._save_calib).pack(side=tk.LEFT, padx=2)
        ttk.Button(file_buttons, text="Load", command=self._load_calib_dialog).pack(side=tk.LEFT, padx=2)
        
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
        
        ttk.Label(grp, text="Mode:").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.var_exp_mode = tk.StringVar(value="Image")
        mode_cb = ttk.Combobox(
            grp, textvariable=self.var_exp_mode,
            values=["Image", "Raw .npy", "Video"],
            state="readonly", width=12
        )
        mode_cb.grid(row=2, column=1, sticky=tk.W, pady=5)
        mode_cb.bind("<<ComboboxSelected>>", self._on_exp_mode_change)

        ttk.Label(grp, text="Dwell per well (s):").grid(row=3, column=0, sticky=tk.W, pady=5)
        self.var_delay = tk.DoubleVar(value=1.0)
        ttk.Entry(grp, textvariable=self.var_delay, width=7).grid(row=3, column=1, sticky=tk.W, pady=5)

        self.lbl_image_fmt = ttk.Label(grp, text="Image format:")
        self.lbl_image_fmt.grid(row=4, column=0, sticky=tk.W, pady=5)
        self.var_image_fmt = tk.StringVar(value="jpg")
        self.cb_image_fmt = ttk.Combobox(grp, textvariable=self.var_image_fmt, values=["jpg", "png", "tif"], state="readonly", width=7)
        self.cb_image_fmt.grid(row=4, column=1, sticky=tk.W, pady=5)

        # --- Duration: shown for Raw .npy and Video ---
        self.lbl_pre_duration = ttk.Label(grp, text="Record duration (s):")
        self.lbl_pre_duration.grid(row=5, column=0, sticky=tk.W, pady=5)
        self.var_pre_duration = tk.DoubleVar(value=5.0)
        self.ent_pre_duration = ttk.Entry(grp, textvariable=self.var_pre_duration, width=7)
        self.ent_pre_duration.grid(row=5, column=1, sticky=tk.W, pady=5)

        # --- Laser checkbox: shown for Raw .npy and Video ---
        self.var_use_laser = tk.BooleanVar(value=False)
        self.chk_laser = ttk.Checkbutton(
            grp, text="Use Laser",
            variable=self.var_use_laser,
            command=self._on_exp_mode_change
        )
        self.chk_laser.grid(row=6, column=0, columnspan=2, sticky=tk.W, pady=5)

        # --- Laser timing: only shown when Use Laser is checked ---
        self.lbl_laser_on = ttk.Label(grp, text="Laser ON (s):")
        self.lbl_laser_on.grid(row=7, column=0, sticky=tk.W, pady=2)
        self.var_laser_on = tk.DoubleVar(value=1.0)
        self.ent_laser_on = ttk.Entry(grp, textvariable=self.var_laser_on, width=7)
        self.ent_laser_on.grid(row=7, column=1, sticky=tk.W, pady=2)

        self.lbl_post_duration = ttk.Label(grp, text="Post-laser (s):")
        self.lbl_post_duration.grid(row=8, column=0, sticky=tk.W, pady=2)
        self.var_post_duration = tk.DoubleVar(value=2.0)
        self.ent_post_duration = ttk.Entry(grp, textvariable=self.var_post_duration, width=7)
        self.ent_post_duration.grid(row=8, column=1, sticky=tk.W, pady=2)

        # Apply initial visibility
        self._on_exp_mode_change()

        preset_group = ttk.LabelFrame(settings_frame, text="Experiment Presets")
        preset_group.pack(fill=tk.X, pady=5)
        self.var_preset_name = tk.StringVar(value="default")
        self.cb_preset = ttk.Combobox(preset_group, textvariable=self.var_preset_name, width=18)
        self.cb_preset.grid(row=0, column=0, columnspan=3, sticky=tk.EW, padx=3, pady=3)
        ttk.Button(preset_group, text="Save", command=self._save_preset).grid(row=1, column=0, sticky=tk.EW, padx=3, pady=3)
        ttk.Button(preset_group, text="Load", command=self._load_preset).grid(row=1, column=1, sticky=tk.EW, padx=3, pady=3)
        ttk.Button(preset_group, text="Refresh", command=self._refresh_presets).grid(row=1, column=2, sticky=tk.EW, padx=3, pady=3)

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
        self._refresh_presets()
        self._update_sel_count()
        
    def _on_exp_mode_change(self, event=None):
        """Show/hide fields based on mode and laser checkbox state."""
        mode = self.var_exp_mode.get()
        is_timed = mode in ("Raw .npy", "Video")  # modes that have a duration
        use_laser = self.var_use_laser.get()

        # Image format: only shown for Image mode
        if mode == "Image":
            self.lbl_image_fmt.grid()
            self.cb_image_fmt.grid()
        else:
            self.lbl_image_fmt.grid_remove()
            self.cb_image_fmt.grid_remove()

        # Duration field: shown for Raw and Video, hidden for Image
        if is_timed:
            self.lbl_pre_duration.grid()
            self.ent_pre_duration.grid()
            # Relabel based on whether laser is active
            if use_laser:
                self.lbl_pre_duration.config(text="Pre-laser (s):")
            else:
                self.lbl_pre_duration.config(text="Record duration (s):")
        else:
            self.lbl_pre_duration.grid_remove()
            self.ent_pre_duration.grid_remove()

        # Laser checkbox: shown for Raw and Video only
        if is_timed:
            self.chk_laser.grid()
        else:
            self.chk_laser.grid_remove()
            self.var_use_laser.set(False)

        # Laser timing fields: only shown when Use Laser is checked AND mode is timed
        if is_timed and use_laser:
            self.lbl_laser_on.grid()
            self.ent_laser_on.grid()
            self.lbl_post_duration.grid()
            self.ent_post_duration.grid()
        else:
            self.lbl_laser_on.grid_remove()
            self.ent_laser_on.grid_remove()
            self.lbl_post_duration.grid_remove()
            self.ent_post_duration.grid_remove()

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
        self.cal_mgr.width = self.var_w.get()
        self.cal_mgr.depth = self.var_d.get()
        self.cal_mgr.pattern = self.var_pattern.get()
        try:
            # Use generate_path_with_labels — the correct method name
            labeled = self.cal_mgr.generate_path_with_labels()
            # labeled is a list of (label, (x, y, z)) in scan order
            # Convert row/col to the flat raster index so we always find the right well
            idx = row * self.cal_mgr.width + col
            if 0 <= idx < len(labeled):
                _, pos = labeled[idx]
                x, y, z = pos
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
            with open(cal_path, 'r') as f:
                data = json.load(f)
            w = data.get('x_quantity', data.get('width', 12))
            d = data.get('y_quantity', data.get('depth', 8))
            self.var_w.set(w)
            self.var_d.set(d)
            self.var_pattern.set(data.get("pattern", WellPlate.PATTERN_RASTER))
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
            if hasattr(self, "cb_res_calib"):
                self.cb_res_calib["values"] = str_list
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
            text = f"X: {self.motion.X:.2f} Y: {self.motion.Y:.2f} Z: {self.motion.Z:.2f}"
            self.lbl_pos.config(text=text)
            if hasattr(self, "lbl_calib_pos"):
                self.lbl_calib_pos.config(text=text)
            
    def _cmd_home(self):
        if not self.motion: return
        def task():
            self.motion.home()
            self.root.after(0, self._update_pos_label)
        threading.Thread(target=task, daemon=True).start()
        
    def _jog(self, X=0, Y=0, Z=0):
        if not self.motion: return
        step = self.step_var.get()
        if step == -1:
            try:
                step = float(self.var_custom_step.get())
            except (ValueError, AttributeError):
                step = 1.0
        if step <= 0:
            step = 1.0
        def task():
            self.motion.move_relative(X=X*step if X else None,
                                      Y=Y*step if Y else None,
                                      Z=Z*step if Z else None)
            self.root.after(0, self._update_pos_label)
        threading.Thread(target=task, daemon=True).start()

    def _goto_xyz(self):
        if not self.motion: return
        cur_x = self.motion.X or 0.0
        cur_y = self.motion.Y or 0.0
        cur_z = self.motion.Z or 0.0
        def _parse(var, fallback):
            s = var.get().strip()
            return float(s) if s else fallback
        try:
            x = _parse(self.var_goto_x, cur_x)
            y = _parse(self.var_goto_y, cur_y)
            z = _parse(self.var_goto_z, cur_z)
        except ValueError:
            from tkinter import messagebox
            messagebox.showerror("Go To Error", "Enter valid numeric values (or leave blank to keep current position).")
            return
        def task():
            self.motion.move_absolute(X=x, Y=y, Z=z)
            self.var_goto_x.set(f"{x:.3f}")
            self.var_goto_y.set(f"{y:.3f}")
            self.var_goto_z.set(f"{z:.3f}")
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

        self._update_calib_map(silent=True)

    def _update_corner_labels(self):
        mapping = [
            (self.cal_mgr.upper_left, self.lbl_ul, "Upper Left"),
            (self.cal_mgr.upper_right, self.lbl_ur, "Upper Right"),
            (self.cal_mgr.lower_left, self.lbl_ll, "Lower Left"),
            (self.cal_mgr.lower_right, self.lbl_lr, "Lower Right"),
        ]
        for pos, label, name in mapping:
            if pos:
                label.config(text=f"{name}: {pos[0]:.1f}, {pos[1]:.1f}, {pos[2]:.1f}")
            else:
                label.config(text=f"{name}: Not Set")

    def _update_calib_map(self, silent=False):
        self.cal_mgr.width = self.var_w.get()
        self.cal_mgr.depth = self.var_d.get()
        self.cal_mgr.pattern = self.var_pattern.get()
        self.calib_grid.rebuild(self.cal_mgr.depth, self.cal_mgr.width)
        self.exp_grid.rebuild(self.cal_mgr.depth, self.cal_mgr.width)
        self._update_sel_count()
        if not silent:
            messagebox.showinfo("Updated", "Well map updated.")

    def _load_calib_dialog(self):
        cal_dir = get_config().get("paths.calibration_dir", "config/calibrations")
        path = filedialog.askopenfilename(
            title="Load Calibration",
            initialdir=cal_dir,
            filetypes=[("Calibration JSON", "*.json"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            self.cal_mgr.load(path)
            self.var_w.set(self.cal_mgr.width)
            self.var_d.set(self.cal_mgr.depth)
            self.var_pattern.set(self.cal_mgr.pattern)
            self.var_calib_name.set(os.path.splitext(os.path.basename(path))[0])
            self._update_corner_labels()
            self._update_calib_map(silent=True)
            self._refresh_cals()
            self.var_cal_file.set(os.path.basename(path))
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load calibration: {e}")

    def _save_calib(self):
        self.cal_mgr.width = self.var_w.get()
        self.cal_mgr.depth = self.var_d.get()
        self.cal_mgr.pattern = self.var_pattern.get()
        try:
            name = self.var_calib_name.get().strip() or "calibration"
            name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in name)
            self.cal_mgr.save(name)
            messagebox.showinfo("Success", f"Saved as {name}.json")
            self._refresh_cals()
            self.var_cal_file.set(f"{name}.json")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _quick_capture_image(self):
        fmt = self.var_quick_fmt.get().lower().lstrip(".") or "jpg"
        out_dir = os.path.join(get_config().get("paths.output_dir", "outputs"), "quick_capture")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"quick_{datetime_name()}.{fmt}")
        frame = self.camera.get_frame()
        if frame is None:
            messagebox.showerror("Error", "Could not read a camera frame.")
            return
        if self.camera.backend == "picamera2":
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        cv2.imwrite(path, frame)
        self.lbl_quick_status.config(text=os.path.basename(path))

    def _quick_capture_video(self):
        if self.exp_runner and self.exp_runner.running:
            messagebox.showwarning("Busy", "Wait for the experiment to finish before quick recording.")
            return
        out_dir = os.path.join(get_config().get("paths.output_dir", "outputs"), "quick_capture")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"quick_{datetime_name()}.avi")
        duration = max(0.1, float(self.var_quick_video_s.get()))
        fps = float(get_config().get("hardware.camera.default_fps", 30.0))
        self.lbl_quick_status.config(text="Recording...")

        def task():
            try:
                runner = ExperimentRunner(self.motion, self.camera)
                runner.running = True
                runner._write_video(path, duration, fps)
                runner.running = False
                self.root.after(0, lambda: self.lbl_quick_status.config(text=os.path.basename(path)))
            except Exception as e:
                self.root.after(0, messagebox.showerror, "Error", str(e))
                self.root.after(0, lambda: self.lbl_quick_status.config(text="Ready"))

        threading.Thread(target=task, daemon=True).start()
            
    def _update_exp_status(self, msg):
        self.lbl_exp_status.config(text=f"Status: {msg}")
        self.root.update_idletasks()

    def _preset_dir(self):
        path = os.path.join(get_config().get("paths.config_dir", "config"), "experiment_presets")
        os.makedirs(path, exist_ok=True)
        return path

    def _preset_data(self):
        return {
            "experiment_name": self.var_exp_name.get(),
            "mode": self.var_exp_mode.get(),
            "delay": self.var_delay.get(),
            "image_format": self.var_image_fmt.get(),
            "pre_duration": self.var_pre_duration.get(),
            "use_laser": self.var_use_laser.get(),
            "laser_on": self.var_laser_on.get(),
            "post_duration": self.var_post_duration.get(),
            "calibration_file": self.var_cal_file.get(),
        }

    def _apply_preset_data(self, data):
        self.var_exp_name.set(data.get("experiment_name", self.var_exp_name.get()))
        self.var_exp_mode.set(data.get("mode", self.var_exp_mode.get()))
        self.var_delay.set(float(data.get("delay", self.var_delay.get())))
        self.var_image_fmt.set(data.get("image_format", self.var_image_fmt.get()))
        self.var_pre_duration.set(float(data.get("pre_duration", self.var_pre_duration.get())))
        self.var_use_laser.set(bool(data.get("use_laser", False)))
        self.var_laser_on.set(float(data.get("laser_on", self.var_laser_on.get())))
        self.var_post_duration.set(float(data.get("post_duration", self.var_post_duration.get())))
        self._on_exp_mode_change()
        cal_file = data.get("calibration_file")
        if cal_file:
            self.var_cal_file.set(cal_file)
            self._on_cal_file_selected()

    def _refresh_presets(self):
        if not hasattr(self, "cb_preset"):
            return
        files = [os.path.splitext(os.path.basename(f))[0] for f in glob.glob(os.path.join(self._preset_dir(), "*.json"))]
        files.sort()
        self.cb_preset["values"] = files
        if files and self.var_preset_name.get() not in files:
            self.var_preset_name.set(files[0])

    def _save_preset(self):
        name = self.var_preset_name.get().strip() or "default"
        name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in name)
        path = os.path.join(self._preset_dir(), f"{name}.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._preset_data(), f, indent=2)
            self.var_preset_name.set(name)
            self._refresh_presets()
            messagebox.showinfo("Preset Saved", f"Saved {name}.json")
        except Exception as e:
            messagebox.showerror("Preset Error", str(e))

    def _load_preset(self):
        name = self.var_preset_name.get().strip()
        if not name:
            messagebox.showerror("Preset Error", "Choose a preset to load.")
            return
        path = os.path.join(self._preset_dir(), f"{name}.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                self._apply_preset_data(json.load(f))
        except Exception as e:
            messagebox.showerror("Preset Error", str(e))

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
            mode_map = {
                "Image": "image",
                "Raw .npy": "raw",
                "Video": "video",
            }
            self.exp_runner.run(
                self.var_exp_name.get(),
                filtered_pos,
                filtered_labels,
                self.var_delay.get(),
                callback=lambda msg: self.root.after(0, self._update_exp_status, msg),
                mode=mode_map.get(self.var_exp_mode.get(), "image"),
                image_format=self.var_image_fmt.get(),
                use_laser=self.var_use_laser.get(),
                pre_duration=self.var_pre_duration.get(),
                laser_on_duration=self.var_laser_on.get(),
                post_duration=self.var_post_duration.get(),
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
            elif self.exp_runner.last_written_video_path:
                self._display_placeholder(target_label, "Video capture running")
            else:
                self._display_placeholder(target_label, "Experiment running")
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
