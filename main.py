import fitz

from kivy.app import App
from kivy.core.window import Window
from kivy.graphics.texture import Texture
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.popup import Popup


class PDFImage(Image):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.start_x = 0
        self.start_y = 0

        self.allow_stretch = True
        self.keep_ratio = True

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            self.start_x = touch.x
            self.start_y = touch.y

        return super().on_touch_down(touch)

    def on_touch_up(self, touch):
        dx = touch.x - self.start_x
        dy = touch.y - self.start_y

        if abs(dx) > abs(dy) * 1.3 and abs(dx) > dp(80):
            app = App.get_running_app()

            if dx < 0:
                app.next_page()
            else:
                app.previous_page()

            return True

        return super().on_touch_up(touch)


class ReaderLayout(FloatLayout):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.pdf_image = PDFImage(
            size_hint=(1, 1)
        )

        self.add_widget(self.pdf_image)

        self.top_bar = BoxLayout(
            orientation="horizontal",
            size_hint=(1, None),
            height=dp(55),
            pos_hint={"top": 1},
            spacing=dp(5),
            padding=dp(5)
        )

        self.add_widget(self.top_bar)

        self.open_button = Button(
            text="Open PDF",
            size_hint_x=None,
            width=dp(100)
        )

        self.previous_button = Button(
            text="‹",
            size_hint_x=None,
            width=dp(50)
        )

        self.page_label = Label(
            text="No PDF"
        )

        self.next_button = Button(
            text="›",
            size_hint_x=None,
            width=dp(50)
        )

        self.top_bar.add_widget(self.open_button)
        self.top_bar.add_widget(self.previous_button)
        self.top_bar.add_widget(self.page_label)
        self.top_bar.add_widget(self.next_button)

        self.open_button.bind(
            on_release=self.open_file
        )

        self.previous_button.bind(
            on_release=lambda *_:
            App.get_running_app().previous_page()
        )

        self.next_button.bind(
            on_release=lambda *_:
            App.get_running_app().next_page()
        )

    def open_file(self, *_):

        chooser = FileChooserListView(
            path="/storage/emulated/0",
            filters=["*.pdf"],
            multiselect=False
        )

        buttons = BoxLayout(
            orientation="horizontal",
            size_hint_y=None,
            height=dp(50)
        )

        cancel = Button(text="Cancel")
        select = Button(text="Open")

        buttons.add_widget(cancel)
        buttons.add_widget(select)

        layout = BoxLayout(
            orientation="vertical"
        )

        layout.add_widget(chooser)
        layout.add_widget(buttons)

        popup = Popup(
            title="Choose PDF",
            content=layout,
            size_hint=(0.95, 0.9)
        )

        cancel.bind(on_release=popup.dismiss)

        def select_file(*args):

            if not chooser.selection:
                return

            path = chooser.selection[0]

            if path.lower().endswith(".pdf"):
                popup.dismiss()
                App.get_running_app().load_pdf(path)

        select.bind(on_release=select_file)

        popup.open()


class PDFReaderApp(App):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.document = None
        self.current_page = 0
        self.page_count = 0

        self.renderer_scale = 2.0

    def build(self):

        Window.clearcolor = (
            0.08,
            0.08,
            0.08,
            1
        )

        self.reader = ReaderLayout()

        return self.reader

    def load_pdf(self, path):

        try:

            if self.document:
                self.document.close()

            self.document = fitz.open(path)

            self.current_page = 0
            self.page_count = len(self.document)

            self.render_page()

        except Exception as error:

            self.show_error(
                "Could not open PDF:\n\n"
                + str(error)
            )

    def render_page(self):

        if not self.document:
            return

        page = self.document.load_page(
            self.current_page
        )

        matrix = fitz.Matrix(
            self.renderer_scale,
            self.renderer_scale
        )

        pixmap = page.get_pixmap(
            matrix=matrix,
            alpha=False
        )

        texture = Texture.create(
            size=(
                pixmap.width,
                pixmap.height
            ),
            colorfmt="rgb"
        )

        texture.blit_buffer(
            pixmap.samples,
            colorfmt="rgb",
            bufferfmt="ubyte"
        )

        texture.flip_vertical()

        self.reader.pdf_image.texture = texture

        self.reader.page_label.text = (
            f"{self.current_page + 1} / "
            f"{self.page_count}"
        )

    def next_page(self):

        if not self.document:
            return

        if self.current_page < self.page_count - 1:
            self.current_page += 1
            self.render_page()

    def previous_page(self):

        if not self.document:
            return

        if self.current_page > 0:
            self.current_page -= 1
            self.render_page()

    def show_error(self, message):

        label = Label(
            text=message,
            halign="center",
            valign="middle"
        )

        popup = Popup(
            title="Error",
            content=label,
            size_hint=(0.85, 0.4)
        )

        popup.open()

    def on_stop(self):

        if self.document:
            self.document.close()


if __name__ == "__main__":
    PDFReaderApp().run()
