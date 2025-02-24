import glob
import concurrent.futures
import os
import sys
import re
import threading
import tkinter as tk
from collections import OrderedDict
from tkinter import filedialog, ttk, messagebox
import psutil
from PIL import Image, ImageTk
import pygame

class ImageViewer:
    def __init__(self, root, initial_image=None):
        self.root = root
        self.root.title("图片查看器")

        # Create the canvas first
        self.canvas = tk.Canvas(root, bg='#333333')
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # Bind events to the canvas
        self.dragging = False
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.canvas.bind('<ButtonPress-1>', self.on_drag_start)
        self.canvas.bind('<B1-Motion>', self.on_drag)
        self.canvas.bind('<ButtonRelease-1>', self.on_drag_end)

        # Viewport settings
        self.viewport_x = 0
        self.viewport_y = 0
        self.viewport_width = 0
        self.viewport_height = 0

        # Other initialization
        self.image_paths = []
        self.current_index = 0
        self.auto_press = False
        self.resize_timer = None
        self.is_playing = False
        self.playback_id = None
        self.loading_active = False
        self.zoom_factor = 1.0
        self.last_directory = None  # 新增：记录上一次加载的目录

        # Memory management
        self.cache_size_limit = 0
        self.current_cache_size = 0
        self.image_cache = {}
        self.lru_list = OrderedDict()

        # Navigation speed control
        self.navigate_delay = 200
        self.speed_boost = 0.90
        self.min_delay = 30
        self.max_delay = 500
        self.repeat_id = None

        # Bind other events
        self.root.bind('<Configure>', self.on_resize)
        self.root.bind('<Left>', lambda e: "break")
        self.root.bind('<Right>', lambda e: "break")
        self.root.bind('<space>', self.toggle_playback)
        self.canvas.bind('<MouseWheel>', self.on_mousewheel)

        # Create menu
        self.create_menu()
        self.update_memory_limit()

        # Load initial image if provided
        if initial_image:
            self.load_initial_image(initial_image)

    def create_menu(self):
        menubar = tk.Menu(self.root)

        # File menu
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="打开", command=self.open_image)

        # Playback menu
        play_menu = tk.Menu(menubar, tearoff=0)
        play_menu.add_command(label="播放/暂停", command=self.toggle_playback)
        play_menu.add_command(label="停止", command=self.stop_playback)

        # Image menu (整合翻转选项)
        image_menu = tk.Menu(menubar, tearoff=0)
        image_menu.add_command(label="图片详细信息", command=self.show_image_info)

        # Rotation sub-menu
        rotate_menu = tk.Menu(image_menu, tearoff=0)
        rotate_menu.add_command(label="逆时针旋转90°", command=self.rotate_ccw_90)
        rotate_menu.add_command(label="逆时针旋转180°", command=self.rotate_ccw_180)
        rotate_menu.add_command(label="顺时针旋转90°", command=self.rotate_cw_90)
        rotate_menu.add_command(label="顺时针旋转180°", command=self.rotate_cw_180)
        image_menu.add_cascade(label="旋转", menu=rotate_menu)

        # Flip options (直接添加到 image_menu)
        image_menu.add_command(label="水平翻转", command=self.flip_horizontal)
        image_menu.add_command(label="垂直翻转", command=self.flip_vertical)
        image_menu.add_command(label="自定义旋转", command=self.custom_rotate)

        # Add menus to menubar
        menubar.add_cascade(label="文件", menu=file_menu)
        menubar.add_cascade(label="播放控制", menu=play_menu)
        menubar.add_cascade(label="图片", menu=image_menu)  # 移除翻转菜单的独立项
        self.root.config(menu=menubar)

    def flip_horizontal(self):
        if not self.image_paths or self.is_playing:
            return
        current_path = self.image_paths[self.current_index]
        img_data = self.image_cache.get(current_path)
        if not img_data:
            return
        img, size = img_data
        flipped_img = img.transpose(Image.FLIP_LEFT_RIGHT)
        self.image_cache[current_path] = (flipped_img, size)
        img_width = flipped_img.width
        self.viewport_x = img_width - (self.viewport_x + self.viewport_width)
        self.fast_redraw()

    def flip_vertical(self):
        if not self.image_paths or self.is_playing:
            return
        current_path = self.image_paths[self.current_index]
        img_data = self.image_cache.get(current_path)
        if not img_data:
            return
        img, size = img_data
        flipped_img = img.transpose(Image.FLIP_TOP_BOTTOM)
        self.image_cache[current_path] = (flipped_img, size)
        img_height = flipped_img.height
        self.viewport_y = img_height - (self.viewport_y + self.viewport_height)
        self.fast_redraw()

    def custom_rotate(self):
        if not self.image_paths or self.is_playing:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("自定义旋转")
        dialog.transient(self.root)
        dialog.grab_set()
        tk.Label(dialog, text="请输入旋转角度 (°):").pack(pady=5)
        angle_entry = tk.Entry(dialog)
        angle_entry.pack(pady=5)
        angle_entry.focus_set()

        def on_submit():
            try:
                angle = float(angle_entry.get())
                self.rotate_image(angle)
                dialog.destroy()
            except ValueError:
                messagebox.showerror("错误", "请输入有效的角度（例如 180 或 -36）")

        tk.Button(dialog, text="确认", command=on_submit).pack(pady=5)
        dialog.bind('<Return>', lambda e: on_submit())

    def rotate_image(self, angle):
        current_path = self.image_paths[self.current_index]
        img_data = self.image_cache.get(current_path)
        if not img_data:
            return
        img, size = img_data
        rotated_img = img.rotate(angle, expand=True, resample=Image.BICUBIC)
        self.image_cache[current_path] = (rotated_img, size)
        self.viewport_x = 0
        self.viewport_y = 0
        self.viewport_width = rotated_img.width
        self.viewport_height = rotated_img.height
        self.fast_redraw()

    def animate_rotate(self, target_angle):
        """执行非线性旋转动画（优化版，使用多线程预处理帧）"""
        if not self.image_paths or self.is_playing:
            return
        current_path = self.image_paths[self.current_index]
        img_data = self.image_cache.get(current_path)
        if not img_data:
            return
        img, size = img_data

        # 动画参数
        steps = 10  # 帧数
        duration = 500  # 总时长（毫秒）
        step_time = duration // steps  # 每帧时间（50ms）

        # 设置标题为“正在处理”
        self.root.title(f"正在处理[{target_angle}°]中")

        def compute_frame(step):
            """计算单帧的旋转图像"""
            progress = self.ease_in_out(step, steps)
            current_angle = target_angle * progress
            return img.rotate(current_angle, expand=True, resample=Image.BICUBIC)

        def precompute_frames(img, target_angle, steps, callback):
            """使用多线程预计算旋转帧"""
            frame_cache = [None] * (steps + 1)  # 预分配列表，确保顺序
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(steps + 1, os.cpu_count() or 4)) as executor:
                # 提交所有帧计算任务
                futures = {executor.submit(compute_frame, step): step for step in range(steps + 1)}
                # 等待所有任务完成并按顺序填充结果
                for future in concurrent.futures.as_completed(futures):
                    step = futures[future]
                    frame_cache[step] = future.result()
            callback(frame_cache)

        def on_frames_ready(frame_cache):
            """线程回调，接收预计算的帧并启动动画"""

            def update_frame(step=0):
                if step > steps:
                    # 动画结束，恢复标题并更新缓存
                    self.image_cache[current_path] = (frame_cache[-1], size)
                    self.viewport_x = 0
                    self.viewport_y = 0
                    self.viewport_width = frame_cache[-1].width
                    self.viewport_height = frame_cache[-1].height
                    self.fast_redraw()
                    self.root.title(f"图片查看器 - {os.path.basename(current_path)}")
                    return
                # 使用预计算的帧
                rotated_img = frame_cache[step]
                self.image_cache[current_path] = (rotated_img, size)
                self.viewport_x = 0
                self.viewport_y = 0
                self.viewport_width = rotated_img.width
                self.viewport_height = rotated_img.height
                self.fast_redraw()
                self.root.after(step_time, update_frame, step + 1)

            update_frame(0)

        # 在线程中预计算帧，完成后调用 on_frames_ready
        threading.Thread(target=precompute_frames, args=(img, target_angle, steps, on_frames_ready),
                         daemon=True).start()

    def ease_in_out(self, step, total_steps):
        """非线性缓动函数（二次缓动）"""
        t = step / total_steps  # 归一化进度
        return t * t / (2.0 * (t * t - t) + 1.0)  # 缓入缓出曲线

    def rotate_ccw_90(self):
        """逆时针旋转90°，带非线性动画"""
        if not self.image_paths or self.is_playing:
            return
        self.animate_rotate(90)

    def rotate_ccw_180(self):
        """逆时针旋转180°，带非线性动画"""
        if not self.image_paths or self.is_playing:
            return
        self.animate_rotate(180)

    def rotate_cw_90(self):
        """顺时针旋转90°，带非线性动画"""
        if not self.image_paths or self.is_playing:
            return
        self.animate_rotate(-90)

    def rotate_cw_180(self):
        """顺时针旋转180°，带非线性动画"""
        if not self.image_paths or self.is_playing:
            return
        self.animate_rotate(-180)

    def on_mousewheel(self, event):
        if not self.image_paths or self.is_playing:
            return
        mouse_x = event.x
        mouse_y = event.y
        img_x, img_y = self.canvas_to_image_coords(mouse_x, mouse_y)
        scale = 1.1 if event.delta > 0 else 1 / 1.1
        self.zoom_at_point(img_x, img_y, scale)
        self.fast_redraw()
        if self.resize_timer:
            self.root.after_cancel(self.resize_timer)
        self.resize_timer = self.root.after(200, self.high_quality_redraw)

    def canvas_to_image_coords(self, canvas_x, canvas_y):
        window_width = self.canvas.winfo_width()
        window_height = self.canvas.winfo_height()
        if window_width < 10 or window_height < 10:
            return 0, 0
        scale = min(window_width / self.viewport_width, window_height / self.viewport_height)
        display_width = self.viewport_width * scale
        display_height = self.viewport_height * scale
        img_left = (window_width - display_width) / 2
        img_top = (window_height - display_height) / 2
        rel_x = (canvas_x - img_left) / display_width
        rel_y = (canvas_y - img_top) / display_height
        img_x = self.viewport_x + rel_x * self.viewport_width
        img_y = self.viewport_y + rel_y * self.viewport_height
        return img_x, img_y

    def zoom_at_point(self, img_x, img_y, scale):
        current_path = self.image_paths[self.current_index]
        img_data = self.image_cache.get(current_path)
        if not img_data:
            return
        img, _ = img_data
        new_width = self.viewport_width / scale
        new_height = self.viewport_height / scale
        if new_width < 10 or new_height < 10:
            return
        if new_width > img.width:
            new_width = img.width
            new_height = img.height
            self.viewport_x = 0
            self.viewport_y = 0
        else:
            self.viewport_x = img_x - (img_x - self.viewport_x) / scale
            self.viewport_y = img_y - (img_y - self.viewport_y) / scale
            self.viewport_x = max(0, min(self.viewport_x, img.width - new_width))
            self.viewport_y = max(0, min(self.viewport_y, img.height - new_height))
        self.viewport_width = new_width
        self.viewport_height = new_height

    def show_image_info(self):
        if not self.image_paths:
            return
        current_path = self.image_paths[self.current_index]
        try:
            with Image.open(current_path) as img:
                info = {
                    "文件名": os.path.basename(current_path),
                    "路径": current_path,
                    "格式": img.format,
                    "尺寸": f"{img.width} x {img.height}",
                    "模式": img.mode,
                    "文件大小": f"{os.path.getsize(current_path)} 字节"
                }
        except Exception as e:
            info = {"错误": str(e)}
        info_dialog = tk.Toplevel(self.root)
        info_dialog.title("图片详细信息")
        for key, value in info.items():
            label = tk.Label(info_dialog, text=f"{key}: {value}")
            label.pack(anchor='w', padx=10, pady=2)
        info_dialog.transient(self.root)
        info_dialog.grab_set()

    def redraw_image(self, img, resample_method):
        window_width = self.canvas.winfo_width()
        window_height = self.canvas.winfo_height()
        if window_width < 10 or window_height < 10:
            return
        box = (int(self.viewport_x), int(self.viewport_y),
               int(self.viewport_x + self.viewport_width), int(self.viewport_y + self.viewport_height))
        cropped_img = img.crop(box)
        scale = min(window_width / self.viewport_width, window_height / self.viewport_height)
        new_size = (int(self.viewport_width * scale), int(self.viewport_height * scale))
        resized_img = cropped_img.resize(new_size, resample_method)
        tk_img = ImageTk.PhotoImage(resized_img)
        self.canvas.delete("all")
        self.canvas.create_image(window_width // 2, window_height // 2, anchor=tk.CENTER, image=tk_img)
        self.canvas.image = tk_img

    def preprocess_images(self):
        for path in self.image_paths:
            if path not in self.image_cache:
                with Image.open(path) as img:
                    # 缩小图像并转换为灰度
                    img = img.convert('RGB').thumbnail((img.width // 2, img.height // 2))
                    img = img.convert('L')
                    self.image_cache[path] = (img.copy(), len(img.tobytes()))

    def fast_redraw(self):
        if not self.image_paths:
            return
        current_path = self.image_paths[self.current_index]
        img_data = self.image_cache.get(current_path)
        if img_data:
            self.redraw_image(img_data[0], Image.Resampling.NEAREST)

    def high_quality_redraw(self):
        if not self.image_paths:
            return
        current_path = self.image_paths[self.current_index]
        img_data = self.image_cache.get(current_path)
        if img_data:
            self.redraw_image(img_data[0], Image.Resampling.LANCZOS)

    def navigate(self, direction):
        max_index = len(self.image_paths) - 1
        if direction == "prev":
            self.current_index = max(0, self.current_index - 1)
        else:
            self.current_index = min(max_index, self.current_index + 1)
        self.zoom_factor = 1.0
        self.show_current_image()

    def start_playback(self):
        self.root.title("图片查看器 - 播放中...")
        self.disable_navigation()
        self.canvas.unbind('<MouseWheel>')
        self.canvas.unbind('<ButtonPress-1>')
        self.canvas.unbind('<B1-Motion>')
        self.canvas.unbind('<ButtonRelease-1>')
        self.auto_advance()

    def pause_playback(self):
        self.is_playing = False
        if self.playback_id:
            self.root.after_cancel(self.playback_id)
            self.playback_id = None
        self.root.title(f"图片查看器 - {os.path.basename(self.image_paths[self.current_index])}")
        self.enable_navigation()
        self.canvas.bind('<MouseWheel>', self.on_mousewheel)
        self.canvas.bind('<ButtonPress-1>', self.on_drag_start)
        self.canvas.bind('<B1-Motion>', self.on_drag)
        self.canvas.bind('<ButtonRelease-1>', self.on_drag_end)

    def stop_playback(self):
        self.pause_playback()
        self.current_index = 0
        self.show_current_image()

    def auto_advance(self):
        if self.is_playing and self.current_index < len(self.image_paths) - 1:
            self.navigate("next")
            self.playback_id = self.root.after(1000, self.auto_advance)
        else:
            self.stop_playback()

    def load_initial_image(self, initial_image):
        directory = os.path.dirname(initial_image)
        self.load_directory_images(directory)
        try:
            self.current_index = self.image_paths.index(initial_image)
        except ValueError:
            self.current_index = 0
        self.show_current_image()

    def update_memory_limit(self):
        virtual_memory = psutil.virtual_memory()
        self.cache_size_limit = int(virtual_memory.available * 0.4)

    def toggle_playback(self, event=None):
        if not self.image_paths:
            return
        self.is_playing = not self.is_playing
        if self.is_playing:
            self.start_playback()
        else:
            self.pause_playback()

    def disable_navigation(self):
        self.root.unbind('<Left>')
        self.root.unbind('<Right>')

    def open_image(self):
        file_types = [
            ("图片文件", "*.jpg;*.jpeg;*.png;*.bmp;*.gif;*.webp;*.tiff"),
            ("所有文件", "*.*")
        ]
        file_path = filedialog.askopenfilename(filetypes=file_types)
        if not file_path:
            return

        # 规范化路径
        file_path = os.path.normpath(file_path)
        directory = os.path.dirname(file_path)

        # 如果目录与上一次相同，且 image_paths 不为空，跳过重新加载
        if directory == self.last_directory and self.image_paths:
            try:
                self.current_index = self.image_paths.index(file_path)
            except ValueError:
                self.current_index = 0
            self.show_current_image()
        else:
            # 目录不同，重新加载
            self.last_directory = directory  # 更新最后加载的目录
            self.load_directory_images(directory)
            try:
                self.current_index = self.image_paths.index(file_path)
            except ValueError:
                self.current_index = 0
            self.show_current_image()


    def on_drag_start(self, event):
        if not self.image_paths or self.is_playing:
            return
        self.dragging = True
        self.drag_start_x = event.x
        self.drag_start_y = event.y

    def on_drag(self, event):
        if not self.dragging:
            return
        dx = event.x - self.drag_start_x
        dy = event.y - self.drag_start_y
        img_dx, img_dy = self.canvas_delta_to_image(dx, dy)
        current_path = self.image_paths[self.current_index]
        img_data = self.image_cache.get(current_path)
        if img_data:
            img, _ = img_data
            self.viewport_x = max(0, min(self.viewport_x - img_dx, img.width - self.viewport_width))
            self.viewport_y = max(0, min(self.viewport_y - img_dy, img.height - self.viewport_height))
        self.fast_redraw()
        self.drag_start_x = event.x
        self.drag_start_y = event.y

    def on_drag_end(self, event):
        self.dragging = False

    def canvas_delta_to_image(self, dx, dy):
        window_width = self.canvas.winfo_width()
        window_height = self.canvas.winfo_height()
        if window_width < 10 or window_height < 10:
            return 0, 0
        scale = min(window_width / self.viewport_width, window_height / self.viewport_height)
        return dx / scale, dy / scale

    def load_directory_images(self, directory):
        self.loading_active = False
        self.release_all_images()  # 清空缓存和路径，确保每次加载都是最新的
        self.image_paths = []
        extensions = ['jpg', 'jpeg', 'png', 'bmp', 'gif', 'webp', 'tiff']
        pattern = os.path.join(directory, '*')

        for file_path in glob.glob(pattern, recursive=False):
            ext = os.path.splitext(file_path)[1][1:].lower()
            if ext in extensions:
                # 规范化路径，确保与 open_image 中的 file_path 一致
                normalized_path = os.path.normpath(file_path)
                self.image_paths.append(normalized_path)

        self.image_paths.sort(key=self.natural_sort_key)

        if len(self.image_paths) > 30:
            self.show_loading_dialog()
            self.loading_active = True
            threading.Thread(target=self.async_load_images, daemon=True).start()
        else:
            self.sync_load_images()

    def release_all_images(self):
        for path in list(self.image_cache.keys()):
            img, size = self.image_cache.pop(path)
            img.close()
        self.lru_list.clear()
        self.current_cache_size = 0
        self.canvas.delete("all")
        self.canvas.image = None

    @staticmethod
    def natural_sort_key(s):
        return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]

    def sync_load_images(self):
        indices = {self.current_index, self.current_index - 1, self.current_index + 1}
        for idx in indices:
            if 0 <= idx < len(self.image_paths):
                self.load_image_to_cache(self.image_paths[idx])
        self.enable_navigation()

    def async_load_images(self):
        total = len(self.image_paths)
        loaded = 0
        priority_indices = set(range(0, 3)) | set(range(len(self.image_paths) - 3, len(self.image_paths)))
        for idx in priority_indices:
            if 0 <= idx < len(self.image_paths) and self.loading_active:
                self.load_image_to_cache(self.image_paths[idx])
                loaded += 1
                self.root.after(0, self.update_progress, loaded, total)
        for idx, path in enumerate(self.image_paths):
            if idx not in priority_indices and self.loading_active:
                if self.load_image_to_cache(path):
                    loaded += 1
                    self.root.after(0, self.update_progress, loaded, total)
        self.root.after(0, self.close_loading_dialog)
        self.root.after(0, self.enable_navigation)

    def load_image_to_cache(self, path):
        if path in self.image_cache:
            return True
        try:
            with Image.open(path) as img:
                img = img.convert('RGB')
                width, height = img.size
                channels = 3
                bytes_per_pixel = 1
                img_size = width * height * channels * bytes_per_pixel
                if img_size > self.cache_size_limit * 0.5:
                    return False
                while self.current_cache_size + img_size > self.cache_size_limit and self.lru_list:
                    self.remove_oldest_image()
                if self.current_cache_size + img_size > self.cache_size_limit:
                    return False
                self.image_cache[path] = (img.copy(), img_size)
                self.lru_list[path] = True
                self.lru_list.move_to_end(path)
                self.current_cache_size += img_size
                return True
        except Exception as e:
            print(f"无法加载图片 {path}: {e}")
            return False

    def remove_oldest_image(self):
        if self.lru_list:
            oldest_path = next(iter(self.lru_list))
            if oldest_path in self.image_cache:
                img, size = self.image_cache.pop(oldest_path)
                img.close()
                del self.lru_list[oldest_path]
                self.current_cache_size -= size

    def show_current_image(self):
        if not self.image_paths or self.current_index >= len(self.image_paths):
            return
        current_path = self.image_paths[self.current_index]
        preload_indices = {self.current_index - 1, self.current_index + 1}
        for idx in preload_indices:
            if 0 <= idx < len(self.image_paths):
                threading.Thread(target=self.load_image_to_cache, args=(self.image_paths[idx],), daemon=True).start()
        if current_path not in self.image_cache:
            self.load_image_to_cache(current_path)
        self.root.title(f"图片查看器 - {os.path.basename(current_path)}")
        self.update_lru(current_path)
        img_data = self.image_cache.get(current_path)
        if not img_data:
            return
        img, _ = img_data
        self.viewport_x = 0
        self.viewport_y = 0
        self.viewport_width = img.width
        self.viewport_height = img.height
        self.fast_redraw()

    def update_lru(self, path):
        if path in self.lru_list:
            self.lru_list.move_to_end(path)

    def on_resize(self, event):
        if self.resize_timer:
            self.root.after_cancel(self.resize_timer)
        self.resize_timer = self.root.after(200, self.high_quality_redraw)
        self.fast_redraw()

    def show_loading_dialog(self):
        self.loading_dialog = tk.Toplevel(self.root)
        self.loading_dialog.title("正在加载...")
        self.progress = ttk.Progressbar(self.loading_dialog, length=300, mode='determinate')
        self.progress.pack(padx=20, pady=10)
        self.loading_label = tk.Label(self.loading_dialog, text="正在加载图片，请稍候...")
        self.loading_label.pack(pady=5)
        self.loading_dialog.transient(self.root)
        self.loading_dialog.grab_set()

    def update_progress(self, loaded, total):
        if self.loading_dialog.winfo_exists():
            self.progress['value'] = (loaded / total) * 100
            self.loading_label.config(
                text=f"已加载 {loaded}/{total} 张图片 ({self.format_memory(self.current_cache_size)} / {self.format_memory(self.cache_size_limit)})"
            )

    def close_loading_dialog(self):
        if self.loading_dialog.winfo_exists():
            self.loading_dialog.grab_release()
            self.loading_dialog.destroy()

    def enable_navigation(self):
        if not self.is_playing:
            self.root.bind('<Left>', self.on_left_press)
            self.root.bind('<KeyRelease-Left>', self.on_left_release)
            self.root.bind('<Right>', self.on_right_press)
            self.root.bind('<KeyRelease-Right>', self.on_right_release)

    def start_repeat(self, direction):
        def repeat(delay):
            if self.auto_press:
                self.navigate(direction)
                new_delay = max(self.min_delay, int(delay * self.speed_boost))
                self.repeat_id = self.root.after(new_delay, lambda: repeat(new_delay))

        self.repeat_id = self.root.after(self.navigate_delay, lambda: repeat(self.navigate_delay))

    def stop_repeat(self):
        self.auto_press = False
        if self.repeat_id:
            self.root.after_cancel(self.repeat_id)
            self.repeat_id = None

    def on_left_press(self, event):
        self.auto_press = True
        self.navigate("prev")
        self.start_repeat("prev")

    def on_left_release(self, event):
        self.stop_repeat()

    def on_right_press(self, event):
        self.auto_press = True
        self.navigate("next")
        self.start_repeat("next")

    def on_right_release(self, event):
        self.stop_repeat()

    @staticmethod
    def format_memory(size):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} GB"


if __name__ == "__main__":
    root = tk.Tk()
    root.geometry("1024x768")
    initial_image = None
    if len(sys.argv) > 1:
        initial_image = os.path.abspath(sys.argv[1])
        print("sys.argv:", sys.argv)
        print("initial_image:", initial_image)
    viewer = ImageViewer(root, initial_image)
    root.mainloop()