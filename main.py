"""
PDF Reader - a fast, minimal PDF viewer for Android built with Kivy + PyMuPDF.

Performance design
------------------
* All rendering happens on ONE background worker thread (PyMuPDF documents
  are not thread-safe); the UI thread only uploads finished pixels to the GPU.
* Pages are rendered at exact screen resolution (no oversized textures).
* Small LRU texture cache + automatic prefetch of the next/previous page.
* Stale jobs (you swiped past them) are skipped before they cost any CPU.
* Double-tap zoom re-renders a crisp high-res texture instead of stretching.
* Resize / rotation is debounced.
"""

import json
import os
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

import fitz  # PyMuPDF

from kivy.animation import Animation
from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle
from kivy.graphics.texture import Texture
from kivy.lang import Builder
from kivy.metrics import dp
from kivy.properties import BooleanProperty, ObjectProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.widget import Widget
from kivy.utils import platform

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
BG_COLOR = (0.055, 0.059, 0.075, 1)
ZOOM = 2.5               # double-tap zoom factor
MAX_TEX = 4096           # max texture side (safe for virtually all GPUs)
CACHE_PAGES = 6          # fit-to-screen textures kept in memory
BAR_AUTOHIDE = 3.0       # seconds before the bars fade away

KV = """
<Chip>:
    size_hint: None, None
    height: dp(44)
    width: max(dp(44), self.texture_size[0] + dp(28))
    font_size: sp(15)
    bold: True
    color: 1, 1, 1, 1
    canvas.before:
        Color:
            rgba: (0.40, 0.49, 1, 1) if self.accent else ((1, 1, 1, 0.18) if self.state == 'down' else (1, 1, 1, 0.08))
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(22)]

<Bar>:
    padding: dp(10), dp(8)
    spacing: dp(8)
    canvas.before:
        Color:
            rgba: 0.10, 0.105, 0.13, 0.94
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(24)]

<EmptyState>:
    orientation: 'vertical'
    size_hint: None, None
    size: dp(300), dp(230)
    pos_hint: {'center_x': .5, 'center_y': .5}
    spacing: dp(14)
    Label:
        text: 'PDF Reader'
        font_size: sp(30)
        bold: True
        size_hint_y: None
        height: dp(50)
    Label:
        text: 'Fast, simple and distraction free.\\nSwipe to turn pages, double-tap to zoom.'
        font_size: sp(14)
        halign: 'center'
        color: 1, 1, 1, 0.55
    Chip:
        text: 'Open a PDF'
        accent: True
        pos_hint: {'center_x': .5}
        on_release: app.open_file()

<ReaderRoot>:
    view: view
    top_bar: top_bar
    bottom_bar: bottom_bar
    empty: empty
    title_label: title_label
    page_label: page_label
    slider: slider
    night_chip: night_chip

    PageView:
        id: view
        size_hint: 1, 1

    EmptyState:
        id: empty

    Bar:
        id: top_bar
        size_hint: .96, None
        height: dp(60)
        pos_hint: {'center_x': .5, 'top': .992}
        Label:
            id: title_label
            text: ''
            font_size: sp(15)
            bold: True
            halign: 'left'
            valign: 'middle'
            shorten: True
            shorten_from: 'right'
            text_size: self.width, None
            padding_x: dp(8)
        Chip:
            text: 'Open'
            accent: True
            pos_hint: {'center_y': .5}
            on_release: app.open_file()

    Bar:
        id: bottom_bar
        orientation: 'vertical'
        size_hint: .96, None
        height: dp(118)
        pos_hint: {'center_x': .5, 'y': .008}
        Slider:
            id: slider
            size_hint_y: None
            height: dp(34)
            min: 1
            max: 2
            step: 1
            value: 1
            cursor_size: dp(22), dp(22)
            value_track: True
            value_track_color: 0.40, 0.49, 1, 1
            value_track_width: dp(3)
        BoxLayout:
            size_hint_y: None
            height: dp(46)
            spacing: dp(8)
            Chip:
                id: night_chip
                text: 'Night'
                on_release: app.toggle_night()
            Chip:
                text: '‹'
                font_size: sp(22)
                on_release: app.prev_page()
            Label:
                id: page_label
                text: '- / -'
                font_size: sp(15)
                bold: True
            Chip:
                text: '›'
                font_size: sp(22)
                on_release: app.next_page()
"""


