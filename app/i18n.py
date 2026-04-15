"""Simple i18n helpers for EN/RU UI localization."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import Request
from fastapi.responses import Response

DEFAULT_LANG = "en"
SUPPORTED_LANGS = {"en", "ru"}
COOKIE_NAME = "ch_lang"

_TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "app_name": "IonFlow",
        "app_subtitle": "Data Processing Suite",
        "nav_overview": "Overview",
        "nav_dashboard": "Dashboard",
        "nav_history": "History",
        "nav_analysis": "Data Analysis",
        "nav_converters": "Converters",
        "nav_admin": "Admin",
        "nav_users": "Users",
        "logout": "Sign out",
        "theme_light": "Light mode",
        "theme_dark": "Dark mode",
        "lang_label": "Language",
        "lang_en": "English",
        "lang_ru": "Russian",
        "login_title": "Sign In",
        "login_card_title": "Sign in to your account",
        "login_username": "Username",
        "login_password": "Password",
        "login_continue": "Continue",
        "login_restricted": "Access restricted to authorised users only.",
        "auth_invalid_credentials": "Invalid username or password.",
        "auth_account_deactivated": "Account is deactivated. Contact an administrator.",
        "dashboard_title": "Dashboard",
        "dashboard_subtitle": "Choose a converter below to begin processing.",
        "dashboard_analysis_desc": "Explore TEC data, export pivot-ready CSV/XLSX, and fetch plot PNG/JSON/script from the analysis backend.",
        "recent_activity": "Recent Activity",
        "view_all": "View all",
        "history_title": "Job History",
        "history_all_users": "All users · {total} total runs",
        "history_your_runs": "Your runs · {total} total",
        "users_title": "User Management",
        "users_subtitle": "Manage accounts and access roles. Admin only.",
        "users_add_user": "Add User",
        "users_role": "Role",
        "users_operator_option": "Operator - can run jobs, view own history",
        "users_admin_option": "Admin - full access including user management",
        "users_create_account": "Create Account",
        "run_back": "Dashboard",
        "run_page_subtitle": "Configure parameters and start a conversion job.",
        "run_configuration": "Configuration",
        "run_hint_tecsuite_server_folder": "Server Folder (host path) is configured from environment variable RINEX_DATA_PATH_HOST.",
        "run_hint_year_folder_format": "Only folders in format YYYY_original are shown.",
        "run_hint_day_folder_options": "Day options include the number of station ZIP files in each day folder.",
        "run_hint_abstec_input_root": "Input DAT Root (host path) is configured from environment variable TECSUITE_OUT_DAT_DATA_PATH_HOST.",
        "run_hint_abstec_output_root": "Output Root (host path) is configured from environment variable ABSTEC_OUTPUT_DATA_PATH_HOST.",
        "run_hint_abstec_years": "Choose one or more years. First selected year drives dependent day/site options.",
        "run_label_day_of_year_single": "Day Of Year (single run)",
        "run_hint_abstec_day_single": "Choose one or more days for single-run mode. Selecting this clears batch days.",
        "run_label_days_batch": "Days (batch mode)",
        "run_hint_abstec_days_batch": "Choose one or more days for batch mode. Selecting this clears single-run days.",
        "run_hint_abstec_sites": "Choose one or more sites. Options depend on selected year/day values.",
        "run_hint_conversion_direction": "Conversion direction.",
        "run_label_source_dataset": "Source Dataset",
        "run_hint_source_dataset": "Choose which env-backed dataset root to use as the source directory.",
        "run_option_whole_source_root": "Whole source root",
        "run_hint_year_filter": "Optional filter. Choose a year to convert only that year subtree.",
        "run_hint_day_filter": "Optional filter. Choose a day to convert only that day subtree.",
        "run_label_source_directory": "Source Directory (host path)",
        "run_label_destination_directory": "Destination Directory (host path)",
        "run_label_auto_remove": "Auto-remove container (--rm)",
        "run_hint_auto_remove": "Remove the Docker container automatically after it exits.",
        "label_year": "Year",
        "label_year_folder": "Year Folder",
        "label_day_folder": "Day Folder",
        "label_site": "Site",
        "label_direction": "Direction",
        "option_all_days_selected_year": "All days in selected year",
        "run_no_year_folders_available": "No year folders available",
        "run_unit_stations": "stations",
        "run_no_source_profile": "No source profile available.",
        "run_no_destination_profile": "No destination profile available.",
        "run_hint_overwrite_enabled": "Overwrite is enabled, so output will be written back into the source directory.",
        "run_button": "Run",
        "run_starting": "Starting container...",
        "badge_running": "Running",
        "badge_success": "Success",
        "analysis_page_subtitle": "Query TEC data, preview pivot summaries, and render plots from the analysis backend.",
        "analysis_data_query_builder": "Data Query Builder",
        "analysis_label_endpoint": "Endpoint",
        "analysis_label_doy": "DOY",
        "analysis_label_doy_start": "DOY Start",
        "analysis_label_doy_end": "DOY End",
        "analysis_label_station": "Station",
        "analysis_label_satellite": "Satellite",
        "analysis_label_stations_multi": "Stations (multi-select)",
        "analysis_label_source": "Source",
        "analysis_label_alpha": "Alpha",
        "analysis_label_format": "Format",
        "analysis_run_data_query": "Run Data Query",
        "analysis_open_new_tab": "Open In New Tab",
        "analysis_plot_viewer": "Plot Viewer",
        "analysis_label_plot_endpoint": "Plot Endpoint",
        "analysis_render_plot": "Render Plot",
        "analysis_data_preview": "Data Preview",
        "analysis_plot_output": "Plot Output",
        "analysis_label_columns_csv": "columns (CSV list)",
        "analysis_plot_options": "Plot Options",
        "analysis_label_show_ci": "show_ci",
        "analysis_label_show_var": "show_var",
        "analysis_label_width_px": "width_px",
        "analysis_label_height_px": "height_px",
        "analysis_label_dpi": "dpi",
        "analysis_label_smooth": "smooth",
        "analysis_label_poly": "poly",
        "analysis_label_size_px": "size_px",
        "analysis_label_column": "column",
        "analysis_label_valid_only": "valid_only",
        "analysis_label_color_by_tec": "color_by_tec",
        "flag_label_days": "Days Filter",
        "flag_help_days": "Optional day selection list/range, e.g. 1-5,10,12-14.",
        "flag_label_day_from": "Day From (inclusive)",
        "flag_help_day_from": "Optional inclusive start day-of-year filter (1..366).",
        "flag_label_day_to": "Day To (inclusive)",
        "flag_help_day_to": "Optional inclusive end day-of-year filter (1..366).",
    },
    "ru": {
        "app_name": "ИоноПоток",
        "app_subtitle": "Пакет обработки данных",
        "nav_overview": "Обзор",
        "nav_dashboard": "Панель",
        "nav_history": "История",
        "nav_analysis": "Аналитика данных",
        "nav_converters": "Конвертеры",
        "nav_admin": "Администрирование",
        "nav_users": "Пользователи",
        "logout": "Выход",
        "theme_light": "Светлая тема",
        "theme_dark": "Темная тема",
        "lang_label": "Язык",
        "lang_en": "Английский",
        "lang_ru": "Русский",
        "login_title": "Вход",
        "login_card_title": "Войдите в учетную запись",
        "login_username": "Имя пользователя",
        "login_password": "Пароль",
        "login_continue": "Продолжить",
        "login_restricted": "Доступ разрешен только авторизованным пользователям.",
        "auth_invalid_credentials": "Неверное имя пользователя или пароль.",
        "auth_account_deactivated": "Учетная запись деактивирована. Обратитесь к администратору.",
        "dashboard_title": "Панель",
        "dashboard_subtitle": "Выберите конвертер ниже, чтобы начать обработку.",
        "dashboard_analysis_desc": "Исследуйте TEC-данные, экспортируйте сводки в CSV/XLSX и получайте PNG/JSON/скрипт графиков из backend аналитики.",
        "recent_activity": "Последняя активность",
        "view_all": "Показать все",
        "history_title": "История задач",
        "history_all_users": "Все пользователи · запусков: {total}",
        "history_your_runs": "Ваши запуски · всего: {total}",
        "users_title": "Управление пользователями",
        "users_subtitle": "Управление учетными записями и ролями доступа. Только администратор.",
        "users_add_user": "Добавить пользователя",
        "users_role": "Роль",
        "users_operator_option": "Оператор - может запускать задачи и видеть свою историю",
        "users_admin_option": "Администратор - полный доступ, включая управление пользователями",
        "users_create_account": "Создать учетную запись",
        "run_back": "Панель",
        "run_page_subtitle": "Настройте параметры и запустите задачу конвертации.",
        "run_configuration": "Конфигурация",
        "run_hint_tecsuite_server_folder": "Папка сервера (путь хоста) берется из переменной окружения RINEX_DATA_PATH_HOST.",
        "run_hint_year_folder_format": "Отображаются только папки в формате YYYY_original.",
        "run_hint_day_folder_options": "Для дней показывается количество ZIP-файлов станций в каждой папке.",
        "run_hint_abstec_input_root": "Корень входных DAT (путь хоста) берется из переменной окружения TECSUITE_OUT_DAT_DATA_PATH_HOST.",
        "run_hint_abstec_output_root": "Корень выхода (путь хоста) берется из переменной окружения ABSTEC_OUTPUT_DATA_PATH_HOST.",
        "run_hint_abstec_years": "Выберите один или несколько годов. Первый выбранный год управляет зависимыми днями и сайтами.",
        "run_label_day_of_year_single": "День года (одиночный запуск)",
        "run_hint_abstec_day_single": "Выберите один или несколько дней для одиночного режима. При выборе очищаются пакетные дни.",
        "run_label_days_batch": "Дни (пакетный режим)",
        "run_hint_abstec_days_batch": "Выберите один или несколько дней для пакетного режима. При выборе очищаются дни одиночного режима.",
        "run_hint_abstec_sites": "Выберите один или несколько сайтов. Список зависит от выбранных года/дня.",
        "run_hint_conversion_direction": "Направление конвертации.",
        "run_label_source_dataset": "Исходный набор данных",
        "run_hint_source_dataset": "Выберите корень набора данных из переменных окружения как исходную директорию.",
        "run_option_whole_source_root": "Весь корень источника",
        "run_hint_year_filter": "Необязательный фильтр. Выберите год, чтобы конвертировать только его поддерево.",
        "run_hint_day_filter": "Необязательный фильтр. Выберите день, чтобы конвертировать только его поддерево.",
        "run_label_source_directory": "Исходная директория (путь хоста)",
        "run_label_destination_directory": "Целевая директория (путь хоста)",
        "run_label_auto_remove": "Автоудаление контейнера (--rm)",
        "run_hint_auto_remove": "Автоматически удалять Docker-контейнер после завершения.",
        "label_year": "Год",
        "label_year_folder": "Папка года",
        "label_day_folder": "Папка дня",
        "label_site": "Сайт",
        "label_direction": "Направление",
        "option_all_days_selected_year": "Все дни выбранного года",
        "run_no_year_folders_available": "Папки годов недоступны",
        "run_unit_stations": "станций",
        "run_no_source_profile": "Профиль источника недоступен.",
        "run_no_destination_profile": "Профиль назначения недоступен.",
        "run_hint_overwrite_enabled": "Включена перезапись, поэтому выход будет записан обратно в исходную директорию.",
        "run_button": "Запуск",
        "run_starting": "Запуск контейнера...",
        "badge_running": "Выполняется",
        "badge_success": "Успешно",
        "analysis_page_subtitle": "Запрашивайте TEC-данные, просматривайте сводные итоги и строите графики из backend аналитики.",
        "analysis_data_query_builder": "Конструктор запросов данных",
        "analysis_label_endpoint": "Эндпоинт",
        "analysis_label_doy": "DOY",
        "analysis_label_doy_start": "Начало DOY",
        "analysis_label_doy_end": "Конец DOY",
        "analysis_label_station": "Станция",
        "analysis_label_satellite": "Спутник",
        "analysis_label_stations_multi": "Станции (множественный выбор)",
        "analysis_label_source": "Источник",
        "analysis_label_alpha": "Альфа",
        "analysis_label_format": "Формат",
        "analysis_run_data_query": "Выполнить запрос данных",
        "analysis_open_new_tab": "Открыть в новой вкладке",
        "analysis_plot_viewer": "Просмотр графика",
        "analysis_label_plot_endpoint": "Эндпоинт графика",
        "analysis_render_plot": "Построить график",
        "analysis_data_preview": "Предпросмотр данных",
        "analysis_plot_output": "Вывод графика",
        "analysis_label_columns_csv": "столбцы (CSV-список)",
        "analysis_plot_options": "Параметры графика",
        "analysis_label_show_ci": "show_ci",
        "analysis_label_show_var": "show_var",
        "analysis_label_width_px": "width_px",
        "analysis_label_height_px": "height_px",
        "analysis_label_dpi": "dpi",
        "analysis_label_smooth": "smooth",
        "analysis_label_poly": "poly",
        "analysis_label_size_px": "size_px",
        "analysis_label_column": "column",
        "analysis_label_valid_only": "valid_only",
        "analysis_label_color_by_tec": "color_by_tec",
        "flag_label_days": "Фильтр дней",
        "flag_help_days": "Необязательный список/диапазон дней, например 1-5,10,12-14.",
        "flag_label_day_from": "День от (включительно)",
        "flag_help_day_from": "Необязательная начальная граница дня года (1..366).",
        "flag_label_day_to": "День до (включительно)",
        "flag_help_day_to": "Необязательная конечная граница дня года (1..366).",
    },
}


def get_lang(request: Request) -> str:
    """Resolve active language from query parameter or cookie."""
    query_lang = request.query_params.get("lang", "").strip().lower()
    if query_lang in SUPPORTED_LANGS:
        return query_lang

    cookie_lang = str(request.cookies.get(COOKIE_NAME, "")).strip().lower()
    if cookie_lang in SUPPORTED_LANGS:
        return cookie_lang

    accept_lang = request.headers.get("accept-language", "").lower()
    if accept_lang.startswith("ru") or ",ru" in accept_lang:
        return "ru"
    return DEFAULT_LANG


def translate(lang: str, key: str, **kwargs: Any) -> str:
    """Translate a key with optional format kwargs and EN fallback."""
    locale = lang if lang in SUPPORTED_LANGS else DEFAULT_LANG
    text = _TRANSLATIONS.get(locale, {}).get(key)
    if text is None:
        text = _TRANSLATIONS[DEFAULT_LANG].get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text


def template_context(request: Request, context: Mapping[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    """Build a template context with localization helpers."""
    lang = get_lang(request)
    merged: dict[str, Any] = {"request": request, "lang": lang}
    if context:
        merged.update(dict(context))
    merged.update(kwargs)

    def _t(key: str, **fmt_kwargs: Any) -> str:
        return translate(lang, key, **fmt_kwargs)

    merged["t"] = _t
    return merged


def apply_lang_cookie(request: Request, response: Response) -> Response:
    """Persist ?lang=xx from the current request into cookie storage."""
    query_lang = request.query_params.get("lang", "").strip().lower()
    if query_lang in SUPPORTED_LANGS:
        response.set_cookie(COOKIE_NAME, query_lang, max_age=31536000, samesite="lax")
    return response
