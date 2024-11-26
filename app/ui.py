import datetime
import json
import queue
import sys
import re
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext
from typing import Optional
from urllib.parse import ParseResult, parse_qs, urlparse
import webbrowser
import requests
from PIL import Image
import customtkinter as ctk
from PIL import Image, ImageTk
import psutil

#from app.patch_notes import PatchNotes
from app.settings_window import SettingsWindow
#from app.user_panel import UserPanel
from app.about_window import AboutWindow
from downloader.bunkr import BunkrDownloader
from downloader.downloader import Downloader
from downloader.erome import EromeDownloader
from downloader.simpcity import SimpCity
from downloader.jpg5 import Jpg5Downloader
from app.progress_manager import ProgressManager

VERSION = "V0.8"
MAX_LOG_LINES = 50  # Límite máximo de líneas de log

def extract_ck_parameters(url: ParseResult) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Get the service, user and post id from the url if they exist
    """
    match = re.search(r"/(?P<service>[^/?]+)(/user/(?P<user>[^/?]+)(/post/(?P<post>[^/?]+))?)?", url.path)
    if match:
        [site, service, post] = match.group("service", "user", "post")
        return site, service, post
    else:
        return None, None, None

def extract_ck_query(url: ParseResult) -> tuple[Optional[str], int]:
    """
    Try to obtain the query and offset from the url if they exist
    """

    # This is kinda contrived but query parameters are awful to get right
    query = parse_qs(url.query)
    q = query.get("q")[0] if query.get("q") is not None and len(query.get("q")) > 0 else None
    o = query.get("o")[0] if query.get("o") is not None and len(query.get("o")) > 0 else "0"

    return q, int(o) if str.isdigit(o) else 0

# Application class
class ImageDownloaderApp(ctk.CTk):
    def __init__(self):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        super().__init__()
        self.title(f"Downloader [{VERSION}]")
        
        # Setup window
        self.setup_window()
        
        # Settings window
        self.settings_window = SettingsWindow(self, self.tr, self.load_translations, self.update_ui_texts, self.save_language_preference, VERSION, self)

        # About window
        self.about_window = AboutWindow(self, self.tr, VERSION)  # Inicializa AboutWindow

        # Load settings
        self.settings = self.settings_window.load_settings()
        
        # Language preferences
        lang = self.load_language_preference()
        self.load_translations(lang)
        self.image_downloader = None

        # Patch notes
        #self.patch_notes = PatchNotes(self, self.tr)

        self.progress_bars = {}
        
        # Obtener el número de estrellas de GitHub
        self.github_stars = self.get_github_stars("emy69", "CoomerDL")

        # Cargar el icono de GitHub
        self.github_icon = self.load_github_icon()

        # Initialize UI
        self.initialize_ui()
        
        self.update_ui_texts()  

        self.update_queue = queue.Queue()
        self.check_update_queue()
        self.protocol("WM_DELETE_WINDOW", self.on_app_close)

        self.download_start_time = None
        self.errors = []
        self.warnings = []
        self.max_downloads = self.settings_window.settings.get('max_downloads', 3)
        
        # Load download folder
        self.download_folder = self.load_download_folder() 
        if self.download_folder:
            self.folder_path.configure(text=self.download_folder)

        self.active_downloader = None  # Initialize active_downloader

        # Cargar iconos redimensionados
        self.icons = {
            'image': self.load_and_resize_image('resources/img/image_icon.png', (40, 40)),
            'video': self.load_and_resize_image('resources/img/video_icon.png', (40, 40)),
            'zip': self.load_and_resize_image('resources/img/zip_icon.png', (40, 40)),
            'default': self.load_and_resize_image('resources/img/default_icon.png', (40, 40))
        }

        # Progress manager
        self.progress_manager = ProgressManager(
            root=self,
            icons=self.icons,
            footer_speed_label=self.footer_speed_label,
            footer_eta_label=self.footer_eta_label,
            progress_bar=self.progress_bar,
            progress_percentage=self.progress_percentage
        )

    # Application close event
    def on_app_close(self):
        if self.is_download_active() and not self.active_downloader.cancel_requested:
            # Mostrar advertencia si hay una descarga activa
            messagebox.showwarning(
                self.tr("Descarga Activa"),
                self.tr("Hay una descarga en progreso. Por favor, cancela la descarga antes de cerrar.")
            )
        else:
            self.destroy()

    def is_download_active(self):
        return self.active_downloader is not None
    
    def close_program(self):
        # Cierra todas las ventanas y termina el proceso principal
        self.destroy()
        # Matar el proceso actual (eliminar del administrador de tareas)
        current_process = psutil.Process(os.getpid())
        for handler in current_process.children(recursive=True):
            handler.kill()
        current_process.kill()
    
    # Save and load language preferences
    def save_language_preference(self, language_code):
        config = {'language': language_code}
        with open('resources/config/languages/save_language/language_config.json', 'w') as config_file:
            json.dump(config, config_file)
        self.load_translations(language_code)
        self.update_ui_texts()
    
    def load_language_preference(self):
        try:
            with open('resources/config/languages/save_language/language_config.json', 'r') as config_file:
                config = json.load(config_file)
                return config.get('language', 'en')
        except FileNotFoundError:
            return 'en'

    # Load translations
    def load_translations(self, lang):
        path = "resources/config/languages/translations.json"
        with open(path, 'r', encoding='utf-8') as file:
            all_translations = json.load(file)
            self.translations = {key: value.get(lang, key) for key, value in all_translations.items()}
    
    def tr(self, text, **kwargs):
        translated_text = self.translations.get(text, text)
        if kwargs:
            translated_text = translated_text.format(**kwargs)
        return translated_text

    # Window setup
    def setup_window(self):
        window_width, window_height = 1000, 600
        center_x = int((self.winfo_screenwidth() / 2) - (window_width / 2))
        center_y = int((self.winfo_screenheight() / 2) - (window_height / 2))
        self.geometry(f"{window_width}x{window_height}+{center_x}+{center_y}")
        
        # Establecer el tamaño mínimo de la ventana
        self.minsize(window_width, window_height)
        
        if sys.platform == "win32":
            self.iconbitmap("resources/img/window.ico")

    # Initialize UI components
    def initialize_ui(self):

        # Crear la barra de menú personalizada
        self.menu_bar = ctk.CTkFrame(self, height=30, corner_radius=0)
        self.menu_bar.pack(side="top", fill="x")

        # Añadir botones al menú
        self.create_custom_menubar()
        # Input frame
        self.input_frame = ctk.CTkFrame(self)
        self.input_frame.pack(fill='x', padx=20, pady=20)
        self.input_frame.grid_columnconfigure(0, weight=1)
        self.input_frame.grid_rowconfigure(1, weight=1)

        self.url_label = ctk.CTkLabel(self.input_frame, text=self.tr("URL de la página web:"))
        self.url_label.grid(row=0, column=0, sticky='w')

        self.url_entry = ctk.CTkEntry(self.input_frame)
        self.url_entry.grid(row=1, column=0, sticky='ew', padx=(0, 5))

        self.browse_button = ctk.CTkButton(self.input_frame, text=self.tr("Seleccionar Carpeta"), command=self.select_folder)
        self.browse_button.grid(row=1, column=1, sticky='e')

        self.folder_path = ctk.CTkLabel(self.input_frame, text="", cursor="hand2", font=("Arial", 13))
        self.folder_path.grid(row=2, column=0, columnspan=2, sticky='w')
        self.folder_path.bind("<Button-1>", self.open_download_folder)

        # Añadir eventos para el efecto hover
        self.folder_path.bind("<Enter>", self.on_hover_enter)
        self.folder_path.bind("<Leave>", self.on_hover_leave)

        # Options frame
        self.options_frame = ctk.CTkFrame(self)
        self.options_frame.pack(pady=10, fill='x', padx=20)

        self.download_images_check = ctk.CTkCheckBox(self.options_frame, text=self.tr("Descargar Imágenes"))
        self.download_images_check.pack(side='left', padx=10)
        self.download_images_check.select()

        self.download_videos_check = ctk.CTkCheckBox(self.options_frame, text=self.tr("Descargar Vídeos"))
        self.download_videos_check.pack(side='left', padx=10)
        self.download_videos_check.select()

        self.download_compressed_check = ctk.CTkCheckBox(self.options_frame, text=self.tr("Descargar Comprimidos"))
        self.download_compressed_check.pack(side='left', padx=10)
        self.download_compressed_check.select()

        # Action frame
        self.action_frame = ctk.CTkFrame(self)
        self.action_frame.pack(pady=10, fill='x', padx=20)

        self.download_button = ctk.CTkButton(self.action_frame, text=self.tr("Descargar"), command=self.start_download)
        self.download_button.pack(side='left', padx=10)

        self.cancel_button = ctk.CTkButton(self.action_frame, text=self.tr("Cancelar Descarga"), state="disabled", command=self.cancel_download)
        self.cancel_button.pack(side='left', padx=10)

        self.progress_label = ctk.CTkLabel(self.action_frame, text="")
        self.progress_label.pack(side='left', padx=10)

        self.log_textbox = ctk.CTkTextbox(self, width=590, height=200, state='disabled')
        self.log_textbox.pack(pady=(10, 0), padx=20, fill='both', expand=True)

        self.download_all_check = ctk.CTkCheckBox(self.action_frame)
        self.download_all_check.pack(side='left', padx=10)
        
        # Conectar el evento del checkbox con una función de actualización
        self.download_all_check.configure(command=self.update_info_text)
        
        self.update_info_text()

        # Progress frame
        self.progress_frame = ctk.CTkFrame(self)
        self.progress_frame.pack(pady=(0, 10), fill='x', padx=20)

        self.progress_bar = ctk.CTkProgressBar(self.progress_frame)
        self.progress_bar.pack(side='left', fill='x', expand=True, padx=(0, 10))

        # self.processing_label = ctk.CTkLabel(self.progress_frame, text=self.tr("Procesando videos..."), font=("Arial", 12))
        # self.processing_label.pack(side='top', pady=(0, 10))
        # self.processing_label.pack_forget()

        self.progress_percentage = ctk.CTkLabel(self.progress_frame, text="0%")
        self.progress_percentage.pack(side='left')

        # Cargar el icono de descarga con un tamaño mayor
        self.download_icon = self.load_and_resize_image('resources/img/download_icon.png', (24, 24))  # Cambiado a (24, 24)

        # Reemplazar el botón con una etiqueta que simule un botón
        self.toggle_details_button = ctk.CTkLabel(self.progress_frame, image=self.download_icon, text="", cursor="hand2")
        self.toggle_details_button.pack(side='left', padx=(5, 0))
        self.toggle_details_button.bind("<Button-1>", lambda e: self.toggle_progress_details())

        # Agregar efecto hover
        self.toggle_details_button.bind("<Enter>", lambda e: self.toggle_details_button.configure(fg_color="gray25"))
        self.toggle_details_button.bind("<Leave>", lambda e: self.toggle_details_button.configure(fg_color="transparent"))

        self.progress_details_frame = ctk.CTkFrame(self)
        self.progress_details_frame.place_forget()

        # Context menu
        self.context_menu = tk.Menu(self.url_entry, tearoff=0)
        self.context_menu.add_command(label=self.tr("Copiar"), command=self.copy_to_clipboard)
        self.context_menu.add_command(label=self.tr("Pegar"), command=self.paste_from_clipboard)
        self.context_menu.add_command(label=self.tr("Cortar"), command=self.cut_to_clipboard)

        self.url_entry.bind("<Button-3>", self.show_context_menu)
        self.bind("<Button-1>", self.on_click)

        footer = ctk.CTkFrame(self, height=30, corner_radius=0)
        footer.pack(side="bottom", fill="x")

        self.footer_eta_label = ctk.CTkLabel(footer, text="", font=("Arial", 10))
        self.footer_eta_label.pack(side="left", padx=20)

        self.footer_speed_label = ctk.CTkLabel(footer, text="", font=("Arial", 10))
        self.footer_speed_label.pack(side="right", padx=20)

        # Actualizar textos después de inicializar la UI
        self.update_ui_texts()

    def update_info_text(self):
        if self.download_all_check.get():
            self.download_all_check.configure(text=self.tr("Descargar todo el perfil"))
        else:
            self.download_all_check.configure(text=self.tr("Descargar solo los posts del URL proporcionado"))

        # Verificar si el icono de información ya existe
        if not hasattr(self, 'info_label'):
            # Cargar la imagen de información
            info_icon = self.load_and_resize_image('resources/img/info_icon.png', (16, 16))
            
            # Crear un Label para el icono de información
            self.info_label = ctk.CTkLabel(self.action_frame, image=info_icon, text="", cursor="hand2")
            self.info_label.pack(side='left', padx=5)

            # Añadir un tooltip al icono de información
            self.create_tooltip(self.info_label, self.tr(
                "Selecciona esta opción para descargar todo el contenido disponible del perfil,\n"
                "en lugar de solo los posts del URL proporcionado."
            ))

    def create_tooltip(self, widget, text):
        tooltip = tk.Toplevel(widget)
        tooltip.wm_overrideredirect(True)  # Sin barra de título
        tooltip.withdraw()  # Ocultar inicialmente

        # Crear un Frame para el contenido del tooltip
        tooltip_frame = tk.Frame(tooltip, bg="#333333", relief='solid', bd=1, padx=10, pady=10)

        # Agregar un Label con el texto y estilo de la tarjeta
        tooltip_label = tk.Label(tooltip_frame, text=text, bg="#333333", fg="white", font=("Arial", 10), justify="left")
        tooltip_label.pack()

        tooltip_frame.pack()

        def enter(event):
            x, y, cx, cy = widget.bbox("insert")
            x += widget.winfo_rootx() + 20
            y += widget.winfo_rooty() + 20
            tooltip.wm_geometry(f"+{x}+{y}")
            tooltip.deiconify() 

        def leave(event):
            tooltip.withdraw()  

        widget.bind("<Enter>", enter)
        widget.bind("<Leave>", leave)


    # Update UI texts
    def update_ui_texts(self):

        # Actualizar textos de los botones del menú
        for widget in self.menu_bar.winfo_children():
            if isinstance(widget, ctk.CTkButton):
                text = widget.cget("text")
                if text.strip() in ["Archivo", "Ayuda", "Donaciones"]:
                    widget.configure(text=self.tr(text.strip()))

        # Si los menús están abiertos, recrearlos para actualizar los textos
        if self.archivo_menu_frame and self.archivo_menu_frame.winfo_exists():
            self.archivo_menu_frame.destroy()
            self.toggle_archivo_menu()

        self.url_label.configure(text=self.tr("URL de la página web:"))
        self.browse_button.configure(text=self.tr("Seleccionar Carpeta"))
        self.download_images_check.configure(text=self.tr("Descargar Imágenes"))
        self.download_videos_check.configure(text=self.tr("Descargar Vídeos"))
        self.download_compressed_check.configure(text=self.tr("Descargar Comprimidos"))
        self.download_button.configure(text=self.tr("Descargar"))
        self.cancel_button.configure(text=self.tr("Cancelar Descarga"))
        # self.processing_label.configure(text=self.tr("Procesando videos..."))
        self.title(self.tr(f"Downloader [{VERSION}]"))

        # Actualizar el texto del tooltip de información
        self.create_tooltip(self.info_label, self.tr(
            "Selecciona esta opción para descargar todo el contenido disponible del perfil,\n"
            "en lugar de solo los posts del URL proporcionado."
        ))

        self.update_info_text()
    
    def open_download_folder(self, event=None):
        if self.download_folder and os.path.exists(self.download_folder):
            if sys.platform == "win32":
                os.startfile(self.download_folder)  # Para Windows
            elif sys.platform == "darwin":
                subprocess.Popen(["open", self.download_folder])  # Para macOS
            else:
                subprocess.Popen(["xdg-open", self.download_folder])  # Para Linux
        else:
            messagebox.showerror(self.tr("Error"), self.tr("La carpeta no existe o no es válida."))


    def on_click(self, event):
        # Obtener la lista de widgets que no deben cerrar el menú al hacer clic
        widgets_to_ignore = [self.menu_bar]

        # Añadir los frames de los menús desplegables si existen
        for frame in [self.archivo_menu_frame, self.ayuda_menu_frame, self.donaciones_menu_frame]:
            if frame and frame.winfo_exists():
                widgets_to_ignore.append(frame)
                widgets_to_ignore.extend(self.get_all_children(frame))

        # Si el widget en el que se hizo clic no es ninguno de los que debemos ignorar, cerramos los menús
        if event.widget not in widgets_to_ignore:
            self.close_all_menus()

    def get_all_children(self, widget):
        children = widget.winfo_children()
        all_children = list(children)
        for child in children:
            all_children.extend(self.get_all_children(child))
        return all_children

    def create_custom_menubar(self):
        # Botón Archivo
        archivo_button = ctk.CTkButton(
            self.menu_bar,
            text=self.tr("Archivo"),
            width=80,
            fg_color="transparent",
            hover_color="gray25",
            command=self.toggle_archivo_menu
        )
        archivo_button.pack(side="left")
        archivo_button.bind("<Button-1>", lambda e: "break")

        # Botón About
        about_button = ctk.CTkButton(
            self.menu_bar,
            text=self.tr("About"),
            width=80,
            fg_color="transparent",
            hover_color="gray25",
            command=self.about_window.show_about 
        )
        about_button.pack(side="left")
        about_button.bind("<Button-1>", lambda e: "break")

        # Inicializar variables para los menús desplegables
        self.archivo_menu_frame = None
        self.ayuda_menu_frame = None
        self.donaciones_menu_frame = None

        # Función para cambiar el fondo al pasar el ratón
        def on_enter(event, frame):
            frame.configure(fg_color="gray25")

        def on_leave(event, frame):
            frame.configure(fg_color="transparent")

        # Añadir el icono de GitHub y el contador de estrellas
        if self.github_icon:
            resized_github_icon = self.github_icon.resize((16, 16), Image.Resampling.LANCZOS)
            resized_github_icon = ctk.CTkImage(resized_github_icon)
            github_frame = ctk.CTkFrame(self.menu_bar,cursor="hand2", fg_color="transparent", corner_radius=5)
            github_frame.pack(side="right", padx=5)
            github_label = ctk.CTkLabel(
                github_frame,
                image=resized_github_icon,
                text=f" Star {self.github_stars}",
                compound="left",
                font=("Arial", 12)
            )
            github_label.pack(padx=5, pady=5)
            github_frame.bind("<Enter>", lambda e: on_enter(e, github_frame))
            github_frame.bind("<Leave>", lambda e: on_leave(e, github_frame))
            github_label.bind("<Enter>", lambda e: on_enter(e, github_frame))
            github_label.bind("<Leave>", lambda e: on_leave(e, github_frame))
            github_label.bind("<Button-1>", lambda e: webbrowser.open("https://github.com/emy69/CoomerDL"))

        # Añadir el icono de Discord
        self.discord_icon = self.load_discord_icon()
        if self.discord_icon:
            resized_discord_icon = self.discord_icon.resize((16, 16), Image.Resampling.LANCZOS)
            resized_discord_icon = ctk.CTkImage(resized_discord_icon)
            discord_frame = ctk.CTkFrame(self.menu_bar,cursor="hand2", fg_color="transparent", corner_radius=5)
            discord_frame.pack(side="right", padx=5)
            discord_label = ctk.CTkLabel(
                discord_frame,
                image=resized_discord_icon,
                text="Discord",
                compound="left"
            )
            discord_label.pack(padx=5, pady=5)
            discord_frame.bind("<Enter>", lambda e: on_enter(e, discord_frame))
            discord_frame.bind("<Leave>", lambda e: on_leave(e, discord_frame))
            discord_label.bind("<Enter>", lambda e: on_enter(e, discord_frame))
            discord_label.bind("<Leave>", lambda e: on_leave(e, discord_frame))
            discord_label.bind("<Button-1>", lambda e: webbrowser.open("https://discord.gg/ku8gSPsesh"))

        # Añadir un nuevo icono PNG
        self.new_icon = self.load_new_icon()
        if self.new_icon:
            resized_new_icon = self.new_icon.resize((16, 16), Image.Resampling.LANCZOS)
            resized_new_icon = ctk.CTkImage(resized_new_icon)
            new_icon_frame = ctk.CTkFrame(self.menu_bar,cursor="hand2", fg_color="transparent", corner_radius=5)
            new_icon_frame.pack(side="right", padx=5)
            new_icon_label = ctk.CTkLabel(
                new_icon_frame,
                image=resized_new_icon,
                text="Support",
                compound="left"
            )
            new_icon_label.pack(padx=5, pady=5)
            new_icon_frame.bind("<Enter>", lambda e: on_enter(e, new_icon_frame))
            new_icon_frame.bind("<Leave>", lambda e: on_leave(e, new_icon_frame))
            new_icon_label.bind("<Enter>", lambda e: on_enter(e, new_icon_frame))
            new_icon_label.bind("<Leave>", lambda e: on_leave(e, new_icon_frame))
            new_icon_label.bind("<Button-1>", lambda e: webbrowser.open("https://buymeacoffee.com/emy_69"))

    def toggle_archivo_menu(self):
        if self.archivo_menu_frame and self.archivo_menu_frame.winfo_exists():
            self.archivo_menu_frame.destroy()
        else:
            self.close_all_menus()
            self.archivo_menu_frame = self.create_menu_frame([
                (self.tr("Configuraciones"), self.settings_window.open_settings),
                ("separator", None),
                (self.tr("Salir"), self.quit),
            ], x=0)


    def create_menu_frame(self, options, x):
        # Crear el marco del menú con fondo oscuro y borde de sombra para resaltar
        menu_frame = ctk.CTkFrame(self, corner_radius=5, fg_color="gray25", border_color="black", border_width=1)
        menu_frame.place(x=x, y=30)
        
        # Agregar sombra alrededor del menú
        menu_frame.configure(border_width=1, border_color="black")

        # Evitar la propagación del clic en el menú
        menu_frame.bind("<Button-1>", lambda e: "break")

        # Añadir opciones al menú con separación entre elementos
        for option in options:
            if option[0] == "separator":
                separator = ctk.CTkFrame(menu_frame, height=1, fg_color="gray50")
                separator.pack(fill="x", padx=5, pady=5)
                separator.bind("<Button-1>", lambda e: "break")
            elif option[1] is None:
                # Texto sin comando (por ejemplo, título de submenú)
                label = ctk.CTkLabel(menu_frame, text=option[0], anchor="w", fg_color="gray30")
                label.pack(fill="x", padx=5, pady=2)
                label.bind("<Button-1>", lambda e: "break")
            else:
                btn = ctk.CTkButton(
                    menu_frame,
                    text=option[0],
                    fg_color="transparent",
                    hover_color="gray35",
                    anchor="w",
                    text_color="white",
                    command=lambda cmd=option[1]: cmd()
                )
                btn.pack(fill="x", padx=5, pady=2)
                btn.bind("<Button-1>", lambda e: "break")

        return menu_frame

    def close_all_menus(self):
        for menu_frame in [self.archivo_menu_frame, self.ayuda_menu_frame, self.donaciones_menu_frame]:
            if menu_frame and menu_frame.winfo_exists():
                menu_frame.destroy()

    # Image processing
    def create_photoimage(self, path, size=(32, 32)):
        img = Image.open(path)
        img = img.resize(size, Image.Resampling.LANCZOS)
        photoimg = ImageTk.PhotoImage(img)
        return photoimg

    # Setup downloaders
    def setup_erome_downloader(self, is_profile_download=False):
        self.erome_downloader = EromeDownloader(
            root=self,
            enable_widgets_callback=self.enable_widgets,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, como Gecko) Chrome/58.0.3029.110 Safari/537.36',
                'Referer': 'https://www.erome.com/'
            },
            log_callback=self.add_log_message_safe,
            update_progress_callback=self.update_progress,
            update_global_progress_callback=self.update_global_progress,
            download_images=self.download_images_check.get(),
            download_videos=self.download_videos_check.get(),
            is_profile_download=is_profile_download,
            max_workers=self.max_downloads,
            tr=self.tr
        )

    def setup_simpcity_downloader(self):
        self.simpcity_downloader = SimpCity(
            download_folder=self.download_folder,
            log_callback=self.add_log_message_safe,
            enable_widgets_callback=self.enable_widgets,
            update_progress_callback=self.update_progress,
            update_global_progress_callback=self.update_global_progress,
            tr=self.tr
        )

    def setup_bunkr_downloader(self):
        self.bunkr_downloader = BunkrDownloader(
            download_folder=self.download_folder,
            log_callback=self.add_log_message_safe,
            enable_widgets_callback=self.enable_widgets,
            update_progress_callback=self.update_progress,
            update_global_progress_callback=self.update_global_progress,
            headers={
                'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
                'Referer': 'https://bunkr.site/',
            },
            max_workers=self.max_downloads
        )

    def setup_general_downloader(self):
        self.general_downloader = Downloader(
            download_folder=self.download_folder,
            log_callback=self.add_log_message_safe,
            enable_widgets_callback=self.enable_widgets,
            update_progress_callback=self.update_progress,
            update_global_progress_callback=self.update_global_progress,
            headers={
                'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
                'Referer': 'https://coomer.su/',
            },
            download_images=self.download_images_check.get(),
            download_videos=self.download_videos_check.get(),
            download_compressed=self.download_compressed_check.get(),
            tr=self.tr,
            max_workers=self.max_downloads,
            folder_structure=self.settings_window.settings.get('folder_structure', 'default')
        )
    
    def setup_jpg5_downloader(self):
        self.active_downloader = Jpg5Downloader(
            url=self.url_entry.get().strip(),
            carpeta_destino=self.download_folder,
            log_callback=self.add_log_message_safe,
            tr=self.tr,
            progress_manager=self.progress_manager,
            max_workers=self.max_downloads
        )

    # Folder selection
    def select_folder(self):
        folder_selected = filedialog.askdirectory()
        if folder_selected:
            self.download_folder = folder_selected
            self.folder_path.configure(text=folder_selected)
            self.save_download_folder(folder_selected)
    
    # Función para cargar y redimensionar imágenes
    def load_and_resize_image(self, path, size=(20, 20)):
        img = Image.open(path)
        return ctk.CTkImage(img, size=size)
    
    # Reemplaza las llamadas a los métodos de progreso con self.progress_manager
    def update_progress(self, downloaded, total, file_id=None, file_path=None, speed=None, eta=None):
        self.progress_manager.update_progress(downloaded, total, file_id, file_path, speed, eta)

    def remove_progress_bar(self, file_id):
        self.progress_manager.remove_progress_bar(file_id)

    def update_global_progress(self, completed_files, total_files):
        self.progress_manager.update_global_progress(completed_files, total_files)

    def toggle_progress_details(self):
        self.progress_manager.toggle_progress_details()

    def center_progress_details_frame(self):
        self.progress_manager.center_progress_details_frame()

    # Error logging
    def log_error(self, error_message):
        self.errors.append(error_message)
        self.add_log_message_safe(f"Error: {error_message}")

    def wrapped_download(self, download_method, *args):
        try:
            download_method(*args)
        finally:
            self.active_downloader = None
            self.enable_widgets()
            self.export_logs()

    # Download management
    def start_download(self):
        url = self.url_entry.get().strip()
        if not hasattr(self, 'download_folder') or not self.download_folder:
            messagebox.showerror(self.tr("Error"), self.tr("Por favor, selecciona una carpeta de descarga."))
            return

        self.download_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.download_start_time = datetime.datetime.now()
        self.errors = []
        download_all = self.download_all_check.get()

        parsed_url = urlparse(url)
        
        if "erome.com" in url:
            self.add_log_message_safe(self.tr("Descargando Erome"))
            is_profile_download = "/a/" not in url
            self.setup_erome_downloader(is_profile_download=is_profile_download)
            self.active_downloader = self.erome_downloader
            if "/a/" in url:
                self.add_log_message_safe(self.tr("URL del álbum"))
                download_thread = threading.Thread(target=self.wrapped_download, args=(self.active_downloader.process_album_page, url, self.download_folder, self.download_images_check.get(), self.download_videos_check.get()))
            else:
                self.add_log_message_safe(self.tr("URL del perfil"))
                download_thread = threading.Thread(target=self.wrapped_download, args=(self.active_downloader.process_profile_page, url, self.download_folder, self.download_images_check.get(), self.download_videos_check.get()))
        
        elif re.search(r"https?://([a-z0-9-]+\.)?bunkr\.[a-z]{2,}", url):
            self.add_log_message_safe(self.tr("Descargando Bunkr"))
            self.setup_bunkr_downloader()
            self.active_downloader = self.bunkr_downloader
            if "/v/" in url or "/i/" in url:
                self.add_log_message_safe(self.tr("URL del post"))
                download_thread = threading.Thread(target=self.wrapped_download, args=(self.bunkr_downloader.descargar_post_bunkr, url))
            else:
                self.add_log_message_safe(self.tr("URL del perfil"))
                download_thread = threading.Thread(target=self.wrapped_download, args=(self.bunkr_downloader.descargar_perfil_bunkr, url))
        
        elif parsed_url.netloc in ["coomer.su", "kemono.su"]:
            self.add_log_message_safe(self.tr("Iniciando descarga..."))
            self.setup_general_downloader()
            self.active_downloader = self.general_downloader

            site = f"{parsed_url.netloc}"
            service, user, post = extract_ck_parameters(parsed_url)
            if service is None or user is None:
                if service is None:
                    self.add_log_message_safe(self.tr("No se pudo extraer el servicio."))
                    messagebox.showerror(self.tr("Error"), self.tr("No se pudo extraer el servicio."))
                else:
                    self.add_log_message_safe(self.tr("No se pudo extraer el ID del usuario."))
                    messagebox.showerror(self.tr("Error"), self.tr("No se pudo extraer el ID del usuario."))

                self.add_log_message_safe(self.tr("URL no válida"))
                self.download_button.configure(state="normal")
                self.cancel_button.configure(state="disabled")
                return

            self.add_log_message_safe(self.tr("Servicio extraído: {service} del sitio: {site}", service=service, site=site))

            if post is not None:
                self.add_log_message_safe(self.tr("Descargando post único..."))
                download_thread = threading.Thread(target=self.wrapped_download, args=(self.start_ck_post_download, site, service, user, post))
            else:
                query, offset = extract_ck_query(parsed_url)
                self.add_log_message_safe(self.tr("Descargando todo el contenido del usuario..." if download_all else "Descargando solo los posts del URL proporcionado..."))
                download_thread = threading.Thread(target=self.wrapped_download, args=(self.start_ck_profile_download, site, service, user, query, download_all, offset))
        
        elif "simpcity.su" in url:
            self.add_log_message_safe(self.tr("Descargando SimpCity"))
            self.setup_simpcity_downloader()
            self.active_downloader = self.simpcity_downloader
            # Iniciar la descarga en un hilo separado
            download_thread = threading.Thread(target=self.wrapped_download, args=(self.active_downloader.download_images_from_simpcity, url))
        
        elif "jpg5.su" in url:
            self.add_log_message_safe(self.tr("Descargando desde Jpg5"))
            self.setup_jpg5_downloader()
            
            # Usar wrapped_download para manejar la descarga
            download_thread = threading.Thread(target=self.wrapped_download, args=(self.active_downloader.descargar_imagenes,))
        
        else:
            self.add_log_message_safe(self.tr("URL no válida"))
            self.download_button.configure(state="normal")
            self.cancel_button.configure(state="disabled")
            return

        download_thread.start()

    def start_ck_profile_download(self, site, service, user, query, download_all, initial_offset):
        download_info = self.active_downloader.download_media(site, user, service, query=query, download_all=download_all, initial_offset=initial_offset)
        if download_info:
            self.add_log_message_safe(f"Download info: {download_info}")
        # Llamar a export_logs al finalizar la descarga
        self.export_logs()
        self.active_downloader = None  # Resetea la active_downloader cuando la descarga termina
        self.enable_widgets()  # Asegúrate de habilitar los widgets
    
    def start_ck_post_download(self, site, service, user, post):
        download_info = self.active_downloader.download_single_post(site, post, service, user)
        if download_info:
            self.add_log_message_safe(f"Download info: {download_info}")
        # Llamar a export_logs al finalizar la descarga
        self.export_logs()
        self.active_downloader = None  # Resetea la active_downloader cuando la descarga termina
        self.enable_widgets()  # Asegúrate de habilitar los widgets

    def extract_user_id(self, url):
        self.add_log_message_safe(self.tr("Extrayendo ID del usuario del URL: {url}", url=url))
        match = re.search(r'/user/([^/?]+)', url)
        if match:
            user_id = match.group(1)
            self.add_log_message_safe(self.tr("ID del usuario extraído: {user_id}", user_id=user_id))
            return user_id
        else:
            self.add_log_message_safe(self.tr("No se pudo extraer el ID del usuario."))
            messagebox.showerror(self.tr("Error"), self.tr("No se pudo extraer el ID del usuario."))
            return None

    def extract_post_id(self, url):
        match = re.search(r'/post/([^/?]+)', url)
        if match:
            post_id = match.group(1)
            self.add_log_message_safe(self.tr("ID del post extraído: {post_id}", post_id=post_id))
            return post_id
        else:
            self.add_log_message_safe(self.tr("No se pudo extraer el ID del post."))
            messagebox.showerror(self.tr("Error"), self.tr("No se pudo extraer el ID del post."))
            return None

    def cancel_download(self):
        if self.active_downloader:
            self.active_downloader.request_cancel()
            self.active_downloader = None
            self.clear_progress_bars()
        else:
            self.add_log_message_safe(self.tr("No hay una descarga en curso para cancelar."))
        self.enable_widgets()

    def clear_progress_bars(self):
        for file_id in list(self.progress_bars.keys()):
            self.remove_progress_bar(file_id)

    # Log messages safely
    def add_log_message_safe(self, message):
        if "error" in message.lower():
            self.errors.append(message)
        if "warning" in message.lower():
            self.warnings.append(message)

        def log_in_main_thread():
            self.log_textbox.configure(state='normal')
            self.log_textbox.insert('end', message + '\n')
            self.limit_log_lines() 
            self.log_textbox.configure(state='disabled')
            self.log_textbox.yview_moveto(1)
        self.after(0, log_in_main_thread)

    def limit_log_lines(self):
        log_lines = self.log_textbox.get("1.0", "end-1c").split("\n")
        if len(log_lines) > MAX_LOG_LINES:
            self.log_textbox.configure(state='normal')
            self.log_textbox.delete("1.0", f"{len(log_lines) - MAX_LOG_LINES}.0")
            self.log_textbox.configure(state='disabled')

    # Export logs to a file
    def export_logs(self):
        log_folder = "resources/config/logs/"
        Path(log_folder).mkdir(parents=True, exist_ok=True)
        log_file_path = Path(log_folder) / f"log_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        try:
            if self.active_downloader:
                total_files = self.active_downloader.total_files
                completed_files = self.active_downloader.completed_files
                skipped_files = self.active_downloader.skipped_files
                failed_files = self.active_downloader.failed_files
            else:
                total_files = 0
                completed_files = 0
                skipped_files = []
                failed_files = []
            
            total_images = completed_files if self.download_images_check.get() else 0
            total_videos = completed_files if self.download_videos_check.get() else 0
            errors = len(self.errors)
            warnings = len(self.warnings)
            duration = datetime.datetime.now() - self.download_start_time

            skipped_files_summary = "\n".join(skipped_files)
            failed_files_summary = "\n".join(failed_files)

            summary = (
                f"{self.tr('Total de archivos descargados')}: {total_files}\n"
                f"{self.tr('Total de imágenes descargadas')}: {total_images}\n"
                f"{self.tr('Total de videos descargados')}: {total_videos}\n"
                f"{self.tr('Errores')}: {errors}\n"
                f"{self.tr('Advertencias')}: {warnings}\n"
                f"{self.tr('Tiempo total de descarga')}: {duration}\n\n"
                f"{self.tr('Archivos saltados por ya estar descargados')}:\n{skipped_files_summary}\n\n"
                f"{self.tr('Archivos fallidos')}:\n{failed_files_summary}\n\n"
            )

            with open(log_file_path, 'w', encoding='utf-8') as file:
                file.write(summary)
                file.write(self.log_textbox.get("1.0", tk.END))
            self.add_log_message_safe(self.tr("Logs exportados exitosamente a {path}", path=log_file_path))
        except Exception as e:
            self.add_log_message_safe(self.tr("No se pudo exportar los logs: {e}", e=e))

    # Clipboard operations
    def copy_to_clipboard(self):
        try:
            selected_text = self.url_entry.selection_get()
            if selected_text:
                self.clipboard_clear()
                self.clipboard_append(selected_text)
            else:
                self.add_log_message_safe(self.tr("No hay texto seleccionado para copiar."))
        except tk.TclError:
            self.add_log_message_safe(self.tr("No hay texto seleccionado para copiar."))

    def paste_from_clipboard(self):
        try:
            clipboard_text = self.clipboard_get()
            if clipboard_text:
                try:
                    self.url_entry.delete("sel.first", "sel.last")  # Elimina el texto seleccionado si hay alguno
                except tk.TclError:
                    pass
                self.url_entry.insert(tk.INSERT, clipboard_text)
            else:
                self.add_log_message_safe(self.tr("No hay texto en el portapapeles para pegar."))
        except tk.TclError as e:
            self.add_log_message_safe(self.tr(f"Error al pegar desde el portapapeles: {e}"))

    def cut_to_clipboard(self):
        try:
            selected_text = self.url_entry.selection_get()
            if selected_text:
                self.clipboard_clear()
                self.clipboard_append(selected_text)
                self.url_entry.delete("sel.first", "sel.last")
            else:
                self.add_log_message_safe(self.tr("No hay texto seleccionado para cortar."))
        except tk.TclError:
            self.add_log_message_safe(self.tr("No hay texto seleccionado para cortar."))


    # Show context menu
    def show_context_menu(self, event):
        self.context_menu.tk_popup(event.x_root, event.y_root)
        self.context_menu.grab_release()

    # Update queue
    def check_update_queue(self):
        while not self.update_queue.empty():
            task = self.update_queue.get_nowait()
            task()
        self.after(100, self.check_update_queue)

    # Enable widgets
    def enable_widgets(self):
        self.update_queue.put(lambda: self.download_button.configure(state="normal"))
        self.update_queue.put(lambda: self.cancel_button.configure(state="disabled"))
        self.update_queue.put(lambda: self.download_all_check.configure(state="normal"))

    # Save and load download folder
    def save_download_folder(self, folder_path):
        config = {'download_folder': folder_path}
        with open('resources/config/download_path/download_folder.json', 'w') as config_file:
            json.dump(config, config_file)

    def load_download_folder(self):
        config_path = 'resources/config/download_path/download_folder.json'
        config_dir = Path(config_path).parent
        if not config_dir.exists():
            config_dir.mkdir(parents=True)
        if not Path(config_path).exists():
            with open(config_path, 'w') as config_file:
                json.dump({'download_folder': ''}, config_file)
        try:
            with open(config_path, 'r') as config_file:
                config = json.load(config_file)
                return config.get('download_folder', '')
        except json.JSONDecodeError:
            return ''

    # Update max downloads
    def update_max_downloads(self, max_downloads):
        self.max_downloads = max_downloads
        if hasattr(self, 'general_downloader'):
            self.general_downloader.max_workers = max_downloads
        if hasattr(self, 'erome_downloader'):
            self.erome_downloader.max_workers = max_downloads
        if hasattr(self, 'bunkr_downloader'):
            self.bunkr_downloader.max_workers = max_downloads

    def on_hover_enter(self, event):
        self.folder_path.configure(font=("Arial", 13, "underline"))  # Subrayar el texto al pasar el ratón

    def on_hover_leave(self, event):
        self.folder_path.configure(font=("Arial", 13))  # Quitar el subrayado al salir el ratón

    def get_github_stars(self, user, repo):
        try:
            response = requests.get(f"https://api.github.com/repos/{user}/{repo}")
            response.raise_for_status()
            data = response.json()
            return data.get("stargazers_count", 0)
        except requests.RequestException as e:
            self.add_log_message_safe(f"Error al obtener las estrellas de GitHub: {e}")
            return 0

    def load_icon(self, icon_path, icon_name):
        try:
            img = Image.open(icon_path)
            return img  # Devuelve la imagen de PIL
        except Exception as e:
            self.add_log_message_safe(f"Error al cargar el icono {icon_name}: {e}")
            return None

    # Uso de la función genérica para cargar íconos específicos
    def load_github_icon(self):
        return self.load_icon("resources/img/github-logo-24.png", "GitHub")

    def load_discord_icon(self):
        return self.load_icon("resources/img/discord-alt-logo-24.png", "Discord")

    def load_new_icon(self):
        return self.load_icon("resources/img/dollar-circle-solid-24.png", "New Icon")
