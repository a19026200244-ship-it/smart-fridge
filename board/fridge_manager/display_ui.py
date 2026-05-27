# LCD屏幕显示模块 - 通过framebuffer绘制UI
import os
import struct
import time
from PIL import Image, ImageDraw, ImageFont


class DisplayUI:
    def __init__(self, width=480, height=480, fb_device="/dev/fb0"):
        self.width = width
        self.height = height
        self.fb_device = fb_device
        self.fb_fd = None
        self.fb_data = None
        self._init_fb()

    def _init_fb(self):
        try:
            self.fb_fd = os.open(self.fb_device, os.O_RDWR)
            print(f"[Display] framebuffer初始化成功 {self.width}x{self.height}")
        except Exception as e:
            print(f"[Display] 无法初始化framebuffer: {e}")
            self.fb_fd = None

    def show(self, image):
        """显示PIL Image到LCD屏幕 (自动缩放至480x480)"""
        if image.size != (self.width, self.height):
            image = image.resize((self.width, self.height), Image.LANCZOS)
        # 转为RGBA -> BGRA (framebuffer格式)
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        r, g, b, a = image.split()
        img_bgra = Image.merge("RGBA", (b, g, r, a))

        if self.fb_fd is not None:
            os.lseek(self.fb_fd, 0, os.SEEK_SET)
            os.write(self.fb_fd, img_bgra.tobytes())

    def draw_inventory_screen(self, inventory, events, door_state, light_state):
        """绘制主界面: 食材库存 + 事件 + 状态"""
        img = Image.new("RGBA", (self.width, self.height), (30, 30, 40, 255))
        draw = ImageDraw.Draw(img)

        # 加载字体 (使用默认字体)
        try:
            font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
            font_body = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        except Exception:
            font_title = ImageFont.load_default()
            font_body = ImageFont.load_default()
            font_small = ImageFont.load_default()

        y = 8

        # === 标题栏 ===
        draw.rectangle([(0, 0), (self.width, 40)], fill=(20, 120, 80, 255))
        draw.text((self.width // 2 - 80, 8), "冰箱食材管理", fill=(255, 255, 255, 255), font=font_title)
        y = 45

        # === 硬件状态 ===
        door_text = "门: 关" if door_state == "closed" else "门: 开"
        door_color = (100, 200, 100, 255) if door_state == "closed" else (255, 150, 50, 255)
        light_text = "灯: 开" if light_state == "on" else "灯: 关"
        light_color = (255, 200, 50, 255) if light_state == "on" else (150, 150, 150, 255)
        status_text = f"  {door_text}    {light_text}  "
        draw.text((10, y), status_text, fill=(200, 200, 200, 255), font=font_small)
        y += 22

        # === 分隔线 ===
        draw.line([(10, y), (self.width - 10, y)], fill=(80, 80, 100, 255), width=1)
        y += 6

        # === 库存列表 ===
        draw.text((10, y), "食材库存:", fill=(100, 200, 150, 255), font=font_body)
        y += 24

        if not inventory:
            draw.text((20, y), "(暂无食材)", fill=(120, 120, 130, 255), font=font_small)
            y += 22
        else:
            for item in inventory[:12]:  # 最多显示12种
                name = item["name"]
                count = item["count"]
                text = f"  {name}  x{count}"
                draw.text((10, y), text, fill=(220, 220, 230, 255), font=font_small)
                y += 20
                if y > self.height - 100:
                    break

        # === 分隔线 ===
        draw.line([(10, y + 2), (self.width - 10, y + 2)], fill=(80, 80, 100, 255), width=1)
        y += 8

        # === 最近事件 ===
        draw.text((10, y), "最近事件:", fill=(100, 200, 150, 255), font=font_body)
        y += 24

        if not events:
            draw.text((20, y), "(暂无事件)", fill=(120, 120, 130, 255), font=font_small)
        else:
            for evt in events[-6:]:  # 最近6条
                action_icon = "↓放入" if evt["action"] == "put_in" else "↑取出"
                color = (100, 200, 100, 255) if evt["action"] == "put_in" else (200, 130, 100, 255)
                text = f"  {action_icon} {evt['food_name']} x{evt['count']}"
                draw.text((10, y), text, fill=color, font=font_small)
                y += 18

        return img

    def close(self):
        if self.fb_data:
            self.fb_data.close()
        if self.fb_fd:
            os.close(self.fb_fd)
