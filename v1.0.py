import tkinter as tk
from tkinter import ttk, filedialog
from PIL import Image, ImageTk
import os
import glob
import re
import threading
import psutil


class ImageViewer:
    def __init__(self, root):
        self.root = root
        self.root.title("图片查看器")
        self.image_paths = []
        self.current_index = 0
        self.auto_press = False
        self.resize_timer = None
        self.is_playing = False  # 新增播放状态
        self.playback_id = None  # 新增播放定时器ID

        # 内存管理相关
        self.cache_size_limit = 0
        self.current_cache_size = 0
        self.image_cache = {}
        self.lru_list = []

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
        self.root.bind('<space>', self.toggle_playback)  # 新增空格键控制

        # 创建菜单
        self.create_menu()
        self.update_memory_limit()

    def update_memory_limit(self):
        """更新内存缓存限制"""
        virtual_memory = psutil.virtual_memory()
        self.cache_size_limit = int(virtual_memory.available * 0.4)

    def create_menu(self):
        menubar = tk.Menu(self.root)

        # 文件菜单
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="打开", command=self.open_image)

        # 新增播放菜单
        play_menu = tk.Menu(menubar, tearoff=0)
        play_menu.add_command(label="播放/暂停", command=self.toggle_playback)
        play_menu.add_command(label="停止", command=self.stop_playback)

        menubar.add_cascade(label="文件", menu=file_menu)
        menubar.add_cascade(label="播放控制", menu=play_menu)  # 新增菜单项
        self.root.config(menu=menubar)

    def toggle_playback(self, event=None):
        """切换播放/暂停状态"""
        if not self.image_paths:
            return

        self.is_playing = not self.is_playing
        if self.is_playing:
            self.start_playback()
        else:
            self.pause_playback()

    def start_playback(self):
        """开始自动播放"""
        self.root.title("图片查看器 - 播放中...")
        self.disable_navigation()
        self.auto_advance()

    def pause_playback(self):
        """暂停播放"""
        self.is_playing = False
        if self.playback_id:
            self.root.after_cancel(self.playback_id)
            self.playback_id = None
        self.root.title(f"图片查看器 - {os.path.basename(self.image_paths[self.current_index])}")
        self.enable_navigation()

    def stop_playback(self):
        """停止并重置播放"""
        self.pause_playback()
        self.current_index = 0
        self.show_current_image()

    def auto_advance(self):
        """自动切换到下一张"""
        if self.is_playing and self.current_index < len(self.image_paths) - 1:
            self.navigate("next")
            #间隔以毫秒为单位
            self.playback_id = self.root.after(30, self.auto_advance)
        else:
            self.stop_playback()

    def disable_navigation(self):
        """禁用方向键控制"""
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
        """加载目录中的图片路径"""
        # 释放旧内存
        self.release_all_images()

        self.image_paths = []

        extensions = ['jpg', 'jpeg', 'png', 'bmp', 'gif', 'webp', 'tiff']
        pattern = os.path.join(directory, '*')

        for file_path in glob.glob(pattern, recursive=False):
            ext = os.path.splitext(file_path)[1][1:].lower()
            if ext in extensions:
                self.image_paths.append(file_path)

        # 使用自然排序
        self.image_paths.sort(key=self.natural_sort_key)

        # 根据图片数量决定加载方式
        if len(self.image_paths) > 30:
            self.show_loading_dialog()
            threading.Thread(target=self.async_load_images, daemon=True).start()
        else:
            self.sync_load_images()

    def release_all_images(self):
        """释放所有已加载的图片资源"""
        # 关闭所有图片对象
        for path in list(self.image_cache.keys()):
            img, size = self.image_cache.pop(path)
            img.close()

        # 重置缓存数据
        self.image_cache.clear()
        self.lru_list.clear()
        self.current_cache_size = 0

        # 清空画布
        self.canvas.delete("all")
        self.canvas.image = None

    @staticmethod
    def natural_sort_key(s):
        """自然排序关键字生成"""
        return [
            int(text) if text.isdigit() else text.lower()
            for text in re.split(r'(\d+)', s)
        ]

    def sync_load_images(self):
        """同步加载图片"""
        for path in self.image_paths:
            self.load_image_to_cache(path)
        self.enable_navigation()

    def async_load_images(self):
        """异步加载图片"""
        total = len(self.image_paths)
        loaded = 0

        for path in self.image_paths:
            if self.load_image_to_cache(path):
                loaded += 1
                self.root.after(0, self.update_progress, loaded, total)

        self.root.after(0, self.close_loading_dialog)
        self.root.after(0, self.enable_navigation)

    def load_image_to_cache(self, path):
        """加载单个图片到缓存"""
        try:
            img = Image.open(path)
            img_size = self.calculate_image_size(img)

            # 检查内存限制
            while self.current_cache_size + img_size > self.cache_size_limit and self.lru_list:
                self.remove_oldest_image()

            if self.current_cache_size + img_size > self.cache_size_limit:
                return False

            self.image_cache[path] = (img, img_size)
            self.current_cache_size += img_size
            self.lru_list.append(path)
            return True
        except Exception as e:
            print(f"无法加载图片 {path}: {e}")
            return False

    def calculate_image_size(self, img):
        """计算图片内存占用"""
        channels = 4 if img.mode == 'RGBA' else 3
        return img.width * img.height * channels

    def remove_oldest_image(self):
        """移除并关闭最久未使用的图片"""
        if not self.lru_list:
            return

        oldest_path = self.lru_list.pop(0)
        if oldest_path in self.image_cache:
            img, size = self.image_cache.pop(oldest_path)
            img.close()
            self.current_cache_size -= size

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

    def show_current_image(self):
        """显示当前图片"""
        if not self.image_paths or self.current_index >= len(self.image_paths):
            return

        current_path = self.image_paths[self.current_index]

        # 确保图片已加载
        if current_path not in self.image_cache:
            self.load_image_to_cache(current_path)

        # 更新窗口标题
        self.root.title(f"图片查看器 - {os.path.basename(current_path)}")

        # 更新LRU列表
        if current_path in self.lru_list:
            self.lru_list.remove(current_path)
        self.lru_list.append(current_path)

        # 显示图片
        img = self.image_cache.get(current_path, (None, None))[0]
        if not img:
            return

        # 缩放逻辑
        window_width = self.canvas.winfo_width()
        window_height = self.canvas.winfo_height()
        img_width, img_height = img.size

        scale_ratio = min(
            window_width / img_width,
            window_height / img_height,
            1
        )
        new_size = (int(img_width * scale_ratio), int(img_height * scale_ratio))
        resized_img = img.resize(new_size, Image.Resampling.LANCZOS)

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
        if self.resize_timer:
            self.root.after_cancel(self.resize_timer)
        self.resize_timer = self.root.after(200, self.show_current_image)

    def navigate(self, direction):
        max_index = len(self.image_paths) - 1
        if direction == "prev":
            self.current_index = max(0, self.current_index - 1)
        else:
            self.current_index = min(max_index, self.current_index + 1)
        self.show_current_image()

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
    ImageViewer(root)
    root.mainloop()