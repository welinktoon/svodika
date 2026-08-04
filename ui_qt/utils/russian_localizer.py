"""Runtime Russian localization for the inherited OpenWhisper interface."""
from PyQt6.QtCore import QObject, QEvent
from PyQt6.QtWidgets import QWidget, QAbstractButton, QLabel, QComboBox, QTabWidget

TEXT = {
    "OpenWhisper": "Svodika", "Settings": "Настройки", "General": "Основные", "Audio": "Аудио", "Hotkeys": "Горячие клавиши", "Cleanup": "Обработка текста", "Advanced": "Дополнительно",
    "Yes": "Да", "&Yes": "Да", "No": "Нет", "&No": "Нет", "OK": "ОК", "Cancel": "Отмена", "&Cancel": "Отмена", "Save": "Сохранить", "Save Settings": "Сохранить настройки", "Close": "Закрыть", "Back": "Назад", "Delete": "Удалить", "Edit": "Изменить", "Add": "Добавить", "Refresh": "Обновить",
    "General Settings": "Основные настройки", "Audio Settings": "Настройки аудио", "Saved Recordings": "Сохранённые записи", "Keep recordings:": "Хранить записи:", "Keep all": "Хранить все", "Custom": "Указать количество", "Number to keep:": "Количество:",
    "Auto-paste transcription to active window": "Вставлять расшифровку в активное окно", "Copy transcription to clipboard": "Копировать расшифровку в буфер обмена", "Minimize to system tray on close": "Сворачивать в трей при закрытии",
    "Recordings folder:": "Папка для записей:", "Choose folder…": "Выбрать папку…",
    "Real-Time Transcription (Experimental)": "Расшифровка в реальном времени (экспериментально)", "Enable real-time transcription preview (while recording)": "Показывать текст во время записи", "Preview font size:": "Размер текста предпросмотра:",
    "Sample Rate (Hz):": "Частота дискретизации (Гц):", "Channels:": "Каналы:", "Mono (1)": "Моно (1)", "Stereo (2)": "Стерео (2)", "Silence Threshold:": "Порог тишины:", "Input Device:": "Микрофон:", "System Default": "Системный по умолчанию", "Select microphone for recording": "Выберите микрофон для записи",
    "Configure global hotkeys for quick access": "Настройте глобальные горячие клавиши", "Configure Hotkeys...": "Настроить горячие клавиши…", "AI Transcript Cleanup": "Обработка расшифровки ИИ", "Learned Rules": "Правила обработки", "Provider:": "Провайдер:", "Model:": "Модель:", "Thinking level:": "Уровень рассуждений:", "Off": "Выключено", "Low": "Низкий", "Medium": "Средний", "High": "Высокий",
    "Model Manager": "Управление моделями", "Model Manager...": "Управление моделями…", "Download": "Скачать", "Remove": "Удалить", "Installed": "Установлено", "Download model": "Скачать модель", "Loading…": "Загрузка…", "Loading...": "Загрузка…", "Retry": "Повторить", "Details": "Подробнее",
    "History": "История", "Copy": "Копировать", "Copy raw": "Копировать исходный текст", "Transcribe again": "Расшифровать снова", "Clear history": "Очистить историю", "Search": "Поиск", "Upload File": "Открыть файл", "Transcribe": "Расшифровать", "Start Recording": "Начать запись", "Stop": "Остановить", "Recording...": "Идёт запись…", "Ready to record": "Готово к записи",
    "File": "Файл", "View": "Вид", "Help": "Справка", "About": "О приложении", "Exit": "Выйти", "Quit": "Выйти", "Minimize to Tray": "Свернуть в трей", "Compact": "Компактный режим", "Compact Mode": "Компактный режим",
}

class RussianLocalizer(QObject):
    def eventFilter(self, watched, event):
        # Translate only complete top-level windows. Traversing a widget tree
        # during ChildAdded can touch half-constructed Qt objects and crash the
        # interpreter at native level.
        if event.type() == QEvent.Type.Show:
            if isinstance(watched, QWidget) and watched.isWindow():
                self.translate_tree(watched)
        return False

    def translate_tree(self, root):
        for widget in [root, *root.findChildren(QWidget)]:
            if isinstance(widget, (QAbstractButton, QLabel)):
                value = widget.text()
                if value in TEXT: widget.setText(TEXT[value])
            if isinstance(widget, QComboBox):
                for index in range(widget.count()):
                    value = widget.itemText(index)
                    if value in TEXT: widget.setItemText(index, TEXT[value])
            if isinstance(widget, QTabWidget):
                for index in range(widget.count()):
                    value = widget.tabText(index)
                    if value in TEXT: widget.setTabText(index, TEXT[value])
        title = root.windowTitle()
        if title in TEXT: root.setWindowTitle(TEXT[title])