# --------------------------------------------------------------------------
# Widgets
# --------------------------------------------------------------------------
class Chip(ButtonBehavior, Label):
    accent = BooleanProperty(False)


class Bar(BoxLayout):
    """Floating card that ignores touches while faded out."""

    def on_touch_down(self, touch):
        return False if self.opacity < 0.5 else super().on_touch_down(touch)

    def on_touch_move(self, touch):
        return False if self.opacity < 0.5 else super().on_touch_move(touch)

    def on_touch_up(self, touch):
        return False if self.opacity < 0.5 else super().on_touch_up(touch)


class EmptyState(BoxLayout):
    pass


class PageView(Widget):
    """Draws one page texture; handles swipe, tap, double-tap zoom and pan."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.page_size = (1.0, 1.0)  # page size in PDF points
        self.zoomed = False
        self.pan = [0.0, 0.0]
        self._tap_event = None

        with self.canvas:
            Color(1, 1, 1, 1)
            self._rect = Rectangle(size=(0, 0))

        self.bind(size=self._relayout, pos=self._relayout)

    # -- geometry ---------------------------------------------------------
    @property
    def fit_scale(self):
        pw, ph = self.page_size
        if not (self.width and self.height):
            return 1.0
        return min(self.width / pw, self.height / ph)

    def set_texture(self, texture):
        self._rect.texture = texture
        self._relayout()

    def reset(self):
        self.zoomed = False
        self.pan = [0.0, 0.0]
        self._relayout()

    def _relayout(self, *_):
        if self._rect.texture is None:
            self._rect.size = (0, 0)
            return
        pw, ph = self.page_size
        scale = self.fit_scale * (ZOOM if self.zoomed else 1)
        dw, dh = pw * scale, ph * scale

        max_x = max(0.0, (dw - self.width) / 2)
        max_y = max(0.0, (dh - self.height) / 2)
        self.pan[0] = min(max(self.pan[0], -max_x), max_x)
        self.pan[1] = min(max(self.pan[1], -max_y), max_y)

        self._rect.size = (dw, dh)
        self._rect.pos = (
            self.x + (self.width - dw) / 2 + self.pan[0],
            self.y + (self.height - dh) / 2 + self.pan[1],
        )

    # -- gestures ---------------------------------------------------------
    def toggle_zoom(self, pos):
        if self.zoomed:
            self.zoomed = False
            self.pan = [0.0, 0.0]
        else:
            cx, cy = self.center
            self.zoomed = True
            # keep the tapped point under the finger
            self.pan = [(pos[0] - cx) * (1 - ZOOM), (pos[1] - cy) * (1 - ZOOM)]
        self._relayout()
        App.get_running_app().show()

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return False
        touch.grab(self)
        touch.ud["sx"], touch.ud["sy"] = touch.pos
        if self._tap_event:
            self._tap_event.cancel()
            self._tap_event = None
        if touch.is_double_tap:
            touch.ud["dbl"] = True
            self.toggle_zoom(touch.pos)
        return True

    def on_touch_move(self, touch):
        if touch.grab_current is not self:
            return False
        if self.zoomed and not touch.ud.get("dbl"):
            self.pan[0] += touch.dx
            self.pan[1] += touch.dy
            self._relayout()
        return True

    def on_touch_up(self, touch):
        if touch.grab_current is not self:
            return False
        touch.ungrab(self)
        if touch.ud.get("dbl"):
            return True

        dx = touch.x - touch.ud["sx"]
        dy = touch.y - touch.ud["sy"]
        app = App.get_running_app()

        if not self.zoomed and abs(dx) > abs(dy) * 1.3 and abs(dx) > dp(70):
            app.next_page() if dx < 0 else app.prev_page()
        elif abs(dx) < dp(12) and abs(dy) < dp(12):
            # delayed so a double-tap never toggles the bars
            self._tap_event = Clock.schedule_once(lambda dt: app.toggle_bars(), 0.28)
        return True


class ReaderRoot(FloatLayout):
    view = ObjectProperty(None)
    top_bar = ObjectProperty(None)
    bottom_bar = ObjectProperty(None)
    empty = ObjectProperty(None)
    title_label = ObjectProperty(None)
    page_label = ObjectProperty(None)
    slider = ObjectProperty(None)
    night_chip = ObjectProperty(None)


Builder.load_string(KV)


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------
class PDFReaderApp(App):
    title = "PDF Reader"

    def build(self):
        Window.clearcolor = BG_COLOR

        self.doc = None
        self.doc_name = ""
        self.page_count = 0
        self.current = 0
        self.night = False
        self.gen = 0                      # bumps whenever cached work becomes invalid
        self.sizes = {}                   # page -> (w, h) in points
        self.cache = OrderedDict()        # (page, night) -> fit texture
        self.zcache = {}                  # (page, night) -> zoom texture (max 1)
        self.pending = {}                 # (page, zoomed, night) -> gen
        self.executor = ThreadPoolExecutor(max_workers=1)
        self._syncing = False
        self._hide_event = None

        self._positions_file = os.path.join(self.user_data_dir, "positions.json")
        self._save_trigger = Clock.create_trigger(self._save_positions, 1.0)
        self._resize_trigger = Clock.create_trigger(self._on_resized, 0.25)
        self._slider_trigger = Clock.create_trigger(self._apply_slider, 0.06)

        self.reader = ReaderRoot()
        self.reader.view.bind(size=lambda *_: self._resize_trigger())
        self.reader.slider.bind(value=self._on_slider)
        self.reader.bottom_bar.opacity = 0
        self.reader.top_bar.opacity = 1

        Window.bind(on_keyboard=self._on_key)
        return self.reader

    # -- file handling ----------------------------------------------------
    def open_file(self, *_):
        if platform == "android":
            # Storage Access Framework: no storage permissions required.
            try:
                from androidstorage4kivy import Chooser

                self._chooser = Chooser(self._on_picked)
                self._chooser.choose_content("application/pdf")
            except Exception as error:
                self.show_error(f"Could not open file picker:\n\n{error}")
        else:
            self._desktop_chooser()

    def _on_picked(self, uris):
        if not uris:
            return
        threading.Thread(target=self._copy_picked, args=(uris[0],), daemon=True).start()

    def _copy_picked(self, uri):
        try:
            from androidstorage4kivy import SharedStorage

            path = SharedStorage().copy_from_shared(uri)
            Clock.schedule_once(lambda dt: self.load_pdf(path))
        except Exception as error:
            Clock.schedule_once(lambda dt: self.show_error(f"Could not read file:\n\n{error}"))

    def _desktop_chooser(self):
        chooser = FileChooserListView(
            path=os.path.expanduser("~"), filters=["*.pdf"], multiselect=False
        )
        buttons = BoxLayout(size_hint_y=None, height=dp(50))
        cancel, select = Button(text="Cancel"), Button(text="Open")
        buttons.add_widget(cancel)
        buttons.add_widget(select)
        layout = BoxLayout(orientation="vertical")
        layout.add_widget(chooser)
        layout.add_widget(buttons)
        popup = Popup(title="Choose PDF", content=layout, size_hint=(0.95, 0.9))
        cancel.bind(on_release=popup.dismiss)

        def pick(*_):
            if chooser.selection and chooser.selection[0].lower().endswith(".pdf"):
                popup.dismiss()
                self.load_pdf(chooser.selection[0])

        select.bind(on_release=pick)
        popup.open()

    def load_pdf(self, path):
        try:
            doc = fitz.open(path)
            if doc.needs_pass:
                doc.close()
                raise ValueError("This PDF is password protected.")
            if len(doc) == 0:
                raise ValueError("This PDF has no pages.")
        except Exception as error:
            self.show_error(f"Could not open PDF:\n\n{error}")
            return

        self._save_positions()
        self.gen += 1                     # invalidates all in-flight jobs
        self.doc = doc                    # old doc is closed by GC once idle
        self.doc_name = os.path.basename(path)
        self.page_count = len(doc)
        self.sizes.clear()
        self.cache.clear()
        self.zcache.clear()
        self.pending.clear()

        r = self.reader
        if r.empty.parent:
            r.remove_widget(r.empty)
        r.title_label.text = self.doc_name
        r.view.reset()
        r.view.set_texture(None)

        self._syncing = True
        r.slider.max = max(2, self.page_count)
        r.slider.disabled = self.page_count < 2
        self._syncing = False

        self.current = min(self._load_positions().get(self.doc_name, 0), self.page_count - 1)
        self._update_ui()
        self.show()
        self.show_bars(auto=True)

    # -- navigation -------------------------------------------------------
    def goto(self, page):
        if not self.doc:
            return
        page = max(0, min(page, self.page_count - 1))
        if page == self.current:
            return
        self.current = page
        self.reader.view.reset()
        self._update_ui()
        self.show()
        self._save_trigger()

    def next_page(self):
        self.goto(self.current + 1)

    def prev_page(self):
        self.goto(self.current - 1)

    def _on_slider(self, _slider, _value):
        if not self._syncing:
            self._slider_trigger()

    def _apply_slider(self, *_):
        self.goto(int(self.reader.slider.value) - 1)

    def _update_ui(self):
        r = self.reader
        r.page_label.text = f"{self.current + 1} / {self.page_count}"
        self._syncing = True
        r.slider.value = self.current + 1
        self._syncing = False

    # -- rendering pipeline ----------------------------------------------
    def show(self):
        """Display the best texture we have for the current state and
        request whatever is missing."""
        if not self.doc:
            return
        view = self.reader.view
        key = (self.current, self.night)

        fit = self.cache.get(key)
        if fit:
            self.cache.move_to_end(key)
            self._display(self.current, fit)
        else:
            self._request(self.current, False)

        if view.zoomed:
            zoom = self.zcache.get(key)
            if zoom:
                self._display(self.current, zoom)
            else:
                self._request(self.current, True)

        self._prefetch()

    def _display(self, page, texture):
        size = self.sizes.get(page)
        if size:
            self.reader.view.page_size = size
        self.reader.view.set_texture(texture)

    def _prefetch(self):
        for page in (self.current + 1, self.current - 1):
            if 0 <= page < self.page_count and (page, self.night) not in self.cache:
                self._request(page, False, prefetch=True)

    def _request(self, page, zoomed, prefetch=False):
        key = (page, zoomed, self.night)
        if self.pending.get(key) == self.gen:
            return
        gen = self.gen
        self.pending[key] = gen
        view = self.reader.view
        width = view.width or Window.width
        height = view.height or Window.height
        future = self.executor.submit(
            self._render_job, self.doc, gen, page, zoomed, self.night, width, height, prefetch
        )
        future.add_done_callback(
            lambda f: Clock.schedule_once(lambda dt: self._on_rendered(key, gen, f))
        )

    def _render_job(self, doc, gen, page_no, zoomed, night, width, height, prefetch):
        """Runs on the worker thread."""
        if gen != self.gen:
            return None
        # skip work the user has already swiped away from
        if abs(page_no - self.current) > (1 if prefetch else 0):
            return None
        if zoomed and not self.reader.view.zoomed:
            return None

        page = doc.load_page(page_no)
        rect = page.rect
        scale = min(width / rect.width, height / rect.height)
        if zoomed:
            scale *= ZOOM
        longest = max(rect.width, rect.height) * scale
        if longest > MAX_TEX:
            scale *= MAX_TEX / longest

        pix = page.get_pixmap(
            matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False
        )
        if night:
            pix.invert_irect(pix.irect)
        return rect.width, rect.height, pix.width, pix.height, pix.samples, pix

    def _on_rendered(self, key, gen, future):
        """Runs on the UI thread."""
        if self.pending.get(key) == gen:
            del self.pending[key]
        if gen != self.gen:
            return
        try:
            result = future.result()
        except Exception as error:  # a single bad page must not kill the app
            print("Render error:", error)
            return
        if result is None:
            return

        page, zoomed, night = key
        pw, ph, w, h, samples, _pix = result
        texture = Texture.create(size=(w, h), colorfmt="rgb")
        texture.blit_buffer(samples, colorfmt="rgb", bufferfmt="ubyte")
        texture.flip_vertical()
        texture.mag_filter = "linear"
        texture.min_filter = "linear"

        self.sizes[page] = (pw, ph)
        if zoomed:
            self.zcache.clear()           # only one heavy texture at a time
            self.zcache[(page, night)] = texture
        else:
            self.cache[(page, night)] = texture
            self.cache.move_to_end((page, night))
            while len(self.cache) > CACHE_PAGES:
                self.cache.popitem(last=False)

        if page == self.current and night == self.night:
            self.show()

    def _on_resized(self, *_):
        if not self.doc:
            return
        self.gen += 1
        self.cache.clear()
        self.zcache.clear()
        self.pending.clear()
        self.show()

    # -- night mode -------------------------------------------------------
    def toggle_night(self):
        self.night = not self.night
        self.reader.night_chip.accent = self.night
        self.cache.clear()
        self.zcache.clear()
        self.show()

    # -- bars -------------------------------------------------------------
    def _fade(self, visible):
        for bar in (self.reader.top_bar, self.reader.bottom_bar):
            Animation.cancel_all(bar, "opacity")
            Animation(opacity=1 if visible else 0, d=0.18, t="out_quad").start(bar)

    def show_bars(self, auto=False):
        if self._hide_event:
            self._hide_event.cancel()
            self._hide_event = None
        self._fade(True)
        if auto:
            self._hide_event = Clock.schedule_once(lambda dt: self.hide_bars(), BAR_AUTOHIDE)

    def hide_bars(self):
        if self.doc:
            self._fade(False)

    def toggle_bars(self):
        if not self.doc:
            return
        if self.reader.bottom_bar.opacity > 0.5:
            self.hide_bars()
        else:
            self.show_bars(auto=True)

    # -- persistence ------------------------------------------------------
    def _load_positions(self):
        try:
            with open(self._positions_file, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}

    def _save_positions(self, *_):
        if not self.doc:
            return
        data = self._load_positions()
        data.pop(self.doc_name, None)
        data[self.doc_name] = self.current          # most recent last
        data = dict(list(data.items())[-30:])        # keep the file tiny
        try:
            os.makedirs(self.user_data_dir, exist_ok=True)
            with open(self._positions_file, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
        except Exception:
            pass

    # -- misc -------------------------------------------------------------
    def _on_key(self, _window, key, *_):
        if key == 27 and self.doc and self.reader.view.zoomed:   # Android back
            self.reader.view.reset()
            self.show()
            return True
        if key == 275:   # right arrow (desktop)
            self.next_page()
            return True
        if key == 276:   # left arrow (desktop)
            self.prev_page()
            return True
        return False

    def show_error(self, message):
        Popup(
            title="Error",
            content=Label(text=message, halign="center", valign="middle"),
            size_hint=(0.85, 0.4),
        ).open()

    def on_pause(self):
        self._save_positions()
        return True

    def on_resume(self):
        pass

    def on_stop(self):
        self._save_positions()
        self.executor.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    PDFReaderApp().run()
