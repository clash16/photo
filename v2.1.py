import glob
import os
import sys
import re
import threading
import tkinter as tk
from collections import OrderedDict
from tkinter import filedialog, ttk
import psutil
from PIL import Image, ImageTk


class ImageViewer:
    def __init__(self, root, initial_image=None):
        self.root = root
        self.root.title("图片查看器")
        self.image_paths = []
        self.current_index = 0
        self.auto_press = False
        self.resize_timer = None
        self.is_playing = False
        self.playback_id = None
        self.loading_active = False  # 新增加载状态控制

        # 内存管理优化：使用OrderedDict
        self.cache_size_limit = 0
        self.current_cache_size = 0
        self.image_cache = {}
        self.lru_list = OrderedDict()

        # 导航速度控制
        self.navigate_delay = 200
        self.speed_boost = 0.90
        self.min_delay = 30
        self.max_delay = 500
        self.repeat_id = None

        # 创建界面组件
        self.canvas = tk.Canvas(root, bg='#333333')
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # 绑定事件
        self.root.bind('<Configure>', self.on_resize)
        self.root.bind('<Left>', lambda e: "break")
        self.root.bind('<Right>', lambda e: "break")
        self.root.bind('<space>', self.toggle_playback)

        # 创建菜单
        self.create_menu()
        self.update_memory_limit()

        # 如果有初始图片，则加载
        if initial_image:
            self.load_initial_image(initial_image)


    def load_initial_image(self, initial_image):
        directory = os.path.dirname(initial_image)
        self.load_directory_images(directory)

        try:
            self.current_index = self.image_paths.index(initial_image)
        except ValueError:
            self.current_index = 0

        self.show_current_image()

    def update_memory_limit(self):
        """动态更新内存限制"""
        virtual_memory = psutil.virtual_memory()
        self.cache_size_limit = int(virtual_memory.available * 0.4)

    def create_menu(self):
        menubar = tk.Menu(self.root)

        # 文件菜单
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="打开", command=self.open_image)

        # 播放菜单
        play_menu = tk.Menu(menubar, tearoff=0)
        play_menu.add_command(label="播放/暂停", command=self.toggle_playback)
        play_menu.add_command(label="停止", command=self.stop_playback)

        menubar.add_cascade(label="文件", menu=file_menu)
        menubar.add_cascade(label="播放控制", menu=play_menu)
        self.root.config(menu=menubar)

    def toggle_playback(self, event=None):
        if not self.image_paths:
            return

        self.is_playing = not self.is_playing
        if self.is_playing:
            self.start_playback()
        else:
            self.pause_playback()

    def start_playback(self):
        self.root.title("图片查看器 - 播放中...")
        self.disable_navigation()
        self.auto_advance()

    def pause_playback(self):
        self.is_playing = False
        if self.playback_id:
            self.root.after_cancel(self.playback_id)
            self.playback_id = None
        self.root.title(f"图片查看器 - {os.path.basename(self.image_paths[self.current_index])}")
        self.enable_navigation()

    def stop_playback(self):
        self.pause_playback()
        self.current_index = 0
        self.show_current_image()

    def auto_advance(self):
        if self.is_playing and self.current_index < len(self.image_paths) - 1:
            self.navigate("next")
            # 播放间隔
            self.playback_id = self.root.after(1, self.auto_advance)
        else:
            self.stop_playback()

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

        directory = os.path.dirname(file_path)
        self.load_directory_images(directory)

        try:
            self.current_index = self.image_paths.index(file_path)
        except ValueError:
            self.current_index = 0

        self.show_current_image()

    def load_directory_images(self, directory):
        """加载目录图片并优化内存管理"""
        self.loading_active = False  # 停止之前的加载
        self.release_all_images()

        self.image_paths = []
        extensions = ['jpg', 'jpeg', 'png', 'bmp', 'gif', 'webp', 'tiff']
        pattern = os.path.join(directory, '*')

        for file_path in glob.glob(pattern, recursive=False):
            ext = os.path.splitext(file_path)[1][1:].lower()
            if ext in extensions:
                self.image_paths.append(file_path)

        self.image_paths.sort(key=self.natural_sort_key)

        if len(self.image_paths) > 30:
            self.show_loading_dialog()
            self.loading_active = True
            threading.Thread(target=self.async_load_images, daemon=True).start()
        else:
            self.sync_load_images()

    def release_all_images(self):
        """优化内存释放机制"""
        for path in list(self.image_cache.keys()):
            img, size = self.image_cache.pop(path)
            img.close()
        self.lru_list.clear()
        self.current_cache_size = 0
        self.canvas.delete("all")
        self.canvas.image = None

    @staticmethod
    def natural_sort_key(s):
        return [
            int(text) if text.isdigit() else text.lower()
            for text in re.split(r'(\d+)', s)
        ]

    def sync_load_images(self):
        """同步加载当前和相邻图片"""
        indices = {self.current_index, self.current_index-1, self.current_index+1}
        for idx in indices:
            if 0 <= idx < len(self.image_paths):
                self.load_image_to_cache(self.image_paths[idx])
        self.enable_navigation()

    def async_load_images(self):
        """优化异步加载逻辑"""
        total = len(self.image_paths)
        loaded = 0

        # 优先加载前3张和后3张
        priority_indices = set(range(0, 3)) | set(range(len(self.image_paths)-3, len(self.image_paths)))
        for idx in priority_indices:
            if 0 <= idx < len(self.image_paths) and self.loading_active:
                self.load_image_to_cache(self.image_paths[idx])
                loaded += 1
                self.root.after(0, self.update_progress, loaded, total)

        # 加载剩余图片
        for idx, path in enumerate(self.image_paths):
            if idx not in priority_indices and self.loading_active:
                if self.load_image_to_cache(path):
                    loaded += 1
                    self.root.after(0, self.update_progress, loaded, total)

        self.root.after(0, self.close_loading_dialog)
        self.root.after(0, self.enable_navigation)

    def load_image_to_cache(self, path):
        """优化缓存加载策略"""
        if path in self.image_cache:
            return True

        try:
            with Image.open(path) as img:
                img = img.convert('RGB')  # 优化内存使用
                width, height = img.size
                channels = 3  # RGB 图像有三个通道
                bytes_per_pixel = 1  # 每个通道占用一个字节
                img_size = width * height * channels * bytes_per_pixel

                # 跳过过大的图片
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
        """优化缓存淘汰机制"""
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

        # 异步加载相邻图片
        preload_indices = {self.current_index-1, self.current_index+1}
        for idx in preload_indices:
            if 0 <= idx < len(self.image_paths):
                threading.Thread(
                    target=self.load_image_to_cache,
                    args=(self.image_paths[idx],),
                    daemon=True
                ).start()

        if current_path not in self.image_cache:
            self.load_image_to_cache(current_path)

        self.root.title(f"图片查看器 - {os.path.basename(current_path)}")
        self.update_lru(current_path)

        img_data = self.image_cache.get(current_path)
        if not img_data:
            return

        img, _ = img_data
        self.redraw_image(img, Image.Resampling.LANCZOS)

    def update_lru(self, path):
        """优化LRU更新"""
        if path in self.lru_list:
            self.lru_list.move_to_end(path)

    def redraw_image(self, img, resample_method):
        """优化绘制逻辑"""
        window_width = self.canvas.winfo_width()
        window_height = self.canvas.winfo_height()
        if window_width < 10 or window_height < 10:
            return

        img_width, img_height = img.size
        scale_ratio = min(window_width / img_width, window_height / img_height, 1)
        new_size = (int(img_width * scale_ratio), int(img_height * scale_ratio))

        resized_img = img.resize(new_size, resample_method)
        tk_img = ImageTk.PhotoImage(resized_img)

        self.canvas.delete("all")
        self.canvas.create_image(
            window_width // 2,
            window_height // 2,
            anchor=tk.CENTER,
            image=tk_img
        )
        self.canvas.image = tk_img

    def on_resize(self, event):
        """优化resize处理"""
        if self.resize_timer:
            self.root.after_cancel(self.resize_timer)
        self.resize_timer = self.root.after(200, self.high_quality_redraw)
        self.fast_redraw()

    def fast_redraw(self):
        """快速重绘（低质量）"""
        if not self.image_paths:
            return
        current_path = self.image_paths[self.current_index]
        img_data = self.image_cache.get(current_path)
        if img_data:
            self.redraw_image(img_data[0], Image.Resampling.BILINEAR)

    def high_quality_redraw(self):
        """高质量重绘"""
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
        self.show_current_image()

    # 其余方法保持原样，包括：
    # !show_loading_dialog, !update_progress, !close_loading_dialog,
    # !enable_navigation, start_repeat, stop_repeat, format_memory等

    def show_loading_dialog(self):
        """显示加载进度对话框"""
        self.loading_dialog = tk.Toplevel(self.root)
        self.loading_dialog.title("正在加载...")
        self.progress = ttk.Progressbar(
            self.loading_dialog,
            length=300,
            mode='determinate'
        )
        self.progress.pack(padx=20, pady=10)
        self.loading_label = tk.Label(
            self.loading_dialog,
            text="正在加载图片，请稍候..."
        )
        self.loading_label.pack(pady=5)
        self.loading_dialog.transient(self.root)
        self.loading_dialog.grab_set()

    def update_progress(self, loaded, total):
        """更新进度条"""
        if self.loading_dialog.winfo_exists():
            self.progress['value'] = (loaded / total) * 100
            self.loading_label.config(
                text=f"已加载 {loaded}/{total} 张图片 ({self.format_memory(self.current_cache_size)} / {self.format_memory(self.cache_size_limit)})"
            )
    def close_loading_dialog(self):
        """关闭加载对话框"""
        if self.loading_dialog.winfo_exists():
            self.loading_dialog.grab_release()
            self.loading_dialog.destroy()
    def enable_navigation(self):
        """启用导航功能"""
        if not self.is_playing:  # 播放时禁用手动导航
            self.root.bind('<Left>', self.on_left_press)
            self.root.bind('<KeyRelease-Left>', self.on_left_release)
            self.root.bind('<Right>', self.on_right_press)
            self.root.bind('<KeyRelease-Right>', self.on_right_release)
    def start_repeat(self, direction):
        """启动连续切换"""

        def repeat(delay):
            if self.auto_press:
                self.navigate(direction)
                # 动态调整延迟
                new_delay = max(self.min_delay, int(delay * self.speed_boost))
                self.repeat_id = self.root.after(new_delay, lambda: repeat(new_delay))

        # 初始立即执行一次，然后开始加速
        self.repeat_id = self.root.after(self.navigate_delay, lambda: repeat(self.navigate_delay))

    def stop_repeat(self):
        """停止连续切换"""
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
        """格式化内存大小显示"""
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
        # 获取第一个参数（索引1，索引0是脚本名）
        initial_image = os.path.abspath(sys.argv[1])  # 修复这里！添加 [1]
        print("sys.argv:", sys.argv)
        print("initial_image:", initial_image)

    viewer = ImageViewer(root, initial_image)
    root.mainloop()