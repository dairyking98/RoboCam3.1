import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import cv2
from PIL import Image, ImageTk
import os
import glob

from robocam.config import get_config
from robocam.motion import MotionController
from robocam.camera import Camera
from robocam.calibration import CalibrationManager
from robocam.experiment import ExperimentRunner

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("RoboCam 3.1")
        self.root.geometry("1000x800")
        
        self.simulate = False
        self.config = get_config()
        self.motion = None
        self.camera = Camera(simulate=self.simulate)
        self.cal_mgr = CalibrationManager()
        self.exp_runner = None
        
        self._build_gui()
        self._connect_motion()
        self.update_camera_preview()
        
    def _connect_motion(self):
        try:
            self.motion = MotionController(simulate=self.simulate)
            self.exp_runner = ExperimentRunner(self.motion, self.camera)
            self.lbl_status.config(text=f"Connected: {self.config.get('hardware.motion_backend', 'marlin').upper()}", foreground="green")
            self._update_pos_label()
        except Exception as e:
            self.lbl_status.config(text=f"Connection Error: {e}", foreground="red")
            self.motion = None
        
    def _build_gui(self):
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        self.tab_motion = ttk.Frame(notebook)
        self.tab_calib = ttk.Frame(notebook)
        self.tab_exp = ttk.Frame(notebook)
        
        notebook.add(self.tab_motion, text="Motion & Camera")
        notebook.add(self.tab_calib, text="Calibration")
        notebook.add(self.tab_exp, text="Experiment")
        
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
        
        # Left side: Camera Preview
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
        frame = ttk.Frame(self.tab_calib)
        frame.pack(padx=20, pady=20, fill=tk.BOTH)
        
        ttk.Label(frame, text="1. Move to corner and click 'Set'").grid(row=0, column=0, columnspan=3, pady=10, sticky=tk.W)
        
        self.lbl_ul = ttk.Label(frame, text="Upper Left: Not Set")
        self.lbl_ul.grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Button(frame, text="Set UL", command=lambda: self._set_corner('ul')).grid(row=1, column=1, padx=10)
        
        self.lbl_ur = ttk.Label(frame, text="Upper Right: Not Set")
        self.lbl_ur.grid(row=2, column=0, sticky=tk.W, pady=5)
        ttk.Button(frame, text="Set UR", command=lambda: self._set_corner('ur')).grid(row=2, column=1, padx=10)
        
        self.lbl_ll = ttk.Label(frame, text="Lower Left: Not Set")
        self.lbl_ll.grid(row=3, column=0, sticky=tk.W, pady=5)
        ttk.Button(frame, text="Set LL", command=lambda: self._set_corner('ll')).grid(row=3, column=1, padx=10)
        
        self.lbl_lr = ttk.Label(frame, text="Lower Right: Not Set")
        self.lbl_lr.grid(row=4, column=0, sticky=tk.W, pady=5)
        ttk.Button(frame, text="Set LR", command=lambda: self._set_corner('lr')).grid(row=4, column=1, padx=10)
        
        ttk.Label(frame, text="Grid Size:").grid(row=5, column=0, sticky=tk.W, pady=20)
        size_frame = ttk.Frame(frame)
        size_frame.grid(row=5, column=1, sticky=tk.W)
        self.var_w = tk.IntVar(value=12)
        self.var_d = tk.IntVar(value=8)
        ttk.Entry(size_frame, textvariable=self.var_w, width=5).pack(side=tk.LEFT)
        ttk.Label(size_frame, text="x").pack(side=tk.LEFT)
        ttk.Entry(size_frame, textvariable=self.var_d, width=5).pack(side=tk.LEFT)
        
        ttk.Button(frame, text="Save Calibration", command=self._save_calib).grid(row=6, column=0, columnspan=2, pady=20)
        
    def _build_exp_tab(self):
        frame = ttk.Frame(self.tab_exp)
        frame.pack(padx=20, pady=20, fill=tk.BOTH)
        
        ttk.Label(frame, text="Experiment Name:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.var_exp_name = tk.StringVar(value="my_experiment")
        ttk.Entry(frame, textvariable=self.var_exp_name).grid(row=0, column=1, sticky=tk.W, pady=5)
        
        ttk.Label(frame, text="Calibration File:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.var_cal_file = tk.StringVar()
        cal_frame = ttk.Frame(frame)
        cal_frame.grid(row=1, column=1, sticky=tk.W, pady=5)
        self.cb_cal = ttk.Combobox(cal_frame, textvariable=self.var_cal_file, state="readonly")
        self.cb_cal.pack(side=tk.LEFT)
        ttk.Button(cal_frame, text="Refresh", command=self._refresh_cals).pack(side=tk.LEFT, padx=5)
        
        ttk.Label(frame, text="Delay per well (s):").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.var_delay = tk.DoubleVar(value=1.0)
        ttk.Entry(frame, textvariable=self.var_delay, width=5).grid(row=2, column=1, sticky=tk.W, pady=5)
        
        ctrl_frame = ttk.Frame(frame)
        ctrl_frame.grid(row=3, column=0, columnspan=2, pady=20)
        self.btn_start = ttk.Button(ctrl_frame, text="Start Experiment", command=self._start_exp)
        self.btn_start.pack(side=tk.LEFT, padx=5)
        self.btn_stop = ttk.Button(ctrl_frame, text="Stop", command=self._stop_exp, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=5)
        
        self._refresh_cals()
        
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
        try:
            name = "calib_" + str(int(time.time()))
            self.cal_mgr.save(name)
            messagebox.showinfo("Success", f"Saved as {name}.json")
            self._refresh_cals()
        except Exception as e:
            messagebox.showerror("Error", str(e))
            
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
            
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        
        def task():
            self.exp_runner.run(self.var_exp_name.get(), positions, labels, self.var_delay.get())
            self.root.after(0, self._exp_done)
            
        threading.Thread(target=task, daemon=True).start()
        
    def _stop_exp(self):
        self.exp_runner.stop()
        
    def _exp_done(self):
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        messagebox.showinfo("Done", "Experiment completed or stopped.")
        
    def update_camera_preview(self):
        if self.camera.running:
            frame = self.camera.get_frame()
            if frame is not None:
                if self.camera.backend == "picamera2":
                    pass # already rgb
                else:
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    
                # Add crosshair
                h, w = frame.shape[:2]
                cv2.line(frame, (w//2, 0), (w//2, h), (0, 255, 0), 1)
                cv2.line(frame, (0, h//2), (w, h//2), (0, 255, 0), 1)
                
                # Resize for display
                frame = cv2.resize(frame, (640, 480))
                
                img = Image.fromarray(frame)
                imgtk = ImageTk.PhotoImage(image=img)
                self.cam_label.imgtk = imgtk
                self.cam_label.configure(image=imgtk)
                
        self.root.after(33, self.update_camera_preview)
        
    def on_close(self):
        self.camera.stop()
        self.exp_runner.stop()
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
