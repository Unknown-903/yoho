import re

# Language code to full display name mapping
LANGUAGE_MAP = {
    # Indian languages
    "en": "English", "eng": "English",
    "hi": "Hindi", "hin": "Hindi",
    "ta": "Tamil", "tam": "Tamil",
    "te": "Telugu", "tel": "Telugu",
    "ml": "Malayalam", "mal": "Malayalam",
    "mr": "Marathi", "mar": "Marathi",
    "bn": "Bengali", "ben": "Bengali",
    "gu": "Gujarati", "guj": "Gujarati",
    "kn": "Kannada", "kan": "Kannada",
    "pa": "Punjabi", "pan": "Punjabi",
    "ur": "Urdu", "urd": "Urdu",
    "as": "Assamese", "asm": "Assamese",
    "or": "Odia", "ori": "Odia",
    "sa": "Sanskrit", "san": "Sanskrit",
    "ne": "Nepali", "nep": "Nepali",
    "sd": "Sindhi", "snd": "Sindhi",
    "ks": "Kashmiri", "kas": "Kashmiri",
    "bh": "Bihari", "bih": "Bihari",
    "kok": "Konkani", "kon": "Konkani",
    "doi": "Dogri",
    "mni": "Manipuri", "mni": "Manipuri",
    "bho": "Bhojpuri", "bho": "Bhojpuri",
    "mai": "Maithili", "mai": "Maithili",
    "brx": "Bodo", "bodo": "Bodo",
    "syl": "Sylheti", "syl": "Sylheti",

    # East/Southeast Asian
    "zh": "Chinese",
    "zh-cn": "Chinese (Simplified)",
    "zh-tw": "Chinese (Traditional)",
    "ja": "Japanese",
    "ko": "Korean",
    "vi": "Vietnamese",
    "th": "Thai",
    "my": "Burmese",
    "km": "Khmer",
    "lo": "Lao", "lao": "Lao",
    "bo": "Tibetan", "bod": "Tibetan",
    "mn": "Mongolian", "mon": "Mongolian",

    # Middle Eastern
    "ar": "Arabic", "ara": "Arabic",
    "fa": "Persian", "fas": "Persian",
    "he": "Hebrew", "heb": "Hebrew",
    "ku": "Kurdish", "kur": "Kurdish",
    "ps": "Pashto", "pus": "Pashto",

    # European
    "fr": "French", "fra": "French",
    "de": "German", "deu": "German",
    "es": "Spanish", "spa": "Spanish",
    "pt": "Portuguese", "por": "Portuguese",
    "it": "Italian", "ita": "Italian",
    "ru": "Russian", "rus": "Russian",
    "uk": "Ukrainian", "ukr": "Ukrainian",
    "pl": "Polish", "pol": "Polish",
    "nl": "Dutch", "nld": "Dutch",
    "ro": "Romanian", "ron": "Romanian",
    "cs": "Czech", "ces": "Czech",
    "sk": "Slovak", "slk": "Slovak",
    "hu": "Hungarian", "hun": "Hungarian",
    "sv": "Swedish", "swe": "Swedish",
    "fi": "Finnish", "fin": "Finnish",
    "no": "Norwegian", "nor": "Norwegian",
    "da": "Danish", "dan": "Danish",
    "el": "Greek", "ell": "Greek",
    "sr": "Serbian", "srp": "Serbian",
    "hr": "Croatian", "hrv": "Croatian",
    "bg": "Bulgarian", "bul": "Bulgarian",
    "lt": "Lithuanian", "lit": "Lithuanian",
    "lv": "Latvian", "lav": "Latvian",
    "et": "Estonian", "est": "Estonian",
    "sl": "Slovenian", "slv": "Slovenian",
    "mt": "Maltese", "mlt": "Maltese",
    "ga": "Irish", "gle": "Irish",
    "is": "Icelandic", "isl": "Icelandic",
    "bs": "Bosnian", "bos": "Bosnian",

    # African
    "sw": "Swahili", "swa": "Swahili",
    "am": "Amharic", "amh": "Amharic",
    "yo": "Yoruba", "yor": "Yoruba",
    "ig": "Igbo", "ibo": "Igbo",
    "ha": "Hausa", "hau": "Hausa",
    "zu": "Zulu", "zul": "Zulu",
    "xh": "Xhosa", "xho": "Xhosa",
    "af": "Afrikaans", "afr": "Afrikaans",
    "rw": "Kinyarwanda", "kin": "Kinyarwanda",
    "so": "Somali", "som": "Somali",
    "tn": "Tswana", "tsn": "Tswana",

    # American
    "qu": "Quechua", "que": "Quechua",
    "gn": "Guarani", "grn": "Guarani",
    "ay": "Aymara", "aym": "Aymara",
    "ht": "Haitian Creole", "hat": "Haitian Creole",
    "cr": "Cree", "cre": "Cree",
    "es-mx": "Spanish (Mexico)",
    "pt-br": "Portuguese (Brazil)",

    # Pacific/Oceanic
    "fil": "Filipino", "fil": "Filipino",
    "tl": "Tagalog", "tgl": "Tagalog",
    "fj": "Fijian", "fij": "Fijian",
    "sm": "Samoan", "smo": "Samoan",
    "to": "Tongan", "ton": "Tongan",
    "mi": "Maori", "mri": "Maori",

    # Artificial / Classical
    "la": "Latin",
    "eo": "Esperanto",
    "tlh": "Klingon",
    "art": "Artificial",
    "sux": "Sumerian",
}


def get_language_label(code):
    if not code:
        return "Unknown"
    value = code.strip().lower()
    # Allow e.g. en-US or zh-CN
    main = value.split("-")[0]
    return LANGUAGE_MAP.get(value) or LANGUAGE_MAP.get(main) or code.strip().title()


def get_original_title(file_path):
    import subprocess
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format_tags=title", "-of", "default=noprint_wrappers=1:nokey=1", file_path],
            capture_output=True,
            text=True,
            timeout=20
        )
        return (result.stdout or "").strip()
    except Exception:
        return ""


def compose_title_with_prefix(original_title, user_title):
    """Replace prefix with user title, preserve suffix separated by special chars."""
    if not user_title:
        return original_title or ""
    if not original_title:
        return user_title.strip()

    orig = original_title.strip()
    user = user_title.strip()
    if not orig:
        return user

    # If user title already has separator, use it as-is
    if re.search(r"[\s\-_:|]", user):
        return user

    # Replace prefix with user title, keep suffix
    m = re.match(r"^(?P<prefix>.+?)(?P<sep>[\s\-_:|]+)(?P<suffix>.+)$", orig)
    if m:
        sep = m.group("sep").strip()
        suffix = m.group("suffix").strip()
        if not sep:
            sep = " - "
        else:
            sep = f" {sep} " if len(sep) == 1 else f" {sep} "
        return f"{user}{sep}{suffix}"

    return user

